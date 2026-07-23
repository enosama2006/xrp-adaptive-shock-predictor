@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
title XASP Real Data Platform - Port 8654

set "PORT=8654"
set "HOST=127.0.0.1"
set "VENV=.venv"
set "PYTHON=%VENV%\Scripts\python.exe"
set "PIP=%VENV%\Scripts\pip.exe"
if not defined XASP_HISTORY_DAYS set "XASP_HISTORY_DAYS=365"

if not exist "%PYTHON%" (
    echo [XASP] Creating Python virtual environment...
    py -3.11 -m venv "%VENV%" 2>nul
    if errorlevel 1 (
        python -m venv "%VENV%"
        if errorlevel 1 goto :error
    )
)

echo [XASP] Installing or updating project dependencies...
"%PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto :error
"%PIP%" install -e ".[dev]"
if errorlevel 1 goto :error

echo.
echo [XASP] Running integration checks before server startup...
"%PYTHON%" -m compileall -q src scripts
if errorlevel 1 goto :verification_error
"%PYTHON%" -m pytest -q
if errorlevel 1 goto :verification_error
"%PYTHON%" -c "from xasp.data_integrity import audit_price_store; from xasp.first_passage_discovery import generate_discovery_report; from xasp.history_expansion import expand_history; from xasp.platform_api import create_app; from xasp.platform_runtime_v2 import RealDataPlatformV2; from xasp.price_store import PartitionedPriceStore; print('[XASP] Import smoke check passed')"
if errorlevel 1 goto :verification_error

REM XASP_HISTORY_DAYS controls the requested observed window. A manually supplied
REM XASP_BOOTSTRAP_START_MS still takes precedence for exact reproducibility.
if not defined XASP_BOOTSTRAP_START_MS (
    for /f "usebackq delims=" %%i in (`"%PYTHON%" scripts\compute_bootstrap_ms.py --days %XASP_HISTORY_DAYS%`) do set "XASP_BOOTSTRAP_START_MS=%%i"
)
if not defined XASP_BOOTSTRAP_START_MS goto :error

if /I "%XASP_EXPAND_HISTORY%"=="1" (
    echo.
    echo [XASP] Expanding observed history toward %XASP_BOOTSTRAP_START_MS%...
    echo [XASP] Progress is saved in data\history_expansion_state.json.
    "%PYTHON%" -m xasp.history_expansion --target-start-ms %XASP_BOOTSTRAP_START_MS% --symbol XRPUSDT --root data\prices --legacy data\prices.parquet --state data\history_expansion_state.json --checkpoint-rows 10000 --fail-on-incomplete
    if errorlevel 1 goto :verification_error
)

echo.
echo [XASP] Auditing existing observed price files...
"%PYTHON%" -m xasp.data_integrity --root data\prices --legacy data\prices.parquet --output reports\data_integrity.json --minimum-coverage 0.995 --fail-on-error
if errorlevel 1 goto :verification_error

echo.
echo [XASP] Discovering empirical +10%% / -10%% passage windows through 14 days...
echo [XASP] Hourly anchors reduce overlap; touch times remain minute-precise.
"%PYTHON%" -m xasp.first_passage_discovery --root data\prices --legacy data\prices.parquet --output reports\first_passage_discovery.json >nul
if errorlevel 1 goto :verification_error

echo.
echo [XASP] Starting real-data platform...
echo [XASP] URL: http://%HOST%:%PORT%
echo [XASP] Requested observed history: %XASP_HISTORY_DAYS% days from %XASP_BOOTSTRAP_START_MS%
echo [XASP] Price storage: restart-safe UTC monthly partitions; legacy file is preserved.
echo [XASP] Data integrity report: reports\data_integrity.json
echo [XASP] First-passage discovery: reports\first_passage_discovery.json
echo [XASP] The server will continue collecting new observed data every minute.
echo [XASP] Press Ctrl+C to stop the server.
echo.

start "" "http://%HOST%:%PORT%"
"%PYTHON%" -m xasp.platform_api --bootstrap-start-ms %XASP_BOOTSTRAP_START_MS% --host %HOST% --port %PORT%
if errorlevel 1 goto :error

goto :eof

:verification_error
echo.
echo [XASP] Verification failed. The server was NOT started.
echo [XASP] Review the failing test, import, historical-expansion, data-integrity, or discovery error above.
pause
exit /b 2

:error
echo.
echo [XASP] Startup failed. Review the error shown above.
pause
exit /b 1
