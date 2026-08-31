import os
from collections import Counter, defaultdict
from datetime import datetime
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from jinja2 import Environment, FileSystemLoader, select_autoescape

from database import engine, SessionLocal, Base, Category, Transaction, ClassificationRule, Budget, ImportBatch
from parser_cs import parse_cs_csv

Base.metadata.create_all(bind=engine)

app = FastAPI(title="FinTrack OSVC")

jinja_env = Environment(
    loader=FileSystemLoader("templates"),
    autoescape=select_autoescape(["html"]),
    auto_reload=True,
    cache_size=0,
)


def render(template_name: str, **ctx) -> HTMLResponse:
    t = jinja_env.get_template(template_name)
    return HTMLResponse(t.render(**ctx))


def format_czk(value) -> str:
    """Formatuje castku ceskym zpusobem: mezera jako oddelovac tisicu, carka jako desetinna (napr. 16 469,04)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f"{v:,.2f}"  # "16,469.04" (anglicky formatovane)
    text = text.replace(",", " ").replace(".", ",")  # -> "16 469,04"
    return text


jinja_env.filters["czk"] = format_czk


# (nazev, typ "expense"/"income", vychozi danova relevance)
DEFAULT_CATEGORIES = [
    ("SOFTWARE", "expense", True),
    ("HARDWARE", "expense", True),
    ("PRONAJEM", "expense", True),
    ("TEL/INTERNET", "expense", True),
    ("TESTY", "expense", True),
    ("VOZIDLO", "expense", True),
    ("PEREX", "expense", True),
    ("PENZIJKO", "expense", False),
    ("DAŇ Z PŘÍJMU OSVČ_ZÁLOHA", "expense", False),
    ("DAŇ Z PŘÍJMU OSVČ", "expense", False),
    ("ZP OSVČ_ZÁLOHA", "expense", False),
    ("ZP OSVČ", "expense", False),
    ("SOC.P OSVČ", "expense", False),
    ("SOC.P OSVČ_ZÁLOHA", "expense", False),
    ("OSOBNÍ POTŘEBA", "expense", False),
    ("POŘÍZENÍ HM", "expense", False),
    ("ODPISY HM", "expense", True),
    ("DROB.VYD", "expense", True),
    ("BANK.POPLATKY", "expense", True),
    ("VLASTNÍ PROSTŘEDKY OSVČ", "income", False),
    ("ZDRAVOTNÍ POJ.", "income", True),
    ("PEDAGOG.PRAC.", "income", True),
    ("OSTATNÍ", "income", True),
]


def init_default_categories(db: Session):
    for name, cat_type, default_relevant in DEFAULT_CATEGORIES:
        if not db.query(Category).filter(Category.name == name).first():
            db.add(Category(name=name, is_active=True, category_type=cat_type, default_tax_relevant=default_relevant))
    db.commit()


def cat_name(db, cat_id):
    if not cat_id:
        return None
    c = db.query(Category).filter(Category.id == cat_id).first()
    return c.name if c else None


def _match_key(t: Transaction):
    """Klic pro parovani navrhu: normalizovany popis + typ transakce.
    Nektere platby (typicky od institucí jako VZP) nemaji v popisu (zprava
    pro me/prijemce/poznamka) vubec nic - banka u nich vyplnuje jen nazev
    protiuctu. V takovem pripade se pro parovani pouzije misto prazdneho
    popisu nazev protistrany. Kdyz je prazdne i to, nebo chybi typ transakce,
    se nikdy neparuje (predejde nesmyslnym shodam napric ruznymi platbami)."""
    desc = (t.description or "").strip().lower()
    if not desc:
        desc = (t.counterparty_name or "").strip().lower()
    ttype = (t.transaction_type or "").strip().lower()
    if not desc or not ttype:
        return None
    return (desc, ttype)


def recompute_suggestions(db: Session):
    """Pro vsechny dosud nezarazene bankovni transakce (category_id je prazdne)
    spocita navrh kategorie + danove relevance podle historie jiz zarazenych
    bankovnich transakci se stejnym identifikacnim textem (popis, nebo pokud je
    prazdny tak nazev protistrany - viz _match_key) a typem transakce
    (prichozi/odchozi uhrada apod.). Pri rozporu v historii (ruzne kategorie u
    stejneho klice) se pouzije nejcastejsi shoda. Nikdy nezapisuje do
    category_id/tax_relevant - jen do suggested_category_id/suggested_tax_relevant,
    coz je pouze doporuceni, ktere uzivatel bud potvrdi (OK), nebo si vybere jinak."""
    unclassified = db.query(Transaction).filter(
        Transaction.category_id.is_(None),
        Transaction.source_type == "bank",
    ).all()
    if not unclassified:
        return

    classified = db.query(Transaction).filter(
        Transaction.category_id.isnot(None),
        Transaction.source_type == "bank",
    ).order_by(Transaction.id.asc()).all()

    history = defaultdict(Counter)
    for t in classified:
        key = _match_key(t)
        if key:
            history[key][(t.category_id, bool(t.tax_relevant))] += 1

    changed = False
    for t in unclassified:
        key = _match_key(t)
        suggestion = None
        if key and key in history:
            suggestion = history[key].most_common(1)[0][0]
        new_cat = suggestion[0] if suggestion else None
        new_rel = suggestion[1] if suggestion else None
        if t.suggested_category_id != new_cat or t.suggested_tax_relevant != new_rel:
            t.suggested_category_id = new_cat
            t.suggested_tax_relevant = new_rel
            changed = True
    if changed:
        db.commit()


# Jednorazove (pri kazdem startu appky) spocitani navrhu i pro transakce,
# ktere uz v databazi jsou nezarazene ze starsich importu - pokryje i
# "zpetne" navrhy pro existujici zaznamy, ne jen pro nove naimportovane.
_startup_db = SessionLocal()
try:
    init_default_categories(_startup_db)
    recompute_suggestions(_startup_db)
finally:
    _startup_db.close()


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    db = SessionLocal()
    try:
        init_default_categories(db)
        now = datetime.now()
        cy = now.year
        txns = db.query(Transaction).filter(Transaction.year == cy).all()
        total_income = sum(t.amount for t in txns if t.is_income and t.tax_relevant)
        total_expense = sum(abs(t.amount) for t in txns if not t.is_income and t.tax_relevant)
        saldo = total_income - total_expense
        unclassified_count = db.query(Transaction).filter(
            Transaction.category_id == None, Transaction.tax_relevant == True).count()
        cats = db.query(Category).filter(Category.is_active == True).all()
        cat_expenses = []
        for c in cats:
            s = sum(abs(t.amount) for t in db.query(Transaction).filter(
                Transaction.category_id == c.id, Transaction.year == cy,
                Transaction.tax_relevant == True).all() if not t.is_income)
            if s > 0:
                cat_expenses.append({"name": c.name, "total": s})
        recent = db.query(ImportBatch).order_by(ImportBatch.imported_at.desc()).limit(5).all()
        recent_list = [{"filename": r.filename, "month": r.month, "year": r.year,
                        "count": r.transaction_count,
                        "imported_at": r.imported_at.strftime("%d.%m.%Y %H:%M") if r.imported_at else ""}
                       for r in recent]
        return render("dashboard.html",
                      total_income=total_income, total_expense=total_expense, saldo=saldo,
                      unclassified_count=unclassified_count, category_expenses=cat_expenses,
                      recent_imports=recent_list, current_year=cy, current_month=now.month)
    finally:
        db.close()


@app.get("/import", response_class=HTMLResponse)
async def import_page(request: Request):
    db = SessionLocal()
    try:
        imports = db.query(ImportBatch).order_by(ImportBatch.imported_at.desc()).all()
        imp_list = [{"id": i.id, "filename": i.filename, "month": i.month, "year": i.year,
                     "count": i.transaction_count,
                     "period_label": i.period_label or "",
                     "imported_at": i.imported_at.strftime("%d.%m.%Y %H:%M") if i.imported_at else ""}
                    for i in imports]
        return render("import.html", imports=imp_list, message=None, error=None)
    finally:
        db.close()


@app.post("/import", response_class=HTMLResponse)
async def import_csv(request: Request, file: UploadFile = File(...), period_label: str = Form(...)):
    db = SessionLocal()
    try:
        init_default_categories(db)
        raw = await file.read()
        try:
            transactions_data = parse_cs_csv(raw)
        except Exception as e:
            return render("import.html", imports=[], message=None, error=f"Chyba parsovani: {e}")
        if not transactions_data:
            return render("import.html", imports=[], message=None, error="CSV je prazdne.")
        first_date = transactions_data[0].get("datum", "")
        try:
            d = datetime.strptime(first_date, "%Y-%m-%d")
            iy, im = d.year, d.month
        except Exception:
            iy, im = datetime.now().year, datetime.now().month
        first = transactions_data[0]
        batch = ImportBatch(filename=file.filename, month=im, year=iy,
                            transaction_count=len(transactions_data), imported_at=datetime.now(), period_label=period_label,
                            owner_account_name=first.get("nazev_uctu_vlastnika") or None,
                            owner_account_number=first.get("cislo_uctu_vlastnika") or None)
        db.add(batch)
        db.flush()
        rules = db.query(ClassificationRule).all()
        for td in transactions_data:
            amount = float(td.get("castka", 0))
            is_inc = amount > 0
            cat_id = None
            desc = (td.get("popis") or "").upper()
            cp = (td.get("nazev_protiuctu") or "").upper()
            for rule in rules:
                kw = (rule.description_contains or "").upper()
                if kw and (kw in desc or kw in cp):
                    cat_id = rule.category_id
                    break
            t = Transaction(
                date=td.get("datum"), year=iy, month=im,
                description=td.get("popis", ""),
                counterparty_name=td.get("nazev_protiuctu", ""),
                counterparty_account=td.get("protiucet", ""),
                bank_code=td.get("kod_banky", ""),
                iban=td.get("iban", ""),
                bic=td.get("bic", ""),
                amount=amount, currency=td.get("mena") or "CZK",
                is_income=is_inc,
                category_id=cat_id, tax_relevant=True, source_type="bank",
                import_batch_id=batch.id,
                variable_symbol=td.get("variabilni", ""),
                specific_symbol=td.get("specificke", ""),
                constant_symbol=td.get("konstantni", ""),
                cs_transaction_id=td.get("id_transakce", ""),
                transaction_type=td.get("typ", ""),
                message_for_me=td.get("zprava_pro_me", ""),
                payer_address=td.get("adresa_platce", ""),
                message_for_recipient=td.get("zprava_pro_prijemce", ""),
                recipient_address=td.get("adresa_prijemce", ""),
                note=td.get("poznamka", ""),
                bank_category=td.get("kategorie_banky", ""),
                card_number=td.get("cislo_karty", ""),
                card_location=td.get("misto_karty", ""),
                payment_reference=td.get("reference_platby", ""),
                raw_data=td.get("raw", ""),
            )
            db.add(t)
        db.commit()
        recompute_suggestions(db)
        imports = db.query(ImportBatch).order_by(ImportBatch.imported_at.desc()).all()
        imp_list = [{"id": i.id, "filename": i.filename, "month": i.month, "year": i.year,
                     "count": i.transaction_count,
                     "period_label": i.period_label or "",
                     "imported_at": i.imported_at.strftime("%d.%m.%Y %H:%M") if i.imported_at else ""}
                    for i in imports]
        return render("import.html", imports=imp_list,
                      message=f"Importovano {len(transactions_data)} transakci z {file.filename}.",
                      error=None)
    except Exception as e:
        db.rollback()
        return render("import.html", imports=[], message=None, error=f"Chyba: {e}")
    finally:
        db.close()



@app.post("/import/delete/{batch_id}")
async def delete_import_batch(batch_id: int):
    db = SessionLocal()
    try:
        batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
        if batch:
            # Smazat vsechny transakce z tohoto importu
            db.query(Transaction).filter(Transaction.import_batch_id == batch_id).delete()
            db.delete(batch)
            db.commit()
        return RedirectResponse(url="/import", status_code=303)
    finally:
        db.close()

@app.get("/transactions/new", response_class=HTMLResponse)
async def new_transaction_page(request: Request, added: Optional[str] = None):
    db = SessionLocal()
    try:
        init_default_categories(db)
        cats = db.query(Category).filter(Category.is_active == True).order_by(Category.name).all()
        recent = db.query(Transaction).filter(Transaction.source_type != "bank").order_by(Transaction.id.desc()).limit(25).all()
        recent_list = [{
            "id": t.id, "date": t.date, "source_type": t.source_type or "cash",
            "is_income": t.is_income, "amount": t.amount, "currency": t.currency or "CZK",
            "counterparty": t.counterparty_name or "", "description": t.description or "",
            "category": cat_name(db, t.category_id), "tax_relevant": t.tax_relevant,
        } for t in recent]
        today = datetime.now().strftime("%Y-%m-%d")
        return render("transaction_new.html", categories=cats, recent=recent_list, today=today,
                      msg="Transakce byla přidána." if added else None)
    finally:
        db.close()


@app.post("/transactions/new")
async def create_manual_transaction(
    source_type: str = Form(...),
    direction: str = Form(...),
    date: str = Form(...),
    amount: float = Form(...),
    category: str = Form(""),
    counterparty: str = Form(""),
    description: str = Form(""),
    tax_relevant: str = Form(""),
    document_url: str = Form(""),
):
    db = SessionLocal()
    try:
        if source_type not in ("cash", "nonmonetary"):
            source_type = "cash"
        is_inc = direction == "income"
        signed_amount = abs(amount) if is_inc else -abs(amount)
        try:
            d = datetime.strptime(date, "%Y-%m-%d")
            iy, im = d.year, d.month
        except Exception:
            now = datetime.now()
            iy, im = now.year, now.month
        cat_id = None
        if category:
            c = db.query(Category).filter(Category.name == category).first()
            cat_id = c.id if c else None
        t = Transaction(
            date=date, year=iy, month=im,
            amount=signed_amount, currency="CZK",
            is_income=is_inc,
            category_id=cat_id,
            tax_relevant=bool(tax_relevant),
            source_type=source_type,
            counterparty_name=counterparty.strip() or None,
            description=description.strip() or None,
            document_url=document_url.strip() or None,
        )
        db.add(t)
        db.commit()
        return RedirectResponse(url="/transactions/new?added=1", status_code=303)
    finally:
        db.close()


@app.post("/transactions/{t_id}/delete")
async def delete_transaction(t_id: int, next: str = Form("/transactions")):
    db = SessionLocal()
    try:
        t = db.query(Transaction).filter(Transaction.id == t_id).first()
        if t:
            db.delete(t)
            db.commit()
        safe_next = next if next.startswith("/") else "/transactions"
        return RedirectResponse(url=safe_next, status_code=303)
    finally:
        db.close()


def _manual_tx_form_context(db, t: Transaction):
    """Spolecny kontext pro predvyplneni formulare uprav/kopie z existujici
    rucni (cash/nonmonetary) transakce."""
    return {
        "id": t.id,
        "source_type": t.source_type or "cash",
        "direction": "income" if t.is_income else "expense",
        "date": t.date or "",
        "amount": abs(t.amount) if t.amount else 0,
        "description": t.description or "",
        "counterparty": t.counterparty_name or "",
        "category": cat_name(db, t.category_id),
        "document_url": t.document_url or "",
        "tax_relevant": t.tax_relevant,
    }


def _parse_manual_tx_form(source_type, direction, date, amount, category, db):
    if source_type not in ("cash", "nonmonetary"):
        source_type = "cash"
    is_inc = direction == "income"
    signed_amount = abs(amount) if is_inc else -abs(amount)
    try:
        d = datetime.strptime(date, "%Y-%m-%d")
        iy, im = d.year, d.month
    except Exception:
        now = datetime.now()
        iy, im = now.year, now.month
    cat_id = None
    if category:
        c = db.query(Category).filter(Category.name == category).first()
        cat_id = c.id if c else None
    return source_type, is_inc, signed_amount, iy, im, cat_id


@app.get("/transactions/{t_id}/edit", response_class=HTMLResponse)
async def edit_transaction_page(t_id: int):
    db = SessionLocal()
    try:
        t = db.query(Transaction).filter(Transaction.id == t_id).first()
        if not t or t.source_type == "bank":
            return RedirectResponse(url="/transactions", status_code=303)
        cats = db.query(Category).filter(Category.is_active == True).order_by(Category.name).all()
        return render("transaction_edit.html", mode="edit",
                      t=_manual_tx_form_context(db, t), categories=cats,
                      form_action=f"/transactions/{t.id}/edit", submit_label="Uložit změny",
                      page_title="Upravit transakci")
    finally:
        db.close()


@app.post("/transactions/{t_id}/edit")
async def update_transaction(
    t_id: int,
    source_type: str = Form(...),
    direction: str = Form(...),
    date: str = Form(...),
    amount: float = Form(...),
    category: str = Form(""),
    counterparty: str = Form(""),
    description: str = Form(""),
    tax_relevant: str = Form(""),
    document_url: str = Form(""),
):
    db = SessionLocal()
    try:
        t = db.query(Transaction).filter(Transaction.id == t_id).first()
        if not t or t.source_type == "bank":
            return RedirectResponse(url="/transactions", status_code=303)
        source_type, is_inc, signed_amount, iy, im, cat_id = _parse_manual_tx_form(
            source_type, direction, date, amount, category, db)
        t.source_type = source_type
        t.is_income = is_inc
        t.amount = signed_amount
        t.date = date
        t.year, t.month = iy, im
        t.category_id = cat_id
        t.tax_relevant = bool(tax_relevant)
        t.counterparty_name = counterparty.strip() or None
        t.description = description.strip() or None
        t.document_url = document_url.strip() or None
        db.commit()
        return RedirectResponse(url="/transactions?msg=Transakce+byla+upravena.", status_code=303)
    finally:
        db.close()


@app.get("/transactions/{t_id}/duplicate", response_class=HTMLResponse)
async def duplicate_transaction_page(t_id: int):
    db = SessionLocal()
    try:
        t = db.query(Transaction).filter(Transaction.id == t_id).first()
        if not t or t.source_type == "bank":
            return RedirectResponse(url="/transactions", status_code=303)
        cats = db.query(Category).filter(Category.is_active == True).order_by(Category.name).all()
        return render("transaction_edit.html", mode="duplicate",
                      t=_manual_tx_form_context(db, t), categories=cats,
                      form_action=f"/transactions/{t.id}/duplicate", submit_label="Vytvořit kopii",
                      page_title="Vytvořit kopii transakce")
    finally:
        db.close()


@app.post("/transactions/{t_id}/duplicate")
async def duplicate_transaction(
    t_id: int,
    source_type: str = Form(...),
    direction: str = Form(...),
    date: str = Form(...),
    amount: float = Form(...),
    category: str = Form(""),
    counterparty: str = Form(""),
    description: str = Form(""),
    tax_relevant: str = Form(""),
    document_url: str = Form(""),
):
    db = SessionLocal()
    try:
        orig = db.query(Transaction).filter(Transaction.id == t_id).first()
        if not orig or orig.source_type == "bank":
            return RedirectResponse(url="/transactions", status_code=303)
        source_type, is_inc, signed_amount, iy, im, cat_id = _parse_manual_tx_form(
            source_type, direction, date, amount, category, db)
        t = Transaction(
            date=date, year=iy, month=im,
            amount=signed_amount, currency=orig.currency or "CZK",
            is_income=is_inc,
            category_id=cat_id,
            tax_relevant=bool(tax_relevant),
            source_type=source_type,
            counterparty_name=counterparty.strip() or None,
            description=description.strip() or None,
            document_url=document_url.strip() or None,
        )
        db.add(t)
        db.commit()
        return RedirectResponse(url="/transactions?msg=Transakce+byla+zkop%C3%ADrov%C3%A1na.", status_code=303)
    finally:
        db.close()


@app.get("/transactions", response_class=HTMLResponse)
async def transactions_page(
    request: Request,
    t_type: Optional[str] = None,
    search: Optional[str] = None,
    month: Optional[str] = None,
    category: Optional[str] = None,
    has_doc: Optional[str] = None,
    page: int = 1,
    msg: Optional[str] = None,
):
    db = SessionLocal()
    try:
        q = db.query(Transaction)
        if t_type == "unclassified":
            q = q.filter(Transaction.category_id == None, Transaction.tax_relevant == True)
        elif t_type == "income":
            q = q.filter(Transaction.is_income == True, Transaction.tax_relevant == True)
        elif t_type == "expense":
            q = q.filter(Transaction.is_income == False, Transaction.tax_relevant == True)
        elif t_type == "not_relevant":
            q = q.filter(Transaction.tax_relevant == False)
        if search:
            q = q.filter(
                (Transaction.description.ilike(f"%{search}%")) |
                (Transaction.counterparty_name.ilike(f"%{search}%"))
            )
        if month:
            try:
                parts = month.split("-")
                q = q.filter(Transaction.year == int(parts[0]), Transaction.month == int(parts[1]))
            except Exception:
                pass
        if category:
            cat_obj = db.query(Category).filter(Category.name == category).first()
            q = q.filter(Transaction.category_id == (cat_obj.id if cat_obj else -1))
        if has_doc == "yes":
            q = q.filter(Transaction.document_url.isnot(None), Transaction.document_url != "")
        elif has_doc == "no":
            q = q.filter(
                (Transaction.document_url.is_(None)) | (Transaction.document_url == "")
            )
        q = q.order_by(Transaction.date.desc())
        # Nacteme vsechny odpovidajici transakce najednou - u osobniho pouziti
        # jde o male mnozstvi radku a potrebujeme z nich spocitat soucet za
        # CELY filtr (ne jen za aktualni stranku), proto strankujeme az v Pythonu.
        all_matching = q.all()
        total_count = len(all_matching)
        total_sum = sum(t.amount for t in all_matching)
        per_page = 50
        total_pages = max(1, (total_count + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        txns = all_matching[(page - 1) * per_page: page * per_page]
        cats = db.query(Category).filter(Category.is_active == True).order_by(Category.name).all()
        all_months_q = db.query(Transaction.year, Transaction.month).distinct().order_by(
            Transaction.year.desc(), Transaction.month.desc()).all()
        months_list = [f"{r[0]}-{r[1]:02d}" for r in all_months_q]
        t_list = []
        for t in txns:
            t_list.append({
                "id": t.id,
                "date": t.date,
                "description": t.description or "",
                "counterparty": t.counterparty_name or "",
                "counterparty_account": t.counterparty_account or "",
                "iban": t.iban or "",
                "bic": t.bic or "",
                "bank_code": t.bank_code or "",
                "amount": t.amount,
                "currency": t.currency or "CZK",
                "is_income": t.is_income,
                "tax_relevant": t.tax_relevant,
                "source_type": t.source_type or "bank",
                "document_url": t.document_url or "",
                "category": cat_name(db, t.category_id),
                "suggested_category": cat_name(db, t.suggested_category_id) if not t.category_id else None,
                "suggested_tax_relevant": t.suggested_tax_relevant if not t.category_id else None,
                "transaction_type": t.transaction_type or "",
                "message_for_me": t.message_for_me or "",
                "payer_address": t.payer_address or "",
                "message_for_recipient": t.message_for_recipient or "",
                "recipient_address": t.recipient_address or "",
                "note": t.note or "",
                "bank_category": t.bank_category or "",
                "cs_transaction_id": t.cs_transaction_id or "",
                "card_number": t.card_number or "",
                "card_location": t.card_location or "",
                "payment_reference": t.payment_reference or "",
                "variable_symbol": t.variable_symbol or "",
                "constant_symbol": t.constant_symbol or "",
                "specific_symbol": t.specific_symbol or "",
            })
        return render("transactions.html",
                      transactions=t_list, categories=cats,
                      current_filter=t_type, search=search or "",
                      months=months_list, selected_month=month or "",
                      selected_category=category or "", selected_has_doc=has_doc or "",
                      total_sum=total_sum,
                      page=page, total_pages=total_pages, total_count=total_count, msg=msg)
    finally:
        db.close()


@app.post("/transactions/{t_id}/categorize")
async def categorize_transaction(t_id: int, category: str = Form(""), tax_relevant: str = Form("")):
    db = SessionLocal()
    try:
        t = db.query(Transaction).filter(Transaction.id == t_id).first()
        if not t:
            raise HTTPException(status_code=404)
        # Kategorie a danova relevance jsou ted na sobe nezavisle - kategorii
        # jde priradit i danove nerelevantni transakci (napr. pro prehled,
        # o jake nejcastejsi polozky u nerelevantnich transakci jde).
        if category:
            c = db.query(Category).filter(Category.name == category).first()
            t.category_id = c.id if c else None
        else:
            t.category_id = None
        t.tax_relevant = bool(tax_relevant)
        if t.category_id:
            # Jakmile je transakce skutecne zarazena (potvrzene, ne jen
            # navrzene), navrh uz neni potreba.
            t.suggested_category_id = None
            t.suggested_tax_relevant = None
        db.commit()
        recompute_suggestions(db)
        return RedirectResponse(url="/transactions", status_code=303)
    finally:
        db.close()


@app.post("/transactions/{t_id}/document")
async def set_transaction_document(t_id: int, document_url: str = Form("")):
    db = SessionLocal()
    try:
        t = db.query(Transaction).filter(Transaction.id == t_id).first()
        if not t:
            raise HTTPException(status_code=404)
        t.document_url = document_url.strip() or None
        db.commit()
        return RedirectResponse(url="/transactions", status_code=303)
    finally:
        db.close()


@app.get("/categories", response_class=HTMLResponse)
async def categories_page(request: Request):
    db = SessionLocal()
    try:
        cats = db.query(Category).filter(Category.is_active == True).order_by(Category.category_type.desc(), Category.name).all()
        cat_list = [{"id": c.id, "name": c.name, "cat_type": c.category_type or "expense",
                     "default_tax_relevant": c.default_tax_relevant} for c in cats]
        return render("categories.html", categories=cat_list, msg=None)
    finally:
        db.close()


@app.post("/categories/add")
async def add_category(name: str = Form(...), cat_type: str = Form("expense"), default_tax_relevant: str = Form("")):
    db = SessionLocal()
    try:
        name = name.strip().upper()
        if cat_type not in ("expense", "income"):
            cat_type = "expense"
        if name and not db.query(Category).filter(Category.name == name).first():
            db.add(Category(name=name, is_active=True, category_type=cat_type,
                             default_tax_relevant=bool(default_tax_relevant)))
            db.commit()
        return RedirectResponse(url="/categories", status_code=303)
    finally:
        db.close()


@app.post("/categories/delete/{cat_id}")
async def delete_category(cat_id: int):
    db = SessionLocal()
    try:
        c = db.query(Category).filter(Category.id == cat_id).first()
        if c:
            c.is_active = False
            db.commit()
        return RedirectResponse(url="/categories", status_code=303)
    finally:
        db.close()


@app.get("/budgets", response_class=HTMLResponse)
async def budgets_page(request: Request):
    db = SessionLocal()
    try:
        cats = db.query(Category).filter(Category.is_active == True).order_by(Category.name).all()
        budgets = db.query(Budget).all()
        bud_list = []
        for b in budgets:
            c = db.query(Category).filter(Category.id == b.category_id).first()
            bud_list.append({"id": b.id, "category": c.name if c else "?", "monthly_limit": b.amount})
        cat_list = [{"id": c.id, "name": c.name} for c in cats]
        return render("budgets.html", categories=cat_list, budgets=bud_list, msg=None)
    finally:
        db.close()


@app.post("/budgets/set")
async def set_budget(category: str = Form(...), amount: float = Form(...)):
    db = SessionLocal()
    try:
        c = db.query(Category).filter(Category.name == category).first()
        if c:
            now_year = datetime.now().year
            existing = db.query(Budget).filter(Budget.category_id == c.id, Budget.year == now_year).first()
            if existing:
                existing.amount = amount
            else:
                db.add(Budget(category_id=c.id, year=now_year, amount=amount))
            db.commit()
        return RedirectResponse(url="/budgets", status_code=303)
    finally:
        db.close()


@app.post("/budgets/delete/{bud_id}")
async def delete_budget(bud_id: int):
    db = SessionLocal()
    try:
        b = db.query(Budget).filter(Budget.id == bud_id).first()
        if b:
            db.delete(b)
            db.commit()
        return RedirectResponse(url="/budgets", status_code=303)
    finally:
        db.close()


@app.get("/reports", response_class=HTMLResponse)
async def reports_page(
    request: Request,
    year: Optional[int] = None,
    period: Optional[str] = "month",
    month: Optional[int] = None,
):
    db = SessionLocal()
    try:
        now = datetime.now()
        if not year:
            year = now.year
        if not month:
            month = now.month
        q = db.query(Transaction).filter(Transaction.year == year, Transaction.tax_relevant == True)
        if period == "month":
            q = q.filter(Transaction.month == month)
        elif period == "quarter":
            qs = ((month - 1) // 3) * 3 + 1
            q = q.filter(Transaction.month >= qs, Transaction.month <= qs + 2)
        elif period == "half":
            hs = 1 if month <= 6 else 7
            q = q.filter(Transaction.month >= hs, Transaction.month <= hs + 5)
        txns = q.all()
        total_income = sum(t.amount for t in txns if t.is_income)
        total_expenses = sum(abs(t.amount) for t in txns if not t.is_income)
        saldo = total_income - total_expenses
        cats = db.query(Category).filter(Category.is_active == True).order_by(Category.name).all()
        budgets_map = {}
        for b in db.query(Budget).filter(Budget.year == year).all():
            c = db.query(Category).filter(Category.id == b.category_id).first()
            if c:
                budgets_map[c.name] = b.amount
        by_category = []
        for c in cats:
            spent = sum(abs(t.amount) for t in txns if t.category_id == c.id and not t.is_income)
            if spent == 0:
                continue
            bud = budgets_map.get(c.name)
            remaining = (bud - spent) if bud else None
            by_category.append({"category": c.name, "spent": spent, "budget": bud, "remaining": remaining})
        monthly_labels = []
        monthly_incomes = []
        monthly_expenses_chart = []
        month_names = ["Led", "Uno", "Bre", "Dub", "Kve", "Cer", "Cvc", "Srp", "Zar", "Rij", "Lis", "Pro"]
        for m in range(1, 13):
            mt = db.query(Transaction).filter(
                Transaction.year == year, Transaction.month == m,
                Transaction.tax_relevant == True).all()
            monthly_labels.append(month_names[m - 1])
            monthly_incomes.append(round(sum(t.amount for t in mt if t.is_income), 2))
            monthly_expenses_chart.append(round(sum(abs(t.amount) for t in mt if not t.is_income), 2))
        available_years = list(range(2020, now.year + 2))
        return render("reports.html",
                      total_income=total_income, total_expenses=total_expenses, saldo=saldo,
                      by_category=by_category, monthly_data=True,
                      monthly_labels=monthly_labels, monthly_incomes=monthly_incomes,
                      monthly_expenses=monthly_expenses_chart,
                      years=available_years, selected_year=year, period=period, selected_month=month)
    finally:
        db.close()
