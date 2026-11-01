import unittest

import pandas as pd

from xauusd_agents import Committee
from xauusd_agents.data import synthetic_xauusd
from xauusd_agents.htf import (confluence_table, filter_signals, htf_direction, resample_ohlc,
                               votes_to_direction)
from tests.test_mt5 import Always, FakeMT5, bot


class Htf(unittest.TestCase):
    ltf = synthetic_xauusd(4000)
    htf = resample_ohlc(ltf, "4h")

    def test_votes_to_direction(self):
        # rows: 3 buys | 1 buy 2 sells | 0 buys 3 sells | 1 buy only | 2 buys 2 sells (tie)
        t = pd.DataFrame({"a": [1, 1, -1, 1, 1], "b": [1, -1, -1, 0, 1], "c": [1, -1, -1, 0, -1], "d": [0, 0, -1, 0, -1]})
        self.assertEqual(votes_to_direction(t, 3).tolist(), [1, 0, -1, 0, 0])
        self.assertEqual(votes_to_direction(t, 2).tolist(), [1, -1, -1, 0, 0])  # tie never confirms

    def test_no_lookahead(self):
        """Direction for early LTF bars must not change when the future HTF data is removed."""
        full = htf_direction(self.ltf, self.htf, 6)
        cut_l = self.ltf.iloc[:2500]
        cut_h = self.htf[self.htf.index <= cut_l.index[-1]]
        part = htf_direction(cut_l, cut_h, 6)
        pd.testing.assert_series_equal(full.iloc[:2500], part, check_names=False)

    def test_htf_candle_unusable_until_closed(self):
        # HTF bar opening at T is only usable for LTF bars closing at >= T+4h, i.e. LTF open >= T+3h
        d = htf_direction(self.ltf, self.htf, 5)
        first_htf = self.htf.index[0]
        early = d[(d.index < first_htf + pd.Timedelta(hours=3))]
        self.assertTrue((early == 0).all())

    def test_filter_signals(self):
        sig = pd.Series([1, 1, -1, -1, 0]); dirn = pd.Series([1, -1, -1, 0, 1])
        self.assertEqual(filter_signals(sig, dirn).tolist(), [1, 0, -1, 0, 0])

    def test_confluence_table_reduces_trades(self):
        r = confluence_table(self.ltf, self.htf, "2024-02-01", "2024-04-15", mins=(0, 7))
        self.assertEqual(len(r), 2)
        self.assertLessEqual(r.trades.iloc[1], r.trades.iloc[0])


class BotHtf(unittest.TestCase):
    def _bot(self, htf_vote):
        m = FakeMT5(); m.TIMEFRAME_H4 = 4
        b = bot(m, dry=False)                      # H1 committee: all BUY
        b.htf_min = 6
        from xauusd_agents.committee import Committee as C
        b.htf_agents = [Always(htf_vote, i) for i in range(10)]
        b.connect()
        return m, b

    def test_blocks_when_htf_disagrees(self):
        m, b = self._bot(-1)
        self.assertIn("does not confirm", b.step()); self.assertEqual(m.sent, [])

    def test_trades_when_htf_agrees(self):
        m, b = self._bot(1)
        self.assertTrue(b.step().startswith("PLACED")); self.assertEqual(len(m.sent), 1)


if __name__ == "__main__":
    unittest.main()


class SweepHtf(unittest.TestCase):
    def test_sweep_runs_with_htf(self):
        from xauusd_agents.optimize import sweep
        ltf = synthetic_xauusd(4000)
        r = sweep(ltf, "2024-02-01", "2024-04-01", thresholds=(0.9,), sls=(1.5,), tps=(2.0,),
                  htf=resample_ohlc(ltf, "4h"), htf_min=6)
        self.assertFalse(r.empty)


class CliPaths(unittest.TestCase):
    """Run the real command line: these catch arguments that never reach the function (a past bug)."""
    @classmethod
    def setUpClass(cls):
        import os, subprocess, sys, tempfile
        cls.sub, cls.sys = subprocess, sys
        d = tempfile.mkdtemp()
        cls.csv = os.path.join(d, "s.csv")
        synthetic_xauusd(4000).rename_axis("time").to_csv(cls.csv)

    def run_cli(self, *args):
        r = self.sub.run([self.sys.executable, "-m", "xauusd_agents", *args], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        return r.stdout

    def test_optimize_htf_flag_changes_results(self):
        base = ("optimize", "--csv", self.csv, "--from", "2024-02-01", "--split", "2024-04-15",
                "--thresholds", "0.8", "--sls", "2.0", "--tps", "2.0")
        self.assertNotEqual(self.run_cli(*base), self.run_cli(*base, "--htf-min", "6"))

    def test_optimize_custom_grid_includes_unanimous(self):
        out = self.run_cli("optimize", "--csv", self.csv, "--from", "2024-02-01", "--split", "2024-04-15",
                           "--thresholds", "0.8,1.0", "--sls", "2.0", "--tps", "2.0")
        self.assertIn("1.0", out)

    def test_tfscan_arguments_reach_scan(self):
        import inspect
        from xauusd_agents.tfscan import scan_mt5
        self.assertNotIn("htf", inspect.signature(scan_mt5).parameters)  # tfscan takes no htf argument
        import xauusd_agents.__main__ as m
        self.assertNotIn("htf=load_htf", inspect.getsource(m).split('cmd == "tfscan"')[1].split("return")[0])
