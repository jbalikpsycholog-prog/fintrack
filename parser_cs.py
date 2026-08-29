import csv
import io
import json
from datetime import datetime
from typing import List, Dict, Optional

# Mozne nazvy sloupcu v CSV Ceske sporitelny.
# Poradi aliasu je dulezite: presny nazev sloupce z aktualniho exportu CS
# je vzdy na prvnim miste, aby nedoslo k zamene s podobne pojmenovanym polem
# (napr. "BIC" vs "Bankovni kod protiuctu").
COLUMN_ALIASES = {
    "datum": ["Datum zaúčtování", "Datum zauctovani", "Datum", "datum", "Datum transakce",
              "Datum pohybu", "Datum uhrady", "date", "Datum účtování", "Datum uctovani"],
    "castka": ["Částka", "Castka", "castka", "Castka v mene uctu", "Objem", "amount",
               "Castka v měně účtu", "Částka v měně účtu"],
    "mena": ["Měna", "Mena", "mena", "Mena uctu", "currency", "Měna účtu"],
    "protiucet": ["Protiúčet", "Protiucet", "protiucet", "Cislo protiuctu", "counterparty_account",
                  "Číslo protiúčtu"],
    "nazev_protiuctu": ["Název protiúčtu", "Nazev protiuctu", "nazev_protiuctu", "Nazev uctu prijemce",
                        "Nazev prijemce", "counterparty_name", "Název účtu příjemce", "Název příjemce"],
    "iban": ["IBAN", "iban"],
    "bic": ["BIC", "bic", "SWIFT"],
    "kod_banky": ["Bankovní kód protiúčtu", "Bankovni kod protiuctu", "Kod banky", "kod_banky", "bank_code"],
    "variabilni": ["Variabilní symbol", "Variabilni symbol", "variabilni", "VS", "variable_symbol"],
    "konstantni": ["Konstantní symbol", "Konstantni symbol", "konstantni", "KS", "constant_symbol"],
    "specificke": ["Specifický symbol", "Specificky symbol", "specificke", "SS", "specific_symbol"],
    "typ": ["Typ transakce", "typ", "transaction_type", "Typ"],
    "zprava_pro_me": ["Zpráva pro mě", "Zprava pro me", "zprava_pro_me"],
    "adresa_platce": ["Adresa plátce", "Adresa platce", "adresa_platce"],
    "zprava_pro_prijemce": ["Zpráva pro příjemce", "Zprava pro prijemce", "zprava_pro_prijemce"],
    "adresa_prijemce": ["Adresa příjemce", "Adresa prijemce", "adresa_prijemce"],
    "poznamka": ["Poznámka", "Poznamka", "poznamka", "Note"],
    "kategorie_banky": ["Kategorie", "kategorie", "category"],
    "id_transakce": ["ID transakce", "id_transakce", "Identifikace transakce", "transaction_id", "Cislo pohybu"],
    "cislo_karty": ["Číslo karty", "Cislo karty", "cislo_karty"],
    "misto_karty": ["Místo použití karty", "Misto pouziti karty", "misto_karty"],
    "reference_platby": ["Reference platby", "reference_platby"],
    "nazev_uctu_vlastnika": ["Název účtu vlastníka", "Nazev uctu vlastnika", "nazev_uctu_vlastnika"],
    "cislo_uctu_vlastnika": ["Číslo účtu vlastníka", "Cislo uctu vlastnika", "cislo_uctu_vlastnika"],
    # zbylá pole exportu CS, která nemapujeme na vlastní sloupec, ale chceme je
    # zachovat v "raw" kopii radku pro pripad potreby (viz _row_to_raw_dict)
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
    for ch in ("\xa0", " ", " ", " ", " "):
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


def _cell(row: List[str], col_map: Dict[str, int], field: str) -> str:
    idx = col_map.get(field)
    if idx is not None and idx < len(row):
        return row[idx].strip()
    return ""


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
            datum_raw = _cell(row, col_map, "datum")
            castka_raw = _cell(row, col_map, "castka")

            datum = parse_date(datum_raw)
            castka = parse_amount(castka_raw)

            if not datum or castka == 0.0:
                continue

            zprava_pro_me = _cell(row, col_map, "zprava_pro_me")
            zprava_pro_prijemce = _cell(row, col_map, "zprava_pro_prijemce")
            poznamka = _cell(row, col_map, "poznamka")

            # "popis" = nejvypovidnejsi dostupny text transakce, pouziva se
            # jako kratky souhrn (napr. pro vyhledavani a klasifikacni pravidla).
            # Jednotliva puvodni pole (zprava_pro_me/zprava_pro_prijemce/poznamka)
            # zustavaji zachovana zvlast, aby se nic neztratilo.
            popis = zprava_pro_me or zprava_pro_prijemce or poznamka or ""

            transaction = {
                "datum": datum,
                "castka": castka,
                "mena": _cell(row, col_map, "mena") or "CZK",
                "protiucet": _cell(row, col_map, "protiucet"),
                "nazev_protiuctu": _cell(row, col_map, "nazev_protiuctu"),
                "iban": _cell(row, col_map, "iban"),
                "bic": _cell(row, col_map, "bic"),
                "kod_banky": _cell(row, col_map, "kod_banky"),
                "variabilni": _cell(row, col_map, "variabilni"),
                "konstantni": _cell(row, col_map, "konstantni"),
                "specificke": _cell(row, col_map, "specificke"),
                "typ": _cell(row, col_map, "typ"),
                "popis": popis,
                "zprava_pro_me": zprava_pro_me,
                "adresa_platce": _cell(row, col_map, "adresa_platce"),
                "zprava_pro_prijemce": zprava_pro_prijemce,
                "adresa_prijemce": _cell(row, col_map, "adresa_prijemce"),
                "poznamka": poznamka,
                "kategorie_banky": _cell(row, col_map, "kategorie_banky"),
                "id_transakce": _cell(row, col_map, "id_transakce"),
                "cislo_karty": _cell(row, col_map, "cislo_karty"),
                "misto_karty": _cell(row, col_map, "misto_karty"),
                "reference_platby": _cell(row, col_map, "reference_platby"),
                "nazev_uctu_vlastnika": _cell(row, col_map, "nazev_uctu_vlastnika"),
                "cislo_uctu_vlastnika": _cell(row, col_map, "cislo_uctu_vlastnika"),
                # Cely puvodni radek (vsechny sloupce z exportu, vcetne tech,
                # ktere nemame zvlast namapovane) - pro 100% dohledatelnost.
                "raw": json.dumps(dict(zip(headers, row)), ensure_ascii=False),
            }
            transactions.append(transaction)
        except (IndexError, ValueError):
            continue

    return transactions


def parse_cs_csv(raw_bytes: bytes) -> List[Dict]:
    """Parsuje CSV export z Ceske sporitelny vcetne UTF-16 kodovani."""
    text = decode_bytes(raw_bytes)
    text = text.lstrip('﻿')

    for delimiter in (";", ",", "\t"):
        try:
            result = _try_parse(text, delimiter)
            if result:
                return result
        except csv.Error:
            continue

    return []
