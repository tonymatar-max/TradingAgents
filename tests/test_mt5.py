import unittest
from types import SimpleNamespace as NS

import numpy as np

from xauusd_agents import Committee
from xauusd_agents.agents import Agent
from xauusd_agents.data import synthetic_xauusd
from xauusd_agents.mt5_bot import Mt5Bot, lot_size

INFO = NS(trade_tick_size=0.01, trade_tick_value=1.0, volume_step=0.01, volume_min=0.01, volume_max=50,
          trade_stops_level=0, point=0.01, digits=2, filling_mode=2)


class Always(Agent):
    def __init__(self, v, n):
        self.v, self.name = v, f"a{n}"

    def votes(self, df):
        import pandas as pd
        return pd.Series(self.v, index=df.index)


class FakeMT5:
    TIMEFRAME_H1 = 1; ACCOUNT_TRADE_MODE_DEMO = 0; TRADE_ACTION_DEAL = 1; ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1; ORDER_TIME_GTC = 0; ORDER_FILLING_FOK = 0; ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2; TRADE_RETCODE_DONE = 10009

    def __init__(self, mode=0, positions=()):
        self.mode, self.pos, self.sent = mode, list(positions), []
        df = synthetic_xauusd(400)
        self.rates = [(t.timestamp(), *r) for t, r in zip(df.index, df[["open", "high", "low", "close"]].values)]
        self.now = self.rates[-1][0] + 600  # server clock: 10 min into the forming candle

    def initialize(self): return True
    def shutdown(self): pass
    def last_error(self): return (0, "")
    def account_info(self): return NS(trade_mode=self.mode, login=1, balance=10000, equity=10000)
    def symbol_select(self, s, e): return True
    def symbol_info(self, s): return INFO
    def symbol_info_tick(self, s): return NS(ask=2000.2, bid=2000.0, time=self.now)
    def positions_get(self, symbol=None): return self.pos
    def history_deals_get(self, a, b): return []
    def copy_rates_from_pos(self, s, tf, pos, n):
        import numpy as np
        dt = np.dtype([("time", "f8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8")])
        return np.array(self.rates, dtype=dt)
    def order_send(self, req):
        self.sent.append(req); return NS(retcode=self.TRADE_RETCODE_DONE)


def bot(mt5, dry=True, buys=10):
    ags = [Always(1, i) for i in range(buys)] + [Always(0, 50 + i) for i in range(10 - buys)]
    return Mt5Bot(mt5, committee=Committee(ags, 0.8), dry_run=dry)


class T(unittest.TestCase):
    def test_refuses_real_account(self):
        with self.assertRaises(RuntimeError):
            bot(FakeMT5(mode=2)).connect()

    def test_lot_size_rounds_down(self):
        # $100 risk, stop 10$ => 1000 ticks * $1 = $1000/lot => 0.10 lot
        self.assertAlmostEqual(lot_size(10_000, 0.01, 10.0, INFO), 0.10)
        self.assertEqual(lot_size(100, 0.01, 10.0, INFO), 0.0)  # below min lot -> skip

    def test_dry_run_sends_nothing(self):
        m = FakeMT5(); b = bot(m); b.connect()
        self.assertTrue(b.step().startswith("DRY-RUN BUY"))
        self.assertEqual(m.sent, [])
        self.assertEqual(b.step(), "waiting for new candle")

    def test_live_order_has_sl_tp(self):
        m = FakeMT5(); b = bot(m, dry=False); b.connect()
        self.assertTrue(b.step().startswith("PLACED"))
        r = m.sent[0]
        self.assertLess(r["sl"], r["price"]); self.assertGreater(r["tp"], r["price"])

    def test_no_second_position(self):
        m = FakeMT5(positions=[NS(magic=26001)]); b = bot(m, dry=False); b.connect()
        self.assertIn("already open", b.step()); self.assertEqual(m.sent, [])

    def test_stale_candle_skipped(self):
        m = FakeMT5(); m.now += 3 * 86400  # weekend gap: last closed candle is days old
        b = bot(m, dry=False); b.connect()
        self.assertIn("stale", b.step()); self.assertEqual(m.sent, [])

    def test_no_consensus_no_trade(self):
        m = FakeMT5(); b = bot(m, dry=False, buys=7); b.connect()
        self.assertEqual(b.step(), "HOLD"); self.assertEqual(m.sent, [])


if __name__ == "__main__":
    unittest.main()
