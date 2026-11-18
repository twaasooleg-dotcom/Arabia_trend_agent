@echo off
cd /d "%~dp0"
if exist PAUSE del PAUSE
powershell.exe -NoProfile -Command "Enable-ScheduledTask -TaskName 'ArabiaTrend-Tech-Agent' | Out-Null"
echo Resumed.
pause
