from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from .binance import BinanceDataClient
from .contracts import MarketRecord
from .storage import write_dataset


def _parse_utc(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill governed XASP market datasets")
    parser.add_argument("--start", required=True, help="UTC ISO timestamp")
    parser.add_argument("--end", required=True, help="UTC ISO timestamp")
    parser.add_argument("--output", default="data/raw")
    parser.add_argument("--symbols", nargs="+", default=["XRPUSDT", "BTCUSDT"])
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--oi-period", default="5m")
    parser.add_argument("--skip-derivatives", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    start_ms = _parse_utc(args.start)
    end_ms = _parse_utc(args.end)
    if end_ms <= start_ms:
        raise SystemExit("--end must be after --start")

    records: list[MarketRecord] = []
    with BinanceDataClient() as client:
        for symbol in args.symbols:
            records.extend(
                client.iter_spot_klines(
                    symbol=symbol,
                    interval=args.interval,
                    start_time_ms=start_ms,
                    end_time_ms=end_ms,
                )
            )
        if not args.skip_derivatives:
            records.extend(
                client.iter_funding_rates(
                    symbol="XRPUSDT",
                    start_time_ms=start_ms,
                    end_time_ms=end_ms,
                )
            )
            records.extend(
                client.iter_open_interest_history(
                    symbol="XRPUSDT",
                    period=args.oi_period,
                    start_time_ms=start_ms,
                    end_time_ms=end_ms,
                )
            )

    manifest = write_dataset(
        records,
        output_dir=Path(args.output),
        source="binance_public_rest",
        notes=[
            "Raw public market data; no trading decisions.",
            "Klines are event-time stamped at candle close.",
        ],
    )
    print(manifest.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
