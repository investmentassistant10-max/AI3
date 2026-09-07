# dashboard/

Panel predykcji — strona statyczna czytajaca z Firestore. Nie ma backendu:
cala logika (dopasowanie strategii do dzisiejszego stanu rynku, wazenie,
projekcja) dzieje sie w przegladarce.

## Uruchomienie

1. **Konfiguracja Firebase**
   Firebase Console -> Project settings -> General -> Your apps -> dodaj
   aplikacje Web (`</>`), skopiuj `firebaseConfig` i wklej do `config.js`.

2. **Reguly Firestore**
   Firebase Console -> Firestore Database -> Rules -> wklej zawartosc
   `../firebase/firestore.rules` -> Publish.
   Reguly daja publiczny ODCZYT czterech kolekcji i blokuja wszelki zapis;
   dane wprowadza wylacznie kod z kontem serwisowym.

3. **Dane**
   ```
   python3 ../engine/ia3.py push       # strategie do Firestore
   python3 ../engine/ia3.py snapshot   # migawka na dzis
   ```

4. **Publikacja**
   GitHub -> Settings -> Pages -> Source: Deploy from a branch ->
   `main` / `/dashboard`. Strona pojawi sie pod adresem
   `https://<uzytkownik>.github.io/AI3/`.

   Lokalnie: `python3 -m http.server 8000` w tym folderze, potem
   `http://localhost:8000` (otwarcie pliku przez `file://` nie zadziala —
   moduly ES wymagaja serwera).

## Co pokazuje

- predykcje kierunku i wielkosci ruchu na najblizsza sesje
- odliczanie do otwarcia NYSE (9:30 czasu nowojorskiego)
- ile strategii ma DZIS spelnione warunki (czesto zero — to normalne)
- rozklad long/short wraz z wagami
- wykres ostatnich 10 sesji z projekcja
- poziomy: wejscie, cel, stop liczony z ATR
- liste strategii z zastosowaniem i ich warunki

## Jak liczona jest pewnosc

Iloczyn czterech czynnikow:
- **zgodnosc kierunku** — czy strategie mowia jednym glosem
- **liczba strategii** — nasyca sie przy osmiu
- **ich jakosc** — sredni rating
- **rezim zmiennosci** — w spokojnym rynku pewnosc jest obnizana

Ostatni czynnik nie jest ozdobnikiem. Na danych 2000-2026 przewaga sygnalow
kontrarianskich rosnie monotonicznie ze zmiennoscia: 0.09% przy spokoju
wobec 0.54% przy panice. Panel, ktory tego nie uwzglednia, zawyzalby pewnosc
dokladnie wtedy, gdy nie ma czym handlowac.
