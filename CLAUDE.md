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
- `mt5/XAUUSD_Consensus_EA.mq5` MQL5 port. The owner compiled it and ran it in the MT5 Strategy Tester (real ticks, 2026-01..10, 9/10, H4 filter 7, 0.3 lots: 129 trades, PF 1.44, +$33.9k on $5k; see `reports` notes below). `tests/mql_mirror.py` proves its algorithms equal the Python agents.
- `tester_report.py` + `reportcheck` command: parse an MT5 tester report (`ReportTester-*.html`, UTF-16) and compare it trade by trade with the Python backtest (`--tester-report FILE --csv XAUUSD_H1_long.csv`).

## Findings so far (owner's real MT5 history 2022-2026)
**IMPORTANT: numbers measured before 2026-11-03 used a backtest with a re-entry bug and are SUPERSEDED (they were too high). Trust only the corrected block directly below; older bullets are kept for their qualitative conclusions only.**

### Corrected (after the re-entry fix; EA real-tick tester agrees with Python within ~9% on 2026)
- Python vs EA tester, 2026, 9/10 + H4 filter 7, 0.3 lots: EA 129 trades PF 1.44 net $33,888; Python 125 trades PF 1.41 net $31,078; 115 trades start on the same candle+direction, 99.1% same exit reason, median entry-price diff $0.30. Mismatches are near-pairs ~1 candle apart (suspected: tester-built candles vs exported candles).
- Re-run of the same tester test with the NEWER EA build (has `InpMainScale`, `InpPrintVotes`; H1 tester chart, leverage 1:200): identical 129 trades (same times, directions, exits; entry price diff <= $0.07; net $33,887.46 vs $33,887.58). So the EA update changed nothing and leverage 1:100 vs 1:200 does not matter here. `reportcheck` now also reads `.xlsx` reports (needs openpyxl).
- H1, 8/10 + H4 filter 6, stop/target 2.0, 1% sizing, 2022-01..2026-10: 1,127 trades, **PF 1.10** (train 2022-01..2024-06: 0.99, test: 1.19).
- By agreement level (R multiples; early = to 2024-06, late = after): **8 agree -0.014R (PF 0.97; -0.022/-0.007)**; **9 agree +0.100R (PF 1.22; +0.009/+0.189)**; **10 agree +0.134R (PF 1.31; +0.049/+0.205)**. 8-agree loses money: use 9 or 10 only.
- The edge is regime dependent: ~0 in 2022-mid 2024 even at 9/10, ~+0.19R since mid 2024 (2026 strongest). Next idea: only trade when volatility/trend strength is high (test on full history, both halves).
- Agent view after the fix: RSI Regime (agree 53.3% vs 42.7% win), Bollinger (54.0 vs 46.7), MACD (53.4 vs 42.3) stand out; Stochastic's earlier edge shrank (53.1 vs 50.6). Leave-one-out HURTS flags still just mean 'stricter vote is better'.

### Older findings (inflated by the bug; qualitative only)
- Settings with best evidence: 8-9 of 10 agree, H4 filter needs 6 agents, stop 2.0 x ATR, target 2.0 x ATR. By-year profit factor with 8/10 ~1.20-1.54, 2026 strongest.
- Agreement level matters (H1, R multiples): 8 agree +0.06R (PF 1.13), 9 agree +0.20R (PF 1.50), 10 agree +0.31R (PF 1.91), same direction in both halves. 8-agree trades are near break-even after costs. (superseded: use 9 or 10, never 8).
- Lower timeframes are worse: M15/M30/M5 give equal or lower quality per trade with more cost exposure (M30 with doubled periods: +0.04/+0.16/+0.18R). Stay on H1.
- Tight stops (0.5 ATR) and 12-agent set did not help. More agents hurt. ADX breaks when its period is scaled x12.
- Stochastic Bias (6) agreement correlates with wins on every timeframe (e.g. H1 60.0% vs 50.7%); RSI Regime (3) too. EMA/Donchian/TS-momentum/Supertrend almost never dissent (redundant).
- Leave-one-out "HURTS/helps" verdicts are confounded when agents are redundant (removing one makes the vote stricter). Trust the by-vote-count table.
- Fixed lots are risky on a ~$4.6k demo: 0.5 lots risks ~39% per trade; 0.3 lots ~23%. Bot default sizing is 1% risk.
- Backtests are optimistic; expect live profit factor ~1.1-1.3 with flat stretches. Real-tick tester check: stop fills averaged $0.20 worse than the level (worst $4.6), targets $0.44 better, commission+swap ~1.9% of net profit, so slippage is negligible on H1 with ~$42 median stops.
- **Backtest bug found and fixed 2026-11-03:** `run_backtest` let a new trade open at the OPEN of the same candle in which the previous trade exited (and ignored that candle's range for the new trade). The EA can only enter on the next candle. On synthetic data this overstated PF (2.18 -> 1.78, trades 198 -> 173). **All earlier Python numbers (profit factors, avg R by vote count, by-year tables, sweeps) were measured with the bug and are optimistic by an unknown amount on real data; rerun them.** Relative conclusions (more agreement is better, lower timeframes worse) probably hold; absolute numbers will drop, and the 8-agree bucket may turn negative.
- Tester report of the EA (above): 3 of 10 months lost money; Jan, Mar, Aug made ~94% of the profit; balance drawdown 52.7%, equity drawdown 61.9% at 0.3 fixed lots on $5k; largest loss -$7,288 = the 2 Feb stop ($242.84), identical to Python's stop distance for that trade (ATR matches).

## Pending / ideas
1. Run the `--require 6` comparison on H1 (9/10 vs 9/10 + Stochastic vs 8/10 + Stochastic); adopt only if profit factor improves in BOTH halves. If it helps, add `require` to the EA.
2. EA compiles and trades in the tester. Run `reportcheck` (trade-by-trade vs Python) and `parity` (votes) to confirm they agree. NOTE: the tester report's inputs lacked `InpMainScale` and `InpPrintVotes`, so the owner runs an older EA build: tell them to copy the latest `mt5/XAUUSD_Consensus_EA.mq5`.
2b. DONE for the vote-count table (see corrected block). STILL TO RERUN with the fixed code: by-year report at 9/10, `--require` comparison, optimize sweeps, tfscan, confluence levels.
2c. Owner has local uncommitted edits (13th-agent candidate: `--agents 13`, `--votes`, `--swap`, `tests/test_candidates.py`, `scripts/test_13.ps1`) saved in git branch `local-stash-backup` / the stash; they conflicted in `__main__.py` and were set aside. Merge them deliberately.
3. Forward test 3-4 weeks in dry run (portal paper trades), then compare with the backtest; `agentstats --live` for per-agent live stats.
4. Verify the news feed (`python -m xauusd_agents news`); the EA uses MT5's own calendar instead.
5. The article https://www.litefinance.org/blog/for-investors/gold-trading/best-indicators-for-trading-gold/ was blocked in the cloud: read it, map its indicators to the existing agents, test any genuinely new one on H1 full history (both halves).

## Pitfalls learned
- Wire new CLI flags through the real command line and test it (`tests/test_htf.py::CliPaths`); an earlier `optimize --htf-min` silently did nothing.
- The PowerShell scripts, EA, portal edit flow and MT5 calls were never run against a real terminal by the author. Treat as unverified until the owner confirms.
- Do not select settings from the same data you judge them on; ~54 combinations were already tried on 2022-2026, so improvements are somewhat flattering.
