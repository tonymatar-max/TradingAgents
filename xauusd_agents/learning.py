"""Learning from losing trades, the safe way: look for patterns over many trades, and test any change walk-forward.

1. loss_report  - every committee trade with its context (who dissented, session, weekday, volatility, how many agreed),
                  then wins vs losses grouped by each of those. A group is only flagged when it is bad (or good) in BOTH
                  halves of the history with enough trades in each; everything else is "noise".
2. walk_forward - agent weights re-learned every month from the PAST only (each agent's own hit rate in the previous
                  N months), then the weighted vote is traded on the NEXT month, which the weights never saw.
                  Compared month by month with the plain N-of-10 committee on exactly the same months.

Neither changes the running bot. A result here is a candidate for a new fixed version that the owner approves.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from . import indicators as ind
from .backtest import Config, run_backtest
from .committee import Committee

SESSIONS = ((0, 7, "Asia 00-07"), (7, 13, "London 07-13"), (13, 21, "New York 13-21"), (21, 24, "Late 21-24"))
MIN_PER_HALF = 20


def session_of(hour: int) -> str:
    for lo, hi, name in SESSIONS:
        if lo <= hour < hi:
            return name
    return "?"


def _r(trades: pd.DataFrame, contract_oz: float) -> pd.Series:
    risk = (trades["entry"] - trades["sl"]).abs() * trades["lots"] * contract_oz
    return trades["pnl"] / risk.where(risk > 0)


def trade_context(df: pd.DataFrame, trades: pd.DataFrame, table: pd.DataFrame, contract_oz: float = 100.0) -> pd.DataFrame:
    """One row per trade: result in R plus what the market and the agents looked like at the signal candle."""
    if trades.empty:
        return pd.DataFrame()
    sig = (trades["entry_i"] - 1).to_numpy()
    d = trades["dir"].to_numpy()
    votes = table.to_numpy()[sig]
    atr = ind.atr(df)
    vol = (atr / atr.rolling(500, min_periods=100).median()).to_numpy()[sig]   # >1 = more volatile than usual
    t = df.index[sig]
    out = pd.DataFrame({
        "time": t, "dir": np.where(d == 1, "BUY", "SELL"), "R": _r(trades, contract_oz).to_numpy(),
        "agreeing": (votes == d[:, None]).sum(axis=1),
        "session": [session_of(x.hour) for x in t], "weekday": t.day_name().str[:3],
        "volatility": pd.cut(vol, [0, 0.8, 1.2, np.inf], labels=["low", "normal", "high"]).astype(str),
    })
    out["win"] = out.R > 0
    out["dissenters"] = [[n for n, v in zip(table.columns, row) if v != dd] for row, dd in zip(votes, d)]
    return out


def _stats(g: pd.DataFrame) -> dict:
    if not len(g):
        return {"n": 0, "win%": None, "avg_R": None}
    return {"n": len(g), "win%": round(100 * g.win.mean(), 1), "avg_R": round(g.R.mean(), 3)}


def loss_report(ctx: pd.DataFrame, split=None) -> pd.DataFrame:
    """Groups trades by each context feature. 'pattern' needs the same sign vs the overall average in both halves."""
    if ctx.empty:
        return pd.DataFrame()
    late = ctx.time >= pd.Timestamp(split) if split is not None else pd.Series(False, index=ctx.index)
    base = {h: ctx[m].R.mean() for h, m in (("early", ~late), ("late", late))}
    groups = []
    for feat in ("session", "weekday", "volatility", "dir", "agreeing"):
        for val, idx in ctx.groupby(feat).groups.items():
            groups.append((feat, str(val), ctx.index.isin(idx)))
    for name in sorted({n for ds in ctx.dissenters for n in ds}):
        groups.append(("dissenting agent", name, ctx.dissenters.apply(lambda ds: name in ds).to_numpy()))
    rows = []
    for feat, val, mask in groups:
        g = ctx[mask]
        r = {"feature": feat, "value": val, **_stats(g)}
        e, l = _stats(g[~late[mask]]), _stats(g[late[mask]])
        r.update({"n_early": e["n"], "avgR_early": e["avg_R"], "n_late": l["n"], "avgR_late": l["avg_R"]})
        verdict = "noise"
        if split is not None and e["n"] >= MIN_PER_HALF and l["n"] >= MIN_PER_HALF:
            de, dl = e["avg_R"] - base["early"], l["avg_R"] - base["late"]
            if de < -0.1 and dl < -0.1:
                verdict = "WORSE both halves"
            elif de > 0.1 and dl > 0.1:
                verdict = "better both halves"
        r["pattern"] = verdict
        rows.append(r)
    out = pd.DataFrame(rows)
    out.attrs["overall"] = _stats(ctx)
    out.attrs["losses"] = int((~ctx.win).sum())
    return out


def live_context(logdir: str) -> pd.DataFrame:
    """Closed paper trades from the bot's events.jsonl joined to the votes on their signal candle."""
    ev = []
    with open(os.path.join(logdir, "events.jsonl"), encoding="utf-8") as f:
        for line in f:
            try:
                ev.append(json.loads(line))
            except ValueError:
                pass
    votes = {e["bar"]: e["votes"] for e in ev if e.get("type") == "candle" and e.get("votes")}
    rows = []
    for t in (e for e in ev if e.get("type") == "paper_close"):
        v = votes.get(t.get("opened_bar"))
        if not v:
            continue
        d = 1 if t["dir"] == "BUY" else -1
        tm = pd.Timestamp(t["opened_bar"])
        risk = abs(t["entry"] - t["sl"])
        rows.append({"time": tm, "dir": t["dir"], "R": d * (t["exit"] - t["entry"]) / risk if risk else 0.0,
                     "agreeing": sum(1 for x in v.values() if x == d), "session": session_of(tm.hour),
                     "weekday": tm.day_name()[:3], "volatility": "n/a",
                     "dissenters": [n for n, x in v.items() if x != d]})
    out = pd.DataFrame(rows)
    if len(out):
        out["win"] = out.R > 0
    return out


# ------------------------------------------------------------------ walk-forward agent weights

def agent_edges(table: pd.DataFrame, df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Per bar and agent: vote x forward move over `horizon` bars in ATR units (positive = the vote was right).
    The value for bar i is only KNOWN at bar i + horizon; walk_forward respects that."""
    atr = ind.atr(df)
    fwd = (df["close"].shift(-horizon) - df["close"]) / atr
    return table.mul(fwd, axis=0).where(table != 0)


def learn_weights(edges: pd.DataFrame, known_until, train_start) -> pd.Series:
    """Weight = the agent's average edge in the window, floored at 0 (an agent with no edge gets no say)."""
    w = edges[(edges.index >= train_start) & (edges.index < known_until)].mean().clip(lower=0).fillna(0)
    if w.sum() <= 0:
        return pd.Series(1.0, index=edges.columns)
    return w * len(w) / w.sum()


def weighted_signals(table: pd.DataFrame, weights: pd.DataFrame, frac: float) -> pd.Series:
    """weights: one row per bar (the weights valid at that bar). Enter when the weighted agreement >= frac."""
    tot = weights.sum(axis=1)
    buy = weights.where(table == 1, 0).sum(axis=1) / tot      # same rule as N-of-10: share of (weighted) votes for BUY
    sell = weights.where(table == -1, 0).sum(axis=1) / tot
    return pd.Series(np.where(buy >= frac - 1e-9, 1, np.where(sell >= frac - 1e-9, -1, 0)), index=table.index)


def walk_forward(df: pd.DataFrame, agents, threshold: float, cfg: Config, start, train_months: int = 12,
                 horizon: int = 10, dirn: pd.Series | None = None):
    """Returns (monthly table, summary dict, weight history). Weights for month M use only data whose forward
    move had finished before M started."""
    from .htf import filter_signals
    com = Committee(agents, threshold=threshold)
    table = com.vote_table(df)
    edges = agent_edges(table, df, horizon)
    bar = df.index[1] - df.index[0]
    months = pd.date_range(pd.Timestamp(start).to_period("M").to_timestamp(), df.index[-1], freq="MS")
    w_rows = pd.DataFrame(np.nan, index=df.index, columns=table.columns)
    hist = {}
    for m in months:
        known_until = m - horizon * bar                           # forward move of these bars is complete at m
        w = learn_weights(edges, known_until, m - pd.DateOffset(months=train_months))
        hist[m.strftime("%Y-%m")] = w.round(2)
        sel = (df.index >= m) & (df.index < m + pd.DateOffset(months=1))
        w_rows.loc[sel] = w.to_numpy()
    w_rows = w_rows.fillna(1.0)
    plain = com.signals(df, table)
    learned = weighted_signals(table, w_rows, threshold)
    if dirn is not None:
        plain, learned = filter_signals(plain, dirn), filter_signals(learned, dirn)
    res = {}
    for name, sig in (("plain", plain), ("learned", learned)):
        tr, _, st = run_backtest(df, com, cfg, start=months[0], signals=sig)
        if len(tr):
            tr = tr.assign(R=_r(tr, cfg.contract_oz).to_numpy(), month=df.index[tr["entry_i"].to_numpy()].strftime("%Y-%m"))
        res[name] = (tr, st)
    rows = []
    for m in hist:
        r = {"month": m}
        for name in ("plain", "learned"):
            tr = res[name][0]
            g = tr[tr.month == m] if len(tr) else tr
            r[f"{name}_n"], r[f"{name}_R"] = len(g), round(g.R.sum(), 2) if len(g) else 0.0
        rows.append(r)
    monthly = pd.DataFrame(rows)

    def summ(tr):
        if not len(tr):
            return {"trades": 0}
        w, lo = tr[tr.R > 0].R.sum(), -tr[tr.R <= 0].R.sum()
        return {"trades": len(tr), "win%": round(float(100 * (tr.R > 0).mean()), 1), "avg_R": round(float(tr.R.mean()), 3),
                "total_R": round(float(tr.R.sum()), 1), "pf_R": round(float(w / lo), 2) if lo else float("inf")}

    half = len(monthly) // 2
    first = set(monthly.month[:half])
    summary = {}
    for name in ("plain", "learned"):
        tr = res[name][0]
        summary[name] = {"all": summ(tr), "first_half": summ(tr[tr.month.isin(first)] if len(tr) else tr),
                         "second_half": summ(tr[~tr.month.isin(first)] if len(tr) else tr)}
    summary["months_learned_better"] = int((monthly.learned_R > monthly.plain_R).sum())
    summary["months_plain_better"] = int((monthly.learned_R < monthly.plain_R).sum())
    return monthly, summary, pd.DataFrame(hist).T
