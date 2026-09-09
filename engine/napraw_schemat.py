"""
Naprawa schematu po odzyskiwaniu bazy.

CO SIE STALO
rescue_db.py odtwarzal tabele poleceniem CREATE TABLE z samymi nazwami kolumn,
bez kluczy glownych. Dane przetrwaly, ale ograniczenia — nie. Konsekwencje sa
ciche i dlatego grozne:

  exit_rules   INSERT OR REPLACE nie mial czego zastapic, wiec dopisywal.
               136 460 wierszy przy 660 faktycznych regulach — ta sama regula
               powielona dwiesta razy.
  tested       INSERT OR IGNORE nie odsiewal duplikatow. Na razie ich nie ma,
               ale licznik prob jest czescia metody statystycznej: zawyzony
               podnosi poprzeczke istotnosci dla wszystkich strategii.

Ten skrypt przebudowuje obie tabele z wlasciwymi kluczami, zostawiajac przy
duplikatach wiersz najswiezszy. Uruchom lokalnie, gdy silnik NIE pracuje.

    python3 napraw_schemat.py
"""
import re
import sqlite3
import sys
import time
from pathlib import Path

import search
import exit_search

DB = Path(__file__).resolve().parent.parent / "data" / "strategies.sqlite"

TABELE = (
    ("tested", search.SCHEMA, "id"),
    ("exit_rules", exit_search.SCHEMA, "strategy_id, rule_label"),
)


def definicja(schema, nazwa):
    m = re.search(rf"CREATE TABLE IF NOT EXISTS {nazwa} \((.*?)\n\);", schema, re.S)
    if not m:
        raise SystemExit(f"nie znalazlem definicji tabeli {nazwa}")
    return m.group(1)


def main():
    if not DB.exists():
        raise SystemExit(f"brak bazy: {DB}")

    conn = sqlite3.connect(DB, timeout=120.0)
    conn.execute("PRAGMA busy_timeout=120000")

    print(f"Baza: {DB}\n")
    for tabela, schema, klucz in TABELE:
        kolumny = [r[1] for r in conn.execute(f"PRAGMA table_info({tabela})")]
        if not kolumny:
            print(f"{tabela:12} brak tabeli — pomijam")
            continue

        obecny = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name=?", (tabela,)).fetchone()[0]
        if "PRIMARY KEY" in obecny.upper():
            print(f"{tabela:12} klucz na miejscu — nic do zrobienia")
            continue

        cols = ", ".join(f'"{c}"' for c in kolumny)
        t0 = time.time()
        conn.execute("DROP TABLE IF EXISTS _naprawa")
        conn.execute(f"CREATE TABLE _naprawa ({definicja(schema, tabela)})")
        conn.execute(
            f"""INSERT OR REPLACE INTO _naprawa ({cols})
                SELECT {cols} FROM {tabela}
                WHERE rowid IN (SELECT MAX(rowid) FROM {tabela} GROUP BY {klucz})""")
        przed = conn.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]
        po = conn.execute("SELECT COUNT(*) FROM _naprawa").fetchone()[0]
        conn.execute(f"DROP TABLE {tabela}")
        conn.execute(f"ALTER TABLE _naprawa RENAME TO {tabela}")
        conn.commit()
        usuniete = przed - po
        print(f"{tabela:12} {przed:>10,} -> {po:>10,}"
              f"{f'   (duplikatow: {usuniete:,})' if usuniete else ''}"
              f"   {time.time()-t0:.0f}s")

    # indeksy z oryginalnego schematu
    conn.executescript(search.SCHEMA)
    conn.executescript(exit_search.SCHEMA)
    conn.commit()

    print("\nPorzadkuje plik (VACUUM) — to potrwa...")
    t0 = time.time()
    conn.execute("VACUUM")
    conn.close()
    print(f"Gotowe w {time.time()-t0:.0f}s. Rozmiar: {DB.stat().st_size/1e6:.0f} MB")
    print("\nSprawdzenie: python3 rescue_db.py --check")


if __name__ == "__main__":
    sys.exit(main())
