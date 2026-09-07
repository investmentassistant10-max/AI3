"""
Warstwa cech: wczytuje lokalną bazę cen i liczy wskaźniki, na których
operują strategie.

Zasada nadrzędna: żadna cecha na dzień t nie może używać informacji
z dnia t+1 lub późniejszych. Zwroty "w przód" (fwd_*) są policzone osobno
i służą WYŁĄCZNIE jako wynik do oceny, nigdy jako wejście strategii.
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "spx_daily.sqlite"

# Horyzonty, na których mierzymy skutek sygnału (w dniach sesyjnych)
FORWARD_HORIZONS = (1, 2, 3, 5, 10)

# Długości średnich kroczących, względem których liczymy odchylenie
SMA_WINDOWS = (5, 10, 20, 50, 200)


def load_prices(db_path=DB_PATH):
    """Wczytuje ceny z SQLite do DataFrame posortowanego po dacie."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT date, open, high, low, close, volume FROM spx_daily ORDER BY date",
        conn,
        parse_dates=["date"],
    )
    conn.close()
    df = df.set_index("date")
    return df


def validate(df):
    """Podstawowe sanity checki. Zwraca listę problemów (pusta = czysto)."""
    problems = []

    if df.index.duplicated().any():
        problems.append(f"zduplikowane daty: {df.index[df.index.duplicated()].tolist()[:5]}")

    if df[["open", "high", "low", "close"]].isna().any().any():
        problems.append("braki w OHLC")

    bad_hl = df[df["high"] < df["low"]]
    if len(bad_hl):
        problems.append(f"high < low w {len(bad_hl)} wierszach")

    bad_range = df[(df["close"] > df["high"]) | (df["close"] < df["low"])]
    if len(bad_range):
        problems.append(f"close poza zakresem high-low w {len(bad_range)} wierszach")

    ret = df["close"].pct_change()
    extreme = ret[ret.abs() > 0.25]
    if len(extreme):
        problems.append(f"zwroty dzienne >25%: {extreme.index.strftime('%Y-%m-%d').tolist()}")

    gaps = df.index.to_series().diff().dt.days
    big_gaps = gaps[gaps > 7]
    if len(big_gaps):
        problems.append(f"przerwy >7 dni w danych: {len(big_gaps)} sztuk, np. {big_gaps.index[:3].strftime('%Y-%m-%d').tolist()}")

    return problems


def add_features(df):
    """Dodaje kolumny z cechami. Wszystkie liczone wyłącznie z przeszłości."""
    out = df.copy()
    close = out["close"]

    # --- podstawowe zwroty ---
    out["ret_1"] = close.pct_change()

    # --- odchylenie od średnich kroczących ---
    for n in SMA_WINDOWS:
        sma = close.rolling(n).mean()
        out[f"sma_{n}"] = sma
        # odchylenie w procentach
        out[f"dist_sma_{n}"] = (close - sma) / sma * 100.0
        # to samo, ale w jednostkach lokalnej zmienności — porównywalne
        # między spokojnym 2017 a zwariowanym 2020
        vol = out["ret_1"].rolling(n).std()
        out[f"z_sma_{n}"] = (close / sma - 1.0) / (vol * np.sqrt(n))

    # --- zmienność ---
    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr_14"] = true_range.rolling(14).mean() / close * 100.0
    out["vol_20"] = out["ret_1"].rolling(20).std() * 100.0
    out["vol_regime"] = out["vol_20"] / out["vol_20"].rolling(252).mean()

    # --- struktura ruchu ---
    out["range_pct"] = (out["high"] - out["low"]) / close * 100.0
    out["gap"] = (out["open"] - prev_close) / prev_close * 100.0

    # seria kolejnych dni w tym samym kierunku (dodatnia = wzrosty)
    sign = np.sign(out["ret_1"].fillna(0.0))
    streak = np.zeros(len(sign), dtype=int)
    for i in range(1, len(sign)):
        s = sign.iloc[i]
        if s == 0:
            streak[i] = 0
        elif s == sign.iloc[i - 1]:
            streak[i] = streak[i - 1] + int(s)
        else:
            streak[i] = int(s)
    out["streak"] = streak

    # --- RSI 14 (klasyczny, wygładzanie Wildera) ---
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out["rsi_14"] = 100 - 100 / (1 + rs)

    # --- pozycja względem lokalnych ekstremów ---
    out["dist_high_252"] = (close / close.rolling(252).max() - 1.0) * 100.0
    out["dist_low_252"] = (close / close.rolling(252).min() - 1.0) * 100.0

    # --- wolumen ---
    out["vol_ratio"] = out["volume"] / out["volume"].rolling(20).mean()

    return out


def add_forward_returns(df, horizons=FORWARD_HORIZONS):
    """
    Dodaje zwroty w przód — to jest WYNIK, nigdy wejście strategii.
    fwd_k = zwrot z zamknięcia dnia t do zamknięcia dnia t+k, w procentach.
    """
    out = df.copy()
    close = out["close"]
    for k in horizons:
        out[f"fwd_{k}"] = (close.shift(-k) / close - 1.0) * 100.0
    return out


def build(db_path=DB_PATH):
    """Pełny pipeline: wczytaj -> zwaliduj -> policz cechy -> dodaj wyniki."""
    df = load_prices(db_path)
    problems = validate(df)
    df = add_features(df)
    df = add_forward_returns(df)
    return df, problems


if __name__ == "__main__":
    df, problems = build()
    print(f"Wczytano {len(df)} dni: {df.index.min().date()} -> {df.index.max().date()}")
    if problems:
        print("\nUWAGI DO DANYCH:")
        for p in problems:
            print(f"  - {p}")
    else:
        print("Walidacja: czysto.")
    print(f"\nKolumny ({len(df.columns)}): {', '.join(df.columns)}")
