#!/usr/bin/env python3
"""
IA3 — jeden punkt wejscia z terminala.

    python3 ia3.py sync                    dociagnij nowe ceny z Firestore
    python3 ia3.py search --hours 2        szukaj warunkow wejscia
    python3 ia3.py exits --top 20          dobierz reguly wyjscia (SL/TP/czas)
    python3 ia3.py validate --top 10       sprawdz najlepsze na skarbcu
    python3 ia3.py diag close_position "<" 0.15 1    diagnostyka jednej hipotezy
    python3 ia3.py report                  co jest w bazie
    python3 ia3.py push --top 100          wyslij wyniki do Firestore
    python3 ia3.py run --hours 8           pelny cykl: sync, search, exits, push
    python3 ia3.py forever                 silnik ciagly — uruchom i zostaw
    python3 ia3.py snapshot                migawka na dzis dla dashboardu
    python3 ia3.py predict                 policz predykcje, zapisz i wyslij
    python3 ia3.py score                   skutecznosc dotychczasowych predykcji
    python3 ia3.py calibrate               zmierz realny prog istotnosci
    python3 ia3.py zmiennosc               prognoza zmiennosci na dzis
    python3 ia3.py wynik-zmiennosci        skutecznosc prognoz zmiennosci
    python3 ia3.py pulse                   wyslij stan silnika do Firestore

Komendy dzialaja tez po polsku:
    raport, predykcja, wynik, stan, kalibracja, synchronizuj, szukaj,
    wyjscia, skarbiec, diagnoza, wyslij, migawka, pracuj
"""
import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STRATEGY_DB = ROOT / "data" / "strategies.sqlite"


# Ktora komenda czego naprawde potrzebuje. Raport czy kalibracja czytaja
# wylacznie lokalna baze — nie ma powodu, zeby wymagaly biblioteki do chmury.
NEEDS_FIREBASE = {"sync", "push", "snapshot", "predict", "pulse", "run", "volatility"}


def _check_deps(command):
    """Podpowiada, czego brakuje, zamiast wysypywac sie na imporcie."""
    required = [("numpy", "numpy"), ("pandas", "pandas")]
    if command in NEEDS_FIREBASE:
        required.append(("firebase_admin", "firebase-admin"))

    missing = []
    for mod, pkg in required:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("Brakuje bibliotek: " + ", ".join(missing))
        print("Zainstaluj: python3 -m pip install --user " + " ".join(missing))
        sys.exit(1)


def cmd_sync(args):
    import sync_prices
    sync_prices.main()


def cmd_search(args):
    from search import run_search
    run_search(hours=args.hours, minutes=args.minutes, level=args.level)


def cmd_exits(args):
    import exit_search
    print(f"Dobieram reguly wyjscia dla top {args.top} warunkow wejscia...\n")
    n = exit_search.run(top=args.top)
    print(f"\nZapisano {n} kombinacji wejscie-wyjscie.")


def cmd_validate(args):
    from validate import validate_top
    validate_top(args.top)


def cmd_diag(args):
    from diagnostics import full_report
    full_report(args.feature, args.op, args.threshold, args.horizon)


def cmd_push(args):
    import push_strategies
    push_strategies.push(args.top)


def cmd_snapshot(args):
    import daily_snapshot
    snap = daily_snapshot.build_snapshot()
    from pathlib import Path as _P
    import json as _j
    out = _P(daily_snapshot.LOCAL_OUT)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_j.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Migawka na {snap['date']}: zamkniecie {snap['last_close']}")
    if not args.local:
        daily_snapshot.push(snap)
        print("Wyslano do Firestore.")


def cmd_predict(args):
    import predict
    predict.settle(quiet=True)
    pred = predict.make_prediction()
    if pred and not args.local:
        predict.push(pred)
        print("Wyslano do Firestore: predictions/latest")


def cmd_score(args):
    import predict
    predict.settle(quiet=True)
    predict.score()


def cmd_calibrate(args):
    import calibrate
    calibrate.run(args.runs, args.sample)


def cmd_vol(args):
    import predict_vol
    predict_vol.settle(quiet=True)
    pred = predict_vol.make_prediction()
    if pred and not args.local:
        predict_vol.push(pred)
        print("\nWyslano do Firestore: vol_predictions/latest")


def cmd_vol_score(args):
    import predict_vol
    predict_vol.settle(quiet=True)
    predict_vol.score()


def cmd_pulse(args):
    import sqlite3
    import heartbeat
    conn = sqlite3.connect(STRATEGY_DB)
    state = heartbeat.collect(conn)
    conn.close()
    print(f"Hipotez: {state['hypotheses_total']:,} | kandydatow: {state['candidates']:,} | "
          f"prog |t| > {state['threshold']} | ponad progiem: {state['above_threshold']:,}")
    if not args.local:
        heartbeat.push(state)
        n = heartbeat.push_exit_rules()
        print(f"Puls wyslany. Regul wyjscia w chmurze: {n}")


def cmd_report(args):
    if not STRATEGY_DB.exists():
        print("Brak bazy strategii. Uruchom: python3 ia3.py search")
        return
    conn = sqlite3.connect(STRATEGY_DB)
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
    print(f"\n{'='*100}\nSTAN BAZY\n{'='*100}")
    print(f"  Sprawdzonych hipotez: {total:,}")
    for r in conn.execute("SELECT status, COUNT(*) n FROM strategies GROUP BY status ORDER BY n DESC"):
        print(f"    {r['status']:<22} {r['n']:>8,}")

    from rating import significance_threshold, load_correlation_factor
    if total > 1:
        corr = load_correlation_factor(conn)
        th = significance_threshold(total, corr)
        th_naive = significance_threshold(total, 1.0)
        above = conn.execute("SELECT COUNT(*) FROM strategies WHERE ABS(t_stat) > ?", (th,)).fetchone()[0]
        print()
        if corr > 1.01:
            print(f"  Kalibracja: hipotezy {corr:.1f}x bardziej skorelowane niz niezalezne proby")
            print(f"  Efektywnych niezaleznych testow: ~{total/corr:,.0f} z {total:,} hipotez")
            print(f"  Prog istotnosci:        |t| > {th:.2f}   (bez kalibracji byloby {th_naive:.2f})")
        else:
            print(f"  Prog istotnosci: |t| > {th:.2f}")
            print(f"  BRAK KALIBRACJI — prog liczony zachowawczo, moze odrzucac dobre strategie.")
            print(f"  Uruchom: python3 ia3.py calibrate")
        print(f"  Hipotez powyzej progu: {above}")

    print(f"\n{'='*100}\nTOP 10 WARUNKOW WEJSCIA\n{'='*100}")
    print(f"{'rating':>6} {'t':>6} {'przew%':>8} {'traf%':>7} {'epiz':>6}  opis")
    print("-" * 100)
    for r in conn.execute("""SELECT * FROM strategies WHERE status='candidate'
                             ORDER BY rating DESC LIMIT 10"""):
        print(f"{r['rating']:>6.1f} {r['t_stat']:>6.2f} {r['edge_mean']:>8.3f} "
              f"{r['hit_rate']:>7.1f} {r['n_episodes']:>6}  {r['description']}")

    has_exits = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='exit_rules'"
    ).fetchone()
    if has_exits:
        n_exits = conn.execute("SELECT COUNT(*) FROM exit_rules").fetchone()[0]
        if n_exits:
            print(f"\n{'='*100}\nTOP 10 KOMBINACJI WEJSCIE + WYJSCIE (wg przewagi nad baseline)\n{'='*100}")
            print(f"{'przew%':>8} {'traf%':>7} {'PF':>6} {'najgorsza':>10}  {'wyjscie':<30} wejscie")
            print("-" * 100)
            for r in conn.execute("""
                SELECT e.*, s.description FROM exit_rules e
                JOIN strategies s ON s.id = e.strategy_id
                WHERE e.trustworthy = 1
                ORDER BY e.edge_mean DESC LIMIT 10"""):
                pf = f"{r['profit_factor']:.2f}" if r["profit_factor"] else "   —"
                print(f"{r['edge_mean']:>8.4f} {r['hit_rate']:>7.1f} {pf:>6} {r['worst']:>10.2f}  "
                      f"{r['rule_label']:<30} {r['description'][:44]}")
    conn.close()
    print()


def cmd_forever(args):
    import subprocess
    cmd = [sys.executable, str(Path(__file__).parent / "run_forever.py")]
    if args.max_hours:
        cmd += ["--max-hours", str(args.max_hours)]
    if args.no_push:
        cmd.append("--no-push")
    subprocess.run(cmd)


def cmd_run(args):
    print(">>> 1/4 synchronizacja cen")
    try:
        cmd_sync(args)
    except Exception as e:
        print(f"    (pominieto: {e})")
    print("\n>>> 2/4 szukanie warunkow wejscia")
    cmd_search(args)
    print("\n>>> 3/4 dobor regul wyjscia")
    cmd_exits(args)
    print("\n>>> 4/5 wysylka do Firestore")
    try:
        cmd_push(args)
    except Exception as e:
        print(f"    (pominieto: {e})")
    print("\n>>> 5/5 predykcja")
    try:
        cmd_predict(argparse.Namespace(local=False))
    except Exception as e:
        print(f"    (pominieto: {e})")
    print("\nGotowe. Podsumowanie: python3 ia3.py report")


# Caly projekt mowi po polsku, wiec komendy tez powinny. Angielskie nazwy
# zostaja — obie formy dzialaja tak samo.
ALIASES = {
    "raport": "report",
    "predykcja": "predict",
    "prognoza": "predict",
    "wynik": "score",
    "skutecznosc": "score",
    "skuteczność": "score",
    "stan": "pulse",
    "puls": "pulse",
    "kalibracja": "calibrate",
    "kalibruj": "calibrate",
    "synchronizuj": "sync",
    "pobierz": "sync",
    "szukaj": "search",
    "wyjscia": "exits",
    "wyjścia": "exits",
    "sprawdz": "validate",
    "sprawdź": "validate",
    "skarbiec": "validate",
    "diagnoza": "diag",
    "wyslij": "push",
    "wyślij": "push",
    "migawka": "snapshot",
    "pracuj": "forever",
    "licz": "forever",
    "silnik": "forever",
    "cykl": "run",
    "zmiennosc": "volatility",
    "zmienność": "volatility",
    "wynik-zmiennosci": "volscore",
    "wynik-zmienności": "volscore",
}


def main():
    ap = argparse.ArgumentParser(
        prog="ia3", description="IA3 — silnik statystyczny SP500",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("sync", help="dociagnij nowe ceny z Firestore")

    p = sub.add_parser("search", help="szukaj warunkow wejscia")
    p.add_argument("--hours", type=float)
    p.add_argument("--minutes", type=float)
    p.add_argument("--level", type=int, choices=[1, 2, 3])

    p = sub.add_parser("exits", help="dobierz reguly wyjscia")
    p.add_argument("--top", type=int, default=20)

    p = sub.add_parser("validate", help="sprawdz na skarbcu")
    p.add_argument("--top", type=int, default=10)

    p = sub.add_parser("diag", help="diagnostyka jednej hipotezy")
    p.add_argument("feature")
    p.add_argument("op", choices=["<", ">"])
    p.add_argument("threshold", type=float)
    p.add_argument("horizon", type=int)

    p = sub.add_parser("push", help="wyslij do Firestore")
    p.add_argument("--top", type=int, default=100)

    sub.add_parser("report", help="co jest w bazie")

    p = sub.add_parser("snapshot", help="migawka na dzis dla dashboardu")
    p.add_argument("--local", action="store_true")

    p = sub.add_parser("predict", help="policz predykcje i wyslij")
    p.add_argument("--local", action="store_true")

    sub.add_parser("score", help="skutecznosc dotychczasowych predykcji")

    p = sub.add_parser("pulse", help="wyslij stan silnika do Firestore")
    p.add_argument("--local", action="store_true")

    p = sub.add_parser("volatility", help="prognoza zmiennosci na dzis")
    p.add_argument("--local", action="store_true")

    sub.add_parser("volscore", help="skutecznosc prognoz zmiennosci")

    p = sub.add_parser("calibrate", help="zmierz realny prog istotnosci")
    p.add_argument("--runs", type=int, default=20)
    p.add_argument("--sample", type=int, default=6000)

    p = sub.add_parser("forever", help="silnik ciagly — uruchom i zostaw")
    p.add_argument("--max-hours", type=float)
    p.add_argument("--no-push", action="store_true")

    p = sub.add_parser("run", help="pelny cykl")
    p.add_argument("--hours", type=float, default=1.0)
    p.add_argument("--minutes", type=float)
    p.add_argument("--level", type=int, choices=[1, 2, 3])
    p.add_argument("--top", type=int, default=20)

    # podmiana polskiej nazwy na wewnetrzna, zanim argparse ja zobaczy
    if len(sys.argv) > 1 and sys.argv[1] in ALIASES:
        sys.argv[1] = ALIASES[sys.argv[1]]

    args = ap.parse_args()
    _check_deps(args.command)
    {"sync": cmd_sync, "search": cmd_search, "exits": cmd_exits,
     "validate": cmd_validate, "diag": cmd_diag, "push": cmd_push,
     "report": cmd_report, "run": cmd_run, "snapshot": cmd_snapshot,
     "predict": cmd_predict, "score": cmd_score, "calibrate": cmd_calibrate,
     "pulse": cmd_pulse, "volatility": cmd_vol, "volscore": cmd_vol_score,
     "forever": cmd_forever}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
