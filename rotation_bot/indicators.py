"""Indicator math. Every function takes closes ordered OLDEST -> NEWEST."""


def ema_series(closes, period):
    """Exponential moving average, seeded with the first close (same as the sheet and dashboard)."""
    if not closes:
        return []
    k = 2 / (period + 1)
    out, e = [], closes[0]
    for x in closes:
        e = k * x + (1 - k) * e
        out.append(e)
    return out


def ema(closes, period):
    s = ema_series(closes, period)
    return s[-1] if s else None


def sma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def rsi(closes, period=14):
    """Wilder RSI. Needs at least period+2 closes."""
    if len(closes) < period + 2:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d > 0:
            gains += d
        else:
            losses -= d
    g, l = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        g = (g * (period - 1) + max(d, 0)) / period
        l = (l * (period - 1) + max(-d, 0)) / period
    if l == 0:
        return 100.0
    return 100 - 100 / (1 + g / l)


def macd_series(closes, fast=12, slow=26):
    f, s = ema_series(closes, fast), ema_series(closes, slow)
    return [a - b for a, b in zip(f, s)]


def macd_divergence(closes, lookback=20):
    """Rulebook definition: today's close is the highest close of the last `lookback` days,
    AND today's MACD is below the highest MACD of the previous `lookback` days."""
    if len(closes) < lookback + 30:
        return False
    m = macd_series(closes)
    price_high = closes[-1] >= max(closes[-lookback:])
    prior_macd_high = max(m[-lookback - 1:-1])
    return bool(price_high and m[-1] < prior_macd_high)


def macd_new_high(closes, lookback=20):
    """MACD made a new `lookback`-day high today (resets a divergence episode)."""
    if len(closes) < lookback + 30:
        return False
    m = macd_series(closes)
    return m[-1] >= max(m[-lookback:])


def angled_up(closes, period, days=5):
    """EMA today is higher than it was `days` trading days ago."""
    s = ema_series(closes, period)
    if len(s) <= days:
        return False
    return s[-1] > s[-1 - days]
