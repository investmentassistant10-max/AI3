/**
 * Arkusz "Predykcje" — co system twierdzil i co z tego wyszlo.
 *
 * To jedyne miejsce w calym projekcie, gdzie wynik jest mierzony na danych,
 * ktorych silnik nie widzial w chwili stawiania tezy. Rating, skarbiec,
 * placebo — wszystko to patrzy wstecz. Ta tabela patrzy w przod.
 *
 * Rozliczenie liczymy tutaj, w chmurze, a nie tylko na Macu, zeby dzialalo
 * takze wtedy, gdy komputer jest wylaczony przez tydzien.
 */

var PRED_SHEET = 'Predykcje';
var PRED_HEADERS = [
  'Data predykcji', 'Horyzont', 'Kierunek', 'Prognoza %', 'Pewność %',
  'Strategii', 'Long/Short', 'Rozliczenie', 'Faktycznie %', 'Wynik'
];

function predSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(PRED_SHEET);
  if (!sh) sh = ss.insertSheet(PRED_SHEET);

  var first = sh.getRange(1, 1, 1, PRED_HEADERS.length);
  if (first.getValue() !== PRED_HEADERS[0]) {
    first.setValues([PRED_HEADERS]).setFontWeight('bold').setBackground('#f1f3f4');
    sh.setFrozenRows(1);
    sh.setColumnWidth(1, 110);
    sh.setColumnWidth(7, 95);
    sh.setColumnWidth(8, 110);
  }
  return sh;
}

/** Dodaje n dni SESYJNYCH (pomija weekendy i swieta). */
function addSessionDays_(date, n) {
  var d = new Date(date.getTime());
  var added = 0;
  var guard = 0;
  while (added < n && guard < 60) {
    guard++;
    d = new Date(d.getTime() + 864e5);
    if (sessionStatus(d).open) added++;
  }
  return added === n ? d : null;
}

function closeOn_(dateIso) {
  var doc = firestoreGetDoc_('spx_daily/' + dateIso);
  if (!doc || !doc.fields) return null;
  return fsValue_(doc.fields.close);
}

/** Mapa "data|horyzont" -> numer wiersza. */
function predIndex_(sh) {
  var idx = {};
  var last = sh.getLastRow();
  if (last < 2) return idx;
  var vals = sh.getRange(2, 1, last - 1, 2).getDisplayValues();
  for (var i = 0; i < vals.length; i++) {
    idx[vals[i][0] + '|' + vals[i][1]] = i + 2;
  }
  return idx;
}

/** Zapisuje biezaca predykcje z Firestore (jeden wiersz na horyzont). */
function recordLatestPrediction_(sh, idx) {
  var pred = firestoreGetDoc_('predictions/latest');
  if (!pred || !pred.fields) return 0;

  var date = fsValue_(pred.fields.date);
  var horizons = pred.fields.horizons && pred.fields.horizons.mapValue &&
    pred.fields.horizons.mapValue.fields;
  if (!date || !horizons) return 0;

  var added = 0;
  Object.keys(horizons).sort(function (a, b) { return Number(a) - Number(b); })
    .forEach(function (h) {
      if (idx[date + '|' + h] !== undefined) return;   // juz zapisana

      var f = horizons[h].mapValue.fields;
      var move = fsValue_(f.expected_move);
      var conf = fsValue_(f.confidence);
      var n = fsValue_(f.n_matched);
      var nl = fsValue_(f.n_long);
      var ns = fsValue_(f.n_short);

      var row = Math.max(sh.getLastRow() + 1, 2);
      sh.getRange(row, 1, 1, 7).setValues([[
        date, Number(h),
        move > 0.02 ? 'wzrost' : move < -0.02 ? 'spadek' : 'neutralnie',
        move, conf, n, nl + ' / ' + ns
      ]]);
      sh.getRange(row, 1).setNumberFormat('@');
      sh.getRange(row, 4).setNumberFormat('+0.000;-0.000');
      sh.getRange(row, 5).setNumberFormat('0');
      idx[date + '|' + h] = row;
      added++;
    });
  return added;
}

/** Rozlicza wiersze, dla ktorych sa juz dane cenowe. */
function settlePredictions_(sh) {
  var last = sh.getLastRow();
  if (last < 2) return 0;

  var rows = sh.getRange(2, 1, last - 1, 10).getDisplayValues();
  var settled = 0;

  for (var i = 0; i < rows.length; i++) {
    var r = rows[i];
    if (r[7]) continue;                       // juz rozliczony
    var dateIso = r[0], horizon = Number(r[1]);
    if (!dateIso || !horizon) continue;

    var target = addSessionDays_(new Date(dateIso + 'T12:00:00Z'), horizon);
    if (!target) continue;
    var targetIso = Utilities.formatDate(target, 'UTC', 'yyyy-MM-dd');

    var closeThen = closeOn_(targetIso);
    var closeBase = closeOn_(dateIso);
    if (closeThen === null || closeBase === null) continue;   // jeszcze nie ma danych

    var actual = (closeThen / closeBase - 1) * 100;
    var predicted = parseFloat(String(r[3]).replace(',', '.'));
    var hit = (actual > 0) === (predicted > 0);

    var rowNum = i + 2;
    sh.getRange(rowNum, 8).setValue(targetIso).setNumberFormat('@');
    sh.getRange(rowNum, 9).setValue(actual).setNumberFormat('+0.000;-0.000');
    sh.getRange(rowNum, 10).setValue(hit ? 'trafiona' : 'chybiona')
      .setBackground(hit ? '#e6f4ea' : '#fce8e6');
    settled++;
  }
  return settled;
}

/**
 * Glowna funkcja arkusza predykcji. Podepnij pod trigger wieczorny.
 */
function syncPredictionLog() {
  var sh = predSheet_();
  var idx = predIndex_(sh);

  var added = recordLatestPrediction_(sh, idx);
  var settled = settlePredictions_(sh);

  var msg = 'zapisano ' + added + ', rozliczono ' + settled;
  Logger.log('Arkusz predykcji: ' + msg);

  try {
    logDay(todayIso(), { prediction: 'log predykcji: ' + msg });
  } catch (e) {
    // arkusz Log jest opcjonalny — brak nie powinien psuc rozliczen
  }
  return { added: added, settled: settled };
}

/** Podsumowanie skutecznosci — wywolaj recznie, wynik w logach. */
function predictionScore() {
  var sh = predSheet_();
  var last = sh.getLastRow();
  if (last < 2) { Logger.log('Brak predykcji.'); return; }

  var rows = sh.getRange(2, 1, last - 1, 10).getDisplayValues()
    .filter(function (r) { return r[9]; });
  if (!rows.length) { Logger.log('Brak rozliczonych predykcji.'); return; }

  var byH = {};
  rows.forEach(function (r) {
    var h = r[1];
    byH[h] = byH[h] || { n: 0, hit: 0 };
    byH[h].n++;
    if (r[9] === 'trafiona') byH[h].hit++;
  });

  Logger.log('Skutecznosc predykcji (' + rows.length + ' rozliczonych):');
  Object.keys(byH).sort(function (a, b) { return a - b; }).forEach(function (h) {
    var s = byH[h];
    Logger.log('  ' + h + 'D: ' + (s.hit / s.n * 100).toFixed(1) + '% z ' + s.n + ' predykcji');
  });
  if (rows.length < 100) {
    Logger.log('Uwaga: przy mniej niz 100 predykcjach te liczby sa bardzo niestabilne.');
  }
}
