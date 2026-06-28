from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime

DATABASE_URL = "sqlite:///./fintrack.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    imported_at = Column(DateTime, default=datetime.now)
    transaction_count = Column(Integer, default=0)
    period_label = Column(String, nullable=True)
    transactions = relationship("Transaction", back_populates="import_batch")


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    is_active = Column(Boolean, default=True)

    transactions = relationship("Transaction", back_populates="category")
    rules = relationship("ClassificationRule", back_populates="category")
    budgets = relationship("Budget", back_populates="category")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    import_batch_id = Column(Integer, ForeignKey("import_batches.id"), nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)

    cs_transaction_id = Column(String, nullable=True, index=True)
    date = Column(String, nullable=True)
    year = Column(Integer, nullable=True, index=True)
    month = Column(Integer, nullable=True, index=True)

    amount = Column(Float, nullable=False, default=0.0)
    currency = Column(String, default="CZK")

    counterparty_account = Column(String, nullable=True)
    counterparty_name = Column(String, nullable=True)
    bank_code = Column(String, nullable=True)

    variable_symbol = Column(String, nullable=True)
    constant_symbol = Column(String, nullable=True)
    specific_symbol = Column(String, nullable=True)

    description = Column(Text, nullable=True)
    transaction_type = Column(String, nullable=True)

    is_income = Column(Boolean, default=False)
    excluded = Column(Boolean, default=False)

    receipt_path = Column(String, nullable=True)

    import_batch = relationship("ImportBatch", back_populates="transactions")
    category = relationship("Category", back_populates="transactions")


class ClassificationRule(Base):
    __tablename__ = "classification_rules"

    id = Column(Integer, primary_key=True, index=True)
    counterparty_name = Column(String, nullable=True, index=True)
    description_contains = Column(String, nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)

    category = relationship("Category", back_populates="rules")


class Budget(Base):
    __tablename__ = "budgets"

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    year = Column(Integer, nullable=False)
    amount = Column(Float, default=0.0)

    category = relationship("Category", back_populates="budgets")


def run_migrations():
        import sqlite3
        db_path = DATABASE_URL.replace("sqlite:///", "")
        try:
                    conn = sqlite3.connect(db_path)
                    cur = conn.cursor()
                    cols = [row[1] for row in cur.execute("PRAGMA table_info(import_batches)")]
                    if "period_label" not in cols:
                                    cur.execute("ALTER TABLE import_batches ADD COLUMN period_label TEXT")
                                    conn.commit()
                                    conn.close()
    except Exception:
        pass

run_migrations()
