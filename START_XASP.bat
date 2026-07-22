@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
title XASP Real Data Platform - Port 8654

set "PORT=8654"
set "HOST=127.0.0.1"
set "VENV=.venv"
set "PYTHON=%VENV%\Scripts\python.exe"
set "PIP=%VENV%\Scripts\pip.exe"

REM Default bootstrap date: 2019-01-01 00:00:00 UTC.
REM Override before launch with: set XASP_BOOTSTRAP_START_MS=1546300800000
if not defined XASP_BOOTSTRAP_START_MS set "XASP_BOOTSTRAP_START_MS=1546300800000"

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
echo [XASP] Starting real-data platform...
echo [XASP] URL: http://%HOST%:%PORT%
echo [XASP] Bootstrap start ms: %XASP_BOOTSTRAP_START_MS%
echo [XASP] Press Ctrl+C to stop the server.
echo.

start "" "http://%HOST%:%PORT%"
"%PYTHON%" -m xasp.platform_app --bootstrap-start-ms %XASP_BOOTSTRAP_START_MS% --host %HOST% --port %PORT%
if errorlevel 1 goto :error

goto :eof

:error
echo.
echo [XASP] Startup failed. Review the error shown above.
pause
exit /b 1
