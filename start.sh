#!/bin/bash
echo "============================================"
echo " FinTrack OSVC - Spusteni aplikace"
echo "============================================"
echo

# Kontrola Pythonu
if ! command -v python3 &>/dev/null; then
    echo "[CHYBA] Python 3 neni nainstalovan."
        echo "Mac: nainstaluj z https://www.python.org/downloads/"
            echo "Linux (Ubuntu/Debian): sudo apt install python3 python3-venv"
                exit 1
                fi

                # Vytvoreni virtualniho prostredi (jen poprvé)
                if [ ! -d ".venv" ]; then
                    echo "Vytvarim virtualni prostredi (jen jednou)..."
                        python3 -m venv .venv
                        fi

                        # Aktivace
                        source .venv/bin/activate

                        # Instalace zavislosti
                        echo "Instaluji zavislosti (muze trvat par minut)..."
                        pip install -r requirements.txt --quiet --upgrade

                        if [ $? -ne 0 ]; then
                            echo "[CHYBA] Nepodarilo se nainstalovat zavislosti!"
                                exit 1
                                fi

                                # Vytvoreni potrebnych slozek
                                mkdir -p static receipts

                                echo
                                echo "============================================"
                                echo " Aplikace bezi na: http://localhost:8000"
                                echo " Ukonceni: stisknete Ctrl+C"
                                echo "============================================"
                                echo

                                # Otevri prohlizec po 2 sekundach (Mac i Linux)
                                (sleep 2 && python3 -m webbrowser "http://localhost:8000") &

                                # Spust server
                                python3 -m uvicorn main:app --host 127.0.0.1 --port 8000
