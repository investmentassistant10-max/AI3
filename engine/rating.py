"""
Rating strategii: trafność, powtarzalność, częstotliwość — plus bramka
na istotność statystyczną skorygowaną o liczbę wykonanych prób.

Ta ostatnia część jest najważniejsza. Przy 50 000 testowanych hipotez
najlepszy wynik na czystym szumie ma t-stat okolo 4.6 (bo maksimum z N
losowych statystyk rosnie jak pierwiastek z 2*ln(N)). Strategia z t-stat 2,
ktora w pojedynczym tescie wygladalaby swietnie, tutaj nie znaczy nic.
"""
import math

# Wagi skladowych ratingu
WEIGHTS = {"accuracy": 0.45, "stability": 0.35, "frequency": 0.20}


# Ile razy mniej mamy NIEZALEZNYCH testow niz sprawdzonych hipotez.
# Nasze hipotezy sa mocno skorelowane: "dist_sma_20 > 2" i "dist_sma_10 > 1.5"
# to praktycznie ta sama teza. Wartosc mierzona empirycznie przez
# calibrate.py; 1.0 znaczy "brak kalibracji, zakladamy najgorsze".
DEFAULT_CORRELATION_FACTOR = 1.0


def load_correlation_factor(conn, default=DEFAULT_CORRELATION_FACTOR):
    """Odczytuje wspolczynnik z ostatniej kalibracji. Brak kalibracji = 1.0."""
    try:
        row = conn.execute(
            """SELECT sample_size, effective_n FROM calibration
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    except Exception:
        return default
    if not row or not row[1]:
        return default
    factor = row[0] / row[1]
    return max(1.0, min(factor, 200.0))   # sanity: nie ufamy skrajnosciom


def significance_threshold(n_trials, correlation_factor=DEFAULT_CORRELATION_FACTOR):
    """
    Prog t-stat, ponizej ktorego wynik jest nieodrozninalny od najlepszego
    przypadku przy takiej liczbie prob.

    Wzor sqrt(2*ln N) zaklada N NIEZALEZNYCH testow. Nasze niezalezne nie sa,
    wiec dzielimy liczbe prob przez zmierzony wspolczynnik korelacji. Bez tego
    prog jest za surowy i odrzucamy hipotezy, ktore moglyby byc prawdziwe.
    """
    if n_trials < 2:
        return 2.0
    effective = max(n_trials / max(correlation_factor, 1.0), 2.0)
    return math.sqrt(2.0 * math.log(effective))


def accuracy_score(edge_hit, edge_mean):
    """
    Trafnosc: o ile punktow procentowych sygnal bije zwykly dzien.
    5 pp przewagi to juz duzo na indeksie, 10 pp to bardzo duzo.
    """
    if edge_hit is None:
        return 0.0
    # kierunek przewagi musi zgadzac sie ze srednim zwrotem — inaczej
    # mamy sygnal, ktory czesciej trafia, ale przegrywa na wielkosci ruchu
    if edge_mean is not None and edge_hit * edge_mean < 0:
        return 0.0
    score = min(abs(edge_hit) / 10.0, 1.0) * 100.0
    return round(score, 1)


def stability_score(periods, overall_edge):
    """
    Powtarzalnosc: w ilu podokresach historii przewaga ma ten sam znak
    co calosc. Efekt obecny tylko w jednym wycinku to zwykle przypadek.
    """
    if not periods or overall_edge is None or overall_edge == 0:
        return 0.0

    usable = [p for p in periods if p["edge_mean"] is not None and p["n_episodes"] >= 5]
    if len(usable) < 2:
        return 0.0

    same_sign = sum(1 for p in usable if p["edge_mean"] * overall_edge > 0)
    ratio = same_sign / len(usable)

    # kara za to, ze czesc okresow byla nieuzywalna (za malo sygnalow)
    coverage = len(usable) / len(periods)
    return round(ratio * coverage * 100.0, 1)


def frequency_score(freq_pct):
    """
    Czestotliwosc: sygnal zbyt rzadki jest bezuzyteczny i niepewny
    statystycznie, zbyt czesty nie jest sygnalem tylko stanem domyslnym.
    Optimum miedzy 2% a 20% dni.
    """
    if freq_pct is None or freq_pct <= 0:
        return 0.0
    if freq_pct < 0.5:
        return round(freq_pct / 0.5 * 30.0, 1)
    if freq_pct < 2.0:
        return round(30.0 + (freq_pct - 0.5) / 1.5 * 50.0, 1)
    if freq_pct <= 20.0:
        return 100.0
    if freq_pct <= 40.0:
        return round(100.0 - (freq_pct - 20.0) / 20.0 * 70.0, 1)
    return 10.0


def compute_rating(stats, periods, n_trials, correlation_factor=DEFAULT_CORRELATION_FACTOR):
    """
    Laczy wszystko w jeden rating 0-100.

    stats    — wynik evaluate()
    periods  — wynik stability_by_period()
    n_trials — ile hipotez sprawdzono do tej pory (korekta na liczbe prob)
    """
    acc = accuracy_score(stats.get("edge_hit"), stats.get("edge_mean"))
    stab = stability_score(periods, stats.get("edge_mean"))
    freq = frequency_score(stats.get("frequency_pct"))

    base = (
        acc * WEIGHTS["accuracy"]
        + stab * WEIGHTS["stability"]
        + freq * WEIGHTS["frequency"]
    )

    # Bramka istotnosci: im wiecej prob, tym wyzej zawieszona poprzeczka.
    t = stats.get("t_stat")
    threshold = significance_threshold(n_trials, correlation_factor)
    if t is None:
        sig_multiplier = 0.0
    else:
        sig_multiplier = min(abs(t) / threshold, 1.0)

    rating = base * sig_multiplier

    return {
        "accuracy_score": acc,
        "stability_score": stab,
        "frequency_score": freq,
        "significance_multiplier": round(sig_multiplier, 3),
        "t_threshold": round(threshold, 2),
        "rating": round(rating, 1),
    }
