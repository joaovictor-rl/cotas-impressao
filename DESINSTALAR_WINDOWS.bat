@echo off
rem Remove completamente o controle de cotas de impressao (Windows):
rem servico, painel, atalhos, configuracao e historico.

net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0desinstalar_windows.ps1" -Tudo
echo.
pause
