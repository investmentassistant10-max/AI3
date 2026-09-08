"""
Ratunek uszkodzonej bazy.

SQLite potrafi uszkodzic plik, gdy zabraknie miejsca na dysku w trakcie
zapisu — a baza rosla do 1.5 GB przy dysku zajetym w 94%. Objaw:
"database disk image is malformed" przy zapytaniach skanujacych tabele.

Ten skrypt NIE naprawia starej bazy. Buduje nowa, czysta, przenoszac do niej
to, co da sie odczytac — czytajac malymi porcjami i pomijajac uszkodzone
fragmenty zamiast przerywac na pierwszym bledzie.

Co jest ratowane i dlaczego akurat to:
  strategies (kandydaci)  — jedyne, co niesie wartosc; odrzucone hipotezy
                            i tak mialy zostac skompaktowane
  tested                  — odciski palca, zeby nie sprawdzac tego samego
                            drugi raz; przy stracie po prostu sprawdzimy
                            ponownie, nic sie nie psuje
  predictions             — log predykcji, najcenniejsza rzecz w calej bazie,
                            bo jego nie da sie odtworzyc przeliczeniem
  calibration, exit_rules, search_runs

    python3 rescue_db.py --check     tylko sprawdz, nie ruszaj
    python3 rescue_db.py             odzyskaj do nowej bazy
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


def open_ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def check():
    print(f"Baza: {DB}  ({os.path.getsize(DB)/1e9:.2f} GB)")
    print(f"Wolne miejsce: {shutil.disk_usage(DB.parent).free/1e9:.1f} GB\n")

    conn = open_ro(DB)
    print("Sprawdzam integralnosc (to potrwa)...")
    t0 = time.time()
    try:
        rows = conn.execute("PRAGMA quick_check(20)").fetchall()
        msgs = [r[0] for r in rows]
        if msgs == ["ok"]:
            print(f"  OK — baza jest spojna ({time.time()-t0:.0f}s)")
            conn.close()
            return True
        print(f"  USZKODZENIA ({time.time()-t0:.0f}s):")
        for m in msgs[:10]:
            print(f"    {m}")
    except Exception as e:
        print(f"  nie da sie sprawdzic: {e}")
    conn.close()
    return False


def copy_table(src, dst, table, columns=None, where=""):
    """Przepisuje tabele porcjami, pomijajac uszkodzone fragmenty."""
    try:
        cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
    except sqlite3.DatabaseError:
        print(f"  {table:<14} nie da sie odczytac struktury — pomijam")
        return 0, 0
    if not cols:
        return 0, 0
    if columns:
        cols = [c for c in cols if c in columns]

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
            # uszkodzony fragment — przeskakujemy i probujemy dalej
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
            print(f"  {table:<14} {copied:,} wierszy...", flush=True)
    dst.commit()
    return copied, lost


def rescue():
    if NEW.exists():
        NEW.unlink()

    free = shutil.disk_usage(DB.parent).free
    if free < 1e9:
        print(f"UWAGA: wolne tylko {free/1e9:.1f} GB. Zwolnij miejsce przed ratunkiem.")
        return

    src = open_ro(DB)
    dst = sqlite3.connect(NEW)
    dst.execute("PRAGMA journal_mode=WAL")

    print("Przepisuje do czystej bazy...\n")
    total_ok = total_lost = 0

    # kandydaci — jedyne, co niesie wartosc
    ok, lost = copy_table(src, dst, "strategies",
                          where="WHERE status IN ('candidate','stale')")
    print(f"  strategie      {ok:,} odzyskanych, {lost:,} straconych")
    total_ok += ok; total_lost += lost

    for table in ("predictions", "calibration", "exit_rules", "search_runs", "tested"):
        ok, lost = copy_table(src, dst, table)
        if ok or lost:
            print(f"  {table:<14} {ok:,} odzyskanych, {lost:,} straconych")
            total_ok += ok; total_lost += lost

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
    print(f"  mv '{DB}' '{BACKUP}'")
    print(f"  mv '{NEW}' '{DB}'")
    print("\nStara zostaje jako kopia — skasuj ja dopiero, gdy nowa sie sprawdzi.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="tylko sprawdz")
    args = ap.parse_args()

    if not DB.exists():
        print("Brak bazy.")
        sys.exit(1)
    if args.check:
        check()
    else:
        check()
        print()
        rescue()
