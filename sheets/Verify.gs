/**
 * Poranna kontrola (8:15): czy swieca z ostatniej sesji dotarla do Firestore.
 *
 * Uruchamiana 15 minut po dailyUpdate — jesli tamta funkcja zawiodla,
 * dowiesz sie o tym z arkusza, a nie dopiero wtedy, gdy panel pokaze
 * nieaktualne dane.
 *
 * Sam brak nowej swiecy nie jest bledem: w weekend i swieta sesji nie ma.
 * Dlatego kontrola najpierw sprawdza kalendarz (Market.gs), a dopiero potem
 * dane.
 */

function firestoreGetDoc_(path) {
  var projectId = PropertiesService.getScriptProperties().getProperty('FIRESTORE_PROJECT_ID');
  var token = getFirestoreAccessToken_();
  var url = 'https://firestore.googleapis.com/v1/projects/' + projectId +
    '/databases/(default)/documents/' + path;
  var resp = UrlFetchApp.fetch(url, {
    headers: { Authorization: 'Bearer ' + token },
    muteHttpExceptions: true
  });
  if (resp.getResponseCode() === 404) return null;
  if (resp.getResponseCode() !== 200) {
    throw new Error('Firestore ' + resp.getResponseCode() + ': ' + resp.getContentText());
  }
  return JSON.parse(resp.getContentText());
}

/** Wyciaga zwykla wartosc z opakowania Firestore. */
function fsValue_(field) {
  if (!field) return null;
  if (field.doubleValue !== undefined) return Number(field.doubleValue);
  if (field.integerValue !== undefined) return Number(field.integerValue);
  if (field.stringValue !== undefined) return field.stringValue;
  if (field.booleanValue !== undefined) return field.booleanValue;
  return null;
}

/**
 * Kontrola poranna. Podepnij pod trigger czasowy na 8:15.
 */
function verifyMorningData() {
  var tz = Session.getScriptTimeZone();
  var now = new Date();
  var todayIsoStr = Utilities.formatDate(now, tz, 'yyyy-MM-dd');

  var todayStatus = sessionStatus(now);
  var sessionText = todayStatus.open
    ? 'sesja dzisiaj 15:30–22:00'
    : 'giełda zamknięta — ' + todayStatus.label;

  // Ktora swieca powinna juz byc w bazie? Ostatnia sesja przed dzisiaj.
  var expected = previousSessionDay(now);
  var expectedIso = expected ? Utilities.formatDate(expected, 'UTC', 'yyyy-MM-dd') : null;

  var candles, state;
  try {
    var doc = expectedIso ? firestoreGetDoc_('spx_daily/' + expectedIso) : null;
    if (!expectedIso) {
      candles = 'nie ustalono ostatniej sesji';
      state = 'error';
    } else if (doc) {
      var close = fsValue_(doc.fields && doc.fields.close);
      candles = 'OK — ' + expectedIso + ', zamknięcie ' +
        (close !== null ? close.toFixed(2) : '?');
      state = 'ok';
    } else {
      // Świecy nie ma — ale to nie musi znaczyć awarii. Triggery czasowe
      // Apps Script odpalają się w losowej minucie godziny, więc weryfikacja
      // potrafi wyprzedzić aktualizację (9.09.2026: weryfikacja 8:11,
      // dailyUpdate 8:27 — alarm o braku danych, których nikt jeszcze nie
      // zdążył pobrać). Zamiast zgadywać kolejność, dociągamy je tutaj.
      // dailyUpdate jest idempotentny, więc powtórzenie nic nie psuje.
      try {
        dailyUpdate();
        doc = firestoreGetDoc_('spx_daily/' + expectedIso);
      } catch (e2) {
        doc = null;
        Logger.log('Awaryjne dailyUpdate nie powiodło się: ' + e2.message);
      }

      if (doc) {
        var close2 = fsValue_(doc.fields && doc.fields.close);
        candles = 'OK — ' + expectedIso + ', zamknięcie ' +
          (close2 !== null ? close2.toFixed(2) : '?') + ' (dociągnięte przy weryfikacji)';
        state = 'ok';
      } else {
        candles = 'BRAK świecy z ' + expectedIso +
          ' — dailyUpdate uruchomiony ponownie i nadal nic';
        state = 'error';
      }
    }
  } catch (e) {
    candles = 'błąd odczytu: ' + e.message;
    state = 'error';
  }

  // Czy jest predykcja na dzis (stawia ja Python na Macu)
  var predText;
  try {
    var pred = firestoreGetDoc_('predictions/latest');
    if (!pred) {
      predText = 'brak — Mac nie policzył';
    } else {
      var pdate = fsValue_(pred.fields && pred.fields.date);
      var horizons = pred.fields && pred.fields.horizons && pred.fields.horizons.mapValue;
      var n = horizons && horizons.fields ? Object.keys(horizons.fields).length : 0;
      predText = pdate === expectedIso
        ? (n ? 'aktualna (' + n + ' horyzontów)' : 'aktualna, brak sygnału')
        : 'nieaktualna — z ' + pdate;
    }
  } catch (e) {
    predText = 'błąd odczytu: ' + e.message;
  }

  logDay(todayIsoStr, {
    session: sessionText,
    candles: candles,
    candlesState: todayStatus.open ? state : (state === 'ok' ? 'ok' : 'closed'),
    prediction: predText
  });

  Logger.log('Kontrola ' + todayIsoStr + ': ' + sessionText + ' | ' + candles + ' | ' + predText);
}

/**
 * Ustawia (idempotentnie) wszystkie triggery czasowe projektu.
 * Uruchom RAZ recznie po wklejeniu kodu.
 */
function setupAllTriggers() {
  var daily = [
    { fn: 'dailyUpdate', hour: 8 },
    { fn: 'verifyMorningData', hour: 8, minute: 15 },
    { fn: 'syncVolatilityLog', hour: 23 }
  ];
  var hourly = ['hourlySync'];
  var names = daily.map(function (w) { return w.fn; }).concat(hourly);

  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (names.indexOf(t.getHandlerFunction()) !== -1) ScriptApp.deleteTrigger(t);
  });

  daily.forEach(function (w) {
    var b = ScriptApp.newTrigger(w.fn).timeBased().everyDays(1).atHour(w.hour);
    if (w.minute !== undefined) b = b.nearMinute(w.minute);
    b.create();
  });

  hourly.forEach(function (fn) {
    ScriptApp.newTrigger(fn).timeBased().everyHours(1).create();
  });

  Logger.log('Ustawione triggery: ' + names.join(', ') +
    ' (strefa czasowa projektu: ' + Session.getScriptTimeZone() + ')');
}
