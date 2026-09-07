#!/bin/bash
# Dociaga nowe ceny, przelicza migawke na dzis i wysyla do Firestore.
# Uruchom rano, przed otwarciem sesji.

cd "$(dirname "$0")/engine" || exit 1

echo "──────────────────────────────────────────"
echo "  IA3 — odswiezanie danych"
echo "──────────────────────────────────────────"
echo ""

echo "1/3  Dociagam nowe sesje z Firestore..."
python3 ia3.py sync

echo ""
echo "2/3  Wysylam strategie..."
python3 ia3.py push --top 100

echo ""
echo "3/3  Licze migawke na dzis..."
python3 ia3.py snapshot

echo ""
echo "Gotowe. Panel pokazuje juz aktualne dane."
echo "Nacisnij Enter, zeby zamknac."
read -r
