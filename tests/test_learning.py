import json
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

from xauusd_agents.agents import default_agents
from xauusd_agents.backtest import Config, run_backtest
from xauusd_agents.committee import Committee
from xauusd_agents.data import synthetic_xauusd
from xauusd_agents.learning import (agent_edges, learn_weights, live_context, loss_report, session_of, trade_context,
                                    walk_forward, weighted_signals)


class Context(unittest.TestCase):
    df = synthetic_xauusd(4000)

    def test_sessions(self):
        self.assertEqual([session_of(h) for h in (0, 6, 7, 12, 13, 20, 21, 23)],
                         ["Asia 00-07"] * 2 + ["London 07-13"] * 2 + ["New York 13-21"] * 2 + ["Late 21-24"] * 2)

    def test_trade_context_and_report(self):
        com = Committee(default_agents(), 0.8)
        table = com.vote_table(self.df)
        tr, _, _ = run_backtest(self.df, com, Config(sl_atr=2, tp_atr=2), table=table)
        ctx = trade_context(self.df, tr, table)
        self.assertEqual(len(ctx), len(tr))
        self.assertTrue((ctx.agreeing >= 8).all())
        self.assertTrue(all(len(d) == 10 - n for d, n in zip(ctx.dissenters, ctx.agreeing)))
        np.testing.assert_allclose(ctx.R.to_numpy()[ctx.win.to_numpy()].min() > 0, True)
        rep = loss_report(ctx, self.df.index[2000])
        self.assertEqual(rep.attrs["overall"]["n"], len(ctx))
        self.assertTrue(set(rep.feature) >= {"session", "weekday", "volatility", "dir", "agreeing"})
        self.assertTrue(set(rep.pattern) <= {"noise", "WORSE both halves", "better both halves"})

    def test_pattern_needs_both_halves(self):
        t = pd.date_range("2024-01-01", periods=200, freq="h")
        ctx = pd.DataFrame({"time": t, "dir": "BUY", "agreeing": 9, "weekday": "Mon", "volatility": "normal",
                            "session": ["Asia 00-07" if i % 2 else "London 07-13" for i in range(200)],
                            "dissenters": [[] for _ in range(200)]})
        # Asia loses in BOTH halves, London wins in both
        ctx["R"] = np.where(ctx.session == "Asia 00-07", -1.0, 1.0)
        ctx["win"] = ctx.R > 0
        rep = loss_report(ctx, t[100]).set_index("value")
        self.assertEqual(rep.loc["Asia 00-07", "pattern"], "WORSE both halves")
        self.assertEqual(rep.loc["London 07-13", "pattern"], "better both halves")
        # Asia bad only early -> noise
        ctx.loc[100:, "R"] = 1.0
        ctx["win"] = ctx.R > 0
        self.assertEqual(loss_report(ctx, t[100]).set_index("value").loc["Asia 00-07", "pattern"], "noise")

    def test_live_context(self):
        d = tempfile.mkdtemp()
        votes = {f"a{i}": -1 for i in range(9)} | {"a9": 0}
        with open(os.path.join(d, "events.jsonl"), "w") as f:
            f.write(json.dumps({"type": "candle", "bar": "2026-11-01 23:00:00", "votes": votes}) + "\n")
            f.write(json.dumps({"type": "paper_close", "dir": "SELL", "entry": 100, "sl": 110, "exit": 110,
                                "pnl": -50, "opened_bar": "2026-11-01 23:00:00"}) + "\n")
        ctx = live_context(d)
        self.assertEqual((ctx.R[0], ctx.agreeing[0], ctx.dissenters[0], ctx.session[0]), (-1.0, 9, ["a9"], "Late 21-24"))


class WalkForward(unittest.TestCase):
    def test_weights(self):
        idx = pd.date_range("2024-01-01", periods=6, freq="h")
        e = pd.DataFrame({"good": [1, 1, 1, 1, 9, 9], "bad": [-1, -1, -1, -1, 9, 9]}, index=idx, dtype=float)
        w = learn_weights(e, idx[4], idx[0])          # rows 4-5 not known yet -> ignored
        self.assertEqual((w["good"], w["bad"]), (2.0, 0.0))
        self.assertTrue((learn_weights(-e.abs(), idx[4], idx[0]) == 1.0).all())   # nobody has an edge -> equal

    def test_weighted_equals_plain_with_equal_weights(self):
        df = synthetic_xauusd(3000)
        com = Committee(default_agents(), 0.9)
        table = com.vote_table(df)
        ws = weighted_signals(table, pd.DataFrame(1.0, index=table.index, columns=table.columns), 0.9)
        pd.testing.assert_series_equal(ws, com.signals(df, table), check_names=False, check_dtype=False)

    def test_no_lookahead(self):
        """Weights for a month must not change when later data is removed."""
        df = synthetic_xauusd(6000)
        edges = agent_edges(Committee(default_agents()).vote_table(df), df, 10)
        m = df.index[4000]
        cut = df.iloc[:4001]
        edges_cut = agent_edges(Committee(default_agents()).vote_table(cut), cut, 10)
        known = m - 10 * (df.index[1] - df.index[0])
        pd.testing.assert_series_equal(learn_weights(edges, known, df.index[1000]),
                                       learn_weights(edges_cut, known, df.index[1000]))

    def test_walk_forward_runs(self):
        df = synthetic_xauusd(6000)
        monthly, summ, w = walk_forward(df, default_agents(), 0.8, Config(sl_atr=2, tp_atr=2), df.index[3000], 3)
        self.assertGreater(len(monthly), 2)
        self.assertEqual(set(summ["plain"]), {"all", "first_half", "second_half"})
        self.assertEqual(len(w), len(monthly))


class Cli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d = tempfile.mkdtemp()
        cls.csv = os.path.join(d, "s.csv")
        synthetic_xauusd(6000).rename_axis("time").to_csv(cls.csv)

    def run_cli(self, *args):
        r = subprocess.run([sys.executable, "-m", "xauusd_agents", *args], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        return r.stdout

    def test_lossreport(self):
        out = self.run_cli("lossreport", "--csv", self.csv, "--from", "2024-02-01", "--split", "2024-05-01",
                           "--threshold", "0.8", "--sl-atr", "2", "--tp-atr", "2")
        self.assertIn("dissenting agent", out)

    def test_walkforward_flags_reach_function(self):
        base = ("walkforward", "--csv", self.csv, "--from", "2024-05-01", "--threshold", "0.8")
        a = self.run_cli(*base, "--train-months", "2")
        self.assertIn("previous 2 months", a)
        self.assertNotEqual(a, self.run_cli(*base, "--train-months", "2", "--horizon", "30"))


if __name__ == "__main__":
    unittest.main()
