"""
Model prognozy zmiennosci: HAR rozszerzony o to, czego HAR nie widzi.

CO HAR JUZ ROBI DOBRZE
Kaskada okien (dzien, tydzien, miesiac) lapie uporczywosc zmiennosci i to,
ze rynek reaguje w roznych skalach czasu. Na naszych danych daje korelacje
0.63 i R^2 0.27 w walidacji kroczacej. To jest poprzeczka.

CZEGO HAR NIE WIDZI
  asymetria       spadki podnosza zmiennosc mocniej niz wzrosty tej samej
                  wielkosci (efekt dzwigni) — HAR traktuje je identycznie
  zakres dnia     high-low niesie informacje, ktorej nie ma w samych
                  zamknieciach; estymator Parkinsona jest z tego powodu
                  dokladniejszy przy tej samej liczbie obserwacji
  rezim           ta sama zmiennosc znaczy co innego, gdy jest w 10.
                  percentylu roku, a co innego w 90.
  luki otwarcia   napiecie przenoszone przez noc, niewidoczne w zwrotach
                  liczonych od zamkniecia do zamkniecia

REGULARYZACJA
Wiecej cech to wieksze ryzyko dopasowania do szumu, wiec zamiast zwyklej
regresji uzywamy grzbietowej (ridge). Sciaga wspolczynniki do zera tym
mocniej, im slabsze poparcie w danych — ta sama idea, co korekta bayesowska
w prognozie kierunku, tylko zastosowana do regresji.
"""
import numpy as np
import pandas as pd

# Cechy modelu rozszerzonego. Kolejnosc ma znaczenie tylko dla czytelnosci
# wspolczynnikow przy diagnostyce.
BASE_FEATURES = ["log_rv_1", "log_rv_5", "log_rv_22"]

EXTRA_FEATURES = [
    "log_rv_66",          # kwartalna kaskada — dluzsza pamiec niz w klasycznym HAR
    "semivol_ratio",      # asymetria: ile zmiennosci pochodzi ze spadkow
    "parkinson_ratio",    # ile mowi zakres dnia ponad to, co mowia zamkniecia
    "rv_ratio_1_5",       # czy zmiennosc wlasnie przyspiesza
    "rv_ratio_5_22",
    "rv_percentile_252",  # rezim: gdzie jestesmy w rozkladzie roku
    "vol_of_vol_22",      # jak niestabilna jest sama zmiennosc
    "gap_vol_22",         # napiecie przenoszone przez noc
]

RIDGE_ALPHA = 1.0


def _design(df, features):
    X = np.column_stack([np.ones(len(df))] + [df[c].to_numpy(dtype=float) for c in features])
    return X


def fit(train, horizon, features=None, alpha=RIDGE_ALPHA):
    """
    Dopasowuje model regresji grzbietowej na logarytmie zmiennosci.

    Standaryzujemy cechy, bo maja rozne skale (percentyl 0-1, logarytm
    zmiennosci okolo 2-4), a kara grzbietowa traktuje wszystkie jednakowo.
    """
    features = features or (BASE_FEATURES + EXTRA_FEATURES)
    target = f"log_fwd_rv_{horizon}"
    cols = features + [target]
    sub = train[cols].replace([np.inf, -np.inf], np.nan).dropna()
    if len(sub) < 200:
        return None

    Xraw = np.column_stack([sub[c].to_numpy(dtype=float) for c in features])
    mu, sd = Xraw.mean(axis=0), Xraw.std(axis=0)
    sd[sd == 0] = 1.0
    X = np.column_stack([np.ones(len(sub)), (Xraw - mu) / sd])
    y = sub[target].to_numpy(dtype=float)

    # kara nie dotyczy wyrazu wolnego
    penalty = np.eye(X.shape[1]) * alpha
    penalty[0, 0] = 0.0
    coef = np.linalg.solve(X.T @ X + penalty, X.T @ y)

    return {"coef": coef, "mu": mu, "sd": sd, "features": features}


def predict(df, model):
    """Prognoza zmiennosci (nie logarytmu) dla kazdego wiersza."""
    if model is None:
        return pd.Series(np.nan, index=df.index)
    f = model["features"]
    Xraw = np.column_stack([df[c].to_numpy(dtype=float) for c in f])
    X = np.column_stack([np.ones(len(df)), (Xraw - model["mu"]) / model["sd"]])
    with np.errstate(over="ignore", invalid="ignore"):
        out = np.exp(X @ model["coef"])
    return pd.Series(out, index=df.index)


def feature_importance(model):
    """Wspolczynniki przy cechach standaryzowanych — porownywalne miedzy soba."""
    if model is None:
        return []
    return sorted(
        zip(model["features"], model["coef"][1:]),
        key=lambda kv: -abs(kv[1]),
    )
