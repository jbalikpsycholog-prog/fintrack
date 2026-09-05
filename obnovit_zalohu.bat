@echo off
echo ============================================
echo  FinTrack - Obnoveni ze zalohy
echo ============================================
echo.

if not exist ".venv" (
    echo [CHYBA] Nenasel jsem slozku .venv - nejdriv aspon jednou spust setup.bat.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

python backup_utils.py restore

echo.
pause
