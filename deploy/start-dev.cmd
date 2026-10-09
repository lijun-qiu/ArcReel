@echo off
title ArcReel start-dev
cd /d "%~dp0"
echo === ArcReel start-dev (window kept open by cmd /k) ===
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-dev.ps1"
echo.
echo [%date% %time%] PowerShell finished, exit code %ERRORLEVEL%.
echo This cmd window will stay open. Type exit to close.
