import unittest
from datetime import date, datetime, timedelta
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(ROOT, "mcp_server")
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)

from mcp_server.fee_model import estimate_us_option_multi_leg_fee
from mcp_server.options_service import evaluate_option_risk, normalize_legs, run_option_backtest
from mcp_server.backtest_engine import Bar


class TestOptionFeatures(unittest.TestCase):
    def test_multi_leg_fee_breakdown(self):
        result = estimate_us_option_multi_leg_fee(
            [
                {"symbol": "AAPL240621C00200000", "side": "buy", "contracts": 1, "price": 1.2},
                {"symbol": "AAPL240621C00210000", "side": "sell", "contracts": 1, "price": 0.7},
            ]
        )
        self.assertIn("fee_breakdown", result)
        self.assertGreater(result["total_fee"], 0)
        self.assertEqual(result["contracts_total"], 2)

    def test_option_risk_blocks(self):
        legs = normalize_legs(
            [
                {"symbol": "AAPL240621C00200000", "side": "buy", "contracts": 5, "price": 2.5},
                {"symbol": "AAPL240621C00210000", "side": "sell", "contracts": 5, "price": 1.0},
            ]
        )
        risk = evaluate_option_risk(
            legs=legs,
            available_cash=100.0,
            max_loss_threshold=200.0,
            max_capital_usage=150.0,
        )
        self.assertFalse(risk["passed"])
        self.assertGreaterEqual(len(risk["blocks"]), 1)

    def test_option_backtest_templates(self):
        bars = []
        start = date(2025, 1, 1)
        for i in range(120):
            px = 100 + i * 0.2
            bars.append(
                Bar(
                    date=datetime.combine(start + timedelta(days=i), datetime.min.time()),
                    open=px,
                    high=px * 1.01,
                    low=px * 0.99,
                    close=px,
                    volume=100000,
                )
            )

        def _fetch(_symbol: str, _days: int):
            return bars

        for template in ["bull_call_spread", "bear_put_spread", "straddle", "strangle"]:
            out = run_option_backtest(_fetch, "AAPL.US", template=template, days=90, holding_days=15, contracts=1)
            self.assertEqual(out["template"], template)
            self.assertIn("stats", out)
            self.assertIn("fee_breakdown", out["stats"])


if __name__ == "__main__":
    unittest.main()
