@echo off
REM ============================================================
REM Universal Crawler - Web Console Launcher
REM Place this .bat in the project root directory
REM ============================================================

set "PROJECT_DIR=%~dp0"
set "SPIDER_PYTHON=D:\79458\Documents\anaconda3\envs\spider\python.exe"
set "PLAYWRIGHT_BROWSERS_PATH=D:\conda_cache\playwright"
set "TEMP=D:\conda_cache\temp"
set "TMP=D:\conda_cache\temp"

cd /d "%PROJECT_DIR%"

echo ============================================================
echo   Universal Crawler Web Console
echo   URL: http://127.0.0.1:5000
echo   Browser will open in 3 seconds...
echo   Close this window to stop the server.
echo ============================================================
echo.

start "" /min cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:5000"

"%SPIDER_PYTHON%" "web\app.py"

pause