from api.routers.auto_trader import router as auto_trader_router
from api.routers.backtest import router as backtest_router
from api.routers.dashboard_market import router as dashboard_market_router
from api.routers.fees_risk import router as fees_risk_router
from api.routers.notifications import router as notifications_router
from api.routers.options_trade import router as options_trade_router
from api.routers.setup import router as setup_router

__all__ = [
    "auto_trader_router",
    "backtest_router",
    "dashboard_market_router",
    "fees_risk_router",
    "notifications_router",
    "options_trade_router",
    "setup_router",
]

