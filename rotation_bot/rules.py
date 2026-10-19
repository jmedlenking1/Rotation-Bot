"""The Rotation System Rulebook, as code. Pure functions: no network, no orders."""
from datetime import date, timedelta

from . import indicators as ind

SECTORS = ["XLK", "XLC", "XLI", "XLE", "XLF", "XLY", "XLP", "XLB", "XLV", "XLU", "XLRE"]


# ---------- helpers on a bar list [(date_str, close), ...] oldest -> newest ----------

def closes(bars):
    return [c for _, c in bars]


def close_on_or_before(bars, days_back):
    last = date.fromisoformat(bars[-1][0])
    target = (last - timedelta(days=days_back)).isoformat()
    for d, c in reversed(bars):
        if d <= target:
            return c
    return None


def pct_change(bars, days_back):
    old = close_on_or_before(bars, days_back)
    if not old:
        return None
    return (bars[-1][1] / old - 1) * 100


def rs_vs_spy(bars, spy_bars, days_back=30):
    a, b = pct_change(bars, days_back), pct_change(spy_bars, days_back)
    if a is None or b is None:
        return None
    return a - b


# ---------- market level ----------

def regime(curve, cpi):
    if curve is None or cpi is None:
        return None
    if curve > 0.25:
        return "Reflation" if cpi > 3 else "Risk-On Expansion"
    if curve < -0.25:
        return "Stagflation" if cpi > 3 else "Deflation / Recession"
    return "Late-Cycle"


def confidence(spy_ratio, vix):
    if spy_ratio is None or vix is None:
        return "LOW"
    if spy_ratio < 1:
        return "LOW"
    if vix < 15 and spy_ratio > 1.05:
        return "HIGH"
    if vix > 25:
        return "LOW"
    return "MODERATE"


def deployment(conf, vix):
    if conf == "LOW" or vix is None:
        return "Conservative"
    if conf == "HIGH" and vix < 15:
        return "Max conviction"
    if conf in ("MODERATE", "HIGH") and vix < 20:
        return "Full size"
    return "Conservative"


def can_enter(deploy):
    return deploy in ("Full size", "Max conviction")


# ---------- sector level ----------

def sector_status(rs, rsi_val):
    if rs is None:
        return "DATA ERROR"
    if rsi_val is None:
        return "NEED RSI"
    if rs > 2.5 and rsi_val > 60:
        return "LEADING"
    if rs > 5 and rsi_val <= 60:
        return "EARLY MOMENTUM"
    if rs > 2.5 or (rs > 0 and rsi_val > 55):
        return "WATCH"
    if rs < -5 or (rs < -2.5 and rsi_val < 50):
        return "AVOID"
    return "NEUTRAL"


def sector_table(sector_bars, spy_bars):
    out = {}
    for s in SECTORS:
        b = sector_bars.get(s)
        if not b:
            out[s] = {"status": "DATA ERROR", "rs": None, "rsi": None}
            continue
        c = closes(b)
        rs = rs_vs_spy(b, spy_bars)
        r = ind.rsi(c)
        out[s] = {"status": sector_status(rs, r), "rs": rs, "rsi": r, "close": c[-1]}
    return out


# ---------- stock level ----------

def stock_check(bars, spy_bars, cfg):
    """Returns (passed: bool, reasons: list[str], metrics: dict)."""
    c = closes(bars)
    reasons = []
    if len(c) < 60:
        return False, ["not enough history"], {}
    e9, e21, e50 = ind.ema(c, 9), ind.ema(c, 21), ind.ema(c, 50)
    r = ind.rsi(c)
    rs = rs_vs_spy(bars, spy_bars)
    dist = (c[-1] - e21) / c[-1] * 100
    m = {"close": c[-1], "ema9": e9, "ema21": e21, "ema50": e50, "rsi": r, "rs": rs, "dist": dist}
    if not (e9 > e21 > e50):
        reasons.append("EMA stack not 9>21>50")
    if not (ind.angled_up(c, 9) and ind.angled_up(c, 21) and ind.angled_up(c, 50)):
        reasons.append("EMAs not all angled up")
    if r is None or r >= cfg["max_rsi"]:
        reasons.append("RSI %.1f not below %s" % (r or 0, cfg["max_rsi"]))
    if rs is None or rs <= 0:
        reasons.append("30-day RS vs SPY not positive")
    if c[-1] <= e21:
        reasons.append("close not above 21 EMA")
    elif dist > cfg["max_ema_distance_pct"]:
        reasons.append("%.1f%% above 21 EMA (limit %s%%)" % (dist, cfg["max_ema_distance_pct"]))
    adx_min = cfg.get("adx_min")
    if adx_min:
        reasons.append("ADX rule configured but not implemented yet")
    return (not reasons), reasons, m


def exit_signal(bars):
    c = closes(bars)
    e21 = ind.ema(c, 21)
    return c[-1] < e21, e21


def reentry_ok(bars, sector_is_leading):
    c = closes(bars)
    r = ind.rsi(c)
    e9, e21, e50 = ind.ema(c, 9), ind.ema(c, 21), ind.ema(c, 50)
    return bool(r is not None and 50 <= r <= 60 and e9 > e21 > e50 and sector_is_leading)
