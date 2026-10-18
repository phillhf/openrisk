@echo off
rem OpenRisk installieren - startet install.ps1 (Parameter werden durchgereicht).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
