#!/bin/bash
#
# Codzienne odswiezenie danych. Uruchamiane przez launchd — patrz plik plist
# obok. Wszystko, co wypisze, ladtuje w data/automat.log.
#
# Kolejnosc ma znaczenie: najpierw sciagamy nowe sesje, potem liczymy na nich
# migawke i predykcje. Odwrotnie predykcja powstalaby na wczorajszych danych.

set -u
PROJECT="$HOME/Documents/IA3"
ENGINE="$PROJECT/engine"
LOG="$PROJECT/data/automat.log"

exec >> "$LOG" 2>&1
echo ""
echo "════════ $(date '+%Y-%m-%d %H:%M:%S') ════════"

cd "$ENGINE" || { echo "BLAD: brak katalogu $ENGINE"; exit 1; }

PY=$(command -v python3 || echo /usr/bin/python3)
echo "python: $PY"

run() {
  echo "--- $1"
  if ! "$PY" ia3.py "$@"; then
    echo "    (krok '$1' nie powiodl sie — ide dalej)"
  fi
}

# 1. nowe ceny — wszystko inne liczy sie na nich
run sync

# 2. prognoza zmiennosci i ceny: rozlicza wczorajsze prognozy, liczy nowa,
#    wysyla do Firestore. To zasila panel.
run volatility

# 3. reszta panelu: strategie, migawka dnia, puls silnika
run push --top 100
run snapshot
run pulse

# 4. kopia bazy i kontrola spojnosci.
#    Silnik robi migawki sam, ale tylko wtedy, gdy pracuje. Ten krok pilnuje,
#    zeby kopia powstala takze w dni, w ktore silnik nie byl uruchamiany.
echo "--- kopia bazy"
"$PY" - <<'PYEOF'
import sys
sys.path.insert(0, ".")
import dbguard, sqlite3
from pathlib import Path
DB = Path("..") / "data" / "strategies.sqlite"
ok, msg = dbguard.quick_check(DB)
print(f"    stan bazy: {msg}")
if not ok:
    print("    UWAGA: baza uszkodzona — kopii nie robie, zeby nie nadpisac zdrowej.")
    print("    Odzyskanie: python3 rescue_db.py --from ../data/kopie/<najswiezsza>")
    raise SystemExit(0)
conn = sqlite3.connect(DB, timeout=30.0)
conn.execute("PRAGMA busy_timeout=30000")
sciezka, info = dbguard.snapshot(conn)
conn.close()
print(f"    kopia: {sciezka.name} ({info:.0f}s)" if sciezka else f"    kopia nieudana: {info}")
PYEOF

echo "Zakonczono $(date '+%H:%M:%S')"

# log nie moze rosnac w nieskonczonosc — zostawiamy ostatnie 2000 linii
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 2000 ]; then
  tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
