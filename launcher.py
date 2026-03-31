from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import json
import atexit
import ctypes
import hashlib
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from config.env_loader import parse_env_file


WEB_PORT = 3000
OPENBB_DEFAULT_PORT = 6900
REQUIRED_API_PATHS = {
    "/options/expiries": "get",
    "/options/chain": "get",
    "/options/backtest": "post",
    "/backtest/strategies": "get",
    "/setup/services/stop-all": "post",
}
REQUIRED_WEB_ROUTES = ["/", "/setup", "/options", "/trade", "/backtest"]
_INSTANCE_MUTEX_NAME = "Global\\LongPortLauncher_SingleInstance_v1"
_WATCHDOG_MUTEX_NAME = "Global\\LongPortLauncher_BackendWatchdog_v1"
_instance_mutex_handle = None
_instance_lock_file: Path | None = None
_watchdog_mutex_handle = None


def _resolve_root() -> Path:
    # Running as script: project root is file directory.
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent

    # Running as PyInstaller EXE: executable usually lives in <root>/dist.
    exe_dir = Path(sys.executable).resolve().parent
    candidates = [exe_dir, exe_dir.parent]
    for c in candidates:
        if (c / "frontend").exists() and (c / "api").exists():
            return c
    return exe_dir


ROOT = _resolve_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AUTO_TRADER_WORKER_PID_FILE = ROOT / ".auto_trader_worker.pid"
AUTO_TRADER_SUPERVISOR_PID_FILE = ROOT / ".auto_trader_supervisor.pid"

from backend_uvicorn_spec import DEFAULT_API_PORT, LAUNCHER_UVICORN_HOST, build_uvicorn_argv
from runtime_process_utils import is_pid_alive as _is_pid_alive
from runtime_process_utils import read_pid_file as _read_pid_file

API_PORT = DEFAULT_API_PORT
FRONTEND_DIR = ROOT / "frontend"
WATCHDOG_PID_FILE = ROOT / ".backend_watchdog.pid"
WATCHDOG_PAUSE_FILE = ROOT / ".backend_watchdog.pause"
WATCHDOG_LOG_FILE = ROOT / "launcher_watchdog.log"
WATCHDOG_BUSY_FILE = ROOT / ".backend_watchdog.busy"
WATCHDOG_HEALTH_TIMEOUT_SECONDS = max(2.0, float(os.getenv("LONGPORT_WATCHDOG_HEALTH_TIMEOUT", "6.0")))
WATCHDOG_CONFIRM_TIMEOUT_SECONDS = max(
    WATCHDOG_HEALTH_TIMEOUT_SECONDS,
    float(os.getenv("LONGPORT_WATCHDOG_CONFIRM_TIMEOUT", "20.0")),
)
WATCHDOG_FAILS_BEFORE_RESTART = max(6, int(os.getenv("LONGPORT_WATCHDOG_FAILS_BEFORE_RESTART", "24")))
WATCHDOG_HEALTHY_SLEEP_SECONDS = 5
WATCHDOG_UNHEALTHY_SLEEP_SECONDS = max(3, int(os.getenv("LONGPORT_WATCHDOG_UNHEALTHY_SLEEP", "6")))
WATCHDOG_BUSY_TTL_SECONDS = max(
    20 * 60, int(os.getenv("LONGPORT_WATCHDOG_BUSY_TTL_SECONDS", "10800"))
)
WATCHDOG_RESTART_COOLDOWN_SECONDS = max(60, int(os.getenv("LONGPORT_WATCHDOG_RESTART_COOLDOWN", "300")))
WATCHDOG_STARTUP_GRACE_SECONDS = max(20, int(os.getenv("LONGPORT_WATCHDOG_STARTUP_GRACE", "90")))


def _is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def _is_http_healthy(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= int(resp.status) < 500
    except Exception:
        return False


def _to_bool(value: str, default: bool = False) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _windows_prepend_path_entries() -> list[str]:
    """
    资源管理器双击 .exe 启动时，进程继承的 PATH 往往不含 Node/Python 安装目录，
    shutil.which 会找不到 npm/python。此处收集常见安装路径并置于 PATH 最前。
    """
    extras: list[str] = []
    seen_norm: set[str] = set()

    def consider(path: Path) -> None:
        try:
            if not path.is_dir():
                return
            key = os.path.normcase(os.path.normpath(str(path.resolve())))
            if key in seen_norm:
                return
            seen_norm.add(key)
            extras.append(str(path))
        except OSError:
            return

    if os.name != "nt":
        return extras

    for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_key, "").strip()
        if not base:
            continue
        nodejs = Path(base) / "nodejs"
        if (nodejs / "npm.cmd").exists() or (nodejs / "node.exe").exists():
            consider(nodejs)

    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        volta_bin = Path(local) / "Volta" / "bin"
        if volta_bin.is_dir() and (
            (volta_bin / "node.exe").exists() or (volta_bin / "npm.cmd").exists()
        ):
            consider(volta_bin)
        py_root = Path(local) / "Programs" / "Python"
        if py_root.is_dir():
            try:
                for child in sorted(py_root.iterdir()):
                    if not child.is_dir():
                        continue
                    if (child / "python.exe").is_file():
                        consider(child)
                        scripts = child / "Scripts"
                        if scripts.is_dir():
                            consider(scripts)
            except OSError:
                pass

    for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_key, "").strip()
        if not base:
            continue
        try:
            for child in Path(base).iterdir():
                if not child.is_dir():
                    continue
                if not child.name.lower().startswith("python"):
                    continue
                if (child / "python.exe").is_file():
                    consider(child)
                    scripts = child / "Scripts"
                    if scripts.is_dir():
                        consider(scripts)
        except OSError:
            pass

    return extras


def _prepend_path_env(env: dict[str, str], extra_dirs: list[str]) -> None:
    if not extra_dirs:
        return
    sep = ";" if os.name == "nt" else ":"
    prev = env.get("PATH", "") or ""
    env["PATH"] = sep.join(extra_dirs) + sep + prev


def _augment_path_for_gui_launch(env: dict[str, str]) -> None:
    if os.name != "nt":
        return
    extras = _windows_prepend_path_entries()
    if not extras:
        return
    _prepend_path_env(env, extras)
    os.environ["PATH"] = env["PATH"]
    preview = "; ".join(extras[:4])
    if len(extras) > 4:
        preview += "; ..."
    print(f"[INFO] 已向前追加 PATH（解决双击启动找不到 npm/Python）: {preview}")


def _http_get_json(url: str, timeout: float = 3.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if int(resp.status) != 200:
                return None
            raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def _http_post_json(url: str, payload: dict, timeout: float = 3.0) -> dict | None:
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if int(resp.status) != 200:
                return None
            raw = resp.read().decode("utf-8", errors="ignore")
            parsed = json.loads(raw) if raw else {}
            return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _http_status_code(url: str, timeout: float = 2.0) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as e:
        return int(getattr(e, "code", 0) or 0)
    except Exception:
        return None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stop_auto_trader_worker_via_api() -> bool:
    """
    尝试通过 API 优雅停止 auto_trader worker/supervisor：
    - 走 /setup/services/stop（stop_auto_trader=true, stop_feishu_bot=false）
    - 成功则返回 True，否则失败返回 False
    """
    stop_url = f"http://127.0.0.1:{API_PORT}/setup/services/stop"
    payload = {"stop_feishu_bot": False, "stop_auto_trader": True}
    res = _http_post_json(stop_url, payload, timeout=3.0)
    if not isinstance(res, dict):
        return False
    # API 返回结构为 {"ok": True, "stopped": {...}}
    return bool(res.get("ok") is True)


def _stop_auto_trader_worker_via_pid_files() -> None:
    """
    API 不可达时的兜底：
    - 读取 .auto_trader_worker.pid / .auto_trader_supervisor.pid
    - 若进程存活则 taskkill
    """
    worker_pid = _read_pid_file(AUTO_TRADER_WORKER_PID_FILE)
    supervisor_pid = _read_pid_file(AUTO_TRADER_SUPERVISOR_PID_FILE)
    pids: list[int] = []
    if worker_pid and _is_pid_alive(worker_pid):
        pids.append(int(worker_pid))
    if supervisor_pid and _is_pid_alive(supervisor_pid):
        pid_i = int(supervisor_pid)
        if pid_i not in pids:
            pids.append(pid_i)
    if pids:
        _kill_pids(pids)


def _stop_auto_trader_before_backend_restart() -> None:
    # 优先 API 优雅停机；失败则用 pid 文件硬停，避免旧 worker 继续扫旧市场。
    if _stop_auto_trader_worker_via_api():
        time.sleep(1.0)
        return
    _stop_auto_trader_worker_via_pid_files()
    time.sleep(1.0)


def _frontend_source_hash() -> str:
    """
    计算前端关键源码哈希，作为“页面版本标记”。
    覆盖 app/components/lib + 关键配置文件，确保任意页面变更都可被感知。
    """
    roots = [FRONTEND_DIR / "app", FRONTEND_DIR / "components", FRONTEND_DIR / "lib"]
    exts = {".ts", ".tsx", ".js", ".jsx", ".css", ".json"}
    files: list[Path] = []
    for r in roots:
        if not r.exists():
            continue
        for p in r.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() in exts:
                files.append(p)
    for p in [
        FRONTEND_DIR / "package.json",
        FRONTEND_DIR / "next.config.js",
        FRONTEND_DIR / "next.config.mjs",
        FRONTEND_DIR / "tsconfig.json",
    ]:
        if p.exists() and p.is_file():
            files.append(p)
    files = sorted(set(files), key=lambda x: str(x).lower())

    h = hashlib.sha256()
    for p in files:
        rel = str(p.relative_to(FRONTEND_DIR)).replace("\\", "/")
        h.update(rel.encode("utf-8", errors="ignore"))
        try:
            content = p.read_bytes()
        except Exception:
            content = b""
        h.update(_sha256_bytes(content).encode("ascii"))
    return h.hexdigest()


def _frontend_hash_marker_path() -> Path:
    return FRONTEND_DIR / ".next" / "launcher_frontend_hash.txt"


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _write_text_file(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except Exception:
        pass


def _frontend_build_ready() -> bool:
    return (FRONTEND_DIR / ".next" / "BUILD_ID").exists()


def _frontend_build_hash_matches(expected_hash: str) -> bool:
    marker = _frontend_hash_marker_path()
    if not marker.exists():
        return False
    return _read_text_file(marker) == expected_hash


def _api_spec_has_required_paths(spec: dict | None) -> bool:
    if not isinstance(spec, dict):
        return False
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return False
    for path, method in REQUIRED_API_PATHS.items():
        item = paths.get(path)
        if not isinstance(item, dict):
            return False
        if method and str(method).lower() not in {str(k).lower() for k in item.keys()}:
            return False
    return True


def _pids_listening_on_port(port: int) -> list[int]:
    if os.name != "nt":
        return []
    try:
        out = subprocess.check_output(  # noqa: S603
            ["netstat", "-ano"],
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
    except Exception:
        return []
    pids: set[int] = set()
    needle = f":{port}"
    for line in out.splitlines():
        if "LISTENING" not in line or needle not in line:
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pids.add(int(parts[-1]))
        except Exception:
            continue
    return sorted(pids)


def _kill_pids(pids: list[int]) -> None:
    for pid in pids:
        try:
            subprocess.run(  # noqa: S603
                ["taskkill", "/PID", str(pid), "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            pass


def _pid_commandline(pid: int) -> str:
    """
    读取进程命令行，用于识别监听端口的进程是否为本项目 uvicorn/next。
    新版 Windows 常移除/禁用 WMIC，优先用 PowerShell CIM。
    """
    if os.name != "nt":
        return ""
    pid = int(pid)
    ps_cmd = (
        f'$p = Get-CimInstance Win32_Process -Filter "ProcessId={pid}" -ErrorAction SilentlyContinue; '
        f"if ($null -ne $p) {{ $p.CommandLine }} else {{ '' }}"
    )
    popen_kw: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "ignore",
        "timeout": 10,
        "check": False,
    }
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        popen_kw["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    try:
        ps_exe = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or "powershell.exe"
        proc = subprocess.run([ps_exe, "-NoProfile", "-NonInteractive", "-Command", ps_cmd], **popen_kw)  # noqa: S603
        out = (proc.stdout or "").strip()
        if out:
            return out.lower()
    except Exception:
        pass
    try:
        wmic_kw: dict[str, object] = {
            "check": False,
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "ignore",
            "timeout": 10,
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            wmic_kw["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        proc = subprocess.run(  # noqa: S603
            ["wmic", "process", "where", f"processid={pid}", "get", "CommandLine", "/value"],
            **wmic_kw,
        )
        out = str(proc.stdout or "")
        for line in out.splitlines():
            if line.startswith("CommandLine="):
                return line.split("=", 1)[1].strip().lower()
    except Exception:
        return ""
    return ""


def _is_backend_cmdline(cmdline: str) -> bool:
    c = str(cmdline or "").lower()
    if not c:
        return False
    # 兼容 api.main:app / 引号路径 / 通过 -m uvicorn 启动
    if "uvicorn" in c and "api.main" in c:
        return True
    if "longportlauncher" in c and "api.main" in c:
        return True
    return False


def _is_frontend_cmdline(cmdline: str) -> bool:
    c = str(cmdline or "").lower()
    return (
        ("next" in c and ("start" in c or "dev" in c))
        or ("npm" in c and (" run start" in c or " run dev" in c))
    )


def _collect_managed_pids(port: int, kind: str) -> tuple[list[int], list[int]]:
    all_pids = _pids_listening_on_port(port)
    managed: list[int] = []
    unknown: list[int] = []
    for pid in all_pids:
        cmd = _pid_commandline(pid)
        if kind == "backend":
            if _is_backend_cmdline(cmd):
                managed.append(pid)
            else:
                unknown.append(pid)
        else:
            if _is_frontend_cmdline(cmd):
                managed.append(pid)
            else:
                unknown.append(pid)
    return managed, unknown


def _is_usable_python(path: Path) -> bool:
    try:
        proc = subprocess.run(  # noqa: S603
            [str(path), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _get_python_cmd() -> list[str]:
    # Prefer project-local virtualenv interpreters first.
    venv_candidates = [
        ROOT / ".launcher-venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    for candidate in venv_candidates:
        if candidate.exists() and _is_usable_python(candidate):
            return [str(candidate)]

    # Script mode can use current interpreter directly.
    if not getattr(sys, "frozen", False):
        return [sys.executable]

    # EXE mode must not use sys.executable (it would recursively start itself).
    # Allow explicit override first.
    env_py = os.getenv("LONGPORT_PYTHON", "").strip()
    if env_py:
        return [env_py]

    py = shutil.which("python.exe") or shutil.which("python")
    if py:
        return [py]

    py_launcher = shutil.which("py.exe") or shutil.which("py")
    if py_launcher:
        return [py_launcher, "-3"]

    raise RuntimeError("未找到 Python 解释器，请安装 Python 并确保 python/py 在 PATH 中。")


def _get_npm_cmd() -> str:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise RuntimeError("未找到 npm，请先安装 Node.js 并确保 npm 在 PATH 中。")
    return npm


def _start_process(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen[bytes]:
    flags = 0
    startupinfo = None
    if os.name == "nt":
        # 默认静默启动子进程，避免 Windows 控制台窗口反复弹出。
        # 如需旧行为，可设置 LONGPORT_CHILD_NEW_CONSOLE=1。
        if _to_bool(os.getenv("LONGPORT_CHILD_NEW_CONSOLE", ""), default=False):
            flags = subprocess.CREATE_NEW_CONSOLE
        else:
            flags = subprocess.CREATE_NO_WINDOW
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return subprocess.Popen(  # noqa: S603
        cmd,
        cwd=str(cwd),
        env=env,
        creationflags=flags,
        startupinfo=startupinfo,
    )


def _run_sync(cmd: list[str], cwd: Path, env: dict[str, str]) -> int:
    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            cwd=str(cwd),
            env=env,
            check=False,
        )
        return int(proc.returncode)
    except Exception:
        return 1


def _write_pid_file(path: Path, pid: int) -> None:
    try:
        path.write_text(str(pid), encoding="utf-8")
    except Exception:
        pass


def _remove_pid_file(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def _watchdog_log(msg: str) -> None:
    _watchdog_log_event(event="watchdog_log", message=msg)


def _watchdog_log_event(event: str, message: str, reason_code: str = "", **fields: object) -> None:
    payload: dict[str, object] = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "event": str(event or "watchdog_log"),
        "message": str(message or ""),
    }
    if reason_code:
        payload["reason_code"] = str(reason_code)
    for k, v in fields.items():
        if v is None:
            continue
        payload[str(k)] = v
    line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
    try:
        with open(WATCHDOG_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _resolve_backend_pids(port: int) -> tuple[list[int], list[int]]:
    """
    在无法读取进程命令行时（权限/策略/PowerShell 失败），netstat 仍可能给出 PID。
    若端口上仅有 1 个监听进程，且本机 OpenAPI 含必选路由，则视为本项目后端，允许重启。
    """
    managed, unknown = _collect_managed_pids(port, "backend")
    if managed or len(unknown) != 1:
        return managed, unknown
    spec = _http_get_json(f"http://127.0.0.1:{port}/openapi.json", timeout=3.0)
    if not _api_spec_has_required_paths(spec):
        return managed, unknown
    sole = unknown[0]
    _watchdog_log_event(
        event="pid_heuristic",
        message=f"sole listener pid={sole} matched openapi, treating as backend",
        reason_code="openapi_sole_listener",
        pid=sole,
    )
    return [sole], []


def _backend_busy_active() -> bool:
    """
    研究/重负载阶段由后端写入 busy 标记，watchdog 在有效期内不重启后端。
    设置环境变量 LONGPORT_WATCHDOG_IGNORE_BUSY=1 可跳过（后端异常退出未清标记时自救）。
    """
    if _to_bool(os.getenv("LONGPORT_WATCHDOG_IGNORE_BUSY", ""), default=False):
        return False
    try:
        if not WATCHDOG_BUSY_FILE.exists():
            return False
        age = time.time() - float(WATCHDOG_BUSY_FILE.stat().st_mtime)
        return age <= float(WATCHDOG_BUSY_TTL_SECONDS)
    except Exception:
        return False


def _backend_cmd(python_cmd: list[str], dev_mode: bool = False) -> list[str]:
    return [*python_cmd, *build_uvicorn_argv(LAUNCHER_UVICORN_HOST, API_PORT, reload=dev_mode)]


def _ensure_backend_running(
    python_cmd: list[str],
    dev_mode: bool,
    env: dict[str, str],
    *,
    startup_message: str,
) -> tuple[bool, bool]:
    """
    拉起 uvicorn 并等待健康检查与必选路由就绪。
    返回 (是否成功, 是否新启动了子进程)。
    """
    api_health_url = f"http://127.0.0.1:{API_PORT}/health"
    backend_cmd = _backend_cmd(python_cmd, dev_mode=dev_mode)
    _start_process(backend_cmd, ROOT, env)
    print(startup_message + (" (dev reload)" if dev_mode else ""))
    for _ in range(12):
        if _is_http_healthy(api_health_url):
            spec = _http_get_json(f"http://127.0.0.1:{API_PORT}/openapi.json", timeout=2.0)
            if _api_spec_has_required_paths(spec):
                return True, True
        time.sleep(0.5)
    py_hint = " ".join(python_cmd)
    print("[ERROR] 后端未成功启动或路由未就绪，请检查后端控制台报错。")
    print(f"[HINT] 当前使用的 Python: {py_hint}")
    print(f"[HINT] 可尝试执行: {py_hint} -m pip install -r requirements.txt")
    print(f"[HINT] 并检查 {api_health_url} 与 /openapi.json 中是否存在期权路由。")
    return False, True


def _read_openbb_runtime_config() -> dict[str, object]:
    env_file = parse_env_file(ROOT / ".env")
    enabled_raw = os.getenv("OPENBB_ENABLED", env_file.get("OPENBB_ENABLED", "0"))
    base_url = os.getenv("OPENBB_BASE_URL", env_file.get("OPENBB_BASE_URL", f"http://127.0.0.1:{OPENBB_DEFAULT_PORT}"))
    auto_start_raw = os.getenv("OPENBB_AUTO_START", env_file.get("OPENBB_AUTO_START", "1"))
    timeout_raw = os.getenv("OPENBB_TIMEOUT_SECONDS", env_file.get("OPENBB_TIMEOUT_SECONDS", "5"))

    enabled = _to_bool(enabled_raw, default=False)
    auto_start = _to_bool(auto_start_raw, default=True)
    base_url = str(base_url or "").strip().rstrip("/")
    if not base_url:
        base_url = f"http://127.0.0.1:{OPENBB_DEFAULT_PORT}"

    parsed = urllib.parse.urlparse(base_url if "://" in base_url else f"http://{base_url}")
    scheme = parsed.scheme or "http"
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or OPENBB_DEFAULT_PORT)
    local_hosts = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
    is_local = host in local_hosts
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    probe_url = f"{scheme}://{probe_host}:{port}/"

    try:
        timeout = max(1.0, float(timeout_raw))
    except Exception:
        timeout = 5.0

    return {
        "enabled": enabled,
        "auto_start": auto_start,
        "base_url": base_url,
        "host": host,
        "port": port,
        "probe_host": probe_host,
        "probe_url": probe_url,
        "timeout": timeout,
        "is_local": is_local,
    }


def _get_openbb_cmd() -> tuple[list[str] | None, str]:
    exe_candidates = [
        ROOT / ".openbb-venv" / "Scripts" / "openbb-api.exe",
        ROOT / ".openbb-venv" / "Scripts" / "openbb-api",
        ROOT / ".venv" / "Scripts" / "openbb-api.exe",
        ROOT / ".venv" / "Scripts" / "openbb-api",
    ]
    for p in exe_candidates:
        if p.exists() and p.is_file():
            return [str(p)], str(p)

    which_openbb = shutil.which("openbb-api.exe") or shutil.which("openbb-api")
    if which_openbb:
        return [which_openbb], which_openbb

    py_candidates = [
        ROOT / ".openbb-venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    for p in py_candidates:
        if p.exists() and p.is_file():
            return [str(p), "-m", "openbb_platform_api.main"], f"{p} -m openbb_platform_api.main"

    return None, ""


def _start_background_process(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen[bytes]:
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    return subprocess.Popen(  # noqa: S603
        cmd,
        cwd=str(cwd),
        env=env,
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _watchdog_running() -> bool:
    pid = _read_pid_file(WATCHDOG_PID_FILE)
    return bool(pid and _is_pid_alive(pid))


def _clear_watchdog_pause() -> None:
    _remove_pid_file(WATCHDOG_PAUSE_FILE)


def _start_backend_watchdog() -> None:
    if _watchdog_running():
        print("[INFO] 后端守护已在运行。")
        return
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--backend-watchdog"]
    else:
        py = _get_python_cmd()
        cmd = [*py, str(Path(__file__).resolve()), "--backend-watchdog"]
    try:
        p = _start_background_process(cmd, ROOT, env)
        print(f"[OK] 已启动后端守护进程 (pid={p.pid})")
    except Exception as e:
        print(f"[WARN] 后端守护进程启动失败: {e}")


def run_backend_watchdog() -> int:
    global _watchdog_mutex_handle
    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateMutexW(None, True, _WATCHDOG_MUTEX_NAME)
            if handle:
                last_error = int(kernel32.GetLastError())
                # ERROR_ALREADY_EXISTS = 183
                if last_error == 183:
                    try:
                        kernel32.CloseHandle(handle)
                    except Exception:
                        pass
                    _watchdog_log_event(
                        event="watchdog_exit",
                        message="watchdog instance already running, exit duplicate",
                        reason_code="duplicate_watchdog_instance",
                    )
                    return 0
                _watchdog_mutex_handle = handle
        except Exception:
            pass

    _write_pid_file(WATCHDOG_PID_FILE, os.getpid())
    def _release_watchdog_runtime() -> None:
        _remove_pid_file(WATCHDOG_PID_FILE)
        if os.name == "nt":
            try:
                if _watchdog_mutex_handle:
                    ctypes.windll.kernel32.ReleaseMutex(_watchdog_mutex_handle)
                    ctypes.windll.kernel32.CloseHandle(_watchdog_mutex_handle)
            except Exception:
                pass

    atexit.register(_release_watchdog_runtime)
    _watchdog_log("backend watchdog started")
    if _to_bool(os.getenv("LONGPORT_WATCHDOG_IGNORE_BUSY", ""), default=False):
        _watchdog_log_event(
            event="watchdog_log",
            message="LONGPORT_WATCHDOG_IGNORE_BUSY=1, backend busy marker will be ignored",
            reason_code="ignore_busy_env",
        )

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    dev_mode = os.getenv("LONGPORT_DEV", "").strip() == "1"
    python_cmd = _get_python_cmd()
    cmd = _backend_cmd(python_cmd, dev_mode=dev_mode)

    fail_count = 0
    # 防抖：后端刚重启后给一段宽限，避免启动期短暂超时被误判。
    last_restart_ts = 0.0
    while True:
        if WATCHDOG_PAUSE_FILE.exists():
            _watchdog_log_event(
                event="watchdog_exit",
                message="pause file detected, watchdog exiting",
                reason_code="pause_file",
            )
            return 0

        if _backend_busy_active():
            busy_port_open = _is_port_open(API_PORT)
            # busy 标记只用于“重负载保护”，不应阻止“后端已死”时的自恢复重启。
            # 忙时仅做端口探测，避免频繁命令行探测触发 Windows 子进程窗口闪现。
            if busy_port_open:
                _watchdog_log_event(
                    event="health_skip",
                    message="backend busy marker active, skip health restart",
                    reason_code="backend_busy",
                )
                fail_count = 0
                time.sleep(WATCHDOG_HEALTHY_SLEEP_SECONDS)
                continue
            try:
                if WATCHDOG_BUSY_FILE.exists():
                    WATCHDOG_BUSY_FILE.unlink()
            except Exception:
                pass
            _watchdog_log_event(
                event="busy_recover",
                message="backend busy marker stale while backend appears down, enabling auto-restart",
                reason_code="backend_busy_stale",
            )
            # 直接进入重启链路，避免长时间停留在“后端断连”状态。
            fail_count = WATCHDOG_FAILS_BEFORE_RESTART

        if last_restart_ts > 0 and (time.time() - last_restart_ts) < WATCHDOG_STARTUP_GRACE_SECONDS:
            _watchdog_log_event(
                event="health_skip",
                message="backend startup grace active, skip health restart",
                reason_code="startup_grace",
            )
            fail_count = 0
            time.sleep(WATCHDOG_HEALTHY_SLEEP_SECONDS)
            continue

        health_url = f"http://127.0.0.1:{API_PORT}/health"
        healthy = _is_http_healthy(health_url, timeout=WATCHDOG_HEALTH_TIMEOUT_SECONDS)
        if healthy:
            fail_count = 0
            time.sleep(WATCHDOG_HEALTHY_SLEEP_SECONDS)
            continue

        # 二次确认：研究任务等重负载时，2s 探针可能误判；若端口仍在，再给一次更长超时确认。
        if _is_port_open(API_PORT) and _is_http_healthy(health_url, timeout=WATCHDOG_CONFIRM_TIMEOUT_SECONDS):
            fail_count = 0
            time.sleep(WATCHDOG_HEALTHY_SLEEP_SECONDS)
            continue

        fail_count += 1
        if fail_count < WATCHDOG_FAILS_BEFORE_RESTART:
            time.sleep(WATCHDOG_UNHEALTHY_SLEEP_SECONDS)
            continue

        # 最终确认（长超时）：
        # 避免在高负载阶段因为短超时累计误判，从而触发重启风暴导致终端闪跳。
        deep_health_timeout = max(30.0, WATCHDOG_CONFIRM_TIMEOUT_SECONDS * 2.0)
        openapi_url = f"http://127.0.0.1:{API_PORT}/openapi.json"
        if _is_http_healthy(health_url, timeout=deep_health_timeout):
            _watchdog_log_event(
                event="restart_skip",
                message="deep health probe recovered, skip restart",
                reason_code="deep_probe_recovered",
                fail_count=fail_count,
                timeout_seconds=deep_health_timeout,
            )
            fail_count = 0
            time.sleep(WATCHDOG_HEALTHY_SLEEP_SECONDS)
            continue
        if _http_get_json(openapi_url, timeout=deep_health_timeout) is not None:
            _watchdog_log_event(
                event="restart_skip",
                message="openapi probe recovered, skip restart",
                reason_code="openapi_probe_recovered",
                fail_count=fail_count,
                timeout_seconds=deep_health_timeout,
            )
            fail_count = 0
            time.sleep(WATCHDOG_HEALTHY_SLEEP_SECONDS)
            continue

        managed, unknown = _resolve_backend_pids(API_PORT)
        if unknown:
            _watchdog_log_event(
                event="restart_skip",
                message=f"port {API_PORT} occupied by unknown pids={unknown}, skip restart",
                reason_code="port_conflict",
                unknown_pids=unknown,
                fail_count=fail_count,
            )
            fail_count = 0
            time.sleep(WATCHDOG_HEALTHY_SLEEP_SECONDS)
            continue

        if last_restart_ts > 0 and (time.time() - last_restart_ts) < WATCHDOG_RESTART_COOLDOWN_SECONDS:
            _watchdog_log_event(
                event="restart_skip",
                message="restart cooldown active, skip immediate restart",
                reason_code="restart_cooldown",
                fail_count=fail_count,
                cooldown_seconds=WATCHDOG_RESTART_COOLDOWN_SECONDS,
            )
            fail_count = 0
            time.sleep(WATCHDOG_HEALTHY_SLEEP_SECONDS)
            continue
        pid_before = managed[0] if managed else None
        if managed:
            # 已有后端进程但不健康，先清理再重启
            _kill_pids(managed)
            time.sleep(1)

        try:
            # 健康探针失败即将重启后端时，先停止 auto_trader 子进程，
            # 避免旧 worker 在新 API 上继续扫旧 market/pair_pool 配置。
            _stop_auto_trader_before_backend_restart()
            _watchdog_log_event(
                event="restart_attempt",
                message=f"health failed continuously, restarting backend fail_count={fail_count}",
                reason_code="health_timeout",
                fail_count=fail_count,
                pid_before=pid_before,
            )
            p = _start_background_process(cmd, ROOT, env)
            _watchdog_log_event(
                event="restart_success",
                message=f"backend restarted pid={p.pid}",
                reason_code="health_timeout",
                fail_count=fail_count,
                pid_before=pid_before,
                pid_after=int(p.pid),
            )
            last_restart_ts = time.time()
        except Exception as e:
            _watchdog_log_event(
                event="restart_failed",
                message=f"backend restart failed: {e}",
                reason_code="restart_exception",
                fail_count=fail_count,
                pid_before=pid_before,
                error=str(e),
            )

        fail_count = 0
        time.sleep(WATCHDOG_HEALTHY_SLEEP_SECONDS)


def _acquire_single_instance_lock() -> bool:
    """
    防止重复双击导致并发启动。
    - Windows: 使用全局命名 Mutex（推荐）
    - 其他平台: 使用 lock 文件兜底
    """
    global _instance_mutex_handle, _instance_lock_file

    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        # BOOL bInitialOwner=True
        handle = kernel32.CreateMutexW(None, True, _INSTANCE_MUTEX_NAME)
        if not handle:
            return True
        last_error = kernel32.GetLastError()
        # ERROR_ALREADY_EXISTS = 183
        if int(last_error) == 183:
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
            return False
        _instance_mutex_handle = handle

        def _release_mutex() -> None:
            try:
                if _instance_mutex_handle:
                    kernel32.ReleaseMutex(_instance_mutex_handle)
                    kernel32.CloseHandle(_instance_mutex_handle)
            except Exception:
                pass

        atexit.register(_release_mutex)
        return True

    # Non-Windows fallback.
    try:
        lock_path = ROOT / ".launcher.lock"
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
        _instance_lock_file = lock_path

        def _release_file_lock() -> None:
            try:
                if _instance_lock_file and _instance_lock_file.exists():
                    _instance_lock_file.unlink()
            except Exception:
                pass

        atexit.register(_release_file_lock)
        return True
    except FileExistsError:
        return False
    except Exception:
        return True


def run_force_restart_backend() -> int:
    """
    不占用启动器单实例互斥锁，仅结束后端并重新拉起 uvicorn。
    用法（exe）：在项目 dist 目录打开终端执行
      .\\LongPortLauncher.exe --force-restart-backend
    """
    if not FRONTEND_DIR.exists():
        print(f"[ERROR] 未找到前端目录: {FRONTEND_DIR}")
        print("[HINT] 请将 LongPortLauncher.exe 放在项目根目录下的 dist 文件夹中运行。")
        return 1
    try:
        if WATCHDOG_BUSY_FILE.exists():
            WATCHDOG_BUSY_FILE.unlink()
    except Exception:
        pass
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    dev_mode = os.getenv("LONGPORT_DEV", "").strip() == "1"
    python_cmd = _get_python_cmd()
    print(f"[INFO] --force-restart-backend | 项目目录: {ROOT}")
    # 手动重启 backend 时，同步停止 auto_trader worker/supervisor，确保新后端拉起的是最新配置。
    _stop_auto_trader_before_backend_restart()
    managed, unknown = _resolve_backend_pids(API_PORT)
    if unknown:
        print(
            f"[ERROR] 端口 {API_PORT} 上存在无法识别为本项目后端的进程 PID={unknown}，"
            "请用任务管理器结束对应进程后重试。"
        )
        print(
            "[HINT] 若仅有一个 python/uvicorn 在监听该端口，仍失败时检查是否能访问 "
            f"http://127.0.0.1:{API_PORT}/openapi.json"
        )
        return 1
    if managed:
        print(f"[INFO] 正在结束后端进程 PID={managed} …")
        _kill_pids(managed)
        time.sleep(1)
    ok, _ = _ensure_backend_running(
        python_cmd, dev_mode, env, startup_message="[OK] 后端已重新拉起"
    )
    return 0 if ok else 1


def main() -> int:
    if "--backend-watchdog" in sys.argv:
        return run_backend_watchdog()

    if "--force-restart-backend" in sys.argv:
        return run_force_restart_backend()

    if not _acquire_single_instance_lock():
        print("[WARN] 检测到已有启动器实例正在执行，请勿重复双击。")
        print("[HINT] 若你确认没有启动器在运行，请稍后再试。")
        print("[HINT] 需要重启 API：请先关闭上一启动器的控制台窗口，再重新双击 Launcher。")
        print(
            "[HINT] 或保持原窗口不关，在本机终端执行："
            f'"{Path(sys.executable).resolve()}" --force-restart-backend'
        )
        return 0

    if not FRONTEND_DIR.exists():
        print(f"[ERROR] 未找到前端目录: {FRONTEND_DIR}")
        print("[HINT] 请将 LongPortLauncher.exe 放在项目目录下的 dist 文件夹中运行。")
        return 1

    env = os.environ.copy()
    _augment_path_for_gui_launch(env)
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    dev_mode = os.getenv("LONGPORT_DEV", "").strip() == "1"

    try:
        python_cmd = _get_python_cmd()
        npm_cmd = _get_npm_cmd()
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        print("[HINT] 从资源管理器双击启动时系统 PATH 常不完整；启动器已尝试追加 Program Files\\nodejs 等路径。")
        print("[HINT] 若仍失败：请先安装 Node.js 与 Python，或用「终端」cd 到 dist 目录后运行 .\\LongPortLauncher.exe。")
        return 1

    _clear_watchdog_pause()
    _start_backend_watchdog()

    print(f"[INFO] 项目目录: {ROOT}")
    print(f"[INFO] 启动模式: {'开发模式' if dev_mode else '生产模式优先'}")

    backend_started = False
    frontend_started = False

    api_health_url = f"http://127.0.0.1:{API_PORT}/health"
    api_healthy = _is_http_healthy(api_health_url)
    api_has_required_routes = False
    if _is_port_open(API_PORT) and api_healthy:
        spec = _http_get_json(f"http://127.0.0.1:{API_PORT}/openapi.json", timeout=3.0)
        api_has_required_routes = _api_spec_has_required_paths(spec)
        if not api_has_required_routes:
            print("[WARN] 检测到后端为旧版本（缺少期权/stop-all 路由），将尝试重启后端。")

    # 健康且路由齐全：若为本项目 uvicorn，再次运行 Launcher 时也重启 API，便于加载最新代码。
    if _is_port_open(API_PORT) and api_healthy and api_has_required_routes:
        managed, unknown = _resolve_backend_pids(API_PORT)
        if unknown:
            print(
                f"[WARN] 端口 {API_PORT} 上存在非本项目监听进程 {unknown}，为安全起见不自动结束；"
                "若需重启 API 请先手动释放该端口。"
            )
            print(
                "[HINT] 若你确定占用进程就是本项目后端：可能是系统无法读取进程命令行（旧版依赖 WMIC）。"
                "请重新打包/更新 LongPortLauncher.exe（已改用 PowerShell 读取命令行）。"
            )
            print(f"[INFO] 当前后端健康，继续使用: http://127.0.0.1:{API_PORT}")
        elif managed:
            print("[INFO] 再次启动 Launcher：正在重启本项目后端以加载最新代码…")
            _kill_pids(managed)
            time.sleep(1)
            ok, started = _ensure_backend_running(
                python_cmd, dev_mode, env, startup_message="[OK] 已重启后端服务"
            )
            if not ok:
                return 1
            backend_started = started
        else:
            print(
                f"[INFO] 后端已在运行（未能识别为本项目 uvicorn，跳过自动重启）: http://127.0.0.1:{API_PORT}"
            )
    else:
        if _is_port_open(API_PORT) and not api_healthy:
            managed, unknown = _resolve_backend_pids(API_PORT)
            if unknown:
                print(f"[ERROR] 端口 {API_PORT} 被非本项目进程占用: {unknown}")
                print("[HINT] 请先释放该端口后再启动，避免误杀其它应用。")
                return 1
            if managed:
                print(f"[WARN] 检测到后端端口异常占用，正在清理本项目进程: {managed}")
                _kill_pids(managed)
                time.sleep(1)
        elif _is_port_open(API_PORT) and api_healthy and not api_has_required_routes:
            managed, unknown = _resolve_backend_pids(API_PORT)
            if unknown:
                print(f"[ERROR] 后端端口 {API_PORT} 存在非本项目进程: {unknown}")
                print("[HINT] 请手动停止该进程，确保启动器可以加载最新后端。")
                return 1
            if managed:
                print(f"[WARN] 正在重启旧版后端进程: {managed}")
                _kill_pids(managed)
                time.sleep(1)
        ok, started = _ensure_backend_running(
            python_cmd, dev_mode, env, startup_message="[OK] 已启动后端服务"
        )
        if not ok:
            return 1
        backend_started = started

    openbb_cfg = _read_openbb_runtime_config()
    if bool(openbb_cfg.get("enabled")):
        openbb_base_url = str(openbb_cfg.get("base_url") or "")
        openbb_probe_url = str(openbb_cfg.get("probe_url") or "")
        openbb_probe_host = str(openbb_cfg.get("probe_host") or "127.0.0.1")
        openbb_port = int(openbb_cfg.get("port") or OPENBB_DEFAULT_PORT)
        openbb_timeout = float(openbb_cfg.get("timeout") or 5.0)

        if _is_http_healthy(openbb_probe_url, timeout=openbb_timeout):
            print(f"[INFO] OpenBB 已在运行: {openbb_base_url}")
        elif not bool(openbb_cfg.get("auto_start")):
            print(f"[WARN] OpenBB 未连通: {openbb_base_url}")
            print("[HINT] OPENBB_AUTO_START=false，已跳过自动拉起。")
        elif not bool(openbb_cfg.get("is_local")):
            print(f"[WARN] OpenBB 未连通: {openbb_base_url}")
            print("[HINT] 仅支持自动拉起本机 OpenBB 服务；当前目标是远端地址。")
        elif _is_port_open(openbb_port, host=openbb_probe_host):
            print(f"[WARN] OpenBB 端口 {openbb_port} 已占用但健康检查失败，已跳过自动重启以避免误杀。")
            print("[HINT] 请手动检查占用该端口的进程。")
        else:
            openbb_cmd, openbb_cmd_hint = _get_openbb_cmd()
            if not openbb_cmd:
                print(f"[WARN] OpenBB 未连通: {openbb_base_url}")
                print("[HINT] 未找到 openbb-api 启动命令，请确认 .openbb-venv 或系统环境已安装 OpenBB API。")
            else:
                try:
                    _start_background_process(openbb_cmd, ROOT, env)
                    print("[OK] 已自动拉起 OpenBB 服务")
                    openbb_ready = False
                    for _ in range(20):
                        if _is_http_healthy(openbb_probe_url, timeout=openbb_timeout):
                            openbb_ready = True
                            break
                        time.sleep(1)
                    if openbb_ready:
                        print(f"[INFO] OpenBB 连接就绪: {openbb_base_url}")
                    else:
                        print(f"[WARN] OpenBB 启动后仍不可达: {openbb_base_url}")
                        if openbb_cmd_hint:
                            print(f"[HINT] 可手动执行: {openbb_cmd_hint}")
                except Exception as e:
                    print(f"[WARN] OpenBB 自动拉起失败: {e}")
                    if openbb_cmd_hint:
                        print(f"[HINT] 可手动执行: {openbb_cmd_hint}")

    web_url = f"http://127.0.0.1:{WEB_PORT}"
    frontend_source_hash = _frontend_source_hash()
    build_ready = _frontend_build_ready()
    build_hash_matches = _frontend_build_hash_matches(frontend_source_hash)
    if not dev_mode:
        print(
            "[INFO] 前端版本标记检查: "
            f"build_ready={build_ready}, hash_match={build_hash_matches}"
        )
    web_healthy = _is_http_healthy(web_url)
    frontend_restart_needed = False
    frontend_force_rebuild = False
    if _is_port_open(WEB_PORT) and web_healthy:
        missing_routes: list[str] = []
        for r in REQUIRED_WEB_ROUTES:
            code = _http_status_code(f"{web_url}{r}", timeout=2.0)
            if code != 200:
                missing_routes.append(f"{r}({code if code is not None else 'N/A'})")
        if missing_routes:
            frontend_restart_needed = True
            frontend_force_rebuild = True
            print(
                "[WARN] 检测到前端关键路由异常: "
                + ", ".join(missing_routes)
                + "，将重启并重建前端。"
            )
        elif not dev_mode and not build_hash_matches:
            frontend_restart_needed = True
            frontend_force_rebuild = True
            print("[WARN] 检测到前端源码版本已变化（哈希不一致），将重启并重建前端。")
        else:
            print(f"[INFO] 前端已在运行: {web_url}")
    else:
        frontend_restart_needed = True

    if frontend_restart_needed:
        if _is_port_open(WEB_PORT):
            managed, unknown = _collect_managed_pids(WEB_PORT, "frontend")
            if unknown:
                print(f"[ERROR] 端口 {WEB_PORT} 被非本项目进程占用: {unknown}")
                print("[HINT] 请先释放该端口后再启动，避免误杀其它应用。")
                return 1
            if managed:
                print(f"[WARN] 正在清理前端进程: {managed}")
                _kill_pids(managed)
                time.sleep(1)

        build_ready = _frontend_build_ready()
        need_build = (not dev_mode) and (frontend_force_rebuild or not build_ready or not build_hash_matches)
        if need_build:
            print("[INFO] 正在执行 npm run build（确保包含 /options 路由）...")
            rc = _run_sync([npm_cmd, "run", "build"], FRONTEND_DIR, env)
            build_ready = _frontend_build_ready()
            if rc != 0 or not build_ready:
                print("[WARN] 前端生产构建失败，将回退到 dev 模式启动。")
            else:
                _write_text_file(_frontend_hash_marker_path(), frontend_source_hash)
        frontend_script = "dev" if dev_mode or not build_ready else "start"
        frontend_cmd = [npm_cmd, "run", frontend_script]
        _start_process(frontend_cmd, FRONTEND_DIR, env)
        frontend_started = True
        print(f"[OK] 已启动前端服务 ({frontend_script})")

    if backend_started or frontend_started:
        print("[INFO] 服务启动中，稍后将自动打开浏览器...")
        # 双击启动时前端可能刚执行完 build + next start，20s 常不够；可用 LONGPORT_BROWSER_WAIT_SECONDS 覆盖。
        try:
            wait_override = int(os.getenv("LONGPORT_BROWSER_WAIT_SECONDS", "0") or "0")
        except ValueError:
            wait_override = 0
        if wait_override > 0:
            wait_loops = max(1, wait_override)
        elif frontend_started:
            wait_loops = 120
        else:
            wait_loops = 45
        all_ready = False
        for _ in range(wait_loops):
            if _is_http_healthy(api_health_url) and _is_http_healthy(f"http://127.0.0.1:{WEB_PORT}"):
                all_ready = True
                backend_ok = True
                break
            time.sleep(1)
        if not all_ready:
            print(
                "[WARN] 服务尚未完全就绪，但将继续打开浏览器。"
                f" 可稍候刷新 {web_url}，或增大等待：LONGPORT_BROWSER_WAIT_SECONDS=180"
            )

    webbrowser.open(f"http://127.0.0.1:{WEB_PORT}")
    print("[DONE] 启动完成。可关闭本窗口，不影响服务进程。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
