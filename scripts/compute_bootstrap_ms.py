from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import os

DEFAULT_HISTORY_DAYS = 365
MAX_HISTORY_DAYS = 3_650


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compute a UTC historical bootstrap timestamp")
    parser.add_argument(
        "--days",
        type=int,
        default=int(os.environ.get("XASP_HISTORY_DAYS", DEFAULT_HISTORY_DAYS)),
        help="Number of observed calendar days to request before launch",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not 1 <= args.days <= MAX_HISTORY_DAYS:
        raise SystemExit(f"history days must be between 1 and {MAX_HISTORY_DAYS}")
    bootstrap = datetime.now(UTC) - timedelta(days=args.days)
    print(int(bootstrap.timestamp() * 1000))


if __name__ == "__main__":
    main()
