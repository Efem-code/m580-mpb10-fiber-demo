@echo off
rem Sets this PC up as the rig's always-on dashboard. Add -CheckOnly to only check, -Remove to undo, -NoScreen to keep it off the touchscreen.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup-IPC.ps1" %*
