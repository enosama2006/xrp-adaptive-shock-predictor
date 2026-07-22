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

REM Default bootstrap is exactly 365 days before launch, calculated in UTC.
REM A manually supplied XASP_BOOTSTRAP_START_MS still takes precedence.
if not defined XASP_BOOTSTRAP_START_MS (
    for /f %%i in ('"%PYTHON%" -c "from datetime import datetime,UTC,timedelta; print(int((datetime.now(UTC)-timedelta(days=365)).timestamp()*1000))"') do set "XASP_BOOTSTRAP_START_MS=%%i"
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

:error
echo.
echo [XASP] Startup failed. Review the error shown above.
pause
exit /b 1
