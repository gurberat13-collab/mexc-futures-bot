from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
import requests


@dataclass
class MarketSnapshot:
    symbol: str
    last_price: float
    bid: float
    ask: float
    funding_rate: float
    spread_pct: float


class MexcFuturesClient:
    base_url = "https://contract.mexc.com"

    def __init__(self, timeout: int = 10):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "mexc-futures-paper-bot/1.0"})
        self.timeout = timeout

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("success") is False:
            raise RuntimeError(f"MEXC API error: {payload}")
        return payload.get("data", payload)

    def get_contract_info(self, symbol: str) -> dict[str, Any]:
        data = self._get("/api/v1/contract/detail")
        if isinstance(data, list):
            for item in data:
                if item.get("symbol") == symbol:
                    return item
        elif isinstance(data, dict) and data.get("symbol") == symbol:
            return data
        raise ValueError(f"Contract info not found for {symbol}")

    def get_ticker(self, symbol: str) -> MarketSnapshot:
        data = self._get(f"/api/v1/contract/ticker/{symbol}")
        last_price = float(data["lastPrice"])
        bid = float(data.get("bid1", last_price))
        ask = float(data.get("ask1", last_price))
        spread_pct = max((ask - bid) / last_price, 0.0) if last_price else 0.0
        funding = self.get_funding_rate(symbol)
        return MarketSnapshot(symbol, last_price, bid, ask, funding, spread_pct)

    def get_funding_rate(self, symbol: str) -> float:
        data = self._get(f"/api/v1/contract/funding_rate/{symbol}")
        return float(data.get("fundingRate", 0.0))

    def get_klines(self, symbol: str, interval: str = "Min15", limit: int = 120) -> pd.DataFrame:
        data = self._get(
            f"/api/v1/contract/kline/{symbol}",
            params={"interval": interval, "limit": limit},
        )
        df = pd.DataFrame(
            {
                "time": pd.to_datetime(data["time"], unit="s", utc=True),
                "open": pd.to_numeric(data["open"]),
                "close": pd.to_numeric(data["close"]),
                "high": pd.to_numeric(data["high"]),
                "low": pd.to_numeric(data["low"]),
                "volume": pd.to_numeric(data["vol"]),
                "amount": pd.to_numeric(data["amount"]),
            }
        )
        return df.sort_values("time").reset_index(drop=True)
