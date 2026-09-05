"""
Zalohovani a obnova databaze FinTracku.

Pouziti (z prikazove radky, uvnitr aktivovaneho virtualniho prostredi):

    python backup_utils.py auto      -> automaticka zaloha (max 1x denne), spousti setup.bat/start.sh
    python backup_utils.py manual    -> rucni zaloha "ihned", spousti zaloha_nyni.bat/.sh
    python backup_utils.py restore   -> interaktivni obnoveni ze zalohy, spousti obnovit_zalohu.bat/.sh

Zalohy se ukladaji:
  1) vzdy lokalne do slozky "zalohy" primo v teto slozce s aplikaci,
  2) navic (pokud je to nastavene a dostupne) do druhe slozky podle
     souboru "zaloha_config.txt" - typicky slozka Google Disku.

Pro bezpecne kopirovani databaze (i kdyz je zrovna otevrena aplikaci)
se pouziva vestavene SQLite Online Backup API (sqlite3.Connection.backup),
ktere na rozdil od proste kopie souboru negeneruje poskozenou/nekompletni
kopii, i kdyz do databaze nekdo prave zapisuje.
"""

import os
import sys
import glob
import shutil
import sqlite3
from datetime import datetime

# Na Windows konzoli muze pri tisku ceskych znaku dojit k chybe podle
# aktualni kodove stranky - radeji tise nahradime nezobrazitelne znaky,
# nez aby to shodilo cely skript.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "fintrack.db")
LOCAL_BACKUP_DIR = os.path.join(BASE_DIR, "zalohy")
CONFIG_PATH = os.path.join(BASE_DIR, "zaloha_config.txt")

REGULAR_PREFIX = "fintrack_zaloha"
PRERESTORE_PREFIX = "fintrack_PRED_OBNOVOU"

REGULAR_KEEP = 60
PRERESTORE_KEEP = 10


def timestamp():
    # Vcetne milisekund, aby se predeslo kolizi nazvu, kdyby se dve zalohy
    # nahodou spustily ve stejnou vterinu (napr. dve rychle rucni zalohy za sebou).
    now = datetime.now()
    return now.strftime("%Y-%m-%d_%H%M%S") + f"_{now.microsecond // 1000:03d}"


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def ensure_local_dir():
    os.makedirs(LOCAL_BACKUP_DIR, exist_ok=True)
    return LOCAL_BACKUP_DIR


def get_secondary_dir():
    """Precte druhe umisteni zaloh (napr. slozku Google Disku) ze souboru
    zaloha_config.txt. Vrati None, pokud soubor neexistuje, je prazdny,
    nebo dana slozka prave neni dostupna (napr. Google Disk neni spusteny)."""
    if not os.path.isfile(CONFIG_PATH):
        return None
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None

    path = None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        path = line
        break

    if not path or not os.path.isdir(path):
        return None

    target = os.path.join(path, "FinTrack zalohy")
    try:
        os.makedirs(target, exist_ok=True)
    except OSError:
        return None
    return target


def safe_sqlite_copy(src, dst):
    """Bezpecna kopie SQLite databaze pres oficialni SQLite Online Backup API -
    funguje spolehlive, i kdyz je zdrojova databaze prave otevrena a pouzivana
    (na rozdil od obycejne kopie souboru, ktera by mohla zachytit databazi
    upro-stred zapisu a vytvorit tak poskozenou zalohu)."""
    src_conn = sqlite3.connect(src)
    dst_conn = sqlite3.connect(dst)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()


def prune(directory, prefix, keep):
    """Smaze nejstarsi zalohy v dane slozce tak, aby jich zbylo maximalne 'keep'.
    Nazvy souboru obsahuji datum a cas ve formatu, ktery se serazuje spravne
    i jako obycejny text, takze staci abecedni razeni."""
    pattern = os.path.join(directory, f"{prefix}_*.db")
    files = sorted(glob.glob(pattern))
    excess = len(files) - keep
    for f in files[: max(0, excess)]:
        try:
            os.remove(f)
        except OSError:
            pass


def create_backup(prefix=REGULAR_PREFIX, keep=REGULAR_KEEP, mirror=True):
    """Vytvori casove označenou zalohu fintrack.db do slozky 'zalohy', a pokud
    je nastavene a dostupne druhe umisteni, zkopiruje zalohu i tam. Vraci cestu
    k lokalni zaloze, nebo None, pokud fintrack.db jeste vubec neexistuje."""
    if not os.path.isfile(DB_PATH):
        return None

    ensure_local_dir()
    fname = f"{prefix}_{timestamp()}.db"
    local_path = os.path.join(LOCAL_BACKUP_DIR, fname)
    safe_sqlite_copy(DB_PATH, local_path)
    prune(LOCAL_BACKUP_DIR, prefix, keep)

    if mirror:
        secondary_dir = get_secondary_dir()
        if secondary_dir:
            try:
                shutil.copy2(local_path, os.path.join(secondary_dir, fname))
                prune(secondary_dir, prefix, keep)
            except OSError as e:
                print(f"  [POZOR] Zalohu se nepodarilo zkopirovat i na druhe misto ({e}).")
                print("  Lokalni zaloha ve slozce 'zalohy' je v poradku.")

    return local_path


def has_backup_today(prefix=REGULAR_PREFIX):
    ensure_local_dir()
    pattern = os.path.join(LOCAL_BACKUP_DIR, f"{prefix}_{today_str()}_*.db")
    return len(glob.glob(pattern)) > 0


def run_auto():
    """Volano automaticky pri kazdem spusteni aplikace (setup.bat / start.sh).
    Zalohu vytvori nejvyse jednou za den, aby se slozka zbytecne nezaplnovala."""
    if has_backup_today():
        print("Dnesni automaticka zaloha uz existuje, preskakuji.")
        return
    path = create_backup()
    if path:
        print(f"Automaticka zaloha vytvorena: zalohy/{os.path.basename(path)}")
    else:
        print("Databaze fintrack.db jeste neexistuje, zaloha se preskakuje.")


def run_manual():
    """Volano ze skriptu zaloha_nyni.bat / zaloha_nyni.sh - uzivatel si chce
    vytvorit zalohu hned ted (napr. po velkem importu vypisu)."""
    print("Vytvarim zalohu...")
    path = create_backup()
    if path is None:
        print("Databaze fintrack.db jeste neexistuje - neni co zalohovat.")
        return

    print(f"Hotovo! Zaloha ulozena zde: {path}")
    secondary_dir = get_secondary_dir()
    if secondary_dir:
        print(f"Kopie zalohy take zde: {secondary_dir}")
    else:
        print("Kopie na druhe misto se nevytvorila (zkontroluj soubor")
        print("zaloha_config.txt a jestli je dana slozka/disk prave dostupny).")


def list_regular_backups():
    ensure_local_dir()
    pattern = os.path.join(LOCAL_BACKUP_DIR, f"{REGULAR_PREFIX}_*.db")
    return sorted(glob.glob(pattern), reverse=True)


def run_restore():
    """Volano ze skriptu obnovit_zalohu.bat / obnovit_zalohu.sh - interaktivne
    provede uzivatele obnovenim vybrane zalohy, vcetne bezpecnostni zalohy
    aktualnich dat pred prepsanim."""
    files = list_regular_backups()
    if not files:
        print("Ve slozce 'zalohy' jsem nenasel zadne zalohy.")
        return

    print("=" * 55)
    print(" Dostupne zalohy (od nejnovejsi):")
    print("=" * 55)
    for i, f in enumerate(files, start=1):
        mtime = datetime.fromtimestamp(os.path.getmtime(f))
        size_kb = os.path.getsize(f) / 1024
        print(f"  {i}. {mtime.strftime('%d.%m.%Y %H:%M')}   ({size_kb:.0f} KB)   {os.path.basename(f)}")
    print()
    print("  0. Zrusit a nic nedelat")
    print()

    choice = input("Kterou zalohu chces obnovit? Zadej cislo a stiskni Enter: ").strip()
    if choice in ("", "0"):
        print("Zruseno, nic se nezmenilo.")
        return

    try:
        idx = int(choice)
        if idx < 1 or idx > len(files):
            raise ValueError
        chosen = files[idx - 1]
    except ValueError:
        print("Neplatna volba. Nic se nezmenilo.")
        return

    print()
    print(f"Chystas se PREPSAT soucasna data zalohou: {os.path.basename(chosen)}")
    print()
    print("DULEZITE: pred pokracovanim se ujisti, ze je FinTrack ZAVRENY")
    print("(zavri cerne okno s bezicim serverem i zavri stranku v prohlizeci).")
    print()
    confirm = input("Opravdu chces obnovit tuto zalohu? Napis ANO (velkymi pismeny) a stiskni Enter: ").strip()
    if confirm != "ANO":
        print("Zruseno, nic se nezmenilo.")
        return

    # Nez cokoliv prepiseme, pro jistotu zalohujeme i soucasny (aktualni) stav.
    if os.path.isfile(DB_PATH):
        snap = create_backup(prefix=PRERESTORE_PREFIX, keep=PRERESTORE_KEEP)
        if snap:
            print(f"Pro jistotu jsem nejdriv zalohoval aktualni data sem: {snap}")

    # Smazeme pripadne pomocne soubory SQLite, aby neprepsaly obnovena data.
    for suffix in ("-wal", "-shm", "-journal"):
        side = DB_PATH + suffix
        if os.path.isfile(side):
            try:
                os.remove(side)
            except OSError:
                pass

    shutil.copy2(chosen, DB_PATH)
    print()
    print("HOTOVO! Data byla obnovena ze zalohy.")
    print("Ted muzes FinTrack normalne spustit (setup.bat / start.sh).")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "auto"
    if cmd == "auto":
        run_auto()
    elif cmd == "manual":
        run_manual()
    elif cmd == "restore":
        run_restore()
    else:
        print(f"Neznamy prikaz: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
