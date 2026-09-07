"""
Drugi etap: dla najlepszych warunkow wejscia szukamy najlepszej reguly wyjscia.

Wyniki ladują w osobnej tabeli, bo jedna strategia wejscia moze miec wiele
sensownych wyjsc — i to wlasnie porownanie jest tu ciekawe.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from features import build
from evaluate import split_search_treasury
from strategy import build_mask
from backtest import Bars, ENTRY_NEXT_OPEN
from exits import optimize, describe_exit

ROOT = Path(__file__).resolve().parent.parent
STRATEGY_DB = ROOT / "data" / "strategies.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS exit_rules (
    strategy_id   TEXT,
    rule          TEXT,
    rule_label    TEXT,
    entry         TEXT,
    n_trades      INTEGER,
    mean          REAL,
    base_mean     REAL,
    edge_mean     REAL,
    edge_hit      REAL,
    hit_rate      REAL,
    profit_factor REAL,
    worst         REAL,
    best          REAL,
    total         REAL,
    uncertainty   REAL,
    trustworthy   INTEGER,
    exit_reasons  TEXT,
    tested_at     TEXT,
    PRIMARY KEY (strategy_id, rule_label)
);
CREATE INDEX IF NOT EXISTS idx_exit_edge ON exit_rules(edge_mean DESC);
"""


def run(top=20, entry=ENTRY_NEXT_OPEN, quiet=False):
    df, _ = build()
    search_df, _ = split_search_treasury(df)
    bars = Bars(search_df, max_days=25)

    conn = sqlite3.connect(STRATEGY_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    rows = conn.execute(
        """SELECT id, definition, description, rating FROM strategies
           WHERE status='candidate' ORDER BY rating DESC LIMIT ?""", (top,)
    ).fetchall()

    if not rows:
        print("Brak kandydatow. Uruchom najpierw: python3 ia3.py search")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    saved = 0

    for r in rows:
        strat = json.loads(r["definition"])
        mask = build_mask(search_df, strat).to_numpy()
        idx = np.flatnonzero(mask)
        base_idx = np.flatnonzero(~mask)
        if len(idx) < 30:
            continue

        results = optimize(bars, idx, entry=entry, baseline_idx=base_idx)
        if not quiet:
            best = results[0] if results else None
            if best:
                print(f"  {r['description'][:58]:<58} najlepsze: {best['label']:<28} "
                      f"przewaga {best['edge_mean']:+.4f}%")

        for s in results:
            conn.execute(
                """INSERT OR REPLACE INTO exit_rules (
                    strategy_id, rule, rule_label, entry, n_trades, mean, base_mean,
                    edge_mean, edge_hit, hit_rate, profit_factor, worst, best, total,
                    uncertainty, trustworthy, exit_reasons, tested_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r["id"], json.dumps(s["rule"], separators=(",", ":")), s["label"],
                 entry, s["n"], s["mean"], s["base_mean"], s["edge_mean"], s["edge_hit"],
                 s["hit_rate"], s["profit_factor"], s["worst"], s["best"], s["total"],
                 s["uncertainty"], int(s["trustworthy"]),
                 json.dumps(s["exit_reasons"], separators=(",", ":")), now),
            )
            saved += 1

    conn.commit()
    conn.close()
    return saved
