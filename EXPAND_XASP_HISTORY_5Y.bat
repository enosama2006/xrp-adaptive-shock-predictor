@echo off
setlocal EnableExtensions

cd /d "%~dp0"
set "XASP_HISTORY_DAYS=1825"
set "XASP_BOOTSTRAP_START_MS="
set "XASP_EXPAND_HISTORY=1"

echo [XASP] Five-year observed-history expansion selected.
echo [XASP] Existing data and models will not be deleted.
echo [XASP] Older missing history will be checkpointed and resumed after interruption.
echo [XASP] The server starts only after the requested older range is completed or already covered.
echo.

call START_XASP.bat
exit /b %errorlevel%
