"""Did the AI reviewer add anything? Compares paper trades the AI would have allowed vs vetoed (from logs/events.jsonl).

Needs the bot to have run with --ai-review shadow (or veto) in DRY-RUN so paper trades exist. In shadow mode every
trade is taken, so the vetoed group shows what skipping them WOULD have done. In veto mode vetoed trades never
happen, so only ALLOW rows will have outcomes.
"""
from __future__ import annotations

import json
import os

import pandas as pd


def _read(logdir: str) -> list[dict]:
    path = os.path.join(logdir, "events.jsonl")
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def joined(logdir: str) -> pd.DataFrame:
    """One row per closed paper trade that had an AI opinion: verdict, reason, pnl, R (pnl / money risked)."""
    ev = _read(logdir)
    opinions = {e["bar"]: e for e in ev if e.get("type") == "ai_review" and e.get("bar")}
    rows = []
    for e in ev:
        if e.get("type") != "paper_close":
            continue
        op = opinions.get(str(e.get("opened_bar")))
        if op is None:
            continue
        risk = float(e.get("risk_usd") or 0)
        pnl = float(e.get("pnl") or 0)
        rows.append({"bar": e["opened_bar"], "dir": e.get("dir"), "verdict": op["verdict"], "reason": op.get("reason", ""),
                     "pnl": pnl, "R": (pnl / risk) if risk > 0 else float("nan"), "reason_closed": e.get("reason")})
    return pd.DataFrame(rows)


def summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    g = df.groupby("verdict")
    return pd.DataFrame({
        "trades": g.size(), "win_rate": g["pnl"].apply(lambda s: (s > 0).mean()),
        "avg_R": g["R"].mean(), "total_pnl": g["pnl"].sum()}).round(3)


def report(logdir: str) -> str:
    ev = _read(logdir)
    ops = [e for e in ev if e.get("type") == "ai_review"]
    if not ops:
        return "No AI review records in " + os.path.join(logdir, "events.jsonl") + ". Run the bot with --ai-review shadow first."
    counts = pd.Series([e.get("verdict", "?") for e in ops]).value_counts().to_dict()
    lat = sorted(int(e.get("latency_ms", 0)) for e in ops)
    models = pd.Series([e.get("model", "?") for e in ops]).value_counts().to_dict()
    lines = [f"AI opinions: {len(ops)}  {counts}", f"latency ms: median {lat[len(lat) // 2]}, max {lat[-1]}", f"served by: {models}"]
    j = joined(logdir)
    if j.empty:
        lines.append("No closed paper trades with an AI opinion yet (trades need to open and then hit SL/TP).")
    else:
        lines += ["", summary(j).to_string(), "",
                  f"closed trades with an opinion: {len(j)}"]
        if "VETO" in set(j["verdict"]):
            v, a = j[j.verdict == "VETO"]["R"].mean(), j[j.verdict == "ALLOW"]["R"].mean()
            lines.append(f"avg R vetoed {v:+.2f} vs allowed {a:+.2f}  (the AI only helps if vetoed is clearly lower)")
        if len(j) < 30:
            lines.append("WARNING: fewer than 30 trades; any difference between the groups is probably noise.")
    return "\n".join(lines)
