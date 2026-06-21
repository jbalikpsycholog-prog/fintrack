from sqlalchemy import create_engine, Column, Integer, String, Float, Date, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime

SQLALCHEMY_DATABASE_URL = "sqlite:///./fintrack.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class ImportBatch(Base):
    __tablename__ = "import_batches"
    id = Column(Integer, primary_key=True)
    filename = Column(String)
    imported_at = Column(DateTime, default=datetime.utcnow)
    period_label = Column(String)
    transactions = relationship("Transaction", back_populates="import_batch")

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    color = Column(String, default="#6366f1")
    active = Column(Boolean, default=True)
    is_income = Column(Boolean, default=False)
    transactions = relationship("Transaction", back_populates="category")
    rules = relationship("ClassificationRule", back_populates="category")
    budgets = relationship("Budget", back_populates="category")

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False)
    description = Column(String)
    note = Column(String)
    counterparty_name = Column(String)
    counterparty_account = Column(String)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="CZK")
    transaction_type = Column(String)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    excluded = Column(Boolean, default=False)
    receipt_filename = Column(String, nullable=True)
    import_batch_id = Column(Integer, ForeignKey("import_batches.id"))
    cs_transaction_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    category = relationship("Category", back_populates="transactions")
    import_batch = relationship("ImportBatch", back_populates="transactions")

class ClassificationRule(Base):
    __tablename__ = "classification_rules"
    id = Column(Integer, primary_key=True)
    counterparty_pattern = Column(String, nullable=True)
    keyword = Column(String, nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"))
    priority = Column(Integer, default=0)
    category = relationship("Category", back_populates="rules")

class Budget(Base):
    __tablename__ = "budgets"
    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("categories.id"))
    period_type = Column(String)
    year = Column(Integer)
    period_value = Column(Integer, default=0)
    amount = Column(Float)
    category = relationship("Category", back_populates="budgets")

def init_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    default_cats = [
        ("SOFTWARE",    "#3b82f6", False),
        ("HARDWARE",    "#8b5cf6", False),
        ("PRONAJEM",    "#f59e0b", False),
        ("TEL/INTERNET","#06b6d4", False),
        ("TESTY",       "#10b981", False),
        ("DROB.ADMIN",  "#84cc16", False),
        ("DROB.OST.",   "#f97316", False),
        ("FIN.SLUZBY",  "#ef4444", False),
        ("VOZIDLO",     "#6366f1", False),
        ("PEREX",       "#ec4899", False),
        ("PRIJMY",      "#22c55e", True),
    ]
    for name, color, is_income in default_cats:
        if not db.query(Category).filter(Category.name == name).first():
            db.add(Category(name=name, color=color, is_income=is_income))
    db.commit()
    db.close()
