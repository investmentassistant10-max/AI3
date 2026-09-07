"""
Silnik ciagly: uruchamiasz i zostawiasz. Pracuje, dopoki go nie zatrzymasz.

    python3 run_forever.py

Zatrzymanie: Ctrl+C. Silnik dokonczy biezaca paczke, zapisze stan i wyjdzie
czysto — nic sie nie gubi, nastepne uruchomienie podejmie prace tam, gdzie
skonczyl.

CO ROBI, W KOLEJNOSCI
  1. systematyka  — przemiata siatke warunkow (raz, potem juz jej nie wraca)
  2. eksploracja  — losowe probki z przestrzeni trzywarunkowej
  3. ewolucja     — mutacje najlepszych znalezisk; to tu spedza wiekszosc
                    czasu w dlugim biegu i tu przynosi najwiecej
  4. wyjscia      — dobiera SL/TP/czas dla nowych kandydatow
  5. wypchniecie  — najlepsze strategie do Firestore

Potem wraca do 2 i tak w kolko. Co cykl sprawdza tez, czy w Firestore
pojawila sie nowa sesja — jesli tak, dociaga ja i przelicza cechy.

DLACZEGO IM DLUZEJ PRACUJE, TYM SUROWIEJ OCENIA
Kazda sprawdzona hipoteza to kolejny los na loterii. Przy 50 tysiacach prob
najlepszy wynik z czystego szumu ma t-stat okolo 4.6, przy milionie — 5.3.
Silnik liczy wszystkie swoje proby i podnosi prog istotnosci wraz z ich
liczba. Dlatego strategia, ktora wczoraj byla kandydatem, dzis moze nim juz
nie byc — nie dlatego, ze sie zepsula, tylko dlatego, ze sprawdzilismy
tymczasem tysiac innych i wiemy, ze taki wynik zdarza sie przypadkiem.
"""
import argparse
import json
import math
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
STRATEGY_DB = ROOT / "data" / "strategies.sqlite"
LOG_PATH = ROOT / "data" / "engine.log"

# --- ustawienia cyklu ---
EXPLORE_BATCH = 40_000      # ile losowych hipotez na jedna runde eksploracji
EVOLVE_BATCH = 40_000       # ile mutacji na jedna runde ewolucji
PARENTS_POOL = 120          # z ilu najlepszych bierzemy rodzicow
EXITS_EVERY_CYCLES = 3      # co ile cykli dobieramy reguly wyjscia
PUSH_EVERY_CYCLES = 3       # co ile cykli wypychamy do Firestore
SYNC_EVERY_CYCLES = 12      # co ile cykli sprawdzamy nowe dane


class Stopper:
    """Lapie Ctrl+C, zeby silnik konczyl czysto zamiast ginac w polowie."""

    def __init__(self):
        self.stop = False
        signal.signal(signal.SIGINT, self._handle)
        signal.signal(signal.SIGTERM, self._handle)

    def _handle(self, *_):
        if self.stop:
            print("\n  (drugie przerwanie — wychodze natychmiast)")
            sys.exit(1)
        self.stop = True
        print("\n  Zatrzymuje sie po biezacej paczce. Ctrl+C jeszcze raz = natychmiast.")


def log(msg, to_file=True):
    stamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    if to_file:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} {msg}\n")


def db_stats(conn):
    total = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
    cand = conn.execute(
        "SELECT COUNT(*) FROM strategies WHERE status='candidate'"
    ).fetchone()[0]
    best = conn.execute(
        "SELECT MAX(rating) FROM strategies WHERE status='candidate'"
    ).fetchone()[0]
    max_t = conn.execute("SELECT MAX(ABS(t_stat)) FROM strategies").fetchone()[0]
    threshold = math.sqrt(2 * math.log(total)) if total > 1 else 2.0
    above = conn.execute(
        "SELECT COUNT(*) FROM strategies WHERE ABS(t_stat) > ?", (threshold,)
    ).fetchone()[0]
    return {
        "total": total, "candidates": cand, "best_rating": best or 0.0,
        "max_t": max_t or 0.0, "threshold": threshold, "above_threshold": above,
    }


def print_status(conn, cycle, elapsed):
    s = db_stats(conn)
    log(
        f"cykl {cycle} | {s['total']:,} hipotez | {s['candidates']:,} kandydatow | "
        f"prog |t|>{s['threshold']:.2f} | ponad progiem: {s['above_threshold']} | "
        f"najlepszy rating {s['best_rating']:.1f} | {elapsed/3600:.1f}h pracy"
    )


def load_parents(conn, limit=PARENTS_POOL):
    rows = conn.execute(
        """SELECT definition FROM strategies WHERE status='candidate'
           ORDER BY rating DESC LIMIT ?""", (limit,)
    ).fetchall()
    return [json.loads(r[0]) for r in rows]


def evaluate_batch(strategies, data, masks, conn, seen, n_prior, quiet=True):
    """Ocenia paczke strategii i zapisuje wyniki. Zwraca (ile, ilu kandydatow)."""
    import fastcore
    from strategy import describe
    from rating import compute_rating
    from search import save_batch, CANDIDATE_THRESHOLD

    rows, n_tested, n_kept = [], 0, 0
    now = datetime.now(timezone.utc).isoformat()

    for strat in strategies:
        if strat["id"] in seen:
            continue
        seen.add(strat["id"])
        n_tested += 1

        mask = None
        for cond in strat["conditions"]:
            key = tuple(cond)
            m = masks.get(key)
            if m is None:
                m = fastcore.condition_mask(data, *key)
                masks[key] = m
            mask = m if mask is None else (mask & m)

        stats = fastcore.evaluate(data, mask, strat["horizon"])
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
                "n_signals": None, "n_episodes": stats.get("n_episodes"),
                "frequency_pct": stats.get("frequency_pct"),
                "mean": None, "base_mean": None, "edge_mean": None,
                "hit_rate": None, "base_hit_rate": None, "edge_hit": None,
                "t_stat": None, "p_value": None, "accuracy_score": None,
                "stability_score": None, "frequency_score": None,
                "significance_multiplier": None, "rating": 0.0,
                "status": stats.get("status", "unevaluable"),
            })
        else:
            stab, _ = fastcore.stability(data, mask, strat["horizon"], stats["edge_mean"])
            scores = compute_rating(stats, None, n_prior + n_tested)
            base = (scores["accuracy_score"] * 0.45 + stab * 0.35
                    + scores["frequency_score"] * 0.20)
            rating = round(base * scores["significance_multiplier"], 1)
            if rating >= CANDIDATE_THRESHOLD:
                n_kept += 1
            row.update({
                "n_signals": stats["n_signals"], "n_episodes": stats["n_episodes"],
                "frequency_pct": stats["frequency_pct"], "mean": stats["mean"],
                "base_mean": stats["base_mean"], "edge_mean": stats["edge_mean"],
                "hit_rate": stats["hit_rate"], "base_hit_rate": stats["base_hit_rate"],
                "edge_hit": stats["edge_hit"], "t_stat": stats["t_stat"],
                "p_value": stats["p_value"],
                "accuracy_score": scores["accuracy_score"],
                "stability_score": stab,
                "frequency_score": scores["frequency_score"],
                "significance_multiplier": scores["significance_multiplier"],
                "rating": rating,
                "status": "candidate" if rating >= CANDIDATE_THRESHOLD else "weak",
            })

        rows.append(row)
        if len(rows) >= 2000:
            save_batch(conn, rows)
            rows = []

    save_batch(conn, rows)
    return n_tested, n_kept


def main():
    ap = argparse.ArgumentParser(description="IA3 — silnik ciagly")
    ap.add_argument("--max-hours", type=float, default=None,
                    help="zatrzymaj sie po tylu godzinach (domyslnie: bez konca)")
    ap.add_argument("--no-push", action="store_true",
                    help="nie wysylaj wynikow do Firestore")
    ap.add_argument("--no-sync", action="store_true",
                    help="nie sprawdzaj nowych danych w Firestore")
    args = ap.parse_args()

    from features import build
    from evaluate import split_search_treasury
    from strategy import (generate_level1, generate_level2, generate_level3,
                          SIGNAL_CONDITIONS, CONTEXT_CONDITIONS)
    from search import get_db
    from mutate import evolve
    import fastcore

    stopper = Stopper()
    t_start = time.time()
    deadline = t_start + args.max_hours * 3600 if args.max_hours else None

    log("=" * 74, to_file=False)
    log("IA3 — silnik ciagly. Ctrl+C konczy czysto.")
    log("=" * 74, to_file=False)

    df, problems = build()
    search_df, treasury_df = split_search_treasury(df)
    data = fastcore.FastData(search_df)
    masks = fastcore.precompute(data, SIGNAL_CONDITIONS + CONTEXT_CONDITIONS)

    log(f"Dane: {search_df.index.min().date()} -> {search_df.index.max().date()} "
        f"({len(search_df)} sesji do szukania, {len(treasury_df)} w skarbcu)")
    if problems:
        log(f"UWAGI DO DANYCH: {problems}")

    conn = get_db()
    seen = {r[0] for r in conn.execute("SELECT id FROM strategies")}
    log(f"W bazie juz: {len(seen):,} sprawdzonych hipotez")
    s = db_stats(conn)
    log(f"Prog istotnosci na start: |t| > {s['threshold']:.2f}")

    cycle = 0
    systematic_done = False

    while not stopper.stop:
        if deadline and time.time() > deadline:
            log("Osiagnieto limit czasu.")
            break
        cycle += 1

        # --- 1. systematyka (tylko raz) ---
        if not systematic_done:
            log("faza: systematyka (poziomy 1 i 2)")
            gen = list(generate_level1()) + list(generate_level2())
            n, kept = evaluate_batch(gen, data, masks, conn, seen, len(seen))
            log(f"  sprawdzonych {n:,}, nowych kandydatow {kept}")
            systematic_done = True
            if stopper.stop:
                break

        # --- 2. eksploracja ---
        log("faza: eksploracja (losowe probki, 3 warunki)")
        gen = generate_level3(seed=int(time.time()), limit=EXPLORE_BATCH)
        n, kept = evaluate_batch(list(gen), data, masks, conn, seen, len(seen))
        log(f"  sprawdzonych {n:,}, nowych kandydatow {kept}")
        if stopper.stop:
            break

        # --- 3. ewolucja ---
        parents = load_parents(conn)
        if parents:
            log(f"faza: ewolucja ({len(parents)} rodzicow)")
            children = list(evolve(parents, EVOLVE_BATCH, seed=int(time.time())))
            n, kept = evaluate_batch(children, data, masks, conn, seen, len(seen))
            log(f"  sprawdzonych {n:,}, nowych kandydatow {kept}")
        if stopper.stop:
            break

        # --- 4. reguly wyjscia ---
        if cycle % EXITS_EVERY_CYCLES == 0:
            log("faza: dobor regul wyjscia")
            try:
                import exit_search
                saved = exit_search.run(top=25, quiet=True)
                log(f"  zapisano {saved} kombinacji wejscie-wyjscie")
            except Exception as e:
                log(f"  blad: {e}")

        # --- 5. wypchniecie ---
        if not args.no_push and cycle % PUSH_EVERY_CYCLES == 0:
            log("faza: wysylka do Firestore")
            try:
                import push_strategies
                push_strategies.push(100)
                import daily_snapshot
                daily_snapshot.push(daily_snapshot.build_snapshot())
                log("  migawka na dzis wyslana")
            except Exception as e:
                log(f"  nie udalo sie wyslac ({e}) — wyniki zostaja lokalnie")

        # --- 6. nowe dane ---
        if not args.no_sync and cycle % SYNC_EVERY_CYCLES == 0:
            log("faza: sprawdzam nowe sesje")
            try:
                import importlib
                import sync_prices
                importlib.reload(sync_prices)
                sync_prices.main()
                df, _ = build()
                search_df, treasury_df = split_search_treasury(df)
                data = fastcore.FastData(search_df)
                masks = fastcore.precompute(data, SIGNAL_CONDITIONS + CONTEXT_CONDITIONS)
                log(f"  dane przeladowane do {search_df.index.max().date()}")
            except Exception as e:
                log(f"  pominieto ({e})")

        print_status(conn, cycle, time.time() - t_start)

    # --- zamkniecie ---
    elapsed = time.time() - t_start
    s = db_stats(conn)
    log("=" * 74, to_file=False)
    log(f"Zatrzymany po {elapsed/3600:.2f}h, {cycle} cyklach.")
    log(f"Lacznie w bazie: {s['total']:,} hipotez | kandydatow: {s['candidates']:,}")
    log(f"Prog istotnosci: |t| > {s['threshold']:.2f} | ponad progiem: {s['above_threshold']}")
    log(f"Najwyzsze |t|: {s['max_t']:.2f} | najlepszy rating: {s['best_rating']:.1f}")
    log("Podsumowanie: python3 ia3.py report")
    conn.close()


if __name__ == "__main__":
    main()
