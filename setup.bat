@echo off
echo ============================================
echo  FinTrack OSVC - Spusteni aplikace
echo ============================================
echo.

REM Kontrola Pythonu (zkusi python i py)
python --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON=python
    goto python_ok
)
py --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON=py
    goto python_ok
)

echo [CHYBA] Python neni nainstalovan nebo neni v PATH.
echo Stahni ho z: https://www.python.org/downloads/
echo Po instalaci RESTARTUJ pocitac a zkus znovu.
pause
exit /b 1

:python_ok
echo Python nalezen: %PYTHON%

REM Vytvoreni virtualniho prostredi (jen poprve)
if not exist ".venv" (
    echo Vytvarim virtualni prostredi...
    %PYTHON% -m venv .venv
)

REM Aktivace virtualniho prostredi
call .venv\Scripts\activate.bat

REM Instalace zavislosti
REM POZN.: pouzivame "python -m pip", ne primo "pip.exe" - na nekterych
REM pocitacich (firemni/IT sprava, Device Guard/WDAC politika) je pip.exe
REM jako samostatny spustitelny soubor blokovan, zatimco python.exe povoleny
REM je. "python -m pip" spusti pip jako soucast jiz povoleneho python.exe,
REM takze se tomuto blokovani vyhne.
echo Instaluji zavislosti (muze trvat par minut)...
%PYTHON% -m pip install -r requirements.txt --quiet --upgrade

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

REM Automaticka zaloha databaze (nejvyse jednou denne)
%PYTHON% backup_utils.py auto

echo.
echo ============================================
echo  Aplikace bezi na: http://localhost:8000
echo  Ukonceni: stisknete Ctrl+C
echo ============================================
echo.

REM Otevri prohlizec
start http://localhost:8000

REM Spust server
%PYTHON% -m uvicorn main:app --host 127.0.0.1 --port 8000

pause
