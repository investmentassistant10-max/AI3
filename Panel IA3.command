#!/bin/bash
# Dwuklik w Finderze uruchamia panel i otwiera go w przegladarce.
# Zamkniecie tego okna Terminala zatrzymuje serwer.

cd "$(dirname "$0")/dashboard" || exit 1
PORT=8765

# jesli port zajety, sprobuj kolejnych
while lsof -i ":$PORT" >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done

echo "──────────────────────────────────────────"
echo "  IA3 — panel predykcji"
echo "  http://localhost:$PORT"
echo ""
echo "  Zamknij to okno, zeby zatrzymac panel."
echo "──────────────────────────────────────────"
echo ""

python3 -m http.server "$PORT" >/dev/null 2>&1 &
SERVER_PID=$!
sleep 1
open "http://localhost:$PORT"

trap "kill $SERVER_PID 2>/dev/null" EXIT
wait $SERVER_PID
