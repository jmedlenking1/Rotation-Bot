"""Decides the day's orders for one plan. Pure: no network, so it can be tested with fake data.

Order of work, matching the Rulebook:
  1. Safety checks on the data (missing, stale, crazy moves) -> halt everything if they fail
  2. Market: regime, confidence, deployment
  3. Exits: any position closing below its 21 EMA is sold in full
  4. Trims: MACD divergence + 15% gain -> sell 30% once per episode
  5. Entries: only if deployment allows and the account isn't in a drawdown pause
"""
import math

from . import indicators as ind
from . import rules


class Halt(Exception):
    pass


def _check_data(bars, needed, max_move_pct):
    ref = bars.get("SPY") or []
    if len(ref) < 210:
        raise Halt("SPY history too short (%d bars); need 210" % len(ref))
    ref_day = ref[-1][0]
    for s in needed:
        b = bars.get(s) or []
        if not b:
            raise Halt("no data for %s" % s)
        if b[-1][0] != ref_day:
            raise Halt("%s is stale: last bar %s, SPY's is %s" % (s, b[-1][0], ref_day))
        if len(b) >= 2 and abs(b[-1][1] / b[-2][1] - 1) * 100 > max_move_pct:
            raise Halt("%s moved more than %s%% in a day; data looks wrong" % (s, max_move_pct))
    return ref_day


def market_view(bars, macro):
    spy = rules.closes(bars["SPY"])
    ma200 = ind.sma(spy, 200)
    ratio = spy[-1] / ma200 if ma200 else None
    vix = macro["vix"][1]
    conf = rules.confidence(ratio, vix)
    dep = rules.deployment(conf, vix)
    reg = rules.regime(macro["curve"], macro["cpi"][1])
    return {"spy": spy[-1], "ma200": ma200, "ratio": ratio, "vix": vix, "confidence": conf,
            "deployment": dep, "regime": reg, "curve": macro["curve"], "cpi": macro["cpi"][1]}


def plan_day(plan, cfg, bars, macro, holdings_map, positions, account, state):
    """Returns dict(orders=[...], log=[...], state=new_state, halted=None|reason)."""
    log, orders = [], []
    st = {"exits": dict(state.get("exits", {})), "divergence": dict(state.get("divergence", {})),
          "hwm": state.get("hwm", 0)}
    safety = cfg["safety"]

    needed = ["SPY"] + rules.SECTORS + list(positions)
    try:
        day = _check_data(bars, needed, safety["max_one_day_move_pct"])
    except Halt as e:
        return {"orders": [], "log": ["HALT: %s. No orders placed." % e], "state": state, "halted": str(e)}
    if account.get("blocked"):
        return {"orders": [], "log": ["HALT: broker says the account is blocked."], "state": state,
                "halted": "account blocked"}

    equity, cash = account["equity"], account["cash"]
    st["hwm"] = max(st["hwm"], equity)
    paused = equity < st["hwm"] * (1 - safety["drawdown_pause_pct"] / 100)

    mv = market_view(bars, macro)
    log.append("Date %s | %s | confidence %s | deployment %s | VIX %s | SPY/200MA %s"
               % (day, mv["regime"], mv["confidence"], mv["deployment"], _f(mv["vix"]), _f(mv["ratio"], 3)))
    secs = rules.sector_table(bars, bars["SPY"])
    leading = sorted([s for s, v in secs.items() if v["status"] == "LEADING"], key=lambda s: -secs[s]["rs"])
    log.append("LEADING: " + (", ".join("%s (RS %+.1f, RSI %.1f)" % (s, secs[s]["rs"], secs[s]["rsi"])
                                        for s in leading) or "none"))
    buyable = [s for s in leading if secs[s]["rsi"] < cfg["entry"]["sector_max_rsi"]]
    for s in leading:
        if s not in buyable:
            log.append("No adds in %s: ETF RSI %.1f is 70+." % (s, secs[s]["rsi"]))

    sell_limit = 1 - cfg["orders"]["sell_limit_below_close_pct"] / 100
    buy_limit = 1 + cfg["orders"]["buy_limit_above_close_pct"] / 100

    # 3. exits
    remaining = {}
    for sym, p in positions.items():
        out, e21 = rules.exit_signal(bars[sym])
        close = bars[sym][-1][1]
        if out:
            orders.append({"symbol": sym, "side": "sell", "qty": p["qty"], "limit": round(close * sell_limit, 2),
                           "reason": "SYSTEM EXIT: close %.2f below 21 EMA %.2f" % (close, e21)})
            st["exits"][sym] = day
            st["divergence"].pop(sym, None)
        else:
            remaining[sym] = p
            log.append("Hold %s: close %.2f, 21 EMA %.2f (%+.1f%%)" % (sym, close, e21, (close / e21 - 1) * 100))

    # 4. trims
    tcfg = cfg["trim"]
    for sym, p in remaining.items():
        c = rules.closes(bars[sym])
        d = st["divergence"].get(sym, {"active": False, "trimmed": False})
        if d["active"] and ind.macd_new_high(c, tcfg["divergence_lookback_days"]):
            d = {"active": False, "trimmed": False}
            log.append("%s: MACD made a new high, divergence episode reset." % sym)
        if ind.macd_divergence(c, tcfg["divergence_lookback_days"]):
            d["active"] = True
            gain = (c[-1] / p["entry"] - 1) * 100
            log.append("%s: MACD divergence on (gain %+.1f%%). No adds." % (sym, gain))
            if gain >= tcfg["min_gain_pct"] and not d["trimmed"]:
                q = math.floor(p["qty"] * tcfg["sell_pct"] / 100)
                if q > 0:
                    orders.append({"symbol": sym, "side": "sell", "qty": q, "limit": round(c[-1] * sell_limit, 2),
                                   "reason": "TRIM %d%%: divergence with %+.1f%% gain" % (tcfg["sell_pct"], gain)})
                    d["trimmed"] = True
        st["divergence"][sym] = d

    # 5. entries
    buys = []
    if not rules.can_enter(mv["deployment"]):
        log.append("No new entries: deployment is %s." % mv["deployment"])
    elif paused:
        log.append("No new entries: account is %.0f%% below its high (%.2f). Paused until you resume."
                   % ((1 - equity / st["hwm"]) * 100, st["hwm"]))
    elif not buyable:
        log.append("No new entries: no LEADING sector under RSI 70.")
    else:
        candidates = _rank_stocks(cfg, bars, holdings_map, buyable, secs, remaining, st, day, log)
        if plan == "A":
            buys = _plan_a(cfg, bars, candidates, remaining, equity, cash, buy_limit, log)
        else:
            buys = _plan_b(cfg, bars, candidates, buyable, remaining, equity, cash, buy_limit, log)

    # safety on the order list: exits/trims always go; buys are capped
    sells = [o for o in orders]
    room = max(0, safety["max_orders_per_day"] - len(sells))
    if len(buys) > room:
        log.append("Order cap: dropping %d buy(s) to stay at %d orders today." % (len(buys) - room,
                                                                               safety["max_orders_per_day"]))
        buys = buys[:room]
    for b in buys:
        if b["qty"] * b["close"] > equity * safety["max_pct_in_one_ticker"] / 100:
            log.append("Blocked %s: would be over %s%% of the account." % (b["symbol"], safety["max_pct_in_one_ticker"]))
            continue
        orders.append({k: b[k] for k in ("symbol", "side", "qty", "limit", "reason")})
    return {"orders": orders, "log": log, "state": st, "halted": None, "market": mv, "sectors": secs}


def _rank_stocks(cfg, bars, holdings_map, buyable, secs, held, st, day, log):
    out = []
    for etf in buyable:
        names = (holdings_map.get(etf) or [])[: cfg["entry"]["scan_top_n_holdings"]]
        if not names:
            log.append("No scan list for %s yet (holdings.json is empty for it)." % etf)
        for sym in names:
            if sym in held or sym not in bars or not bars[sym]:
                continue
            if bars[sym][-1][0] != day:
                continue
            ok, why, m = rules.stock_check(bars[sym], bars["SPY"], cfg["entry"])
            if ok and sym in st["exits"] and not rules.reentry_ok(bars[sym], True):
                ok, why = False, ["re-entry conditions not met after %s exit" % st["exits"][sym]]
            if ok:
                c = rules.closes(bars[sym])
                e21 = ind.ema_series(c, 21)
                m["slope21"] = (e21[-1] / e21[-6] - 1) * 100
                out.append([m["rs"], sym, etf, m])
    _score(cfg, out)
    out.sort(key=lambda x: -x[3]["score"])
    if out:
        log.append("Passed checklist (by score: 60% RS + 40% chart): " + ", ".join(
            "%s %.0f (RS %+.1f)" % (s, m["score"], rs) for rs, s, _, m in out[:8]))
    return [tuple(x) for x in out]


def _score(cfg, cands):
    """Score out of 100. RS share is scaled across today's passers; chart share =
    trend slope (15) + closeness to the 21 EMA (15) + RSI sweet spot 55-68 (10)."""
    if not cands:
        return
    w = cfg.get("ranking", {}).get("rs_weight", 60)
    rss = [x[0] for x in cands]
    lo, hi = min(rss), max(rss)
    max_dist = cfg["entry"]["max_ema_distance_pct"]
    for rs, sym, etf, m in cands:
        rs_part = w * ((rs - lo) / (hi - lo) if hi > lo else 1.0)
        trend = 15 * max(0.0, min(m["slope21"] / 2.0, 1.0))      # 2%+ rise in the 21 EMA over 5 days = full marks
        entry = 15 * max(0.0, 1 - m["dist"] / max_dist)          # right at the 21 EMA = full, at the cap = 0
        r = m["rsi"]
        rsi_pts = 10.0 if 55 <= r <= 68 else (10 * max(0.0, (r - 45) / 10) if r < 55 else 10 * max(0.0, (70 - r) / 2))
        chart = (trend + entry + rsi_pts) * (100 - w) / 40
        m["score"] = rs_part + chart
        m["chart"] = chart


def _buy(sym, dollars, close, buy_limit, reason):
    qty = math.floor(dollars / (close * buy_limit))
    if qty < 1:
        return None
    return {"symbol": sym, "side": "buy", "qty": qty, "limit": round(close * buy_limit, 2), "close": close,
            "reason": reason}


def _plan_a(cfg, bars, candidates, held, equity, cash, buy_limit, log):
    a = cfg["plan_a"]
    slots = min(a["target_positions"], a["max_positions"]) - len(held)
    slot = equity / a["target_positions"]
    buys = []
    for rs, sym, etf, m in candidates:
        if slots <= 0:
            break
        if cash < slot * 0.9:
            log.append("Not enough cash for another %.0f slot (cash %.0f)." % (slot, cash))
            break
        b = _buy(sym, min(slot, cash), m["close"], buy_limit, "ENTRY (%s, RS %+.1f, RSI %.1f)" % (etf, rs, m["rsi"]))
        if b:
            buys.append(b)
            cash -= b["qty"] * b["limit"]
            slots -= 1
    if not buys:
        log.append("Plan A: no buys today.")
    return buys


def _plan_b(cfg, bars, candidates, buyable, held, equity, cash, buy_limit, log):
    b = cfg["plan_b"]
    cores = buyable[:2]
    buys = []
    core_each = equity * b["core_pct"] / 100 / len(cores)
    for etf in cores:
        if etf in held:
            continue
        o = _buy(etf, min(core_each, cash), bars[etf][-1][1], buy_limit, "CORE ENTRY %s" % etf)
        if o:
            buys.append(o)
            cash -= o["qty"] * o["limit"]
    sat_each = equity * (100 - b["core_pct"]) / 100 / b["satellite_count"]
    have = len([s for s in held if s not in rules.SECTORS])
    for rs, sym, etf, m in candidates:
        if have >= b["satellite_count"] or cash < sat_each * 0.9:
            break
        o = _buy(sym, sat_each, m["close"], buy_limit, "SATELLITE ENTRY (%s, RS %+.1f)" % (etf, rs))
        if o:
            buys.append(o)
            cash -= o["qty"] * o["limit"]
            have += 1
    if not buys:
        log.append("Plan B: no buys today.")
    return buys


def _f(v, n=2):
    return "—" if v is None else ("%." + str(n) + "f") % v
