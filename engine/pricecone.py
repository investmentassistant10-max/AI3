"""
Od prognozy zmiennosci do prognozy CENY.

CZEGO SIE TU NIE OBIECUJE
Kierunku. Walidacja krocząca pokazala, ze na dziennych swiecach SP500 kierunek
jest nieprzewidywalny (korelacja 0.04, zero przewagi nad "zawsze wzrost").
Zaden stozek tego nie naprawi i nie o to w nim chodzi.

CO SIE DA OBIECAC
Rozklad. Zmiennosc mowi, JAK DALEKO cena prawdopodobnie zajdzie, a to
wystarcza, zeby policzyc konkretne rzeczy o cenie:

    "za 5 sesji cena bedzie miedzy 7560 a 7880 z prawdopodobienstwem 80%"
    "szansa, ze dotknie 7600 w ciagu tych 5 sesji: 34%"
    "szansa zamkniecia powyzej dzisiejszego poziomu: 51%"

To sa zdania o cenie, sprawdzalne co do liczby — i takie, ktore da sie
rozliczyc po fakcie, w odroznieniu od "pojdzie w gore".

MODEL
Log-cena blądzi losowo z dryfem: ln(S_h/S_0) ~ N(mu*h - sigma^2*h/2, sigma^2*h),
gdzie sigma bierze sie z prognozy zmiennosci, a mu to historyczny dryf indeksu.
Dryf na krotkich horyzontach jest prawie niewidoczny przy szumie: przez tydzien
daje okolo 0.15%, podczas gdy typowy tygodniowy ruch to kilka procent. Dlatego
stozek jest niemal symetryczny — i tak wlasnie ma byc.

Prawdopodobienstwo DOTKNIECIA poziomu liczymy zasada odbicia: sciezka, ktora
poziom przekroczyla i wrocila, tez go dotknela. Szansa dotkniecia jest zawsze
wieksza niz szansa zamkniecia po drugiej stronie — o tym latwo zapomniec przy
ustawianiu stopa.

    python3 pricecone.py            pokaz stozek na dzis
    python3 pricecone.py --sprawdz  ile razy cena faktycznie trafiala w stozek
"""
import argparse
from math import erf, exp, log, sqrt

import numpy as np

TRADING_DAYS = 252

# Dryf indeksu liczony z wlasnej historii przy pierwszym uzyciu.
# Nie zaszywamy "8% rocznie" na sztywno — niech powie to ta sama baza,
# na ktorej dziala reszta systemu.
_DRIFT_CACHE = {}


def phi(x):
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def historical_drift(df, do_daty=None):
    """Sredni logarytmiczny dryf dzienny, liczony tylko z przeszlosci."""
    key = str(do_daty)
    if key in _DRIFT_CACHE:
        return _DRIFT_CACHE[key]
    sub = df if do_daty is None else df[df.index < do_daty]
    if len(sub) < 500:
        return 0.0
    lc = np.log(sub["close"].to_numpy(dtype=float))
    drift = float((lc[-1] - lc[0]) / (len(lc) - 1))
    _DRIFT_CACHE[key] = drift
    return drift


def mnoznik_stozka(sigma_log):
    """
    Poprawka z mediany na srednia.

    Model uczy sie na LOGARYTMIE zmiennosci, wiec po powrocie do skali
    procentowej zwraca mediane rozkladu, nie srednia. Do prognozy samej
    zmiennosci to jest wlasciwa liczba — najbardziej prawdopodobna wartosc.
    Ale stozek cenowy potrzebuje typowej WIELKOSCI ruchu, a ta jest wieksza,
    bo rozklad ma dlugi prawy ogon. Dla rozkladu logarytmiczno-normalnego
    roznica wynosi dokladnie exp(sigma^2/2).

    Bez tej poprawki stozek jednosesyjny obejmowal 49% przypadkow zamiast 80%
    (sprawdzone na 2935 obserwacjach 2015-2026). Z poprawka: 76%.
    Na dluzszych horyzontach poprawka jest niewielka, bo model myli sie tam
    mniej: 1.10 dla pieciu sesji, 1.06 dla miesiaca.
    """
    if sigma_log is None or not np.isfinite(sigma_log):
        return 1.0
    return float(np.exp(sigma_log * sigma_log / 2.0))


def cone(close, vol_pct, horizon, drift_daily=0.0, poziomy=(0.10, 0.25, 0.50, 0.75, 0.90)):
    """
    Rozklad ceny za `horizon` sesji.

    close    — dzisiejsze zamkniecie
    vol_pct  — prognoza zmiennosci w skali rocznej (to, co liczy model)
    """
    sigma = (vol_pct / 100.0) * sqrt(horizon / TRADING_DAYS)
    mu = drift_daily * horizon - 0.5 * sigma * sigma
    kwantyle = {}
    for q in poziomy:
        # odwrotnosc dystrybuanty normalnej — przyblizenie Actona, blad < 1e-9
        z = _ppf(q)
        kwantyle[q] = close * exp(mu + z * sigma)
    return {"sigma": sigma, "mu": mu, "kwantyle": kwantyle}


def _ppf(p):
    """Odwrotna dystrybuanta normalna (Acklam), bez zaleznosci od scipy."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = sqrt(-2 * log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = sqrt(-2 * log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def p_powyzej(close, poziom, c):
    """Szansa, ze ZAMKNIECIE za h sesji wypadnie powyzej poziomu."""
    if poziom <= 0 or close <= 0:
        return None
    return 1.0 - phi((log(poziom / close) - c["mu"]) / c["sigma"])


def p_dotknie(close, poziom, c):
    """
    Szansa, ze cena DOTKNIE poziomu w dowolnym momencie do horyzontu.

    Zasada odbicia: dla ruchu bez dryfu szansa dotkniecia jest dokladnie
    dwa razy wieksza od szansy zakonczenia po drugiej stronie. Przy dryfie
    dochodzi poprawka wykladnicza. Roznica jest praktyczna: stop postawiony
    tam, gdzie "raczej nie zamkniemy", bywa dotykany dwa razy czesciej.
    """
    if poziom <= 0 or close <= 0:
        return None
    s, m = c["sigma"], c["mu"]
    b = log(poziom / close)
    if b > 0:   # poziom nad cena
        return min(1.0, phi((-b + m) / s) + exp(2 * m * b / (s * s)) * phi((-b - m) / s))
    return min(1.0, phi((b - m) / s) + exp(2 * m * b / (s * s)) * phi((b + m) / s))


def opisz(close, vol_pct, horizon, drift_daily=0.0):
    """Komplet liczb o cenie dla jednego horyzontu — to leci do panelu."""
    c = cone(close, vol_pct, horizon, drift_daily)
    k = c["kwantyle"]
    return {
        "horizon": horizon,
        "close": round(close, 2),
        "p10": round(k[0.10], 1), "p25": round(k[0.25], 1),
        "p50": round(k[0.50], 1),
        "p75": round(k[0.75], 1), "p90": round(k[0.90], 1),
        "zasieg_pct": round((k[0.90] / k[0.10] - 1) * 100, 1),
        "p_powyzej_dzis": round(p_powyzej(close, close, c), 3),
        "p_dotknie_gora_1pct": round(p_dotknie(close, close * 1.01, c), 3),
        "p_dotknie_dol_1pct": round(p_dotknie(close, close * 0.99, c), 3),
        "p_dotknie_gora_2pct": round(p_dotknie(close, close * 1.02, c), 3),
        "p_dotknie_dol_2pct": round(p_dotknie(close, close * 0.98, c), 3),
    }


# ---------------------------------------------------------------- sprawdzenie

def sprawdz(horyzonty=(1, 5, 10, 22), od_roku=2015, z_dryfem=True):
    """
    Ile razy cena faktycznie wpadla w stozek.

    To jest ten sam rodzaj sprawdzenia, ktory zabil prognoze kierunku, tylko
    zadane inaczej: nie "czy zgadl", ale "czy przedzial 80% obejmuje 80%
    przypadkow". Model moze miec dobra korelacje i mimo to zle kalibrowany
    stozek — na przyklad systematycznie za waski, co w praktyce znaczy stopy
    wybijane czesciej, niz obiecuje panel.

    Kazdy rok prognozowany jest modelem uczonym WYLACZNIE na latach
    wczesniejszych. Dryf tez liczony jest tylko z przeszlosci.
    """
    from volatility import build_vol
    import volmodel

    df, _ = build_vol()
    close = df["close"].to_numpy(dtype=float)
    lata = sorted({d.year for d in df.index if d.year >= od_roku})

    print("=" * 92)
    print("KALIBRACJA STOZKA CENOWEGO" + ("" if z_dryfem else "  (bez dryfu)"))
    print("=" * 92)
    print("Ile obserwacji faktycznie wpadlo w przedzial. Idealnie: 50% i 80%.")
    print(f"\n{'horyzont':>9} {'przypadkow':>11} {'w pasmie 50%':>14} {'w pasmie 80%':>14} "
          f"{'ponizej':>9} {'powyzej':>9}")
    print("-" * 92)

    wyniki = {}
    for h in horyzonty:
        traf50 = traf80 = ponizej = powyzej = n = 0
        for rok in lata:
            train = df[df.index < f"{rok}-01-01"]
            if len(train) < 500:
                continue
            model = volmodel.fit(train, h)
            if model is None:
                continue
            import drivers
            popr = mnoznik_stozka(drivers.residual_sigma(train, model, h, ostroznosc=1.0))
            drift = historical_drift(df, f"{rok}-01-01") if z_dryfem else 0.0
            maska = np.array([d.year == rok for d in df.index])
            idx = np.where(maska)[0]
            pred = volmodel.predict(df.iloc[idx], model).to_numpy(dtype=float)

            for j, i in enumerate(idx):
                if i + h >= len(close) or not np.isfinite(pred[j]) or pred[j] <= 0:
                    continue
                c = cone(close[i], pred[j] * popr, h, drift)
                k, rzecz = c["kwantyle"], close[i + h]
                n += 1
                if k[0.25] <= rzecz <= k[0.75]:
                    traf50 += 1
                if k[0.10] <= rzecz <= k[0.90]:
                    traf80 += 1
                elif rzecz < k[0.10]:
                    ponizej += 1
                else:
                    powyzej += 1

        if not n:
            continue
        wyniki[h] = (n, traf50 / n, traf80 / n)
        print(f"{h:>8}s {n:>11,} {traf50/n:>13.1%} {traf80/n:>13.1%} "
              f"{ponizej/n:>8.1%} {powyzej/n:>8.1%}")

    print("\nCo znaczy odchylenie od 80%:")
    print("  wynik ponizej — stozek jest za waski, cena wypada z niego czesciej,")
    print("                  niz panel obiecuje; stopy beda wybijane za czesto")
    print("  wynik powyzej — stozek jest za szeroki, model przesadza z ostroznoscia")
    return wyniki


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sprawdz", action="store_true", help="kalibracja na historii")
    ap.add_argument("--bez-dryfu", action="store_true")
    ap.add_argument("--od", type=int, default=2015)
    args = ap.parse_args()

    if args.sprawdz:
        sprawdz(od_roku=args.od, z_dryfem=not args.bez_dryfu)
        return

    from volatility import build_vol
    import predict_vol

    df, _ = build_vol()
    p = predict_vol.make_prediction(quiet=True)
    drift = historical_drift(df)
    c0 = float(df["close"].iloc[-1])

    print(f"Stozek cenowy na {p['date']}, zamkniecie {c0:.2f}")
    print(f"Dryf historyczny: {drift*TRADING_DAYS*100:.1f}% rocznie "
          f"({drift*100:.4f}% na sesje)\n")
    print(f"{'horyzont':>9} {'10%':>9} {'25%':>9} {'mediana':>9} {'75%':>9} {'90%':>9} "
          f"{'>dzis':>7} {'dotknie +1%':>12} {'dotknie -1%':>12}")
    print("-" * 96)
    for h, e in sorted(p["horizons"].items(), key=lambda kv: int(kv[0])):
        o = opisz(c0, e["predicted_vol"], int(h), drift)
        print(f"{h:>8}s {o['p10']:>9.0f} {o['p25']:>9.0f} {o['p50']:>9.0f} "
              f"{o['p75']:>9.0f} {o['p90']:>9.0f} {o['p_powyzej_dzis']:>6.0%} "
              f"{o['p_dotknie_gora_1pct']:>11.0%} {o['p_dotknie_dol_1pct']:>11.0%}")


if __name__ == "__main__":
    main()
