# -*- coding: utf-8 -*-
"""Pomocne funkce pro modul Faktury (vydavani faktur OSVC, zari 2026):
- generovani cisla faktury ve formatu MM+RRRR (napr. "092026" pro zari 2026),
  s "-2"/"-3" priponou pri kolizi vic faktur ve stejnem mesici,
- variabilni symbol odvozeny ze stejnych cislic (banka nepovoli pomlcky),
- QR Platba (format SPAYD dle Ceske bankovni asociace) jako base64 PNG,
- nastaveni dodavatele (jeden radek v InvoiceSettings, "get or create").

Kdyz chybi knihovna qrcode (novy pip zavislost - viz pripominka v setup.bat),
QR kod se proste nevygeneruje (qr_payment_data_uri vrati None) - faktura jde
i tak normalne vytisknout/ulozit, jen bez QR kodu.
"""
import re
import base64
from io import BytesIO
from datetime import datetime, timedelta

try:
    import qrcode  # noqa: F401
    QRCODE_AVAILABLE = True
except Exception:
    QRCODE_AVAILABLE = False


def generate_invoice_number(db, Invoice, issue_date_str):
    """Cislo faktury ve formatu MMRRRR (napr. "092026" pro fakturu vystavenou
    v zari 2026) - pri kolizi (vic faktur vystavenych ve stejnem mesici) se
    pripoji "-2", "-3" atd., dokud cislo neni volne."""
    try:
        d = datetime.strptime(issue_date_str, "%Y-%m-%d")
    except Exception:
        d = datetime.now()
    base = f"{d.month:02d}{d.year}"
    candidate = base
    n = 1
    while db.query(Invoice).filter(Invoice.invoice_number == candidate).first():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def variable_symbol_from_invoice_number(invoice_number):
    """Variabilni symbol smi obsahovat jen cislice - pouzijeme stejne cislice
    jako v cisle faktury (pomlcka pri kolizi se proste vynecha)."""
    digits = re.sub(r"[^0-9]", "", invoice_number or "")
    return digits or None


def get_or_create_settings(db, InvoiceSettings):
    """Vrati jediny radek s udaji dodavatele pro hlavicku faktur - pri prvnim
    pouziti ho zalozi, predvyplneny jiz potvrzenymi udaji (zari 2026)."""
    row = db.query(InvoiceSettings).first()
    if not row:
        row = InvoiceSettings(
            supplier_name="Mgr. et Mgr. Petra Balíková",
            supplier_address="Pazderky 3776/5\n669 02 Znojmo",
            supplier_ic="04351339",
            supplier_dic=None,
            bank_account="4164826309/0800",
            iban="CZ6808000000004164826309",
            vat_note="Nejsem plátce DPH.",
            due_days_default=14,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def spayd_string(iban, amount, variable_symbol, message):
    """Sestavi retezec ve formatu SPAYD (QR Platba, specifikace CBA -
    https://qr-platba.cz/pro-vyvojare/specifikace-formatu/)."""
    iban_clean = (iban or "").replace(" ", "")
    parts = ["SPD*1.0", f"ACC:{iban_clean}", f"AM:{amount:.2f}", "CC:CZK"]
    if variable_symbol:
        parts.append(f"X-VS:{variable_symbol}")
    if message:
        # Hvezdicka a plus maji v SPAYD vyznam oddelovace - ve zprave se
        # nahradi mezerou, aby se format nerozbil.
        safe_msg = re.sub(r"[*+]", " ", message)[:60]
        parts.append(f"MSG:{safe_msg}")
    return "*".join(parts)


def qr_payment_data_uri(iban, amount, variable_symbol, message):
    """QR kod (SPAYD) jako base64 PNG data URI pro primo vlozeni do
    <img src="...">, nebo None (chybi knihovna qrcode / IBAN / castka)."""
    if not QRCODE_AVAILABLE or not iban or not amount:
        return None
    try:
        payload = spayd_string(iban, amount, variable_symbol, message)
        img = qrcode.make(payload, box_size=6, border=2)
        buf = BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception:
        return None


def add_days_to_date(start_date_str, days):
    """Pricte 'days' kalendarnich dnu k datu ('YYYY-MM-DD') - vychozi datum
    splatnosti pri zalozeni nove faktury."""
    try:
        d = datetime.strptime(start_date_str, "%Y-%m-%d")
    except Exception:
        d = datetime.now()
    return (d + timedelta(days=days or 0)).strftime("%Y-%m-%d")


def invoice_total(invoice):
    """Soucet vsech polozek faktury (mnozstvi x jednotkova cena)."""
    return sum((it.quantity or 0) * (it.unit_price or 0) for it in invoice.items)
