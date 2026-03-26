from __future__ import annotations

import unittest
from unittest.mock import patch

from exchange.mexc_futures import MexcFuturesClient


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.headers = {}

    def update(self, headers):
        self.headers.update(headers)

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        return self.responses.pop(0)


class ExchangeTests(unittest.TestCase):
    def test_get_ticker_uses_query_param_endpoint(self) -> None:
        client = MexcFuturesClient()
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "symbol": "BTC_USDT",
                            "lastPrice": "70000",
                            "bid1": "69999",
                            "ask1": "70001",
                            "fundingRate": "0.0001",
                            "holdVol": "123456",
                            "timestamp": 1,
                        },
                    },
                )
            ]
        )
        client.session = session

        snapshot = client.get_ticker("BTC_USDT")

        self.assertEqual(snapshot.symbol, "BTC_USDT")
        self.assertAlmostEqual(snapshot.spread_pct, (70001 - 69999) / 70000)
        self.assertEqual(session.calls[0][0], "https://contract.mexc.com/api/v1/contract/ticker")
        self.assertEqual(session.calls[0][1], {"symbol": "BTC_USDT"})

    def test_get_funding_rate_history_collects_pages(self) -> None:
        client = MexcFuturesClient()
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "resultList": [{"symbol": "BTC_USDT", "fundingRate": "0.0001", "settleTime": 1}],
                            "totalPage": 2,
                        },
                    },
                ),
                FakeResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "resultList": [{"symbol": "BTC_USDT", "fundingRate": "0.0002", "settleTime": 2}],
                            "totalPage": 2,
                        },
                    },
                ),
            ]
        )
        client.session = session

        history = client.get_funding_rate_history("BTC_USDT", page_size=500)

        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["fundingRate"], "0.0001")
        self.assertEqual(session.calls[0][0], "https://contract.mexc.com/api/v1/contract/funding_rate/history")
        self.assertEqual(session.calls[0][1], {"symbol": "BTC_USDT", "page_num": 1, "page_size": 500})
        self.assertEqual(session.calls[1][1], {"symbol": "BTC_USDT", "page_num": 2, "page_size": 500})

    @patch("exchange.mexc_futures.time.sleep", return_value=None)
    def test_retry_on_transient_status(self, _sleep) -> None:
        client = MexcFuturesClient()
        session = FakeSession(
            [
                FakeResponse(503, {"success": False}),
                FakeResponse(
                    200,
                    {
                        "success": True,
                        "data": {
                            "symbol": "BTC_USDT",
                            "lastPrice": "70000",
                            "bid1": "69999",
                            "ask1": "70001",
                            "fundingRate": "0.0001",
                            "holdVol": "123456",
                            "timestamp": 1,
                        },
                    },
                ),
            ]
        )
        client.session = session
        client.timeout = 1

        snapshot = client.get_ticker("BTC_USDT")

        self.assertEqual(snapshot.symbol, "BTC_USDT")
        self.assertEqual(client.stats["rest_retries"], 1)


if __name__ == "__main__":
    unittest.main()
