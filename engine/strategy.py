"""
Strategia jako struktura danych.

Wersja 2 wprowadza podzial warunkow na dwie role, bo slepe mieszanie
wszystkiego ze wszystkim produkowalo sprzeczne hipotezy:

  SYGNAL   — co wlasnie sie stalo (spadek, wykupienie, seria wzrostow)
  KONTEKST — w jakim rynku to sie stalo (trend, zmiennosc, kalendarz)

Silnik odkryl sam, ze "po serii wzrostow przychodzi spadek" dziala inaczej
w hossie niz w bessie. Bez warunku kontekstowego te dwa przypadki laduja
w jednym worku i wzajemnie sie znosza.
"""
import hashlib
import itertools
import json
import random

# --- CO SIE STALO ---------------------------------------------------------
SIGNAL_GRID = {
    "dist_sma_5":     [-4, -3, -2, -1, 1, 2, 3, 4],
    "dist_sma_10":    [-6, -4, -2.5, -1.5, 1.5, 2.5, 4, 6],
    "dist_sma_20":    [-8, -5, -3, -2, 2, 3, 5, 8],
    "dist_sma_50":    [-12, -8, -4, -2, 2, 4, 8, 12],
    "z_sma_20":       [-2.5, -2, -1.5, -1, 1, 1.5, 2, 2.5],
    "rsi_14":         [20, 25, 30, 35, 65, 70, 75, 80],
    "streak":         [-5, -4, -3, -2, 2, 3, 4, 5],
    "ret_1":          [-3, -2, -1.5, -1, 1, 1.5, 2, 3],
    "ret_5":          [-6, -4, -2, 2, 4, 6],
    "ret_10":         [-8, -5, -2, 2, 5, 8],
    "gap":            [-1.5, -1, -0.5, 0.5, 1, 1.5],
    "close_position": [0.15, 0.3, 0.7, 0.85],
    "body_pct":       [20, 40, 60, 80],
    "upper_wick":     [30, 50],
    "lower_wick":     [30, 50],
    "mom_accel":      [-5, -3, -1, 1, 3, 5],
    "range_pct":      [1.0, 1.5, 2.0, 2.5],
    "drawdown":       [-20, -12, -7, -3, -1],
    "vol_ratio":      [0.7, 0.85, 1.2, 1.5],
}

# --- W JAKIM RYNKU --------------------------------------------------------
CONTEXT_GRID = {
    "above_sma_200":     [0.5],
    "regime_bull":       [0.5],
    "sma_slope_200":     [-1, 0, 1],
    "sma_slope_50":      [-2, 0, 2],
    "sma_spread_50_200": [-3, 0, 3],
    "vol_percentile":    [0.3, 0.5, 0.7, 0.9],
    "vol_regime":        [0.8, 1.0, 1.3],
    "atr_ratio":         [0.8, 1.0, 1.3],
    "dist_high_252":     [-15, -8, -3, -1],
    "vol_20":            [0.8, 1.2, 1.8],
    "day_of_week":       [0.5, 1.5, 2.5, 3.5],
    "days_to_month_end": [2.5, 5.5, 20.5],
    "month":             [3.5, 6.5, 9.5],
}

OPERATORS = ("<", ">")
HORIZONS = (1, 2, 3, 5, 10)


def _conditions(grid):
    for feature, thresholds in grid.items():
        for th in thresholds:
            for op in OPERATORS:
                yield (feature, op, th)


SIGNAL_CONDITIONS = list(_conditions(SIGNAL_GRID))
CONTEXT_CONDITIONS = list(_conditions(CONTEXT_GRID))
ALL_CONDITIONS = SIGNAL_CONDITIONS + CONTEXT_CONDITIONS


def make_strategy(conditions, horizon):
    canonical = {
        "conditions": sorted([list(c) for c in conditions], key=lambda c: (c[0], c[1], c[2])),
        "horizon": horizon,
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return {"id": hashlib.sha1(blob.encode()).hexdigest()[:16], **canonical}


def describe(strategy):
    parts = [f"{f} {op} {th}" for f, op, th in strategy["conditions"]]
    return " ORAZ ".join(parts) + f"  ->  {strategy['horizon']}d"


def build_mask(df, strategy):
    """Wolna sciezka (pandas) — uzywana przez validate.py."""
    mask = None
    for feature, op, threshold in strategy["conditions"]:
        col = df[feature]
        cond = col < threshold if op == "<" else col > threshold
        mask = cond if mask is None else (mask & cond)
    return mask.fillna(False)


# --- generatory przestrzeni ----------------------------------------------

def generate_level1(horizons=HORIZONS):
    """Sam sygnal, bez kontekstu."""
    for cond in SIGNAL_CONDITIONS:
        for h in horizons:
            yield make_strategy([cond], h)


def generate_level2(horizons=HORIZONS):
    """Sygnal + jeden warunek kontekstowy."""
    for sig in SIGNAL_CONDITIONS:
        for ctx in CONTEXT_CONDITIONS:
            for h in horizons:
                yield make_strategy([sig, ctx], h)


def generate_level3(horizons=HORIZONS, seed=None, limit=None):
    """
    Sygnal + dwa konteksty. Pelna siatka to dziesiatki milionow kombinacji,
    a kazda dolozona hipoteza podnosi poprzeczke istotnosci dla wszystkich
    pozostalych — wiec losujemy probke zamiast miec wszystko.
    """
    rng = random.Random(seed)
    ctx_pairs = list(itertools.combinations(range(len(CONTEXT_CONDITIONS)), 2))
    n = 0
    while True:
        sig = rng.choice(SIGNAL_CONDITIONS)
        i, j = rng.choice(ctx_pairs)
        c1, c2 = CONTEXT_CONDITIONS[i], CONTEXT_CONDITIONS[j]
        if c1[0] == c2[0]:
            continue  # ta sama cecha dwa razy — bez sensu
        h = rng.choice(horizons)
        yield make_strategy([sig, c1, c2], h)
        n += 1
        if limit and n >= limit:
            return


def space_sizes():
    n1 = len(SIGNAL_CONDITIONS) * len(HORIZONS)
    n2 = len(SIGNAL_CONDITIONS) * len(CONTEXT_CONDITIONS) * len(HORIZONS)
    pairs = len(CONTEXT_CONDITIONS) * (len(CONTEXT_CONDITIONS) - 1) // 2
    n3 = len(SIGNAL_CONDITIONS) * pairs * len(HORIZONS)
    return {"level1": n1, "level2": n2, "level3_full": n3}


if __name__ == "__main__":
    s = space_sizes()
    print(f"Warunkow sygnalowych:   {len(SIGNAL_CONDITIONS)}")
    print(f"Warunkow kontekstowych: {len(CONTEXT_CONDITIONS)}")
    print()
    print(f"Poziom 1 (sam sygnal):          {s['level1']:>12,}")
    print(f"Poziom 2 (sygnal + kontekst):   {s['level2']:>12,}")
    print(f"Poziom 3 (sygnal + 2 konteksty):{s['level3_full']:>12,}  (losujemy probke)")
    print(f"{'':<32}{'-'*12}")
    print(f"RAZEM systematycznie:           {s['level1']+s['level2']:>12,}")
