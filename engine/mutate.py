"""
Ewolucja strategii: zamiast losowac w nieskonczonosc po calej przestrzeni,
szukamy w okolicy tego, co juz dziala.

Losowe probkowanie jest kiepskim sposobem na spedzenie osmiu godzin —
wiekszosc trafien to smiecie, a kazde z nich podnosi poprzeczke istotnosci
dla wszystkich pozostalych. Mutacje sa gestsze: biora warunek, ktory sie
sprawdzil, i sprawdzaja jego najblizsze sasiedztwo.

Przy okazji daja darmowy test monotonicznosci — jesli sasiednie progi daja
podobny wynik, efekt jest prawdziwy; jesli tylko jeden prog dziala, to byl
przypadek.
"""
import random

from strategy import (
    SIGNAL_GRID, CONTEXT_GRID, SIGNAL_CONDITIONS, CONTEXT_CONDITIONS,
    OPERATORS, HORIZONS, make_strategy,
)

# o ile procent przesuwamy prog przy mutacji
THRESHOLD_STEPS = (-0.30, -0.20, -0.10, -0.05, 0.05, 0.10, 0.20, 0.30)


def _nudge(threshold, step):
    """Przesuwa prog o procent jego wartosci; przy zerze uzywa staleji."""
    if threshold == 0:
        return round(step, 4)
    return round(threshold * (1.0 + step), 4)


def mutate_threshold(strategy, rng):
    """Przesuwa jeden prog o maly krok — sprawdza sasiedztwo."""
    conds = [list(c) for c in strategy["conditions"]]
    i = rng.randrange(len(conds))
    conds[i][2] = _nudge(conds[i][2], rng.choice(THRESHOLD_STEPS))
    return make_strategy([tuple(c) for c in conds], strategy["horizon"])


def mutate_horizon(strategy, rng):
    """Ten sam warunek, inny horyzont."""
    other = [h for h in HORIZONS if h != strategy["horizon"]]
    return make_strategy(
        [tuple(c) for c in strategy["conditions"]], rng.choice(other)
    )


def mutate_add_context(strategy, rng):
    """Dokłada warunek kontekstowy — zaweza sygnal do konkretnego rynku."""
    if len(strategy["conditions"]) >= 4:
        return None
    used = {c[0] for c in strategy["conditions"]}
    pool = [c for c in CONTEXT_CONDITIONS if c[0] not in used]
    if not pool:
        return None
    conds = [tuple(c) for c in strategy["conditions"]] + [rng.choice(pool)]
    return make_strategy(conds, strategy["horizon"])


def mutate_drop_condition(strategy, rng):
    """Usuwa warunek — sprawdza, czy nie byl tylko ozdobnikiem."""
    if len(strategy["conditions"]) < 2:
        return None
    conds = [tuple(c) for c in strategy["conditions"]]
    conds.pop(rng.randrange(len(conds)))
    return make_strategy(conds, strategy["horizon"])


def mutate_swap_signal(strategy, rng):
    """Podmienia warunek sygnalowy na inny, zachowujac kontekst."""
    conds = [tuple(c) for c in strategy["conditions"]]
    sig_idx = [i for i, c in enumerate(conds) if c[0] in SIGNAL_GRID]
    if not sig_idx:
        return None
    conds[rng.choice(sig_idx)] = rng.choice(SIGNAL_CONDITIONS)
    seen = set()
    uniq = [c for c in conds if not (c[0] in seen or seen.add(c[0]))]
    return make_strategy(uniq, strategy["horizon"])


def crossover(a, b, rng):
    """Laczy warunki dwoch dobrych strategii."""
    pool = [tuple(c) for c in a["conditions"]] + [tuple(c) for c in b["conditions"]]
    rng.shuffle(pool)
    seen, conds = set(), []
    for c in pool:
        if c[0] not in seen:
            seen.add(c[0])
            conds.append(c)
        if len(conds) >= 3:
            break
    return make_strategy(conds, rng.choice([a["horizon"], b["horizon"]]))


MUTATIONS = (
    (mutate_threshold, 0.40),     # najczesciej: sasiedztwo progu
    (mutate_horizon, 0.15),
    (mutate_add_context, 0.20),
    (mutate_drop_condition, 0.10),
    (mutate_swap_signal, 0.15),
)


def evolve(parents, n, seed=None):
    """
    Generuje n potomkow z listy rodzicow (definicje strategii jako dicty).
    Miesza mutacje z krzyzowaniem.
    """
    rng = random.Random(seed)
    ops, weights = zip(*MUTATIONS)
    produced = 0
    guard = 0
    while produced < n and guard < n * 20:
        guard += 1
        if len(parents) >= 2 and rng.random() < 0.15:
            child = crossover(rng.choice(parents), rng.choice(parents), rng)
        else:
            op = rng.choices(ops, weights=weights, k=1)[0]
            child = op(rng.choice(parents), rng)
        if child is None:
            continue
        produced += 1
        yield child
