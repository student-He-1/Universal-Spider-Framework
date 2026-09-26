@echo off
REM ============================================================
REM Universal Crawler - Web Console Launcher
REM Auto-activates conda env "spider" if available, then starts.
REM ============================================================

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

REM Try to activate conda env "spider" (silently fail if not found)
call conda activate spider 2>nul

echo ============================================================
echo   Universal Crawler Web Console
echo   URL: http://127.0.0.1:5000
echo   Browser will open in 3 seconds...
echo   Close this window to stop the server.
echo ============================================================
echo.

start "" /min cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:5000"

python web\app.py

pause
