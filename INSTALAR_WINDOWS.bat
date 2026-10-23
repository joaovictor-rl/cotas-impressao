@echo off
rem Instalador do controle de cotas de impressao (Windows).
rem De dois cliques neste arquivo. Ele pede permissao de Administrador sozinho.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Pedindo permissao de Administrador...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar_windows.ps1"
echo.
pause
