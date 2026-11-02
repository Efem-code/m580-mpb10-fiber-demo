@echo off
rem Machine Health demo dashboard. Add --live to read the M580, --lan to show it on TVs/iPads.
cd /d "%~dp0"
python server.py %*
pause
