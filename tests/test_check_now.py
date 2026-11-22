import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from xauusd_agents.entry_check import CheckError, EntryChecker
from xauusd_agents.portal import build_state, make_handler
from tests.test_mt5 import Always, FakeMT5, bot

BUYERS = [Always(1, i) for i in range(10)]


class FakeReviewer:
    mode = "shadow"

    def __init__(self, verdict="ALLOW"):
        self.verdict, self.seen = verdict, []

    def review(self, ctx):
        from xauusd_agents.ai_review import Review
        self.seen.append(ctx)
        return Review(self.verdict, "test reason", "fake", 5, "shadow")


def run_bot(dry=True, buys=10):
    """A bot with every agent voting BUY, writing its live snapshot into a temp log folder."""
    d = tempfile.mkdtemp()
    m = FakeMT5()
    b = bot(m, dry=dry, buys=buys)
    from xauusd_agents.events import EventLog
    b.events, b._logdir = EventLog(d), d
    b.connect()
    b.step()
    return d, b


class Snapshot(unittest.TestCase):
    def test_bot_writes_price_and_candles(self):
        d, _ = run_bot()
        px = json.load(open(os.path.join(d, "live_price.json")))
        self.assertEqual((px["bid"], px["ask"]), (2000.0, 2000.2))
        self.assertGreater(sum(1 for _ in open(os.path.join(d, "live_candles.csv"))), 250)

    def test_snapshot_failure_never_stops_the_bot(self):
        d, b = run_bot()
        b._logdir = os.path.join(d, "no", "such", "dir")
        b._snap_bar = None
        b._snapshot()          # must not raise


class CheckNow(unittest.TestCase):
    def checker(self, d, reviewer=None, **kw):
        return EntryChecker(None, reviewer=reviewer, votes=8, htf_min=0, sl_atr=2.0, tp_atr=2.0, logdir=d, agents=kw.pop("agents", BUYERS), **kw)

    def test_buy_with_plan_from_current_price_and_ai(self):
        d, _ = run_bot()
        rv = FakeReviewer("ALLOW")
        r = self.checker(d, rv).check_now(use_ai=True)
        self.assertTrue(r["live"])
        self.assertEqual(r["committee"]["action"], "BUY")
        self.assertTrue(r["entry"].startswith("ENTRY OK NOW"))
        self.assertEqual(r["plan"]["approx_entry"], 2000.2)                 # buys at the ask
        self.assertAlmostEqual(r["plan"]["target"] - r["plan"]["approx_entry"], 2.0 * r["atr"], places=1)
        self.assertEqual(r["ai"]["verdict"], "ALLOW")
        self.assertNotIn("warning", r["ai"])                                # live, not a past candle
        self.assertEqual(len(rv.seen), 1)

    def test_ai_not_called_without_a_trade(self):
        d, _ = run_bot(buys=0)
        rv = FakeReviewer()
        r = self.checker(d, rv, agents=[Always(0, i) for i in range(10)]).check_now(use_ai=True)
        self.assertEqual(r["committee"]["action"], "HOLD")
        self.assertEqual(r["ai"]["verdict"], "SKIPPED")
        self.assertEqual(rv.seen, [])

    def test_htf_without_file_blocks(self):
        d, _ = run_bot()
        r = self.checker(d).check_now(htf_min=6)
        self.assertTrue(r["entry"].startswith("NO ENTRY NOW"))
        self.assertNotIn("plan", r)

    def test_news_blackout_blocks(self):
        d, _ = run_bot()
        news = type("N", (), {"blocked": lambda s: (True, "news blackout test")})()
        r = self.checker(d, news=news).check_now()
        self.assertIn("news blackout test", r["entry"])

    def test_notes_when_paused_or_position_open(self):
        d, _ = run_bot()
        r = self.checker(d).check_now(status={"settings": {"paused": True}, "paper_open": {"dir": "BUY"}})
        self.assertEqual(len(r["notes"]), 2)

    def test_errors(self):
        with self.assertRaises(CheckError):
            self.checker(tempfile.mkdtemp()).check_now()                    # bot never wrote anything
        d, _ = run_bot()
        p = os.path.join(d, "live_price.json")
        px = json.load(open(p)); px["ts"] = time.time() - 3600
        json.dump(px, open(p, "w"))
        with self.assertRaises(CheckError) as c:
            self.checker(d).check_now()
        self.assertIn("not running", str(c.exception))


class Http(unittest.TestCase):
    def serve(self, d, checker):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(d, None, "pw123", loopback=True, checker=checker))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return "http://127.0.0.1:%d" % srv.server_address[1]

    def post(self, url, pw, body=None):
        req = urllib.request.Request(url, data=json.dumps(body or {}).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Edit-Token": pw})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_route_needs_password_and_works_without_csv(self):
        d, _ = run_bot()
        base = self.serve(d, EntryChecker(None, reviewer=FakeReviewer(), votes=8, logdir=d, agents=BUYERS))
        self.assertEqual(self.post(base + "/api/checknow", "wrong")[0], 401)
        code, j = self.post(base + "/api/checknow", "pw123", {"ai": True})
        self.assertEqual(code, 200, j)
        self.assertEqual(j["ai"]["verdict"], "ALLOW")
        code, j = self.post(base + "/api/check", "pw123", {"time": "2026-01-02 10:00"})   # past check still needs --csv
        self.assertEqual(code, 400)

    def test_state_carries_ai_opinion_per_candle(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "events.jsonl"), "w") as f:
            f.write(json.dumps({"type": "ai_review", "bar": "2026-11-06 11:00:00", "verdict": "VETO",
                                "reason": "exhausted", "mode": "shadow", "blocked": False, "model": "m"}) + "\n")
        st = build_state(d)
        self.assertEqual(st["ai"]["2026-11-06 11:00:00"]["verdict"], "VETO")


if __name__ == "__main__":
    unittest.main()
