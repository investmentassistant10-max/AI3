"""
Korekta przeszacowania: sciaganie estymat do sredniej populacyjnej.

PROBLEM
Pomiar na losowej probce hipotez pokazal, ze obserwowana przewaga zalezy od
liczby obserwacji: przy 20-40 epizodach srednio 0.378%, przy 400+ tylko 0.049%.
Osmiokrotna roznica. Strategie na malych probach nie sa lepsze — po prostu
przy malej probie srednia odchyla sie mocniej od prawdy w obie strony,
a przez prog ratingu przechodza tylko te, ktore odchylily sie korzystnie.

Skutek: system systematycznie obiecuje wiecej, niz dostarcza. Predykcja
"+0.38%" oparta na strategii z 25 epizodami to w rzeczywistosci prognoza
bliska "+0.08%".

ROZWIAZANIE
Empiryczny Bayes. Kazda obserwowana przewaga jest sciagana w strone sredniej
populacyjnej, tym mocniej, im mniej obserwacji za nia stoi:

    przewaga_skorygowana = mu + (przewaga_obserwowana - mu) * waga
    waga = tau^2 / (tau^2 + sigma^2 / n)

gdzie tau^2 to rozrzut PRAWDZIWYCH efektow miedzy strategiami, a sigma^2/n
to niepewnosc pojedynczego pomiaru. Przy duzej probie waga zbiega do 1
(ufamy pomiarowi), przy malej do 0 (ufamy sredniej).

To nie jest ostroznosciowy chwyt, tylko estymator o mniejszym bledzie
sredniokwadratowym niz surowa srednia — wynik znany od lat szescdziesiatych
(James-Stein), standard w modelach ilościowych.
"""
import numpy as np

# Domyslne parametry, gdyby nie dalo sie ich oszacowac z danych.
# tau to typowy rozmiar PRAWDZIWEJ przewagi (w punktach procentowych),
# sigma to typowy rozrzut pojedynczej transakcji.
DEFAULT_TAU = 0.06
DEFAULT_SIGMA = 1.10


def estimate_hyperparams(edges, n_episodes, sigmas=None):
    """
    Szacuje rozrzut prawdziwych efektow (tau^2) metoda momentow.

    Obserwowana wariancja przewag = wariancja prawdziwych efektow
    + srednia wariancja bledu pomiaru. Odejmujemy to drugie.
    """
    edges = np.asarray(edges, dtype=float)
    n = np.asarray(n_episodes, dtype=float)
    ok = np.isfinite(edges) & np.isfinite(n) & (n > 1)
    if ok.sum() < 30:
        return DEFAULT_TAU, DEFAULT_SIGMA

    edges, n = edges[ok], n[ok]
    if sigmas is None:
        sigma = DEFAULT_SIGMA
    else:
        s = np.asarray(sigmas, dtype=float)[ok]
        sigma = float(np.nanmedian(s[np.isfinite(s)])) or DEFAULT_SIGMA

    observed_var = float(np.var(edges, ddof=1))
    measurement_var = float(np.mean(sigma**2 / n))
    tau2 = max(observed_var - measurement_var, 1e-6)
    return float(np.sqrt(tau2)), float(sigma)


def shrink(edge, n_episodes, tau=DEFAULT_TAU, sigma=DEFAULT_SIGMA, mu=0.0):
    """
    Sciaga pojedyncza estymate. Zwraca (przewaga_skorygowana, waga).

    Waga blizsza 1 = ufamy pomiarowi (duza proba).
    Waga blizsza 0 = ufamy sredniej populacyjnej (mala proba).
    """
    if edge is None or n_episodes is None or n_episodes < 1:
        return mu, 0.0
    tau2 = tau * tau
    se2 = (sigma * sigma) / float(n_episodes)
    weight = tau2 / (tau2 + se2)
    return mu + (edge - mu) * weight, weight


def shrink_array(edges, n_episodes, tau=None, sigma=None, mu=0.0):
    """Sciaga caly wektor estymat; parametry szacuje z danych, jesli ich nie ma."""
    edges = np.asarray(edges, dtype=float)
    n = np.asarray(n_episodes, dtype=float)
    if tau is None or sigma is None:
        tau, sigma = estimate_hyperparams(edges, n)
    tau2 = tau * tau
    se2 = (sigma * sigma) / np.maximum(n, 1.0)
    weight = tau2 / (tau2 + se2)
    return mu + (edges - mu) * weight, weight


def describe(edges, n_episodes):
    """Podsumowanie efektu korekty — do raportow i diagnostyki."""
    edges = np.asarray(edges, dtype=float)
    n = np.asarray(n_episodes, dtype=float)
    tau, sigma = estimate_hyperparams(edges, n)
    shrunk, w = shrink_array(edges, n, tau, sigma)
    return {
        "tau": round(tau, 4),
        "sigma": round(sigma, 3),
        "mean_weight": round(float(np.nanmean(w)), 3),
        "mean_before": round(float(np.nanmean(np.abs(edges))), 4),
        "mean_after": round(float(np.nanmean(np.abs(shrunk))), 4),
        "reduction_pct": round(
            (1 - np.nanmean(np.abs(shrunk)) / max(np.nanmean(np.abs(edges)), 1e-9)) * 100, 1),
    }
