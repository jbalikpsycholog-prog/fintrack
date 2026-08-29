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
    # Cislo a nazev uctu, ke kteremu se vypis vztahuje (bereme z hlavicky/radku CSV) -
    # je to vlastnost celeho vypisu, ne jednotlive transakce.
    owner_account_name = Column(String, nullable=True)
    owner_account_number = Column(String, nullable=True)
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
    iban = Column(String, nullable=True)
    bic = Column(String, nullable=True)

    variable_symbol = Column(String, nullable=True)
    constant_symbol = Column(String, nullable=True)
    specific_symbol = Column(String, nullable=True)

    description = Column(Text, nullable=True)
    transaction_type = Column(String, nullable=True)

    # Jednotliva puvodni pole z vypisu CS - drzime je zvlast, aby se
    # pri importu nic neztratilo (viz pozadavek na zobrazeni "1:1").
    message_for_me = Column(Text, nullable=True)
    payer_address = Column(Text, nullable=True)
    message_for_recipient = Column(Text, nullable=True)
    recipient_address = Column(Text, nullable=True)
    note = Column(Text, nullable=True)

    # Kategorie, kterou transakci prirazuje sama banka (informativni, odlisna
    # od uzivatelovych vlastnich kategorii v poli category_id).
    bank_category = Column(String, nullable=True)

    card_number = Column(String, nullable=True)
    card_location = Column(String, nullable=True)
    payment_reference = Column(String, nullable=True)

    # Cely puvodni radek z CSV (vsechny sloupce) jako JSON - pojistka, aby
    # byl k dispozici uplne kazdy udaj z vypisu, i kdyz pro nej nemame
    # vlastni sloupec.
    raw_data = Column(Text, nullable=True)

    is_income = Column(Boolean, default=False)

    # Zdroj transakce: "bank" (import z vypisu), "cash" (rucni hotovostni
    # zaznam), "nonmonetary" (nepenezni polozka, napr. pausal za auto).
    source_type = Column(String, default="bank")

    # Jedno spolecne pole pro relevanci: pocita se transakce do prehledu
    # prijmu/vydaju a do danoveho podkladu? (nahrazuje puvodni "excluded")
    tax_relevant = Column(Boolean, default=True)

    # Odkaz na doklad/fakturu (napr. URL na Google disk). Zadny upload
    # souboru primo do appky - jen odkaz ven.
    document_url = Column(Text, nullable=True)

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


# Sloupce, ktere pribyly po prvnim vydani aplikace. Pri kazdem startu se
# zkontroluje, jestli v databazi chybi, a pokud ano, doplni se (ALTER TABLE),
# aniz by se smazala existujici data.
NEW_COLUMNS = [
    ("import_batches", "owner_account_name", "TEXT"),
    ("import_batches", "owner_account_number", "TEXT"),
    ("import_batches", "period_label", "TEXT"),
    ("transactions", "iban", "TEXT"),
    ("transactions", "bic", "TEXT"),
    ("transactions", "message_for_me", "TEXT"),
    ("transactions", "payer_address", "TEXT"),
    ("transactions", "message_for_recipient", "TEXT"),
    ("transactions", "recipient_address", "TEXT"),
    ("transactions", "note", "TEXT"),
    ("transactions", "bank_category", "TEXT"),
    ("transactions", "card_number", "TEXT"),
    ("transactions", "card_location", "TEXT"),
    ("transactions", "payment_reference", "TEXT"),
    ("transactions", "raw_data", "TEXT"),
    ("transactions", "source_type", "TEXT DEFAULT 'bank'"),
    ("transactions", "tax_relevant", "BOOLEAN DEFAULT 1"),
    ("transactions", "document_url", "TEXT"),
]


def run_migrations():
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        tables_seen = {}
        newly_added = set()
        for table, column, coltype in NEW_COLUMNS:
            if table not in tables_seen:
                tables_seen[table] = [row[1] for row in cur.execute(f"PRAGMA table_info({table})")]
            if column not in tables_seen[table]:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
                tables_seen[table].append(column)
                newly_added.add((table, column))

        # Jednorazovy prevod stareho pole "excluded" (pokud v databazi jeste
        # existuje) do noveho "tax_relevant" - jen v okamziku, kdy sloupec
        # tax_relevant teprve vznikl, aby se pozdeji nepremazavaly rucni
        # zmeny, ktere uzivatel v appce udela.
        if ("transactions", "tax_relevant") in newly_added and "excluded" in tables_seen["transactions"]:
            cur.execute("UPDATE transactions SET tax_relevant = 0 WHERE excluded = 1")

        conn.commit()
        conn.close()
    except Exception:
        pass


run_migrations()
