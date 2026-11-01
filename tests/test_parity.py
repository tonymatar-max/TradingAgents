import json
import os
import tempfile
import unittest

import numpy as np

from tests.mql_mirror import DEFAULTS, compute_votes
from xauusd_agents.agents import default_agents
from xauusd_agents.data import synthetic_xauusd
from xauusd_agents.parity import compare, read_ea_candles

NAMES = [a.name for a in default_agents()]


def fake_ea_log(df, votes, start, end, flip_agent=None):
    d = tempfile.mkdtemp(); p = os.path.join(d, "events.jsonl")
    with open(p, "w") as f:
        for i in range(start, end):
            v = {NAMES[k]: int(votes[i][k]) for k in range(10)}
            if flip_agent is not None and i % 7 == 0:
                v[flip_agent] = -v[flip_agent] or 1
            f.write(json.dumps({"type": "candle", "bar": str(df.index[i]), "action": "HOLD", "votes": v}) + "\n")
    return p


class Parity(unittest.TestCase):
    df = synthetic_xauusd(2500)

    def test_perfect_when_ea_equals_python(self):
        votes, _ = compute_votes(list(zip(self.df.high, self.df.low, self.df.close)), DEFAULTS)
        res = compare(self.df, read_ea_candles(fake_ea_log(self.df, votes, 800, 1200)))
        self.assertEqual(res["compared"], 400)
        self.assertTrue(all(v["mismatches"] == 0 for v in res["agents"].values()))
        self.assertEqual(res["same_vote_counts_%"], 100.0)

    def test_detects_a_wrong_agent(self):
        votes, _ = compute_votes(list(zip(self.df.high, self.df.low, self.df.close)), DEFAULTS)
        res = compare(self.df, read_ea_candles(fake_ea_log(self.df, votes, 800, 1200, flip_agent="8_Supertrend")))
        self.assertGreater(res["agents"]["8_Supertrend"]["mismatches"], 30)
        self.assertEqual(res["agents"]["1_EMA_Trend"]["mismatches"], 0)
        self.assertTrue(res["examples"])


if __name__ == "__main__":
    unittest.main()
