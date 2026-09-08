"""
Ratunek uszkodzonej bazy.

Buduje NOWA, czysta baze, przenoszac do niej to, co da sie odczytac —
czytajac malymi porcjami i pomijajac uszkodzone fragmenty zamiast
przerywac na pierwszym bledzie.

    python3 rescue_db.py --check                 sprawdz biezaca baze
    python3 rescue_db.py                         odzyskaj z biezacej bazy
    python3 rescue_db.py --from PLIK             odzyskaj ze wskazanego pliku
                                                 (np. ze starej kopii, gdy
                                                  biezaca nie ma juz naglowka)

Gdy w pliku nie ma naglowka "SQLite format 3" — plik jest nie do odczytania
w calosci i trzeba ratowac ze starszej kopii.
"""
import argparse
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "strategies.sqlite"
NEW = ROOT / "data" / "strategies.rescued.sqlite"
BACKUP = ROOT / "data" / "strategies.uszkodzona.sqlite"

CHUNK = 5000
MAGIC = b"SQLite format 3\x00"

# kolejnosc ma znaczenie: najpierw to, czego nie da sie odtworzyc obliczeniem
PRIORITY = ("vol_predictions", "predictions", "calibration",
            "strategies", "exit_rules", "search_runs", "tested")


def has_magic(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(16) == MAGIC
    except OSError:
        return False


def open_ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def check(path=DB):
    print(f"Baza: {path}  ({os.path.getsize(path)/1e9:.2f} GB)")
    print(f"Wolne miejsce: {shutil.disk_usage(Path(path).parent).free/1e9:.1f} GB\n")

    if not has_magic(path):
        print("  NAGLOWEK ZNISZCZONY — pliku nie da sie otworzyc wcale.")
        print("  Ratuj ze starszej kopii:  python3 rescue_db.py --from <kopia>")
        return False

    conn = open_ro(path)
    print("Sprawdzam integralnosc (to potrwa)...")
    t0 = time.time()
    ok = False
    try:
        msgs = [r[0] for r in conn.execute("PRAGMA quick_check(20)")]
        if msgs == ["ok"]:
            print(f"  OK — baza jest spojna ({time.time()-t0:.0f}s)")
            ok = True
        else:
            print(f"  USZKODZENIA ({time.time()-t0:.0f}s):")
            for m in msgs[:10]:
                print(f"    {m}")
    except Exception as e:
        print(f"  nie da sie sprawdzic: {e}")
    conn.close()
    return ok


def list_tables(src):
    try:
        return [r[0] for r in src.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
    except sqlite3.DatabaseError:
        return list(PRIORITY)


def copy_table(src, dst, table, where=""):
    """Przepisuje tabele porcjami, pomijajac uszkodzone fragmenty."""
    try:
        info = list(src.execute(f"PRAGMA table_info({table})"))
    except sqlite3.DatabaseError:
        print(f"  {table:<16} nie da sie odczytac struktury — pomijam")
        return 0, 0
    if not info:
        return 0, 0
    cols = [r[1] for r in info]

    coldef = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join("?" * len(cols))
    dst.execute(f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(cols)})")

    copied = lost = offset = 0
    while True:
        try:
            batch = src.execute(
                f"SELECT {coldef} FROM {table} {where} LIMIT {CHUNK} OFFSET {offset}"
            ).fetchall()
        except sqlite3.DatabaseError:
            lost += CHUNK
            offset += CHUNK
            if lost > 500_000:
                break
            continue
        if not batch:
            break
        dst.executemany(
            f"INSERT INTO {table} ({coldef}) VALUES ({placeholders})", batch)
        copied += len(batch)
        offset += CHUNK
        if copied % 50_000 == 0:
            dst.commit()
            print(f"  {table:<16} {copied:,} wierszy...", flush=True)
    dst.commit()
    return copied, lost


def rescue(source=DB):
    if NEW.exists():
        NEW.unlink()

    free = shutil.disk_usage(Path(source).parent).free
    if free < 2e9:
        print(f"UWAGA: wolne tylko {free/1e9:.1f} GB. Zwolnij miejsce przed ratunkiem.")
        return

    src = open_ro(source)
    dst = sqlite3.connect(NEW)
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA synchronous=NORMAL")

    print(f"Zrodlo: {source}")
    print("Przepisuje do czystej bazy...\n")
    total_ok = total_lost = 0

    tables = list_tables(src)
    order = [t for t in PRIORITY if t in tables] + \
            [t for t in tables if t not in PRIORITY]

    for table in order:
        # z odrzuconych hipotez zostawiamy tylko kandydatow — reszta to balast
        where = "WHERE status IN ('candidate','stale')" if table == "strategies" else ""
        ok, lost = copy_table(src, dst, table, where)
        if ok or lost:
            print(f"  {table:<16} {ok:,} odzyskanych, {lost:,} straconych")
            total_ok += ok
            total_lost += lost

    print("\nOdtwarzam indeksy...")
    for stmt in (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_strat_id ON strategies(id)",
        "CREATE INDEX IF NOT EXISTS idx_rating ON strategies(rating DESC)",
        "CREATE INDEX IF NOT EXISTS idx_status ON strategies(status)",
    ):
        try:
            dst.execute(stmt)
        except sqlite3.DatabaseError as e:
            print(f"  {e}")
    dst.commit()
    dst.close()
    src.close()

    print(f"\nOdzyskano {total_ok:,} wierszy, stracono okolo {total_lost:,}")
    print(f"Nowa baza: {os.path.getsize(NEW)/1e9:.2f} GB")
    print("\nZeby ja wlaczyc:")
    print(f"  mv '{DB}' '{DB}.zepsuta' 2>/dev/null; rm -f '{DB}-wal' '{DB}-shm'")
    print(f"  mv '{NEW}' '{DB}'")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="tylko sprawdz")
    ap.add_argument("--from", dest="source", default=None,
                    help="plik zrodlowy (domyslnie biezaca baza)")
    args = ap.parse_args()

    source = Path(args.source) if args.source else DB
    if not source.exists():
        print(f"Brak pliku: {source}")
        sys.exit(1)
    if args.check:
        check(source)
    else:
        check(source)
        print()
        rescue(source)
