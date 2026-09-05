@echo off
echo ============================================
echo  FinTrack - technicky rezim (pro reseni problemu)
echo ============================================
echo.
echo Tohle okno musi zustat otevrene, dokud aplikaci pouzivas.
echo Ukonceni: stisknete Ctrl+C, nebo proste zavrete tohle okno.
echo.
echo (Pro bezne pouzivani je pohodlnejsi Spustit_FinTrack.vbs - ten
echo  zadne okno neotevira, FinTrack bezi na pozadi s ikonkou dole
echo  u hodin. Tohle okno slouzi hlavne k tomu, aby bylo videt, kdyby
echo  neco nefungovalo.)
echo.

if not exist ".venv" (
    echo [CHYBA] Nenasel jsem slozku .venv - nejdriv aspon jednou spust setup.bat.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

echo Aplikace pobezi na: http://localhost:8000
echo.
start http://localhost:8000
python -m uvicorn main:app --host 127.0.0.1 --port 8000

pause
