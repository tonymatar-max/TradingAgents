# XAUUSD 10-Agent Consensus Trader

Ten independent strategy agents analyse XAUUSD (gold). A trade is opened **only when >= 80% of the agents (8 of 10) vote the same direction**.

| # | Agent | Style |
|---|-------|-------|
| 1 | EMA_Trend | EMA20/50 cross + EMA200 filter |
| 2 | MACD_Momentum | MACD vs signal + histogram |
| 3 | RSI_Regime | RSI above 55 / below 45 regime |
| 4 | Bollinger_Walk | price vs midline with expanding bands |
| 5 | Donchian_Breakout | 20-bar breakout, 10-bar exit |
| 6 | Stochastic_Bias | %K/%D with 50 line |
| 7 | ADX_DI | ADX>20 with +DI/-DI dominance |
| 8 | Supertrend | ATR(10)x3 direction |
| 9 | TS_Momentum | 60/20-bar returns vs ATR noise |
| 10 | Linreg_Slope | 30-bar regression slope, R^2>0.5 |

## Use
```
pip install -r requirements.txt
python -m xauusd_agents demo                         # synthetic data (NOT real prices)
python -m xauusd_agents backtest --csv xauusd.csv    # CSV: time,open,high,low,close
python -m xauusd_agents signal   --csv xauusd.csv    # latest committee decision + each vote
python -m xauusd_agents backtest --csv f.csv --threshold 0.7
python -m unittest discover -s tests -t .
```
Entry is at the next bar's open after consensus; SL/TP are ATR-based (1.5x / 3x); position size risks 1% of equity.

## Important
- Backtest/signal only: **no broker connection** is included. Wire `Committee.decide()` into your broker/MT5 API after you've validated on real data and paper-traded.
- The demo numbers come from synthetic data with built-in trends and prove nothing about real profitability.
- Trend and oscillator agents often disagree, so 80% consensus is rare by design: expect few trades.
- Not financial advice; leveraged gold trading can lose more than you expect.

## MetaTrader 5 demo bot
Windows only. Open MT5, log in to a **DEMO** account, enable Tools > Options > Expert Advisors > "Allow algorithmic trading", and open XAUUSD in Market Watch.
```
pip install -r requirements.txt
python -m xauusd_agents mt5 --symbol XAUUSD --timeframe H1                 # DRY-RUN: logs "would BUY", sends nothing
python -m xauusd_agents mt5 --symbol XAUUSD --timeframe H1 --live-orders   # sends orders (demo account only)
```
Safeguards: refuses non-demo accounts; decides only on closed candles; one position at a time; 3% daily loss limit; every order has SL+TP; lot size rounded down to broker step. Use your broker's gold symbol name (e.g. `GOLD`, `XAUUSD.m`).

## Backtest on real MT5 data from a start date
```
python -m xauusd_agents export   --symbol XAUUSD --timeframe H1 --from 2026-01-01 --out xauusd_h1.csv
python -m xauusd_agents backtest --csv xauusd_h1.csv --from 2026-01-01 --spread 0.30
```
Earlier candles are loaded only to warm up the indicators; trades start at `--from`. Add `--to YYYY-MM-DD` to cap the end.
