/**
 * Kalendarz sesji NYSE.
 *
 * Sam weekend nie wystarcza: gielda stoi tez w dziewiec swiat rocznie,
 * a bez tego system raportuje "brak danych" jako blad wtedy, gdy po prostu
 * nie bylo sesji. Wiekszosc tych swiat jest ruchoma (n-ty poniedzialek
 * miesiaca), a Wielki Piatek zalezy od daty Wielkanocy.
 */

/** Wielkanoc (algorytm Meeusa/Jonesa/Butchera) — potrzebna do Wielkiego Piatku. */
function easterSunday_(year) {
  var a = year % 19;
  var b = Math.floor(year / 100);
  var c = year % 100;
  var d = Math.floor(b / 4);
  var e = b % 4;
  var f = Math.floor((b + 8) / 25);
  var g = Math.floor((b - f + 1) / 3);
  var h = (19 * a + b - d - g + 15) % 30;
  var i = Math.floor(c / 4);
  var k = c % 4;
  var l = (32 + 2 * e + 2 * i - h - k) % 7;
  var m = Math.floor((a + 11 * h + 22 * l) / 451);
  var month = Math.floor((h + l - 7 * m + 114) / 31);
  var day = ((h + l - 7 * m + 114) % 31) + 1;
  return new Date(Date.UTC(year, month - 1, day));
}

/** N-ty dzien tygodnia w miesiacu, np. 3. poniedzialek stycznia. */
function nthWeekday_(year, month, weekday, n) {
  var d = new Date(Date.UTC(year, month, 1));
  var shift = (weekday - d.getUTCDay() + 7) % 7;
  return new Date(Date.UTC(year, month, 1 + shift + (n - 1) * 7));
}

/** Ostatni dzien tygodnia w miesiacu, np. ostatni poniedzialek maja. */
function lastWeekday_(year, month, weekday) {
  var d = new Date(Date.UTC(year, month + 1, 0));
  var shift = (d.getUTCDay() - weekday + 7) % 7;
  return new Date(Date.UTC(year, month + 1, 0 - shift));
}

/** Swieta stale przesuwaja sie: sobota -> piatek przed, niedziela -> poniedzialek po. */
function observed_(date) {
  var wd = date.getUTCDay();
  if (wd === 6) return new Date(date.getTime() - 864e5);
  if (wd === 0) return new Date(date.getTime() + 864e5);
  return date;
}

function iso_(d) {
  return Utilities.formatDate(d, 'UTC', 'yyyy-MM-dd');
}

/** Zbior dni, w ktore NYSE jest zamknieta w danym roku. */
function marketHolidays_(year) {
  var h = {};
  var add = function (date, name) { h[iso_(date)] = name; };

  add(observed_(new Date(Date.UTC(year, 0, 1))), 'Nowy Rok');
  add(nthWeekday_(year, 0, 1, 3), 'Dzień Martina Luthera Kinga');
  add(nthWeekday_(year, 1, 1, 3), 'Dzień Prezydentów');
  add(new Date(easterSunday_(year).getTime() - 2 * 864e5), 'Wielki Piątek');
  add(lastWeekday_(year, 4, 1), 'Memorial Day');
  if (year >= 2022) add(observed_(new Date(Date.UTC(year, 5, 19))), 'Juneteenth');
  add(observed_(new Date(Date.UTC(year, 6, 4))), 'Święto Niepodległości USA');
  add(nthWeekday_(year, 8, 1, 1), 'Labor Day');
  add(nthWeekday_(year, 10, 4, 4), 'Święto Dziękczynienia');
  add(observed_(new Date(Date.UTC(year, 11, 25))), 'Boże Narodzenie');
  return h;
}

/**
 * Czy danego dnia odbywa sie sesja? Zwraca obiekt z powodem, jesli nie.
 * date — obiekt Date (liczy sie data kalendarzowa w Nowym Jorku)
 */
function sessionStatus(date) {
  var day = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  var wd = day.getUTCDay();
  if (wd === 0 || wd === 6) {
    return { open: false, reason: 'weekend', label: wd === 6 ? 'sobota' : 'niedziela' };
  }
  var holidays = marketHolidays_(day.getUTCFullYear());
  var name = holidays[iso_(day)];
  if (name) {
    return { open: false, reason: 'swieto', label: name };
  }
  return { open: true, reason: null, label: 'sesja' };
}

/** Ostatni dzien sesyjny PRZED podana data (nie liczac jej samej). */
function previousSessionDay(date) {
  var d = new Date(date.getTime());
  for (var i = 0; i < 14; i++) {
    d = new Date(d.getTime() - 864e5);
    if (sessionStatus(d).open) return d;
  }
  return null;
}

/** Data jako yyyy-MM-dd w strefie arkusza. */
function todayIso() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');
}

/** Szybki podglad — uruchom recznie, zeby sprawdzic kalendarz. */
function testCalendar() {
  var today = new Date();
  var st = sessionStatus(today);
  Logger.log('Dzis: ' + todayIso() + ' -> ' + (st.open ? 'SESJA' : 'ZAMKNIETE (' + st.label + ')'));
  var prev = previousSessionDay(today);
  Logger.log('Ostatnia sesja przed dzis: ' + iso_(prev));
  var hs = marketHolidays_(today.getFullYear());
  Logger.log('Swieta ' + today.getFullYear() + ':');
  Object.keys(hs).sort().forEach(function (k) { Logger.log('  ' + k + '  ' + hs[k]); });
}
