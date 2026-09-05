#!/bin/bash
echo "============================================"
echo " FinTrack - Obnoveni ze zalohy"
echo "============================================"
echo

if [ ! -d ".venv" ]; then
    echo "[CHYBA] Nenasel jsem slozku .venv - nejdriv aspon jednou spust start.sh."
    exit 1
fi

source .venv/bin/activate

python3 backup_utils.py restore
