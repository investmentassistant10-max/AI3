"""
Kompaktowanie bazy: przenosi odrzucone hipotezy do lekkiej tabeli.

Do tej pory kazda sprawdzona hipoteza — takze odrzucona jako zbyt rzadka
czy zbyt czesta — zajmowala pelny wiersz z definicja JSON, opisem i
kompletem statystyk: okolo 426 bajtow. Przy 3.6 mln hipotez to 1.5 GB,
a przy dziesieciu milionach byloby ponad 4 GB.

Po odrzuconych potrzebujemy dwoch rzeczy: odcisku palca (zeby nie sprawdzac
tej samej hipotezy drugi raz) i tego, ze proba zostala wykonana (bo od liczby
prob zalezy prog istotnosci). Oba mieszcza sie w ~30 bajtach.

URUCHAMIAJ PRZY ZATRZYMANYM SILNIKU.

    python3 compact_db.py --dry-run    pokaz, ile zaoszczedzi
    python3 compact_db.py              wykonaj
"""
import argparse
import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "strategies.sqlite"

KEEP = ("candidate", "stale")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DB.exists():
        print("Brak bazy.")
        return

    size_before = os.path.getsize(DB)
    conn = sqlite3.connect(DB)

    total = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
    keep_n = conn.execute(
        f"SELECT COUNT(*) FROM strategies WHERE status IN {KEEP}").fetchone()[0]
    move_n = total - keep_n

    print(f"Baza:            {size_before/1e9:.2f} GB")
    print(f"Wierszy lacznie: {total:,}")
    print(f"Do zachowania:   {keep_n:,} (kandydaci i wycofane)")
    print(f"Do przeniesienia:{move_n:,} (odrzucone)")
    print(f"Szacowany rozmiar po: ~{(keep_n*426 + move_n*30)/1e9:.2f} GB")

    if args.dry_run:
        print("\n(--dry-run: nic nie zmieniam)")
        conn.close()
        return

    print("\nPrzenosze odrzucone do lekkiej tabeli...")
    t0 = time.time()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tested (id TEXT PRIMARY KEY, status TEXT);
        INSERT OR IGNORE INTO tested (id, status)
            SELECT id, status FROM strategies
            WHERE status NOT IN ('candidate','stale');
    """)
    conn.commit()
    moved = conn.execute("SELECT COUNT(*) FROM tested").fetchone()[0]
    print(f"  przeniesiono {moved:,} w {time.time()-t0:.0f}s")

    print("Usuwam je z glownej tabeli...")
    t0 = time.time()
    conn.execute("DELETE FROM strategies WHERE status NOT IN ('candidate','stale')")
    conn.commit()
    print(f"  gotowe w {time.time()-t0:.0f}s")

    print("Odzyskuje miejsce (VACUUM — to potrwa)...")
    t0 = time.time()
    conn.execute("VACUUM")
    conn.close()
    print(f"  gotowe w {time.time()-t0:.0f}s")

    size_after = os.path.getsize(DB)
    print(f"\nBylo:  {size_before/1e9:.2f} GB")
    print(f"Jest:  {size_after/1e9:.2f} GB")
    print(f"Zwolnione: {(size_before-size_after)/1e9:.2f} GB")
    print("\nLiczba prob i deduplikacja dzialaja bez zmian — obie tabele")
    print("sa liczone razem.")


if __name__ == "__main__":
    main()
