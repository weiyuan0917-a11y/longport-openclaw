import os
from pathlib import Path

from config.env_loader import load_project_env

# Load .env from project root.
_ROOT = Path(__file__).resolve().parents[1]
load_project_env(_ROOT)


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _get_symbols(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return default
    out = []
    seen = set()
    for row in raw.replace("\n", ",").split(","):
        sym = row.strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out or default


class LiveSettings:
    def __init__(self):
        # LongPort credentials (must come from env).
        self.LONGPORT_APP_KEY = os.getenv("LONGPORT_APP_KEY", "").strip()
        self.LONGPORT_APP_SECRET = os.getenv("LONGPORT_APP_SECRET", "").strip()
        self.LONGPORT_ACCESS_TOKEN = os.getenv("LONGPORT_ACCESS_TOKEN", "").strip()

        # Optional runtime settings.
        self.DEFAULT_SYMBOLS = _get_symbols("DEFAULT_SYMBOLS", ["AAPL.US", "MSFT.US", "TSLA.US"])
        self.TRADE_INTERVAL = _get_int("TRADE_INTERVAL", 3600)
        self.MAX_POSITION_PERCENT = _get_float("MAX_POSITION_PERCENT", 0.2)
        self.STOP_LOSS_PERCENT = _get_float("STOP_LOSS_PERCENT", 0.03)

    def missing_longport_fields(self) -> list[str]:
        missing = []
        if not self.LONGPORT_APP_KEY:
            missing.append("LONGPORT_APP_KEY")
        if not self.LONGPORT_APP_SECRET:
            missing.append("LONGPORT_APP_SECRET")
        if not self.LONGPORT_ACCESS_TOKEN:
            missing.append("LONGPORT_ACCESS_TOKEN")
        return missing

    def assert_longport_configured(self) -> None:
        missing = self.missing_longport_fields()
        if missing:
            raise ValueError(
                "Missing required LongPort env vars: "
                + ", ".join(missing)
                + ". Please fill .env (see .env.example)."
            )


live_settings = LiveSettings()
