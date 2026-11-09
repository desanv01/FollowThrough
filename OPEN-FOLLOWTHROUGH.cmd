@echo off
setlocal
title FollowThrough - T3 Run Ledger
mode con cols=140 lines=34 >nul 2>&1
cd /d "%~dp0"
if not defined FOLLOWTHROUGH_PYTHON set "FOLLOWTHROUGH_PYTHON=python"
"%FOLLOWTHROUGH_PYTHON%" -X utf8 -B -m reporting.run_ledger --input "%~dp0docs\research\recorded-development-input.json" --format tui
set "VIEWER_EXIT=%ERRORLEVEL%"
echo.
echo FollowThrough saved-report viewer closed.
pause
exit /b %VIEWER_EXIT%
