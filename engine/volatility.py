"""
Warstwa zmiennosci: cechy, cele i modele odniesienia.

DLACZEGO ZMIENNOSC, A NIE KIERUNEK
Walidacja kroczaca pokazala, ze kierunek indeksu na dziennych swiecach jest
nieprzewidywalny naszymi metodami (51.6% wobec 53.7% dla trywialnego "zawsze
wzrost", korelacja 0.04). To zgodne z tym, czego oczekuje literatura: cala
informacja zawarta w cenie jest obserwowana przez tysiace zespolow.

Zmiennosc jest inna. Jej struktura wynika z mechaniki rynku — dopasowywania
dzwigni, wezwan do uzupelnienia depozytow, wolniejszego przeplywu informacji —
a nie z niewiedzy uczestnikow. Dlatego nie znika od tego, ze wszyscy o niej
wiedza. To jeden z niewielu efektow w finansach, ktory przetrwal dekady
publikacji.

CO TU JEST
  realized_vol      — zmiennosc zrealizowana na oknie n sesji
  add_vol_features  — cechy predykcyjne (kaskada okien HAR, asymetria, reżim)
  add_vol_targets   — cele: zmiennosc w PRZYSZLYCH h sesjach
  har_forecast      — model odniesienia HAR (Corsi 2009), ktory trzeba pobic
  naive_forecast    — model odniesienia "jutro jak dzis"

MODELE ODNIESIENIA SA TU NAJWAZNIEJSZE
Zmiennosc jest tak uporczywa, ze zwykle "jutro bedzie jak dzis" osiaga
korelacje 0.7 i wyzej. Kazdy model, ktory tego nie bije, jest bezuzyteczny —
niezaleznie od tego, jak dobrze wyglada sam w sobie.
"""
import numpy as np
import pandas as pd

TRADING_DAYS = 252

# Okna kaskady HAR: dzien, tydzien, miesiac, kwartal.
HAR_WINDOWS = (1, 5, 22, 66)

# Horyzonty prognozy zmiennosci (w sesjach)
VOL_HORIZONS = (1, 5, 10, 22)


def realized_vol(returns, window, annualize=True):
    """
    Zmiennosc zrealizowana: pierwiastek ze sredniej kwadratow zwrotow.

    UWAGA NA JEDNOSTKI. W features.py `ret_1` jest UŁAMKIEM (0.005 = pol
    procenta), podczas gdy ret_5, ret_10 i reszta sa w PROCENTACH. Ta
    niespojnosc siedzi tam od poczatku i jest kompensowana ad hoc w miejscach,
    ktore z ret_1 korzystaja (vol_20 mnozy przez 100). Tutaj przeliczamy
    jawnie, zeby wynik byl w procentach rocznie — inaczej wychodzi zmiennosc
    rzedu 0.1% zamiast 15%.
    """
    rv = np.sqrt((returns ** 2).rolling(window).mean())
    return rv * np.sqrt(TRADING_DAYS) if annualize else rv


def _pct_returns(df):
    """Zwroty dzienne w procentach, niezaleznie od tego, jak trzyma je features.py."""
    r = df["ret_1"]
    # jesli typowy zwrot jest mniejszy niz 0.2, to sa ulamki, nie procenty
    return r * 100.0 if r.abs().median() < 0.2 else r


def add_vol_features(df):
    """
    Cechy do prognozowania zmiennosci.

    Trzon to kaskada okien z modelu HAR: zmiennosc dzienna, tygodniowa,
    miesieczna i kwartalna wchodza osobno, bo rynek reaguje w roznych skalach
    czasu — inaczej dziala trader dzienny, inaczej fundusz przebudowujacy
    portfel raz na kwartal.
    """
    out = df.copy()
    r = _pct_returns(out)

    # --- kaskada HAR ---
    for w in HAR_WINDOWS:
        out[f"rv_{w}"] = realized_vol(r, w)
        out[f"log_rv_{w}"] = np.log(out[f"rv_{w}"].clip(lower=0.1))

    # --- tempo zmian: czy zmiennosc rosnie czy opada ---
    out["rv_ratio_1_5"] = out["rv_1"] / out["rv_5"]
    out["rv_ratio_5_22"] = out["rv_5"] / out["rv_22"]
    out["rv_ratio_22_66"] = out["rv_22"] / out["rv_66"]
    out["rv_change_5"] = out["rv_5"].pct_change(5) * 100

    # --- asymetria (efekt dzwigni) ---
    # Spadki podnosza zmiennosc mocniej niz wzrosty tej samej wielkosci.
    # To jeden z najlepiej udokumentowanych efektow w finansach.
    neg = r.clip(upper=0)
    pos = r.clip(lower=0)
    out["semivol_down_22"] = realized_vol(neg, 22)
    out["semivol_up_22"] = realized_vol(pos, 22)
    out["semivol_ratio"] = out["semivol_down_22"] / out["semivol_up_22"].replace(0, np.nan)

    # --- zmiennosc zmiennosci ---
    out["vol_of_vol_22"] = out["rv_5"].rolling(22).std()

    # --- pozycja w rozkladzie historycznym ---
    out["rv_percentile_252"] = out["rv_22"].rolling(252).rank(pct=True)
    out["rv_zscore_252"] = (
        (out["rv_22"] - out["rv_22"].rolling(252).mean())
        / out["rv_22"].rolling(252).std()
    )

    # --- zakres wewnatrzdzienny jako niezalezna miara ---
    # Parkinson: wykorzystuje high-low, jest dokladniejszy niz sam close-to-close
    hl = np.log(out["high"] / out["low"])
    out["parkinson_22"] = np.sqrt(
        (hl ** 2).rolling(22).mean() / (4 * np.log(2))
    ) * np.sqrt(TRADING_DAYS) * 100
    out["parkinson_ratio"] = out["parkinson_22"] / out["rv_22"].replace(0, np.nan)

    # --- luki otwarcia jako sygnal napiecia ---
    out["gap_vol_22"] = realized_vol(out["gap"], 22)  # gap jest juz w procentach

    return out


def add_vol_targets(df, horizons=VOL_HORIZONS):
    """
    Cele: zmiennosc zrealizowana w NASTEPNYCH h sesjach.

    To jest odpowiednik fwd_* dla kierunku — wartosc, ktorej w chwili
    prognozy jeszcze nie znamy.
    """
    out = df.copy()
    r = _pct_returns(out)
    for h in horizons:
        # srednia kwadratow zwrotow w oknie [t+1, t+h]
        fut = (r ** 2).shift(-h).rolling(h).mean()
        out[f"fwd_rv_{h}"] = np.sqrt(fut) * np.sqrt(TRADING_DAYS)
        out[f"log_fwd_rv_{h}"] = np.log(out[f"fwd_rv_{h}"].clip(lower=0.1))
    return out


def naive_forecast(df, horizon):
    """Model odniesienia: zmiennosc w kolejnych h sesjach bedzie jak w ostatnich h."""
    return df[f"rv_{horizon}"] if f"rv_{horizon}" in df.columns else realized_vol(df["ret_1"], horizon)


def har_fit(train, horizon):
    """
    Dopasowuje model HAR na logarytmach zmiennosci.

    log(RV_przyszla) = a + b1*log(RV_dzien) + b2*log(RV_tydzien) + b3*log(RV_miesiac)

    Logarytmy, bo rozklad zmiennosci jest silnie prawoskosny — w skali
    logarytmicznej robi sie niemal normalny, a regresja liniowa dziala.
    """
    cols = ["log_rv_1", "log_rv_5", "log_rv_22"]
    target = f"log_fwd_rv_{horizon}"
    sub = train[cols + [target]].dropna()
    if len(sub) < 100:
        return None
    X = np.column_stack([np.ones(len(sub))] + [sub[c].to_numpy() for c in cols])
    y = sub[target].to_numpy()
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    return coef


def har_predict(df, coef):
    """Prognoza HAR w skali logarytmicznej, zwracana jako zmiennosc."""
    if coef is None:
        return pd.Series(np.nan, index=df.index)
    cols = ["log_rv_1", "log_rv_5", "log_rv_22"]
    X = np.column_stack([np.ones(len(df))] + [df[c].to_numpy() for c in cols])
    return pd.Series(np.exp(X @ coef), index=df.index)


def build_vol(db_path=None):
    """Pelny zestaw: ceny, cechy kierunkowe, cechy i cele zmiennosci."""
    from features import build

    df, problems = build() if db_path is None else build(db_path)
    df = add_vol_features(df)
    df = add_vol_targets(df)
    return df, problems


if __name__ == "__main__":
    df, problems = build_vol()
    print(f"Wczytano {len(df)} sesji, kolumn: {len(df.columns)}")
    print(f"Walidacja: {problems if problems else 'czysto'}")
    vol_cols = [c for c in df.columns if c.startswith(("rv_", "log_rv", "semivol",
                                                        "parkinson", "fwd_rv", "vol_of_vol"))]
    print(f"Kolumny zmiennosci ({len(vol_cols)}): {', '.join(vol_cols[:12])} ...")
