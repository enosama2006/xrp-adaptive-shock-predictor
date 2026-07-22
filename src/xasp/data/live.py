from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import websockets

from .contracts import MarketRecord


DEFAULT_STREAMS = (
    "xrpusdt@aggTrade/xrpusdt@bookTicker/"
    "btcusdt@aggTrade/btcusdt@bookTicker/"
    "xrpusdt@forceOrder"
)


def normalize_message(message: dict[str, Any], received_time_ms: int) -> MarketRecord | None:
    data = message.get("data", message)
    event = data.get("e")
    symbol = str(data.get("s", "")).upper()
    if not symbol:
        return None

    if event == "aggTrade":
        return MarketRecord(
            venue="binance_spot",
            symbol=symbol,
            record_type="agg_trade",
            event_time_ms=int(data["T"]),
            received_time_ms=received_time_ms,
            source_sequence=int(data["a"]),
            payload={
                "price": str(data["p"]),
                "quantity": str(data["q"]),
                "buyer_is_maker": bool(data["m"]),
            },
        )
    if "b" in data and "a" in data and "B" in data and "A" in data:
        return MarketRecord(
            venue="binance_spot",
            symbol=symbol,
            record_type="book_ticker",
            event_time_ms=int(data.get("E", received_time_ms)),
            received_time_ms=received_time_ms,
            source_sequence=int(data["u"]) if "u" in data else None,
            payload={
                "bid_price": str(data["b"]),
                "bid_quantity": str(data["B"]),
                "ask_price": str(data["a"]),
                "ask_quantity": str(data["A"]),
            },
        )
    if event == "forceOrder":
        order = data["o"]
        return MarketRecord(
            venue="binance_usdm",
            symbol=symbol,
            record_type="liquidation",
            event_time_ms=int(data["E"]),
            received_time_ms=received_time_ms,
            payload={
                "side": str(order["S"]),
                "order_type": str(order["o"]),
                "quantity": str(order["q"]),
                "price": str(order["p"]),
                "average_price": str(order.get("ap", "")),
                "status": str(order["X"]),
            },
        )
    return None


async def record(*, output: Path, websocket_url: str, flush_every: int = 1) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = 0
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(
                websocket_url,
                ping_interval=20,
                ping_timeout=20,
                max_queue=50_000,
            ) as socket:
                backoff = 1.0
                with output.open("a", encoding="utf-8", buffering=1) as handle:
                    async for raw in socket:
                        received = int(time.time() * 1000)
                        message = json.loads(raw)
                        row = normalize_message(message, received)
                        if row is None:
                            continue
                        handle.write(row.model_dump_json() + "\n")
                        pending += 1
                        if pending >= flush_every:
                            handle.flush()
                            pending = 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # reconnect is deliberate; error remains observable
            error_path = output.with_suffix(output.suffix + ".errors.log")
            with error_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {"at_ms": int(time.time() * 1000), "error": repr(exc)},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Record append-only Binance live streams")
    parser.add_argument("--output", default="data/live/market.jsonl")
    parser.add_argument(
        "--url",
        default=f"wss://stream.binance.com:9443/stream?streams={DEFAULT_STREAMS}",
    )
    args = parser.parse_args()
    asyncio.run(record(output=Path(args.output), websocket_url=args.url))


if __name__ == "__main__":
    main()
