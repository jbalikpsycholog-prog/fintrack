@echo off
echo ============================================
echo  FinTrack OSVC - Instalace a spusteni
echo ============================================
echo.

REM Zkontroluj Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo CHYBA: Python neni nainstalovan!
    echo Stahni Python z https://python.org
    pause
    exit /b 1
)

REM Vytvor slozku pro statické soubory
if not exist "static" mkdir static
if not exist "receipts" mkdir receipts
if not exist "templates" (
    echo CHYBA: Slozka templates chybi!
    pause
    exit /b 1
)

REM Nainstaluj zavislosti
echo Instaluji zavislosti (muze trvat par minut)...
pip install -r requirements.txt --quiet

if %errorlevel% neq 0 (
    echo CHYBA: Nepodařilo se nainstalovat zavislosti!
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Aplikace se spousti na http://localhost:8000
echo  Zavreni: stiskni Ctrl+C
echo ============================================
echo.

REM Otevri prohlizec
start http://localhost:8000

REM Spust server
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

pause
