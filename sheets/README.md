# sheets/ — Google Apps Script

Kod dziala w Apps Script, w projekcie spietym z arkuszem. Tutaj jest
wersjonowany; do Apps Script wklejasz go recznie.

## Pliki

| plik | rola |
|---|---|
| `Firestore.gs` | klient REST do Firestore, autoryzacja kluczem konta serwisowego |
| `Market.gs` | kalendarz sesji NYSE — weekendy i dziewiec swiat rocznie |
| `PriceSync.gs` | pobiera dzienne swiece SP500 z Yahoo Finance |
| `Verify.gs` | poranna kontrola: czy swieca dotarla; ustawianie triggerow |
| `Log.gs` | arkusz **Log** — jeden wiersz na dzien |
| `PredictionLog.gs` | arkusz **Predykcje** — co system twierdzil i co z tego wyszlo |
| `Engine.gs` | arkusze **Silnik**, **Strategie**, **Wyjścia** — odczyt co godzine |

## Podzial pracy miedzy chmura a Makiem

W Apps Script dziala tylko to, co lekkie i harmonogramowe. Ciezkie liczenie
zostaje na Macu, bo:
- **predykcja** wymaga 57 wskaznikow policzonych z 6709 sesji — w Apps Script
  znaczyloby to druga implementacje tej samej matematyki w JavaScripcie,
  ktora predzej czy pozniej rozjedzie sie z pierwsza
- **kalibracja** to setki tysiecy ewaluacji, a Apps Script ma twardy limit
  6 minut na wykonanie funkcji

Chmura robi natomiast to, czego Mac nie zrobi, gdy jest wylaczony:
dociaga ceny, pilnuje logu i **rozlicza predykcje** (predykcja i ceny sa
w Firestore, reszta to arytmetyka).

## Triggery

Uruchom raz `setupAllTriggers()` — ustawi trzy zadania i usunie stare
duplikaty:

| godzina | funkcja | co robi |
|---|---|---|
| 8:00 | `dailyUpdate` | dociaga wczorajsza swiece do Firestore |
| 8:15 | `verifyMorningData` | sprawdza, czy dotarla; zapisuje status do arkusza Log |
| 23:00 | `syncPredictionLog` | zapisuje predykcje dnia i rozlicza zalegle |
| co godzine | `hourlySync` | stan silnika, ranking strategii, reguly wyjscia |

Strefa czasowa projektu (Project Settings -> Time zone) musi byc ustawiona
na `Europe/Warsaw`, bo od niej zaleza godziny triggerow.

## Wymagane Script Properties

| klucz | wartosc |
|---|---|
| `FIRESTORE_PROJECT_ID` | id projektu Firebase |
| `FIRESTORE_CLIENT_EMAIL` | `client_email` z serviceAccountKey.json |
| `FIRESTORE_PRIVATE_KEY` | `private_key` z serviceAccountKey.json |

## Funkcje do recznego uruchomienia

- `testFirestoreConnection()` — sprawdza autoryzacje
- `testCalendar()` — pokazuje swieta NYSE w tym roku i status dzisiejszego dnia
- `backfillHistory()` — jednorazowy import historii od 2000 roku
- `predictionScore()` — skutecznosc predykcji, wynik w logach

## Arkusze, ktore powstaja same

| arkusz | co zawiera | odswiezany |
|---|---|---|
| **Log** | jeden wiersz na dzien: sesja, swiece, predykcja | 8:15 i 23:00 |
| **Predykcje** | co system twierdzil i co z tego wyszlo | 23:00 |
| **Silnik** | jeden wiersz na odczyt: ile hipotez, przyrost, prog, stan | co godzine |
| **Strategie** | top 100 z ratingiem, przewaga, trafnosc, warunki | co godzine |
| **Wyjscia** | najlepsze kombinacje wejscie + SL/TP/czas | co godzine |

Arkusz **Silnik** ma kolumne "Przyrost" — ile hipotez przybylo od poprzedniego
odczytu. Zero przez kilka godzin znaczy, ze silnik stoi. Kolumna "Stan"
rozroznia trzy sytuacje: `pracuje` (puls swiezy), `bez zmian` (ten sam puls
co poprzednio) i `zatrzymany` (silnik wyslal puls pozegnalny przy wyjsciu).

Arkusz **Wyjscia** jest najbardziej praktyczny ze wszystkich: mowi nie tylko
KIEDY wejsc, ale tez jak dlugo trzymac i gdzie postawic stop. Do niedawna te
dane w ogole nie opuszczaly Maca.
