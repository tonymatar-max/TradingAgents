# XAUUSD 10-agent consensus trading system: project handoff

Read this first. It is the memory of a long cloud session; the chat history does not transfer.

## What this is
Ten independent strategy agents vote on each closed candle of XAUUSD. A trade needs N of 10 to agree, optionally confirmed by the same
10 agents on a higher timeframe (H4). Python package `xauusd_agents/` (backtest, MT5 bot, portal), MQL5 Expert Advisor in `mt5/`,
PowerShell task scripts in `scripts/`, tests in `tests/` (run: `python -m unittest discover -s tests -t .`, ~80 tests, ~20 s).

## Owner / rules of engagement
- Owner uses **Windows + MetaTrader 5 on a DEMO account**, repo at `C:\claude\tradingagents`, Microsoft-Store Python 3.12, PowerShell.
- **Demo and dry-run only.** Never suggest real money. Bot and EA refuse non-demo accounts; `--live-orders` / `RUN_DEMO` are deliberate opt-ins.
- Be honest: say what was NOT run or tested. Do not call results "good" without out-of-sample evidence. The owner pastes outputs; interpret them.
- PowerShell pastes get scrambled: always give commands as `$a = @("cmd","--flag","value"); python -m xauusd_agents @a` (one array, one flag per string).
- Pushed branch: `claude/eloquent-goodall-7t9v4w`. No PR unless asked.

## Layout
- `agents.py` 10 agents (+2 extra, tested and rejected). `default_agents(scale)` multiplies indicator periods (`--agent-scale`).
- `committee.py` N-of-10 vote, optional `require` (agents that must agree, `--require 6`).
- `backtest.py` bar-by-bar; entry at next open, ATR stop/target, stop-first if both touched, spread, fixed lots, leverage/stop-out, risk cap.
- `htf.py` higher-timeframe confluence (no look-ahead: a candle is usable only after it closed). `optimize.py`, `tfscan.py`, `agent_stats.py`, `parity.py`.
- `mt5_bot.py` live/dry-run bot (paper positions in dry run, portal settings, stale-candle guard with broker-clock offset).
- `portal.py` + `portal.html` local web page; edit panel (lots, risk, cap, pause, votes 7-10, stop/target ATR) behind an edit password.
- `news.py` news blackout (unofficial community feed, fails closed) + daily brief. `mt5_export.py` chunked candle export.
- `mt5/XAUUSD_Consensus_EA.mq5` MQL5 port. **Never compiled** (no MetaEditor in the cloud). `tests/mql_mirror.py` proves its algorithms equal the Python agents.

## Findings so far (all on the owner's real MT5 history 2022-2026 unless stated)
- Settings with best evidence: 8-9 of 10 agree, H4 filter needs 6 agents, stop 2.0 x ATR, target 2.0 x ATR. By-year profit factor with 8/10 ~1.20-1.54, 2026 strongest.
- Agreement level matters (H1, R multiples): 8 agree +0.06R (PF 1.13), 9 agree +0.20R (PF 1.50), 10 agree +0.31R (PF 1.91), same direction in both halves. 8-agree trades are near break-even after costs. Plan: **9 of 10**.
- Lower timeframes are worse: M15/M30/M5 give equal or lower quality per trade with more cost exposure (M30 with doubled periods: +0.04/+0.16/+0.18R). Stay on H1.
- Tight stops (0.5 ATR) and 12-agent set did not help. More agents hurt. ADX breaks when its period is scaled x12.
- Stochastic Bias (6) agreement correlates with wins on every timeframe (e.g. H1 60.0% vs 50.7%); RSI Regime (3) too. EMA/Donchian/TS-momentum/Supertrend almost never dissent (redundant).
- Leave-one-out "HURTS/helps" verdicts are confounded when agents are redundant (removing one makes the vote stricter). Trust the by-vote-count table.
- Fixed lots are risky on a ~$4.6k demo: 0.5 lots risks ~39% per trade; 0.3 lots ~23%. Bot default sizing is 1% risk.
- Backtests are optimistic (exact stop fills, no slippage); expect live profit factor ~1.1-1.3 with flat stretches.

## Pending / ideas
1. Run the `--require 6` comparison on H1 (9/10 vs 9/10 + Stochastic vs 8/10 + Stochastic); adopt only if profit factor improves in BOTH halves. If it helps, add `require` to the EA.
2. Compile the EA in MetaEditor (F7), fix errors, run `parity` against Python on the same candles (expect ~100%).
3. Forward test 3-4 weeks in dry run (portal paper trades), then compare with the backtest; `agentstats --live` for per-agent live stats.
4. Verify the news feed (`python -m xauusd_agents news`); the EA uses MT5's own calendar instead.
5. The article https://www.litefinance.org/blog/for-investors/gold-trading/best-indicators-for-trading-gold/ was blocked in the cloud: read it, map its indicators to the existing agents, test any genuinely new one on H1 full history (both halves).

## Pitfalls learned
- Wire new CLI flags through the real command line and test it (`tests/test_htf.py::CliPaths`); an earlier `optimize --htf-min` silently did nothing.
- The PowerShell scripts, EA, portal edit flow and MT5 calls were never run against a real terminal by the author. Treat as unverified until the owner confirms.
- Do not select settings from the same data you judge them on; ~54 combinations were already tried on 2022-2026, so improvements are somewhat flattering.
