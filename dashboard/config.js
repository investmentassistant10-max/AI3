// Konfiguracja Firebase dla dashboardu.
//
// Skad to wziac:
//   Firebase Console -> Project settings -> zakladka General
//   -> sekcja "Your apps" -> dodaj aplikacje typu Web (ikona </>)
//   -> skopiuj obiekt firebaseConfig i wklej ponizej.
//
// To sa klucze PUBLICZNE — sa widoczne w kazdej stronie webowej korzystajacej
// z Firebase i same w sobie nie daja dostepu do danych. O tym, co wolno
// odczytac, decyduja reguly bezpieczenstwa Firestore (patrz firebase/firestore.rules).

window.IA3_CONFIG = {
  firebase: {
  apiKey: "AIzaSyCVWMIpL_BOI1WBbzbE0bNTWyMV2KBAuQA",
  authDomain: "ai-3-d55c1.firebaseapp.com",
  projectId: "ai-3-d55c1",
  storageBucket: "ai-3-d55c1.firebasestorage.app",
  messagingSenderId: "899965672530",
  appId: "1:899965672530:web:76ab8ee0e82c43fef4938b"
  },

  // Ile najlepszych strategii pobierac do oceny
  strategyLimit: 300,

  // Ponizej tego ratingu strategia nie bierze udzialu w predykcji
  minRating: 40,

  // Godzina otwarcia sesji w Nowym Jorku (dla odliczania)
  marketOpen: { hour: 9, minute: 30, timeZone: "America/New_York" }
};
