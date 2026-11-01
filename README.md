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
`--balance` sets the start balance, `--lots` forces a fixed lot size (1 lot = 100 oz). Add `--leverage 100` (your broker's leverage) and `--stopout 0.5` to model margin: orders you cannot afford are rejected and positions are force-closed at the stop-out level (reason `STOPOUT`). Extra output: `lowest_equity`, `worst_loss_%_of_equity`, `stop_outs`. Gaps/slippage are still not modelled.

## Dry run with a fixed lot
```
python -m xauusd_agents mt5 --symbol XAUUSD --timeframe H1 --threshold 0.9 --sl-atr 1.5 --tp-atr 2.0 --htf-min 7 --news --lots 0.1 --magic 26003
```
`--lots` replaces risk-based sizing; every log line shows the dollar risk and % of equity. `--max-risk 0.10` (default) skips any trade whose stop would risk more than 10% of equity; `--max-risk 0` removes the cap. Add `--list-trades` to a backtest to print each trade and compare it with the bot log.

## Long-history sweep with the H4 filter
```
python -m xauusd_agents optimize --csv XAUUSD_H1_long.csv --from 2022-01-01 --split 2024-07-01 --htf-min 6 --spread 0.30
```
Chooses threshold/stop/target on the earlier period and checks them on the later one, with the H4 filter on.

## Broker clock / stale-candle guard
The bot compares the last closed candle with your PC clock, converted to broker time. The offset is learned from a fresh tick while the market is open (or fixed with `--server-utc-offset 3`). If the market is closed and no offset is known, it does not trade. Check yours with `python -m xauusd_agents servertime` while gold is trading.

## Run as a background task (Windows)
```
powershell -ExecutionPolicy Bypass -File scripts\install_tasks.ps1                 # dry run, starts at logon
powershell -ExecutionPolicy Bypass -File scripts\install_tasks.ps1 -Mt5Path "C:\Program Files\MetaTrader 5\terminal64.exe"
powershell -ExecutionPolicy Bypass -File scripts\install_tasks.ps1 -LiveOrders     # demo orders ON (deliberate choice)
Start-ScheduledTask -TaskName XAUUSD-Bot
powershell -ExecutionPolicy Bypass -File scripts\uninstall_tasks.ps1
```
Logs go to `logs\bot.log` (rotates daily, 60 days), `logs\crash.log`, and `logs\news_YYYY-MM-DD.txt` (daily briefing, default 06:30). The PC must stay on, awake and logged in, with MT5 open. `python -m xauusd_agents newsbrief` prints the briefing on demand.

## Portal (results page)
```
python -m xauusd_agents portal                       # http://127.0.0.1:8765 (this PC only)
python -m xauusd_agents portal --host 0.0.0.0 --token MYSECRET   # reachable from phone on your Wi-Fi: http://PC-IP:8765/?token=MYSECRET
```
Shows bot status, latest committee votes, paper (dry-run) trades and equity curve, open paper position, recent decisions, running settings and the news briefing. The bot writes `logs/events.jsonl` and `logs/status.json`; the portal only reads them. In dry-run the bot keeps ONE paper position at a time and closes it when a later candle touches SL/TP (SL first if both).

## Editing settings from the portal
The page has a "Change settings while the bot is running" panel: lot size (auto risk-based or fixed 0.01-1.00), risk %, max-risk cap, and a pause switch. Saving needs the edit password (printed when the portal starts; stored in `logs/portal_token.txt`, or pass `--token`). The portal writes `logs/settings.json`; the bot re-reads it every cycle (~15 s), validates it again, and logs each change (`logs/settings_audit.jsonl`, `events.jsonl`). "Reset" deletes the file and returns to the command-line settings. Not editable from the web by design: live/dry-run, symbol, threshold, stop/target, H4 filter. Edits are refused from other websites (Host/Origin checks, JSON-only, password).

## Testing unanimity (10 of 10)
```
python -m xauusd_agents optimize --csv XAUUSD_H1_long.csv --from 2022-01-01 --split 2024-07-01 --htf-min 6 --thresholds 0.7,0.8,0.9,1.0 --sls 2.0 --tps 2.0
python -m xauusd_agents backtest --csv XAUUSD_H1_long.csv --from 2022-01-01 --threshold 1.0 --sl-atr 2.0 --tp-atr 2.0 --htf-min 6 --report
```

## Fixed 0.5 lots
`scripts\run_bot.ps1` starts the background bot with `--lots 0.5 --max-risk 0` (override with `-Lots` / `-MaxRisk`). Manual equivalent: add `"--lots","0.5","--max-risk","0"` to the `mt5` command. The portal's "Reset to startup settings" returns to 0.5.
