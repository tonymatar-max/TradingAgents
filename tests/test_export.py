import os
import sys
import tempfile
import types
import unittest

import numpy as np
import pandas as pd

from xauusd_agents import mt5_export


def fake_mt5(minutes=5, first="2025-06-01", limit=100_000):
    """Simulated terminal: refuses any request covering more than `limit` bars (the real failure) and has data only from `first`."""
    m = types.ModuleType("MetaTrader5")
    m.TIMEFRAME_M5 = 5; m.TIMEFRAME_H1 = 60
    m.initialize = lambda: True; m.shutdown = lambda: None; m.symbol_select = lambda s, e: True
    m.calls = []
    m._err = (0, "")
    m.last_error = lambda: m._err
    dt = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
                   ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")])
    avail_from = pd.Timestamp(first)

    def copy_rates_range(sym, tf, d0, d1):
        d0, d1 = pd.Timestamp(d0), pd.Timestamp(d1)
        m.calls.append((d0, d1))
        step = pd.Timedelta(minutes=minutes)
        if (d1 - d0) / step > limit:
            m._err = (-2, "Terminal: Invalid params"); return None
        lo = max(d0, avail_from)
        idx = pd.date_range(lo.ceil(f"{minutes}min"), d1 - step, freq=f"{minutes}min")
        idx = idx[idx.dayofweek < 5]
        arr = np.zeros(len(idx), dtype=dt)
        arr["time"] = idx.as_unit("s").asi8                       # epoch seconds whatever the pandas default unit
        arr["close"] = 2000.0 + np.arange(len(idx)) * 0.01; arr["open"] = arr["close"]; arr["high"] = arr["close"] + 1; arr["low"] = arr["close"] - 1
        return arr
    m.copy_rates_range = copy_rates_range
    return m


class Export(unittest.TestCase):
    def run_export(self, mod, **kw):
        sys.modules["MetaTrader5"] = mod
        self.addCleanup(lambda: sys.modules.pop("MetaTrader5", None))
        out = os.path.join(tempfile.mkdtemp(), "x.csv")
        mt5_export.export_csv("XAUUSD", "M5", "2025-09-01", "2025-12-31", out, **kw)
        return pd.read_csv(out, parse_dates=["time"])

    def test_big_range_is_split_so_the_terminal_accepts_it(self):
        m = fake_mt5()
        df = self.run_export(m)
        self.assertGreater(len(df), 20_000)
        self.assertTrue(df.time.is_monotonic_increasing); self.assertFalse(df.time.duplicated().any())
        self.assertGreater(len(m.calls), 1)                                   # more than one request
        self.assertTrue(all((b - a) / pd.Timedelta(minutes=5) <= 20_000 + 1 for a, b in m.calls))

    def test_would_fail_as_one_request(self):
        m = fake_mt5(limit=10_000)                                            # strict terminal: chunks must be retried smaller
        df = self.run_export(m)
        self.assertGreater(len(df), 20_000)

    def test_warmup_grows_with_scale(self):
        self.assertEqual(mt5_export.warmup_bars(1.0), 650)
        self.assertEqual(mt5_export.warmup_bars(12.0), 7800)
        m1, m12 = fake_mt5(first="2024-01-01"), fake_mt5(first="2024-01-01")
        d1, d12 = self.run_export(m1, scale=1.0), self.run_export(m12, scale=12.0)
        self.assertLess(d12.time.iloc[0], d1.time.iloc[0])                    # starts earlier to warm up the slow indicators


if __name__ == "__main__":
    unittest.main()
