import pandas as pd
import io
from datetime import datetime

def parse_cs_csv(content: bytes) -> list:
    """Parsuje CSV vypis z Ceske sporitelny."""
    text = None
    for encoding in ["utf-8-sig", "cp1250", "utf-8", "iso-8859-2"]:
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("Nepodarilo se dekodovat soubor.")

    lines = text.splitlines()

    # Najdi radek se zahlavi tabulky
    header_idx = 0
    for i, line in enumerate(lines):
        low = line.lower()
        if ("datum" in low or "date" in low) and ("stka" in low or "amount" in low):
            header_idx = i
            break

    csv_text = "\n".join(lines[header_idx:])

    df = None
    for sep in [";", ","]:
        try:
            tmp = pd.read_csv(io.StringIO(csv_text), sep=sep, dtype=str)
            if len(tmp.columns) >= 3:
                df = tmp
                break
        except Exception:
            continue

    if df is None or df.empty:
        raise ValueError("Nepodarilo se nacist data z CSV.")

    col_map = {}
    for col in df.columns:
        low = col.lower().strip()
        if "datum" in low or "date" in low:
            col_map[col] = "date"
        elif "id pohybu" in low or "pohybu" in low:
            col_map[col] = "cs_id"
        elif "popis" in low or "description" in low or "nazev" in low:
            col_map[col] = "description"
        elif "pozn" in low or "note" in low or "zpr" in low or "message" in low:
            col_map[col] = "note"
        elif "stka" in low or "amount" in low:
            col_map[col] = "amount"
        elif "mena" in low or "currency" in low:
            col_map[col] = "currency"
        elif ("protiuctu" in low or "protistrany" in low or "prijemce" in low
              or "prikazce" in low or "counterparty name" in low
              or "nazev protiuctu" in low):
            col_map[col] = "counterparty_name"
        elif ("cislo protiuctu" in low or "protiucet" in low
              or "counterparty account" in low):
            col_map[col] = "counterparty_account"

    df = df.rename(columns=col_map)

    transactions = []
    for _, row in df.iterrows():
        try:
            raw_date = str(row.get("date", "")).strip()
            if not raw_date or raw_date == "nan":
                continue

            parsed_date = None
            for fmt in ["%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d. %m. %Y"]:
                try:
                    parsed_date = datetime.strptime(raw_date, fmt).date()
                    break
                except ValueError:
                    continue
            if not parsed_date:
                continue

            raw_amount = str(row.get("amount", "0")).strip()
            if raw_amount == "nan" or not raw_amount:
                continue
            raw_amount = raw_amount.replace("\xa0", "").replace("\u00a0", "").replace(" ", "").replace(",", ".")
            amount = float(raw_amount)

            def clean(val):
                s = str(val).strip()
                return "" if s == "nan" else s

            t = {
                "date": parsed_date,
                "cs_transaction_id": clean(row.get("cs_id", "")) or None,
                "description": clean(row.get("description", "")),
                "note": clean(row.get("note", "")),
                "counterparty_name": clean(row.get("counterparty_name", "")),
                "counterparty_account": clean(row.get("counterparty_account", "")),
                "currency": clean(row.get("currency", "CZK")) or "CZK",
                "amount": amount,
                "transaction_type": "income" if amount > 0 else "expense",
            }
            transactions.append(t)
        except Exception:
            continue

    return transactions
