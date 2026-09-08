"""
Wysyla najlepsze strategie z lokalnej bazy do Firestore.

Do Firebase ida TYLKO wyniki warte pokazania — Google Sheet i dashboard
maja z czego czytac. Surowy log wszystkich 50 000 sprawdzonych hipotez
zostaje lokalnie, bo tam jest tani, a w chmurze kosztowalby i spowalnial.

Wymaga sieci — uruchamiaj na Macu (nie przez zdalne narzedzie, ktore ma
zablokowany ruch do googleapis.com).

Uzycie:
    python3 push_strategies.py            # wysyla top 100
    python3 push_strategies.py --top 250
"""
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore

ROOT = Path(__file__).resolve().parent.parent
STRATEGY_DB = ROOT / "data" / "strategies.sqlite"
KEY_PATH = ROOT / "firebase" / "serviceAccountKey.json"

COLLECTION = "strategies"
META_COLLECTION = "engine_meta"


def firestore_safe_definition(strat):
    """
    Firestore nie obsluguje tablic zagniezdzonych w tablicach, a nasze
    warunki to lista list: [["close_position", "<", 0.15], ...].
    Zamieniamy kazdy warunek na mape — liste map Firestore przyjmuje.
    """
    return {
        "id": strat.get("id"),
        "horizon": strat["horizon"],
        "conditions": [
            {"feature": f, "op": op, "threshold": float(th)}
            for f, op, th in strat["conditions"]
        ],
    }


def get_firestore():
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(str(KEY_PATH)))
    return firestore.client()


def push(top=100, conn=None):
    """conn — polaczenie silnika. Jedno polaczenie na proces; osobne,
    otwierane w trakcie pracy silnika, potrafilo uszkodzic baze."""
    wlasne = conn is None
    if wlasne:
        from search import get_db
        conn = get_db()
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """SELECT * FROM strategies WHERE status='candidate'
           ORDER BY rating DESC LIMIT ?""", (top,)
    ).fetchall()

    if not rows:
        print("Brak kandydatow do wyslania. Uruchom najpierw search.py")
        return

    db = get_firestore()
    batch = db.batch()
    n = 0

    for r in rows:
        doc = db.collection(COLLECTION).document(r["id"])
        payload = {
            "definition": firestore_safe_definition(json.loads(r["definition"])),
            "description": r["description"],
            "horizon": r["horizon"],
            "rating": r["rating"],
            "accuracy_score": r["accuracy_score"],
            "stability_score": r["stability_score"],
            "frequency_score": r["frequency_score"],
            "significance_multiplier": r["significance_multiplier"],
            "n_signals": r["n_signals"],
            "n_episodes": r["n_episodes"],
            "frequency_pct": r["frequency_pct"],
            "edge_mean": r["edge_mean"],
            "edge_hit": r["edge_hit"],
            "hit_rate": r["hit_rate"],
            "base_hit_rate": r["base_hit_rate"],
            "t_stat": r["t_stat"],
            "p_value": r["p_value"],
            "status": r["status"],
            "tested_at": r["tested_at"],
        }
        # wyniki ze skarbca, jesli byly sprawdzane
        for col in ("treasury_n_signals", "treasury_edge_mean", "treasury_edge_hit",
                    "treasury_hit_rate", "treasury_status", "treasury_checked_at"):
            if col in r.keys() and r[col] is not None:
                payload[col] = r[col]

        batch.set(doc, payload)
        n += 1
        if n % 400 == 0:
            batch.commit()
            batch = db.batch()

    batch.commit()

    # metadane przebiegu — dashboard potrzebuje wiedziec, na czym stoi
    total = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
    avg_rating = conn.execute(
        "SELECT AVG(rating) FROM strategies WHERE status='candidate'"
    ).fetchone()[0]
    last_run = conn.execute(
        "SELECT * FROM search_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()

    db.collection(META_COLLECTION).document("latest").set({
        "pushed_at": datetime.now(timezone.utc).isoformat(),
        "strategies_pushed": n,
        "total_hypotheses_tested": total,
        "avg_candidate_rating": round(avg_rating, 2) if avg_rating else None,
        "data_from": last_run["data_from"] if last_run else None,
        "data_to": last_run["data_to"] if last_run else None,
    })

    print(f"Wyslano {n} strategii do Firestore (kolekcja '{COLLECTION}').")
    print(f"Metadane w '{META_COLLECTION}/latest': {total:,} sprawdzonych hipotez lacznie.")
    if wlasne:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=100)
    args = ap.parse_args()
    push(args.top)
