# Run the XASP real-data platform

The platform does not generate synthetic rows, simulated labels, or heuristic probabilities.
It downloads public Binance minute data, resumes from the last durable watermark, builds
first-touch labels, trains only when enough final real rows exist, and otherwise displays WAIT.

## Install

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -e ".[dev]"
```

## Choose the historical start

Use a UTC millisecond timestamp. For example, obtain it explicitly in Python rather than
copying an unverified number:

```bash
python -c "from datetime import datetime,UTC; print(int(datetime(2020,1,1,tzinfo=UTC).timestamp()*1000))"
```

## Start collection, incremental training, and the dashboard

```bash
xasp-platform --bootstrap-start-ms <TIMESTAMP_MS> --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

On first start, historical download may take time. The dashboard remains WAIT until:

1. real data are durable;
2. horizons have matured into final labels;
3. every horizon has the configured minimum real training rows;
4. a fitted model exists.

The default minimum is 2,000 final rows per horizon. It can be changed for research:

```bash
xasp-platform --bootstrap-start-ms <TIMESTAMP_MS> --minimum-final-rows 5000
```

Lowering the minimum does not improve scientific validity and does not promote trading.

## Durable files

- `data/prices.parquet`: real XRP minute prices
- `data/anchors.parquet`: rolling 15/30/45/60-minute labels
- `data/features.parquet`: causal features
- `data/state.json`: restart watermarks and counters
- `data/predictions.parquet`: immutable prediction ledger
- `models/champion.joblib`: latest fitted research bundle
- `reports/training.json`: untouched temporal-test report by horizon
- `data/platform_status.json`: dashboard state

Restarting the same command resumes the missing tail; it does not rebuild all history.

## Important boundary

A fitted model is still marked `RESEARCH_ONLY`. The UI deliberately keeps the trading decision
at WAIT until walk-forward, calibration, economic simulation, paper trading, and promotion gates
are all passed with reproducible evidence. Model probabilities are real model outputs, but are not
represented as guaranteed or approved trade recommendations.
