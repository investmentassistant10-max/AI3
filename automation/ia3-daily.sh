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

run sync
run push --top 100
run snapshot
run volatility
run pulse

echo "Zakonczono $(date '+%H:%M:%S')"

# log nie moze rosnac w nieskonczonosc — zostawiamy ostatnie 2000 linii
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 2000 ]; then
  tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
