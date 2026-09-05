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

    # Typ kategorie: "expense" (vydajova) nebo "income" (prijmova) - urcuje,
    # ktere kategorie se nabizeji u vydajovych/prijmovych transakci.
    category_type = Column(String, default="expense")

    # Vychozi danova relevance pro transakce v teto kategorii (pouziva se
    # jen jako navrh/predvyplneni ve formulari, uzivatel muze u kazde
    # transakce zmenit).
    default_tax_relevant = Column(Boolean, default=True)

    transactions = relationship("Transaction", back_populates="category", foreign_keys="Transaction.category_id")
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

    # Navrh kategorie/danove relevance na zaklade jiz zarazenych transakci se
    # stejnym popisem a typem (viz main.recompute_suggestions). Jen doporuceni
    # - do skutecneho category_id/tax_relevant se prepise az potvrzenim (OK).
    suggested_category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    suggested_tax_relevant = Column(Boolean, nullable=True)

    receipt_path = Column(String, nullable=True)

    import_batch = relationship("ImportBatch", back_populates="transactions")
    category = relationship("Category", back_populates="transactions", foreign_keys=[category_id])
    suggested_category = relationship("Category", foreign_keys=[suggested_category_id])


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


class OpeningBalance(Base):
    """Pocatecni stav beznaho uctu / pokladny k 1.1. daneho roku - zaklad pro
    prehled Penize (vyvoj zustatku po mesicich na strance /money)."""
    __tablename__ = "opening_balances"

    id = Column(Integer, primary_key=True, index=True)
    year = Column(Integer, nullable=False)
    # "bank" (bezny ucet) nebo "cash" (pokladna) - stejne hodnoty jako
    # Transaction.source_type, aby se dalo primo parovat.
    account_type = Column(String, nullable=False)
    amount = Column(Float, default=0.0)


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
    ("categories", "category_type", "TEXT DEFAULT 'expense'"),
    ("categories", "default_tax_relevant", "BOOLEAN DEFAULT 1"),
    ("transactions", "suggested_category_id", "INTEGER"),
    ("transactions", "suggested_tax_relevant", "BOOLEAN"),
]


# Cilovy seznam kategorii dle uzivatele (krok 3): (nazev, typ, vychozi danova
# relevance). Pouziva se jen k jednorazovemu seedovani/aktualizaci pri prvnim
# spusteni po pridani sloupcu category_type/default_tax_relevant - pozdejsi
# rucni zmeny uzivatele v appce se timto neprepisuji.
CATEGORY_SEED = [
    # Vydajove (bez danoveho dopadu)
    ("PENZIJKO", "expense", False),
    ("DAŇ Z PŘÍJMU OSVČ_ZÁLOHA", "expense", False),
    ("DAŇ Z PŘÍJMU OSVČ", "expense", False),
    ("ZP OSVČ_ZÁLOHA", "expense", False),
    ("ZP OSVČ", "expense", False),
    ("SOC.P OSVČ", "expense", False),
    ("SOC.P OSVČ_ZÁLOHA", "expense", False),
    ("OSOBNÍ POTŘEBA", "expense", False),
    ("POŘÍZENÍ HM", "expense", False),
    # Vydajove (s danovym dopadem)
    ("ODPISY HM", "expense", True),
    ("DROB.VYD", "expense", True),
    ("BANK.POPLATKY", "expense", True),
    # Prijmove (bez danoveho dopadu)
    ("VLASTNÍ PROSTŘEDKY OSVČ", "income", False),
    # Prijmove (s danovym dopadem) - "DRAVOTNÍ POJ." opraveno na "ZDRAVOTNÍ POJ." (zjevny preklep)
    ("ZDRAVOTNÍ POJ.", "income", True),
    ("PEDAGOG.PRAC.", "income", True),
    ("OSTATNÍ", "income", True),
]

# Kategorie, ktere uzivatel vyslovne zrusil (deaktivuji se, historicke
# transakce v nich zustanou zachovany, jen se prestanou nabizet).
CATEGORY_DEACTIVATE = ["DROB.ADMIN", "DROB.OST.", "FIN.SLUZBY", "PRIJMY"]


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

        # Jednorazove seedovani/aktualizace kategorii (krok 3) - jen v okamziku,
        # kdy sloupec category_type teprve vznikl, aby se pozdeji nepremazavaly
        # rucni zmeny, ktere uzivatel v appce udela.
        if ("categories", "category_type") in newly_added:
            existing = {row[0]: row[1] for row in cur.execute("SELECT name, id FROM categories")}
            for name, cat_type, default_relevant in CATEGORY_SEED:
                if name in existing:
                    cur.execute(
                        "UPDATE categories SET category_type = ?, default_tax_relevant = ?, is_active = 1 WHERE id = ?",
                        (cat_type, 1 if default_relevant else 0, existing[name]),
                    )
                else:
                    cur.execute(
                        "INSERT INTO categories (name, is_active, category_type, default_tax_relevant) VALUES (?, 1, ?, ?)",
                        (name, cat_type, 1 if default_relevant else 0),
                    )
            for name in CATEGORY_DEACTIVATE:
                if name in existing:
                    cur.execute("UPDATE categories SET is_active = 0 WHERE id = ?", (existing[name],))

        conn.commit()
        conn.close()
    except Exception:
        pass


run_migrations()
