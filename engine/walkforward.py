"""
Walidacja kroczaca — czy system ma jakakolwiek wartosc predykcyjna.

WSZYSTKO INNE W TYM PROJEKCIE MIERZY PRZESZLOSC
Rating, skarbiec, placebo, kalibracja — kazde z nich patrzy na dane, ktore
silnik juz widzial, albo odklada na bok kawalek tej samej historii. Nawet
skarbiec to przeszlosc, tyle ze schowana.

CO ROBI TEN TEST
Symuluje uczciwie caly cykl zycia systemu, rok po roku:

    1. bierze dane WYLACZNIE do konca roku X-1
    2. szuka na nich strategii — tak jak silnik szukalby wtedy
    3. przewiduje kazda sesje roku X, nie widzac ani jednej jego swiecy
    4. porownuje predykcje z tym, co sie faktycznie stalo

Powtarza to dla kazdego roku. Zaden dzien nie jest przewidywany na podstawie
danych, ktore w tamtym momencie nie istnialy.

Z CZYM POROWNUJEMY
Sama trafnosc niczego nie mowi: rynek rosnie, wiec "zawsze wzrost" trafia
w okolicach 54%. Liczy sie przewaga nad trzema punktami odniesienia:
  - zawsze wzrost         (najprostsza strategia, jaka istnieje)
  - sredni dryf rynku     (prognoza stala, rowna historycznej sredniej)
  - rzut moneta           (50%)

    python3 walkforward.py                  lata 2015-2024, horyzont 1 dnia
    python3 walkforward.py --from 2010 --horizon 3
"""
import argparse
import random
import time

import numpy as np

from features import build
from strategy import (generate_level1, generate_level2, SIGNAL_CONDITIONS,
                      CONTEXT_CONDITIONS)
from rating import compute_rating, significance_threshold
from shrinkage import estimate_hyperparams, shrink
import fastcore

# Ile hipotez sprawdzamy w kazdym roku. Mniej niz w pelnym silniku, bo ten
# test powtarza caly cykl dziesiec razy — chodzi o kierunek, nie o rekord.
SEARCH_SAMPLE = 25_000
MIN_RATING = 40.0
MATCH_LIMIT = 300


def train_year(df, end_date, sample=SEARCH_SAMPLE, seed=0):
    """
    Szuka strategii na danych do end_date. Zwraca liste kandydatow.
    To jest dokladnie to, co silnik zrobilby, gdyby byl uruchomiony wtedy.
    """
    train = df[df.index < end_date]
    if len(train) < 500:
        return [], 1.0, 1.1

    data = fastcore.FastData(train)
    rng = random.Random(seed)

    pool = list(generate_level1())
    level2 = list(generate_level2())
    rng.shuffle(level2)
    pool += level2[: max(0, sample - len(pool))]

    masks = {}
    candidates = []
    edges, eps = [], []

    for i, strat in enumerate(pool):
        mask = None
        for cond in strat["conditions"]:
            key = tuple(cond)
            m = masks.get(key)
            if m is None:
                m = fastcore.condition_mask(data, *key)
                masks[key] = m
            mask = m if mask is None else (mask & m)

        stats = fastcore.evaluate(data, mask, strat["horizon"])
        if stats.get("status") != "ok":
            continue

        edges.append(stats["edge_mean"])
        eps.append(stats["n_episodes"])

        stab, _ = fastcore.stability(data, mask, strat["horizon"], stats["edge_mean"])
        scores = compute_rating(stats, None, i + 1)
        base = (scores["accuracy_score"] * 0.45 + stab * 0.35
                + scores["frequency_score"] * 0.20)
        rating = round(base * scores["significance_multiplier"], 1)
        if rating >= MIN_RATING:
            candidates.append({
                "conditions": strat["conditions"],
                "horizon": strat["horizon"],
                "rating": rating,
                "edge_mean": stats["edge_mean"],
                "base_mean": stats["base_mean"],
                "n_episodes": stats["n_episodes"],
            })

    tau, sigma = estimate_hyperparams(edges, eps) if edges else (0.06, 1.1)
    candidates.sort(key=lambda c: -c["rating"])
    return candidates, tau, sigma


def signature(conds):
    return tuple(sorted(f"{c[0]}{c[1]}" for c in conds))


def predict_day(row, candidates, horizon, tau, sigma):
    """Prognoza na jeden dzien — ta sama logika co w predict.py."""
    matched = []
    for c in candidates:
        if c["horizon"] != horizon:
            continue
        ok = True
        for f, op, th in c["conditions"]:
            v = row.get(f)
            if v is None or not np.isfinite(v):
                ok = False
                break
            if (op == "<" and not v < th) or (op == ">" and not v > th):
                ok = False
                break
        if ok:
            matched.append(c)
        if len(matched) >= MATCH_LIMIT:
            break

    if not matched:
        return None, 0, 0

    families = {}
    for c in matched:
        families.setdefault(signature(c["conditions"]), []).append(c)

    fam_move, fam_w = [], []
    for members in families.values():
        w = np.array([m["rating"] / 100.0 for m in members])
        moves = []
        for m in members:
            e_adj, _ = shrink(m["edge_mean"], m["n_episodes"], tau=tau, sigma=sigma)
            moves.append(m["base_mean"] + e_adj)
        fam_move.append(float((np.array(moves) * w).sum() / w.sum()))
        fam_w.append(float(w.max()))

    fam_move, fam_w = np.array(fam_move), np.array(fam_w)
    return float((fam_move * fam_w).sum() / fam_w.sum()), len(matched), len(families)


def run(start_year=2015, end_year=2024, horizon=1, sample=SEARCH_SAMPLE):
    df, _ = build()
    fwd_col = f"fwd_{horizon}"

    print("=" * 96)
    print(f"WALIDACJA KROCZACA — horyzont {horizon}D, lata {start_year}-{end_year}")
    print("Kazdy rok przewidywany wylacznie na podstawie danych sprzed niego.")
    print("=" * 96)
    print(f"{'rok':>6} {'sesji':>7} {'sygnal':>7} {'traf.':>7} {'zawsze^':>8} "
          f"{'przewaga':>9} {'korelacja':>10} {'kandyd.':>8}")
    print("-" * 96)

    all_pred, all_act = [], []
    yearly = []

    for year in range(start_year, end_year + 1):
        t0 = time.time()
        cutoff = f"{year}-01-01"
        candidates, tau, sigma = train_year(df, cutoff, sample=sample, seed=year)

        test = df[(df.index >= cutoff) & (df.index < f"{year+1}-01-01")]
        test = test[test[fwd_col].notna()]
        if not len(test) or not candidates:
            print(f"{year:>6} {len(test):>7} {'brak kandydatow':>40}")
            continue

        preds, actuals = [], []
        for idx, row in test.iterrows():
            r = {c: row[c] for c in df.columns if not c.startswith("fwd_")}
            move, n_m, n_f = predict_day(r, candidates, horizon, tau, sigma)
            if move is None:
                continue
            preds.append(move)
            actuals.append(float(row[fwd_col]))

        if len(preds) < 10:
            print(f"{year:>6} {len(test):>7} {'za malo sygnalow':>40}")
            continue

        preds, actuals = np.array(preds), np.array(actuals)
        all_pred.extend(preds); all_act.extend(actuals)

        hit = float(np.mean(np.sign(preds) == np.sign(actuals)) * 100)
        always_up = float(np.mean(actuals > 0) * 100)
        corr = float(np.corrcoef(preds, actuals)[0, 1]) if preds.std() > 0 else 0.0

        yearly.append((year, hit, always_up, corr))
        print(f"{year:>6} {len(test):>7} {len(preds):>7} {hit:>6.1f}% {always_up:>7.1f}% "
              f"{hit-always_up:>+8.1f} {corr:>10.3f} {len(candidates):>8} "
              f" ({time.time()-t0:.0f}s)")

    if not all_pred:
        print("\nBrak wynikow.")
        return

    P, A = np.array(all_pred), np.array(all_act)
    hit = float(np.mean(np.sign(P) == np.sign(A)) * 100)
    always_up = float(np.mean(A > 0) * 100)
    drift = float(A.mean())
    corr = float(np.corrcoef(P, A)[0, 1]) if P.std() > 0 else 0.0
    mae_model = float(np.abs(P - A).mean())
    mae_drift = float(np.abs(drift - A).mean())
    mae_zero = float(np.abs(A).mean())

    print("=" * 96)
    print("LACZNIE")
    print("=" * 96)
    print(f"  predykcji:                       {len(P):,}")
    print(f"  trafnosc kierunku:               {hit:.2f}%")
    print(f"  'zawsze wzrost' trafialby:       {always_up:.2f}%")
    print(f"  rzut moneta:                     50.00%")
    print(f"  PRZEWAGA nad 'zawsze wzrost':    {hit - always_up:+.2f} pp")
    print()
    print(f"  korelacja prognoza-rzeczywistosc:{corr:>7.4f}")
    print(f"  sredni blad modelu:              {mae_model:.4f} pp")
    print(f"  sredni blad prognozy stalej:     {mae_drift:.4f} pp")
    print(f"  sredni blad prognozy zerowej:    {mae_zero:.4f} pp")
    print()

    # test dwumianowy: czy przewaga nad 'zawsze wzrost' to nie przypadek
    from math import comb
    k = int(round(hit / 100 * len(P)))
    p_base = always_up / 100
    pval = sum(comb(len(P), i) * p_base**i * (1-p_base)**(len(P)-i)
               for i in range(k, len(P) + 1)) if len(P) < 2000 else None

    # Werdykt wymaga TRZECH rzeczy naraz. Sama przewaga w trafnosci nie
    # wystarcza: model moze czesciej zgadywac kierunek, a jednoczescie mylic
    # sie co do wielkosci bardziej niz prognoza "zawsze zero" — i wtedy jest
    # bezuzyteczny do czegokolwiek poza zakladem o kierunek.
    beats_direction = pval is not None and pval < 0.05 and hit > always_up
    beats_magnitude = mae_model < min(mae_drift, mae_zero)
    has_correlation = corr > 0.05

    passed = sum([beats_direction, beats_magnitude, has_correlation])

    print("  KRYTERIA:")
    print(f"    {'TAK' if beats_direction else 'nie'}  bije 'zawsze wzrost' istotnie "
          f"(p={pval:.4f} < 0.05)" if pval is not None else
          f"    ?    zbyt duza probka, zeby policzyc p")
    print(f"    {'TAK' if beats_magnitude else 'nie'}  myli sie mniej niz prognoza stala "
          f"({mae_model:.4f} vs {min(mae_drift, mae_zero):.4f})")
    print(f"    {'TAK' if has_correlation else 'nie'}  prognozy koreluja z rzeczywistoscia "
          f"({corr:.4f} > 0.05)")

    if passed == 3:
        verdict = "SYSTEM MA WARTOSC PREDYKCYJNA"
    elif passed == 2:
        verdict = "system moze miec wartosc, ale dowod jest niepelny"
    elif passed == 1:
        verdict = "SLABO — jedno kryterium z trzech to za malo, zeby na tym polegac"
    else:
        verdict = "BRAK WARTOSCI PREDYKCYJNEJ na tych danych"
    print(f"\n  -> {verdict}  ({passed}/3 kryteria)")

    if len(P) < 1000:
        print(f"     Uwaga: {len(P)} predykcji to malo. Uruchom na dluzszym okresie")
        print(f"     (--from 2010), zeby wynik cokolwiek znaczyl.")

    if yearly:
        pos = sum(1 for _, h, a, _ in yearly if h > a)
        print(f"\n  Lat z przewaga nad 'zawsze wzrost': {pos} z {len(yearly)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=2015)
    ap.add_argument("--to", dest="end", type=int, default=2024)
    ap.add_argument("--horizon", type=int, default=1)
    ap.add_argument("--sample", type=int, default=SEARCH_SAMPLE)
    args = ap.parse_args()
    run(args.start, args.end, args.horizon, args.sample)
