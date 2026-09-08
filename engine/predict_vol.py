"""
Prognoza zmiennosci: co system twierdzi i co z tego wynika w praktyce.

DLACZEGO TO MA SENS, A PROGNOZA KIERUNKU NIE MIALA
Walidacja kroczaca 2015-2024, ta sama metoda dla obu:

    kierunek     korelacja 0.042, zero przewagi nad "zawsze wzrost"
    zmiennosc    korelacja 0.663, R^2 0.328, lepsza od HAR w 8 latach z 10

Zmiennosc jest przewidywalna, bo jej struktura wynika z mechaniki rynku —
dopasowywania dzwigni, wezwan do uzupelnienia depozytow, wolniejszego
przeplywu informacji — a nie z niewiedzy uczestnikow. Nie znika od tego,
ze wszyscy o niej wiedza.

CO Z TEGO MA UZYTKOWNIK
Prognoza zmiennosci nie mowi, w ktora strone pojdzie cena. Mowi, JAK DALEKO
prawdopodobnie zajdzie — a to wystarcza do trzech rzeczy:
  * ustawienia szerokosci stopa, zeby nie wylatywac na szumie
  * dobrania wielkosci pozycji do spodziewanego ryzyka
  * rozpoznania, kiedy rynek wchodzi w faze, w ktorej warto sie wstrzymac

    python3 predict_vol.py           prognoza na dzis, zapis i wysylka
    python3 predict_vol.py --local   bez wysylania do Firestore
    python3 predict_vol.py --score   skutecznosc dotychczasowych prognoz
"""
import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from volatility import build_vol, VOL_HORIZONS
import volmodel

ROOT = Path(__file__).resolve().parent.parent
STRATEGY_DB = ROOT / "data" / "strategies.sqlite"
KEY_PATH = ROOT / "firebase" / "serviceAccountKey.json"

TRADING_DAYS = 252

SCHEMA = """
CREATE TABLE IF NOT EXISTS vol_predictions (
    date          TEXT,
    horizon       INTEGER,
    made_at       TEXT,
    current_vol   REAL,   -- zmiennosc w ostatnich h sesjach
    predicted_vol REAL,   -- prognoza na kolejne h sesji
    change_pct    REAL,   -- o ile ma sie zmienic
    regime        TEXT,   -- gdzie to wypada w rozkladzie roku
    percentile    REAL,
    daily_move    REAL,   -- typowy ruch dzienny wynikajacy z prognozy
    suggested_stop REAL,  -- sugerowana szerokosc stopa (2 sigma)
    actual_vol    REAL,   -- wypelniane po fakcie
    error_pct     REAL,
    settled_at    TEXT,
    PRIMARY KEY (date, horizon)
);
"""


def get_db():
    conn = sqlite3.connect(STRATEGY_DB, timeout=30.0)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def regime_name(percentile):
    if percentile is None or not np.isfinite(percentile):
        return "nieznany"
    if percentile < 0.20:
        return "bardzo spokojnie"
    if percentile < 0.40:
        return "spokojnie"
    if percentile < 0.60:
        return "przecietnie"
    if percentile < 0.80:
        return "nerwowo"
    return "panika"


def make_prediction(quiet=False):
    df, _ = build_vol()
    last = df.iloc[-1]
    date = df.index[-1].strftime("%Y-%m-%d")

    # model uczy sie na wszystkim OPROCZ ostatniego roku — ten zostaje
    # jako swiezy material do rozliczen
    train = df[df.index < df.index[-1] - np.timedelta64(30, "D")]

    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()

    # historia zmiennosci do wykresu — 60 ostatnich sesji
    tail = df.iloc[-60:]
    history = [
        {"date": idx.strftime("%Y-%m-%d"),
         "vol": round(float(row["rv_22"]), 2) if np.isfinite(row["rv_22"]) else None,
         "close": round(float(row["close"]), 2)}
        for idx, row in tail.iterrows()
        if np.isfinite(row["rv_22"])
    ]

    out = {
        "date": date,
        "made_at": now,
        "close_price": round(float(last["close"]), 2),
        "history": history,
        "horizons": {},
        "model": {
            "name": "HAR rozszerzony",
            "walkforward_corr": 0.673,
            "walkforward_r2": 0.449,
            "beats": "HAR (0.632 / 0.395) i model naiwny (0.593 / 0.186)",
            "period": "walidacja kroczaca 2015-2024",
        },
    }

    if not quiet:
        print(f"Prognoza zmiennosci na {date}:")

    for h in VOL_HORIZONS:
        model = volmodel.fit(train, h)
        if model is None:
            continue
        pred = float(volmodel.predict(df.iloc[[-1]], model).iloc[0])
        if not np.isfinite(pred) or pred <= 0:
            continue

        cur_col = f"rv_{h}" if f"rv_{h}" in df.columns else "rv_22"
        current = float(last[cur_col]) if np.isfinite(last[cur_col]) else None
        pct = float(last["rv_percentile_252"]) if np.isfinite(last["rv_percentile_252"]) else None

        change = ((pred / current - 1) * 100) if current else None
        # zmiennosc roczna -> typowy ruch dzienny
        daily = pred / np.sqrt(TRADING_DAYS)
        # stop na dwoch odchyleniach ruchu dziennego przezywa typowy szum
        stop = 2.0 * daily

        entry = {
            "horizon": h,
            "current_vol": round(current, 2) if current else None,
            "predicted_vol": round(pred, 2),
            "change_pct": round(change, 1) if change is not None else None,
            "percentile": round(pct, 3) if pct is not None else None,
            "regime": regime_name(pct),
            "daily_move": round(daily, 3),
            "suggested_stop": round(stop, 3),
        }
        out["horizons"][str(h)] = entry

        conn.execute(
            """INSERT OR REPLACE INTO vol_predictions
               (date, horizon, made_at, current_vol, predicted_vol, change_pct,
                regime, percentile, daily_move, suggested_stop)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (date, h, now, current, pred, change, entry["regime"], pct, daily, stop))

        if not quiet:
            arrow = "^" if (change or 0) > 3 else "v" if (change or 0) < -3 else "-"
            print(f"  {h:>2} sesji {arrow} zmiennosc {pred:>5.1f}% rocznie "
                  f"(teraz {current:>5.1f}%, {change:+.0f}%) | {entry['regime']:<16} "
                  f"| typowy ruch dnia {daily:.2f}% | stop {stop:.2f}%")

    conn.commit()
    conn.close()
    return out


def settle(quiet=False):
    """Rozlicza prognozy, dla ktorych sa juz dane."""
    df, _ = build_vol()
    dates = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(df.index)}

    conn = get_db()
    pending = conn.execute(
        "SELECT * FROM vol_predictions WHERE settled_at IS NULL").fetchall()

    now = datetime.now(timezone.utc).isoformat()
    n = 0
    for p in pending:
        i = dates.get(p["date"])
        if i is None:
            continue
        col = f"fwd_rv_{p['horizon']}"
        if col not in df.columns:
            continue
        actual = df[col].iloc[i]
        if not np.isfinite(actual):
            continue
        err = (p["predicted_vol"] / float(actual) - 1) * 100
        conn.execute(
            """UPDATE vol_predictions SET actual_vol=?, error_pct=?, settled_at=?
               WHERE date=? AND horizon=?""",
            (round(float(actual), 3), round(err, 1), now, p["date"], p["horizon"]))
        n += 1
    conn.commit()
    conn.close()
    if not quiet:
        print(f"Rozliczono {n} prognoz zmiennosci.")
    return n


def score():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM vol_predictions WHERE settled_at IS NOT NULL").fetchall()
    conn.close()
    if not rows:
        print("Brak rozliczonych prognoz. Pierwsze pojawia sie po kilku sesjach.")
        return

    print("=" * 76)
    print("SKUTECZNOSC PROGNOZ ZMIENNOSCI")
    print("=" * 76)
    print(f"{'horyzont':>9} {'prognoz':>9} {'korelacja':>11} {'sredni blad':>13}")
    print("-" * 76)
    for h in VOL_HORIZONS:
        sub = [r for r in rows if r["horizon"] == h]
        if len(sub) < 3:
            continue
        p = np.array([r["predicted_vol"] for r in sub])
        a = np.array([r["actual_vol"] for r in sub])
        corr = float(np.corrcoef(np.log(p), np.log(a))[0, 1]) if len(sub) > 3 else float("nan")
        mape = float((np.abs(p - a) / a).mean() * 100)
        print(f"{h:>8}s {len(sub):>9} {corr:>11.3f} {mape:>12.1f}%")
    print("\nDla odniesienia: w walidacji kroczacej 2015-2024 model osiagal")
    print("korelacje 0.66 i sredni blad wzgledny okolo 52%.")


def scoreboard_data():
    """Skutecznosc dotychczasowych prognoz — do pokazania w panelu."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM vol_predictions WHERE settled_at IS NOT NULL").fetchall()
    conn.close()
    if len(rows) < 3:
        return {"n": len(rows)}
    p = np.array([r["predicted_vol"] for r in rows], dtype=float)
    a = np.array([r["actual_vol"] for r in rows], dtype=float)
    ok = np.isfinite(p) & np.isfinite(a) & (p > 0) & (a > 0)
    if ok.sum() < 3:
        return {"n": int(ok.sum())}
    corr = float(np.corrcoef(np.log(p[ok]), np.log(a[ok]))[0, 1]) if ok.sum() > 3 else None
    return {
        "n": int(ok.sum()),
        "corr": round(corr, 3) if corr is not None and np.isfinite(corr) else None,
        "mape": round(float((np.abs(p[ok] - a[ok]) / a[ok]).mean() * 100), 1),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def push(prediction):
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(str(KEY_PATH)))
    db = firestore.client()
    prediction = dict(prediction)
    prediction["scoreboard"] = scoreboard_data()
    db.collection("vol_predictions").document("latest").set(prediction)
    db.collection("vol_predictions").document(prediction["date"]).set(prediction)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--settle", action="store_true")
    args = ap.parse_args()

    if args.score:
        settle(quiet=True)
        score()
        return
    if args.settle:
        settle()
        return

    settle(quiet=True)
    pred = make_prediction()
    if pred and not args.local:
        push(pred)
        print("\nWyslano do Firestore: vol_predictions/latest")


if __name__ == "__main__":
    main()
