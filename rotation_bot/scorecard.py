"""Daily results history + the copy-trading scorecard.

Benchmarks:
  SPY            - just hold the S&P 500
  Leading basket - each day, hold the sector ETFs that were LEADING at the prior close, equal weight
                   ("would simply buying the leading sector funds have done as well?")
Criteria to start copying the bot with real money (agreed Oct 19, 2026):
  1. At least 2 months (42 trading days) of practice trading
  2. Beats SPY over the same period
  3. Beats the leading-sector basket over the same period
  4. Worst drawdown is one Jacob says he could sit through with real money
"""
import json
import os

MIN_DAYS = 42


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def record(path, day, equities, bars, leading):
    """Add (or replace) today's row. equities = {"A": 9000.0, "B": ...}."""
    h = _load(path, {"days": []})
    closes = {s: b[-1][1] for s, b in bars.items() if b}
    days = [d for d in h["days"] if d["date"] != day]
    prev = days[-1] if days else None
    basket = 100.0
    if prev:
        basket = prev["basket"]
        pc = prev.get("lead_closes", {})
        rets = [closes[s] / pc[s] - 1 for s in pc if s in closes and pc[s]]
        if rets:
            basket *= 1 + sum(rets) / len(rets)
    days.append({"date": day, "spy": closes.get("SPY"), "basket": round(basket, 4),
                 "lead_closes": {s: closes[s] for s in leading if s in closes},
                 **{p: round(v, 2) for p, v in equities.items()}})
    h = {"days": days}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(h, f, indent=1)
    return h


def _max_drawdown(values):
    peak, worst = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst * 100


def render(h):
    days = h.get("days", [])
    if len(days) < 2:
        return "# Bot scorecard\n\nNot enough history yet (needs 2+ trading days)."
    first, last = days[0], days[-1]
    n = len(days) - 1
    spy = (last["spy"] / first["spy"] - 1) * 100
    bsk = (last["basket"] / first["basket"] - 1) * 100
    out = ["# Bot scorecard", "",
           "%s to %s, %d trading days (goal: %d)." % (first["date"], last["date"], n, MIN_DAYS), "",
           "| | Return | Worst drawdown |", "| --- | --- | --- |"]
    for p, name in (("A", "Plan A (stocks)"), ("B", "Plan B (70/30)")):
        vals = [d[p] for d in days if d.get(p)]
        if len(vals) >= 2:
            out.append("| %s | %+.2f%% | %.2f%% |" % (name, (vals[-1] / vals[0] - 1) * 100, _max_drawdown(vals)))
    out.append("| SPY (hold) | %+.2f%% | %.2f%% |" % (spy, _max_drawdown([d["spy"] for d in days])))
    out.append("| Leading-sector basket | %+.2f%% | %.2f%% |" % (bsk, _max_drawdown([d["basket"] for d in days])))
    out += ["", "## Copy-trading checklist"]
    for p, name in (("A", "Plan A"), ("B", "Plan B")):
        vals = [d[p] for d in days if d.get(p)]
        if len(vals) < 2:
            continue
        r = (vals[-1] / vals[0] - 1) * 100
        checks = [("%d+ trading days" % MIN_DAYS, n >= MIN_DAYS), ("beats SPY", r > spy),
                  ("beats leading basket", r > bsk)]
        line = ", ".join("%s %s" % ("✅" if ok else "❌", label) for label, ok in checks)
        out.append("- **%s:** %s. Worst drawdown %.1f%%: could you sit through that with real money?"
                   % (name, line, _max_drawdown(vals)))
    if n < MIN_DAYS:
        out += ["", "Too early to judge. Keep practicing."]
    return "\n".join(out)
