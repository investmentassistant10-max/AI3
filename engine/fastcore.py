"""
Szybka sciezka silnika: to samo, co evaluate.py, ale na czystym numpy.

Dwie sztuczki daja tu wiekszosc zysku:
1. Maski warunkow bazowych (np. "rsi_14 < 35") liczymy RAZ i trzymamy jako
   tablice bool. Strategia zlozona z trzech warunkow to potem trzy operacje
   AND na bitach zamiast trzech porownan na kolumnach DataFrame.
2. Zadnego indeksowania pandas w petli — tylko numpy.

Semantyka identyczna jak w evaluate.py: porownanie do baseline, statystyki
liczone po epizodach, te same progi odsiewajace.
"""
import math

import numpy as np

from evaluate import (
    MIN_FREQUENCY_PCT,
    MAX_FREQUENCY_PCT,
    MIN_EPISODES,
    MIN_BASELINE_DAYS,
)

N_PERIODS = 4  # na ile czesci dzielimy historie przy ocenie powtarzalnosci


class FastData:
    """Dane w formie gotowej do szybkiego liczenia."""

    def __init__(self, df, horizons=(1, 2, 3, 5, 10)):
        self.n = len(df)
        self.horizons = horizons
        self.fwd = {}
        self.fwd_valid = {}
        for h in horizons:
            arr = df[f"fwd_{h}"].to_numpy(dtype=np.float64)
            self.fwd[h] = arr
            self.fwd_valid[h] = ~np.isnan(arr)
        self.columns = {
            c: df[c].to_numpy(dtype=np.float64)
            for c in df.columns
            if not c.startswith("fwd_")
        }
        self.period_slices = [
            (int(chunk[0]), int(chunk[-1]) + 1)
            for chunk in np.array_split(np.arange(self.n), N_PERIODS)
        ]


def condition_mask(data, feature, op, threshold):
    """Maska pojedynczego warunku. NaN nigdy nie spelnia warunku."""
    col = data.columns[feature]
    with np.errstate(invalid="ignore"):
        m = col < threshold if op == "<" else col > threshold
    return m & ~np.isnan(col)


def precompute(data, conditions):
    """Liczy raz maski wszystkich warunkow bazowych."""
    return {c: condition_mask(data, *c) for c in conditions}


def count_episodes(mask, min_gap):
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return 0
    return 1 + int(np.count_nonzero(np.diff(idx) >= min_gap))


def _p_value(t):
    if not np.isfinite(t):
        return None
    return math.erfc(abs(t) / math.sqrt(2.0))


def evaluate(data, mask, horizon, lo=None, hi=None):
    """
    Ocena jednej hipotezy. lo/hi pozwalaja policzyc to samo na wycinku
    historii (do oceny powtarzalnosci) bez kopiowania danych.
    """
    valid = data.fwd_valid[horizon]
    fwd = data.fwd[horizon]

    if lo is not None:
        window = np.zeros(data.n, dtype=bool)
        window[lo:hi] = True
        valid = valid & window

    sig = mask & valid
    n_valid = int(np.count_nonzero(valid))
    if n_valid == 0:
        return {"status": "empty"}

    n_signals = int(np.count_nonzero(sig))
    frequency = n_signals / n_valid * 100.0

    if frequency < MIN_FREQUENCY_PCT:
        return {"status": "too_rare", "frequency_pct": round(frequency, 3)}
    if frequency > MAX_FREQUENCY_PCT:
        return {"status": "too_common", "frequency_pct": round(frequency, 3)}

    base = (~mask) & valid
    n_base = int(np.count_nonzero(base))
    if n_base < MIN_BASELINE_DAYS:
        return {"status": "baseline_too_small", "frequency_pct": round(frequency, 3)}

    n_episodes = count_episodes(sig, min_gap=horizon)
    if n_episodes < MIN_EPISODES:
        return {"status": "too_few_episodes", "frequency_pct": round(frequency, 3),
                "n_episodes": n_episodes}

    sig_ret = fwd[sig]
    base_ret = fwd[base]

    mean = float(sig_ret.mean())
    base_mean = float(base_ret.mean())
    hit = float(np.count_nonzero(sig_ret > 0) / sig_ret.size * 100.0)
    base_hit = float(np.count_nonzero(base_ret > 0) / base_ret.size * 100.0)

    std = float(sig_ret.std(ddof=1))
    se = std / math.sqrt(n_episodes) if n_episodes > 1 and std > 0 else float("nan")
    t_stat = (mean - base_mean) / se if np.isfinite(se) and se > 0 else float("nan")

    return {
        "status": "ok",
        "horizon": horizon,
        "n_signals": n_signals,
        "n_episodes": n_episodes,
        "frequency_pct": round(frequency, 2),
        "mean": round(mean, 4),
        "median": round(float(np.median(sig_ret)), 4),
        "base_mean": round(base_mean, 4),
        "edge_mean": round(mean - base_mean, 4),
        "hit_rate": round(hit, 2),
        "base_hit_rate": round(base_hit, 2),
        "edge_hit": round(hit - base_hit, 2),
        "t_stat": round(t_stat, 3) if np.isfinite(t_stat) else None,
        "p_value": round(_p_value(t_stat), 6) if np.isfinite(t_stat) else None,
    }


def stability(data, mask, horizon, overall_edge):
    """
    Powtarzalnosc: w ilu podokresach przewaga ma ten sam znak co calosc.
    Zwraca (score 0-100, lista szczegolow).
    """
    if overall_edge is None or overall_edge == 0:
        return 0.0, []

    details = []
    usable = 0
    same_sign = 0

    for lo, hi in data.period_slices:
        r = evaluate(data, mask, horizon, lo, hi)
        ok = r.get("status") == "ok"
        edge = r["edge_mean"] if ok else None
        details.append({"edge_mean": edge, "n_episodes": r.get("n_episodes", 0)})
        if ok and r["n_episodes"] >= 5:
            usable += 1
            if edge * overall_edge > 0:
                same_sign += 1

    if usable < 2:
        return 0.0, details

    ratio = same_sign / usable
    coverage = usable / len(data.period_slices)
    return round(ratio * coverage * 100.0, 1), details
