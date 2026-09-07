"""
Optymalizacja regul wyjscia dla znalezionych warunkow wejscia.

Dwuetapowosc jest celowa: najpierw szukamy KIEDY wchodzic (search.py, tanio,
na zwrotach o stalym horyzoncie), potem dla najlepszych warunkow szukamy JAK
wychodzic. Testowanie wszystkich kombinacji warunek x wyjscie od razu
podnioslby liczbe prob o dwa rzedy wielkosci, a wiec i poprzeczke istotnosci
dla wszystkiego.

Kazdy wariant dostaje tez miare NIEPEWNOSCI: roznice miedzy wynikiem przy
pesymistycznym i optymistycznym zalozeniu o kolejnosci wewnatrz dnia. Duza
niepewnosc znaczy, ze wynik jest w duzej mierze wymyslony przez backtest, bo
dane dzienne nie wystarczaja, zeby go rozstrzygnac.
"""
import numpy as np

from backtest import (
    simulate, summarize, Bars,
    ENTRY_NEXT_OPEN, ENTRY_CLOSE,
    ASSUME_PESSIMISTIC, ASSUME_OPTIMISTIC,
)

# Siatka regul wyjscia. Celowo brak bardzo ciasnych par SL+TP —
# na danych dziennych ich wynik zalezy glownie od zalozenia, nie od rynku.
EXIT_GRID = [
    # samo wyjscie po czasie
    {"type": "time", "max_days": 1},
    {"type": "time", "max_days": 2},
    {"type": "time", "max_days": 3},
    {"type": "time", "max_days": 5},
    {"type": "time", "max_days": 10},
    # sam stop loss + czas (brak remisu = brak niepewnosci)
    {"type": "sl", "sl": 0.75, "max_days": 3},
    {"type": "sl", "sl": 1.0, "max_days": 3},
    {"type": "sl", "sl": 1.5, "max_days": 5},
    {"type": "sl", "sl": 2.0, "max_days": 5},
    {"type": "sl", "sl": 2.0, "max_days": 10},
    {"type": "sl", "sl": 3.0, "max_days": 10},
    # sam take profit + czas
    {"type": "tp", "tp": 1.0, "max_days": 3},
    {"type": "tp", "tp": 1.5, "max_days": 5},
    {"type": "tp", "tp": 2.0, "max_days": 5},
    {"type": "tp", "tp": 3.0, "max_days": 10},
    # oba, ale tylko szerokie — waskie sa nierozstrzygalne na danych dziennych
    {"type": "sl_tp", "sl": 1.5, "tp": 3.0, "max_days": 5},
    {"type": "sl_tp", "sl": 2.0, "tp": 2.0, "max_days": 5},
    {"type": "sl_tp", "sl": 2.0, "tp": 4.0, "max_days": 10},
    {"type": "sl_tp", "sl": 3.0, "tp": 3.0, "max_days": 10},
    {"type": "sl_tp", "sl": 3.0, "tp": 6.0, "max_days": 20},
]

# Powyzej tej roznicy miedzy zalozeniem pesymistycznym a optymistycznym
# uznajemy wynik za nierozstrzygalny na danych dziennych.
MAX_UNCERTAINTY_PCT = 0.10


def describe_exit(rule):
    t = rule["type"]
    if t == "time":
        return f"czas {rule['max_days']}d"
    if t == "sl":
        return f"SL {rule['sl']}% / czas {rule['max_days']}d"
    if t == "tp":
        return f"TP {rule['tp']}% / czas {rule['max_days']}d"
    return f"SL {rule['sl']}% / TP {rule['tp']}% / czas {rule['max_days']}d"


def test_exit(bars, signal_idx, rule, direction=1, entry=ENTRY_NEXT_OPEN,
              baseline_idx=None):
    """
    Testuje jedna regule wyjscia. Zwraca statystyki przy zalozeniu
    pesymistycznym, miare niepewnosci ORAZ przewage nad baseline.

    Baseline jest tu tak samo niezbedny jak przy event study: przy wyjsciu
    po 10 dniach sama srednia wyglada swietnie, bo indeks w tym okresie
    rosl. Liczy sie roznica wobec tej samej reguly zastosowanej do
    wszystkich dni, nie sama wysokosc zwrotu.
    """
    sl = rule.get("sl")
    tp = rule.get("tp")
    days = rule["max_days"]

    r_pess, reasons = simulate(bars, signal_idx, direction=direction, entry=entry,
                               sl_pct=sl, tp_pct=tp, max_days=days,
                               assume=ASSUME_PESSIMISTIC)
    if len(r_pess) == 0:
        return None

    stats = summarize(r_pess, describe_exit(rule))

    # niepewnosc ma sens tylko gdy oba progi sa ustawione
    if sl and tp:
        r_opt, _ = simulate(bars, signal_idx, direction=direction, entry=entry,
                            sl_pct=sl, tp_pct=tp, max_days=days,
                            assume=ASSUME_OPTIMISTIC)
        uncertainty = abs(float(r_opt.mean()) - float(r_pess.mean()))
    else:
        uncertainty = 0.0

    # ta sama regula na wszystkich dniach — ile z wyniku to sam dryf rynku
    edge_mean = None
    edge_hit = None
    base_mean = None
    if baseline_idx is not None and len(baseline_idx):
        r_base, _ = simulate(bars, baseline_idx, direction=direction, entry=entry,
                             sl_pct=sl, tp_pct=tp, max_days=days,
                             assume=ASSUME_PESSIMISTIC)
        if len(r_base):
            base_mean = float(r_base.mean())
            edge_mean = float(r_pess.mean()) - base_mean
            edge_hit = float((r_pess > 0).mean() * 100.0) - float((r_base > 0).mean() * 100.0)

    unique, counts = np.unique(reasons, return_counts=True)
    stats.update({
        "rule": rule,
        "uncertainty": round(uncertainty, 4),
        "trustworthy": uncertainty <= MAX_UNCERTAINTY_PCT,
        "exit_reasons": {str(u): int(c) for u, c in zip(unique, counts)},
        "base_mean": round(base_mean, 4) if base_mean is not None else None,
        "edge_mean": round(edge_mean, 4) if edge_mean is not None else None,
        "edge_hit": round(edge_hit, 2) if edge_hit is not None else None,
    })
    return stats


def optimize(bars, signal_idx, direction=1, entry=ENTRY_NEXT_OPEN, grid=None,
             baseline_idx=None):
    """
    Testuje wszystkie reguly wyjscia dla danego zestawu sygnalow.
    Sortuje po PRZEWADZE nad baseline, nie po surowym zwrocie.
    """
    results = []
    for rule in (grid or EXIT_GRID):
        s = test_exit(bars, signal_idx, rule, direction, entry, baseline_idx)
        if s:
            results.append(s)
    key = lambda s: (s["trustworthy"], s["edge_mean"] if s["edge_mean"] is not None else s["mean"])
    results.sort(key=key, reverse=True)
    return results
