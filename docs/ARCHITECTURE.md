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

## Co jest w chmurze, a co zostaje na dysku

| dane | Firestore | dysk |
|---|---|---|
| ceny dzienne | tak | tak |
| top 100 strategii | tak | wszystkie 3.1 mln |
| reguly wyjscia | top 40 | wszystkie |
| migawka rynku, predykcje | tak | tak |
| puls silnika, historia przebiegow | tak | log tekstowy |
| log silnika (5.5 tys. linii) | nie | tak |

Podzial wynika z kosztu zapisu. Kazdy dokument w Firestore to platny zapis,
a darmowy limit to 20 000 dziennie. Dlatego:

- **puls** (jeden maly dokument) idzie co 15 minut — 96 zapisow dziennie
- **strategie** (sto dokumentow) ida co godzine — 2400 zapisow dziennie

Wczesniej strategie szly co kwadrans, czyli 9600 zapisow — polowa darmowego
limitu na dane, ktore zmieniaja sie o kilka pozycji na godzine.

Historia przebiegow (`engine_runs`) jest przycinana do 500 ostatnich punktow,
zeby kolekcja nie rosla bez konca.

## Jak czytac skarbiec (poprawka z 2026-09-08)

Skarbiec to 502 sesje. Strategia o czestotliwosci 5% daje w nim jakies
25 sygnalow — a przy dwudziestu pieciu obserwacjach szansa, ze srednia wyjdzie
w przewidywanym kierunku czystym przypadkiem, to mniej wiecej rzut moneta.

Wynika z tego rzecz, ktora latwo przeoczyc: **"potwierdzona" pojedyncza
strategia nie znaczy prawie nic**. Przy stu sprawdzeniach okolo piecdziesieciu
przejdzie samym przypadkiem, a lista piecdziesieciu potwierdzonych strategii
wyglada przekonujaco mimo ze nie zawiera zadnej informacji.

Skarbiec ma za to moc, zeby ocenic CALA GRUPE naraz:

| wynik | p (z przypadku) | ocena |
|---|---|---|
| 5 z 7 | 0.227 | szum |
| 11 z 20 | 0.412 | szum |
| 14 z 20 | 0.058 | szum |
| 16 z 20 | 0.006 | przypadek nie tlumaczy |
| 18 z 20 | 0.0002 | przypadek nie tlumaczy |

`validate.py` liczy teraz te wartosc (rozklad dwumianowy) i wypisuje werdykt
dla grupy zamiast zostawiac uzytkownika z lista pojedynczych "TAK".

Konsekwencja dla wczesniejszych wynikow: raportowane w tej sesji "5 z 7
potwierdzonych" i "3 z 10" **nie byly dowodem niczego** — pierwszy miesci sie
w przypadku z zapasem, drugi jest ponizej oczekiwanej polowy. Dopiero wynik
rzedu 16 z 20 bylby argumentem.

Prawdziwy test pozostaje ten sam co wczesniej: log predykcji, gdzie kazdy
kolejny dzien dokłada nowa, nigdy wczesniej niewidziana obserwacje — zamiast
kolejny raz odpytywac te same 502 sesje.

## Przeglad z 2026-09-08 — co bylo nie tak

**Predykcja liczyla glosy, nie niezalezne sygnaly.**
Pomiar na danych z 2026-09-04: 356 pasujacych strategii, ale srednia korelacja
miedzy ich sygnalami 0.53, a efektywnie okolo 11 niezaleznych glosow.
Najliczniejsza rodzina liczyla 26 wariantow jednej tezy (te same cechy, rozne
progi). Predykcje wygrywal wiec ten pomysl, ktory mial w bazie najwiecej
wariantow — a liczba wariantow zalezy od tego, jak gesto siatka progow akurat
pokryla dana ceche, czyli od przypadku.

Poprawka: strategie sa grupowane w **rodziny** o wspolnej sygnaturze (zestaw
cech wraz z kierunkiem porownania). Kazda rodzina ma jeden glos, wazony
jakoscia jej najlepszego czlonka, a nie suma czlonkow. Liczba rodzin — nie
liczba strategii — wchodzi tez do pewnosci.

Efekt na tych samych danych: zgodnosc kierunku spadla z 90.6% na 81.6% (1D)
i z 72.8% na 63.1% (3D). Prognoza zmienila sie nieznacznie, ale przestala
zalezec od tego, ile wariantow progu akurat przetrwalo w bazie.

**Baza rosla bez ograniczen.**
3.6 mln hipotez zajmowalo 1.54 GB, bo kazda — takze odrzucona jako zbyt rzadka
czy zbyt czesta — miala pelny wiersz z definicja JSON, opisem i kompletem
statystyk (~426 bajtow). Po odrzuconych potrzebujemy tylko odcisku palca
(deduplikacja) i faktu wykonania proby (prog istotnosci): ~30 bajtow.

Poprawka: tabela `tested` na odrzucone, `compact_db.py` do jednorazowego
przeniesienia istniejacych. Pomiar: 1.54 GB -> ~0.20 GB, czyli 1.34 GB
odzyskane. Deduplikacja i licznik prob dzialaja bez zmian, bo obie tabele sa
liczone razem.

**Uszkodzenie bazy (2026-09-08).** Silnik padl z "database disk image is
malformed" przy zapytaniu skanujacym tabele. Wolnego miejsca bylo 33 GB, wiec
brak miejsca to nie byla przyczyna. Najbardziej prawdopodobne wyjasnienie:
baza byla czytana przez zdalny mount w trakcie, gdy silnik do niej pisal —
SQLite polega na blokadach plikowych, a te przez siec nie dzialaja niezawodnie.

Naprawa techniczna, zeby nie zalezalo to od niczyjej dyscypliny:
- baza pracuje w trybie **WAL** (write-ahead log): czytelnicy widza spojna
  migawke, pisarz dopisuje obok, nikt nikomu nie wchodzi w droge
- `busy_timeout=30000` — polaczenia czekaja na zwolnienie zamiast zglaszac blad
- `synchronous=NORMAL` — przy WAL nadal bezpieczne wobec awarii aplikacji,
  a znaczaco szybsze przy milionach zapisow

Do odzyskania danych z uszkodzonej bazy sluzy `rescue_db.py`. Silnik sprawdza
tez wolne miejsce (start i co dziesiaty cykl) i zatrzymuje sie swiadomie
ponizej 3 GB.

## Zwrot ku zmiennosci (2026-09-08)

### Dlaczego kierunek zostal usuniety

Walidacja kroczaca 2015-2024, 2508 predykcji, kazda postawiona wylacznie na
danych sprzed roku, ktorego dotyczyla:

| miara | wynik |
|---|---|
| trafnosc kierunku | 51.59% |
| trywialne "zawsze wzrost" w tych samych dniach | 53.71% |
| przewaga | **-2.11 pp** |
| korelacja prognoza-rzeczywistosc | 0.042 |
| lat z przewaga | 2 z 10 |

Najostrzejszy dowod przyszedl z rozbicia po sile sygnalu: najmocniejsze
prognozy mialy **zerowa** przewage, najslabsze +3.3 pp. Gdyby sygnal niosl
informacje, zaleznosc bylaby odwrotna. Wielkosc prognozy byla szumem.

To zgodne z tym, czego oczekuje literatura. Cala informacja zawarta w cenie
i wolumenie indeksu jest obserwowana przez tysiace zespolow z lepszymi danymi.

### Dlaczego zmiennosc dziala

Ta sama metoda, te same dane, ten sam kod walidacji:

| model | korelacja | R^2 | blad wzgledny |
|---|---|---|---|
| naiwny ("bedzie jak bylo") | 0.593 | 0.186 | 48.8% |
| HAR (standard literatury) | 0.632 | 0.395 | 43.0% |
| **nasz rozszerzony** | **0.673** | **0.449** | **37.2%** |

Lepszy od HAR w 8 latach z 10. Model wyjasnia 45% wariancji przyszlej
zmiennosci — wobec 0.2% dla kierunku.

Zmiennosc jest przewidywalna, bo jej struktura wynika z mechaniki rynku:
dopasowywania dzwigni, wezwan do uzupelnienia depozytow, wolniejszego
przeplywu informacji. Nie znika od tego, ze wszyscy o niej wiedza — inaczej
niz przewaga kierunkowa, ktora znika w momencie odkrycia.

### Co niesie informacje ponad HAR

Wspolczynniki przy cechach standaryzowanych, model na pelnej historii:

```
log_rv_5           +0.157   kaskada HAR: okno tygodniowe
log_rv_22          +0.096   okno miesieczne
log_rv_66          +0.081   okno kwartalne (poza klasycznym HAR)
semivol_ratio      +0.079   asymetria: ile zmiennosci pochodzi ze spadkow
parkinson_ratio    +0.060   ile mowi zakres dnia ponad same zamkniecia
```

Dwie cechy dolozone poza HAR — asymetria i zakres wewnatrzdzienny — trafily
do pierwszej piatki. To potwierdza, ze efekt dzwigni i informacja z high-low
sa realne, a nie ozdobne.

### Co zostalo z poprzedniego systemu

Silnik przeszukujacy przestrzen strategii **zostaje jako narzedzie badawcze**.
Odpowiada na pytanie "czy ten wzorzec dziala" w kilka minut, z korekta na
liczbe prob, testem placebo i walidacja kroczaca. To jest wartosc sama w sobie —
wiekszosc amatorskich systemow nie ma zadnego z tych zabezpieczen i dlatego
ich wlasciciele latami handluja na iluzjach.

Panel, arkusze i automat pokazuja wylacznie zmiennosc. System, ktory
wyswietla liczbe bez wartosci predykcyjnej, predzej czy pozniej zostanie na
niej oparty.

## Pętla uczenia się — co system robi z własnymi błędami

**1. Uczy się na nowych danych.** Model jest dopasowywany OD NOWA przy każdej
prognozie, na coraz dłuższej historii. Wczorajsza sesja wchodzi do treningu
dzisiaj. Żaden współczynnik nie jest zamrożony.

**2. Weryfikuje własne prognozy.** `settle()` przy każdym uruchomieniu sprawdza,
dla których prognoz są już dane, i dopisuje rzeczywistą zmienność oraz błąd.
To samo robi Apps Script wieczorem w arkuszu Zmienność.

**3. Koryguje systematyczne odchylenie** (`calibration_factor`). Model może
stale zawyżać albo zaniżać — na przykład dlatego, że uczył się na okresie
o innym reżimie. Regresja tego nie naprawi sama, bo w chwili uczenia nie zna
jeszcze swoich przyszłych błędów. Ale my je zapisujemy, więc możemy je wykorzystać.

Liczymy **medianę** stosunku rzeczywistość/prognoza z ostatnich 250 rozliczonych
przypadków i mnożymy przez nią kolejne prognozy. Mediana, nie średnia — jeden
skok zmienności potrafiłby przestawić mnożnik na lata.

Dwa zabezpieczenia:
- **próg 20 rozliczonych prognoz** na horyzont; poniżej system nie rusza niczego,
  bo każda "korekta" byłaby reakcją na szum
- **granice 0.65–1.55**; model mylący się dwukrotnie ma problem poważniejszy
  niż przesunięcie skali i mnożnik nie ma tego maskować

Sprawdzone na danych syntetycznych: przy modelu zaniżającym o 20% mnożnik
zbiega do 1.25, przy zawyżającym o 30% do 0.70, a przy trzykrotnej pomyłce
zatrzymuje się na granicy zamiast udawać, że wszystko naprawił.

Panel pokazuje stan tej pętli: ile prognoz uzbierano, czy korekta jest aktywna
i jaka jest surowa prognoza modelu przed korektą.
