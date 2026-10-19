"""Run with:  python -m unittest discover tests"""
import json
import os
import unittest
from datetime import date, timedelta

from rotation_bot import indicators as ind
from rotation_bot import rules
from rotation_bot.planner import plan_day

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "config.json")))

# Real XLE closes, newest first, as Alpha Vantage returned them on Oct 16 2026
XLE = [68.24, 66.56, 67.63, 67.20, 66.01, 65.08, 65.24, 63.36, 63.75, 63.45, 62.82, 62.70, 61.50, 61.54, 62.10,
       62.04, 62.60, 62.37, 61.78, 62.46, 64.31, 64.48, 64.03, 65.93, 64.53, 65.14, 64.93, 65.31, 64.77, 64.06,
       64.62, 65.10, 64.77, 63.96, 62.68, 62.29, 62.43, 62.06, 63.11, 63.64, 63.75, 63.58, 63.68, 62.58, 61.91,
       61.06, 61.03, 60.93, 60.18, 57.50, 58.16, 57.31, 58.52, 58.79, 59.55, 58.96, 58.65, 57.57, 58.36, 59.62,
       59.38, 59.20, 58.50, 57.94, 57.68, 57.02, 56.50, 56.95, 56.74, 55.08, 54.82, 55.60, 54.64, 53.13, 53.22,
       52.81, 53.11, 53.58, 53.84, 54.09, 53.57, 54.46, 54.06, 53.77, 54.67, 55.36, 55.55, 57.55, 57.12, 58.25,
       57.39, 58.33, 57.67, 58.75, 58.71, 57.96, 57.30, 56.29, 56.95, 56.99][::-1]


def series(n, start, daily_pct, end=date(2026, 10, 16), wiggle=0.0):
    """n trading-day bars ending on `end`, growing daily_pct per day, with an optional zig-zag."""
    out, d, p = [], end, start * (1 + daily_pct / 100) ** n
    days = []
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    p = start
    for i, day in enumerate(days):
        p *= 1 + daily_pct / 100
        out.append((day.isoformat(), round(p * (1 + (wiggle if i % 2 else -wiggle) / 100), 4)))
    return out


def market(spy_pct=0.02, strong=("XLV",), extra=None):
    bars = {"SPY": series(260, 500, spy_pct)}
    for s in rules.SECTORS:
        bars[s] = series(260, 100, 0.25 if s in strong else -0.02, wiggle=0.6 if s in strong else 0)
    bars.update(extra or {})
    return bars


MACRO = {"t10": ("", 5.1), "t3m": ("", 4.28), "curve": 0.82, "vix": ("", 18.0), "cpi": ("", 3.7)}
ACCOUNT = {"equity": 9000.0, "cash": 9000.0, "blocked": False}


class Indicators(unittest.TestCase):
    def test_xle_rsi_matches_sheet(self):
        self.assertAlmostEqual(ind.rsi(XLE), 69.1, delta=0.1)  # sheet and dashboard both showed 69.1

    def test_ema_seed_and_direction(self):
        self.assertEqual(ind.ema([10, 10, 10], 21), 10)
        self.assertGreater(ind.ema(XLE, 9), ind.ema(XLE, 21))


class Rules(unittest.TestCase):
    def test_status_table(self):
        self.assertEqual(rules.sector_status(4.9, 69.1), "LEADING")
        self.assertEqual(rules.sector_status(0.08, 58.3), "WATCH")
        self.assertEqual(rules.sector_status(-2.54, 41.3), "AVOID")
        self.assertEqual(rules.sector_status(-2.89, 54.6), "NEUTRAL")
        self.assertEqual(rules.sector_status(6, 55), "EARLY MOMENTUM")

    def test_deployment(self):
        self.assertEqual(rules.deployment(rules.confidence(1.053, 19.99), 19.99), "Full size")
        self.assertEqual(rules.deployment(rules.confidence(1.053, 20.14), 20.14), "Conservative")
        self.assertEqual(rules.confidence(0.99, 12), "LOW")
        self.assertEqual(rules.regime(0.82, 3.7), "Reflation")


class Planner(unittest.TestCase):
    def stocks(self):
        return {"AAA": series(260, 50, 0.35, wiggle=0.8), "BBB": series(260, 50, 0.30, wiggle=0.8),
                "CCC": series(260, 50, 0.28, wiggle=0.8), "DDD": series(260, 50, -0.1)}

    def test_plan_a_buys_top_three_from_leading_sector(self):
        bars = market(extra=self.stocks())
        res = plan_day("A", CFG, bars, MACRO, {"XLV": ["AAA", "BBB", "CCC", "DDD"]}, {}, ACCOUNT, {})
        buys = [o for o in res["orders"] if o["side"] == "buy"]
        self.assertIsNone(res["halted"], res["log"])
        self.assertEqual([o["symbol"] for o in buys], ["AAA", "BBB", "CCC"], res["log"])
        for o in buys:
            self.assertLessEqual(o["qty"] * o["limit"], 3000.01)

    def test_conservative_blocks_entries(self):
        bars = market(extra=self.stocks())
        m = dict(MACRO, vix=("", 21.0))
        res = plan_day("A", CFG, bars, m, {"XLV": ["AAA"]}, {}, ACCOUNT, {})
        self.assertFalse([o for o in res["orders"] if o["side"] == "buy"])
        self.assertTrue(any("Conservative" in l for l in res["log"]))

    def test_exit_below_21ema(self):
        st = self.stocks()
        st["AAA"][-1] = (st["AAA"][-1][0], st["AAA"][-2][1] * 0.85)  # big drop -> below 21 EMA
        bars = market(extra=st)
        pos = {"AAA": {"qty": 20, "entry": 60.0, "value": 1200}}
        res = plan_day("A", CFG, bars, MACRO, {}, pos, ACCOUNT, {})
        sells = [o for o in res["orders"] if o["side"] == "sell"]
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0]["qty"], 20)
        self.assertIn("SYSTEM EXIT", sells[0]["reason"])
        self.assertIn("AAA", res["state"]["exits"])

    def test_halts_on_stale_data(self):
        bars = market()
        bars["XLE"] = bars["XLE"][:-1]
        res = plan_day("A", CFG, bars, MACRO, {}, {}, ACCOUNT, {})
        self.assertTrue(res["halted"])
        self.assertEqual(res["orders"], [])

    def test_halts_on_crazy_move(self):
        bars = market()
        bars["XLK"][-1] = (bars["XLK"][-1][0], bars["XLK"][-2][1] * 1.5)
        res = plan_day("A", CFG, bars, MACRO, {}, {}, ACCOUNT, {})
        self.assertIn("moved more than", res["halted"])

    def test_drawdown_pause(self):
        bars = market(extra=self.stocks())
        res = plan_day("A", CFG, bars, MACRO, {"XLV": ["AAA"]}, {}, ACCOUNT, {"hwm": 12000})
        self.assertFalse([o for o in res["orders"] if o["side"] == "buy"])
        self.assertTrue(any("Paused" in l for l in res["log"]))

    def test_plan_b_core_and_satellites(self):
        bars = market(extra=self.stocks())
        res = plan_day("B", CFG, bars, MACRO, {"XLV": ["AAA", "BBB", "CCC"]}, {}, ACCOUNT, {})
        buys = {o["symbol"]: o for o in res["orders"] if o["side"] == "buy"}
        self.assertIn("XLV", buys, res["log"])
        self.assertLessEqual(buys["XLV"]["qty"] * buys["XLV"]["limit"], 6300.01)
        self.assertEqual(len(buys), 3, res["log"])

    def test_trim_on_divergence_once(self):
        # rise, pullback, then a marginal new price high on weaker momentum
        up = series(240, 50, 0.4)
        last = up[-1][1]
        tail_days = series(30, 1, 0)  # just for dates
        tail = []
        for i, (d, _) in enumerate(tail_days):
            p = last * (0.94 + 0.002 * i) if i < 29 else last * 1.005
            tail.append((d, p))
        st = {"AAA": [b for b in up if b[0] < tail[0][0]] + tail}
        bars = market(extra=st)
        self.assertTrue(ind.macd_divergence(rules.closes(st["AAA"])))
        pos = {"AAA": {"qty": 100, "entry": 40.0, "value": 9000}}
        res = plan_day("A", CFG, bars, MACRO, {}, pos, ACCOUNT, {})
        trims = [o for o in res["orders"] if "TRIM" in o["reason"]]
        self.assertEqual(len(trims), 1, res["log"])
        self.assertEqual(trims[0]["qty"], 30)
        res2 = plan_day("A", CFG, bars, MACRO, {}, pos, ACCOUNT, res["state"])
        self.assertFalse([o for o in res2["orders"] if "TRIM" in o["reason"]])


if __name__ == "__main__":
    unittest.main()
