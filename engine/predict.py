"""
Predykcja na najblizsze sesje + log tego, co system twierdzil, i rozliczenie
po fakcie.

DLACZEGO TO LICZY PYTHON, A NIE PANEL
Wczesniej predykcje liczyl dashboard w JavaScripcie. To znaczylo, ze logika
istniala w dwoch jezykach i nikt nie zapisywal, co system twierdzil danego
dnia. Teraz Python liczy raz, zapisuje do logu i wypycha gotowy wynik —
panel go tylko pokazuje. Log powstaje przy okazji, za darmo.

DWIE RZECZY, KTORE LATWO POMYLIC
1. `edge_mean` to PRZEWAGA nad typowym dniem, nie prognoza ruchu ceny.
   Prognoza to `mean` — sredni zwrot po sygnale. Przy baseline +0.04%
   i przewadze +0.30% cena ma isc o +0.34%, nie o +0.30%.
2. Kazdy horyzont liczymy osobno. Strategia dziesieciodniowa mowi o ruchu
   w ciagu dziesieciu sesji i nie da sie jej usrednic z jednodniowa.

ROZLICZENIE
Kazda predykcja jest po fakcie porownywana z rzeczywistoscia na dwa sposoby:
  close-to-close  — porownywalny z tym, co mierzyl silnik
  open-to-close   — wykonalny naprawde (wejscie na otwarciu nastepnej sesji)
Roznica miedzy nimi to cena, ktora placimy za to, ze sygnal znamy dopiero
po zamknieciu.

Uzycie:
    python3 predict.py              # policz predykcje na dzis, zapisz, wyslij
    python3 predict.py --local      # bez wysylania do Firestore
    python3 predict.py --settle     # tylko rozlicz zalegle predykcje
    python3 predict.py --score      # pokaz skutecznosc dotychczasowych
"""
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
STRATEGY_DB = ROOT / "data" / "strategies.sqlite"
KEY_PATH = ROOT / "firebase" / "serviceAccountKey.json"

HORIZONS = (1, 2, 3, 5, 10)
MIN_RATING = 40.0

# Ile PASUJACYCH strategii bierzemy do predykcji. To nie jest limit na
# przeszukiwanie bazy — te sprawdzamy w calosci.
MATCHED_LIMIT = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    date            TEXT,
    horizon         INTEGER,
    made_at         TEXT,
    n_matched       INTEGER,
    n_long          INTEGER,
    n_short         INTEGER,
    weight_long     REAL,
    weight_short    REAL,
    expected_move   REAL,   -- prognoza ruchu ceny (srednia z `mean`)
    edge_mean       REAL,   -- przewaga nad baseline (do oceny jakosci)
    confidence      REAL,
    agreement       REAL,
    avg_rating      REAL,
    vol_percentile  REAL,
    close_price     REAL,   -- zamkniecie dnia predykcji
    strategy_ids    TEXT,
    -- rozliczenie
    actual_close_to_close REAL,
    actual_open_to_close  REAL,
    direction_correct     INTEGER,
    settled_at            TEXT,
    PRIMARY KEY (date, horizon)
);
CREATE INDEX IF NOT EXISTS idx_pred_settled ON predictions(settled_at);
"""


def get_db():
    from search import ensure_schema

    conn = sqlite3.connect(STRATEGY_DB)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)      # kolumny dokladane do schematu strategii
    conn.executescript(SCHEMA)  # tabela predictions
    conn.commit()
    return conn


def load_strategies(conn, min_rating=MIN_RATING):
    """
    Wszystkie strategie-kandydaci, nie tylko czolowka.

    Wczesniej bralismy 500 najlepszych i dopiero potem sprawdzali, ktore
    pasuja do dzisiejszego rynku. Kolejnosc byla odwrotna niz trzeba: czolowka
    rankingu to warianty jednego pomyslu, wiec kiedy on nie pasowal, wychodzilo
    zero — mimo ze setki innych strategii mialy spelnione warunki, tylko lezaly
    nizej w rankingu.
    """
    return conn.execute(
        """SELECT id, definition, description, horizon, rating, mean, base_mean,
                  edge_mean, hit_rate, base_hit_rate, n_episodes, t_stat,
                  placebo_p, treasury_status
           FROM strategies
           WHERE status='candidate' AND rating >= ?
           ORDER BY rating DESC""",
        (min_rating,),
    ).fetchall()


def matches(features, conditions):
    """Czy dzisiejszy stan rynku spelnia warunki strategii."""
    if not conditions:
        return False
    for c in conditions:
        v = features.get(c["feature"] if isinstance(c, dict) else c[0])
        op = c["op"] if isinstance(c, dict) else c[1]
        th = c["threshold"] if isinstance(c, dict) else c[2]
        if v is None or not np.isfinite(v):
            return False
        if op == "<" and not (v < th):
            return False
        if op == ">" and not (v > th):
            return False
    return True


def predict_for_horizon(rows, vol_percentile):
    """
    Prognoza dla jednego horyzontu. Wagą jest rating.

    Pewnosc to iloczyn czterech czynnikow. Ostatni — rezim zmiennosci — nie
    jest ozdobnikiem: na danych 2000-2026 przewaga sygnalow kontrarianskich
    rosnie monotonicznie ze zmiennoscia (0.09% przy spokoju wobec 0.54% przy
    panice). Bez tego czynnika system melodowalby najwyzsza pewnosc dokladnie
    wtedy, gdy sygnaly zarabiaja najmniej.
    """
    if not rows:
        return None

    w = np.array([r["rating"] / 100.0 for r in rows])
    means = np.array([r["mean"] if r["mean"] is not None else 0.0 for r in rows])
    edges = np.array([r["edge_mean"] if r["edge_mean"] is not None else 0.0 for r in rows])

    is_long = edges > 0
    w_long, w_short = float(w[is_long].sum()), float(w[~is_long].sum())
    w_all = w_long + w_short

    expected_move = float((means * w).sum() / w.sum())
    edge_weighted = float((edges * w).sum() / w.sum())
    agreement = abs(w_long - w_short) / w_all if w_all > 0 else 0.0
    avg_rating = float(np.mean([r["rating"] for r in rows]))

    count_factor = min(len(rows) / 8.0, 1.0)
    rating_factor = min(avg_rating / 85.0, 1.0)
    vol_factor = 0.7 if vol_percentile is None else 0.55 + 0.45 * vol_percentile
    confidence = round(agreement * count_factor * rating_factor * vol_factor * 100, 1)

    # szczegoly do pokazania w panelu — panel widzi w Firestore tylko top 100
    # strategii wg ratingu, a te prawie nigdy nie sa tymi, ktore dzis pasuja
    detail = sorted(rows, key=lambda r: -(r["rating"] or 0))[:25]
    strategies_detail = [{
        "description": r["description"],
        "rating": r["rating"],
        "mean": r["mean"],
        "edge_mean": r["edge_mean"],
        "hit_rate": r["hit_rate"],
        "base_hit_rate": r["base_hit_rate"],
        "n_episodes": r["n_episodes"],
        "t_stat": r["t_stat"],
        "direction": "long" if (r["edge_mean"] or 0) > 0 else "short",
    } for r in detail]

    return {
        "n_matched": len(rows),
        "strategies": strategies_detail,
        "n_long": int(is_long.sum()),
        "n_short": int((~is_long).sum()),
        "weight_long": round(w_long, 3),
        "weight_short": round(w_short, 3),
        "expected_move": round(expected_move, 4),
        "edge_mean": round(edge_weighted, 4),
        "confidence": confidence,
        "agreement": round(agreement, 3),
        "avg_rating": round(avg_rating, 1),
        "strategy_ids": [r["id"] for r in rows],
    }


def make_prediction(quiet=False):
    from features import build

    df, _ = build()
    last = df.iloc[-1]
    date = df.index[-1].strftime("%Y-%m-%d")
    vol_p = float(last["vol_percentile"]) if np.isfinite(last["vol_percentile"]) else None

    features = {
        c: float(last[c]) for c in df.columns
        if not c.startswith("fwd_") and np.isfinite(last[c])
    }

    conn = get_db()
    strategies = load_strategies(conn)
    if not strategies:
        print("Brak strategii w bazie. Uruchom najpierw silnik.")
        return None

    # najpierw dopasowanie do dzisiejszego rynku, potem ranking
    matched = [
        r for r in strategies
        if matches(features, json.loads(r["definition"]).get("conditions", []))
    ]
    if len(matched) > MATCHED_LIMIT:
        matched = matched[:MATCHED_LIMIT]   # juz posortowane po ratingu

    now = datetime.now(timezone.utc).isoformat()
    out = {"date": date, "made_at": now, "close_price": round(float(last["close"]), 2),
           "vol_percentile": vol_p,
           "n_strategies_considered": len(strategies),
           "n_strategies_matched": len(matched),
           "horizons": {}}

    for h in HORIZONS:
        rows = [r for r in matched if r["horizon"] == h]
        p = predict_for_horizon(rows, vol_p)
        if p is None:
            continue
        out["horizons"][str(h)] = p
        conn.execute(
            """INSERT OR REPLACE INTO predictions
               (date, horizon, made_at, n_matched, n_long, n_short, weight_long,
                weight_short, expected_move, edge_mean, confidence, agreement,
                avg_rating, vol_percentile, close_price, strategy_ids)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (date, h, now, p["n_matched"], p["n_long"], p["n_short"],
             p["weight_long"], p["weight_short"], p["expected_move"], p["edge_mean"],
             p["confidence"], p["agreement"], p["avg_rating"], vol_p,
             out["close_price"], json.dumps(p["strategy_ids"])),
        )

    conn.commit()
    conn.close()

    if not quiet:
        print(f"Predykcja na {date} (zamkniecie {out['close_price']}):")
        print(f"  sprawdzono {len(strategies):,} strategii, warunki spelnia {len(matched):,}")
        if not out["horizons"]:
            print("  zadna strategia nie ma dzis zastosowania")
        for h, p in out["horizons"].items():
            arrow = "^" if p["expected_move"] > 0 else "v" if p["expected_move"] < 0 else "-"
            print(f"  {h}D {arrow} {p['expected_move']:+.3f}%  "
                  f"(przewaga {p['edge_mean']:+.3f}%, pewnosc {p['confidence']:.0f}%, "
                  f"{p['n_matched']} strategii: {p['n_long']}L/{p['n_short']}S)")
    return out


def settle(quiet=False):
    """Rozlicza predykcje, dla ktorych sa juz dane."""
    from features import build

    df, _ = build()
    closes = df["close"]
    opens = df["open"]
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    pos = {d: i for i, d in enumerate(dates)}

    conn = get_db()
    pending = conn.execute(
        "SELECT * FROM predictions WHERE settled_at IS NULL ORDER BY date, horizon"
    ).fetchall()

    settled = 0
    now = datetime.now(timezone.utc).isoformat()

    for p in pending:
        i = pos.get(p["date"])
        if i is None:
            continue
        h = p["horizon"]
        # potrzebujemy dnia wejscia (i+1) i dnia wyjscia (i+h)
        if i + h >= len(df) or i + 1 >= len(df):
            continue

        c2c = (float(closes.iloc[i + h]) / float(closes.iloc[i]) - 1.0) * 100.0
        o2c = (float(closes.iloc[i + h]) / float(opens.iloc[i + 1]) - 1.0) * 100.0
        correct = int(np.sign(c2c) == np.sign(p["expected_move"])) if p["expected_move"] else None

        conn.execute(
            """UPDATE predictions SET actual_close_to_close=?, actual_open_to_close=?,
               direction_correct=?, settled_at=? WHERE date=? AND horizon=?""",
            (round(c2c, 4), round(o2c, 4), correct, now, p["date"], h),
        )
        settled += 1

    conn.commit()
    conn.close()
    if not quiet:
        print(f"Rozliczono {settled} predykcji.")
    return settled


def score():
    """Skutecznosc dotychczasowych predykcji — jedyny naprawde uczciwy test."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM predictions WHERE settled_at IS NOT NULL"
    ).fetchall()

    if not rows:
        print("Brak rozliczonych predykcji. To normalne na poczatku —")
        print("pierwsze wyniki pojawia sie po kilku sesjach.")
        conn.close()
        return

    print("=" * 86)
    print("SKUTECZNOSC PREDYKCJI — na danych, ktorych system nie widzial, gdy je stawial")
    print("=" * 86)
    print(f"{'horyzont':>9} {'predykcji':>10} {'trafnosc':>9} {'prognoza%':>11} "
          f"{'faktyczne%':>11} {'blad%':>9}")
    print("-" * 86)

    for h in HORIZONS:
        sub = [r for r in rows if r["horizon"] == h]
        if not sub:
            continue
        hits = [r["direction_correct"] for r in sub if r["direction_correct"] is not None]
        pred = np.array([r["expected_move"] for r in sub])
        act = np.array([r["actual_close_to_close"] for r in sub])
        acc = np.mean(hits) * 100 if hits else float("nan")
        print(f"{h:>8}D {len(sub):>10} {acc:>8.1f}% {pred.mean():>11.3f} "
              f"{act.mean():>11.3f} {np.abs(pred - act).mean():>9.3f}")

    print("-" * 86)
    all_hits = [r["direction_correct"] for r in rows if r["direction_correct"] is not None]
    if all_hits:
        print(f"\nLacznie: {np.mean(all_hits)*100:.1f}% trafnosci kierunku "
              f"na {len(all_hits)} predykcjach")
        print("\nUwaga: przy malej liczbie predykcji ta liczba jest bardzo niestabilna.")
        print("Sensowne wnioski zaczynaja sie od jakichs 100 rozliczonych predykcji.")
    conn.close()


def scoreboard():
    """Podsumowanie skutecznosci — do pokazania w panelu."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM predictions WHERE settled_at IS NOT NULL"
    ).fetchall()
    conn.close()
    if not rows:
        return {"n_settled": 0, "by_horizon": {}, "overall_accuracy": None}

    out = {"n_settled": len(rows), "by_horizon": {}}
    for h in HORIZONS:
        sub = [r for r in rows if r["horizon"] == h]
        if not sub:
            continue
        hits = [r["direction_correct"] for r in sub if r["direction_correct"] is not None]
        pred = np.array([r["expected_move"] for r in sub])
        act = np.array([r["actual_close_to_close"] for r in sub])
        out["by_horizon"][str(h)] = {
            "n": len(sub),
            "accuracy": round(float(np.mean(hits)) * 100, 1) if hits else None,
            "predicted_mean": round(float(pred.mean()), 4),
            "actual_mean": round(float(act.mean()), 4),
            "mean_abs_error": round(float(np.abs(pred - act).mean()), 4),
        }
    all_hits = [r["direction_correct"] for r in rows if r["direction_correct"] is not None]
    out["overall_accuracy"] = round(float(np.mean(all_hits)) * 100, 1) if all_hits else None
    out["updated_at"] = datetime.now(timezone.utc).isoformat()
    return out


def push(prediction):
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(str(KEY_PATH)))
    db = firestore.client()
    db.collection("predictions").document("latest").set(prediction)
    db.collection("predictions").document(prediction["date"]).set(prediction)
    db.collection("predictions").document("scoreboard").set(scoreboard())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="bez wysylania do Firestore")
    ap.add_argument("--settle", action="store_true", help="tylko rozlicz zalegle")
    ap.add_argument("--score", action="store_true", help="pokaz skutecznosc")
    args = ap.parse_args()

    if args.score:
        score()
        return
    if args.settle:
        settle()
        return

    settle(quiet=True)
    pred = make_prediction()
    if pred and not args.local:
        push(pred)
        print("Wyslano do Firestore: predictions/latest")


if __name__ == "__main__":
    main()
