# sheets/ — Google Apps Script

Kod w tym folderze jest wersjonowany tu dla porządku, ale **działa w Apps
Script**, w projekcie spiętym z Google Sheet. Trzeba go tam ręcznie wkleić —
patrz `docs/ARCHITECTURE.md` / instrukcje w rozmowie z Claude po szczegóły
wdrożenia krok po kroku.

## Pliki
- `Firestore.gs` — minimalny klient REST do Firestore, uwierzytelniany
  kluczem konta serwisowego (JWT budowany ręcznie, bez zewnętrznych bibliotek).
- `PriceSync.gs` — pobiera dzienne świece SP500 z Yahoo Finance i zapisuje
  je do kolekcji `spx_daily` w Firestore.

## Wymagane Script Properties
(Project Settings -> Script Properties w edytorze Apps Script)

| Klucz | Wartość |
|---|---|
| `FIRESTORE_PROJECT_ID` | id projektu Firebase |
| `FIRESTORE_CLIENT_EMAIL` | `client_email` z serviceAccountKey.json |
| `FIRESTORE_PRIVATE_KEY` | `private_key` z serviceAccountKey.json |

## Uruchomienie
1. `testFirestoreConnection()` — sprawdza, czy autoryzacja działa.
2. `backfillHistory()` — raz, ręcznie: ściąga historię od 2000 roku.
3. `dailyUpdate()` — podpięte pod trigger czasowy (Triggers -> Add Trigger),
   codziennie rano. Bezpieczne do wielokrotnego uruchamiania (nadpisuje po id
   dokumentu, nie duplikuje).

## Struktura danych
```
spx_daily/{YYYY-MM-DD}
  date: string
  open, high, low, close: number
  volume: number
```
