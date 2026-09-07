/**
 * Arkusz "Silnik" — co godzine zaglada do Firestore i dopisuje wiersz.
 *
 * Odpowiada na pytanie, ktore inaczej wymaga podejscia do Maca: czy silnik
 * chodzi, ile juz sprawdzil i czy cos z tego wynika. Kazdy wiersz to jeden
 * odczyt, wiec kolumna "przyrost" pokazuje tempo pracy miedzy odczytami.
 */

var ENGINE_SHEET = 'Silnik';
var ENGINE_HEADERS = [
  'Odczyt', 'Stan', 'Hipotez', 'Przyrost', 'Kandydatów', 'Ponad progiem',
  'Próg |t|', 'Najlepszy rating', 'Max |t|', 'Godzin pracy', 'Cykl'
];

function engineSheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(ENGINE_SHEET);
  if (!sh) sh = ss.insertSheet(ENGINE_SHEET);
  var first = sh.getRange(1, 1, 1, ENGINE_HEADERS.length);
  if (first.getValue() !== ENGINE_HEADERS[0]) {
    first.setValues([ENGINE_HEADERS]).setFontWeight('bold').setBackground('#f1f3f4');
    sh.setFrozenRows(1);
    sh.setColumnWidth(1, 135);
    sh.setColumnWidth(2, 110);
  }
  return sh;
}

/**
 * Odczyt pulsu. Podepnij pod trigger godzinowy.
 *
 * Silnik nie musi dzialac — jesli stoi, puls zostaje ten sam i arkusz
 * pokaze zerowy przyrost. To tez jest informacja.
 */
function syncEngineLog() {
  var doc = firestoreGetDoc_('engine_meta/heartbeat');
  if (!doc || !doc.fields) {
    Logger.log('Brak pulsu w Firestore — silnik nigdy nie wysylal.');
    return;
  }
  var f = doc.fields;
  var sh = engineSheet_();

  var total = fsValue_(f.hypotheses_total) || 0;
  var updatedAt = fsValue_(f.updated_at) || '';

  // ile przybylo od poprzedniego odczytu
  var last = sh.getLastRow();
  var prevTotal = null;
  var prevStamp = null;
  if (last >= 2) {
    prevTotal = Number(sh.getRange(last, 3).getValue()) || null;
    prevStamp = sh.getRange(last, 12).getValue();   // ukryta kolumna ze znacznikiem
  }

  // ten sam puls co poprzednio = silnik nie pisal od ostatniego odczytu
  var stale = prevStamp && String(prevStamp) === String(updatedAt);
  var phase = fsValue_(f.phase) || '';
  var state = stale ? 'bez zmian' : (phase === 'zatrzymany' ? 'zatrzymany' : 'pracuje');

  var row = Math.max(last + 1, 2);
  sh.getRange(row, 1, 1, 11).setValues([[
    Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd HH:mm'),
    state,
    total,
    prevTotal === null ? '' : total - prevTotal,
    fsValue_(f.candidates) || 0,
    fsValue_(f.above_threshold) || 0,
    fsValue_(f.threshold) || '',
    fsValue_(f.best_rating) || '',
    fsValue_(f.max_t) || '',
    fsValue_(f.running_hours) || '',
    fsValue_(f.cycle) || ''
  ]]);
  sh.getRange(row, 12).setValue(updatedAt);          // znacznik do porownania
  sh.hideColumns(12);
  sh.getRange(row, 3, 1, 4).setNumberFormat('#,##0');

  var color = state === 'pracuje' ? '#e6f4ea'
    : state === 'zatrzymany' ? '#fef7e0' : '#f8f9fa';
  sh.getRange(row, 2).setBackground(color);

  Logger.log('Silnik: ' + state + ', ' + total + ' hipotez, ' +
    (prevTotal === null ? '?' : (total - prevTotal)) + ' od ostatniego odczytu');
}

/** Arkusz "Strategie" — top 100 z Firestore, nadpisywany przy kazdym odczycie. */
function syncStrategies() {
  var projectId = PropertiesService.getScriptProperties().getProperty('FIRESTORE_PROJECT_ID');
  var token = getFirestoreAccessToken_();
  var url = 'https://firestore.googleapis.com/v1/projects/' + projectId +
    '/databases/(default)/documents/strategies?pageSize=300';

  var resp = UrlFetchApp.fetch(url, {
    headers: { Authorization: 'Bearer ' + token }, muteHttpExceptions: true
  });
  if (resp.getResponseCode() !== 200) {
    Logger.log('Blad odczytu strategii: ' + resp.getContentText());
    return;
  }
  var docs = (JSON.parse(resp.getContentText()).documents || []);
  if (!docs.length) { Logger.log('Brak strategii w Firestore.'); return; }

  var rows = docs.map(function (d) {
    var f = d.fields || {};
    var conds = [];
    var cd = f.definition && f.definition.mapValue && f.definition.mapValue.fields;
    if (cd && cd.conditions && cd.conditions.arrayValue && cd.conditions.arrayValue.values) {
      conds = cd.conditions.arrayValue.values.map(function (v) {
        var c = v.mapValue.fields;
        return fsValue_(c.feature) + ' ' + fsValue_(c.op) + ' ' + fsValue_(c.threshold);
      });
    }
    return [
      fsValue_(f.rating) || 0,
      fsValue_(f.horizon) || '',
      (fsValue_(f.edge_mean) || 0) > 0 ? 'long' : 'short',
      fsValue_(f.mean),
      fsValue_(f.edge_mean),
      fsValue_(f.hit_rate),
      fsValue_(f.base_hit_rate),
      fsValue_(f.t_stat),
      fsValue_(f.n_episodes),
      fsValue_(f.frequency_pct),
      fsValue_(f.treasury_status) || '',
      conds.join(' ORAZ ')
    ];
  }).sort(function (a, b) { return b[0] - a[0]; });

  var headers = ['Rating', 'Horyzont', 'Kierunek', 'Prognoza %', 'Przewaga %',
    'Trafność %', 'Baza %', 't', 'Epizodów', 'Częstość %', 'Skarbiec', 'Warunki'];

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName('Strategie') || ss.insertSheet('Strategie');
  sh.clear();
  sh.getRange(1, 1, 1, headers.length).setValues([headers])
    .setFontWeight('bold').setBackground('#f1f3f4');
  sh.setFrozenRows(1);
  sh.getRange(2, 1, rows.length, headers.length).setValues(rows);
  sh.getRange(2, 4, rows.length, 2).setNumberFormat('+0.000;-0.000');
  sh.setColumnWidth(12, 420);

  Logger.log('Strategie: zapisano ' + rows.length);
}

/** Arkusz "Wyjścia" — najlepsze kombinacje wejscie + regula wyjscia. */
function syncExitRules() {
  var projectId = PropertiesService.getScriptProperties().getProperty('FIRESTORE_PROJECT_ID');
  var token = getFirestoreAccessToken_();
  var url = 'https://firestore.googleapis.com/v1/projects/' + projectId +
    '/databases/(default)/documents/exit_rules?pageSize=100';

  var resp = UrlFetchApp.fetch(url, {
    headers: { Authorization: 'Bearer ' + token }, muteHttpExceptions: true
  });
  if (resp.getResponseCode() !== 200) {
    Logger.log('Brak regul wyjscia: ' + resp.getResponseCode());
    return;
  }
  var docs = (JSON.parse(resp.getContentText()).documents || []);
  if (!docs.length) { Logger.log('Brak regul wyjscia w Firestore.'); return; }

  var rows = docs.map(function (d) {
    var f = d.fields || {};
    return [
      fsValue_(f.rank) || 0,
      fsValue_(f.entry) || '',
      fsValue_(f.exit) || '',
      fsValue_(f.edge_mean),
      fsValue_(f.mean),
      fsValue_(f.hit_rate),
      fsValue_(f.profit_factor),
      fsValue_(f.worst),
      fsValue_(f.n_trades)
    ];
  }).sort(function (a, b) { return a[0] - b[0]; });

  var headers = ['#', 'Wejście', 'Wyjście', 'Przewaga %', 'Średnia %',
    'Trafność %', 'Profit factor', 'Najgorsza %', 'Transakcji'];

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName('Wyjścia') || ss.insertSheet('Wyjścia');
  sh.clear();
  sh.getRange(1, 1, 1, headers.length).setValues([headers])
    .setFontWeight('bold').setBackground('#f1f3f4');
  sh.setFrozenRows(1);
  sh.getRange(2, 1, rows.length, headers.length).setValues(rows);
  sh.setColumnWidth(2, 320);
  sh.setColumnWidth(3, 210);

  Logger.log('Wyjścia: zapisano ' + rows.length);
}

/** Wszystko naraz — to podpinamy pod trigger godzinowy. */
function hourlySync() {
  syncEngineLog();
  try { syncStrategies(); } catch (e) { Logger.log('Strategie: ' + e.message); }
  try { syncExitRules(); } catch (e) { Logger.log('Wyjścia: ' + e.message); }
}
