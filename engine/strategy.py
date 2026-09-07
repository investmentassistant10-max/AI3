"""
Strategia jako struktura danych, nie jako kod.

Dzięki temu silnik może ją zahaszować (żeby nie testować dwa razy tego
samego), mutować (szukać wokół obiecujących miejsc) i — co najważniejsze —
DOKŁADNIE POLICZYĆ, ile hipotez już sprawdził. Ta liczba jest potrzebna,
żeby uczciwie ocenić, czy najlepszy wynik to odkrycie, czy zbieg okoliczności.
"""
import hashlib
import json
import itertools

# Cechy, po których szukamy sygnałów, wraz z sensownymi progami.
# Progi dobrane tak, żeby siatka pokrywała i typowe, i skrajne stany rynku.
FEATURE_GRID = {
    "dist_sma_5":    [-4, -3, -2, -1.5, -1, 1, 1.5, 2, 3, 4],
    "dist_sma_10":   [-6, -4, -3, -2, -1.5, 1.5, 2, 3, 4, 6],
    "dist_sma_20":   [-8, -6, -5, -4, -3, -2, 2, 3, 4, 5, 6, 8],
    "dist_sma_50":   [-12, -9, -6, -4, -3, 3, 4, 6, 9, 12],
    "dist_sma_200":  [-20, -15, -10, -5, 5, 10, 15, 20],
    "z_sma_20":      [-2.5, -2, -1.5, -1, 1, 1.5, 2, 2.5],
    "rsi_14":        [20, 25, 30, 35, 65, 70, 75, 80],
    "streak":        [-5, -4, -3, -2, 2, 3, 4, 5],
    "ret_1":         [-3, -2, -1.5, -1, 1, 1.5, 2, 3],
    "gap":           [-2, -1.5, -1, -0.5, 0.5, 1, 1.5, 2],
    "range_pct":     [1.5, 2, 2.5, 3],
    "vol_regime":    [0.7, 0.85, 1.2, 1.5, 2.0],
    "vol_ratio":     [0.7, 0.85, 1.2, 1.5, 2.0],
    "dist_high_252": [-20, -15, -10, -5, -2, -0.5],
    "dist_low_252":  [2, 5, 10, 20, 30],
    "atr_14":        [0.8, 1.2, 1.8, 2.5],
}

OPERATORS = ("<", ">")
HORIZONS = (1, 2, 3, 5, 10)


def make_strategy(conditions, horizon):
    """
    conditions — lista krotek (feature, operator, threshold)
    horizon    — na ile dni w przód mierzymy skutek
    """
    canonical = {
        "conditions": sorted([list(c) for c in conditions], key=lambda c: (c[0], c[1], c[2])),
        "horizon": horizon,
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    strategy_id = hashlib.sha1(blob.encode()).hexdigest()[:16]
    return {"id": strategy_id, **canonical}


def describe(strategy):
    """Czytelny dla człowieka opis strategii."""
    parts = [f"{f} {op} {th}" for f, op, th in strategy["conditions"]]
    return " ORAZ ".join(parts) + f"  ->  {strategy['horizon']}d"


def build_mask(df, strategy):
    """Zamienia definicję strategii na maskę bool: które dni spełniają warunki."""
    mask = None
    for feature, op, threshold in strategy["conditions"]:
        col = df[feature]
        cond = col < threshold if op == "<" else col > threshold
        mask = cond if mask is None else (mask & cond)
    return mask.fillna(False)


def generate_single(horizons=HORIZONS):
    """Wszystkie strategie jednowarunkowe — podstawowa siatka."""
    for feature, thresholds in FEATURE_GRID.items():
        for threshold in thresholds:
            for op in OPERATORS:
                for h in horizons:
                    yield make_strategy([(feature, op, threshold)], h)


def generate_pairs(horizons=HORIZONS, max_per_feature=4):
    """
    Strategie dwuwarunkowe — kombinacje dwóch różnych cech.
    Ograniczamy progi na cechę, bo inaczej przestrzeń eksploduje, a im
    więcej testów, tym surowszy musi być próg akceptacji.
    """
    features = list(FEATURE_GRID.keys())
    for f1, f2 in itertools.combinations(features, 2):
        th1 = FEATURE_GRID[f1][::max(1, len(FEATURE_GRID[f1]) // max_per_feature)]
        th2 = FEATURE_GRID[f2][::max(1, len(FEATURE_GRID[f2]) // max_per_feature)]
        for t1, t2 in itertools.product(th1, th2):
            for op1, op2 in itertools.product(OPERATORS, OPERATORS):
                for h in horizons:
                    yield make_strategy([(f1, op1, t1), (f2, op2, t2)], h)


def count_space(pairs=True):
    """Ile hipotez jest w przestrzeni — potrzebne do korekty na liczbę prób."""
    n = sum(1 for _ in generate_single())
    if pairs:
        n += sum(1 for _ in generate_pairs())
    return n


if __name__ == "__main__":
    n_single = sum(1 for _ in generate_single())
    n_pairs = sum(1 for _ in generate_pairs())
    print(f"Strategie jednowarunkowe: {n_single:,}")
    print(f"Strategie dwuwarunkowe:   {n_pairs:,}")
    print(f"RAZEM w przestrzeni:      {n_single + n_pairs:,}")
    print()
    s = make_strategy([("dist_sma_20", "<", -5)], 3)
    print("Przyklad:", s["id"], "|", describe(s))
