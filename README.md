# Rotation Bot (paper trading)

Runs Jacob's Rotation System Rulebook every weekday after the close and places **practice** trades in Alpaca paper accounts. It can never touch real money: the code only talks to Alpaca's paper endpoint.

## What it does each weekday (about 5:40 PM ET)

1. Pulls ~1 year of daily prices from Alpaca (free) and the yield curve, CPI and VIX from FRED (free, no key).
2. Stops and places nothing if any data is missing, stale, or looks wrong.
3. Works out regime, confidence and deployment.
4. Sells any position that closed below its 21 EMA (system exit).
5. Trims 30% on MACD divergence when a position is up 15% or more (once per episode).
6. If deployment is Full size: scans the top 25 stocks of each LEADING sector (ETF RSI under 70), runs the checklist, ranks by 30-day RS, and buys into open slots.
   - **Plan A:** 3 stocks, equal dollars.
   - **Plan B:** 70% in the leading sector ETF, 30% across the top 2 stocks.
7. Sends limit orders for the next open (buy at most 1% above the close, sell at most 1% below).
8. Saves a log in `logs/` and, if set up, pushes a summary to your phone.

Safety stops: max 4 orders a day (exits always go through), no ticker over 70% of the account, new entries pause if the account drops 15% below its high.

## Setup (one time, about 20 minutes)

1. **Alpaca:** sign up free at alpaca.markets. Open **Paper Trading** and generate an API key and secret. That's Plan A. Create a second paper account for Plan B and generate its keys too.
2. **GitHub:** make a free account at github.com, then connect GitHub to Claude in claude.ai Settings → Connectors. Tell Claude, and it will create the repository and upload this code for you.
3. **Add your keys** (never paste them into a chat): in the repository go to Settings → Secrets and variables → Actions → New repository secret, and add `ALPACA_KEY_A`, `ALPACA_SECRET_A`, `ALPACA_KEY_B`, `ALPACA_SECRET_B`.
4. **Phone alerts (optional):** install the free **ntfy** app, subscribe to a made-up topic name (like `jacob-rotation-7x3k`), and add it as a secret named `NTFY_TOPIC`.
5. **First week = dry run.** It logs what it *would* trade without sending orders. Compare it to what you'd do.
6. **Turn on paper trading:** Settings → Secrets and variables → Actions → Variables → New variable `TRADE` = `yes`.

## Changing a rule

All numbers live in `config.json` and match the Rulebook. Change them there, not in the code. The tests in `tests/` run before every daily run; if they fail, nothing trades.
