"""
Straznik bazy: migawki i kontrola spojnosci.

Powod: baza strategii ulegla uszkodzeniu dwa razy w ciagu doby, za drugim
razem tak gleboko, ze zniknal naglowek pliku i nie dalo sie jej otworzyc
wcale. Utrata pracy silnika jest do odrobienia (przeliczy ponownie), ale
log predykcji — nie. Dlatego silnik co jakis czas robi migawke.

Migawka powstaje przez sqlite3 backup API, a nie przez kopiowanie pliku:
API czyta baze pod blokada i daje spojna kopie nawet wtedy, gdy silnik
w tej samej chwili pisze. Kopiowanie pliku w trakcie zapisu jest wlasnie
tym, co potrafi wyprodukowac polamana kopie.

Trzymamy dwie migawki na zmiane (A/B). Gdyby uszkodzenie zdazylo trafic
do najswiezszej, druga wciaz jest starsza o cala rotacje.
"""
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = ROOT / "data" / "kopie"

MAGIC = b"SQLite format 3\x00"


def has_magic(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(16) == MAGIC
    except OSError:
        return False


def quick_check(path):
    """(ok, komunikat). Nie rzuca wyjatkow — wolno ja wolac przed startem.

    Otwieramy normalnie, a nie w trybie ro. Baza w trybie WAL potrzebuje przy
    otwarciu pliku -shm; tryb ro zabrania go zalozyc, wiec zdrowa baza zglasza
    wtedy "unable to open database file" i wyglada jak zepsuta.
    """
    path = Path(path)
    if not path.exists():
        return True, "brak bazy (zostanie zalozona)"
    if not has_magic(path):
        return False, "zniszczony naglowek pliku — baza nie do otwarcia"
    try:
        conn = sqlite3.connect(str(path), timeout=15.0)
        msgs = [r[0] for r in conn.execute("PRAGMA quick_check(5)")]
        conn.close()
        return (msgs == ["ok"]), ("ok" if msgs == ["ok"] else "; ".join(msgs[:3]))
    except sqlite3.DatabaseError as e:
        return False, str(e)


def snapshot(conn, keep=2):
    """Spojna migawka otwartej bazy. Zwraca (sciezka, sekundy) albo (None, blad)."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    slot = int(time.time() // 3600) % keep
    target = BACKUP_DIR / f"strategies.kopia{slot}.sqlite"
    tmp = target.with_suffix(".sqlite.tmp")
    t0 = time.time()
    try:
        for stale in (tmp, Path(str(tmp) + "-wal"), Path(str(tmp) + "-shm")):
            if stale.exists():
                stale.unlink()
        dst = sqlite3.connect(tmp)
        with dst:
            conn.backup(dst)
        dst.close()
        tmp.replace(target)          # podmiana atomowa: albo stara, albo nowa
        return target, time.time() - t0
    except (sqlite3.DatabaseError, OSError) as e:
        return None, str(e)


def newest_snapshot():
    if not BACKUP_DIR.exists():
        return None
    kopie = [p for p in BACKUP_DIR.glob("strategies.kopia*.sqlite") if has_magic(p)]
    return max(kopie, key=lambda p: p.stat().st_mtime, default=None)
