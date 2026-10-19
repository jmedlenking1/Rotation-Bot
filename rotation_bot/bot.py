"""Daily run. Usage:  python -m rotation_bot.bot

Environment variables (set as GitHub secrets):
  ALPACA_KEY_A / ALPACA_SECRET_A   paper account for Plan A (stocks)
  ALPACA_KEY_B / ALPACA_SECRET_B   paper account for Plan B (70/30 ETF + stocks)   [optional]
  TRADE=yes                        actually send orders. Anything else = dry run (log only)
  NTFY_TOPIC                       optional: push the summary to the free ntfy app
Live trading is deliberately NOT wired up: the broker is always the paper endpoint.
"""
import json
import os
import sys
import urllib.request
from datetime import date

from . import data, rules, scorecard
from .broker import Broker
from .planner import plan_day

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(path, default):
    try:
        with open(os.path.join(ROOT, path)) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _save(path, obj):
    full = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=True)


def notify(topic, title, text):
    if not topic:
        return
    try:
        req = urllib.request.Request("https://ntfy.sh/" + topic, data=text.encode()[:3900],
                                     headers={"Title": title[:200]}, method="POST")
        urllib.request.urlopen(req, timeout=20)
    except Exception as e:
        print("ntfy failed:", e)


def main():
    cfg = _load("config.json", None)
    holdings = {k: v for k, v in _load("holdings.json", {}).items() if isinstance(v, list)}
    trade = os.environ.get("TRADE", "").lower() == "yes"
    plans = []
    for p in ("A", "B"):
        k, s = os.environ.get("ALPACA_KEY_" + p), os.environ.get("ALPACA_SECRET_" + p)
        if k and s:
            plans.append((p, Broker(k, s, live=False), k, s))
    if not plans:
        print("No Alpaca keys found. Set ALPACA_KEY_A and ALPACA_SECRET_A.")
        return 1
    key, secret = plans[0][2], plans[0][3]

    accounts = {p: (b.account(), b.positions()) for p, b, _, _ in plans}
    held = set().union(*[set(pos) for _, pos in accounts.values()])
    scan = set()
    for names in holdings.values():
        scan.update(names[: cfg["entry"]["scan_top_n_holdings"]])
    symbols = sorted({"SPY", *rules.SECTORS, *held, *scan})
    bars = data.daily_bars(symbols, key, secret)
    macro = data.macro()

    report, leading = [], None
    for p, broker, _, _ in plans:
        account, positions = accounts[p]
        state_path = "state/plan_%s.json" % p
        res = plan_day(p, cfg, bars, macro, holdings, positions, account, _load(state_path, {}))
        lines = ["## Plan %s — %s — equity $%.2f, cash $%.2f%s" % (
            p, date.today().isoformat(), account["equity"], account["cash"], "" if trade else " (DRY RUN)")]
        lines += ["- " + l for l in res["log"]]
        if trade and res["orders"]:
            try:  # a rerun replaces earlier unfilled orders instead of doubling them
                if broker.open_orders():
                    broker.cancel_all()
                    lines.append("- Cancelled earlier unfilled orders before placing today's.")
            except Exception as e:
                lines.append("- Could not check open orders: %s" % e)
        for o in res["orders"]:
            status = "logged only"
            if trade:
                try:
                    r = broker.limit_order(o["symbol"], o["qty"], o["side"], o["limit"])
                    status = "sent (%s)" % r.get("status")
                except Exception as e:
                    status = "FAILED: %s" % e
            lines.append("- ORDER %s %d %s @ %.2f limit — %s — %s" % (
                o["side"].upper(), o["qty"], o["symbol"], o["limit"], o["reason"], status))
        if not res["orders"]:
            lines.append("- No orders today.")
        if not res["halted"]:
            _save(state_path, res["state"])
            leading = [s for s, v in res["sectors"].items() if v["status"] == "LEADING"]
        report += lines + [""]

    if leading is not None and bars.get("SPY"):
        try:
            hist = scorecard.record(os.path.join(ROOT, "state/history.json"), bars["SPY"][-1][0],
                                    {p: accounts[p][0]["equity"] for p, _, _, _ in plans}, bars, leading)
            with open(os.path.join(ROOT, "scorecard.md"), "w") as f:
                f.write(scorecard.render(hist))
        except Exception as e:
            report.append("- Scorecard update failed: %s" % e)

    text = "\n".join(report)
    print(text)
    _log_path = "logs/%s.md" % date.today().isoformat()
    full = os.path.join(ROOT, _log_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(text)
    notify(os.environ.get("NTFY_TOPIC"), "Rotation bot " + date.today().isoformat(), text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
