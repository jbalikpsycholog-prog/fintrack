import os
import io
import shutil
from datetime import datetime, date
from typing import Optional, List
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from database import engine, SessionLocal, Base, Category, Transaction, ClassificationRule, Budget, ImportBatch
from parser_cs import parse_cs_csv

# Inicializace databáze
Base.metadata.create_all(bind=engine)

app = FastAPI(title="FinTrack OSVČ")
templates = Jinja2Templates(directory="templates")

# Složka pro přílohy
RECEIPTS_DIR = Path("receipts")
RECEIPTS_DIR.mkdir(exist_ok=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_default_categories(db: Session):
    """Inicializuje výchozí kategorie pokud ještě neexistují."""
    default_cats = [
        ("SOFTWARE", True),
        ("HARDWARE", True),
        ("PRONAJEM", True),
        ("TEL/INTERNET", True),
        ("TESTY", True),
        ("DROB.ADMIN", True),
        ("DROB.OST.", True),
        ("FIN.SLUZBY", True),
        ("VOZIDLO", True),
        ("PEREX", True),
        ("PRIJMY", True),
    ]
    for name, active in default_cats:
        existing = db.query(Category).filter(Category.name == name).first()
        if not existing:
            db.add(Category(name=name, is_active=active))
    db.commit()


# ==================== DASHBOARD ====================

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    db = SessionLocal()
    try:
        init_default_categories(db)
        
        now = datetime.now()
        current_year = now.year
        current_month = now.month
        
        # Celkový přehled za aktuální rok
        all_transactions = db.query(Transaction).filter(
            Transaction.year == current_year
        ).all()
        
        total_income = sum(t.amount for t in all_transactions if t.is_income and not t.excluded)
        total_expense = sum(abs(t.amount) for t in all_transactions if not t.is_income and not t.excluded)
        saldo = total_income - total_expense
        
        # Počet nezařazených transakcí
        unclassified_count = db.query(Transaction).filter(
            Transaction.category_id == None,
            Transaction.excluded == False
        ).count()
        
        # Výdaje dle kategorie (aktuální rok)
        categories = db.query(Category).filter(Category.is_active == True).all()
        category_expenses = []
        for cat in categories:
            if cat.name == "PRIJMY":
                continue
            total = db.query(Transaction).filter(
                Transaction.category_id == cat.id,
                Transaction.year == current_year,
                Transaction.excluded == False
            ).all()
            cat_sum = sum(abs(t.amount) for t in total if not t.is_income)
            if cat_sum > 0:
                category_expenses.append({"name": cat.name, "total": cat_sum})
        
        # Poslední importy
        recent_imports = db.query(ImportBatch).order_by(ImportBatch.imported_at.desc()).limit(5).all()
        
        ctx = {
            "request": request,
            "total_income": total_income,
            "total_expense": total_expense,
            "saldo": saldo,
            "unclassified_count": unclassified_count,
            "category_expenses": category_expenses,
            "recent_imports": recent_imports,
            "current_year": current_year,
            "current_month": current_month,
        }
        return templates.TemplateResponse("dashboard.html", ctx)
    finally:
        db.close()


# ==================== IMPORT ====================

@app.get("/import", response_class=HTMLResponse)
async def import_page(request: Request):
    db = SessionLocal()
    try:
        imports = db.query(ImportBatch).order_by(ImportBatch.imported_at.desc()).all()
        return templates.TemplateResponse("import.html", {
            "request": request,
            "imports": imports,
            "message": None,
            "error": None,
        })
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
            imports = db.query(ImportBatch).order_by(ImportBatch.imported_at.desc()).all()
            return templates.TemplateResponse("import.html", {
                "request": request,
                "imports": imports,
                "message": None,
                "error": f"Chyba při parsování CSV: {str(e)}",
            })
        
        if not transactions_data:
            imports = db.query(ImportBatch).order_by(ImportBatch.imported_at.desc()).all()
            return templates.TemplateResponse("import.html", {
                "request": request,
                "imports": imports,
                "message": None,
                "error": "CSV soubor neobsahuje žádné transakce.",
            })
        
        # Zjisti rok/měsíc z prvního záznamu
        first_date = transactions_data[0].get("date", "")
        try:
            d = datetime.strptime(first_date, "%Y-%m-%d")
            import_year = d.year
            import_month = d.month
        except Exception:
            import_year = datetime.now().year
            import_month = datetime.now().month
        
        # Vytvoř ImportBatch
        batch = ImportBatch(
            filename=file.filename,
            year=import_year,
            month=import_month,
            imported_at=datetime.now(),
        )
        db.add(batch)
        db.flush()
        
        # Načti pravidla klasifikace
        rules = db.query(ClassificationRule).all()
        rule_map = {}
        for r in rules:
            key = (r.counterparty_name or "").lower().strip()
            if key:
                rule_map[key] = r.category_id
        
        new_count = 0
        duplicate_count = 0
        
        for td in transactions_data:
            # Detekce duplicit
            existing = db.query(Transaction).filter(
                Transaction.cs_transaction_id == td.get("cs_transaction_id", "")
            ).first()
            
            if not existing and td.get("cs_transaction_id"):
                pass
            elif existing:
                duplicate_count += 1
                continue
            
            # Zjisti rok/měsíc transakce
            try:
                td_date = datetime.strptime(td.get("date", ""), "%Y-%m-%d")
                t_year = td_date.year
                t_month = td_date.month
            except Exception:
                t_year = import_year
                t_month = import_month
            
            # Auto-klasifikace dle pravidel
            cat_id = None
            cp_name = (td.get("counterparty_name") or "").lower().strip()
            if cp_name and cp_name in rule_map:
                cat_id = rule_map[cp_name]
            
            t = Transaction(
                import_batch_id=batch.id,
                cs_transaction_id=td.get("cs_transaction_id"),
                date=td.get("date"),
                year=t_year,
                month=t_month,
                amount=td.get("amount", 0),
                currency=td.get("currency", "CZK"),
                counterparty_account=td.get("counterparty_account"),
                counterparty_name=td.get("counterparty_name"),
                bank_code=td.get("bank_code"),
                variable_symbol=td.get("variable_symbol"),
                constant_symbol=td.get("constant_symbol"),
                specific_symbol=td.get("specific_symbol"),
                description=td.get("description"),
                transaction_type=td.get("transaction_type"),
                is_income=td.get("is_income", False),
                category_id=cat_id,
                excluded=False,
            )
            db.add(t)
            new_count += 1
        
        batch.transaction_count = new_count
        db.commit()
        
        imports = db.query(ImportBatch).order_by(ImportBatch.imported_at.desc()).all()
        return templates.TemplateResponse("import.html", {
            "request": request,
            "imports": imports,
            "message": f"Import dokončen: {new_count} nových transakcí, {duplicate_count} duplikátů přeskočeno.",
            "error": None,
        })
    except Exception as e:
        db.rollback()
        imports = db.query(ImportBatch).order_by(ImportBatch.imported_at.desc()).all()
        return templates.TemplateResponse("import.html", {
            "request": request,
            "imports": imports,
            "message": None,
            "error": f"Chyba importu: {str(e)}",
        })
    finally:
        db.close()


# ==================== TRANSAKCE ====================

@app.get("/transactions", response_class=HTMLResponse)
async def transactions(
    request: Request,
    year: Optional[int] = None,
    month: Optional[int] = None,
    category_id: Optional[int] = None,
    show_excluded: bool = False,
    show_unclassified: bool = False,
):
    db = SessionLocal()
    try:
        now = datetime.now()
        if year is None:
            year = now.year
        if month is None:
            month = now.month
        
        query = db.query(Transaction).filter(
            Transaction.year == year,
            Transaction.month == month,
        )
        
        if show_unclassified:
            query = query.filter(Transaction.category_id == None, Transaction.excluded == False)
        elif not show_excluded:
            query = query.filter(Transaction.excluded == False)
        
        if category_id:
            query = query.filter(Transaction.category_id == category_id)
        
        transactions_list = query.order_by(Transaction.date.desc()).all()
        categories = db.query(Category).filter(Category.is_active == True).all()
        
        return templates.TemplateResponse("transactions.html", {
            "request": request,
            "transactions": transactions_list,
            "categories": categories,
            "year": year,
            "month": month,
            "category_id": category_id,
            "show_excluded": show_excluded,
            "show_unclassified": show_unclassified,
            "years": list(range(2020, now.year + 2)),
            "months": list(range(1, 13)),
        })
    finally:
        db.close()


@app.post("/transactions/{t_id}/categorize")
async def categorize_transaction(
    t_id: int,
    category_id: Optional[int] = Form(None),
    excluded: bool = Form(False),
    save_rule: bool = Form(False),
    year: int = Form(datetime.now().year),
    month: int = Form(datetime.now().month),
):
    db = SessionLocal()
    try:
        t = db.query(Transaction).filter(Transaction.id == t_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Transakce nenalezena")
        
        t.category_id = category_id if category_id and category_id > 0 else None
        t.excluded = excluded
        
        # Uložit pravidlo
        if save_rule and category_id and t.counterparty_name:
            existing_rule = db.query(ClassificationRule).filter(
                ClassificationRule.counterparty_name == t.counterparty_name
            ).first()
            if existing_rule:
                existing_rule.category_id = category_id
            else:
                rule = ClassificationRule(
                    counterparty_name=t.counterparty_name,
                    category_id=category_id,
                )
                db.add(rule)
        
        db.commit()
        return RedirectResponse(
            url=f"/transactions?year={year}&month={month}",
            status_code=303
        )
    finally:
        db.close()


# ==================== PŘÍLOHY ====================

@app.post("/transactions/{t_id}/receipt")
async def upload_receipt(t_id: int, receipt: UploadFile = File(...)):
    db = SessionLocal()
    try:
        t = db.query(Transaction).filter(Transaction.id == t_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Transakce nenalezena")
        
        receipt_dir = RECEIPTS_DIR / str(t_id)
        receipt_dir.mkdir(exist_ok=True)
        
        safe_filename = os.path.basename(receipt.filename)
        file_path = receipt_dir / safe_filename
        
        with open(file_path, "wb") as f:
            content = await receipt.read()
            f.write(content)
        
        t.receipt_path = str(file_path)
        db.commit()
        return RedirectResponse(url="/transactions", status_code=303)
    finally:
        db.close()


@app.get("/receipts/{t_id}/{filename}")
async def get_receipt(t_id: int, filename: str):
    file_path = RECEIPTS_DIR / str(t_id) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Soubor nenalezen")
    return FileResponse(str(file_path))


# ==================== KATEGORIE ====================

@app.get("/categories", response_class=HTMLResponse)
async def categories_page(request: Request):
    db = SessionLocal()
    try:
        categories = db.query(Category).order_by(Category.name).all()
        rules = db.query(ClassificationRule).all()
        
        # Načti jména kategorií pro pravidla
        cat_map = {c.id: c.name for c in categories}
        rules_with_cat = []
        for r in rules:
            rules_with_cat.append({
                "id": r.id,
                "counterparty_name": r.counterparty_name,
                "description_contains": r.description_contains,
                "category_name": cat_map.get(r.category_id, "?"),
                "category_id": r.category_id,
            })
        
        return templates.TemplateResponse("categories.html", {
            "request": request,
            "categories": categories,
            "rules": rules_with_cat,
        })
    finally:
        db.close()


@app.post("/categories/add")
async def add_category(name: str = Form(...)):
    db = SessionLocal()
    try:
        name = name.strip().upper()
        existing = db.query(Category).filter(Category.name == name).first()
        if not existing:
            db.add(Category(name=name, is_active=True))
            db.commit()
        return RedirectResponse(url="/categories", status_code=303)
    finally:
        db.close()


@app.post("/categories/{cat_id}/toggle")
async def toggle_category(cat_id: int):
    db = SessionLocal()
    try:
        cat = db.query(Category).filter(Category.id == cat_id).first()
        if cat:
            cat.is_active = not cat.is_active
            db.commit()
        return RedirectResponse(url="/categories", status_code=303)
    finally:
        db.close()


@app.post("/rules/{rule_id}/delete")
async def delete_rule(rule_id: int):
    db = SessionLocal()
    try:
        rule = db.query(ClassificationRule).filter(ClassificationRule.id == rule_id).first()
        if rule:
            db.delete(rule)
            db.commit()
        return RedirectResponse(url="/categories", status_code=303)
    finally:
        db.close()


# ==================== SESTAVY ====================

@app.get("/reports", response_class=HTMLResponse)
async def reports(
    request: Request,
    period: str = "monthly",
    year: Optional[int] = None,
    month: Optional[int] = None,
):
    db = SessionLocal()
    try:
        now = datetime.now()
        if year is None:
            year = now.year
        if month is None:
            month = now.month
        
        categories = db.query(Category).filter(Category.is_active == True).all()
        
        # Určit rozsah
        if period == "monthly":
            months_range = [month]
        elif period == "quarterly":
            q = (month - 1) // 3
            months_range = [q*3+1, q*3+2, q*3+3]
        elif period == "halfyear":
            if month <= 6:
                months_range = list(range(1, 7))
            else:
                months_range = list(range(7, 13))
        else:  # annual
            months_range = list(range(1, 13))
        
        # Data dle kategorií
        report_data = []
        total_income = 0
        total_expense = 0
        
        for cat in categories:
            txns = db.query(Transaction).filter(
                Transaction.category_id == cat.id,
                Transaction.year == year,
                Transaction.month.in_(months_range),
                Transaction.excluded == False,
            ).all()
            
            income = sum(t.amount for t in txns if t.is_income)
            expense = sum(abs(t.amount) for t in txns if not t.is_income)
            
            if income > 0 or expense > 0:
                # Rozpočet
                budget = db.query(Budget).filter(
                    Budget.category_id == cat.id,
                    Budget.year == year,
                ).first()
                budget_amount = budget.amount if budget else 0
                
                report_data.append({
                    "category": cat.name,
                    "income": income,
                    "expense": expense,
                    "budget": budget_amount,
                    "diff": budget_amount - expense if budget_amount > 0 else None,
                })
                total_income += income
                total_expense += expense
        
        # Nezařazené
        uncat_txns = db.query(Transaction).filter(
            Transaction.category_id == None,
            Transaction.year == year,
            Transaction.month.in_(months_range),
            Transaction.excluded == False,
        ).all()
        uncat_expense = sum(abs(t.amount) for t in uncat_txns if not t.is_income)
        uncat_income = sum(t.amount for t in uncat_txns if t.is_income)
        if uncat_expense > 0 or uncat_income > 0:
            report_data.append({
                "category": "NEZAŘAZENO",
                "income": uncat_income,
                "expense": uncat_expense,
                "budget": 0,
                "diff": None,
            })
            total_income += uncat_income
            total_expense += uncat_expense
        
        return templates.TemplateResponse("reports.html", {
            "request": request,
            "report_data": report_data,
            "total_income": total_income,
            "total_expense": total_expense,
            "saldo": total_income - total_expense,
            "period": period,
            "year": year,
            "month": month,
            "years": list(range(2020, now.year + 2)),
            "months": list(range(1, 13)),
        })
    finally:
        db.close()


# ==================== ROZPOČTY ====================

@app.get("/budgets", response_class=HTMLResponse)
async def budgets_page(request: Request, year: Optional[int] = None):
    db = SessionLocal()
    try:
        if year is None:
            year = datetime.now().year
        
        categories = db.query(Category).filter(Category.is_active == True).all()
        budgets = db.query(Budget).filter(Budget.year == year).all()
        budget_map = {b.category_id: b for b in budgets}
        
        budget_list = []
        for cat in categories:
            b = budget_map.get(cat.id)
            budget_list.append({
                "category_id": cat.id,
                "category_name": cat.name,
                "amount": b.amount if b else 0,
                "budget_id": b.id if b else None,
            })
        
        return templates.TemplateResponse("budgets.html", {
            "request": request,
            "budget_list": budget_list,
            "year": year,
            "years": list(range(2020, datetime.now().year + 2)),
        })
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
                try:
                    amount = float(value) if value else 0
                except ValueError:
                    amount = 0
                
                existing = db.query(Budget).filter(
                    Budget.category_id == cat_id,
                    Budget.year == year,
                ).first()
                
                if existing:
                    existing.amount = amount
                else:
                    db.add(Budget(category_id=cat_id, year=year, amount=amount))
        
        db.commit()
        return RedirectResponse(url=f"/budgets?year={year}", status_code=303)
    finally:
        db.close()
