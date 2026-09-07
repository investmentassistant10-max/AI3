"""
Silnik oceny hipotez.

Prymityw: dostajesz maskę (które dni spełniają warunek) i mówisz, co się
działo potem — ale ZAWSZE w porównaniu do dnia bez sygnału w tym samym
okresie. Bez tego porównania odkrywasz beta rynku i nazywasz to strategią.

Druga rzecz, którą to liczy uczciwie: nakładające się okna. Jeśli sygnał
trwa 5 dni z rzędu, a mierzysz skutek na 5 dni w przód, to nie jest pięć
niezależnych obserwacji, tylko jedna. Statystyki idą po EPIZODACH, nie po
dniach — dlatego wychodzą skromniejsze niż w naiwnym backteście, i o to
chodzi.
"""
import math

import numpy as np
import pandas as pd

# Ostatnie dwa lata trzymamy z dala od szukania — to skarbiec,
# służy wyłącznie do potwierdzania gotowych hipotez.
TREASURY_YEARS = 2

# Progi, poniżej/powyżej których hipotezy nie da się uczciwie ocenić.
# Sygnał obejmujący 98% dni nie jest sygnałem, tylko stanem domyślnym rynku —
# a "baseline", z którym go porównujemy, to wtedy garstka skrajnych dni i
# porównanie przestaje cokolwiek znaczyć (produkuje absurdalne t-staty).
MIN_FREQUENCY_PCT = 0.3    # rzadziej = za mało obserwacji, żeby cokolwiek stwierdzić
MAX_FREQUENCY_PCT = 35.0   # częściej = to nie sygnał, tylko opis normalnego dnia
MIN_EPISODES = 20          # niezależnych okazji, nie dni
MIN_BASELINE_DAYS = 200    # baseline musi mieć z czego liczyć średnią


def split_search_treasury(df, years=TREASURY_YEARS):
    """Dzieli dane na część do szukania i skarbiec (ostatnie N lat)."""
    cutoff = df.index.max() - pd.DateOffset(years=years)
    return df[df.index <= cutoff], df[df.index > cutoff]


def count_episodes(mask, min_gap):
    """
    Liczy odrębne epizody sygnału. Dni sygnału oddalone o mniej niż
    min_gap sesji traktujemy jako jeden epizod — bo ich okna wyników
    się nakładają i nie niosą niezależnej informacji.
    """
    idx = np.flatnonzero(mask.to_numpy())
    if len(idx) == 0:
        return 0
    return 1 + int(np.sum(np.diff(idx) >= min_gap))


def _normal_two_sided_p(t):
    """p-wartość dwustronna z przybliżenia normalnego (bez scipy)."""
    if not np.isfinite(t):
        return float("nan")
    return math.erfc(abs(t) / math.sqrt(2.0))


def evaluate(df, mask, horizon):
    """
    Ocenia jedną hipotezę na jednym horyzoncie.

    df      — ramka z cechami i kolumnami fwd_*
    mask    — bool Series: które dni spełniają warunek
    horizon — na ile dni w przód mierzymy skutek

    Zwraca słownik statystyk albo None, jeśli sygnał jest zbyt rzadki.
    """
    col = f"fwd_{horizon}"
    if col not in df.columns:
        raise ValueError(f"brak kolumny {col}")

    valid = df[col].notna()
    sig = mask & valid
    base = (~mask) & valid

    n_valid = int(valid.sum())
    n_signals = int(sig.sum())
    n_base = int(base.sum())
    frequency = n_signals / n_valid * 100.0 if n_valid else 0.0

    if frequency < MIN_FREQUENCY_PCT:
        return {"status": "too_rare", "frequency_pct": round(frequency, 3)}
    if frequency > MAX_FREQUENCY_PCT:
        return {"status": "too_common", "frequency_pct": round(frequency, 3)}
    if n_base < MIN_BASELINE_DAYS:
        return {"status": "baseline_too_small", "frequency_pct": round(frequency, 3)}

    n_episodes = count_episodes(sig, min_gap=horizon)
    if n_episodes < MIN_EPISODES:
        return {"status": "too_few_episodes", "frequency_pct": round(frequency, 3),
                "n_episodes": n_episodes}

    sig_ret = df.loc[sig, col]
    base_ret = df.loc[base, col]

    mean = float(sig_ret.mean())
    base_mean = float(base_ret.mean())
    hit = float((sig_ret > 0).mean() * 100.0)
    base_hit = float((base_ret > 0).mean() * 100.0)

    # Błąd standardowy liczony na EPIZODACH, nie na dniach — konserwatywnie.
    std = float(sig_ret.std(ddof=1))
    se = std / math.sqrt(n_episodes) if n_episodes > 1 else float("nan")
    t_stat = (mean - base_mean) / se if se and se > 0 else float("nan")

    return {
        "status": "ok",
        "horizon": horizon,
        "n_signals": n_signals,
        "n_episodes": n_episodes,
        "frequency_pct": round(frequency, 2),
        "mean": round(mean, 4),
        "median": round(float(sig_ret.median()), 4),
        "base_mean": round(base_mean, 4),
        "edge_mean": round(mean - base_mean, 4),
        "hit_rate": round(hit, 2),
        "base_hit_rate": round(base_hit, 2),
        "edge_hit": round(hit - base_hit, 2),
        "t_stat": round(t_stat, 3) if np.isfinite(t_stat) else None,
        "p_value": round(_normal_two_sided_p(t_stat), 5) if np.isfinite(t_stat) else None,
    }


def evaluate_all_horizons(df, mask, horizons=(1, 2, 3, 5, 10)):
    """Ocenia hipotezę na wszystkich horyzontach naraz."""
    results = []
    for h in horizons:
        r = evaluate(df, mask, h)
        if r and r.get("status") == "ok":
            results.append(r)
    return results


def stability_by_period(df, mask, horizon, n_periods=4):
    """
    Sprawdza, czy efekt trzyma się w różnych okresach historii.
    Efekt obecny tylko w jednym wycinku to zwykle przypadek, nie reguła.
    """
    chunks = np.array_split(np.arange(len(df)), n_periods)
    out = []
    for chunk in chunks:
        sub = df.iloc[chunk]
        sub_mask = mask.iloc[chunk]
        r = evaluate(sub, sub_mask, horizon)
        ok = r is not None and r.get("status") == "ok"
        out.append(
            {
                "from": sub.index.min().strftime("%Y-%m"),
                "to": sub.index.max().strftime("%Y-%m"),
                "edge_mean": r["edge_mean"] if ok else None,
                "edge_hit": r["edge_hit"] if ok else None,
                "n_episodes": r["n_episodes"] if ok else 0,
            }
        )
    return out
