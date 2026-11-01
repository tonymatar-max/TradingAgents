import json
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

from xauusd_agents.agent_stats import attribution, backtest_report, live_report, _signals
from xauusd_agents.agents import Agent, default_agents
from xauusd_agents.backtest import Config
from xauusd_agents.data import synthetic_xauusd


class T(unittest.TestCase):
    def test_attribution_counts(self):
        # 4 trades; agent A agreed on the 2 winners only, agent B on the 2 losers only
        trades = pd.DataFrame({"entry_i": [1, 2, 3, 4], "dir": [1, 1, 1, 1], "pnl": [10, 10, -5, -5]})
        table = pd.DataFrame({"A": [1, 1, 0, 0, 0], "B": [0, 0, 1, 1, 1]})   # signal bar = entry_i - 1 -> A agrees on trades 1-2, B on 3-4
        r = attribution(trades, table).set_index("agent")
        self.assertEqual(r.loc["A", "agree_n"], 2); self.assertEqual(r.loc["A", "agree_win%"], 100.0)
        self.assertEqual(r.loc["B", "agree_win%"], 0.0); self.assertEqual(r.loc["B", "other_win%"], 100.0)

    def test_by_votes(self):
        from xauusd_agents.agent_stats import by_votes
        trades = pd.DataFrame({"entry_i": [1, 2, 3], "dir": [1, 1, 1], "pnl": [10, -5, 20]})
        table = pd.DataFrame({"A": [1, 1, 1, 0], "B": [1, 1, 0, 0], "C": [0, 1, 1, 0]})  # agreeing at bars 0,1,2: 2, 3, 2
        r = by_votes(trades, table).set_index("agents_agreeing")
        self.assertEqual(r.loc[2, "trades"], 2); self.assertEqual(r.loc[3, "trades"], 1); self.assertEqual(r.loc[3, "win%"], 0.0)

    def test_signals_required_votes(self):
        t = pd.DataFrame({"a": [1, 1, -1], "b": [1, 1, -1], "c": [1, 0, -1]})
        self.assertEqual(_signals(t, 3).tolist(), [1, 0, -1])
        self.assertEqual(_signals(t, 2).tolist(), [1, 1, -1])

    def test_report_flags_a_harmful_agent(self):
        """9 normal agents + one that always votes AGAINST the move of the last 5 bars (a contrarian that fails)."""
        class Anti(Agent):
            name = "99_Anti"
            def votes(self, df):
                r = df["close"].pct_change(5)
                return pd.Series(np.where(r > 0, -1, np.where(r < 0, 1, 0)), index=df.index)
        df = synthetic_xauusd(5000)
        agents = default_agents()[:9] + [Anti()]
        res = backtest_report(df, agents, 8, "2024-02-01", "2024-05-01", Config(sl_atr=2.0, tp_atr=2.0))
        self.assertEqual(len(res), 10); self.assertIn("verdict", res.columns)
        self.assertIn(res.iloc[0]["verdict"], ("HURTS", "unclear", "helps"))
        self.assertTrue(res.attrs["committee_trades"] > 0)

    def test_live_report(self):
        d = tempfile.mkdtemp()
        ev = [{"type": "candle", "bar": "b1", "votes": {"A": 1, "B": -1}},
              {"type": "paper_close", "dir": "BUY", "pnl": 50.0, "opened_bar": "b1"},
              {"type": "candle", "bar": "b2", "votes": {"A": 1, "B": 1}},
              {"type": "paper_close", "dir": "BUY", "pnl": -20.0, "opened_bar": "b2"}]
        open(os.path.join(d, "events.jsonl"), "w").write("\n".join(json.dumps(e) for e in ev))
        r = live_report(d).set_index("agent")
        self.assertEqual(r.loc["A", "agree_n"], 2); self.assertEqual(r.loc["B", "agree_n"], 1)
        self.assertEqual(r.loc["B", "agree_win%"], 0.0); self.assertEqual(r.loc["B", "other_win%"], 100.0)

    def test_cli_smoke(self):
        d = tempfile.mkdtemp(); csv = os.path.join(d, "s.csv")
        synthetic_xauusd(4000).rename_axis("time").to_csv(csv)
        r = subprocess.run([sys.executable, "-m", "xauusd_agents", "agentstats", "--csv", csv, "--from", "2024-02-01",
                            "--split", "2024-04-15", "--sl-atr", "2.0", "--tp-atr", "2.0", "--htf-min", "6"],
                           capture_output=True, text=True, timeout=600)
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        self.assertIn("EMA_Trend", r.stdout); self.assertIn("verdict", r.stdout)


if __name__ == "__main__":
    unittest.main()
