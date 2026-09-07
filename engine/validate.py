"""
Potwierdzanie strategii na skarbcu — ostatnich dwoch latach, ktorych
silnik NIGDY nie widzial podczas szukania.

To jedyny naprawdę uczciwy test, jaki mamy przed uruchomieniem strategii
na żywo. Dlatego skarbiec ma budżet: każde odpytanie jest logowane, a im
więcej strategii przez niego przepuścimy, tym mniej znaczy fakt, że któraś
przeszła. Sprawdzamy garstkę najlepszych, nie wszystko po kolei.
"""
import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from features import build
from evaluate import split_search_treasury, evaluate
from strategy import build_mask
import json

ROOT = Path(__file__).resolve().parent.parent
STRATEGY_DB = ROOT / "data" / "strategies.sqlite"

# Skarbiec zuzywa sie od patrzenia. Kazda strategia przepuszczona przez
# ostatnie dwa lata to kolejna proba na tych samych 502 sesjach — po
# kilkuset takich probach "potwierdzenie" nie znaczy juz nic, bo cos musialo
# przejsc przypadkiem. Limit jest twardy i celowo niski.
TREASURY_BUDGET = 100

EXTRA_SCHEMA = """
ALTER TABLE strategies ADD COLUMN treasury_n_signals INTEGER;
ALTER TABLE strategies ADD COLUMN treasury_edge_mean REAL;
ALTER TABLE strategies ADD COLUMN treasury_edge_hit REAL;
ALTER TABLE strategies ADD COLUMN treasury_hit_rate REAL;
ALTER TABLE strategies ADD COLUMN treasury_status TEXT;
ALTER TABLE strategies ADD COLUMN treasury_checked_at TEXT;
"""

TREASURY_LOG = """
CREATE TABLE IF NOT EXISTS treasury_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at  TEXT,
    n_strategies INTEGER,
    n_confirmed  INTEGER,
    note        TEXT
);
"""


def ensure_columns(conn):
    for stmt in EXTRA_SCHEMA.strip().split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # kolumna juz istnieje
    conn.executescript(TREASURY_LOG)
    conn.commit()


def treasury_budget_used(conn):
    row = conn.execute("SELECT COALESCE(SUM(n_strategies),0) FROM treasury_log").fetchone()
    return row[0]


def validate_top(n=10):
    df, _ = build()
    search_df, treasury_df = split_search_treasury(df)

    conn = sqlite3.connect(STRATEGY_DB)
    conn.row_factory = sqlite3.Row
    from search import ensure_schema
    ensure_schema(conn)
    ensure_columns(conn)

    used = treasury_budget_used(conn)
    remaining = TREASURY_BUDGET - used
    print(f"Skarbiec: {treasury_df.index.min().date()} -> {treasury_df.index.max().date()} ({len(treasury_df)} dni)")
    print(f"Budzet: zuzyte {used} z {TREASURY_BUDGET}, zostalo {remaining}\n")

    if remaining <= 0:
        print("BUDZET SKARBCA WYCZERPANY.")
        print("Kazde kolejne sprawdzenie osłabia wartosc wszystkich poprzednich —")
        print("po tylu probach na tych samych 502 sesjach 'potwierdzenie' przestaje")
        print("cokolwiek znaczyc. Poczekaj na nowe dane albo zwieksz TREASURY_BUDGET")
        print("swiadomie, wiedzac, co tracisz.")
        conn.close()
        return

    if n > remaining:
        print(f"Zadales {n} strategii, a w budzecie zostalo {remaining}. Sprawdzam {remaining}.")
        n = remaining

    rows = conn.execute(
        """SELECT * FROM strategies WHERE status='candidate'
           ORDER BY rating DESC LIMIT ?""", (n,)
    ).fetchall()

    print("=" * 108)
    print(f"{'rating':>6} | {'SZUKANIE (2000-2024)':^32} | {'SKARBIEC (2024-2026)':^30} | opis")
    print(f"{'':>6} | {'przew%':>8} {'traf%':>7} {'epiz':>6} {'':>6} | {'przew%':>8} {'traf%':>7} {'sygn':>6} {'':>4} |")
    print("-" * 108)

    n_confirmed = 0
    now = datetime.now(timezone.utc).isoformat()

    for r in rows:
        strat = json.loads(r["definition"])
        mask = build_mask(treasury_df, strat)
        t_stats = evaluate(treasury_df, mask, strat["horizon"])

        if t_stats is None or t_stats.get("status") != "ok":
            status = t_stats.get("status", "brak") if t_stats else "brak"
            verdict = "?"
            t_edge_mean = t_edge_hit = t_hit = t_n = None
        else:
            t_edge_mean = t_stats["edge_mean"]
            t_edge_hit = t_stats["edge_hit"]
            t_hit = t_stats["hit_rate"]
            t_n = t_stats["n_signals"]
            # potwierdzenie = przewaga zachowala ten sam kierunek
            same_direction = t_edge_mean * r["edge_mean"] > 0
            status = "potwierdzona" if same_direction else "obalona"
            verdict = "TAK" if same_direction else "nie"
            if same_direction:
                n_confirmed += 1

        conn.execute(
            """UPDATE strategies SET treasury_n_signals=?, treasury_edge_mean=?,
               treasury_edge_hit=?, treasury_hit_rate=?, treasury_status=?,
               treasury_checked_at=? WHERE id=?""",
            (t_n, t_edge_mean, t_edge_hit, t_hit, status, now, r["id"]),
        )

        fmt = lambda v, w, p=3: f"{v:>{w}.{p}f}" if v is not None else f"{'—':>{w}}"
        print(f"{r['rating']:>6.1f} | {r['edge_mean']:>8.3f} {r['hit_rate']:>7.1f} "
              f"{r['n_episodes']:>6} {'':>6} | {fmt(t_edge_mean,8)} {fmt(t_hit,7,1)} "
              f"{t_n if t_n is not None else '—':>6} {verdict:>4} | {r['description']}")

    conn.execute(
        "INSERT INTO treasury_log (checked_at, n_strategies, n_confirmed, note) VALUES (?,?,?,?)",
        (now, len(rows), n_confirmed, f"top {n} wg ratingu"),
    )
    conn.commit()

    print("-" * 108)
    print(f"\nPotwierdzonych na skarbcu: {n_confirmed} z {len(rows)}")
    print(f"Budzet skarbca po tej operacji: {treasury_budget_used(conn)} odpytan")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()
    validate_top(args.top)
