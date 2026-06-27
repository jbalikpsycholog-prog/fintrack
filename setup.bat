@echo off
chcp 65001 >nul
echo ============================================
echo  FinTrack OSVC - Spusteni aplikace
echo ============================================
echo.

REM Kontrola Pythonu
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [CHYBA] Python neni nainstalovan.
    echo Stahni ho z: https://www.python.org/downloads/
    echo Pri instalaci zaskrtni "Add Python to PATH"!
    pause
    exit /b 1
)

REM Vytvoreni virtualniho prostredi (jen poprvé - pri prvnim spusteni)
if not exist ".venv" (
    echo Vytvarim virtualni prostredi (jen jednou)...
    python -m venv .venv
)

REM Aktivace virtualniho prostredi
call .venv\Scripts\activate.bat

REM Instalace zavislosti
echo Instaluji zavislosti (muze trvat par minut)...
pip install -r requirements.txt --quiet --upgrade

if %errorlevel% neq 0 (
    echo [CHYBA] Nepodarilo se nainstalovat zavislosti!
    pause
    exit /b 1
)

REM Vytvoreni potrebnych slozek
if not exist "static" mkdir static
if not exist "receipts" mkdir receipts
if not exist "templates" (
    echo [CHYBA] Slozka templates chybi! Stahnete aplikaci znovu.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Aplikace bezi na: http://localhost:8000
echo  Ukonceni: stisknete Ctrl+C
echo ============================================
echo.

REM Otevri prohlizec
start http://localhost:8000

REM Spust server
python -m uvicorn main:app --host 127.0.0.1 --port 8000

pause
