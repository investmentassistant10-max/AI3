#!/bin/bash
# Instaluje codzienne odswiezanie danych o 9:00 (launchd).
# Dwuklik wystarczy — mozna uruchomic ponownie, zeby nadpisac ustawienia.

set -u
PROJECT="$HOME/Documents/IA3"
PLIST_SRC="$PROJECT/automation/com.ia3.daily.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.ia3.daily.plist"
LABEL="com.ia3.daily"

echo "──────────────────────────────────────────"
echo "  IA3 — automatyczne odświeżanie danych"
echo "──────────────────────────────────────────"
echo ""

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT/data"

# podmieniamy placeholder na prawdziwa sciezke domowa
sed "s|REPLACE_HOME|$HOME|g" "$PLIST_SRC" > "$PLIST_DST"
chmod 644 "$PLIST_DST"
chmod +x "$PROJECT/automation/ia3-daily.sh"

# wylaczamy poprzednia wersje, jesli byla
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
launchctl unload "$PLIST_DST" 2>/dev/null

if launchctl bootstrap "gui/$(id -u)" "$PLIST_DST" 2>/dev/null ||
   launchctl load "$PLIST_DST" 2>/dev/null; then
  echo "Zainstalowane. Odświeżanie będzie się uruchamiać codziennie o 9:00."
  echo ""
  echo "  Jeśli Mac o tej porze śpi, zadanie ruszy zaraz po przebudzeniu."
  echo "  Log z przebiegów:  $PROJECT/data/automat.log"
  echo ""
  echo "Sprawdzenie, czy zadanie jest zarejestrowane:"
  launchctl list | grep "$LABEL" || echo "  (nie widać — coś poszło nie tak)"
  echo ""
  echo "Chcesz przetestować teraz, bez czekania do 9:00? Wpisz w Terminalu:"
  echo "  launchctl kickstart gui/$(id -u)/$LABEL"
else
  echo "Nie udało się zarejestrować zadania."
  echo "Spróbuj ręcznie:"
  echo "  launchctl load $PLIST_DST"
fi

echo ""
echo "Żeby wyłączyć automat:  launchctl bootout gui/$(id -u)/$LABEL"
echo ""
echo "Naciśnij Enter, żeby zamknąć."
read -r
