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

# Jinja2 bez cache - obchází bug s dict/tuple
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


def init_default_categories(db: Session):
    default_cats = [
        "SOFTWARE", "HARDWARE", "PRONAJEM", "TEL/INTERNET", "TESTY",
        "DROB.ADMIN", "DROB.OST.", "FIN.SLUZBY", "VOZIDLO", "PEREX", "PRIJMY"
    ]
    for name in default_cats:
        if not db.query(Category).filter(Category.name == name).first():
            db.add(Category(name=name, is_active=True))
    db.commit()


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
            if c.name == "PRIJMY":
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

        batch = ImportBatch(filename=file.filename, year=iy, month=im,
                            imported_at=datetime.now())
        db.add(batch)
        db.flush()

        rules = {(r.counterparty_name or "").lower().strip(): r.category_id
                 for r in db.query(ClassificationRule).all() if r.counterparty_name}

        new_count = dup_count = 0
        for td in transactions_data:
            if td.get("cs_transaction_id") and db.query(Transaction).filter(
                    Transaction.cs_transaction_id == td["cs_transaction_id"]).first():
                dup_count += 1
                continue
            try:
                d2 = datetime.strptime(td.get("date", ""), "%Y-%m-%d")
                ty, tm = d2.year, d2.month
            except Exception:
                ty, tm = iy, im

            cp = (td.get("counterparty_name") or "").lower().strip()
            cat_id = rules.get(cp)

            t = Transaction(
                import_batch_id=batch.id,
                cs_transaction_id=td.get("cs_transaction_id"),
                date=td.get("date"), year=ty, month=tm,
                amount=td.get("amount", 0), currency=td.get("currency", "CZK"),
                counterparty_account=td.get("counterparty_account"),
                counterparty_name=td.get("counterparty_name"),
                bank_code=td.get("bank_code"),
                variable_symbol=td.get("variable_symbol"),
                constant_symbol=td.get("constant_symbol"),
                specific_symbol=td.get("specific_symbol"),
                description=td.get("description"),
                transaction_type=td.get("transaction_type"),
                is_income=td.get("is_income", False),
                category_id=cat_id, excluded=False,
            )
            db.add(t)
            new_count += 1

        batch.transaction_count = new_count
        db.commit()
        imports = db.query(ImportBatch).order_by(ImportBatch.imported_at.desc()).all()
        imp_list = [{"id": i.id, "filename": i.filename, "month": i.month, "year": i.year,
                     "count": i.transaction_count,
                     "imported_at": i.imported_at.strftime("%d.%m.%Y %H:%M") if i.imported_at else ""
                     } for i in imports]
        return render("import.html", imports=imp_list,
                      message=f"Import dokončen: {new_count} nových, {dup_count} duplikátů.", error=None)
    except Exception as e:
        db.rollback()
        return render("import.html", imports=[], message=None, error=f"Chyba: {e}")
    finally:
        db.close()


@app.get("/transactions", response_class=HTMLResponse)
async def transactions(request: Request, year: Optional[int] = None,
                       month: Optional[int] = None, category_id: Optional[int] = None,
                       show_excluded: bool = False, show_unclassified: bool = False):
    db = SessionLocal()
    try:
        now = datetime.now()
        year = year or now.year
        month = month or now.month
        q = db.query(Transaction).filter(Transaction.year == year, Transaction.month == month)
        if show_unclassified:
            q = q.filter(Transaction.category_id == None, Transaction.excluded == False)
        elif not show_excluded:
            q = q.filter(Transaction.excluded == False)
        if category_id:
            q = q.filter(Transaction.category_id == category_id)
        txns = q.order_by(Transaction.date.desc()).all()
        cats = db.query(Category).filter(Category.is_active == True).all()

        txn_list = [{"id": t.id, "date": t.date, "amount": t.amount,
                     "counterparty_name": t.counterparty_name or "",
                     "description": t.description or "",
                     "is_income": t.is_income, "excluded": t.excluded,
                     "category_id": t.category_id,
                     "category_name": next((c.name for c in cats if c.id == t.category_id), ""),
                     "receipt_path": t.receipt_path or ""} for t in txns]
        cat_list = [{"id": c.id, "name": c.name} for c in cats]
        return render("transactions.html", transactions=txn_list, categories=cat_list,
                      year=year, month=month, category_id=category_id,
                      show_excluded=show_excluded, show_unclassified=show_unclassified,
                      years=list(range(2020, now.year + 2)), months=list(range(1, 13)))
    finally:
        db.close()


@app.post("/transactions/{t_id}/categorize")
async def categorize_transaction(t_id: int, category_id: Optional[int] = Form(None),
                                  excluded: bool = Form(False), save_rule: bool = Form(False),
                                  year: int = Form(datetime.now().year),
                                  month: int = Form(datetime.now().month)):
    db = SessionLocal()
    try:
        t = db.query(Transaction).filter(Transaction.id == t_id).first()
        if not t:
            raise HTTPException(status_code=404)
        t.category_id = category_id if category_id and category_id > 0 else None
        t.excluded = excluded
        if save_rule and category_id and t.counterparty_name:
            er = db.query(ClassificationRule).filter(
                ClassificationRule.counterparty_name == t.counterparty_name).first()
            if er:
                er.category_id = category_id
            else:
                db.add(ClassificationRule(counterparty_name=t.counterparty_name, category_id=category_id))
        db.commit()
        return RedirectResponse(url=f"/transactions?year={year}&month={month}", status_code=303)
    finally:
        db.close()


@app.post("/transactions/{t_id}/receipt")
async def upload_receipt(t_id: int, receipt: UploadFile = File(...)):
    db = SessionLocal()
    try:
        t = db.query(Transaction).filter(Transaction.id == t_id).first()
        if not t:
            raise HTTPException(status_code=404)
        d = RECEIPTS_DIR / str(t_id)
        d.mkdir(exist_ok=True)
        fp = d / os.path.basename(receipt.filename)
        with open(fp, "wb") as f:
            f.write(await receipt.read())
        t.receipt_path = str(fp)
        db.commit()
        return RedirectResponse(url="/transactions", status_code=303)
    finally:
        db.close()


@app.get("/receipts/{t_id}/{filename}")
async def get_receipt(t_id: int, filename: str):
    fp = RECEIPTS_DIR / str(t_id) / filename
    if not fp.exists():
        raise HTTPException(status_code=404)
    return FileResponse(str(fp))


@app.get("/categories", response_class=HTMLResponse)
async def categories_page(request: Request):
    db = SessionLocal()
    try:
        cats = db.query(Category).order_by(Category.name).all()
        rules = db.query(ClassificationRule).all()
        cat_map = {c.id: c.name for c in cats}
        cat_list = [{"id": c.id, "name": c.name, "is_active": c.is_active} for c in cats]
        rule_list = [{"id": r.id, "counterparty_name": r.counterparty_name or "",
                      "description_contains": r.description_contains or "",
                      "category_name": cat_map.get(r.category_id, "?"),
                      "category_id": r.category_id} for r in rules]
        return render("categories.html", categories=cat_list, rules=rule_list)
    finally:
        db.close()


@app.post("/categories/add")
async def add_category(name: str = Form(...)):
    db = SessionLocal()
    try:
        name = name.strip().upper()
        if not db.query(Category).filter(Category.name == name).first():
            db.add(Category(name=name, is_active=True))
            db.commit()
        return RedirectResponse(url="/categories", status_code=303)
    finally:
        db.close()


@app.post("/categories/{cat_id}/toggle")
async def toggle_category(cat_id: int):
    db = SessionLocal()
    try:
        c = db.query(Category).filter(Category.id == cat_id).first()
        if c:
            c.is_active = not c.is_active
            db.commit()
        return RedirectResponse(url="/categories", status_code=303)
    finally:
        db.close()


@app.post("/rules/{rule_id}/delete")
async def delete_rule(rule_id: int):
    db = SessionLocal()
    try:
        r = db.query(ClassificationRule).filter(ClassificationRule.id == rule_id).first()
        if r:
            db.delete(r)
            db.commit()
        return RedirectResponse(url="/categories", status_code=303)
    finally:
        db.close()


@app.get("/reports", response_class=HTMLResponse)
async def reports(request: Request, period: str = "monthly",
                  year: Optional[int] = None, month: Optional[int] = None):
    db = SessionLocal()
    try:
        now = datetime.now()
        year = year or now.year
        month = month or now.month
        if period == "monthly":
            months_range = [month]
        elif period == "quarterly":
            q = (month - 1) // 3
            months_range = [q*3+1, q*3+2, q*3+3]
        elif period == "halfyear":
            months_range = list(range(1, 7)) if month <= 6 else list(range(7, 13))
        else:
            months_range = list(range(1, 13))

        cats = db.query(Category).filter(Category.is_active == True).all()
        report_data = []
        total_income = total_expense = 0

        for c in cats:
            txns = db.query(Transaction).filter(
                Transaction.category_id == c.id, Transaction.year == year,
                Transaction.month.in_(months_range), Transaction.excluded == False).all()
            inc = sum(t.amount for t in txns if t.is_income)
            exp = sum(abs(t.amount) for t in txns if not t.is_income)
            if inc > 0 or exp > 0:
                b = db.query(Budget).filter(Budget.category_id == c.id, Budget.year == year).first()
                ba = b.amount if b else 0
                report_data.append({"category": c.name, "income": inc, "expense": exp,
                                     "budget": ba, "diff": ba - exp if ba > 0 else None})
                total_income += inc
                total_expense += exp

        unc = db.query(Transaction).filter(Transaction.category_id == None,
            Transaction.year == year, Transaction.month.in_(months_range),
            Transaction.excluded == False).all()
        ue = sum(abs(t.amount) for t in unc if not t.is_income)
        ui = sum(t.amount for t in unc if t.is_income)
        if ue > 0 or ui > 0:
            report_data.append({"category": "NEZARAZENO", "income": ui, "expense": ue,
                                 "budget": 0, "diff": None})
            total_income += ui
            total_expense += ue

        return render("reports.html", report_data=report_data, total_income=total_income,
                      total_expense=total_expense, saldo=total_income - total_expense,
                      period=period, year=year, month=month,
                      years=list(range(2020, now.year + 2)), months=list(range(1, 13)))
    finally:
        db.close()


@app.get("/budgets", response_class=HTMLResponse)
async def budgets_page(request: Request, year: Optional[int] = None):
    db = SessionLocal()
    try:
        year = year or datetime.now().year
        cats = db.query(Category).filter(Category.is_active == True).all()
        budgets = {b.category_id: b for b in db.query(Budget).filter(Budget.year == year).all()}
        bl = [{"category_id": c.id, "category_name": c.name,
               "amount": budgets[c.id].amount if c.id in budgets else 0,
               "budget_id": budgets[c.id].id if c.id in budgets else None} for c in cats]
        return render("budgets.html", budget_list=bl, year=year,
                      years=list(range(2020, datetime.now().year + 2)))
    finally:
        db.close()


@app.post("/budgets/save")
async def save_budgets(request: Request):
    db = SessionLocal()
    try:
        form = await request.form()
        year = int(form.get("year", datetime.now().year))
        for key, value in form.items():
            if key.startswith("budget_cat_"):
                cat_id = int(key.replace("budget_cat_", ""))
                amount = float(value) if value else 0
                ex = db.query(Budget).filter(Budget.category_id == cat_id, Budget.year == year).first()
                if ex:
                    ex.amount = amount
                else:
                    db.add(Budget(category_id=cat_id, year=year, amount=amount))
        db.commit()
        return RedirectResponse(url=f"/budgets?year={year}", status_code=303)
    finally:
        db.close()
