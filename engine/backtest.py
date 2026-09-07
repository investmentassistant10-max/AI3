"""
Silnik transakcyjny: wejscie, stop loss, take profit, wyjscie po czasie.

Dwie rzeczy, ktore ten modul traktuje powaznie, a wiekszosc naiwnych
backtestow nie:

1. MOMENT WEJSCIA. Sygnal liczony z ceny zamkniecia znamy dopiero PO
   zamknieciu sesji. Wejscie po tej samej cenie zamkniecia jest wiec
   niewykonalne — to zagladanie w przyszlosc o kilka sekund, ale wystarczy,
   zeby zawyzyc wynik. Domyslnie wchodzimy na otwarciu nastepnego dnia.

2. KOLEJNOSC WEWNATRZ DNIA. Majac tylko dzienne OHLC nie wiemy, czy
   najpierw padlo minimum, czy maksimum. Jesli tego samego dnia cena
   dotknela i stop lossa, i take profitu, naiwny backtest zaklada, ze
   najpierw byl zysk. My domyslnie zakladamy odwrotnie (stop pierwszy) —
   i pokazujemy obie wersje, zeby bylo widac, ile niepewnosci wnosi brak
   danych srodsesyjnych.
"""
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

# --- momenty wejscia ---
ENTRY_CLOSE = "close"          # na zamknieciu dnia sygnalu (uwaga: look-ahead!)
ENTRY_NEXT_OPEN = "next_open"  # na otwarciu nastepnej sesji — wykonalne

# --- zalozenia o kolejnosci wewnatrz dnia ---
ASSUME_PESSIMISTIC = "pessimistic"  # przy remisie: stop trafiony pierwszy
ASSUME_OPTIMISTIC = "optimistic"    # przy remisie: cel trafiony pierwszy


class Bars:
    """Surowe ceny w postaci tablic numpy + gotowe okna do symulacji."""

    def __init__(self, df, max_days=20):
        self.open = df["open"].to_numpy(np.float64)
        self.high = df["high"].to_numpy(np.float64)
        self.low = df["low"].to_numpy(np.float64)
        self.close = df["close"].to_numpy(np.float64)
        self.n = len(self.close)
        self.max_days = max_days
        pad = max_days + 2
        # okna [i, d] = wartosc d dni po dniu i; ogon dopelniony NaN
        self._hw = self._windows(self.high, pad)
        self._lw = self._windows(self.low, pad)
        self._cw = self._windows(self.close, pad)
        self._ow = self._windows(self.open, pad)

    @staticmethod
    def _windows(arr, width):
        padded = np.concatenate([arr, np.full(width, np.nan)])
        return sliding_window_view(padded, width)[: len(arr)]


def simulate(
    bars,
    signal_idx,
    direction=1,
    entry=ENTRY_NEXT_OPEN,
    sl_pct=None,
    tp_pct=None,
    max_days=5,
    assume=ASSUME_PESSIMISTIC,
):
    """
    Symuluje transakcje dla podanych dni sygnalu.

    direction  1 = pozycja dluga, -1 = krotka
    sl_pct     stop loss w % od ceny wejscia (None = brak)
    tp_pct     take profit w % (None = brak)
    max_days   po tylu dniach zamykamy po cenie zamkniecia

    Zwraca tablice zwrotow w % (dodatni = zysk, niezaleznie od kierunku)
    oraz tablice powodow wyjscia.
    """
    if len(signal_idx) == 0:
        return np.array([]), np.array([])

    # --- cena wejscia ---
    if entry == ENTRY_CLOSE:
        entry_price = bars.close[signal_idx]
        offset = 1  # obserwacje zaczynamy od nastepnego dnia
    else:
        # wejscie na otwarciu nastepnej sesji; sygnaly z ostatniego dnia
        # danych odpadaja, bo nie ma juz gdzie wejsc
        valid = signal_idx + 1 < bars.n
        signal_idx = signal_idx[valid]
        if len(signal_idx) == 0:
            return np.array([]), np.array([])
        entry_price = bars.open[signal_idx + 1]
        offset = 1

    # okna kolejnych dni po wejsciu
    highs = bars._hw[signal_idx][:, offset : offset + max_days]
    lows = bars._lw[signal_idx][:, offset : offset + max_days]
    closes = bars._cw[signal_idx][:, offset : offset + max_days]

    ep = entry_price[:, None]

    if direction == 1:
        sl_price = ep * (1 - sl_pct / 100.0) if sl_pct else None
        tp_price = ep * (1 + tp_pct / 100.0) if tp_pct else None
        sl_hit = (lows <= sl_price) if sl_pct else np.zeros_like(highs, bool)
        tp_hit = (highs >= tp_price) if tp_pct else np.zeros_like(highs, bool)
    else:
        sl_price = ep * (1 + sl_pct / 100.0) if sl_pct else None
        tp_price = ep * (1 - tp_pct / 100.0) if tp_pct else None
        sl_hit = (highs >= sl_price) if sl_pct else np.zeros_like(highs, bool)
        tp_hit = (lows <= tp_price) if tp_pct else np.zeros_like(highs, bool)

    sl_hit = np.nan_to_num(sl_hit, nan=False).astype(bool)
    tp_hit = np.nan_to_num(tp_hit, nan=False).astype(bool)

    n_sig = len(signal_idx)
    big = max_days + 10
    first_sl = np.where(sl_hit.any(1), sl_hit.argmax(1), big)
    first_tp = np.where(tp_hit.any(1), tp_hit.argmax(1), big)

    # ktore zdarzenie pierwsze; przy remisie decyduje zalozenie
    if assume == ASSUME_PESSIMISTIC:
        sl_first = first_sl <= first_tp
    else:
        sl_first = first_sl < first_tp

    exit_sl = (first_sl < big) & sl_first
    exit_tp = (first_tp < big) & ~sl_first & (first_tp <= first_sl)

    returns = np.empty(n_sig, dtype=np.float64)
    reasons = np.empty(n_sig, dtype=object)

    # 1) wyjscie na stopie
    if sl_pct:
        r = (sl_price[:, 0] / entry_price - 1.0) * 100.0 * direction
        returns[exit_sl] = r[exit_sl]
        reasons[exit_sl] = "SL"
    # 2) wyjscie na celu
    if tp_pct:
        r = (tp_price[:, 0] / entry_price - 1.0) * 100.0 * direction
        returns[exit_tp] = r[exit_tp]
        reasons[exit_tp] = "TP"
    # 3) wyjscie po czasie
    rest = ~(exit_sl | exit_tp)
    if rest.any():
        last_close = closes[rest, max_days - 1]
        # jesli ostatni dzien wypada poza danymi, bierzemy ostatni znany
        for k, row in enumerate(np.flatnonzero(rest)):
            if np.isnan(last_close[k]):
                valid_c = closes[row][~np.isnan(closes[row])]
                last_close[k] = valid_c[-1] if valid_c.size else entry_price[row]
        returns[rest] = (last_close / entry_price[rest] - 1.0) * 100.0 * direction
        reasons[rest] = "czas"

    return returns, reasons


def summarize(returns, label=""):
    """Podstawowe statystyki serii transakcji."""
    if len(returns) == 0:
        return None
    wins = returns > 0
    gross_win = returns[wins].sum()
    gross_loss = -returns[~wins].sum()
    return {
        "label": label,
        "n": int(len(returns)),
        "mean": round(float(returns.mean()), 4),
        "median": round(float(np.median(returns)), 4),
        "hit_rate": round(float(wins.mean() * 100.0), 2),
        "avg_win": round(float(returns[wins].mean()), 4) if wins.any() else 0.0,
        "avg_loss": round(float(returns[~wins].mean()), 4) if (~wins).any() else 0.0,
        "profit_factor": round(float(gross_win / gross_loss), 3) if gross_loss > 0 else None,
        "total": round(float(returns.sum()), 2),
        "worst": round(float(returns.min()), 3),
        "best": round(float(returns.max()), 3),
    }
