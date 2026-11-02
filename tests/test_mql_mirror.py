import unittest

import numpy as np

from tests.mql_mirror import DEFAULTS, compute_votes, scaled
from xauusd_agents import indicators as ind
from xauusd_agents.agents import default_agents
from xauusd_agents.data import synthetic_xauusd


class Mirror(unittest.TestCase):
    def test_ea_algorithms_match_the_pandas_agents(self):
        df = synthetic_xauusd(2500)
        rows = list(zip(df.high, df.low, df.close))
        votes, atr = compute_votes(rows, DEFAULTS)
        votes = np.array(votes)
        np.testing.assert_allclose(atr[300:], ind.atr(df).to_numpy()[300:], rtol=1e-9)
        for k, a in enumerate(default_agents()):
            py = a.votes(df).to_numpy()
            mism = int((votes[300:, k] != py[300:]).sum())
            self.assertEqual(mism, 0, f"{a.name}: {mism} of {len(py) - 300} votes differ")

    def test_scaled_periods_match_python_agent_scale(self):
        df = synthetic_xauusd(4000)
        rows = list(zip(df.high, df.low, df.close))
        for scale in (2.0, 1.5):
            votes, atr = compute_votes(rows, scaled(scale))
            votes = np.array(votes)
            np.testing.assert_allclose(atr[400:], ind.atr(df).to_numpy()[400:], rtol=1e-9)   # stop ATR is never scaled
            for k, a in enumerate(default_agents(scale)):
                mism = int((votes[1500:, k] != a.votes(df).to_numpy()[1500:]).sum())
                self.assertEqual(mism, 0, f"scale {scale} {a.name}: {mism} votes differ")


if __name__ == "__main__":
    unittest.main()
