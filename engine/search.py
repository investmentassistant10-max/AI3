"""
Petla przeszukiwania: generuje hipotezy, ocenia je i zapisuje wyniki
do lokalnej bazy.

Zasady:
- Szuka WYLACZNIE na danych sprzed skarbca. Ostatnie dwa lata pozostaja
  nietkniete, zeby bylo czym potwierdzic to, co znajdziemy.
- Kazda sprawdzona hipoteza jest zapisywana, takze ta odrzucona. Licznik
  prob jest czescia metody statystycznej, nie tylko logiem.
- Da sie przerwac i wznowic — juz sprawdzone hipotezy nie sa liczone drugi raz.

Uzycie:
    python3 search.py                 # pelny przebieg
    python3 search.py --minutes 10    # ograniczony czasowo
    python3 search.py --singles-only  # tylko strategie jednowarunkowe
"""
import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from features import build
from evaluate import split_search_treasury, evaluate, stability_by_period
from strategy import generate_single, generate_pairs, build_mask, describe, count_space
from rating import compute_rating

ROOT = Path(__file__).resolve().parent.parent
STRATEGY_DB = ROOT / "data" / "strategies.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
    id              TEXT PRIMARY KEY,
    definition      TEXT NOT NULL,
    description     TEXT,
    horizon         INTEGER,
    n_signals       INTEGER,
    n_episodes      INTEGER,
    frequency_pct   REAL,
    mean            REAL,
    base_mean       REAL,
    edge_mean       REAL,
    hit_rate        REAL,
    base_hit_rate   REAL,
    edge_hit        REAL,
    t_stat          REAL,
    p_value         REAL,
    accuracy_score  REAL,
    stability_score REAL,
    frequency_score REAL,
    significance_multiplier REAL,
    rating          REAL,
    status          TEXT,
    tested_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_rating ON strategies(rating DESC);
CREATE INDEX IF NOT EXISTS idx_status ON strategies(status);

CREATE TABLE IF NOT EXISTS search_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT,
    finished_at  TEXT,
    n_tested     INTEGER,
    n_kept       INTEGER,
    n_total_seen INTEGER,
    data_from    TEXT,
    data_to      TEXT,
    notes        TEXT
);
"""


def get_strategy_db():
    STRATEGY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STRATEGY_DB)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def known_ids(conn):
    return {row[0] for row in conn.execute("SELECT id FROM strategies")}


def total_tested(conn):
    return conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]


def save_batch(conn, rows):
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO strategies (
            id, definition, description, horizon,
            n_signals, n_episodes, frequency_pct,
            mean, base_mean, edge_mean,
            hit_rate, base_hit_rate, edge_hit,
            t_stat, p_value,
            accuracy_score, stability_score, frequency_score,
            significance_multiplier, rating, status, tested_at
        ) VALUES (
            :id, :definition, :description, :horizon,
            :n_signals, :n_episodes, :frequency_pct,
            :mean, :base_mean, :edge_mean,
            :hit_rate, :base_hit_rate, :edge_hit,
            :t_stat, :p_value,
            :accuracy_score, :stability_score, :frequency_score,
            :significance_multiplier, :rating, :status, :tested_at
        )
        """,
        rows,
    )
    conn.commit()


def run_search(minutes=None, singles_only=False, max_strategies=None, quiet=False):
    started = datetime.now(timezone.utc)
    deadline = time.time() + minutes * 60 if minutes else None

    df, problems = build()
    search_df, treasury_df = split_search_treasury(df)

    if not quiet:
        print(f"Dane do szukania: {search_df.index.min().date()} -> {search_df.index.max().date()} ({len(search_df)} dni)")
        print(f"Skarbiec (nietykany): {treasury_df.index.min().date()} -> {treasury_df.index.max().date()} ({len(treasury_df)} dni)")
        if problems:
            print(f"UWAGI DO DANYCH: {problems}")

    conn = get_strategy_db()
    seen = known_ids(conn)
    n_prior = len(seen)
    space_size = count_space(pairs=not singles_only)

    if not quiet:
        print(f"Przestrzen hipotez: {space_size:,} | juz sprawdzonych wczesniej: {n_prior:,}")
        print()

    generators = [generate_single()]
    if not singles_only:
        generators.append(generate_pairs())

    batch = []
    n_tested = 0
    n_kept = 0
    t0 = time.time()

    for gen in generators:
        for strat in gen:
            if strat["id"] in seen:
                continue
            if deadline and time.time() > deadline:
                break
            if max_strategies and n_tested >= max_strategies:
                break

            seen.add(strat["id"])
            n_tested += 1

            mask = build_mask(search_df, strat)
            stats = evaluate(search_df, mask, strat["horizon"])

            now = datetime.now(timezone.utc).isoformat()

            if stats is None or stats.get("status") != "ok":
                # nie da sie tego uczciwie ocenic (za rzadkie, za czeste,
                # za malo niezaleznych epizodow) — ale i tak liczy sie
                # do puli prob, bo probe wykonalismy
                batch.append({
                    "id": strat["id"],
                    "definition": json.dumps(strat, separators=(",", ":")),
                    "description": describe(strat),
                    "horizon": strat["horizon"],
                    "n_signals": None, "n_episodes": None, "frequency_pct": None,
                    "mean": None, "base_mean": None, "edge_mean": None,
                    "hit_rate": None, "base_hit_rate": None, "edge_hit": None,
                    "t_stat": None, "p_value": None,
                    "accuracy_score": None, "stability_score": None,
                    "frequency_score": None, "significance_multiplier": None,
                    "rating": 0.0,
                    "status": (stats or {}).get("status", "unevaluable"),
                    "tested_at": now,
                })
            else:
                periods = stability_by_period(search_df, mask, strat["horizon"])
                n_trials_so_far = n_prior + n_tested
                scores = compute_rating(stats, periods, n_trials_so_far)

                status = "candidate" if scores["rating"] >= 40 else "weak"
                if scores["rating"] >= 40:
                    n_kept += 1

                batch.append({
                    "id": strat["id"],
                    "definition": json.dumps(strat, separators=(",", ":")),
                    "description": describe(strat),
                    "horizon": strat["horizon"],
                    "n_signals": stats["n_signals"],
                    "n_episodes": stats["n_episodes"],
                    "frequency_pct": stats["frequency_pct"],
                    "mean": stats["mean"],
                    "base_mean": stats["base_mean"],
                    "edge_mean": stats["edge_mean"],
                    "hit_rate": stats["hit_rate"],
                    "base_hit_rate": stats["base_hit_rate"],
                    "edge_hit": stats["edge_hit"],
                    "t_stat": stats["t_stat"],
                    "p_value": stats["p_value"],
                    "accuracy_score": scores["accuracy_score"],
                    "stability_score": scores["stability_score"],
                    "frequency_score": scores["frequency_score"],
                    "significance_multiplier": scores["significance_multiplier"],
                    "rating": scores["rating"],
                    "status": status,
                    "tested_at": now,
                })

            if len(batch) >= 200:
                save_batch(conn, batch)
                batch = []
                if not quiet:
                    rate = n_tested / max(time.time() - t0, 0.001)
                    print(f"  sprawdzonych {n_tested:,} | kandydatow {n_kept} | {rate:.0f}/s", flush=True)

    save_batch(conn, batch)

    finished = datetime.now(timezone.utc)
    conn.execute(
        """INSERT INTO search_runs (started_at, finished_at, n_tested, n_kept,
                                    n_total_seen, data_from, data_to, notes)
           VALUES (?,?,?,?,?,?,?,?)""",
        (started.isoformat(), finished.isoformat(), n_tested, n_kept,
         total_tested(conn), str(search_df.index.min().date()),
         str(search_df.index.max().date()),
         "singles_only" if singles_only else "full"),
    )
    conn.commit()

    if not quiet:
        elapsed = time.time() - t0
        print()
        print(f"Koniec. Sprawdzono {n_tested:,} nowych hipotez w {elapsed/60:.1f} min.")
        print(f"Kandydatow (rating >= 40): {n_kept}")
        print(f"Lacznie w bazie: {total_tested(conn):,} hipotez.")

    conn.close()
    return n_tested, n_kept


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=None, help="limit czasu w minutach")
    ap.add_argument("--singles-only", action="store_true", help="tylko strategie jednowarunkowe")
    ap.add_argument("--max", type=int, default=None, help="limit liczby hipotez")
    args = ap.parse_args()

    run_search(minutes=args.minutes, singles_only=args.singles_only, max_strategies=args.max)
