@echo off
rem One-click launcher: Edge with CDP port 9223 (isolated profile) for jw crawlers.
rem Usage: double-click this file, log into jw.ustc.edu.cn in the opened window, keep it open.
tasklist /FI "IMAGENAME eq msedge.exe" | find /I "msedge.exe" >nul
if %errorlevel%==0 (
    echo [WARN] Edge is still running. Close ALL Edge windows first (right-click taskbar icon - Exit), then double-click this file again.
    pause
    exit /b 1
)
start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9223 --user-data-dir="%TEMP%\xiaowo_cdp_profile" https://jw.ustc.edu.cn
echo [OK] Debug Edge started on port 9223. Please log in (CAS), then keep this window open.
pause
