@echo off
rem Drive bridge: Control Expert's PLC simulator runs the real ATV930 (testing only).
rem Options: --max-rpm 1750  --drive 192.168.20.30  --drive-unit 248  --no-ramps
cd /d "%~dp0"
python drive_bridge.py %*
pause
