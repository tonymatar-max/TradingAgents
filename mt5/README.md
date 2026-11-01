# XAUUSD Consensus EA (MetaTrader 5)

Port of the Python bot. 10 agents vote on each closed candle; a trade needs 8, 9 or 10 of them to agree (dropdown),
optionally confirmed by the same 10 agents on H4. **Dry run by default. Orders are refused on real-money accounts.**
**Not compiled by the author: compile it in MetaEditor and send any error text back.**

## Install
1. In MT5: File > Open Data Folder > `MQL5\Experts` and copy `XAUUSD_Consensus_EA.mq5` there.
2. Open MetaEditor (F4), open the file, press F7 (Compile). It must say `0 errors`.
3. Open an XAUUSD **H1** chart, drag the EA from Navigator > Expert Advisors onto it, tick "Allow Algo Trading".
4. Toolbar: switch Algo Trading on (green). The first decision comes after the next candle closes (`InpActOnStart` = false).

## Inputs (all editable in the EA's properties window)
- **Mode**: `RUN_DRY` (log + paper trades) or `RUN_DEMO` (sends orders, demo accounts only).
- **Committee**: votes needed 8/9/10 (default 9), and each agent on/off. Agent parameters default to the Python values.
- **Higher timeframe**: on/off, timeframe, agents needed (default H4, 6).
- **Stop/target**: ATR multiples (default 2.0 / 2.0).
- **Size**: fixed lots (default 0.3) or risk %, plus a max-risk cap for fixed lots (0 = off).
- **Safety**: daily loss limit, max spread, slippage. **News**: MT5 economic calendar, blackout minutes, importance, currency.

## Records for the portal
The EA writes `events.jsonl` and `status.json` to `Common\Files\xauusd_ea` (tester runs: `xauusd_ea_tester`).
```
python -m xauusd_agents portal --port 8766 --logdir "$env:APPDATA\MetaQuotes\Terminal\Common\Files\xauusd_ea"
```
The portal is view-only for the EA (its edit panel only controls the Python bot); change the EA's inputs in MT5.

## Parity check (do this before trusting it)
Run the EA with all agents on for a few days (or a Strategy Tester run), export the same H1 candles, then:
```
python -m xauusd_agents export --symbol XAUUSD --timeframe H1 --from 2026-01-01 --out XAUUSD_H1_check.csv
python -m xauusd_agents parity --csv XAUUSD_H1_check.csv --events "$env:APPDATA\MetaQuotes\Terminal\Common\Files\xauusd_ea\events.jsonl"
```
Expect ~100% per agent. Small gaps right after the EA starts are normal (indicator warm-up).

## Strategy Tester
Set `InpMode = RUN_DEMO` (otherwise it only logs), model "Every tick based on real ticks". The MT5 calendar is not available
in the tester, so the news filter is skipped there. Expect results close to, not identical to, the Python backtest.

## Running on M30 (or another lower timeframe)
Agent periods are counted in candles, so on M30 they cover half the time they do on H1. Two ways to test:
1. **Same numbers** (periods unchanged), or
2. **Doubled periods** (same time spans as H1). Set these inputs: EMA 40/100/400, MACD 24/52/18, RSI 28, Bollinger 40 (lag 10),
   Donchian 40/20, Stochastic 28/6, ADX 28, Supertrend 20, Momentum 120/40, Regression 60. Also set `InpTF = PERIOD_M30`,
   `InpHtf = PERIOD_H1`, and `InpHistoryBars = 4000` (the 400-period EMA needs a longer warm-up).
The EA uses the same agent inputs for the higher timeframe, whereas the Python `--agent-scale` option scales only the main timeframe,
so run the parity check at default periods (scale 1).
