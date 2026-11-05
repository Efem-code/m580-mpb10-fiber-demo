@echo off
rem Lexium Cobot controller, for the dashboard's Cobot tab. Controller only: it doesn't start the simulator.
cd /d "%~dp0..\..\lexium-cobot"
python run.py ui --no-browser
pause
