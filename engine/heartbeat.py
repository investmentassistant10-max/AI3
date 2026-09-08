"""
Puls silnika: mala migawka stanu do Firestore, zebys z dowolnego miejsca
wiedzial, czy cos liczy i na jakim jest etapie.

Dlaczego osobno od wysylki strategii: sto dokumentow ze strategiami co
kwadrans to 9600 zapisow dziennie przy darmowym limicie 20 000 — polowa
budzetu na dane, ktore zmieniaja sie o kilka pozycji na godzine. Puls to
jeden dokument, wiec moze chodzic czesto; strategie ida rzadziej.

Zapisuje dwie rzeczy:
  engine_meta/heartbeat  — biezacy stan (nadpisywany)
  engine_runs/{znacznik} — punkt historii, do wykresu postepu w arkuszu
"""
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STRATEGY_DB = ROOT / "data" / "strategies.sqlite"
KEY_PATH = ROOT / "firebase" / "serviceAccountKey.json"

# ile punktow historii trzymamy w chmurze — starsze kasujemy, zeby
# kolekcja nie rosla w nieskonczonosc
HISTORY_KEEP = 500


def collect(conn, phase=None, cycle=None, started_at=None):
    """Zbiera stan z lokalnej bazy. Nie wymaga sieci."""
    from rating import significance_threshold, load_correlation_factor
    from search import total_trials

    total = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
    trials = total_trials(conn)
    corr = load_correlation_factor(conn)
    threshold = significance_threshold(trials, corr)

    cand = conn.execute(
        "SELECT COUNT(*) FROM strategies WHERE status='candidate'").fetchone()[0]
    stale = conn.execute(
        "SELECT COUNT(*) FROM strategies WHERE status='stale'").fetchone()[0]
    best = conn.execute(
        "SELECT MAX(rating) FROM strategies WHERE status='candidate'").fetchone()[0]
    max_t = conn.execute("SELECT MAX(ABS(t_stat)) FROM strategies").fetchone()[0]
    above = conn.execute(
        "SELECT COUNT(*) FROM strategies WHERE ABS(t_stat) > ?", (threshold,)).fetchone()[0]

    # rozklad powodow odrzucenia — pokazuje, jak silnik odsiewa
    by_status = {r[0]: r[1] for r in conn.execute(
        "SELECT status, COUNT(*) FROM strategies GROUP BY status")}

    n_exits = 0
    try:
        n_exits = conn.execute("SELECT COUNT(*) FROM exit_rules").fetchone()[0]
    except sqlite3.OperationalError:
        pass

    now = datetime.now(timezone.utc)
    out = {
        "updated_at": now.isoformat(),
        "hypotheses_total": total,
        "trials_total": trials,
        "candidates": cand,
        "stale": stale,
        "exit_rules": n_exits,
        "best_rating": round(best, 1) if best else 0.0,
        "max_t": round(max_t, 2) if max_t else 0.0,
        "threshold": round(threshold, 2),
        "above_threshold": above,
        "correlation_factor": round(corr, 1),
        "effective_tests": int(trials / corr) if corr else trials,
        "by_status": by_status,
    }
    if phase:
        out["phase"] = phase
    if cycle is not None:
        out["cycle"] = cycle
    if started_at:
        out["running_since"] = started_at
        out["running_hours"] = round(
            (now - datetime.fromisoformat(started_at)).total_seconds() / 3600, 2)
    return out


def push(state, keep_history=True):
    """Wysyla puls do Firestore. Wymaga sieci — uruchamiaj na Macu."""
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(str(KEY_PATH)))
    db = firestore.client()

    db.collection("engine_meta").document("heartbeat").set(state)

    if keep_history:
        stamp = state["updated_at"].replace(":", "").replace("-", "")[:15]
        db.collection("engine_runs").document(stamp).set({
            "at": state["updated_at"],
            "hypotheses_total": state["hypotheses_total"],
            "candidates": state["candidates"],
            "above_threshold": state["above_threshold"],
            "best_rating": state["best_rating"],
            "max_t": state["max_t"],
            "threshold": state["threshold"],
            "cycle": state.get("cycle"),
            "running_hours": state.get("running_hours"),
        })
        _trim_history(db)


def _trim_history(db):
    """Kasuje najstarsze punkty historii ponad limit."""
    docs = list(db.collection("engine_runs").order_by("at").stream())
    excess = len(docs) - HISTORY_KEEP
    if excess > 0:
        batch = db.batch()
        for d in docs[:excess]:
            batch.delete(d.reference)
        batch.commit()


def push_exit_rules(top=40):
    """
    Najlepsze kombinacje wejscie + wyjscie do Firestore.

    To sa dane, ktorych panel dotad nie widzial, a sa najbardziej praktyczne
    ze wszystkiego: mowia nie tylko KIEDY wejsc, ale tez jak dlugo trzymac
    i gdzie postawic stop.
    """
    import firebase_admin
    import json
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(str(KEY_PATH)))
    db = firestore.client()

    conn = sqlite3.connect(STRATEGY_DB, timeout=30.0)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT e.*, s.description, s.rating, s.horizon
               FROM exit_rules e JOIN strategies s ON s.id = e.strategy_id
               WHERE e.trustworthy = 1 AND s.status = 'candidate'
               ORDER BY e.edge_mean DESC LIMIT ?""", (top,)).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return 0

    batch = db.batch()
    for i, r in enumerate(rows):
        doc = db.collection("exit_rules").document(f"{i:03d}")
        batch.set(doc, {
            "rank": i + 1,
            "entry": r["description"],
            "exit": r["rule_label"],
            "entry_rating": r["rating"],
            "n_trades": r["n_trades"],
            "edge_mean": r["edge_mean"],
            "mean": r["mean"],
            "hit_rate": r["hit_rate"],
            "profit_factor": r["profit_factor"],
            "worst": r["worst"],
            "uncertainty": r["uncertainty"],
        })
    batch.commit()
    conn.close()
    return len(rows)


if __name__ == "__main__":
    conn = sqlite3.connect(STRATEGY_DB)
    state = collect(conn)
    conn.close()
    print(f"Hipotez: {state['hypotheses_total']:,} | kandydatow: {state['candidates']:,} | "
          f"prog |t|>{state['threshold']} | ponad progiem: {state['above_threshold']}")
    push(state)
    n = push_exit_rules()
    print(f"Puls wyslany. Regul wyjscia w chmurze: {n}")
