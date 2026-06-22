import os
from datetime import datetime
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from jinja2 import Environment, FileSystemLoader, select_autoescape

from database import engine, SessionLocal, Base, Category, Transaction, ClassificationRule, Budget, ImportBatch
from parser_cs import parse_cs_csv

Base.metadata.create_all(bind=engine)

app = FastAPI(title="FinTrack OSVČ")

# Jinja2 bez cache - obchází bug s dict/tuple v Python 3.12
jinja_env = Environment(
        loader=FileSystemLoader("templates"),
        autoescape=select_autoescape(["html"]),
        auto_reload=True,
        cache_size=0,
)

def render(template_name: str, **ctx) -> HTMLResponse:
        t = jinja_env.get_template(template_name)
        return HTMLResponse(t.render(**ctx))

RECEIPTS_DIR = Path("receipts")
RECEIPTS_DIR.mkdir(exist_ok=True)

DEFAULT_CATEGORIES = [
        ("SOFTWARE", "expense"),
        ("HARDWARE", "expense"),
        ("PRONAJEM", "expense"),
        ("TEL/INTERNET", "expense"),
        ("TESTY", "expense"),
        ("DROB.ADMIN", "expense"),
        ("DROB.OST.", "expense"),
        ("FIN.SLUZBY", "expense"),
        ("VOZIDLO", "expense"),
        ("PEREX", "expense"),
        ("PRIJMY", "income"),
]

def init_default_categories(db: Session):
        for name, cat_type in DEFAULT_CATEGORIES:
                    if not db.query(Category).filter(Category.name == name).first():
                                    db.add(Category(name=name, cat_type=cat_type, is_active=True))
                            db.commit()


# ─── DASHBOARD ──────────────────────────────────────────────────────────────

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
                        if c.cat_type == "income":
                                            continue
                                        s = sum(abs(t.amount) for t in db.query(Transaction).filter(
                                                            Transaction.category_id == c.id, Transaction.year == cy,
                                                            Transaction.excluded == False).all() if not t.is_income)
            if s > 0:
                                cat_expenses.append({"name": c.name, "total": s})

        recent = db.query(ImportBatch).order_by(ImportBatch.imported_at.desc()).limit(5).all()
        recent_list = [{"filename": r.filename, "month": r.month, "year": r.year,
                                                "count": r.transaction_count,
                                                "imported_at": r.imported_at.strftime("%d.%m.%Y %H:%M") if r.imported_at else ""
                       } for r in recent]

        return render("dashboard.html",
                                            total_income=total_income, total_expense=total_expense, saldo=saldo,
                                            unclassified_count=unclassified_count, category_expenses=cat_expenses,
                                            recent_imports=recent_list, current_year=cy, current_month=now.month)
finally:
        db.close()


# ─── IMPORT ─────────────────────────────────────────────────────────────────

@app.get("/import", response_class=HTMLResponse)
async def import_page(request: Request):
        db = SessionLocal()
    try:
                imports = db.query(ImportBatch).order_by(ImportBatch.imported_at.desc()).all()
        imp_list = [{"id": i.id, "filename": i.filename, "month": i.month, "year": i.year,
                                          "count": i.transaction_count,
                                          "imported_at": i.imported_at.strftime("%d.%m.%Y %H:%M") if i.imported_at else ""
                    } for i in imports]
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
            return render("import.html", imports=[], message=None, error=f"Chyba při parsování CSV: {e}")

        if not transactions_data:
                        return render("import.html", imports=[], message=None, error="CSV neobsahuje žádné transakce.")

        first_date = transactions_data[0].get("date", "")
        try:
                        d = datetime.strptime(first_date, "%Y-%m-%d")
            iy, im = d.year, d.month
except Exception:
            iy, im = datetime.now().year, datetime.now().month

        batch = ImportBatch(
                        filename=file.filename,
                        month=im, year=iy,
                        transaction_count=len(transactions_data),
                        imported_at=datetime.now()
        )
        db.add(batch)
        db.flush()

        cats = {c.name: c for c in db.query(Category).all()}
        rules = db.query(ClassificationRule).all()

        for td in transactions_data:
                        amount = float(td.get("amount", 0))
            is_inc = amount > 0

            cat_id = None
            desc = (td.get("description") or "").upper()
            counterparty = (td.get("counterparty") or "").upper()
            for rule in rules:
                                for kw in (rule.keyword or "").split(","):
                                                        kw = kw.strip().upper()
                                                        if kw and (kw in desc or kw in counterparty):
                                                                                    cat_id = rule.category_id
                                                                                    break
                                                                            if cat_id:
                                                                                                    break

                                                t = Transaction(
                                                                    date=td.get("date"),
                                                                    year=iy, month=im,
                                                                    description=td.get("description", ""),
                                                                    counterparty=td.get("counterparty", ""),
                                                                    amount=amount,
                                                                    is_income=is_inc,
                                                                    category_id=cat_id,
                                                                    excluded=False,
                                                                    import_batch_id=batch.id,
                                                                    variable_symbol=td.get("variable_symbol", ""),
                                                                    specific_symbol=td.get("specific_symbol", ""),
                                                                    constant_symbol=td.get("constant_symbol", ""),
                                                                    note=td.get("note", ""),
                                                )
            db.add(t)

        db.commit()

        imports = db.query(ImportBatch).order_by(ImportBatch.imported_at.desc()).all()
        imp_list = [{"id": i.id, "filename": i.filename, "month": i.month, "year": i.year,
                                          "count": i.transaction_count,
                                          "imported_at": i.imported_at.strftime("%d.%m.%Y %H:%M") if i.imported_at else ""
                    } for i in imports]
        return render("import.html", imports=imp_list,
                                            message=f"Importováno {len(transactions_data)} transakcí z {file.filename}.",
                                            error=None)
except Exception as e:
        db.rollback()
        return render("import.html", imports=[], message=None, error=f"Chyba při importu: {e}")
finally:
        db.close()


# ─── TRANSACTIONS ────────────────────────────────────────────────────────────

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
                                            (Transaction.counterparty.ilike(f"%{search}%"))
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

        # Distinct months for filter
        all_months_q = db.query(Transaction.year, Transaction.month).distinct().order_by(
                        Transaction.year.desc(), Transaction.month.desc()).all()
        months_list = [f"{r[0]}-{r[1]:02d}" for r in all_months_q]

        t_list = []
        for t in txns:
                        cat_name = None
            if t.category_id:
                                cat_obj = next((c for c in cats if c.id == t.category_id), None)
                cat_name = cat_obj.name if cat_obj else None
            t_list.append({
                                "id": t.id,
                                "date": t.date,
                                "description": t.description,
                                "counterparty": t.counterparty,
                                "amount": t.amount,
                                "is_income": t.is_income,
                                "excluded": t.excluded,
                                "category": cat_name,
            })

        return render("transactions.html",
                                            transactions=t_list,
                                            categories=cats,
                                            current_filter=t_type,
                                            search=search or "",
                                            months=months_list,
                                            selected_month=month or "",
                                            page=page,
                                            total_pages=total_pages,
                                            total_count=total_count,
                                            msg=None)
finally:
        db.close()

@app.post("/transactions/{t_id}/categorize", response_class=HTMLResponse)
async def categorize_transaction(request: Request, t_id: int, category: str = Form("")):
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
            cat = db.query(Category).filter(Category.name == category).first()
            if cat:
                                t.category_id = cat.id
                t.excluded = False
        db.commit()
        return RedirectResponse(url="/transactions", status_code=303)
finally:
        db.close()


# ─── CATEGORIES ──────────────────────────────────────────────────────────────

@app.get("/categories", response_class=HTMLResponse)
async def categories_page(request: Request):
        db = SessionLocal()
    try:
                cats = db.query(Category).filter(Category.is_active == True).order_by(Category.name).all()
        cat_list = [{"id": c.id, "name": c.name, "cat_type": c.cat_type,
                                          "keywords": c.keywords} for c in cats]
        return render("categories.html", categories=cat_list, msg=None)
finally:
        db.close()

@app.post("/categories/add", response_class=HTMLResponse)
async def add_category(request: Request,
                                              name: str = Form(...),
                                              cat_type: str = Form("expense"),
                                              keywords: str = Form("")):
                                                      db = SessionLocal()
                                                      try:
                                                                  name = name.strip().upper()
                                                                  if name and not db.query(Category).filter(Category.name == name).first():
                                                                                  db.add(Category(name=name, cat_type=cat_type, is_active=True, keywords=keywords))
                                                                                  db.commit()
                                                                              return RedirectResponse(url="/categories", status_code=303)
finally:
        db.close()

@app.post("/categories/delete/{cat_id}")
async def delete_category(cat_id: int):
        db = SessionLocal()
    try:
                cat = db.query(Category).filter(Category.id == cat_id).first()
        if cat:
                        cat.is_active = False
            db.commit()
        return RedirectResponse(url="/categories", status_code=303)
finally:
        db.close()


# ─── BUDGETS ─────────────────────────────────────────────────────────────────

@app.get("/budgets", response_class=HTMLResponse)
async def budgets_page(request: Request):
        db = SessionLocal()
    try:
                cats = db.query(Category).filter(Category.is_active == True,
                                                                                           Category.cat_type == "expense").order_by(Category.name).all()
        budgets = db.query(Budget).all()
        bud_list = [{"id": b.id, "category": b.category_name, "monthly_limit": b.monthly_limit}
                                        for b in budgets]
        cat_list = [{"id": c.id, "name": c.name} for c in cats]
        return render("budgets.html", categories=cat_list, budgets=bud_list, msg=None)
finally:
        db.close()

@app.post("/budgets/set", response_class=HTMLResponse)
async def set_budget(request: Request,
                                          category: str = Form(...),
                                          amount: float = Form(...)):
                                                  db = SessionLocal()
                                                  try:
                                                              existing = db.query(Budget).filter(Budget.category_name == category).first()
                                                              if existing:
                                                                              existing.monthly_limit = amount
                                                  else:
                                                                  db.add(Budget(category_name=category, monthly_limit=amount))
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


# ─── REPORTS ─────────────────────────────────────────────────────────────────

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

        # Build query for selected period
        q = db.query(Transaction).filter(Transaction.year == year, Transaction.excluded == False)
        if period == "month":
                        q = q.filter(Transaction.month == month)
elif period == "quarter":
            qtr_start = ((month - 1) // 3) * 3 + 1
            q = q.filter(Transaction.month >= qtr_start, Transaction.month <= qtr_start + 2)
elif period == "half":
            half_start = 1 if month <= 6 else 7
            q = q.filter(Transaction.month >= half_start, Transaction.month <= half_start + 5)
        # "year" = no month filter

        txns = q.all()
        total_income = sum(t.amount for t in txns if t.is_income)
        total_expenses = sum(abs(t.amount) for t in txns if not t.is_income)
        saldo = total_income - total_expenses

        # By category
        cats = db.query(Category).filter(Category.is_active == True,
                                                                                   Category.cat_type == "expense").order_by(Category.name).all()
        budgets_map = {b.category_name: b.monthly_limit for b in db.query(Budget).all()}

        by_category = []
        for c in cats:
                        spent = sum(abs(t.amount) for t in txns if t.category_id == c.id and not t.is_income)
            if spent == 0:
                                continue
            bud = budgets_map.get(c.name)
            remaining = (bud - spent) if bud else None
            by_category.append({
                                "category": c.name,
                                "spent": spent,
                                "budget": bud,
                                "remaining": remaining,
            })

        # Monthly chart data for full year
        monthly_labels = []
        monthly_incomes = []
        monthly_expenses_data = []
        month_names = ["Led","Úno","Bře","Dub","Kvě","Čer","Čvc","Srp","Zář","Říj","Lis","Pro"]
        for m in range(1, 13):
                        mt = db.query(Transaction).filter(
                                            Transaction.year == year, Transaction.month == m,
                                            Transaction.excluded == False).all()
            monthly_labels.append(month_names[m - 1])
            monthly_incomes.append(round(sum(t.amount for t in mt if t.is_income), 2))
            monthly_expenses_data.append(round(sum(abs(t.amount) for t in mt if not t.is_income), 2))

        available_years = list(range(2020, now.year + 2))

        return render("reports.html",
                                            total_income=total_income,
                                            total_expenses=total_expenses,
                                            saldo=saldo,
                                            by_category=by_category,
                                            monthly_data=True,
                                            monthly_labels=monthly_labels,
                                            monthly_incomes=monthly_incomes,
                                            monthly_expenses=monthly_expenses_data,
                                            years=available_years,
                                            selected_year=year,
                                            period=period,
                                            selected_month=month)
finally:
        db.close()
