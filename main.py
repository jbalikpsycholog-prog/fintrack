import os
from fastapi import FastAPI, Request, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from datetime import date, datetime
from typing import Optional

from database import get_db, init_db, Transaction, Category, ClassificationRule, Budget, ImportBatch
from parser_cs import parse_cs_csv

app = FastAPI(title="FinTrack OSVC")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

os.makedirs("receipts", exist_ok=True)

@app.on_event("startup")
def startup():
    init_db()

MONTHS_CZ = ["Leden","Unor","Brezen","Duben","Kveten","Cerven",
             "Cervenec","Srpen","Zari","Rijen","Listopad","Prosinec"]

def auto_classify(t: dict, rules: list) -> Optional[int]:
    counterparty = (t.get("counterparty_name") or "").lower()
    text = f"{counterparty} {(t.get('description') or '').lower()} {(t.get('note') or '').lower()}"
    for rule in sorted(rules, key=lambda r: -r.priority):
        match = True
        if rule.counterparty_pattern and rule.counterparty_pattern.lower() not in counterparty:
            match = False
        if rule.keyword and rule.keyword.lower() not in text:
            match = False
        if match:
            return rule.category_id
    return None

def get_unclassified_count(db: Session) -> int:
    return db.query(func.count(Transaction.id)).filter(
        Transaction.category_id == None,
        Transaction.excluded == False
    ).scalar() or 0

def base_ctx(request: Request, db: Session) -> dict:
    return {
        "request": request,
        "unclassified_count": get_unclassified_count(db),
        "months_cz": MONTHS_CZ,
        "current_year": date.today().year,
        "current_month": date.today().month,
    }

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, year: int = None, month: int = None, db: Session = Depends(get_db)):
    today = date.today()
    year = year or today.year
    month = month or today.month
    expenses_by_cat = (
        db.query(Category.name, Category.color, func.sum(Transaction.amount).label("total"))
        .join(Transaction, Transaction.category_id == Category.id)
        .filter(Transaction.excluded == False, Transaction.transaction_type == "expense",
                extract("year", Transaction.date) == year, extract("month", Transaction.date) == month)
        .group_by(Category.id).all()
    )
    income_total = db.query(func.sum(Transaction.amount)).filter(
        Transaction.transaction_type == "income", Transaction.excluded == False,
        extract("year", Transaction.date) == year, extract("month", Transaction.date) == month,
    ).scalar() or 0.0
    total_expenses = sum(abs(e.total) for e in expenses_by_cat)
    last_imports = db.query(ImportBatch).order_by(ImportBatch.imported_at.desc()).limit(5).all()
    ctx = base_ctx(request, db)
    ctx.update({"expenses_by_cat": expenses_by_cat, "total_expenses": total_expenses,
                "income_total": income_total, "saldo": income_total - total_expenses,
                "last_imports": last_imports, "year": year, "month": month,
                "years": list(range(2024, today.year + 2))})
    return templates.TemplateResponse("dashboard.html", ctx)

@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request, db: Session = Depends(get_db)):
    ctx = base_ctx(request, db)
    ctx["batches"] = db.query(ImportBatch).order_by(ImportBatch.imported_at.desc()).all()
    return templates.TemplateResponse("import.html", ctx)

@app.post("/import")
async def do_import(request: Request, file: UploadFile = File(...),
                    period_label: str = Form(""), db: Session = Depends(get_db)):
    content = await file.read()
    try:
        parsed = parse_cs_csv(content)
    except Exception as e:
        ctx = base_ctx(request, db)
        ctx.update({"error": f"Chyba pri cteni souboru: {e}",
                    "batches": db.query(ImportBatch).order_by(ImportBatch.imported_at.desc()).all()})
        return templates.TemplateResponse("import.html", ctx)
    batch = ImportBatch(filename=file.filename, period_label=period_label or file.filename)
    db.add(batch)
    db.flush()
    rules = db.query(ClassificationRule).all()
    new_count = dup_count = 0
    for td in parsed:
        if td.get("cs_transaction_id"):
            if db.query(Transaction).filter(Transaction.cs_transaction_id == td["cs_transaction_id"]).first():
                dup_count += 1
                continue
        else:
            existing = db.query(Transaction).filter(
                Transaction.date == td["date"], Transaction.amount == td["amount"],
                Transaction.counterparty_name == td["counterparty_name"]).first()
            if existing:
                dup_count += 1
                continue
        category_id = auto_classify(td, rules)
        t = Transaction(date=td["date"], cs_transaction_id=td.get("cs_transaction_id"),
                        description=td["description"], note=td["note"],
                        counterparty_name=td["counterparty_name"],
                        counterparty_account=td["counterparty_account"],
                        currency=td["currency"], amount=td["amount"],
                        transaction_type=td["transaction_type"],
                        category_id=category_id, import_batch_id=batch.id)
        db.add(t)
        new_count += 1
    db.commit()
    return RedirectResponse(
        url=f"/transactions?batch_id={batch.id}&msg=Importovano+{new_count}+polozek,+{dup_count}+duplikatu+preskoceno",
        status_code=303)

@app.get("/transactions", response_class=HTMLResponse)
def transactions(request: Request, batch_id: int = None, category_id: int = None,
                 t_type: str = None, year: int = None, month: int = None,
                 search: str = None, msg: str = None, db: Session = Depends(get_db)):
    q = db.query(Transaction)
    if batch_id: q = q.filter(Transaction.import_batch_id == batch_id)
    if category_id: q = q.filter(Transaction.category_id == category_id)
    if t_type == "unclassified":
        q = q.filter(Transaction.category_id == None, Transaction.excluded == False)
    elif t_type == "excluded": q = q.filter(Transaction.excluded == True)
    elif t_type == "income":
        q = q.filter(Transaction.transaction_type == "income", Transaction.excluded == False)
    elif t_type == "expense":
        q = q.filter(Transaction.transaction_type == "expense", Transaction.excluded == False)
    if year: q = q.filter(extract("year", Transaction.date) == year)
    if month: q = q.filter(extract("month", Transaction.date) == month)
    if search:
        like = f"%{search}%"
        q = q.filter(Transaction.description.ilike(like) | Transaction.counterparty_name.ilike(like) | Transaction.note.ilike(like))
    trans = q.order_by(Transaction.date.desc()).all()
    categories = db.query(Category).filter(Category.active == True).all()
    ctx = base_ctx(request, db)
    ctx.update({"transactions": trans, "categories": categories, "current_filter": t_type,
                "current_batch": batch_id, "msg": msg, "search": search or "",
                "year": year, "month": month, "years": list(range(2024, date.today().year + 2))})
    return templates.TemplateResponse("transactions.html", ctx)

@app.post("/transactions/{tid}/categorize")
def categorize(tid: int, category_id: str = Form(None), excluded: str = Form("false"),
               save_rule: str = Form("false"), db: Session = Depends(get_db)):
    t = db.query(Transaction).filter(Transaction.id == tid).first()
    if not t: raise HTTPException(404)
    cat_id = int(category_id) if category_id and category_id.strip() else None
    t.category_id = cat_id
    t.excluded = excluded.lower() == "true"
    if save_rule == "true" and cat_id and t.counterparty_name:
        exists = db.query(ClassificationRule).filter(
            ClassificationRule.counterparty_pattern == t.counterparty_name,
            ClassificationRule.category_id == cat_id).first()
        if not exists:
            db.add(ClassificationRule(counterparty_pattern=t.counterparty_name, category_id=cat_id, priority=1))
    db.commit()
    return JSONResponse({"ok": True})

@app.post("/transactions/{tid}/receipt")
async def upload_receipt(tid: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    t = db.query(Transaction).filter(Transaction.id == tid).first()
    if not t: raise HTTPException(404)
    filename = f"{tid}_{file.filename}"
    with open(f"receipts/{filename}", "wb") as f:
        f.write(await file.read())
    t.receipt_filename = filename
    db.commit()
    return JSONResponse({"ok": True, "filename": filename})

@app.get("/receipts/{filename}")
def get_receipt(filename: str):
    path = f"receipts/{filename}"
    if not os.path.exists(path): raise HTTPException(404)
    return FileResponse(path)

@app.post("/transactions/{tid}/delete")
def delete_transaction(tid: int, db: Session = Depends(get_db)):
    t = db.query(Transaction).filter(Transaction.id == tid).first()
    if t:
        db.delete(t)
        db.commit()
    return JSONResponse({"ok": True})

@app.get("/categories", response_class=HTMLResponse)
def categories_page(request: Request, db: Session = Depends(get_db)):
    ctx = base_ctx(request, db)
    ctx.update({"categories": db.query(Category).all(),
                "rules": db.query(ClassificationRule).all()})
    return templates.TemplateResponse("categories.html", ctx)

@app.post("/categories/add")
def add_category(name: str = Form(...), color: str = Form("#6366f1"),
                 is_income: str = Form("false"), db: Session = Depends(get_db)):
    if not db.query(Category).filter(Category.name == name).first():
        db.add(Category(name=name, color=color, is_income=is_income.lower() == "true"))
        db.commit()
    return RedirectResponse(url="/categories", status_code=303)

@app.post("/categories/{cid}/toggle")
def toggle_category(cid: int, db: Session = Depends(get_db)):
    c = db.query(Category).filter(Category.id == cid).first()
    if c:
        c.active = not c.active
        db.commit()
    return RedirectResponse(url="/categories", status_code=303)

@app.post("/rules/{rid}/delete")
def delete_rule(rid: int, db: Session = Depends(get_db)):
    r = db.query(ClassificationRule).filter(ClassificationRule.id == rid).first()
    if r:
        db.delete(r)
        db.commit()
    return RedirectResponse(url="/categories", status_code=303)

@app.get("/reports", response_class=HTMLResponse)
def reports(request: Request, period_type: str = "monthly", year: int = None,
            period_value: int = None, db: Session = Depends(get_db)):
    today = date.today()
    year = year or today.year
    if period_value is None:
        if period_type == "monthly": period_value = today.month
        elif period_type == "quarterly": period_value = (today.month - 1) // 3 + 1
        elif period_type == "halfyear": period_value = 1 if today.month <= 6 else 2
        else: period_value = 0
    def month_list():
        if period_type == "monthly": return [period_value]
        elif period_type == "quarterly":
            return [(period_value-1)*3+1, (period_value-1)*3+2, (period_value-1)*3+3]
        elif period_type == "halfyear":
            return list(range(1,7)) if period_value == 1 else list(range(7,13))
        else: return list(range(1,13))
    months = month_list()
    expense_by_cat = (
        db.query(Category.name, Category.color, Category.id, func.sum(Transaction.amount).label("total"))
        .join(Transaction, Transaction.category_id == Category.id)
        .filter(Transaction.excluded == False, Transaction.transaction_type == "expense",
                extract("year", Transaction.date) == year,
                extract("month", Transaction.date).in_(months))
        .group_by(Category.id).all()
    )
    income_total = db.query(func.sum(Transaction.amount)).filter(
        Transaction.transaction_type == "income", Transaction.excluded == False,
        extract("year", Transaction.date) == year,
        extract("month", Transaction.date).in_(months)).scalar() or 0.0
    total_expenses = sum(abs(e.total) for e in expense_by_cat)
    budgets = db.query(Budget).filter(Budget.period_type == period_type,
        Budget.year == year, Budget.period_value == period_value).all()
    budget_map = {b.category_id: b.amount for b in budgets}
    from dateutil.relativedelta import relativedelta
    trend_data = []
    cur = today.replace(day=1)
    for _ in range(12):
        m_exp = db.query(func.sum(Transaction.amount)).filter(
            Transaction.transaction_type == "expense", Transaction.excluded == False,
            extract("year", Transaction.date) == cur.year,
            extract("month", Transaction.date) == cur.month).scalar() or 0
        m_inc = db.query(func.sum(Transaction.amount)).filter(
            Transaction.transaction_type == "income", Transaction.excluded == False,
            extract("year", Transaction.date) == cur.year,
            extract("month", Transaction.date) == cur.month).scalar() or 0
        trend_data.insert(0, {"label": f"{cur.month:02d}/{cur.year}", "expense": abs(m_exp), "income": m_inc})
        cur -= relativedelta(months=1)
    ctx = base_ctx(request, db)
    ctx.update({"expense_by_cat": expense_by_cat, "income_total": income_total,
                "total_expenses": total_expenses, "saldo": income_total - total_expenses,
                "budget_map": budget_map, "period_type": period_type, "year": year,
                "period_value": period_value, "years": list(range(2024, today.year + 2)),
                "categories": db.query(Category).filter(Category.active == True).all(),
                "trend_data": trend_data})
    return templates.TemplateResponse("reports.html", ctx)

@app.get("/budgets", response_class=HTMLResponse)
def budgets_page(request: Request, db: Session = Depends(get_db)):
    ctx = base_ctx(request, db)
    ctx.update({"budgets": db.query(Budget).all(),
                "categories": db.query(Category).filter(Category.active == True).all()})
    return templates.TemplateResponse("budgets.html", ctx)

@app.post("/budgets/add")
def add_budget(category_id: int = Form(...), period_type: str = Form(...),
               year: int = Form(...), period_value: int = Form(0), amount: float = Form(...),
               db: Session = Depends(get_db)):
    ex = db.query(Budget).filter(Budget.category_id == category_id, Budget.period_type == period_type,
        Budget.year == year, Budget.period_value == period_value).first()
    if ex: ex.amount = amount
    else: db.add(Budget(category_id=category_id, period_type=period_type,
                        year=year, period_value=period_value, amount=amount))
    db.commit()
    return RedirectResponse(url="/budgets", status_code=303)

@app.post("/budgets/{bid}/delete")
def delete_budget(bid: int, db: Session = Depends(get_db)):
    b = db.query(Budget).filter(Budget.id == bid).first()
    if b:
        db.delete(b)
        db.commit()
    return RedirectResponse(url="/budgets", status_code=303)
