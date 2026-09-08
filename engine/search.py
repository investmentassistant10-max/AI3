"""
Petla przeszukiwania — wersja na szybkiej sciezce numpy.

Zasady bez zmian:
- Szuka WYLACZNIE na danych sprzed skarbca.
- Kazda sprawdzona hipoteza laduje w bazie, takze odrzucona. Licznik prob
  jest czescia metody statystycznej: im wiecej testow, tym wyzej zawieszona
  poprzeczka dla kazdego pojedynczego wyniku.
- Da sie przerwac i wznowic.

Uzycie:
    python3 search.py                      # poziomy 1 i 2 (systematycznie)
    python3 search.py --hours 8            # + losowe probkowanie poziomu 3
    python3 search.py --level 1
"""
import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from features import build
from evaluate import split_search_treasury
from strategy import (
    generate_level1, generate_level2, generate_level3,
    describe, space_sizes, SIGNAL_CONDITIONS, CONTEXT_CONDITIONS,
)
from rating import compute_rating
import fastcore

ROOT = Path(__file__).resolve().parent.parent
STRATEGY_DB = ROOT / "data" / "strategies.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
    id              TEXT PRIMARY KEY,
    definition      TEXT NOT NULL,
    description     TEXT,
    horizon         INTEGER,
    n_conditions    INTEGER,
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
    placebo_p       REAL,
    tested_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_rating ON strategies(rating DESC);
CREATE INDEX IF NOT EXISTS idx_status ON strategies(status);

-- Odrzucone hipotezy trafiaja tutaj zamiast do `strategies`. Po nich
-- potrzebujemy tylko dwoch rzeczy: odcisku palca (zeby nie sprawdzac drugi
-- raz) i tego, ze proba zostala wykonana (do progu istotnosci). Pelny wiersz
-- w `strategies` kosztuje ~426 bajtow, ten ~30.
CREATE TABLE IF NOT EXISTS tested (
    id     TEXT PRIMARY KEY,
    status TEXT
);

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

CANDIDATE_THRESHOLD = 40.0


def total_trials(conn):
    """
    Wszystkie proby, jakie system wykonal — nie tylko warunki wejscia.
    Kazda przetestowana regula wyjscia to tez los na loterii i tez podnosi
    poprzeczke dla wszystkiego pozostalego.
    """
    n = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
    for table in ("tested", "exit_rules"):
        try:
            n += conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            pass
    return n


def all_seen_ids(conn):
    """Odciski palca wszystkich sprawdzonych hipotez — z obu tabel."""
    seen = {r[0] for r in conn.execute("SELECT id FROM strategies")}
    try:
        seen |= {r[0] for r in conn.execute("SELECT id FROM tested")}
    except sqlite3.OperationalError:
        pass
    return seen


def save_rejected(conn, rows):
    """Odrzucone hipotezy — tylko odcisk palca i powod."""
    if not rows:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO tested (id, status) VALUES (?, ?)",
        [(r["id"], r["status"]) for r in rows],
    )
    conn.commit()


# Kolumny dokladane do schematu juz po tym, jak bazy zaczely istniec.
# Trzymamy je tutaj, zeby kazdy modul otwierajacy baze dostawal ten sam
# komplet — wczesniej migracja byla tylko w silniku i predict.py wywracal
# sie na brakujacej kolumnie.
LATE_COLUMNS = (
    ("placebo_p", "REAL"),
    ("n_conditions", "INTEGER"),
    ("treasury_n_signals", "INTEGER"),
    ("treasury_edge_mean", "REAL"),
    ("treasury_edge_hit", "REAL"),
    ("treasury_hit_rate", "REAL"),
    ("treasury_status", "TEXT"),
    ("treasury_checked_at", "TEXT"),
)


def ensure_schema(conn):
    """Tworzy tabele i dokłada brakujace kolumny. Bezpieczne do wielokrotnego uzycia."""
    conn.executescript(SCHEMA)
    for col, typ in LATE_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE strategies ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass  # kolumna juz jest
    conn.commit()
    return conn


def get_db():
    """
    Polaczenie z baza w trybie WAL.

    WAL (write-ahead log) pozwala czytac baze w trakcie zapisu bez ryzyka
    zobaczenia niespojnego obrazu — czytelnicy pracuja na migawce, pisarz
    dopisuje obok. W trybie domyslnym (rollback journal) jednoczesny odczyt
    i zapis moga sie zderzyc, zwlaszcza gdy jeden z procesow siega do pliku
    przez sieciowy mount, gdzie blokady plikowe nie dzialaja niezawodnie.

    synchronous=FULL: po dwoch uszkodzeniach bazy w ciagu doby (za drugim
    razem zniknal caly naglowek pliku) wybieramy pewnosc zamiast szybkosci.
    Zapisy ida paczkami co kilka sekund, wiec koszt jest niewielki.
    """
    STRATEGY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STRATEGY_DB, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA busy_timeout=30000")
    return ensure_schema(conn)


def save_batch(conn, rows):
    if not rows:
        return
    conn.executemany(
        """INSERT OR REPLACE INTO strategies (
            id, definition, description, horizon, n_conditions,
            n_signals, n_episodes, frequency_pct,
            mean, base_mean, edge_mean, hit_rate, base_hit_rate, edge_hit,
            t_stat, p_value, accuracy_score, stability_score, frequency_score,
            significance_multiplier, rating, status, placebo_p, tested_at
        ) VALUES (
            :id, :definition, :description, :horizon, :n_conditions,
            :n_signals, :n_episodes, :frequency_pct,
            :mean, :base_mean, :edge_mean, :hit_rate, :base_hit_rate, :edge_hit,
            :t_stat, :p_value, :accuracy_score, :stability_score, :frequency_score,
            :significance_multiplier, :rating, :status, :placebo_p, :tested_at
        )""",
        rows,
    )
    conn.commit()


def run_search(hours=None, minutes=None, level=None, quiet=False):
    started = datetime.now(timezone.utc)
    limit_s = None
    if hours:
        limit_s = hours * 3600
    elif minutes:
        limit_s = minutes * 60
    deadline = time.time() + limit_s if limit_s else None

    df, problems = build()
    search_df, treasury_df = split_search_treasury(df)
    data = fastcore.FastData(search_df)

    if not quiet:
        print(f"Dane do szukania:     {search_df.index.min().date()} -> {search_df.index.max().date()} ({len(search_df)} dni)")
        print(f"Skarbiec (nietykany): {treasury_df.index.min().date()} -> {treasury_df.index.max().date()} ({len(treasury_df)} dni)")
        if problems:
            print(f"UWAGI: {problems}")

    # maski warunkow bazowych — liczone raz, uzywane miliony razy
    t_pre = time.time()
    masks = fastcore.precompute(data, SIGNAL_CONDITIONS + CONTEXT_CONDITIONS)
    if not quiet:
        sizes = space_sizes()
        print(f"Prekomputacja {len(masks)} masek warunkow: {time.time()-t_pre:.1f}s")
        print(f"Przestrzen: poziom1 {sizes['level1']:,} | poziom2 {sizes['level2']:,} | poziom3 {sizes['level3_full']:,}")
        print()

    conn = get_db()
    seen = {row[0] for row in conn.execute("SELECT id FROM strategies")}
    n_prior = len(seen)
    if not quiet and n_prior:
        print(f"Juz sprawdzonych we wczesniejszych przebiegach: {n_prior:,}\n")

    # Poziom 3 losuje z przestrzeni 3.2 mln kombinacji i sam z siebie nigdy
    # sie nie konczy. Bez limitu czasu musi dostac limit liczby hipotez,
    # inaczej `search` bez argumentow wisialby w nieskonczonosc.
    level3_limit = None if limit_s else 200_000

    if level == 1:
        gens = [("poziom 1", generate_level1())]
    elif level == 2:
        gens = [("poziom 2", generate_level2())]
    elif level == 3:
        gens = [("poziom 3", generate_level3(seed=int(time.time()), limit=level3_limit))]
    else:
        gens = [
            ("poziom 1", generate_level1()),
            ("poziom 2", generate_level2()),
            ("poziom 3", generate_level3(seed=int(time.time()), limit=level3_limit)),
        ]

    batch, n_tested, n_kept = [], 0, 0
    t0 = time.time()
    stop = False

    for label, gen in gens:
        if stop:
            break
        if not quiet:
            print(f"--- {label} ---", flush=True)
        for strat in gen:
            if deadline and time.time() > deadline:
                stop = True
                break
            if strat["id"] in seen:
                continue
            seen.add(strat["id"])
            n_tested += 1

            # skladanie maski z prekomputowanych kawalkow
            mask = None
            for cond in strat["conditions"]:
                m = masks.get(tuple(cond))
                if m is None:
                    m = fastcore.condition_mask(data, *cond)
                    masks[tuple(cond)] = m
                mask = m if mask is None else (mask & m)

            stats = fastcore.evaluate(data, mask, strat["horizon"])
            now = datetime.now(timezone.utc).isoformat()
            row = {
                "id": strat["id"],
                "definition": json.dumps(strat, separators=(",", ":")),
                "description": describe(strat),
                "horizon": strat["horizon"],
                "n_conditions": len(strat["conditions"]),
                "tested_at": now,
            }

            if stats.get("status") != "ok":
                row.update({
                    "placebo_p": None,
                    "n_signals": None, "n_episodes": stats.get("n_episodes"),
                    "frequency_pct": stats.get("frequency_pct"),
                    "mean": None, "base_mean": None, "edge_mean": None,
                    "hit_rate": None, "base_hit_rate": None, "edge_hit": None,
                    "t_stat": None, "p_value": None,
                    "accuracy_score": None, "stability_score": None,
                    "frequency_score": None, "significance_multiplier": None,
                    "rating": 0.0, "status": stats.get("status", "unevaluable"),
                })
            else:
                stab_score, _ = fastcore.stability(
                    data, mask, strat["horizon"], stats["edge_mean"]
                )
                scores = compute_rating(
                    stats, None, n_prior + n_tested
                )
                # stability liczymy szybka sciezka, wiec podmieniamy skladowa
                scores["stability_score"] = stab_score
                base = (
                    scores["accuracy_score"] * 0.45
                    + stab_score * 0.35
                    + scores["frequency_score"] * 0.20
                )
                scores["rating"] = round(base * scores["significance_multiplier"], 1)

                is_candidate = scores["rating"] >= CANDIDATE_THRESHOLD
                if is_candidate:
                    n_kept += 1
                row.update({
                    "placebo_p": None,
                    "n_signals": stats["n_signals"],
                    "n_episodes": stats["n_episodes"],
                    "frequency_pct": stats["frequency_pct"],
                    "mean": stats["mean"], "base_mean": stats["base_mean"],
                    "edge_mean": stats["edge_mean"],
                    "hit_rate": stats["hit_rate"],
                    "base_hit_rate": stats["base_hit_rate"],
                    "edge_hit": stats["edge_hit"],
                    "t_stat": stats["t_stat"], "p_value": stats["p_value"],
                    "accuracy_score": scores["accuracy_score"],
                    "stability_score": stab_score,
                    "frequency_score": scores["frequency_score"],
                    "significance_multiplier": scores["significance_multiplier"],
                    "rating": scores["rating"],
                    "status": "candidate" if is_candidate else "weak",
                })

            batch.append(row)
            if len(batch) >= 2000:
                save_batch(conn, batch)
                batch = []
                if not quiet:
                    el = time.time() - t0
                    print(f"  {n_tested:>10,} sprawdzonych | {n_kept:>5} kandydatow "
                          f"| {n_tested/el:>7,.0f}/s | {el/60:>5.1f} min", flush=True)

    save_batch(conn, batch)

    total = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
    conn.execute(
        """INSERT INTO search_runs (started_at, finished_at, n_tested, n_kept,
           n_total_seen, data_from, data_to, notes) VALUES (?,?,?,?,?,?,?,?)""",
        (started.isoformat(), datetime.now(timezone.utc).isoformat(), n_tested,
         n_kept, total, str(search_df.index.min().date()),
         str(search_df.index.max().date()), f"level={level or 'all'}"),
    )
    conn.commit()

    if not quiet:
        el = time.time() - t0
        print(f"\nKoniec. {n_tested:,} nowych hipotez w {el/60:.1f} min ({n_tested/el:,.0f}/s)")
        print(f"Kandydatow (rating >= {CANDIDATE_THRESHOLD:.0f}): {n_kept:,}")
        print(f"Lacznie w bazie: {total:,} hipotez")
    conn.close()
    return n_tested, n_kept


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=None)
    ap.add_argument("--minutes", type=float, default=None)
    ap.add_argument("--level", type=int, choices=[1, 2, 3], default=None)
    args = ap.parse_args()
    run_search(hours=args.hours, minutes=args.minutes, level=args.level)
