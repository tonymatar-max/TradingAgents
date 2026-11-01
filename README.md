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

## Out-of-sample parameter sweep
```
python -m xauusd_agents optimize --csv xauusd_h1.csv --from 2026-01-01 --split 2026-07-01 --spread 0.30
```
Tests consensus threshold, stop and target sizes on the train period (from -> split) and on the unseen test period (split -> end). Adopt only settings that are good in both and have similar neighbours.

## 12 agents + news blackout
```
# compare the validated 10-agent set with 12 (adds Inside Bar + Initial Balance); backtest first!
python -m xauusd_agents backtest --csv xauusd_h1.csv --from 2026-01-01 --threshold 0.9 --sl-atr 1.5 --tp-atr 2.0 --agents 12 --report
# Initial Balance uses your broker's SERVER clock: set the hour the London session opens in server time
python -m xauusd_agents backtest ... --agents 12 --ib-start-hour 10 --ib-bars 2

python -m xauusd_agents news                      # check the calendar feed parses on your PC
python -m xauusd_agents mt5 --symbol XAUUSD --threshold 0.9 --sl-atr 1.5 --tp-atr 2.0 --news
```
`--news` blocks NEW entries 30 min before / after high-impact USD events (change with `--news-before/--news-after`). It fails closed if no calendar data is available. It is live-only: the free feed has no history, so it is not in backtests. Open positions keep their SL/TP.

## Timeframe scan
```
python -m xauusd_agents tfscan --symbol XAUUSD --tfs M15,M30,H1,H4 --from 2026-01-01 --split 2026-07-01 --threshold 0.9 --sl-atr 1.5 --tp-atr 2.0 --spread 0.30
```
Exports each timeframe from MT5 and runs the same settings on all of them (train / test split).

## H1 + H4 confluence filter
An H1 signal only counts if at least N of the 10 agents, run on H4 candles, point the same way (ties never confirm). H4 candles are only used after they close (no look-ahead; unit-tested).
```
# compare off vs N=5..9 with a train/test split (H4 is built from the H1 csv unless --htf-csv is given)
python -m xauusd_agents confluence --csv XAUUSD_H1.csv --from 2026-01-01 --split 2026-07-01 --threshold 0.9 --sl-atr 1.5 --tp-atr 2.0 --spread 0.30
# single backtest / live dry-run with the filter on
python -m xauusd_agents backtest --csv XAUUSD_H1.csv --from 2026-01-01 --threshold 0.9 --sl-atr 1.5 --tp-atr 2.0 --htf-min 6 --report
python -m xauusd_agents mt5 --symbol XAUUSD --timeframe H1 --threshold 0.9 --sl-atr 1.5 --tp-atr 2.0 --htf-min 6 --news
```

## Fixed lot / small account test
```
python -m xauusd_agents backtest --csv XAUUSD_H1.csv --from 2026-01-01 --threshold 0.9 --sl-atr 1.5 --tp-atr 2.0 --htf-min 7 --balance 5000 --lots 1 --report
```
`--balance` sets the start balance, `--lots` forces a fixed lot size (1 lot = 100 oz). Margin and stop-out levels are NOT modelled; the account is only stopped if equity reaches 0.
