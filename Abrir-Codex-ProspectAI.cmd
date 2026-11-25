@echo off
setlocal
cd /d "%~dp0" || goto erro
set "PROSPECT_CODEX=%LOCALAPPDATA%\Programs\OpenAI\Codex\bin\codex.exe"
if not exist "%PROSPECT_CODEX%" set "PROSPECT_CODEX=codex"
if /i "%~1"=="--verificar" (
    "%PROSPECT_CODEX%" resume --help
    exit /b
)
"%PROSPECT_CODEX%" resume 01a20b13-abd1-7901-8e58-5d21cbd6a1ce
if errorlevel 1 goto erro
exit /b 0

:erro
echo Nao foi possivel retomar a conversa no Codex.
pause
exit /b 1
