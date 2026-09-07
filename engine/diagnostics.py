"""
Testy, ktore odrozniaja prawdziwy efekt od dopasowania do szumu.

Rating i t-stat same w sobie nie wystarcza — przy setkach tysiecy prob
zawsze cos wyjdzie na gorze. Te trzy testy sprawdzaja co innego:

  monotonicznosc — czy efekt zmienia sie plynnie z progiem, czy ma ostry pik
                   akurat tam, gdzie go szukalismy
  placebo        — czy przetasowanie dat niszczy efekt (powinno) czy nie
                   (wtedy to artefakt konstrukcji testu, nie rynku)
  skarbiec       — czy przezyl na danych, ktorych silnik nigdy nie widzial

Uzycie:
    python3 diagnostics.py close_position "<" 0.15 1
"""
import sys

import numpy as np

from features import build
from evaluate import split_search_treasury
import fastcore


def monotonicity(data, feature, op, thresholds, horizon):
    """Efekt przy kolejnych progach. Prawdziwy sygnal zmienia sie lagodnie."""
    rows = []
    for th in thresholds:
        m = fastcore.condition_mask(data, feature, op, th)
        r = fastcore.evaluate(data, m, horizon)
        rows.append({
            "threshold": th,
            "status": r.get("status"),
            "n_signals": r.get("n_signals"),
            "edge_mean": r.get("edge_mean"),
            "hit_rate": r.get("hit_rate"),
            "t_stat": r.get("t_stat"),
        })
    return rows


def placebo(data, mask, horizon, n=200, seed=42):
    """
    Przetasowuje maske w czasie. Jesli efekt przezyje losowe przetasowanie,
    to nie pochodzi z rynku, tylko z konstrukcji testu.
    Zwraca (t prawdziwe, ile przetasowan bylo rownie dobrych, rozklad).
    """
    rng = np.random.default_rng(seed)
    real = fastcore.evaluate(data, mask, horizon)
    if real.get("status") != "ok":
        return None, None, None

    ts = []
    for _ in range(n):
        r = fastcore.evaluate(data, rng.permutation(mask), horizon)
        if r.get("status") == "ok" and r["t_stat"] is not None:
            ts.append(r["t_stat"])
    ts = np.array(ts)
    as_good = int(np.count_nonzero(np.abs(ts) >= abs(real["t_stat"])))
    return real["t_stat"], as_good, ts


def treasury_check(feature, op, threshold, horizon):
    """Porownuje wynik na danych do szukania i na skarbcu."""
    df, _ = build()
    search_df, treasury_df = split_search_treasury(df)
    ds, dt = fastcore.FastData(search_df), fastcore.FastData(treasury_df)
    ms = fastcore.condition_mask(ds, feature, op, threshold)
    mt = fastcore.condition_mask(dt, feature, op, threshold)
    return fastcore.evaluate(ds, ms, horizon), fastcore.evaluate(dt, mt, horizon)


def full_report(feature, op, threshold, horizon, thresholds=None):
    df, _ = build()
    search_df, treasury_df = split_search_treasury(df)
    data = fastcore.FastData(search_df)
    mask = fastcore.condition_mask(data, feature, op, threshold)

    print(f"\n{'='*80}\nDIAGNOSTYKA: {feature} {op} {threshold}  ->  {horizon}d\n{'='*80}")

    base = fastcore.evaluate(data, mask, horizon)
    if base.get("status") != "ok":
        print(f"Nie da sie ocenic: {base.get('status')}")
        return
    print(f"Na danych do szukania: przewaga {base['edge_mean']:+.3f}%, "
          f"trafnosc {base['hit_rate']:.1f}% (baza {base['base_hit_rate']:.1f}%), "
          f"t={base['t_stat']:.2f}, epizodow {base['n_episodes']}")

    if thresholds is None:
        span = abs(threshold) * 0.6 or 0.3
        thresholds = [round(threshold + span * k / 3, 4) for k in range(-3, 4)]
    print(f"\nMONOTONICZNOSC (prawdziwy efekt = lagodna zmiana):")
    print(f"  {'prog':>10} {'dni':>7} {'przewaga%':>11} {'traf%':>7} {'t':>7}")
    for row in monotonicity(data, feature, op, thresholds, horizon):
        if row["status"] != "ok":
            print(f"  {row['threshold']:>10} {row['status']:>34}")
        else:
            print(f"  {row['threshold']:>10} {row['n_signals']:>7} "
                  f"{row['edge_mean']:>11.4f} {row['hit_rate']:>7.1f} {row['t_stat']:>7.2f}")

    t_real, as_good, ts = placebo(data, mask, horizon)
    print(f"\nPLACEBO (200 przetasowan dat):")
    print(f"  prawdziwe |t| = {abs(t_real):.2f} | z przetasowan: max {np.abs(ts).max():.2f}, "
          f"srednio {np.abs(ts).mean():.2f}")
    print(f"  rownie dobrych losowych: {as_good}/200  "
          f"{'-> efekt nie jest artefaktem' if as_good <= 10 else '-> PODEJRZANE'}")

    print(f"\nEFEKT W KOLEJNYCH ERACH RYNKOWYCH:")
    print(f"  {'era':<38} {'sygn':>6} {'przewaga%':>11} {'traf%':>7}")
    for e in by_era(feature, op, threshold, horizon):
        if e["status"] != "ok":
            print(f"  {e['era']:<38} {e['status']:>25}")
        else:
            print(f"  {e['era']:<38} {e['n_signals']:>6} {e['edge_mean']:>11.4f} {e['hit_rate']:>7.1f}")

    print(f"\nEFEKT WEDLUG REZIMU ZMIENNOSCI:")
    print(f"  {'rezim':<20} {'sygn':>6} {'przewaga%':>11} {'traf%':>7}")
    for b in by_volatility(feature, op, threshold, horizon):
        if b["status"] != "ok":
            print(f"  {b['band']:<20} {b['status']:>25}")
        else:
            print(f"  {b['band']:<20} {b['n_signals']:>6} {b['edge_mean']:>11.4f} {b['hit_rate']:>7.1f}")

    rs, rt = treasury_check(feature, op, threshold, horizon)
    print(f"\nSKARBIEC:")
    if rt.get("status") != "ok":
        print(f"  nie da sie sprawdzic: {rt.get('status')}")
    else:
        same = rs["edge_mean"] * rt["edge_mean"] > 0
        print(f"  szukanie: przewaga {rs['edge_mean']:+.3f}%, trafnosc {rs['hit_rate']:.1f}%")
        print(f"  skarbiec: przewaga {rt['edge_mean']:+.3f}%, trafnosc {rt['hit_rate']:.1f}% "
              f"({rt['n_signals']} sygnalow)")
        print(f"  -> {'POTWIERDZONA' if same else 'OBALONA'}")


# Ery rynkowe w naszych danych. Baza siega 2000 roku, wiec caly zbior lezy
# w epoce po bance internetowej — w duzej mierze era taniego pieniadza.
# Dlatego warto sprawdzac efekt osobno w kazdym rezimie, a nie tylko srednio.
ERAS = [
    ("2000-2002  pekniecie banki dot-com", "2000-01-01", "2002-12-31"),
    ("2003-2007  hossa przed kryzysem",    "2003-01-01", "2007-12-31"),
    ("2008-2009  kryzys finansowy",        "2008-01-01", "2009-12-31"),
    ("2010-2019  era QE, dluga hossa",     "2010-01-01", "2019-12-31"),
    ("2020-2021  COVID i stymulacja",      "2020-01-01", "2021-12-31"),
    ("2022       bessa inflacyjna",        "2022-01-01", "2022-12-31"),
    ("2023-2026  obecna hossa",            "2023-01-01", "2026-12-31"),
]


def by_era(feature, op, threshold, horizon, df=None):
    """
    Efekt w kolejnych erach rynkowych. Efekt obecny tylko w jednej epoce
    jest wlasnoscia tamtej epoki, nie rynku.
    """
    if df is None:
        df, _ = build()
    out = []
    for label, start, end in ERAS:
        sub = df[(df.index >= start) & (df.index <= end)]
        if len(sub) < 120:
            continue
        d = fastcore.FastData(sub)
        m = fastcore.condition_mask(d, feature, op, threshold)
        r = fastcore.evaluate(d, m, horizon)
        out.append({
            "era": label,
            "n_days": len(sub),
            "status": r.get("status"),
            "n_signals": r.get("n_signals"),
            "edge_mean": r.get("edge_mean"),
            "hit_rate": r.get("hit_rate"),
            "t_stat": r.get("t_stat"),
        })
    return out


def by_volatility(feature, op, threshold, horizon, df=None):
    """
    Efekt w podziale na rezim zmiennosci. Wiele sygnalow kontrariańskich
    zyje ze strachu na rynku — w spokojnym rynku nie maja z czego zarabiac.
    """
    if df is None:
        df, _ = build()
    from evaluate import split_search_treasury
    search_df, _ = split_search_treasury(df)
    d = fastcore.FastData(search_df)
    sig = fastcore.condition_mask(d, feature, op, threshold)
    vp = d.columns["vol_percentile"]

    bands = [
        ("spokojnie (< 0.3)", (vp < 0.3)),
        ("srednio (0.3-0.7)", (vp >= 0.3) & (vp <= 0.7)),
        ("nerwowo (> 0.7)",   (vp > 0.7)),
        ("panika (> 0.9)",    (vp > 0.9)),
    ]
    out = []
    for label, ctx in bands:
        ctx = ctx & ~np.isnan(vp)
        r = fastcore.evaluate(d, sig & ctx, horizon)
        out.append({
            "band": label,
            "status": r.get("status"),
            "n_signals": r.get("n_signals"),
            "edge_mean": r.get("edge_mean"),
            "hit_rate": r.get("hit_rate"),
            "t_stat": r.get("t_stat"),
        })
    return out


if __name__ == "__main__":
    if len(sys.argv) >= 5:
        full_report(sys.argv[1], sys.argv[2], float(sys.argv[3]), int(sys.argv[4]))
    else:
        full_report("close_position", "<", 0.15, 1,
                    thresholds=[0.05, 0.1, 0.15, 0.2, 0.25, 0.3])
