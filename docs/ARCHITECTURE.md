# IA3 — architektura i plan wdrożenia

## Cel
Silnik codziennie (do ~8h) przeszukuje warianty strategii statystycznych na
dziennych świecach SP500, ocenia je i zapisuje do Firebase. Dashboard łączy
aktywne strategie w jedną predykcję na dziś / 2D / 3D.

## Zasady, które trzymają system uczciwym
- **Skarbiec**: ostatnie ~2 lata danych nigdy nie są używane do szukania
  strategii — tylko do końcowego potwierdzenia. Każde odpytanie skarbca jest
  liczone i limitowane.
- **Lokalny log, nie Firebase**: każda pojedyncza próba silnika trafia do
  lokalnej bazy (SQLite) w `data/`. Do Firebase idą tylko strategie, które
  przeszły sito, razem z ich ratingiem.
- **Rating strategii** = funkcja trzech składowych:
  - trafność (hit rate względem baseline'u, nie w oderwaniu od niego),
  - powtarzalność (stabilność wyniku w różnych okresach historii),
  - częstotliwość (jak często sygnał w ogóle występuje).
- **Bez zaglądania w przyszłość**: każda cecha licząca się na dany dzień może
  używać tylko danych sprzed tego dnia.

## Fazy wdrożenia
0. Szkielet projektu (repo, foldery, ten dokument) — **w trakcie**
1. Dane: pobieranie i przechowywanie dziennych świec SP500
2. Silnik strategii: generowanie, testowanie, rating, zapis do Firebase
3. Automatyzacja: uruchamianie silnika raz dziennie na Macu (launchd), z
   zapisywaniem postępu
4. Google Sheet: trigger 8:00 dociągający świecę dnia + widoki strategii
5. Dashboard (GitHub Pages): odczyt z Firebase, ważona predykcja, metryki
   pewności
6. Zamknięcie pętli: log realnych predykcji dashboardu vs to, co faktycznie
   się wydarzyło

## Szkic struktury danych w Firestore
```
spx_daily/{YYYY-MM-DD}                          # ceny — Faza 1, gotowe
  date, open, high, low, close, volume

strategies/{strategyId}
  definition: { ... parametry strategii ... }
  rating: { accuracy, stability, frequency, overall }
  created_at, last_tested_at, status: candidate | validated | retired

strategy_tests/{strategyId}/history/{testId}   # historia ocen w czasie
  period, accuracy, sample_size, timestamp

predictions/{date}
  direction, magnitude, confidence
  strategies_used, avg_rating
  # log dziennych predykcji dashboardu — do sprawdzania skuteczności później

treasury_log/{date}
  strategy_id, query_count   # budżet odpytań skarbca
```
Schemat na razie roboczy — dopracujemy go w Fazie 1-2, jak zobaczymy, czego
faktycznie silnik potrzebuje.

## Struktura repo
```
IA3/
  engine/     # silnik w Pythonie (Faza 2-3)
  data/       # lokalne dane i log strategii (gitignored)
  firebase/   # reguły Firestore, klucz serwisowy (gitignored)
  sheets/     # kod Google Apps Script, wersjonowany tu
  dashboard/  # statyczna strona na GitHub Pages (Faza 5)
  docs/       # ten dokument i inne notatki
```

## Co wiemy o danych (i czego nie)

Baza zaczyna sie w 2000 roku — 6709 sesji. To wystarcza statystycznie, ale
caly zbior lezy w jednej epoce makroekonomicznej: po bance internetowej,
w wiekszosci w erze niskich stop i luzowania ilosciowego. Brakuje w niej
lat 80. i 90., krachu 1987 i rezimu wysokich stop procentowych.

Dlatego kazdy wynik sprawdzamy osobno w erach (`diagnostics.by_era`),
a nie tylko srednio po calosci.

### Ustalenia na temat sygnalu close_position
- Przewaga dodatnia we wszystkich 6 ocenialnych okresach — efekt nie jest
  wlasnoscia samej ery QE.
- Sila efektu rosnie monotonicznie ze zmiennoscia rynku: 0.09% przy
  spokoju, 0.54% przy panice (percentyl zmiennosci > 0.9).
- Slaby wynik na skarbcu (2024-2026) tlumaczy sie tym, ze byl to okres
  spokojny — sygnal nie wygasl, tylko nie mial okazji.

Wniosek praktyczny: predykcja powinna byc wazona rezimem zmiennosci.
W spokojnym rynku system powinien deklarowac nizsza pewnosc.
