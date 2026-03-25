from __future__ import annotations

import time
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
    hold_vol: float
    timestamp: int
    source: str = "rest"

    @classmethod
    def from_payload(cls, data: dict[str, Any], source: str = "rest") -> "MarketSnapshot":
        last_price = float(data["lastPrice"])
        bid = float(data.get("bid1", last_price))
        ask = float(data.get("ask1", last_price))
        spread_pct = max((ask - bid) / last_price, 0.0) if last_price else 0.0
        return cls(
            symbol=str(data["symbol"]),
            last_price=last_price,
            bid=bid,
            ask=ask,
            funding_rate=float(data.get("fundingRate", 0.0)),
            spread_pct=spread_pct,
            hold_vol=float(data.get("holdVol", 0.0)),
            timestamp=int(data.get("timestamp", 0) or 0),
            source=source,
        )


class MexcFuturesClient:
    base_url = "https://contract.mexc.com"

    def __init__(self, config: Any | None = None, timeout: int = 10):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "mexc-futures-paper-bot/2.0"})
        self.timeout = timeout
        self.cfg = config
        self.stats = {
            "rest_calls": 0,
            "rest_retries": 0,
            "rest_failures": 0,
            "last_error": "",
            "last_success_at": None,
        }

    @property
    def retry_attempts(self) -> int:
        if self.cfg is None:
            return 3
        return max(int(self.cfg.rest_retry_attempts), 1)

    @property
    def retry_backoff_seconds(self) -> float:
        if self.cfg is None:
            return 0.7
        return max(float(self.cfg.rest_retry_backoff_seconds), 0.1)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None
        for attempt in range(1, self.retry_attempts + 1):
            self.stats["rest_calls"] += 1
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code in {429, 500, 502, 503, 504} and attempt < self.retry_attempts:
                    self.stats["rest_retries"] += 1
                    time.sleep(self.retry_backoff_seconds * attempt)
                    continue
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict) and payload.get("success") is False:
                    raise RuntimeError(f"MEXC API error: {payload}")
                self.stats["last_success_at"] = int(time.time())
                return payload.get("data", payload)
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                last_error = exc
                self.stats["last_error"] = str(exc)
                if attempt >= self.retry_attempts:
                    break
                self.stats["rest_retries"] += 1
                time.sleep(self.retry_backoff_seconds * attempt)
        self.stats["rest_failures"] += 1
        if last_error is None:
            raise RuntimeError("Unknown REST error")
        raise last_error

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
        data = self._get("/api/v1/contract/ticker", params={"symbol": symbol})
        if isinstance(data, list):
            for item in data:
                if item.get("symbol") == symbol:
                    data = item
                    break
            else:
                raise ValueError(f"Ticker not found for {symbol}")
        return MarketSnapshot.from_payload(data, source="rest")

    def get_funding_rate(self, symbol: str) -> float:
        data = self._get(f"/api/v1/contract/funding_rate/{symbol}")
        return float(data.get("fundingRate", 0.0))

    def get_klines(
        self,
        symbol: str,
        interval: str = "Min15",
        limit: int = 160,
        start: int | None = None,
        end: int | None = None,
    ) -> pd.DataFrame:
        params: dict[str, Any] = {"interval": interval, "limit": limit}
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end

        data = self._get(f"/api/v1/contract/kline/{symbol}", params=params)
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
