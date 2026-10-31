"""Committee: opens a trade only when >= `threshold` (default 80%) of agents agree."""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from .agents import Agent, default_agents


@dataclass
class Decision:
    action: str          # "BUY" | "SELL" | "HOLD"
    buy: int
    sell: int
    neutral: int
    required: int
    votes: dict


class Committee:
    def __init__(self, agents: list[Agent] | None = None, threshold: float = 0.8):
        self.agents = agents or default_agents()
        self.threshold = threshold
        # round() guards float error, e.g. 0.8*10 = 8.000000000000002
        self.required = math.ceil(round(threshold * len(self.agents), 9))

    def vote_table(self, df: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({a.name: a.votes(df) for a in self.agents})

    def signals(self, df: pd.DataFrame, table: pd.DataFrame | None = None) -> pd.Series:
        t = self.vote_table(df) if table is None else table
        buys, sells = (t == 1).sum(axis=1), (t == -1).sum(axis=1)
        out = pd.Series(0, index=t.index)
        out[buys >= self.required] = 1
        out[sells >= self.required] = -1
        return out

    def decide(self, df: pd.DataFrame) -> Decision:
        t = self.vote_table(df).iloc[-1]
        buy, sell = int((t == 1).sum()), int((t == -1).sum())
        action = "BUY" if buy >= self.required else "SELL" if sell >= self.required else "HOLD"
        return Decision(action, buy, sell, len(t) - buy - sell, self.required, t.to_dict())
