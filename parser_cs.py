import csv
import io
import chardet
from datetime import datetime
from typing import List, Dict, Optional


# Možné názvy sloupců v CSV České spořitelny
COLUMN_ALIASES = {
    "datum": ["Datum", "datum", "Datum transakce", "Datum pohybu", "Datum úhrady", "date"],
    "castka": ["Částka", "castka", "Částka v měně účtu", "Objem", "amount", "Castka"],
    "mena": ["Měna", "mena", "Měna účtu", "currency", "Mena"],
    "protiucet": ["Protiúčet", "protiucet", "Číslo protiúčtu", "Protiúčet a kód banky", "counterparty_account"],
    "nazev_protiuctu": ["Název protiúčtu", "nazev_protiuctu", "Název účtu příjemce", "Název příjemce", "counterparty_name"],
    "kod_banky": ["Kód banky", "kod_banky", "BIC", "bank_code"],
    "variabilni": ["Variabilní symbol", "variabilni", "VS", "variable_symbol"],
    "konstantni": ["Konstantní symbol", "konstantni", "KS", "constant_symbol"],
    "specificke": ["Specifický symbol", "specificke", "SS", "specific_symbol"],
    "popis": ["Popis", "popis", "Poznámka", "Zpráva", "Note", "description", "Zpráva pro příjemce"],
    "id_transakce": ["ID transakce", "id_transakce", "Identifikace transakce", "transaction_id", "Číslo pohybu"],
    "typ": ["Typ transakce", "typ", "transaction_type", "Typ"],
}


def detect_encoding(raw_bytes: bytes) -> str:
    """Detekce kódování souboru."""
    result = chardet.detect(raw_bytes)
    encoding = result.get("encoding", "utf-8") or "utf-8"
    # ČS používá typicky windows-1250 nebo utf-8-sig
    if encoding.lower() in ("ascii",):
        encoding = "windows-1250"
    return encoding


def find_column(headers: List[str], field: str) -> Optional[int]:
    """Najde index sloupce podle aliasů."""
    aliases = COLUMN_ALIASES.get(field, [])
    for alias in aliases:
        for i, h in enumerate(headers):
            if h.strip().lower() == alias.strip().lower():
                return i
    # Fuzzy: contains
    for alias in aliases:
        for i, h in enumerate(headers):
            if alias.strip().lower() in h.strip().lower() or h.strip().lower() in alias.strip().lower():
                return i
    return None


def parse_amount(value: str) -> float:
    """Převede textovou částku na float. Formát ČS: '1 234,56' nebo '-1234.56'"""
    if not value:
        return 0.0
    v = value.strip().replace("\xa0", "").replace("\u00a0", "").replace(" ", "").replace("\N{NO-BREAK SPACE}", "")
    v = v.replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return 0.0


def parse_date(value: str) -> Optional[str]:
    """Pokusí se parsovat datum v různých formátech."""
    if not value:
        return None
    value = value.strip()
    formats = [
        "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y",
        "%d.%m.%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return value  # Vrátit jak je, pokud nelze parsovat


def parse_cs_csv(file_bytes: bytes) -> List[Dict]:
    """
    Parsuje CSV výpis z České spořitelny.
    Vrací seznam slovníků s normalizovanými klíči.
    """
    encoding = detect_encoding(file_bytes)
    
    # Zkus různá kódování
    text = None
    for enc in [encoding, "utf-8-sig", "windows-1250", "utf-8", "latin-1", "cp1250"]:
        try:
            text = file_bytes.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    
    if text is None:
        raise ValueError("Nelze dekódovat soubor CSV. Zkuste jiné kódování.")

    # Najdi začátek datové části (řádek s hlavičkou sloupců)
    lines = text.splitlines()
    header_row = None
    header_idx = 0
    
    for i, line in enumerate(lines):
        # Hledáme řádek který obsahuje klíčová slova
        line_lower = line.lower()
        if any(kw in line_lower for kw in ["datum", "částka", "castka", "objem", "amount"]):
            header_row = line
            header_idx = i
            break
    
    if header_row is None:
        # Zkus první neprázdný řádek
        for i, line in enumerate(lines):
            if line.strip():
                header_row = line
                header_idx = i
                break
    
    if header_row is None:
        raise ValueError("CSV soubor neobsahuje rozpoznatelnou hlavičku.")

    # Parsuj CSV od hlavičky
    data_text = "\n".join(lines[header_idx:])
    
    # Detekuj oddělovač
    separator = ";"
    if header_row.count(",") > header_row.count(";"):
        separator = ","
    
    reader = csv.reader(io.StringIO(data_text), delimiter=separator)
    rows = list(reader)
    
    if not rows:
        raise ValueError("CSV soubor neobsahuje žádná data.")
    
    headers = rows[0]
    
    # Namapuj sloupce
    col_map = {}
    for field in COLUMN_ALIASES:
        idx = find_column(headers, field)
        if idx is not None:
            col_map[field] = idx

    transactions = []
    
    for row_idx, row in enumerate(rows[1:], start=1):
        if not row or all(cell.strip() == "" for cell in row):
            continue  # Přeskočit prázdné řádky
        
        # Pomocná funkce pro získání hodnoty
        def get_val(field):
            idx = col_map.get(field)
            if idx is not None and idx < len(row):
                return row[idx].strip()
            return ""
        
        castka_raw = get_val("castka")
        if not castka_raw:
            continue  # Přeskočit řádky bez částky
        
        castka = parse_amount(castka_raw)
        datum_raw = get_val("datum")
        datum = parse_date(datum_raw)
        
        if not datum:
            continue  # Přeskočit řádky bez data
        
        # Generuj ID transakce
        id_trans = get_val("id_transakce")
        if not id_trans:
            # Vytvoř hash z dostupných dat
            protiucet = get_val("protiucet")
            popis = get_val("popis")
            id_trans = f"{datum}_{castka}_{protiucet}_{popis}"[:80]
        
        transaction = {
            "cs_transaction_id": id_trans,
            "date": datum,
            "amount": castka,
            "currency": get_val("mena") or "CZK",
            "counterparty_account": get_val("protiucet"),
            "counterparty_name": get_val("nazev_protiuctu"),
            "bank_code": get_val("kod_banky"),
            "variable_symbol": get_val("variabilni"),
            "constant_symbol": get_val("konstantni"),
            "specific_symbol": get_val("specificke"),
            "description": get_val("popis"),
            "transaction_type": get_val("typ"),
            "is_income": castka > 0,
        }
        transactions.append(transaction)
    
    return transactions
