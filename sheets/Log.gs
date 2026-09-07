/**
 * Arkusz "Log" — jeden wiersz na dzien, od A2 w dol, chronologicznie.
 *
 * Sluzy do jednego: zebys po tygodniu nieobecnosci widzial na jednym ekranie,
 * ktore dni system obsluzyl poprawnie, a ktore przespal.
 */

var LOG_SHEET = 'Log';
var LOG_HEADERS = ['Data', 'Dzień', 'Sesja', 'Świece', 'Predykcja', 'Sprawdzono'];

function logSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(LOG_SHEET);
  if (!sh) {
    sh = ss.insertSheet(LOG_SHEET);
  }
  // naglowki w wierszu 1
  var first = sh.getRange(1, 1, 1, LOG_HEADERS.length);
  if (first.getValue() !== LOG_HEADERS[0]) {
    first.setValues([LOG_HEADERS])
      .setFontWeight('bold')
      .setBackground('#f1f3f4');
    sh.setFrozenRows(1);
    sh.setColumnWidth(1, 100);
    sh.setColumnWidth(2, 80);
    sh.setColumnWidth(3, 150);
    sh.setColumnWidth(4, 230);
    sh.setColumnWidth(5, 230);
    sh.setColumnWidth(6, 130);
  }
  return sh;
}

var POLISH_DAYS = ['niedziela', 'poniedziałek', 'wtorek', 'środa', 'czwartek', 'piątek', 'sobota'];

/**
 * Znajduje wiersz danego dnia albo zaklada nowy na koncu.
 * Zwraca numer wiersza.
 */
function dayRow_(sh, dateIso) {
  var last = sh.getLastRow();
  if (last >= 2) {
    var dates = sh.getRange(2, 1, last - 1, 1).getDisplayValues();
    for (var i = 0; i < dates.length; i++) {
      if (dates[i][0] === dateIso) return i + 2;
    }
  }
  var row = Math.max(last + 1, 2);
  var d = new Date(dateIso + 'T12:00:00Z');
  sh.getRange(row, 1).setValue(dateIso).setNumberFormat('@');
  sh.getRange(row, 2).setValue(POLISH_DAYS[d.getUTCDay()]);
  return row;
}

/**
 * Zapisuje status dnia. Pola nieprzekazane zostaja bez zmian, wiec
 * kolejne funkcje w ciagu dnia dopisuja swoje kolumny do tego samego wiersza.
 *
 * fields: { session, candles, prediction }
 */
function logDay(dateIso, fields) {
  var sh = logSheet_();
  var row = dayRow_(sh, dateIso);

  if (fields.session !== undefined) sh.getRange(row, 3).setValue(fields.session);
  if (fields.candles !== undefined) sh.getRange(row, 4).setValue(fields.candles);
  if (fields.prediction !== undefined) sh.getRange(row, 5).setValue(fields.prediction);

  sh.getRange(row, 6).setValue(
    Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'HH:mm')
  );

  // kolorujemy kolumne "Świece": zielono gdy komplet, bursztynowo gdy brak sesji,
  // czerwono gdy sesja byla, a danych nie ma
  if (fields.candlesState) {
    var color = fields.candlesState === 'ok' ? '#e6f4ea'
      : fields.candlesState === 'closed' ? '#fef7e0' : '#fce8e6';
    sh.getRange(row, 4).setBackground(color);
  }
  return row;
}
