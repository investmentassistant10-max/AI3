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

## Zabezpieczenia metodologiczne (stan po audycie)

Silnik ma cztery niezalezne mechanizmy chroniace przed braniem szumu za
odkrycie. Zadnego z nich nie da sie pominac bez swiadomej decyzji.

**1. Prog istotnosci rosnacy z liczba prob**
Liczony ze WSZYSTKICH prob — warunkow wejscia i regul wyjscia razem
(`search.total_trials`). Przy 50 tys. hipotez wynosi |t| > 4.6, przy
milionie 5.3.

**2. Kalibracja empiryczna progu** (`calibrate.py`)
Teoretyczny wzor zaklada niezalezne testy, a nasze sa skorelowane. Zamiast
zgadywac, mierzymy: przesuwamy cyklicznie zwroty w przod (co niszczy kazdy
prawdziwy zwiazek, zostawiajac te sama strukture danych) i patrzymy, jak
wysoko siega najlepszy wynik na czystym szumie. To jest prawdziwy prog.

**3. Test placebo jako bramka** (`fastcore.placebo_p`)
Kazdy kandydat przechodzi 40 przesuniec cyklicznych maski sygnalu.
Przesuniecie zachowuje strukture skupisk sygnalu — jest ostrzejsze niz pelne
przetasowanie, ktore te skupiska rozbija i przez to zanizyloby poprzeczke.
Kandydat, ktorego przewaga przezywa przesuniecie w wiecej niz 5% prob,
dostaje rating obniżony do 30% wartosci.

**4. Budzet skarbca** (`validate.TREASURY_BUDGET`)
Twardy limit 100 odpytan. Skarbiec to 502 sesje — po kilkuset probach na tych
samych danych "potwierdzenie" przestaje cokolwiek znaczyc, bo cos musialo
przejsc przypadkiem. Po wyczerpaniu budzetu walidacja odmawia dzialania.

**Re-walidacja** (`run_forever.revalidate`)
Co szesc cykli 800 najlepszych strategii jest przeliczanych na aktualnych
danych i aktualnym progu. Strategia moze stracic status kandydata bez zadnego
bledu — bo doszly nowe sesje albo bo sprawdzilismy tymczasem setki tysiecy
innych hipotez i poprzeczka poszla w gore. Zdegradowane dostaja status
`stale` i nie sa kasowane: informacja, ze cos przestalo dzialac, tez jest
wiedza.

## Log predykcji

`predict.py` zapisuje kazda predykcje do tabeli `predictions` i rozlicza ja,
gdy pojawia sie dane. Rozliczenie liczone jest na dwa sposoby:
- **close-to-close** — porownywalny z tym, co mierzyl silnik
- **open-to-close** — wykonalny naprawde (wejscie na otwarciu nastepnej sesji)

Roznica miedzy nimi to cena za to, ze sygnal znamy dopiero po zamknieciu.

To jedyny test przeprowadzany na danych, ktorych system nie widzial w chwili
stawiania tezy. Wszystko inne — rating, skarbiec, placebo — mierzy przeszlosc.

## Czego swiadomie NIE robimy

**Kosztow transakcyjnych nie uwzgledniamy** (decyzja z 2026-09-07). Wszystkie
przewagi sa liczone brutto. Przy przewadze rzedu 0.17% na horyzoncie 1D
realny koszt round-trip zjadlby 20-45% wyniku, przy 0.38% na 10D — 10-20%.
Oznacza to, ze ranking moze faworyzowac strategie krotkoterminowe bardziej,
niz uzasadnialaby to praktyka.

## Wynik kalibracji (2026-09-07)

Pierwszy pomiar na 6000 hipotez, 20 powtorzen z przesunieciem cyklicznym
zwrotow:

| | |
|---|---|
| prog teoretyczny sqrt(2 ln N) | 4.17 |
| prog **zmierzony** (mediana) | **3.40** |
| prog ostrozny (95 percentyl) | 3.84 |
| rozrzut miedzy powtorzeniami | 2.95 - 4.34 |
| efektywnych niezaleznych testow | ~321 z 6000 |

**Wspolczynnik korelacji: 18.7x.** Z kazdych 19 sprawdzonych hipotez tylko
jedna niesie niezalezna informacje — reszta to warianty tej samej tezy.
Wzor teoretyczny zakladal, ze wszystkie sa niezalezne, i przez to zawieszal
poprzeczke o 0.77 za wysoko.

Silnik uzywa teraz zmierzonego wspolczynnika (`rating.load_correlation_factor`).
Dla bazy 3.07 mln hipotez prog spadl z 5.47 na 4.90, a liczba hipotez ponad
progiem wzrosla z 702 do 4814.

Uwaga przy czytaniu tej liczby: 4814 to nie 4814 odkryc. Te same efekty
wystepuja w dziesiatkach wariantow (rozne progi, rozne konteksty), wiec
realnych, roznych zjawisk jest tam raczej kilkanascie.

Kalibracje warto powtorzyc po kazdym istotnym rozszerzeniu przestrzeni
hipotez — wspolczynnik zalezy od tego, jak bardzo cechy sa ze soba zwiazane.

## Automatyzacja — co dzieje sie samo

| godzina | gdzie | co |
|---|---|---|
| 8:00 | Apps Script | `dailyUpdate` — swieca z ostatniej sesji do Firestore |
| 8:15 | Apps Script | `verifyMorningData` — kontrola i wpis do arkusza Log |
| 9:00 | Mac (launchd) | `ia3-daily.sh` — sync, push, migawka, predykcja |
| 23:00 | Apps Script | `syncPredictionLog` — zapis predykcji i rozliczenie zaleglych |
| ciagle | Mac | silnik, gdy go uruchomisz; wysylka do Firestore co 15 minut |

Podzial wynika z ograniczen, nie z upodobania. Apps Script ma limit 6 minut
na wykonanie funkcji i nie ma numpy — wiec liczenie zostaje na Macu. Mac bywa
wylaczony — wiec pilnowanie danych i rozliczanie predykcji zostaje w chmurze.
Jedyne, co robia oba, to rozliczanie predykcji; robia to niezaleznie i zgodnie,
bo licza z tych samych cen w Firestore.

**Kalendarz sesji** (`sheets/Market.gs` i ta sama logika w panelu) zna weekendy
i dziewiec swiat NYSE rocznie, w tym ruchome (n-ty poniedzialek miesiaca)
i Wielki Piatek liczony z daty Wielkanocy. Bez tego system raportowalby brak
danych jako blad w dni, gdy sesji po prostu nie bylo — i twierdzil, ze sesja
trwa, w Labor Day.

**Czestotliwosc zapisow do Firestore** zmieniono z "co trzeci cykl" na
"co 15 minut". Cykle maja rozna dlugosc zaleznie od tego, ile hipotez przejdzie
do kosztownych etapow, wiec licznik cykli dawal nieprzewidywalna liczbe zapisow
przy dlugim biegu.
