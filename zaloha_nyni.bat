@echo off
echo ============================================
echo  FinTrack - Vytvorit zalohu ihned
echo ============================================
echo.

if not exist ".venv" (
    echo [CHYBA] Nenasel jsem slozku .venv - nejdriv aspon jednou spust setup.bat.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

python backup_utils.py manual

echo.
pause
