/**
 * Pobiera dzienne świece SP500 (^GSPC) z Yahoo Finance i zapisuje do
 * Firestore w kolekcji `spx_daily` (jeden dokument na dzień, id = YYYY-MM-DD).
 *
 * backfillHistory() — uruchamiasz RAZ ręcznie: ściąga historię od 2000 roku.
 * dailyUpdate()      — spinasz z triggerem czasowym (8:00): dogrywa nowe dni,
 *                       bezpieczne do wielokrotnego odpalania (nadpisuje, nie duplikuje).
 */

function fetchYahooDaily_(period1, period2) {
  var url = 'https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC' +
    '?period1=' + period1 + '&period2=' + period2 + '&interval=1d&events=history';

  var resp = UrlFetchApp.fetch(url, {
    muteHttpExceptions: true,
    headers: { 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)' }
  });

  if (resp.getResponseCode() !== 200) {
    throw new Error('Pobranie z Yahoo nie powiodło się: ' + resp.getResponseCode() + ' ' + resp.getContentText());
  }

  var data = JSON.parse(resp.getContentText());
  var result = data.chart.result[0];
  var ts = result.timestamp || [];
  var q = result.indicators.quote[0];
  var out = [];

  for (var i = 0; i < ts.length; i++) {
    if (q.open[i] == null || q.close[i] == null) continue; // dziury w danych (święta itp.)
    var d = new Date(ts[i] * 1000);
    var dateStr = Utilities.formatDate(d, 'UTC', 'yyyy-MM-dd');
    out.push({
      date: dateStr,
      open: q.open[i],
      high: q.high[i],
      low: q.low[i],
      close: q.close[i],
      volume: q.volume[i] || 0
    });
  }
  return out;
}

function saveCandles_(candles) {
  var docs = candles.map(function (c) {
    return { path: 'spx_daily/' + c.date, fields: c };
  });
  firestoreBatchWrite_(docs);
}

/** Jednorazowy backfill historii od 2000-01-01 do dziś. Uruchom ręcznie z edytora. */
function backfillHistory() {
  var period1 = Math.floor(new Date('2000-01-01T00:00:00Z').getTime() / 1000);
  var period2 = Math.floor(Date.now() / 1000);

  var candles = fetchYahooDaily_(period1, period2);
  Logger.log('Pobrano ' + candles.length + ' świec od 2000 roku.');

  saveCandles_(candles);
  Logger.log('Zapisano do Firestore (kolekcja spx_daily).');
}

/** Codzienna aktualizacja — podepnij pod trigger czasowy o 8:00. */
function dailyUpdate() {
  var period2 = Math.floor(Date.now() / 1000);
  var period1 = period2 - 14 * 24 * 60 * 60; // 14 dni bufora na weekendy/święta

  var candles = fetchYahooDaily_(period1, period2);
  saveCandles_(candles);
  Logger.log('Dzienna aktualizacja: sprawdzonych ' + candles.length + ' ostatnich świec.');
}

/**
 * Ustawia (idempotentnie) codzienny trigger czasowy dla dailyUpdate().
 * Uruchom RAZ ręcznie z edytora. Bezpieczne do wielokrotnego odpalania —
 * usuwa stare triggery dla tej funkcji przed dodaniem nowego, więc się nie
 * zduplikuje. Godzina odpalenia liczona jest w strefie czasowej projektu
 * (Project Settings -> General settings -> Time zone -> ustaw Europe/Warsaw).
 */
function setupDailyTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'dailyUpdate') {
      ScriptApp.deleteTrigger(t);
    }
  });

  ScriptApp.newTrigger('dailyUpdate')
    .timeBased()
    .everyDays(1)
    .atHour(8)
    .nearMinute(0)
    .create();

  Logger.log('Trigger ustawiony: dailyUpdate codziennie ok. 8:00 (strefa czasowa projektu).');
}
