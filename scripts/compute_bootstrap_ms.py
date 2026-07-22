from __future__ import annotations

from datetime import UTC, datetime, timedelta


def main() -> None:
    bootstrap = datetime.now(UTC) - timedelta(days=365)
    print(int(bootstrap.timestamp() * 1000))


if __name__ == "__main__":
    main()
