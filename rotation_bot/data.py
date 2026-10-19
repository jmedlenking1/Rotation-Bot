"""Market data. Prices: Alpaca (free IEX feed). Macro: FRED public CSVs (no key needed).
Only the standard library is used so it runs anywhere."""
import csv
import io
import json
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta

ALPACA_DATA = "https://data.alpaca.markets/v2/stocks/bars"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="


def _get(url, headers=None, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or {"User-Agent": "rotation-bot"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode()
        except Exception as e:  # network hiccup: retry with backoff
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError("GET failed: %s (%s)" % (url.split("?")[0], last))


def daily_bars(symbols, key, secret, days=320, feed="iex"):
    """{symbol: [(YYYY-MM-DD, close, high, low), ...] oldest -> newest}, split/dividend adjusted."""
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "User-Agent": "rotation-bot"}
    start = (date.today() - timedelta(days=days)).isoformat()
    out = {s: [] for s in symbols}
    syms = list(symbols)
    for i in range(0, len(syms), 50):
        chunk, token = syms[i:i + 50], None
        while True:
            q = {"symbols": ",".join(chunk), "timeframe": "1Day", "start": start, "limit": 10000,
                 "adjustment": "all", "feed": feed}
            if token:
                q["page_token"] = token
            data = json.loads(_get(ALPACA_DATA + "?" + urllib.parse.urlencode(q), headers))
            for sym, bars in (data.get("bars") or {}).items():
                out[sym].extend((b["t"][:10], float(b["c"]), float(b["h"]), float(b["l"])) for b in bars)
            token = data.get("next_page_token")
            if not token:
                break
    for s in out:
        out[s].sort()
    return out


def fred_latest(series_id):
    """Latest (date, value) of a FRED series, skipping blanks."""
    text = _get(FRED_CSV + series_id)
    rows = [r for r in csv.reader(io.StringIO(text))][1:]
    for d, v in reversed(rows):
        try:
            return d, float(v)
        except ValueError:
            continue
    return None, None


def cpi_yoy():
    text = _get(FRED_CSV + "CPIAUCSL")
    rows = []
    for d, v in [r for r in csv.reader(io.StringIO(text))][1:]:
        try:
            rows.append((d, float(v)))
        except ValueError:
            pass
    d, v = rows[-1]
    prev = dict(rows).get("%d%s" % (int(d[:4]) - 1, d[4:]))
    return (d, (v / prev - 1) * 100) if prev else (d, None)


def macro():
    t10 = fred_latest("DGS10")
    t3m = fred_latest("DGS3MO")
    vix = fred_latest("VIXCLS")
    cpi = cpi_yoy()
    curve = t10[1] - t3m[1] if t10[1] is not None and t3m[1] is not None else None
    return {"t10": t10, "t3m": t3m, "curve": curve, "vix": vix, "cpi": cpi}
