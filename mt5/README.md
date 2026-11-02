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

## Running on a lower timeframe (M30, M15, M5)
Agent periods are counted in candles, so on M30 they cover half the time they do on H1. `InpMainScale` multiplies every agent period on
the **signal timeframe only**; the higher timeframe (e.g. H4) always uses the unscaled periods, so the two never interfere.
- M30 + H1 filter: `InpTF = PERIOD_M30`, `InpHtf = PERIOD_H1`, `InpMainScale = 2` (same time spans as H1) or 1.
- M5 + H4 filter: `InpTF = PERIOD_M5`, `InpHtf = PERIOD_H4`, `InpMainScale = 12` (or 1).
- Raise `InpHistoryBars` when scaling up (the longest EMA needs about 3x its length as warm-up): 4000 at scale 2, 9000 at scale 12.
Rounding is "half up" with a minimum of 1, identical to `python -m xauusd_agents ... --agent-scale` (checked by `tests/test_mql_mirror.py`).

## Where to see what the EA is doing
- **Experts tab** (View > Toolbox, Ctrl+T > Experts; not the "Journal" tab, which is the terminal's own log). Every new candle prints:
  `Votes for candle 2026-11-02 10:00:00: 1_EMA_Trend BUY | 2_MACD_Momentum BUY | ... | 10_Linreg_Slope -`,
  then the decision line `XAUUSD ...: HOLD (BUY 6 / SELL 1, need 9) -> HOLD`. When a signal appears it also prints the H4 confirmation,
  and either `DRY-RUN BUY ...` / `PLACED BUY ...` or the reason it was skipped (news, spread, margin, cap, position open...).
  Turn the vote line off with `InpPrintVotes = false`.
- **Log files:** right-click the Experts tab > Open, or the folder `MQL5\Logs` (one file per day).
- **Structured records:** `Common\Files\xauusd_ea\events.jsonl` (every candle with all votes) and the portal.
- **Trades:** Trade tab (open positions), History tab (closed). Orders carry the comment "10-agent consensus" and the magic number.
