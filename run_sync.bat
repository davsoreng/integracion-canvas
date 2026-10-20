@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ============================================================
echo   Sincronizacion Manual Canvas -^> Google Cloud
echo ============================================================
echo.
echo Conectando con Canvas LMS, Google Drive y Google Calendar...
echo.
.\.venv\Scripts\python.exe -c "from src.services.sync_manager import SyncManager; res = SyncManager().run_sync(); print('\nResultado:', res.message)"
echo.
echo Presione cualquier tecla para cerrar esta ventana...
pause > nul
