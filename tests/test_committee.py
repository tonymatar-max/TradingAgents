import unittest

import pandas as pd

from xauusd_agents import Committee, Config, run_backtest
from xauusd_agents.agents import Agent, default_agents
from xauusd_agents.backtest import position_size
from xauusd_agents.data import synthetic_xauusd


class Fixed(Agent):
    def __init__(self, v, n):
        self.v, self.name = v, f"fixed{n}"

    def votes(self, df):
        return pd.Series(self.v, index=df.index)


def committee(buys, sells):
    ags = [Fixed(1, i) for i in range(buys)] + [Fixed(-1, 100 + i) for i in range(sells)]
    ags += [Fixed(0, 200 + i) for i in range(10 - buys - sells)]
    return Committee(ags, 0.8)


class T(unittest.TestCase):
    df = synthetic_xauusd(1500)

    def test_ten_agents(self):
        self.assertEqual(len(default_agents()), 10)
        self.assertEqual(len({a.name for a in default_agents()}), 10)

    def test_threshold_is_eight_of_ten(self):
        self.assertEqual(Committee().required, 8)
        self.assertEqual(committee(8, 0).decide(self.df).action, "BUY")
        self.assertEqual(committee(7, 0).decide(self.df).action, "HOLD")
        self.assertEqual(committee(0, 8).decide(self.df).action, "SELL")
        self.assertEqual(committee(5, 5).decide(self.df).action, "HOLD")

    def test_agents_causal(self):
        full = {a.name: a.votes(self.df) for a in default_agents()}
        cut = self.df.iloc[:1000]
        for a in default_agents():
            pd.testing.assert_series_equal(a.votes(cut), full[a.name].iloc[:1000], check_names=False)

    def test_backtest_runs(self):
        trades, curve, stats = run_backtest(self.df)
        self.assertEqual(len(curve), len(self.df))
        self.assertIn("trades", stats)

    def test_position_size_risk(self):
        cfg = Config()
        lots = position_size(10_000, 10.0, cfg)  # $100 risk / (10$ * 100oz)
        self.assertAlmostEqual(lots, 0.1)


if __name__ == "__main__":
    unittest.main()


class TestStart(unittest.TestCase):
    def test_no_trades_before_start(self):
        df = synthetic_xauusd(3000)
        start = df.index[1800]
        trades, _, _ = run_backtest(df, start=start)
        self.assertTrue(trades.empty or (df.index[trades.entry_i] >= start).all())


class TestFixedLots(unittest.TestCase):
    def test_fixed_lot_and_blowup_protection(self):
        df = synthetic_xauusd(4000)
        t, curve, st = run_backtest(df, cfg=Config(balance=5000, fixed_lots=1.0), start=df.index[300])
        self.assertTrue((t.lots == 1.0).all())
        self.assertGreaterEqual(curve.min(), 0)            # never negative
        self.assertIn("account_blown", st)
        t2, _, st2 = run_backtest(df, cfg=Config(balance=5000), start=df.index[300])
        self.assertFalse(st2["account_blown"])             # 1% risk sizing survives the same data


class TestMargin(unittest.TestCase):
    df = synthetic_xauusd(4000)

    def test_cannot_afford_margin_means_no_trades(self):
        # 1 lot of ~$2000 gold = $200k notional; at 1:20 needs ~$10k margin > $5k balance
        t, _, st = run_backtest(self.df, cfg=Config(balance=5000, fixed_lots=1.0, leverage=20), start=self.df.index[300])
        self.assertTrue(t.empty)

    def test_stopout_triggers_before_far_stop_loss(self):
        cfg = Config(balance=2500, fixed_lots=1.0, leverage=100, sl_atr=60, tp_atr=60)  # SL absurdly far
        t, curve, st = run_backtest(self.df, cfg=cfg, start=self.df.index[300])
        self.assertGreater(st["stop_outs"], 0)
        self.assertGreaterEqual(curve.min(), 0)

    def test_no_leverage_means_no_stopouts(self):
        _, _, st = run_backtest(self.df, cfg=Config(balance=5000, fixed_lots=1.0), start=self.df.index[300])
        self.assertEqual(st["stop_outs"], 0)
