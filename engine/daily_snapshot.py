"""
Migawka na dzis: wartosci wszystkich cech dla ostatniej sesji plus ceny
z ostatnich sesji. Dashboard czyta to i sam sprawdza, ktore strategie maja
dzis zastosowanie.

Dlaczego tak, a nie inaczej: dashboard moglby liczyc wskazniki sam
w JavaScripcie, ale wtedy ta sama logika istnialaby w dwoch miejscach
i predzej czy pozniej by sie rozjechala. Python liczy raz, dashboard tylko
porownuje liczby z progami.

Uzycie:
    python3 daily_snapshot.py            # policz i wyslij do Firestore
    python3 daily_snapshot.py --local    # tylko zapisz do pliku JSON
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
KEY_PATH = ROOT / "firebase" / "serviceAccountKey.json"
LOCAL_OUT = ROOT / "data" / "daily_snapshot.json"

RECENT_SESSIONS = 10

# Cechy nieuzywane przez strategie (ceny surowe, zwroty w przod) pomijamy.
SKIP_PREFIXES = ("fwd_",)
SKIP_EXACT = {"open", "high", "low", "close", "volume"}


def build_snapshot(recent=RECENT_SESSIONS):
    from features import build

    df, problems = build()
    last = df.iloc[-1]
    last_date = df.index[-1]

    features = {}
    for col in df.columns:
        if col in SKIP_EXACT or col.startswith(SKIP_PREFIXES):
            continue
        val = last[col]
        if val is None or (isinstance(val, float) and not np.isfinite(val)):
            continue
        features[col] = round(float(val), 6)

    tail = df.iloc[-recent:]
    sessions = [
        {
            "date": idx.strftime("%Y-%m-%d"),
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
        }
        for idx, row in tail.iterrows()
    ]

    return {
        "date": last_date.strftime("%Y-%m-%d"),
        "last_close": round(float(last["close"]), 2),
        "atr_14_pct": round(float(last["atr_14"]), 4) if np.isfinite(last["atr_14"]) else None,
        "vol_percentile": round(float(last["vol_percentile"]), 4)
        if np.isfinite(last["vol_percentile"]) else None,
        "features": features,
        "recent_sessions": sessions,
        "data_warnings": problems,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def push(snapshot):
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(str(KEY_PATH)))
    db = firestore.client()
    db.collection("daily_snapshot").document("latest").set(snapshot)
    db.collection("daily_snapshot").document(snapshot["date"]).set(snapshot)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="tylko plik, bez Firestore")
    args = ap.parse_args()

    snap = build_snapshot()
    LOCAL_OUT.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_OUT.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Migawka na {snap['date']}: zamkniecie {snap['last_close']}, "
          f"{len(snap['features'])} cech, {len(snap['recent_sessions'])} sesji")
    print(f"Zapisano lokalnie: {LOCAL_OUT}")

    if not args.local:
        push(snap)
        print("Wyslano do Firestore: daily_snapshot/latest")


if __name__ == "__main__":
    main()
