#!/bin/bash
#
# macOS nie pozwala zadaniom w tle czytac folderu Dokumenty. Dlatego automat
# konczyl sie bledem "Operation not permitted" i nie wykonal ani jednego kroku.
#
# Ten skrypt niczego nie zmienia w systemie — tylko pokazuje, co zrobic klikiem,
# i sprawdza, czy uprawnienie juz dziala.

PROJECT="$HOME/Documents/IA3"

echo "══════════════════════════════════════════════════════════════════"
echo "  Uprawnienie dla automatu IA3"
echo "══════════════════════════════════════════════════════════════════"
echo ""
echo "Zrob to raz, klikiem:"
echo ""
echo "  1. Ustawienia systemowe → Prywatnosc i ochrona → Pelny dostep do dysku"
echo "  2. Kliknij + (moze poprosic o haslo)"
echo "  3. W oknie wyboru wcisnij Cmd+Shift+G i wklej:  /bin/bash"
echo "  4. Dodaj i upewnij sie, ze przelacznik przy bash jest wlaczony"
echo ""
echo "Uwaga: to uprawnienie dostaje kazdy skrypt bash na tym Macu, nie tylko IA3."
echo ""
read -r -p "Zrobione? Nacisnij Enter, sprawdze czy dziala. " _

echo ""
if launchctl list | grep -q com.ia3.daily; then
  echo "Automat jest zaladowany w launchd."
else
  echo "UWAGA: automatu nie ma w launchd — uruchom najpierw 'Wlacz automat.command'."
fi

echo ""
echo "Probne uruchomienie automatu (to potrwa okolo minuty)..."
launchctl start com.ia3.daily
sleep 45

LOG="$PROJECT/data/automat.log"
if [ -f "$LOG" ]; then
  echo ""
  echo "──────── ostatni przebieg ────────"
  tail -n 30 "$LOG"
  echo "──────────────────────────────────"
  echo ""
  echo "Jesli widzisz wyzej kroki 'sync', 'volatility', 'push' — dziala."
else
  echo ""
  echo "Log nadal pusty. Sprawdz komunikat:"
  tail -n 5 "$PROJECT/data/automat-launchd.log" 2>/dev/null
  echo ""
  echo "Jesli nadal 'Operation not permitted' — uprawnienie nie zostalo nadane"
  echo "albo trzeba sie wylogowac i zalogowac, zeby macOS je zauwazyl."
fi

echo ""
read -r -p "Nacisnij Enter, zeby zamknac. " _
