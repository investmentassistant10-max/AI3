/**
 * Arkusz "Zmienność" — prognozy i ich rozliczenia.
 *
 * Zastapil arkusz predykcji kierunku. Kierunek indeksu na dziennych swiecach
 * nie przeszedl walidacji kroczacej (korelacja 0.04, zero przewagi nad
 * trywialnym "zawsze wzrost"), wiec zostal z systemu usuniety. Zmiennosc
 * przeszla: korelacja 0.67, R^2 0.45, lepsza od modelu HAR w 8 latach z 10.
 */

var VOL_SHEET = 'Zmienność';
var VOL_HEADERS = [
  'Data', 'Horyzont', 'Teraz %', 'Prognoza %', 'Zmiana %', 'Reżim',
  'Ruch dnia %', 'Stop %', 'Rozliczenie', 'Faktycznie %', 'Błąd %'
];

function volSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(VOL_SHEET) || ss.insertSheet(VOL_SHEET);
  var first = sh.getRange(1, 1, 1, VOL_HEADERS.length);
  if (first.getValue() !== VOL_HEADERS[0]) {
    first.setValues([VOL_HEADERS]).setFontWeight('bold').setBackground('#f1f3f4');
    sh.setFrozenRows(1);
    sh.setColumnWidth(1, 100);
    sh.setColumnWidth(6, 140);
  }
  return sh;
}

function volIndex_(sh) {
  var idx = {}, last = sh.getLastRow();
  if (last < 2) return idx;
  var v = sh.getRange(2, 1, last - 1, 2).getDisplayValues();
  for (var i = 0; i < v.length; i++) idx[v[i][0] + '|' + v[i][1]] = i + 2;
  return idx;
}

/** Zapisuje biezaca prognoze i rozlicza zalegle. Podepnij pod trigger wieczorny. */
function syncVolatilityLog() {
  var doc = firestoreGetDoc_('vol_predictions/latest');
  if (!doc || !doc.fields) {
    Logger.log('Brak prognozy zmiennosci w Firestore.');
    return;
  }

  var sh = volSheet_();
  var idx = volIndex_(sh);
  var date = fsValue_(doc.fields.date);
  var horizons = doc.fields.horizons && doc.fields.horizons.mapValue &&
    doc.fields.horizons.mapValue.fields;

  var added = 0;
  if (date && horizons) {
    Object.keys(horizons).sort(function (a, b) { return Number(a) - Number(b); })
      .forEach(function (h) {
        if (idx[date + '|' + h] !== undefined) return;
        var f = horizons[h].mapValue.fields;
        var row = Math.max(sh.getLastRow() + 1, 2);
        sh.getRange(row, 1, 1, 8).setValues([[
          date, Number(h),
          fsValue_(f.current_vol), fsValue_(f.predicted_vol), fsValue_(f.change_pct),
          fsValue_(f.regime), fsValue_(f.daily_move), fsValue_(f.suggested_stop)
        ]]);
        sh.getRange(row, 1).setNumberFormat('@');
        sh.getRange(row, 3, 1, 2).setNumberFormat('0.0');
        idx[date + '|' + h] = row;
        added++;
      });
  }

  // rozliczenie: zmiennosc zrealizowana liczymy z cen w Firestore
  var settled = settleVolatility_(sh);
  Logger.log('Arkusz zmiennosci: zapisano ' + added + ', rozliczono ' + settled);

  try {
    logDay(todayIso(), { prediction: 'zmienność: +' + added + ', rozliczono ' + settled });
  } catch (e) { /* arkusz Log jest opcjonalny */ }
}

/**
 * Rozlicza prognozy, dla ktorych sa juz ceny.
 * Zmiennosc zrealizowana = pierwiastek ze sredniej kwadratow zwrotow,
 * przeliczony na skale roczna — dokladnie tak samo jak liczy to silnik.
 */
function settleVolatility_(sh) {
  var last = sh.getLastRow();
  if (last < 2) return 0;
  var rows = sh.getRange(2, 1, last - 1, 11).getDisplayValues();
  var settled = 0;

  for (var i = 0; i < rows.length; i++) {
    if (rows[i][8]) continue;                       // juz rozliczony
    var dateIso = rows[i][0], h = Number(rows[i][1]);
    if (!dateIso || !h) continue;

    // zbieramy h kolejnych sesji po dacie prognozy
    var closes = [];
    var d = new Date(dateIso + 'T12:00:00Z');
    var prev = closeOn_(dateIso);
    if (prev === null) continue;
    closes.push(prev);

    var guard = 0;
    while (closes.length <= h && guard < 40) {
      guard++;
      d = new Date(d.getTime() + 864e5);
      if (!sessionStatus(d).open) continue;
      var iso = Utilities.formatDate(d, 'UTC', 'yyyy-MM-dd');
      var c = closeOn_(iso);
      if (c === null) break;
      closes.push(c);
    }
    if (closes.length <= h) continue;               // brak danych, czekamy

    var sumSq = 0;
    for (var k = 1; k < closes.length; k++) {
      var r = (closes[k] / closes[k - 1] - 1) * 100;
      sumSq += r * r;
    }
    var realized = Math.sqrt(sumSq / (closes.length - 1)) * Math.sqrt(252);
    var predicted = parseFloat(String(rows[i][3]).replace(',', '.'));
    var err = (predicted / realized - 1) * 100;

    var rowNum = i + 2;
    sh.getRange(rowNum, 9).setValue(Utilities.formatDate(d, 'UTC', 'yyyy-MM-dd')).setNumberFormat('@');
    sh.getRange(rowNum, 10).setValue(realized).setNumberFormat('0.0');
    sh.getRange(rowNum, 11).setValue(err).setNumberFormat('+0.0;-0.0');
    sh.getRange(rowNum, 11).setBackground(Math.abs(err) < 25 ? '#e6f4ea' : '#fce8e6');
    settled++;
  }
  return settled;
}
