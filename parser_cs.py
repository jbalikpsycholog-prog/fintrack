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

def detect_encoding(raw_bytes: bytes) -> str:
    """Detekce kodovani souboru bez externich zavislosti.
    CS exportuje typicky windows-1250 nebo utf-8-sig.
    """
    for enc in ("utf-8-sig", "utf-8", "windows-1250", "iso-8859-2", "latin-1"):
        try:
            raw_bytes.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "utf-8"

def find_column(headers: List[str], field: str) -> Optional[int]:
    """Najde index sloupce podle aliasu."""
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
    """Prevede textovou castku na float."""
    if not value:
        return 0.0
    v = value.strip().replace("\xa0", "").replace("\u00a0", "").replace("\u202f", "").replace(" ", "")
    v = v.replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return 0.0

def parse_date(value: str) -> Optional[str]:
    """Prevede datum z ruznych formatu CS na ISO 8601 (YYYY-MM-DD)."""
    if not value:
        return None
    value = value.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d. %m. %Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return value

def parse_cs_csv(raw_bytes: bytes) -> List[Dict]:
    """Parsuje CSV export z Ceske sporitelny."""
    encoding = detect_encoding(raw_bytes)
    text = raw_bytes.decode(encoding, errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]

    transactions = []

    for delimiter in (";", ",", "\t"):
        try:
            reader = csv.reader(io.StringIO(text), delimiter=delimiter)
            rows = list(reader)

            if not rows:
                continue

            header_row_idx = None
            for i, row in enumerate(rows):
                if row and any(
                    any(alias.lower() in cell.strip().lower() for alias in aliases)
                    for field, aliases in COLUMN_ALIASES.items()
                    for cell in row
                ):
                    header_row_idx = i
                    break

            if header_row_idx is None:
                for i, row in enumerate(rows):
                    if row and len(row) > 1:
                        header_row_idx = i
                        break

            if header_row_idx is None:
                continue

            headers = rows[header_row_idx]

            col_map = {}
            for field in COLUMN_ALIASES:
                idx = find_column(headers, field)
                if idx is not None:
                    col_map[field] = idx

            if "datum" not in col_map or "castka" not in col_map:
                continue

            for row in rows[header_row_idx + 1:]:
                if not row or all(cell.strip() == "" for cell in row):
                    continue

                try:
                    datum_raw = row[col_map["datum"]] if "datum" in col_map and col_map["datum"] < len(row) else ""
                    castka_raw = row[col_map["castka"]] if "castka" in col_map and col_map["castka"] < len(row) else ""

                    datum = parse_date(datum_raw)
                    castka = parse_amount(castka_raw)

                    transaction = {
                        "datum": datum,
                        "castka": castka,
                        "mena": row[col_map["mena"]].strip() if "mena" in col_map and col_map["mena"] < len(row) else "CZK",
                        "protiucet": row[col_map["protiucet"]].strip() if "protiucet" in col_map and col_map["protiucet"] < len(row) else "",
                        "nazev_protiuctu": row[col_map["nazev_protiuctu"]].strip() if "nazev_protiuctu" in col_map and col_map["nazev_protiuctu"] < len(row) else "",
                        "kod_banky": row[col_map["kod_banky"]].strip() if "kod_banky" in col_map and col_map["kod_banky"] < len(row) else "",
                        "variabilni": row[col_map["variabilni"]].strip() if "variabilni" in col_map and col_map["variabilni"] < len(row) else "",
                        "konstantni": row[col_map["konstantni"]].strip() if "konstantni" in col_map and col_map["konstantni"] < len(row) else "",
                        "specificke": row[col_map["specificke"]].strip() if "specificke" in col_map and col_map["specificke"] < len(row) else "",
                        "popis": row[col_map["popis"]].strip() if "popis" in col_map and col_map["popis"] < len(row) else "",
                        "id_transakce": row[col_map["id_transakce"]].strip() if "id_transakce" in col_map and col_map["id_transakce"] < len(row) else "",
                        "typ": row[col_map["typ"]].strip() if "typ" in col_map and col_map["typ"] < len(row) else "",
                    }

                    if datum and castka != 0.0:
                        transactions.append(transaction)

                except (IndexError, ValueError):
                    continue

            if transactions:
                return transactions

        except csv.Error:
            continue

    return transactions
