import csv
import io
from datetime import datetime
from typing import List, Dict, Optional

# Mozne nazvy sloupcu v CSV Ceske sporitelny
COLUMN_ALIASES = {
    "datum": ["Datum", "datum", "Datum transakce", "Datum pohybu", "Datum uhrady", "date",
              "Datum zaúčtování", "Datum zauctovani", "Datum účtování", "Datum uctovani"],
    "castka": ["Castka", "castka", "Castka v mene uctu", "Objem", "amount",
               "Částka", "Castka v měně účtu", "Částka v měně účtu"],
    "mena": ["Mena", "mena", "Mena uctu", "currency", "Měna", "Měna účtu"],
    "protiucet": ["Protiucet", "protiucet", "Cislo protiuctu", "counterparty_account",
                  "Protiúčet", "Číslo protiúčtu"],
    "nazev_protiuctu": ["Nazev protiuctu", "nazev_protiuctu", "Nazev uctu prijemce", "Nazev prijemce", "counterparty_name",
                        "Název protiúčtu", "Název účtu příjemce", "Název příjemce"],
    "kod_banky": ["Kod banky", "kod_banky", "BIC", "bank_code",
                  "Bankovní kód protiúčtu", "Bankovni kod protiuctu"],
    "variabilni": ["Variabilni symbol", "variabilni", "VS", "variable_symbol",
                   "Variabilní symbol"],
    "konstantni": ["Konstantni symbol", "konstantni", "KS", "constant_symbol",
                   "Konstantní symbol"],
    "specificke": ["Specificky symbol", "specificke", "SS", "specific_symbol",
                   "Specifický symbol"],
    "popis": ["Popis", "popis", "Poznamka", "Zprava", "Note", "description", "Zprava pro prijemce",
              "Zpráva pro příjemce", "Zpráva pro mě", "Zprava pro me", "Poznámka"],
    "id_transakce": ["ID transakce", "id_transakce", "Identifikace transakce", "transaction_id", "Cislo pohybu"],
    "typ": ["Typ transakce", "typ", "transaction_type", "Typ"],
}

def decode_bytes(raw_bytes: bytes) -> str:
    """Dekoduje bajty do textu - podporuje UTF-16, UTF-8 i Windows-1250."""
    if raw_bytes[:2] == b'\xff\xfe':
        return raw_bytes[2:].decode('utf-16-le', errors='replace')
    if raw_bytes[:2] == b'\xfe\xff':
        return raw_bytes[2:].decode('utf-16-be', errors='replace')
    for enc in ("utf-8-sig", "utf-8", "windows-1250", "iso-8859-2", "latin-1"):
        try:
            return raw_bytes.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw_bytes.decode("utf-8", errors="replace")

def find_column(headers: List[str], field: str) -> Optional[int]:
    aliases = COLUMN_ALIASES.get(field, [])
    for alias in aliases:
        for i, h in enumerate(headers):
            if h.strip().lower() == alias.strip().lower():
                return i
    for alias in aliases:
        for i, h in enumerate(headers):
            if alias.strip().lower() in h.strip().lower() or h.strip().lower() in alias.strip().lower():
                return i
    return None

def parse_amount(value: str) -> float:
    if not value:
        return 0.0
    v = value.strip()
    for ch in ("\xa0", "\u00a0", "\u202f", "\u2009", " "):
        v = v.replace(ch, "")
    v = v.replace(",", ".")
    parts = v.split(".")
    if len(parts) > 2:
        v = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(v)
    except ValueError:
        return 0.0

def parse_date(value: str) -> Optional[str]:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d. %m. %Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return value

def _find_header_row(rows: list) -> Optional[int]:
    """Najde radek s hlavickami - zkusi find_column na kazdem radku."""
    for i, row in enumerate(rows[:10]):
        if not row or len(row) < 3:
            continue
        headers = [h.strip() for h in row]
        # Pokud najdeme sloupec datum i castka, je to hlavicka
        if find_column(headers, "datum") is not None and find_column(headers, "castka") is not None:
            return i
    return None

def _try_parse(text: str, delimiter: str) -> List[Dict]:
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = list(reader)

    if not rows:
        return []

    header_row_idx = _find_header_row(rows)

    if header_row_idx is None:
        return []

    headers = [h.strip() for h in rows[header_row_idx]]

    col_map = {}
    for field in COLUMN_ALIASES:
        idx = find_column(headers, field)
        if idx is not None:
            col_map[field] = idx

    if "datum" not in col_map or "castka" not in col_map:
        return []

    transactions = []
    for row in rows[header_row_idx + 1:]:
        if not row or all(c.strip() == "" for c in row):
            continue
        try:
            datum_raw = row[col_map["datum"]].strip() if col_map.get("datum") is not None and col_map["datum"] < len(row) else ""
            castka_raw = row[col_map["castka"]].strip() if col_map.get("castka") is not None and col_map["castka"] < len(row) else ""

            datum = parse_date(datum_raw)
            castka = parse_amount(castka_raw)

            if not datum or castka == 0.0:
                continue

            transaction = {
                "datum": datum,
                "castka": castka,
                "mena": row[col_map["mena"]].strip() if col_map.get("mena") is not None and col_map["mena"] < len(row) else "CZK",
                "protiucet": row[col_map["protiucet"]].strip() if col_map.get("protiucet") is not None and col_map["protiucet"] < len(row) else "",
                "nazev_protiuctu": row[col_map["nazev_protiuctu"]].strip() if col_map.get("nazev_protiuctu") is not None and col_map["nazev_protiuctu"] < len(row) else "",
                "kod_banky": row[col_map["kod_banky"]].strip() if col_map.get("kod_banky") is not None and col_map["kod_banky"] < len(row) else "",
                "variabilni": row[col_map["variabilni"]].strip() if col_map.get("variabilni") is not None and col_map["variabilni"] < len(row) else "",
                "konstantni": row[col_map["konstantni"]].strip() if col_map.get("konstantni") is not None and col_map["konstantni"] < len(row) else "",
                "specificke": row[col_map["specificke"]].strip() if col_map.get("specificke") is not None and col_map["specificke"] < len(row) else "",
                "popis": row[col_map["popis"]].strip() if col_map.get("popis") is not None and col_map["popis"] < len(row) else "",
                "id_transakce": row[col_map["id_transakce"]].strip() if col_map.get("id_transakce") is not None and col_map["id_transakce"] < len(row) else "",
                "typ": row[col_map["typ"]].strip() if col_map.get("typ") is not None and col_map["typ"] < len(row) else "",
            }
            transactions.append(transaction)
        except (IndexError, ValueError):
            continue

    return transactions


def parse_cs_csv(raw_bytes: bytes) -> List[Dict]:
    """Parsuje CSV export z Ceske sporitelny vcetne UTF-16 kodovani."""
    text = decode_bytes(raw_bytes)
    text = text.lstrip('\ufeff')

    for delimiter in (";", ",", "\t"):
        try:
            result = _try_parse(text, delimiter)
            if result:
                return result
        except csv.Error:
            continue

    return []
