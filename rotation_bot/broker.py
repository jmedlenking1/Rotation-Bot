"""Alpaca trading API (paper by default). Standard library only."""
import json
import urllib.request

PAPER = "https://paper-api.alpaca.markets"
LIVE = "https://api.alpaca.markets"


class Broker:
    def __init__(self, key, secret, live=False):
        self.base = LIVE if live else PAPER
        self.h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
                  "Content-Type": "application/json", "User-Agent": "rotation-bot"}

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=self.h, method=method)
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt else None

    def account(self):
        a = self._req("GET", "/v2/account")
        return {"equity": float(a["equity"]), "cash": float(a["cash"]),
                "blocked": a.get("trading_blocked") or a.get("account_blocked")}

    def positions(self):
        """{symbol: {"qty": int, "entry": avg entry price, "value": market value}}"""
        out = {}
        for p in self._req("GET", "/v2/positions"):
            out[p["symbol"]] = {"qty": int(float(p["qty"])), "entry": float(p["avg_entry_price"]),
                                "value": float(p["market_value"])}
        return out

    def open_orders(self):
        return self._req("GET", "/v2/orders?status=open") or []

    def cancel_all(self):
        return self._req("DELETE", "/v2/orders")

    def limit_order(self, symbol, qty, side, limit_price):
        return self._req("POST", "/v2/orders", {
            "symbol": symbol, "qty": str(int(qty)), "side": side, "type": "limit",
            "time_in_force": "day", "limit_price": "%.2f" % limit_price})
