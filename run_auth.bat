@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ============================================================
echo   Iniciando Asistente de Autorizacion Google
echo ============================================================
echo.
.\.venv\Scripts\python.exe -u scripts\auth_setup.py
echo.
pause
