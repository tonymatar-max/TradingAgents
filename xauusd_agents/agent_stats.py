"""Which agent is failing? Three views, because any single one can mislead.

1. solo      - the agent trading ALONE with the same stop/target (does it have an edge by itself?)
2. agreement - committee trades split by whether this agent voted WITH the trade (win rate / avg P&L)
3. leave-one-out - committee results with this agent removed (same number of votes required);
                   dPF > 0 means the committee did BETTER without it (the agent hurts)
Verdict needs the same sign in BOTH halves of the history, otherwise it is "unclear" (noise).
"""
from __future__ import annotations

import json
import os

import pandas as pd

from .backtest import Config, run_backtest
from .committee import Committee


def _signals(table: pd.DataFrame, required: int) -> pd.Series:
    buys, sells = (table == 1).sum(axis=1), (table == -1).sum(axis=1)
    out = pd.Series(0, index=table.index)
    out[buys >= required] = 1
    out[sells >= required] = -1
    return out


def attribution(trades: pd.DataFrame, table: pd.DataFrame) -> pd.DataFrame:
    """For each agent: committee trades where it voted with the trade vs not (neutral or opposed)."""
    rows = []
    sig_bar = (trades["entry_i"] - 1).to_numpy()
    for name in table.columns:
        votes = table[name].to_numpy()[sig_bar]
        agree = votes == trades["dir"].to_numpy()
        a, o = trades[agree], trades[~agree]
        rows.append({"agent": name,
                     "agree_n": len(a), "agree_win%": round(100 * (a.pnl > 0).mean(), 1) if len(a) else None,
                     "agree_avg$": round(a.pnl.mean(), 2) if len(a) else None,
                     "other_n": len(o), "other_win%": round(100 * (o.pnl > 0).mean(), 1) if len(o) else None,
                     "other_avg$": round(o.pnl.mean(), 2) if len(o) else None})
    return pd.DataFrame(rows)


def by_votes(trades: pd.DataFrame, table: pd.DataFrame) -> pd.DataFrame:
    """Committee trades grouped by how many agents agreed at the signal candle (8, 9, 10...)."""
    if trades.empty:
        return pd.DataFrame()
    sig_bar = (trades["entry_i"] - 1).to_numpy()
    n = (table.to_numpy()[sig_bar] == trades["dir"].to_numpy()[:, None]).sum(axis=1)
    t = trades.assign(agreeing=n)
    rows = []
    for k, g in t.groupby("agreeing"):
        w, lo = g[g.pnl > 0].pnl.sum(), -g[g.pnl <= 0].pnl.sum()
        rows.append({"agents_agreeing": int(k), "trades": len(g), "win%": round(100 * (g.pnl > 0).mean(), 1),
                     "avg_pnl$": round(g.pnl.mean(), 2), "pf": round(w / lo, 2) if lo else float("inf")})
    return pd.DataFrame(rows)


def _pf(stats):
    return stats.get("profit_factor", float("nan")) if stats.get("trades", 0) else float("nan")


def backtest_report(df, agents, required, start, split, cfg: Config, dirn=None) -> pd.DataFrame:
    from .htf import filter_signals
    com = Committee(agents, threshold=required / len(agents))
    table = com.vote_table(df)
    split = pd.Timestamp(split)
    tr_df = df[df.index < split]

    def run(sig, which):
        if dirn is not None:
            sig = filter_signals(sig, dirn)
        if which == "all":
            return run_backtest(df, com, cfg, start=start, signals=sig)
        if which == "train":
            return run_backtest(tr_df, com, cfg, start=start, signals=sig.loc[tr_df.index])
        return run_backtest(df, com, cfg, start=split, signals=sig)

    full = _signals(table, required)
    base = {w: run(full, w) for w in ("all", "train", "test")}
    attr = attribution(base["all"][0], table).set_index("agent") if len(base["all"][0]) else pd.DataFrame()
    rows = []
    for name in table.columns:
        solo = {w: run(table[name], w)[2] for w in ("all", "train", "test")}
        loo_sig = _signals(table.drop(columns=name), required)
        loo = {w: run(loo_sig, w)[2] for w in ("all", "train", "test")}
        d_tr = _pf(loo["train"]) - _pf(base["train"][2])
        d_te = _pf(loo["test"]) - _pf(base["test"][2])
        if d_tr > 0.02 and d_te > 0.02:
            verdict = "HURTS"
        elif d_tr < -0.02 and d_te < -0.02:
            verdict = "helps"
        else:
            verdict = "unclear"
        r = {"agent": name, "solo_trades": solo["all"].get("trades", 0), "solo_pf": _pf(solo["all"]),
             "solo_pf_train": _pf(solo["train"]), "solo_pf_test": _pf(solo["test"]),
             "pf_without": _pf(loo["all"]), "dPF_train": round(d_tr, 3), "dPF_test": round(d_te, 3), "verdict": verdict}
        if name in attr.index:
            r.update({"agree_n": attr.loc[name, "agree_n"], "agree_win%": attr.loc[name, "agree_win%"],
                      "other_n": attr.loc[name, "other_n"], "other_win%": attr.loc[name, "other_win%"]})
        rows.append(r)
    out = pd.DataFrame(rows)
    out.attrs["committee_pf"] = {w: _pf(base[w][2]) for w in base}
    out.attrs["committee_trades"] = base["all"][2].get("trades", 0)
    out.attrs["by_votes"] = by_votes(base["all"][0], table)
    return out.sort_values("dPF_test", ascending=False)


def live_report(logdir: str) -> pd.DataFrame:
    """Same agreement view from the bot's own records: paper trades joined to the votes on their signal candle."""
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
        if v:
            rows.append({"dir": 1 if t["dir"] == "BUY" else -1, "pnl": t["pnl"], "votes": v})
    if not rows:
        return pd.DataFrame()
    t = pd.DataFrame(rows)
    out = []
    for name in t.iloc[0]["votes"]:
        agree = t.apply(lambda r: r["votes"].get(name) == r["dir"], axis=1)
        a, o = t[agree], t[~agree]
        out.append({"agent": name, "agree_n": len(a), "agree_win%": round(100 * (a.pnl > 0).mean(), 1) if len(a) else None,
                    "agree_avg$": round(a.pnl.mean(), 2) if len(a) else None, "other_n": len(o),
                    "other_win%": round(100 * (o.pnl > 0).mean(), 1) if len(o) else None,
                    "other_avg$": round(o.pnl.mean(), 2) if len(o) else None})
    res = pd.DataFrame(out)
    res.attrs["trades"] = len(t)
    return res
