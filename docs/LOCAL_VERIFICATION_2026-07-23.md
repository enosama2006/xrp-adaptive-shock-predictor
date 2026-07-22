# XASP Local Verification Evidence — 2026-07-23

## Environment

- Operating system: Windows PowerShell
- Repository: clean clone of `main`
- Verified commit: `74c2e179f7905d1e21b69c44625868b570ca903b`
- Python environment: project-managed `.venv`, Python 3.11-compatible dependency set
- Package version: `xasp 1.1.0`

## Commands

```powershell
Set-Location D:\Projects\xasp-live
git fetch origin main
git reset --hard origin/main
.\START_XASP.bat
```

## Observed results

- Editable installation completed successfully.
- Full pytest suite executed by `START_XASP.bat` and completed at 100% with no failures.
- Platform import smoke check passed.
- Uvicorn server process started successfully.
- FastAPI application startup completed.
- Local service listened on `http://127.0.0.1:8654`.
- Dashboard assets returned HTTP 200:
  - `/`
  - `/styles.css`
  - `/app.js`
- Dual-model and status endpoints returned HTTP 200 repeatedly:
  - `/api/status`
  - `/api/models`
  - `/api/models/adaptive-shock/latest`
  - `/api/models/first-touch/latest`
  - `/api/ledger?limit=100`

## Scope of this evidence

This proves that the integration test suite, import path, API server startup, static dashboard delivery, and endpoint routing worked on the stated Windows checkout.

It does **not** yet prove:

- that the 365-day backfill completed;
- that stored history passed coverage/gap checks;
- that either model completed training;
- that either model passed its empirical quality gate;
- that predictions were created or matured;
- that CI, Ruff, mypy, or the JavaScript syntax check passed on this exact commit;
- that the system is suitable for trading.

## Warnings observed and follow-up

The successful run emitted:

- FastAPI deprecation warnings for `@app.on_event` startup/shutdown handlers;
- a pandas future warning when concatenating the empty initial price frame with the first real batch.

Follow-up refactor commits migrated the worker to FastAPI lifespan handling and avoided empty-frame concatenation. Those warning-cleanup commits require the next local verification run.

## Verdict

`CONDITIONAL PASS` for local integration startup on commit `74c2e179...`.

The official model/action state remains `WAIT` until real historical collection, training, untouched temporal evaluation, and model-specific evidence gates complete.