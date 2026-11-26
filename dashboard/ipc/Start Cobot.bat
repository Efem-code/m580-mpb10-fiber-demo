@echo off
rem Lexium Cobot controller on the IPC, for the dashboard's Cobot tab (http://127.0.0.1:8765).
rem Needs LexiumCobot.exe (github.com/Efem-code/lexium-cobot/releases) in this folder. Controller only:
rem connect to the cobot (192.168.10.94, on the control network) from its page.
cd /d "%~dp0"
if not exist LexiumCobot.exe (
  echo LexiumCobot.exe isn't in %~dp0. Download it from github.com/Efem-code/lexium-cobot/releases and put it here.
  pause
  exit /b 1
)
LexiumCobot.exe ui --no-browser
pause
