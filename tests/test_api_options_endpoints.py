import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import app


class _FakeBalance:
    buy_power = 100000
    net_assets = 200000
    currency = "USD"


class _FakeTradeContext:
    def account_balance(self):
        return [_FakeBalance()]

    def today_orders(self):
        return []

    def stock_positions(self):
        class _P:
            channels = []

        return _P()

    def submit_order(self, **_kwargs):
        class _Resp:
            order_id = "ORD-TEST-1"

        return _Resp()


class _FakeQuoteContext:
    def option_chain_expiry_date_list(self, _symbol):
        from datetime import date

        return [date(2026, 6, 19)]

    def option_chain_info_by_date(self, _symbol, _target):
        class _Item:
            price = 200
            call_symbol = "AAPL260619C00200000"
            put_symbol = "AAPL260619P00200000"
            standard = True

        return [_Item()]

    def quote(self, _symbols):
        return []


class TestApiOptionsEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("api.main.ensure_contexts", return_value=(_FakeQuoteContext(), _FakeTradeContext()))
    def test_options_chain(self, _mock_ctx):
        r = self.client.get("/options/chain", params={"symbol": "AAPL.US"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("options", body)
        self.assertGreaterEqual(len(body["options"]), 1)

    @patch("api.main.ensure_contexts", return_value=(_FakeQuoteContext(), _FakeTradeContext()))
    @patch("api.main._ensure_l3_confirmation", return_value=None)
    def test_options_order(self, _mock_auth, _mock_ctx):
        r = self.client.post(
            "/options/order",
            json={
                "legs": [
                    {"symbol": "AAPL260619C00200000", "side": "buy", "contracts": 1, "price": 1.2},
                    {"symbol": "AAPL260619C00210000", "side": "sell", "contracts": 1, "price": 0.7},
                ],
                "confirmation_token": "ok",
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("risk", r.json())


if __name__ == "__main__":
    unittest.main()
