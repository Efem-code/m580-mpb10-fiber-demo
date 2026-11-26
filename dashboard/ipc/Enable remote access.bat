@echo off
rem Lets the rig laptop log in over SSH from the control network (192.168.10.x), with its key only. Add -Off to switch it off.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Enable-Remote-Access.ps1" %*
