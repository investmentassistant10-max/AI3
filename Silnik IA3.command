#!/bin/bash
# Uruchamia silnik liczacy. Pracuje, dopoki nie nacisniesz Ctrl+C
# albo nie zamkniesz tego okna.

cd "$(dirname "$0")/engine" || exit 1

echo "──────────────────────────────────────────────────────────"
echo "  IA3 — silnik liczacy"
echo ""
echo "  Pracuje bez konca, szukajac strategii i oceniajac je."
echo "  Ctrl+C  = zatrzymanie (dokonczy paczke i zapisze stan)"
echo ""
echo "  Mozesz go zatrzymac i wznowic kiedy chcesz — nic nie"
echo "  ginie, nastepny start podejmie prace w tym samym miejscu."
echo "──────────────────────────────────────────────────────────"
echo ""

# sprawdzenie zaleznosci przy pierwszym uruchomieniu
if ! python3 -c "import numpy, pandas" 2>/dev/null; then
  echo "Brakuje bibliotek numpy/pandas — instaluje (tylko za pierwszym razem)..."
  python3 -m pip install --user --quiet numpy pandas || {
    echo ""
    echo "Instalacja nie powiodla sie. Sprobuj recznie:"
    echo "  python3 -m pip install --user numpy pandas"
    echo ""
    echo "Nacisnij Enter, zeby zamknac."
    read -r
    exit 1
  }
  echo "Gotowe."
  echo ""
fi

python3 run_forever.py

echo ""
echo "Silnik zatrzymany. Podsumowanie: python3 ia3.py report"
echo "Nacisnij Enter, zeby zamknac."
read -r
