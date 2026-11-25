@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Iniciar-ProspectAI.ps1" %*
if errorlevel 1 pause
