from __future__ import annotations

import asyncio
import gzip
import json
import time
from contextlib import suppress
from typing import Any

import websockets

from exchange.mexc_futures import MarketSnapshot


class MexcWsMarketStream:
    def __init__(self, config, symbols: list[str], logger):
        self.cfg = config
        self.symbols = set(symbols)
        self.logger = logger
        self.connected = False
        self.latest_snapshots: dict[str, MarketSnapshot] = {}
        self.last_error: str = ""
        self.last_message_at: int | None = None
        self.last_kline_event_at: int | None = None
        self.market_event_count = 0
        self._update_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._closing = False

    async def start(self) -> None:
        if not self.cfg.websocket_enabled or self._task:
            return
        self._closing = False
        self._task = asyncio.create_task(self._run_forever(), name="mexc-ws-stream")

    async def stop(self) -> None:
        self._closing = True
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self.connected = False

    def get_snapshot(self, symbol: str) -> MarketSnapshot | None:
        return self.latest_snapshots.get(symbol)

    def mark_rest_snapshot(self, snapshot: MarketSnapshot) -> None:
        current = self.latest_snapshots.get(snapshot.symbol)
        if current is None or snapshot.timestamp >= current.timestamp:
            self.latest_snapshots[snapshot.symbol] = snapshot

    async def wait_for_update(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._update_event.wait(), timeout=timeout)
            self._update_event.clear()
            return True
        except TimeoutError:
            return False

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.cfg.websocket_enabled,
            "connected": self.connected,
            "last_message_at": self.last_message_at,
            "last_kline_event_at": self.last_kline_event_at,
            "market_event_count": self.market_event_count,
            "cached_symbols": sorted(self.latest_snapshots.keys()),
            "last_error": self.last_error,
        }

    async def _run_forever(self) -> None:
        while not self._closing:
            try:
                async with websockets.connect(
                    self.cfg.websocket_url,
                    ping_interval=None,
                    close_timeout=5,
                    max_size=2**20,
                ) as ws:
                    self.connected = True
                    self.last_error = ""
                    self.logger.info("WebSocket connected: %s", self.cfg.websocket_url)
                    await self._subscribe(ws)
                    ping_task = asyncio.create_task(self._ping_loop(ws), name="mexc-ws-ping")
                    try:
                        async for raw in ws:
                            await self._handle_message(raw)
                    finally:
                        ping_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await ping_task
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                self.logger.warning("WebSocket error: %s", exc)
            finally:
                self.connected = False
            if not self._closing:
                await asyncio.sleep(self.cfg.websocket_reconnect_seconds)

    async def _subscribe(self, ws) -> None:
        intervals = {self.cfg.timeframe, self.cfg.higher_timeframe}
        for symbol in sorted(self.symbols):
            await ws.send(
                json.dumps(
                    {
                        "method": "sub.ticker",
                        "param": {"symbol": symbol},
                        "gzip": False,
                    }
                )
            )
            for interval in sorted(intervals):
                await ws.send(
                    json.dumps(
                        {
                            "method": "sub.kline",
                            "param": {"symbol": symbol, "interval": interval},
                            "gzip": False,
                        }
                    )
                )

    async def _ping_loop(self, ws) -> None:
        while True:
            await asyncio.sleep(self.cfg.websocket_ping_seconds)
            await ws.send(json.dumps({"method": "ping"}))

    async def _handle_message(self, raw: str | bytes) -> None:
        payload = self._decode_payload(raw)
        if not isinstance(payload, dict):
            return

        channel = payload.get("channel")
        now_ts = int(time.time())
        self.last_message_at = now_ts

        if channel == "push.ticker":
            item = payload.get("data", {})
            symbol = item.get("symbol") or payload.get("symbol")
            if symbol in self.symbols:
                snapshot = MarketSnapshot.from_payload(item, source="ws")
                self.latest_snapshots[symbol] = snapshot
                self.market_event_count += 1
            self._update_event.set()
            return

        if channel == "push.kline":
            symbol = payload.get("symbol")
            data = payload.get("data", {})
            if symbol in self.symbols and data.get("interval") in {self.cfg.timeframe, self.cfg.higher_timeframe}:
                self.last_kline_event_at = now_ts
                self.market_event_count += 1
                self._update_event.set()

    def _decode_payload(self, raw: str | bytes) -> Any:
        if isinstance(raw, bytes):
            try:
                raw = gzip.decompress(raw).decode("utf-8")
            except OSError:
                raw = raw.decode("utf-8")
        return json.loads(raw)
