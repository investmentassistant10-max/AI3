"""
Empiryczna kalibracja progu istotnosci.

PROBLEM
Prog sqrt(2*ln N) mowi, jak wysoko siega najlepszy wynik z czystego szumu
przy N NIEZALEZNYCH probach. Nasze proby niezalezne nie sa: "dist_sma_20 > 2"
i "dist_sma_10 > 1.5" to praktycznie ta sama hipoteza. Efektywna liczba
niezaleznych testow jest wiec mniejsza niz liczba sprawdzonych hipotez —
a to znaczy, ze teoretyczny prog jest ZA SUROWY i odrzucamy rzeczy, ktore
moglyby byc prawdziwe.

ROZWIAZANIE
Zamiast zgadywac, mierzymy. Przesuwamy cyklicznie zwroty w przod wzgledem
cech — to niszczy kazdy prawdziwy zwiazek, zostawiajac dokladnie te sama
strukture danych, te same korelacje miedzy cechami i te sama liczbe hipotez.
Potem puszczamy na tym normalne przeszukiwanie i patrzymy, jak wysoko siega
najlepszy wynik. To JEST nasz prog — zmierzony, nie zalozony.

Uzycie:
    python3 calibrate.py                # 20 powtorzen na probce 6000 hipotez
    python3 calibrate.py --runs 40 --sample 12000
"""
import argparse
import json
import math
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
STRATEGY_DB = ROOT / "data" / "strategies.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS calibration (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at          TEXT,
    n_runs          INTEGER,
    sample_size     INTEGER,
    max_t_median    REAL,
    max_t_p95       REAL,
    max_t_mean      REAL,
    theoretical     REAL,
    effective_n     REAL,
    note            TEXT
);
"""


def shifted_data(search_df, shift):
    """Kopia danych z przesunietymi cyklicznie zwrotami w przod."""
    import fastcore
    df = search_df.copy()
    for col in df.columns:
        if col.startswith("fwd_"):
            df[col] = np.roll(df[col].to_numpy(), shift)
    return fastcore.FastData(df)


def run(n_runs=20, sample=6000, quiet=False):
    import fastcore
    from features import build
    from evaluate import split_search_treasury
    from strategy import (generate_level1, generate_level2, generate_level3,
                          SIGNAL_CONDITIONS, CONTEXT_CONDITIONS)

    df, _ = build()
    search_df, _ = split_search_treasury(df)

    # ta sama probka hipotez w kazdym powtorzeniu — porownujemy jablka z jablkami
    rng = random.Random(20260907)
    # UWAGA: generate_level3 bez `limit` jest nieskonczony — filtrowanie
    # po enumerate nie zatrzymuje iteracji, tylko odsiewa elementy.
    pool = list(generate_level1()) + list(generate_level2())
    pool += list(generate_level3(seed=7, limit=sample))
    strategies = rng.sample(pool, min(sample, len(pool)))

    if not quiet:
        print(f"Kalibracja: {n_runs} powtorzen po {len(strategies):,} hipotez")
        print("Kazde powtorzenie = te same hipotezy na danych bez prawdziwego zwiazku.\n")

    n_days = len(search_df)
    max_ts = []
    shift_rng = np.random.default_rng(4242)

    for run_i in range(n_runs):
        shift = int(shift_rng.integers(30, n_days - 30))
        data = shifted_data(search_df, shift)
        masks = fastcore.precompute(data, SIGNAL_CONDITIONS + CONTEXT_CONDITIONS)

        best = 0.0
        for strat in strategies:
            mask = None
            for cond in strat["conditions"]:
                key = tuple(cond)
                m = masks.get(key)
                if m is None:
                    m = fastcore.condition_mask(data, *key)
                    masks[key] = m
                mask = m if mask is None else (mask & m)
            r = fastcore.evaluate(data, mask, strat["horizon"])
            if r.get("status") == "ok" and r["t_stat"] is not None:
                best = max(best, abs(r["t_stat"]))

        max_ts.append(best)
        if not quiet:
            print(f"  powtorzenie {run_i+1:>2}/{n_runs}: najwyzsze |t| na szumie = {best:.2f}",
                  flush=True)

    max_ts = np.array(max_ts)
    theoretical = math.sqrt(2 * math.log(len(strategies)))
    median = float(np.median(max_ts))
    p95 = float(np.percentile(max_ts, 95))
    # ile niezaleznych testow odpowiadaloby zmierzonemu progowi
    effective_n = math.exp(median ** 2 / 2)

    conn = sqlite3.connect(STRATEGY_DB)
    conn.executescript(SCHEMA)
    conn.execute(
        """INSERT INTO calibration (run_at, n_runs, sample_size, max_t_median,
           max_t_p95, max_t_mean, theoretical, effective_n, note)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (datetime.now(timezone.utc).isoformat(), n_runs, len(strategies),
         round(median, 3), round(p95, 3), round(float(max_ts.mean()), 3),
         round(theoretical, 3), round(effective_n, 1),
         "przesuniecie cykliczne zwrotow w przod"),
    )
    conn.commit()
    conn.close()

    if not quiet:
        print()
        print("=" * 74)
        print("WYNIK KALIBRACJI")
        print("=" * 74)
        print(f"  Hipotez w probce:                {len(strategies):,}")
        print(f"  Prog teoretyczny sqrt(2 ln N):   |t| > {theoretical:.2f}")
        print(f"  Prog ZMIERZONY (mediana):        |t| > {median:.2f}")
        print(f"  Prog ostrozny (95 percentyl):    |t| > {p95:.2f}")
        print(f"  Rozrzut miedzy powtorzeniami:    {max_ts.min():.2f} - {max_ts.max():.2f}")
        print()
        print(f"  Efektywna liczba niezaleznych testow: ~{effective_n:,.0f}")
        print(f"  czyli {len(strategies)/effective_n:.1f}x mniej niz sprawdzonych hipotez")
        print()
        if median < theoretical:
            print(f"  Prog teoretyczny byl ZA SUROWY o {theoretical - median:.2f}.")
            print(f"  Hipotezy z |t| miedzy {median:.2f} a {theoretical:.2f} byly")
            print(f"  odrzucane niepotrzebnie.")
        else:
            print(f"  Prog teoretyczny okazal sie zbyt lagodny — korelacje miedzy")
            print(f"  cechami nie pomagaja tak, jak zakladalismy.")
    return {"median": median, "p95": p95, "theoretical": theoretical,
            "effective_n": effective_n}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--sample", type=int, default=6000)
    args = ap.parse_args()
    run(args.runs, args.sample)
