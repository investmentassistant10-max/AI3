"""
Rozklad prognozy na czynniki: co ja podnosi, co obniza i z jaka sila.

Model jest regresja grzbietowa na LOGARYTMIE zmiennosci, co ma wygodna
konsekwencje: w logarytmie czynniki sie dodaja, wiec po powrocie do skali
procentowej — mnoza. Prognoze da sie wiec uczciwie rozlozyc:

    prognoza = poziom_bazowy x mnoznik_1 x mnoznik_2 x ...

Poziom bazowy to wyraz wolny: zmiennosc typowa dla calego okresu uczenia.
Kazdy mnoznik to jedna cecha — o ile odchyla prognoze od tego poziomu.
To NIE jest interpretacja dorobiona do wyniku. To sam wynik, zapisany
inaczej: iloczyn wszystkich mnoznikow razy poziom bazowy daje dokladnie
te liczbe, ktora model zwraca.

Sila czynnika bierze sie z dwoch rzeczy naraz: jak mocno model na niego
reaguje (wspolczynnik) i jak nietypowa jest dzis jego wartosc (odchylenie
od sredniej). Cecha wazna, ale dzis przecietna, nie przechyla niczego —
i tak wlasnie jest tu liczona.
"""
import numpy as np

# Nazwy po ludzku + zdanie tlumaczace, czemu ta cecha w ogole cos mowi.
OPISY = {
    "log_rv_1": ("ostatnia sesja",
                 "zmienność wczorajsza — najsilniejszy pojedynczy sygnał, bo "
                 "zmienność jest uporczywa: dzień po dniu wraca podobna"),
    "log_rv_5": ("ostatni tydzień",
                 "zmienność pięciosesyjna — wygładza pojedynczy wyskok"),
    "log_rv_22": ("ostatni miesiąc",
                  "zmienność miesięczna — tło, do którego rynek wraca"),
    "log_rv_66": ("ostatni kwartał",
                  "dłuższa pamięć niż w klasycznym HAR: kwartał zmienności"),
    "semivol_ratio": ("przewaga spadków",
                      "ile zmienności pochodzi ze spadków — spadki podnoszą "
                      "przyszłą zmienność mocniej niż wzrosty tej samej wielkości"),
    "parkinson_ratio": ("zakres dnia",
                        "ile mówi rozpiętość high-low ponad to, co mówią same "
                        "zamknięcia — szeroka świeca zdradza napięcie, które "
                        "zamknięcie ukrywa"),
    "rv_ratio_1_5": ("przyspieszenie (dzień do tygodnia)",
                     "czy zmienność właśnie rusza z miejsca"),
    "rv_ratio_5_22": ("przyspieszenie (tydzień do miesiąca)",
                      "czy ruch ostatniego tygodnia odstaje od miesiąca"),
    "rv_percentile_252": ("pozycja w rozkładzie roku",
                          "ta sama zmienność znaczy co innego przy dolnym "
                          "krańcu roku, a co innego przy górnym"),
    "vol_of_vol_22": ("niestabilność samej zmienności",
                      "jak bardzo skacze sama zmienność — spokój nierówny "
                      "jest mniej trwały niż spokój równy"),
    "gap_vol_22": ("luki otwarcia",
                   "napięcie przenoszone przez noc, niewidoczne w zwrotach "
                   "liczonych od zamknięcia do zamknięcia"),
}


def opis(nazwa):
    return OPISY.get(nazwa, (nazwa, ""))


def decompose(row, model, top=6, min_effect_pct=1.0):
    """
    Rozklada jedna prognoze na czynniki.

    row   — wiersz danych (Series) z wartosciami cech na dzis
    model — slownik z volmodel.fit

    Zwraca (poziom_bazowy_%, lista czynnikow). Czynniki sa posortowane od
    najmocniejszego, z pominieciem tych ponizej progu — zeby lista mowila
    o tym, co faktycznie przechyla wynik, a nie o wszystkim po kolei.
    """
    if model is None:
        return None, []

    features = model["features"]
    coef, mu, sd = model["coef"], model["mu"], model["sd"]

    base = float(np.exp(coef[0]))           # zmiennosc typowa dla okresu uczenia
    czynniki = []

    for i, nazwa in enumerate(features):
        x = float(row[nazwa])
        if not np.isfinite(x):
            continue
        z = (x - mu[i]) / sd[i]             # o ile odchyleñ od typowej wartosci
        wklad = float(coef[i + 1] * z)      # w logarytmie
        efekt = (np.exp(wklad) - 1) * 100   # w procentach prognozy
        if abs(efekt) < min_effect_pct:
            continue
        etykieta, dlaczego = opis(nazwa)
        czynniki.append({
            "cecha": nazwa,
            "etykieta": etykieta,
            "dlaczego": dlaczego,
            "odchylenie": round(z, 2),
            "efekt_pct": round(efekt, 1),
            "kierunek": "w gore" if efekt > 0 else "w dol",
        })

    czynniki.sort(key=lambda c: -abs(c["efekt_pct"]))
    return round(base, 2), czynniki[:top]


def residual_sigma(train, model, horizon):
    """
    Rozrzut bledu modelu w logarytmie — podstawa przedzialu ufnosci.

    Liczony na danych uczacych, wiec jest optymistyczny; walidacja krocząca
    pokazala blad wiekszy. Dlatego mnozymy przez wspolczynnik ostroznosci,
    zeby przedzial nie obiecywal precyzji, ktorej model nie ma.
    """
    OSTROZNOSC = 1.15
    target = f"log_fwd_rv_{horizon}"
    cols = model["features"] + [target]
    sub = train[cols].replace([np.inf, -np.inf], np.nan).dropna()
    if len(sub) < 200:
        return None
    Xraw = np.column_stack([sub[c].to_numpy(dtype=float) for c in model["features"]])
    X = np.column_stack([np.ones(len(sub)), (Xraw - model["mu"]) / model["sd"]])
    resid = sub[target].to_numpy(dtype=float) - X @ model["coef"]
    return float(np.std(resid) * OSTROZNOSC)


def _phi(x):
    """Dystrybuanta rozkladu normalnego — bez zaleznosci od scipy."""
    from math import erf, sqrt
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def uncertainty(pred, sigma, current=None):
    """
    Przedzial i prawdopodobienstwa.

    Blad modelu w logarytmie jest z grubsza normalny, wiec sama zmiennosc
    ma rozklad logarytmiczno-normalny. To wygodne zalozenie, nie prawda
    objawiona — ale znacznie blizsze rzeczywistosci niz przedzial
    symetryczny, bo zmiennosc nie schodzi ponizej zera, a w gore potrafi
    odjechac bardzo daleko.
    """
    if sigma is None or not np.isfinite(sigma) or sigma <= 0:
        return None
    lp = np.log(pred)
    out = {
        "sigma_log": round(sigma, 3),
        "p10": round(float(np.exp(lp - 1.2816 * sigma)), 1),
        "p25": round(float(np.exp(lp - 0.6745 * sigma)), 1),
        "p75": round(float(np.exp(lp + 0.6745 * sigma)), 1),
        "p90": round(float(np.exp(lp + 1.2816 * sigma)), 1),
    }
    if current and current > 0:
        # szansa, ze bedzie nerwowiej niz teraz
        out["p_wzrost"] = round(1.0 - _phi((np.log(current) - lp) / sigma), 2)
        # szansa na wyrazny skok: polowa raza wiecej niz dzis
        out["p_skok"] = round(1.0 - _phi((np.log(1.5 * current) - lp) / sigma), 2)
        # szansa, ze bedzie spokojniej o jedna piata
        out["p_spadek"] = round(_phi((np.log(0.8 * current) - lp) / sigma), 2)
    return out
