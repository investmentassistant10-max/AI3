"""
Walidacja kroczaca dla zmiennosci — ta sama metoda co dla kierunku.

Dla kazdego roku: dopasuj model wylacznie na danych sprzed niego, prognozuj
kazda sesje, porownaj z rzeczywistoscia. Zaden dzien nie jest prognozowany
na podstawie danych, ktore wtedy nie istnialy.

TRZY MODELE, KTORE POROWNUJEMY
  naiwny  — "w kolejnych h sesjach bedzie jak w ostatnich h"
  HAR     — regresja na zmiennosci dziennej, tygodniowej i miesiecznej
            (Corsi 2009, standard w literaturze)
  stala   — zawsze srednia historyczna

Naiwny model jest tu zaskakujaco mocny, bo zmiennosc jest uporczywa. To on
jest prawdziwa poprzeczka, nie srednia.

    python3 walkforward_vol.py                lata 2015-2024, horyzont 5 sesji
    python3 walkforward_vol.py --from 2010 --horizon 22
"""
import argparse

import numpy as np

from volatility import build_vol, har_fit, har_predict


def metrics(pred, actual):
    """Miary w skali logarytmicznej — rozklad zmiennosci jest prawoskosny."""
    ok = np.isfinite(pred) & np.isfinite(actual) & (pred > 0) & (actual > 0)
    if ok.sum() < 20:
        return None
    p, a = pred[ok], actual[ok]
    lp, la = np.log(p), np.log(a)
    corr = float(np.corrcoef(lp, la)[0, 1])
    ss_res = float(((la - lp) ** 2).sum())
    ss_tot = float(((la - la.mean()) ** 2).sum())
    return {
        "n": int(ok.sum()),
        "corr": corr,
        "r2": 1 - ss_res / ss_tot if ss_tot > 0 else 0.0,
        "mae": float(np.abs(p - a).mean()),
        "mape": float((np.abs(p - a) / a).mean() * 100),
    }


def run(start_year=2015, end_year=2024, horizon=5):
    df, _ = build_vol()
    target = f"fwd_rv_{horizon}"

    print("=" * 100)
    print(f"WALIDACJA KROCZACA — ZMIENNOSC, horyzont {horizon} sesji, lata {start_year}-{end_year}")
    print("=" * 100)
    print(f"{'rok':>6} {'sesji':>7} | {'naiwny':^22} | {'HAR':^22}")
    print(f"{'':>6} {'':>7} | {'korelacja':>10} {'MAPE':>10} | {'korelacja':>10} {'MAPE':>10}")
    print("-" * 100)

    acc = {"naive": {"p": [], "a": []}, "har": {"p": [], "a": []}, "const": {"p": [], "a": []}}

    for year in range(start_year, end_year + 1):
        train = df[df.index < f"{year}-01-01"]
        test = df[(df.index >= f"{year}-01-01") & (df.index < f"{year+1}-01-01")]
        test = test[test[target].notna()]
        if len(train) < 500 or len(test) < 20:
            continue

        actual = test[target].to_numpy()

        # naiwny: ostatnia znana zmiennosc na tym samym oknie
        naive_col = f"rv_{horizon}" if f"rv_{horizon}" in df.columns else "rv_22"
        naive = test[naive_col].to_numpy()

        # HAR dopasowany wylacznie na danych treningowych
        coef = har_fit(train, horizon)
        har = har_predict(test, coef).to_numpy()

        # stala: srednia z treningu
        const = np.full(len(test), float(train[target].dropna().mean()))

        m_naive = metrics(naive, actual)
        m_har = metrics(har, actual)

        for key, pred in (("naive", naive), ("har", har), ("const", const)):
            acc[key]["p"].extend(pred)
            acc[key]["a"].extend(actual)

        if m_naive and m_har:
            print(f"{year:>6} {len(test):>7} | {m_naive['corr']:>10.3f} {m_naive['mape']:>9.1f}% "
                  f"| {m_har['corr']:>10.3f} {m_har['mape']:>9.1f}%")

    print("=" * 100)
    print("LACZNIE")
    print("=" * 100)
    print(f"  {'model':<12} {'korelacja':>11} {'R2':>9} {'MAE':>10} {'MAPE':>9}")
    print("  " + "-" * 55)

    results = {}
    for key, label in (("const", "stala"), ("naive", "naiwny"), ("har", "HAR")):
        m = metrics(np.array(acc[key]["p"]), np.array(acc[key]["a"]))
        if m:
            results[key] = m
            print(f"  {label:<12} {m['corr']:>11.4f} {m['r2']:>9.3f} "
                  f"{m['mae']:>10.3f} {m['mape']:>8.1f}%")

    print()
    if "har" in results and "naive" in results:
        gain = results["har"]["corr"] - results["naive"]["corr"]
        mape_gain = results["naive"]["mape"] - results["har"]["mape"]
        print(f"  HAR nad naiwnym: korelacja {gain:+.4f}, blad wzgledny {mape_gain:+.1f} pp")
        print()
        print("  DLA POROWNANIA — ten sam test dla KIERUNKU dal korelacje 0.0415")
        print("  i zerowa przewage nad trywialnym modelem. Tutaj korelacja to")
        print(f"  {results['naive']['corr']:.3f} juz dla najprostszego modelu.")

    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=2015)
    ap.add_argument("--to", dest="end", type=int, default=2024)
    ap.add_argument("--horizon", type=int, default=5)
    args = ap.parse_args()
    run(args.start, args.end, args.horizon)
