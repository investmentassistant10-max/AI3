#!/bin/bash
# Dodaje skrot "ia3" do powloki, zebys mogl wywolywac komendy z dowolnego
# katalogu. Dwuklik wystarczy; mozna uruchomic wielokrotnie.

PROJECT="$HOME/Documents/IA3"
LINE="alias ia3=\"python3 $PROJECT/engine/ia3.py\""

echo "──────────────────────────────────────────"
echo "  IA3 — skrót do komend"
echo "──────────────────────────────────────────"
echo ""

added=0
for rc in "$HOME/.zshrc" "$HOME/.bash_profile"; do
  # tworzymy plik, jesli go nie ma
  [ -f "$rc" ] || touch "$rc"
  if grep -q "alias ia3=" "$rc" 2>/dev/null; then
    # podmieniamy stara wersje, gdyby sciezka sie zmienila
    sed -i '' "s|alias ia3=.*|$LINE|" "$rc"
    echo "  zaktualizowano w $(basename "$rc")"
  else
    printf '\n# IA3 — skrót do silnika\n%s\n' "$LINE" >> "$rc"
    echo "  dodano do $(basename "$rc")"
  fi
  added=1
done

echo ""
if [ "$added" = "1" ]; then
  echo "Gotowe. Teraz otwórz NOWE okno Terminala (albo wpisz: source ~/.zshrc)"
  echo "i sprawdź:"
  echo ""
  echo "    ia3 raport"
  echo ""
  echo "Komendy działają po polsku i po angielsku:"
  echo "    ia3 raport      ia3 predykcja     ia3 wynik"
  echo "    ia3 stan        ia3 kalibracja    ia3 pracuj"
else
  echo "Nie udało się zapisać skrótu."
fi

echo ""
echo "Naciśnij Enter, żeby zamknąć."
read -r
