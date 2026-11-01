"""Higher-timeframe (e.g. H4) confluence filter for lower-timeframe (e.g. H1) signals.

An H1 BUY/SELL is only allowed if at least `min_votes` of the 10 agents, run on the H4 candles,
point the same way (and outnumber the opposite side). NO LOOK-AHEAD: an H4 candle's votes only
become usable once that candle has CLOSED (open time + 4h), and an H1 signal is made at its close.
"""
from __future__ import annotations

import pandas as pd

from .agents import Agent, default_agents
from .backtest import Config, run_backtest
from .committee import Committee

RULES = {"H4": "4h", "D1": "1D"}


def bar_delta(index: pd.DatetimeIndex) -> pd.Timedelta:
    return index.to_series().diff().dropna().mode().iloc[0]


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (df.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna())


def votes_to_direction(table: pd.DataFrame, min_votes: int) -> pd.Series:
    buys, sells = (table == 1).sum(axis=1), (table == -1).sum(axis=1)
    d = pd.Series(0, index=table.index)
    d[(buys >= min_votes) & (buys > sells)] = 1
    d[(sells >= min_votes) & (sells > buys)] = -1
    return d


def htf_direction(ltf: pd.DataFrame, htf: pd.DataFrame, min_votes: int,
                  agents: list[Agent] | None = None) -> pd.Series:
    """+1/-1/0 per LTF bar, using only HTF candles already closed when the LTF bar closes."""
    agents = agents or default_agents()
    table = pd.DataFrame({a.name: a.votes(htf) for a in agents})
    d = votes_to_direction(table, min_votes)
    right = pd.DataFrame({"t": (htf.index + bar_delta(htf.index)).astype("datetime64[ns]"), "d": d.to_numpy()})
    left = pd.DataFrame({"t": (ltf.index + bar_delta(ltf.index)).astype("datetime64[ns]")})
    m = pd.merge_asof(left, right.sort_values("t"), on="t", direction="backward")
    return pd.Series(m["d"].fillna(0).astype(int).to_numpy(), index=ltf.index)


def filter_signals(sig: pd.Series, direction: pd.Series) -> pd.Series:
    return sig.where(sig == direction, 0)


def confluence_table(ltf, htf, start, split, mins=(0, 5, 6, 7, 8, 9), thr=0.9, sl=1.5, tp=2.0,
                     spread=0.30, risk=0.01, agents=None, htf_agents=None) -> pd.DataFrame:
    com, cfg = Committee(agents, threshold=thr), Config(sl_atr=sl, tp_atr=tp, spread=spread, risk_pct=risk)
    table = com.vote_table(ltf)
    base = com.signals(ltf, table)
    split, rows = pd.Timestamp(split), []
    for m in mins:
        sig = base if m == 0 else filter_signals(base, htf_direction(ltf, htf, m, htf_agents))
        tr = ltf[ltf.index < split]
        res = {"all": run_backtest(ltf, com, cfg, start=start, signals=sig)[2],
               "train": run_backtest(tr, com, cfg, start=start, signals=sig.loc[tr.index])[2],
               "test": run_backtest(ltf, com, cfg, start=split, signals=sig)[2]}
        g = lambda k, f: res[k].get(f, float("nan"))
        rows.append({"htf_min": m if m else "off", "trades": g("all", "trades"), "win%": g("all", "win_rate_%"),
                     "pf": g("all", "profit_factor"), "ret%": g("all", "return_%"), "dd%": g("all", "max_drawdown_%"),
                     "train_pf": g("train", "profit_factor"), "test_pf": g("test", "profit_factor"),
                     "test_trades": g("test", "trades")})
    return pd.DataFrame(rows)


def last_closed_direction(htf_df: pd.DataFrame, min_votes: int, agents: list[Agent] | None = None) -> int:
    """Live use: direction implied by the most recent CLOSED htf candle (caller drops the forming one)."""
    agents = agents or default_agents()
    table = pd.DataFrame({a.name: a.votes(htf_df) for a in agents})
    return int(votes_to_direction(table, min_votes).iloc[-1])
