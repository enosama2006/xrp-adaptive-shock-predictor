@echo off
setlocal EnableExtensions

cd /d "%~dp0"
set "XASP_HISTORY_DAYS=1825"
set "XASP_BOOTSTRAP_START_MS="

echo [XASP] Five-year observed-history expansion selected.
echo [XASP] Existing data and models will not be deleted.
echo [XASP] Monthly checkpoints make interruption and restart safe.
echo.

call START_XASP.bat
exit /b %errorlevel%
