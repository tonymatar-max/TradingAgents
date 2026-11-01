"""Compare the EA's logged votes (events.jsonl) with this Python package's votes on the same candles."""
from __future__ import annotations

import json

import pandas as pd

from .agents import Agent, default_agents


def read_ea_candles(path: str) -> pd.DataFrame:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "candle" and e.get("votes"):
                rows.append({"bar": pd.Timestamp(e["bar"]), "action": e["action"], **e["votes"]})
    return pd.DataFrame(rows).drop_duplicates("bar", keep="last").set_index("bar") if rows else pd.DataFrame()


def compare(df: pd.DataFrame, ea: pd.DataFrame, agents: list[Agent] | None = None) -> dict:
    agents = agents or default_agents()
    py = pd.DataFrame({a.name: a.votes(df) for a in agents})
    common = ea.index.intersection(py.index)
    out = {"ea_candles": len(ea), "compared": len(common), "agents": {}, "examples": []}
    if not len(common):
        return out
    for a in agents:
        if a.name not in ea.columns:
            continue
        same = (ea.loc[common, a.name].astype(int) == py.loc[common, a.name].astype(int))
        out["agents"][a.name] = {"agree_%": round(100 * same.mean(), 2), "mismatches": int((~same).sum())}
        for t in same[~same].index[:3]:
            out["examples"].append((str(t), a.name, int(ea.loc[t, a.name]), int(py.loc[t, a.name])))
    need = ea.loc[common, [a.name for a in agents if a.name in ea.columns]]
    buys, sells = (need == 1).sum(axis=1), (need == -1).sum(axis=1)
    pyn = py.loc[common]
    pb, ps = (pyn == 1).sum(axis=1), (pyn == -1).sum(axis=1)
    out["same_vote_counts_%"] = round(100 * ((buys == pb) & (sells == ps)).mean(), 2)
    return out
