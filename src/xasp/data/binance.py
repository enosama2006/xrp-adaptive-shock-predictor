from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import httpx

from .contracts import MarketRecord


class BinanceDataClient:
    """Minimal deterministic client for public Binance research endpoints.

    The caller controls requested time ranges. Raw responses are converted to canonical
    records without using future information or local timezone assumptions.
    """

    def __init__(
        self,
        *,
        spot_base_url: str = "https://api.binance.com",
        futures_base_url: str = "https://fapi.binance.com",
        timeout_seconds: float = 20.0,
    ) -> None:
        self.spot_base_url = spot_base_url.rstrip("/")
        self.futures_base_url = futures_base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_seconds, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> BinanceDataClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, base_url: str, path: str, params: dict[str, Any]) -> Any:
        response = self._client.get(f"{base_url}{path}", params=params)
        response.raise_for_status()
        return response.json()

    def iter_spot_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_time_ms: int,
        end_time_ms: int,
        limit: int = 1000,
    ) -> Iterator[MarketRecord]:
        cursor = start_time_ms
        symbol = symbol.upper()
        while cursor <= end_time_ms:
            received = int(time.time() * 1000)
            rows = self._get(
                self.spot_base_url,
                "/api/v3/klines",
                {
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": end_time_ms,
                    "limit": limit,
                },
            )
            if not rows:
                break
            for row in rows:
                open_time = int(row[0])
                close_time = int(row[6])
                yield MarketRecord(
                    venue="binance_spot",
                    symbol=symbol,
                    record_type="kline",
                    event_time_ms=close_time,
                    received_time_ms=received,
                    payload={
                        "interval": interval,
                        "open_time_ms": open_time,
                        "close_time_ms": close_time,
                        "open": str(row[1]),
                        "high": str(row[2]),
                        "low": str(row[3]),
                        "close": str(row[4]),
                        "volume": str(row[5]),
                        "quote_volume": str(row[7]),
                        "trade_count": int(row[8]),
                        "taker_buy_base": str(row[9]),
                        "taker_buy_quote": str(row[10]),
                    },
                )
            next_cursor = int(rows[-1][0]) + 1
            if next_cursor <= cursor:
                raise RuntimeError("Binance kline pagination did not advance")
            cursor = next_cursor
            if len(rows) < limit:
                break

    def iter_funding_rates(
        self,
        *,
        symbol: str,
        start_time_ms: int,
        end_time_ms: int,
        limit: int = 1000,
    ) -> Iterator[MarketRecord]:
        cursor = start_time_ms
        symbol = symbol.upper()
        while cursor <= end_time_ms:
            received = int(time.time() * 1000)
            rows = self._get(
                self.futures_base_url,
                "/fapi/v1/fundingRate",
                {
                    "symbol": symbol,
                    "startTime": cursor,
                    "endTime": end_time_ms,
                    "limit": limit,
                },
            )
            if not rows:
                break
            for row in rows:
                event_time = int(row["fundingTime"])
                yield MarketRecord(
                    venue="binance_usdm",
                    symbol=symbol,
                    record_type="funding_rate",
                    event_time_ms=event_time,
                    received_time_ms=received,
                    payload={
                        "funding_rate": str(row["fundingRate"]),
                        "mark_price": str(row.get("markPrice", "")),
                    },
                )
            next_cursor = int(rows[-1]["fundingTime"]) + 1
            if next_cursor <= cursor:
                raise RuntimeError("Binance funding pagination did not advance")
            cursor = next_cursor
            if len(rows) < limit:
                break

    def iter_open_interest_history(
        self,
        *,
        symbol: str,
        period: str,
        start_time_ms: int,
        end_time_ms: int,
        limit: int = 500,
    ) -> Iterator[MarketRecord]:
        cursor = start_time_ms
        symbol = symbol.upper()
        while cursor <= end_time_ms:
            received = int(time.time() * 1000)
            rows = self._get(
                self.futures_base_url,
                "/futures/data/openInterestHist",
                {
                    "symbol": symbol,
                    "period": period,
                    "startTime": cursor,
                    "endTime": end_time_ms,
                    "limit": limit,
                },
            )
            if not rows:
                break
            for row in rows:
                event_time = int(row["timestamp"])
                yield MarketRecord(
                    venue="binance_usdm",
                    symbol=symbol,
                    record_type="open_interest",
                    event_time_ms=event_time,
                    received_time_ms=received,
                    payload={
                        "period": period,
                        "sum_open_interest": str(row["sumOpenInterest"]),
                        "sum_open_interest_value": str(row["sumOpenInterestValue"]),
                    },
                )
            next_cursor = int(rows[-1]["timestamp"]) + 1
            if next_cursor <= cursor:
                raise RuntimeError("Binance OI pagination did not advance")
            cursor = next_cursor
            if len(rows) < limit:
                break
