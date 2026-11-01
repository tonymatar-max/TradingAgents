import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd

from xauusd_agents import Committee
from xauusd_agents.agents import InitialBalanceBreakout, InsideBarBreakout, extended_agents
from xauusd_agents.data import synthetic_xauusd
from xauusd_agents.news import NewsFilter, parse_events
from tests.test_mt5 import FakeMT5, bot


def mk(rows, start="2025-01-06 00:00"):
    idx = pd.date_range(start, periods=len(rows), freq="h")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


class Agents(unittest.TestCase):
    def test_twelve_agents_and_threshold(self):
        ags = extended_agents()
        self.assertEqual(len({a.name for a in ags}), 12)
        self.assertEqual(Committee(ags, 0.9).required, 11)

    def test_causal(self):
        df = synthetic_xauusd(1500)
        for a in (InsideBarBreakout(), InitialBalanceBreakout()):
            pd.testing.assert_series_equal(a.votes(df.iloc[:900]), a.votes(df).iloc[:900], check_names=False)

    def test_initial_balance(self):
        # day 1: 2-bar IB range 99..101, then break up, then back inside, then break down
        rows = [(100, 101, 99, 100), (100, 101, 99.5, 100), (100, 102, 100, 101.5),
                (101, 101.5, 100, 100.5), (100, 100.2, 97, 98)]
        v = InitialBalanceBreakout(ib_bars=2).votes(mk(rows)).tolist()
        self.assertEqual(v, [0, 0, 1, 0, -1])

    def test_inside_bar_long(self):
        base = [(100 + i * .5, 101 + i * .5, 99 + i * .5, 100.5 + i * .5) for i in range(60)]  # uptrend
        top = base[-1][1]
        rows = base + [(top, top + 1, top - 3, top - 1),     # mother bar
                       (top - 1, top, top - 2, top - 0.5),   # inside bar
                       (top - 0.5, top + 3, top - 1, top + 2)]  # close above mother high
        v = InsideBarBreakout().votes(mk(rows))
        self.assertEqual(v.iloc[-1], 1)


class News(unittest.TestCase):
    NOW = datetime(2026, 11, 4, 13, 45, tzinfo=timezone.utc)
    RAW = [
        {"title": "FOMC", "country": "USD", "date": "2026-11-04T14:00:00+00:00", "impact": "High"},
        {"title": "Low thing", "country": "USD", "date": "2026-11-04T13:50:00+00:00", "impact": "Low"},
        {"title": "ECB", "country": "EUR", "date": "2026-11-04T13:50:00+00:00", "impact": "High"},
        {"title": "bad", "country": "USD", "date": "nonsense", "impact": "High"},
        {"title": "no tz", "country": "USD", "date": "2026-11-04T13:50:00", "impact": "High"},
    ]

    def nf(self, raw=None, now=None, fetch_fail=False):
        def f():
            if fetch_fail:
                raise OSError("down")
            return raw if raw is not None else self.RAW
        return NewsFilter(30, 30, cache_path="/nonexistent_dir/x.json", fetcher=f, clock=lambda: now or self.NOW)

    def test_parse_filters(self):
        self.assertEqual([e["title"] for e in parse_events(self.RAW)], ["FOMC"])

    def test_blackout_window(self):
        n = self.nf()
        # cache write fails (bad dir) but in-memory events are kept only if write succeeds -> use real tmp
        import tempfile, os
        n.cache_path = os.path.join(tempfile.mkdtemp(), "c.json")
        self.assertTrue(n.blocked()[0])                      # 15 min before FOMC
        n.clock = lambda: self.NOW + timedelta(minutes=50)   # 35 min after
        self.assertFalse(n.blocked()[0])
        n.clock = lambda: self.NOW - timedelta(hours=2)
        self.assertFalse(n.blocked()[0])

    def test_fails_closed(self):
        b, why = self.nf(fetch_fail=True).blocked()
        self.assertTrue(b); self.assertIn("failing closed", why)

    def test_bot_blocked_by_news(self):
        import tempfile, os
        n = self.nf(); n.cache_path = os.path.join(tempfile.mkdtemp(), "c.json")
        m = FakeMT5(); b = bot(m, dry=False); b.news = n; b.connect()
        self.assertIn("news blackout", b.step()); self.assertEqual(m.sent, [])


if __name__ == "__main__":
    unittest.main()
