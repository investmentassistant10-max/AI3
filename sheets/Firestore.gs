/**
 * Minimalny klient Firestore REST dla Apps Script, uwierzytelniany kluczem
 * konta serwisowego (bez zewnętrznych bibliotek — JWT budujemy ręcznie).
 *
 * Wymaga Script Properties (Project Settings -> Script Properties):
 *   FIRESTORE_PROJECT_ID    np. ai-3-d55c1
 *   FIRESTORE_CLIENT_EMAIL  z pliku serviceAccountKey.json (client_email)
 *   FIRESTORE_PRIVATE_KEY   z pliku serviceAccountKey.json (private_key)
 */

function getFirestoreAccessToken_() {
  var cache = CacheService.getScriptCache();
  var cached = cache.get('firestore_token');
  if (cached) return cached;

  var props = PropertiesService.getScriptProperties();
  var clientEmail = props.getProperty('FIRESTORE_CLIENT_EMAIL');
  var privateKey = props.getProperty('FIRESTORE_PRIVATE_KEY').replace(/\\n/g, '\n');

  if (!clientEmail || !privateKey) {
    throw new Error('Brak FIRESTORE_CLIENT_EMAIL / FIRESTORE_PRIVATE_KEY w Script Properties.');
  }

  var header = { alg: 'RS256', typ: 'JWT' };
  var now = Math.floor(Date.now() / 1000);
  var claimSet = {
    iss: clientEmail,
    scope: 'https://www.googleapis.com/auth/datastore',
    aud: 'https://oauth2.googleapis.com/token',
    exp: now + 3600,
    iat: now
  };

  var base64Header = Utilities.base64EncodeWebSafe(JSON.stringify(header)).replace(/=+$/, '');
  var base64Claim = Utilities.base64EncodeWebSafe(JSON.stringify(claimSet)).replace(/=+$/, '');
  var signatureInput = base64Header + '.' + base64Claim;
  var signatureBytes = Utilities.computeRsaSha256Signature(signatureInput, privateKey);
  var signature = Utilities.base64EncodeWebSafe(signatureBytes).replace(/=+$/, '');
  var jwt = signatureInput + '.' + signature;

  var response = UrlFetchApp.fetch('https://oauth2.googleapis.com/token', {
    method: 'post',
    contentType: 'application/x-www-form-urlencoded',
    payload: {
      grant_type: 'urn:ietf:params:oauth:grant-type:jwt-bearer',
      assertion: jwt
    },
    muteHttpExceptions: true
  });

  var result = JSON.parse(response.getContentText());
  if (!result.access_token) {
    throw new Error('Autoryzacja Firestore nie powiodła się: ' + response.getContentText());
  }
  cache.put('firestore_token', result.access_token, result.expires_in - 60);
  return result.access_token;
}

function toFirestoreFields_(obj) {
  var fields = {};
  Object.keys(obj).forEach(function (k) {
    var v = obj[k];
    if (typeof v === 'number') {
      fields[k] = { doubleValue: v };
    } else if (typeof v === 'string') {
      fields[k] = { stringValue: v };
    }
  });
  return fields;
}

/**
 * Zapisuje (upsert) dokumenty do Firestore w paczkach.
 * docs: [{ path: 'spx_daily/2024-01-02', fields: {date, open, high, low, close, volume} }, ...]
 */
function firestoreBatchWrite_(docs) {
  if (!docs.length) return;
  var projectId = PropertiesService.getScriptProperties().getProperty('FIRESTORE_PROJECT_ID');
  var token = getFirestoreAccessToken_();
  var CHUNK = 300;

  for (var i = 0; i < docs.length; i += CHUNK) {
    var chunk = docs.slice(i, i + CHUNK);
    var writes = chunk.map(function (d) {
      return {
        update: {
          name: 'projects/' + projectId + '/databases/(default)/documents/' + d.path,
          fields: toFirestoreFields_(d.fields)
        }
      };
    });

    var resp = UrlFetchApp.fetch(
      'https://firestore.googleapis.com/v1/projects/' + projectId + '/databases/(default)/documents:batchWrite',
      {
        method: 'post',
        contentType: 'application/json',
        headers: { Authorization: 'Bearer ' + token },
        payload: JSON.stringify({ writes: writes }),
        muteHttpExceptions: true
      }
    );

    if (resp.getResponseCode() !== 200) {
      throw new Error('Firestore batchWrite nie powiódł się: ' + resp.getContentText());
    }
    Utilities.sleep(200);
  }
}

/** Szybki test uwierzytelnienia — uruchom ręcznie z edytora Apps Script. */
function testFirestoreConnection() {
  var token = getFirestoreAccessToken_();
  Logger.log('OK, dostałem token: ' + token.substring(0, 20) + '...');
}
