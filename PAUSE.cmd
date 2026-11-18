@echo off
cd /d "%~dp0"
type nul > PAUSE
powershell.exe -NoProfile -Command "Disable-ScheduledTask -TaskName 'ArabiaTrend-Tech-Agent' | Out-Null"
echo Paused future processing. Already-scheduled WordPress posts remain scheduled.
pause
