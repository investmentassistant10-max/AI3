"""
Synchronizuje lokalną bazę cen SPX (data/spx_daily.sqlite) z Firestore.

Pobiera TYLKO dni nowsze niż ostatni dzień już zapisany lokalnie — nie
ściąga całej kolekcji przy każdym uruchomieniu. Przy pustej lokalnej bazie
(pierwsze uruchomienie) pobiera całą historię, tak jak jest w Firestore.

Użycie:
    source .venv/bin/activate
    python3 sync_prices.py
"""
import sqlite3
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "spx_daily.sqlite"
KEY_PATH = ROOT / "firebase" / "serviceAccountKey.json"
COLLECTION = "spx_daily"


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS spx_daily (
            date   TEXT PRIMARY KEY,
            open   REAL NOT NULL,
            high   REAL NOT NULL,
            low    REAL NOT NULL,
            close  REAL NOT NULL,
            volume REAL
        )
        """
    )
    conn.commit()
    return conn


def get_last_local_date(conn):
    row = conn.execute("SELECT MAX(date) FROM spx_daily").fetchone()
    return row[0]


def get_firestore_client():
    if not firebase_admin._apps:
        cred = credentials.Certificate(str(KEY_PATH))
        firebase_admin.initialize_app(cred)
    return firestore.client()


def fetch_new_candles(db_client, after_date):
    col = db_client.collection(COLLECTION)
    query = col.order_by("date")
    if after_date:
        query = query.where("date", ">", after_date)
    return [doc.to_dict() for doc in query.stream()]


def main():
    conn = get_db()
    last_date = get_last_local_date(conn)
    print(f"Ostatni dzień lokalnie: {last_date or '(pusta baza)'}")

    db_client = get_firestore_client()
    new_candles = fetch_new_candles(db_client, last_date)

    if not new_candles:
        print("Brak nowych dni — baza lokalna jest aktualna.")
        return

    conn.executemany(
        """
        INSERT OR REPLACE INTO spx_daily (date, open, high, low, close, volume)
        VALUES (:date, :open, :high, :low, :close, :volume)
        """,
        new_candles,
    )
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM spx_daily").fetchone()[0]
    span = conn.execute("SELECT MIN(date), MAX(date) FROM spx_daily").fetchone()
    print(f"Dodano {len(new_candles)} nowych dni. Łącznie w bazie: {total}.")
    print(f"Zakres dat: {span[0]} -> {span[1]}")


if __name__ == "__main__":
    main()
