# -*- coding: utf-8 -*-
"""Automaticke parovani dokladu (PDF na Google Disku) k importovanym
bankovnim transakcim - viz dohoda s uzivatelem (zari 2026).

Jak to funguje:
1. Precte konfiguracni soubor doklady_config.txt (korenova slozka, napr.
   "G:\\Muj disk", stejny format jako zaloha_config.txt).
2. Pro dany rok/mesic spocita cestu do prislusne mesicni podslozky podle
   vzoru, ktery uzivatel uz pouziva: "<rok>_VYDAJE\\<rok><mesic 2
   cislice>_Doklady", napr. "2026_VYDAJE\\202601_Doklady" pro leden 2026.
3. Pro kazdy PDF v teto slozce zkusi precist text (pdfplumber) a najit:
   - variabilni symbol (nejspolehlivejsi ukazatel - porovna se s
     Transaction.variable_symbol dane transakce),
   - castku k uhrade (zalozni varianta, pokud VS nesedi/neni k dispozici -
     hleda se cislo za klicovymi slovy jako "Celkem k uhrade" apod.).
4. Mezi transakcemi (source_type="bank", dany rok/mesic, jeste bez
   document_url) se hleda JEDNOZNACNA shoda - podle VS, nebo (kdyz VS
   nevyjde) podle castky, ale jen pokud v danem mesici sedi prave jedna
   transakce. Pri nejednoznacnosti se nic nenavrhuje - radsi zadny navrh
   nez spatny.
5. Vysledek se pouze NAVRHNE (suggested_document_url/suggested_document_name
   na danem radku Transaction) - nikdy se rovnou neuklada do document_url.
   Potvrzeni je vzdy na uzivateli (tlacitko "Ulozit" v UI, stejny princip
   jako u navrhovanych kategorii).

Pokud PDF nema zadny citelny text (napr. naskenovana/vyfocena uctenka bez
textove vrstvy), konfiguracni slozka neexistuje, nebo mesicni podslozka
chybi (Google Disk pro pocitace zrovna nebezi apod.), prislusne doklady se
proste tise preskoci - zadna chyba, zadny pad aplikace.
"""
import os
import re
import glob
from pathlib import Path

try:
    import pdfplumber  # noqa: F401
    PDFPLUMBER_AVAILABLE = True
except Exception:
    PDFPLUMBER_AVAILABLE = False

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "doklady_config.txt")

_VS_RE = re.compile(r"variabiln[ií]\s*symbol\s*[:.]?\s*(\d{3,15})", re.IGNORECASE)

# Klicova slova, u kterych hledame castku k uhrade - v poradi dulezitosti
# (prvni nalezene v textu dokladu se pouzije).
_AMOUNT_KEYWORDS = [
    r"celkem\s*k\s*[uú]hrad[eě]",
    r"[cč][aá]stka\s*k\s*[uú]hrad[eě]",
    r"k\s*[uú]hrad[eě]",
    r"celkem\s*k\s*platb[eě]",
    r"celkov[aá]\s*[cč][aá]stka",
    r"total",
]

# Cislo ve formatu "1 234,56" / "1234.56" / "645,00", pripadne s "Kc"/"Kč" za nim.
_AMOUNT_RE = re.compile(r"([0-9][0-9\s\xa0]*[.,][0-9]{2})")


def read_config_root():
    """Vrati korenovou cestu ke slozkam s doklady (napr. "G:\\Muj disk"),
    nebo None, pokud soubor neexistuje/je prazdny/zakomentovany."""
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except Exception:
        return None
    return None


def month_folder(root, year, month):
    """Slozi cestu do mesicni podslozky s doklady podle vzoru uzivatele:
    <root>/<rok>_VYDAJE/<rok><mesic 2 cislice>_Doklady"""
    year_folder = f"{year}_VÝDAJE"
    month_folder_name = f"{year}{month:02d}_Doklady"
    return os.path.join(root, year_folder, month_folder_name)


def _extract_text(pdf_path):
    if not PDFPLUMBER_AVAILABLE:
        return ""
    try:
        import pdfplumber
        parts = []
        with pdfplumber.open(pdf_path) as pdf:
            # castka k uhrade i variabilni symbol byvaji na prvni strane -
            # pro rychlost a spolehlivost staci prohledat prvnich par stran.
            for page in pdf.pages[:3]:
                parts.append(page.extract_text() or "")
        return "\n".join(parts)
    except Exception:
        return ""


def _normalize_vs(raw):
    """Porovnava variabilni symboly bez ohledu na vodici nuly ("01234" ==
    "1234"). Vraci normalizovany retezec, nebo None, pokud raw neni cislo."""
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    try:
        return str(int(raw))
    except ValueError:
        return raw


def _find_variable_symbol(text):
    m = _VS_RE.search(text)
    if m:
        return _normalize_vs(m.group(1))
    return None


def _parse_czech_amount(raw):
    """'1 234,56' / '1234.56' / '645,00' -> 1234.56 (float)."""
    cleaned = raw.replace(" ", "").replace("\xa0", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _find_total_amount(text):
    for kw_pattern in _AMOUNT_KEYWORDS:
        for m in re.finditer(kw_pattern, text, re.IGNORECASE):
            # castka byva hned za klicovym slovem (na stejnem radku, klidne
            # oddelena mezerami/tabulatorem od nazvu polozky) - prohledej
            # kratke okno textu za shodou.
            window = text[m.end():m.end() + 60]
            amount_m = _AMOUNT_RE.search(window)
            if amount_m:
                val = _parse_czech_amount(amount_m.group(1))
                if val is not None and val > 0:
                    return val
    return None


def _to_file_uri(path):
    try:
        return Path(path).as_uri()
    except Exception:
        return path


def scan_month_documents(db, Transaction, year, month):
    """Projde mesicni slozku dokladu pro dany rok/mesic a navrhne
    suggested_document_url/suggested_document_name u jednoznacne sparovanych
    transakci (source_type="bank", jeste bez document_url). Nic necommituje
    - volajici (main.py) commit provede sam. Vraci pocet nove navrzenych
    dokladu."""
    root = read_config_root()
    if not root:
        return 0
    folder = month_folder(root, year, month)
    if not os.path.isdir(folder):
        return 0

    pdfs = sorted(
        set(glob.glob(os.path.join(folder, "*.pdf")) + glob.glob(os.path.join(folder, "*.PDF")))
    )
    if not pdfs:
        return 0

    if not PDFPLUMBER_AVAILABLE:
        # Slozka s doklady existuje a jsou v ni PDF, ale chybi knihovna pro
        # jejich cteni - bez tohohle upozorneni by parovani jen tise nikdy
        # nic nenaslo, aniz by uzivatel vedel proc.
        raise RuntimeError(
            "Chybí knihovna pro čtení PDF (pdfplumber) - spusť prosím jednou "
            "setup.bat (dvojklikem), ať se doinstaluje, a pak to zkus znovu."
        )

    candidates = db.query(Transaction).filter(
        Transaction.year == year, Transaction.month == month,
        Transaction.source_type == "bank",
        (Transaction.document_url.is_(None)) | (Transaction.document_url == ""),
    ).all()
    if not candidates:
        return 0

    by_vs = {}
    for t in candidates:
        vs = _normalize_vs(t.variable_symbol)
        if vs:
            by_vs.setdefault(vs, []).append(t)

    suggested_count = 0
    for pdf_path in pdfs:
        text = _extract_text(pdf_path)
        if not text.strip():
            continue  # nejspis naskenovana/vyfocena uctenka bez textove vrstvy

        filename = os.path.basename(pdf_path)
        matched = None

        vs = _find_variable_symbol(text)
        if vs and len(by_vs.get(vs, [])) == 1:
            matched = by_vs[vs][0]

        if matched is None:
            amount = _find_total_amount(text)
            if amount is not None:
                same_amount = [
                    t for t in candidates
                    if not t.suggested_document_url and round(abs(t.amount), 2) == round(amount, 2)
                ]
                if len(same_amount) == 1:
                    matched = same_amount[0]

        if matched is not None and not matched.suggested_document_url:
            matched.suggested_document_url = _to_file_uri(pdf_path)
            matched.suggested_document_name = filename
            suggested_count += 1

    return suggested_count
