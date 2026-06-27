import os
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


DEFAULT_CATEGORIES = [
    "SOFTWARE", "HARDWARE", "PRONAJEM", "TEL/INTERNET", "TESTY",
    "DROB.ADMIN", "DROB.OST.", "FIN.SLUZBY", "VOZIDLO", "PEREX", "PRIJMY"
]


def init_default_categories(db: Session):
    for name in DEFAULT_CATEGORIES:
        if not db.query(Category).filter(Category.name == name).first():
            db.add(Category(name=name, is_active=True))
    db.commit()


def cat_name(db, cat_id):
    if not cat_id:
        return None
    c = db.query(Category).filter(Category.id == cat_id).first()
    return c.name if c else None


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    db = SessionLocal()
    try:
        init_default_categories(db)
        now = datetime.now()
        cy = now.year
        txns = db.query(Transaction).filter(Transaction.year == cy).all()
        total_income = sum(t.amount for t in txns if t.is_income and not t.excluded)
        total_expense = sum(abs(t.amount) for t in txns if not t.is_income and not t.excluded)
        saldo = total_income - total_expense
        unclassified_count = db.query(Transaction).filter(
            Transaction.category_id == None, Transaction.excluded == False).count()
        cats = db.query(Category).filter(Category.is_active == True).all()
        cat_expenses = []
        for c in cats:
            s = sum(abs(t.amount) for t in db.query(Transaction).filter(
                Transaction.category_id == c.id, Transaction.year == cy,
                Transaction.excluded == False).all() if not t.is_income)
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
                     "imported_at": i.imported_at.strftime("%d.%m.%Y %H:%M") if i.imported_at else ""}
                    for i in imports]
        return render("import.html", imports=imp_list, message=None, error=None)
    finally:
        db.close()


@app.post("/import", response_class=HTMLResponse)
async def import_csv(request: Request, file: UploadFile = File(...)):
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
        first_date = transactions_data[0].get("date", "")
        try:
            d = datetime.strptime(first_date, "%Y-%m-%d")
            iy, im = d.year, d.month
        except Exception:
            iy, im = datetime.now().year, datetime.now().month
        batch = ImportBatch(filename=file.filename, month=im, year=iy,
                            transaction_count=len(transactions_data), imported_at=datetime.now())
        db.add(batch)
        db.flush()
        rules = db.query(ClassificationRule).all()
        for td in transactions_data:
            amount = float(td.get("amount", 0))
            is_inc = amount > 0
            cat_id = None
            desc = (td.get("description") or "").upper()
            cp = (td.get("counterparty") or "").upper()
            for rule in rules:
                kw = (rule.description_contains or "").upper()
                if kw and (kw in desc or kw in cp):
                    cat_id = rule.category_id
                    break
            t = Transaction(
                date=td.get("date"), year=iy, month=im,
                description=td.get("description", ""),
                counterparty_name=td.get("counterparty", ""),
                amount=amount, is_income=is_inc,
                category_id=cat_id, excluded=False,
                import_batch_id=batch.id,
                variable_symbol=td.get("variable_symbol", ""),
                specific_symbol=td.get("specific_symbol", ""),
                constant_symbol=td.get("constant_symbol", ""),
            )
            db.add(t)
        db.commit()
        imports = db.query(ImportBatch).order_by(ImportBatch.imported_at.desc()).all()
        imp_list = [{"id": i.id, "filename": i.filename, "month": i.month, "year": i.year,
                     "count": i.transaction_count,
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


@app.get("/transactions", response_class=HTMLResponse)
async def transactions_page(
    request: Request,
    t_type: Optional[str] = None,
    search: Optional[str] = None,
    month: Optional[str] = None,
    page: int = 1,
):
    db = SessionLocal()
    try:
        q = db.query(Transaction)
        if t_type == "unclassified":
            q = q.filter(Transaction.category_id == None, Transaction.excluded == False)
        elif t_type == "income":
            q = q.filter(Transaction.is_income == True, Transaction.excluded == False)
        elif t_type == "expense":
            q = q.filter(Transaction.is_income == False, Transaction.excluded == False)
        elif t_type == "excluded":
            q = q.filter(Transaction.excluded == True)
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
        q = q.order_by(Transaction.date.desc())
        total_count = q.count()
        per_page = 50
        total_pages = max(1, (total_count + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        txns = q.offset((page - 1) * per_page).limit(per_page).all()
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
                "amount": t.amount,
                "is_income": t.is_income,
                "excluded": t.excluded,
                "category": cat_name(db, t.category_id),
            })
        return render("transactions.html",
                      transactions=t_list, categories=cats,
                      current_filter=t_type, search=search or "",
                      months=months_list, selected_month=month or "",
                      page=page, total_pages=total_pages, total_count=total_count, msg=None)
    finally:
        db.close()


@app.post("/transactions/{t_id}/categorize")
async def categorize_transaction(t_id: int, category: str = Form("")):
    db = SessionLocal()
    try:
        t = db.query(Transaction).filter(Transaction.id == t_id).first()
        if not t:
            raise HTTPException(status_code=404)
        if category == "EXCLUDED":
            t.excluded = True
            t.category_id = None
        elif category == "":
            t.category_id = None
            t.excluded = False
        else:
            c = db.query(Category).filter(Category.name == category).first()
            if c:
                t.category_id = c.id
                t.excluded = False
        db.commit()
        return RedirectResponse(url="/transactions", status_code=303)
    finally:
        db.close()


@app.get("/categories", response_class=HTMLResponse)
async def categories_page(request: Request):
    db = SessionLocal()
    try:
        cats = db.query(Category).filter(Category.is_active == True).order_by(Category.name).all()
        cat_list = [{"id": c.id, "name": c.name, "cat_type": "expense", "keywords": ""} for c in cats]
        return render("categories.html", categories=cat_list, msg=None)
    finally:
        db.close()


@app.post("/categories/add")
async def add_category(name: str = Form(...), cat_type: str = Form("expense"), keywords: str = Form("")):
    db = SessionLocal()
    try:
        name = name.strip().upper()
        if name and not db.query(Category).filter(Category.name == name).first():
            db.add(Category(name=name, is_active=True))
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
        q = db.query(Transaction).filter(Transaction.year == year, Transaction.excluded == False)
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
                Transaction.excluded == False).all()
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
