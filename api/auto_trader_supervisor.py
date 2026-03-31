import atexit
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKER_SCRIPT = os.path.join(ROOT, "api", "auto_trader_worker.py")
PID_FILE = os.path.join(ROOT, ".auto_trader_supervisor.pid")
STOP_FILE = os.path.join(ROOT, ".auto_trader_supervisor.stop")
STATUS_FILE = os.path.join(ROOT, ".auto_trader_supervisor.status.json")
LOG_FILE = os.path.join(ROOT, "auto_trader_supervisor.log")

_stop = False


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(msg: str) -> None:
    line = f"[{_now_iso()}] {msg}\n"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _write_json(path: str, data: dict[str, Any]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except Exception:
        pass


def _write_pid() -> None:
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def _remove_file(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _on_signal(_signum: int, _frame: Any) -> None:
    global _stop
    _stop = True


def _build_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = ROOT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def _win_subprocess_silent_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def main() -> None:
    global _stop
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    _remove_file(STOP_FILE)
    _write_pid()
    atexit.register(_remove_file, PID_FILE)

    restart_count = 0
    crash_times: list[datetime] = []
    cooldown_until: datetime | None = None
    worker_proc: subprocess.Popen[Any] | None = None

    _log("auto-trader supervisor started")
    _write_json(
        STATUS_FILE,
        {
            "started_at": _now_iso(),
            "worker_running": False,
            "restart_count": restart_count,
            "cooldown_until": None,
            "last_event": "supervisor_started",
        },
    )

    while not _stop:
        if os.path.exists(STOP_FILE):
            _log("stop file detected, supervisor stopping")
            break

        now = datetime.now()
        crash_times = [t for t in crash_times if (now - t).total_seconds() <= 600]
        if cooldown_until and now < cooldown_until:
            _write_json(
                STATUS_FILE,
                {
                    "worker_running": False,
                    "restart_count": restart_count,
                    "recent_crash_count": len(crash_times),
                    "cooldown_until": cooldown_until.isoformat(timespec="seconds"),
                    "last_event": "cooldown_waiting",
                },
            )
            time.sleep(2)
            continue

        started_at = datetime.now()
        worker_proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-u", WORKER_SCRIPT],
            cwd=ROOT,
            env=_build_env(),
            **_win_subprocess_silent_kwargs(),
        )
        _log(f"worker started pid={worker_proc.pid}")
        _write_json(
            STATUS_FILE,
            {
                "worker_running": True,
                "worker_pid": worker_proc.pid,
                "restart_count": restart_count,
                "recent_crash_count": len(crash_times),
                "cooldown_until": None,
                "last_event": "worker_started",
            },
        )

        while not _stop and worker_proc.poll() is None:
            if os.path.exists(STOP_FILE):
                _stop = True
                break
            time.sleep(1)

        if worker_proc and worker_proc.poll() is None:
            try:
                worker_proc.terminate()
                worker_proc.wait(timeout=5)
            except Exception:
                pass

        exit_code = worker_proc.poll() if worker_proc else None
        run_seconds = (datetime.now() - started_at).total_seconds()
        if _stop:
            _log("supervisor stopping, worker terminated")
            break

        restart_count += 1
        if run_seconds < 30:
            crash_times.append(datetime.now())
        recent_crashes = len(crash_times)

        backoff_seconds = min(60, max(2, 2 * recent_crashes))
        if recent_crashes >= 5:
            cooldown_until = datetime.now() + timedelta(minutes=10)
            _log(
                "worker crashed frequently, enter cooldown "
                f"10m exit={exit_code} run_seconds={int(run_seconds)}"
            )
            _write_json(
                STATUS_FILE,
                {
                    "worker_running": False,
                    "restart_count": restart_count,
                    "recent_crash_count": recent_crashes,
                    "cooldown_until": cooldown_until.isoformat(timespec="seconds"),
                    "last_exit_code": exit_code,
                    "last_run_seconds": int(run_seconds),
                    "last_event": "cooldown_started",
                },
            )
            continue

        _log(
            "worker exited, scheduling restart "
            f"exit={exit_code} run_seconds={int(run_seconds)} backoff={backoff_seconds}s"
        )
        _write_json(
            STATUS_FILE,
            {
                "worker_running": False,
                "restart_count": restart_count,
                "recent_crash_count": recent_crashes,
                "cooldown_until": None,
                "last_exit_code": exit_code,
                "last_run_seconds": int(run_seconds),
                "next_restart_in_seconds": backoff_seconds,
                "last_event": "worker_restarting",
            },
        )
        time.sleep(backoff_seconds)

    _write_json(
        STATUS_FILE,
        {
            "worker_running": False,
            "restart_count": restart_count,
            "recent_crash_count": len(crash_times),
            "cooldown_until": None,
            "last_event": "supervisor_stopped",
        },
    )
    _remove_file(STOP_FILE)
    _log("auto-trader supervisor stopped")


if __name__ == "__main__":
    main()
