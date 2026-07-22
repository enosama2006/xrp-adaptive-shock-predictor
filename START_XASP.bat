@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
title XASP Real Data Platform - Port 8654

set "PORT=8654"
set "HOST=127.0.0.1"
set "VENV=.venv"
set "PYTHON=%VENV%\Scripts\python.exe"
set "PIP=%VENV%\Scripts\pip.exe"

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
"%PYTHON%" -c "from xasp.platform_api import create_app; from xasp.platform_runtime_v2 import RealDataPlatformV2; print('[XASP] Import smoke check passed')"
if errorlevel 1 goto :verification_error

REM Default bootstrap is exactly 365 days before launch, calculated in UTC.
REM A manually supplied XASP_BOOTSTRAP_START_MS still takes precedence.
if not defined XASP_BOOTSTRAP_START_MS (
    for /f "usebackq delims=" %%i in (`"%PYTHON%" scripts\compute_bootstrap_ms.py`) do set "XASP_BOOTSTRAP_START_MS=%%i"
)
if not defined XASP_BOOTSTRAP_START_MS goto :error

echo.
echo [XASP] Starting real-data platform...
echo [XASP] URL: http://%HOST%:%PORT%
echo [XASP] Real historical window: one year from %XASP_BOOTSTRAP_START_MS%
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
echo [XASP] Review the failing test or import error above.
pause
exit /b 2

:error
echo.
echo [XASP] Startup failed. Review the error shown above.
pause
exit /b 1
