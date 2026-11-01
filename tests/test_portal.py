import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace as NS

import pandas as pd

from xauusd_agents.events import EventLog, PaperBook
from xauusd_agents.portal import build_state, make_handler, paper_stats
from http.server import ThreadingHTTPServer
from tests.test_mt5 import FakeMT5, bot

INFO = NS(trade_tick_size=0.01, trade_tick_value=1.0)


def candles(rows, start="2026-01-05 10:00"):
    idx = pd.date_range(start, periods=len(rows), freq="h")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


class Paper(unittest.TestCase):
    def pos(self, d="BUY"):
        return {"dir": d, "lots": 0.1, "entry": 100.0, "sl": 98.0 if d == "BUY" else 102.0,
                "tp": 104.0 if d == "BUY" else 96.0, "risk_usd": 20, "risk_pct": 0.4, "opened_bar": "2026-01-05 10:00:00"}

    def test_tp_hit(self):
        pb = PaperBook(tempfile.mkdtemp(), 5000)
        pb.open_position(self.pos(), "2026-01-05 10:00:00")
        df = candles([(100, 101, 99, 100), (100, 102, 99.5, 101), (101, 105, 100.5, 104)])  # bars 10:00, 11:00, 12:00
        out = pb.update(df, INFO)
        self.assertEqual(len(out), 1); self.assertEqual(out[0]["reason"], "TP")
        self.assertAlmostEqual(out[0]["pnl"], 4.0 / 0.01 * 1.0 * 0.1)   # +$40
        self.assertIsNone(pb.open)

    def test_sl_first_when_both_touched(self):
        pb = PaperBook(tempfile.mkdtemp(), 5000)
        pb.open_position(self.pos(), "2026-01-05 10:00:00")
        out = pb.update(candles([(100, 100, 100, 100), (100, 105, 97, 100)]), INFO)  # one candle touches SL and TP
        self.assertEqual(out[0]["reason"], "SL"); self.assertLess(out[0]["pnl"], 0)

    def test_short_and_no_reprocessing_and_persistence(self):
        d = tempfile.mkdtemp()
        pb = PaperBook(d, 5000)
        pb.open_position(self.pos("SELL"), "2026-01-05 10:00:00")
        df = candles([(100, 100, 100, 100), (100, 101, 99, 100)])
        self.assertEqual(pb.update(df, INFO), [])            # nothing touched, still open
        pb2 = PaperBook(d)                                   # restart: state reloads
        self.assertIsNotNone(pb2.open); self.assertEqual(pb2.state["start_balance"], 5000)
        out = pb2.update(candles([(100, 100, 100, 100), (100, 101, 99, 100), (100, 100.5, 95, 96)]), INFO)
        self.assertEqual(out[0]["reason"], "TP"); self.assertGreater(out[0]["pnl"], 0)


class Stats(unittest.TestCase):
    def test_stats(self):
        t = [{"pnl": 100, "closed_bar": "a"}, {"pnl": -50, "closed_bar": "b"}, {"pnl": 25, "closed_bar": "c"}]
        s = paper_stats(t, 5000)
        self.assertEqual(s["trades"], 3); self.assertEqual(s["net"], 75); self.assertEqual(s["win_rate"], 66.7)
        self.assertEqual(s["profit_factor"], 2.5); self.assertEqual(s["max_dd_usd"], 50)
        self.assertEqual(paper_stats([], 5000), {"trades": 0})


class BotWritesEvents(unittest.TestCase):
    def test_dry_run_records_events_and_blocks_second_signal(self):
        d = tempfile.mkdtemp()
        m = FakeMT5(); b = bot(m, dry=True); b.events = EventLog(d); b._logdir = d; b.connect()
        self.assertTrue(b.step().startswith("DRY-RUN BUY"))
        self.assertIsNotNone(b.paper.open)                     # paper position opened
        b.last_bar = None                                      # pretend a new candle with the same strong signal
        self.assertIn("already open", b.step())                # one position at a time, even in dry-run
        ev = [json.loads(l) for l in open(os.path.join(d, "events.jsonl"))]
        self.assertEqual([e["type"] for e in ev], ["signal", "candle", "candle"])
        self.assertEqual(ev[1]["buy"], 10); self.assertIn("votes", ev[1])
        st = json.load(open(os.path.join(d, "status.json")))
        self.assertEqual(st["mode"], "DRY RUN"); self.assertIsNotNone(st["paper_open"])


class Server(unittest.TestCase):
    def serve(self, d, token=None):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(d, token))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return "http://127.0.0.1:%d" % srv.server_address[1]

    def test_api_and_page(self):
        d = tempfile.mkdtemp()
        EventLog(d).append(type="candle", bar="x", action="HOLD", buy=1, sell=0, neutral=9, need=9, votes={}, status="HOLD")
        base = self.serve(d)
        s = json.load(urllib.request.urlopen(base + "/api/state"))
        self.assertEqual(s["counts"]["candles"], 1); self.assertIsNone(s["status"])
        self.assertIn(b"XAUUSD 10-Agent Portal", urllib.request.urlopen(base + "/").read())

    def test_token_required(self):
        base = self.serve(tempfile.mkdtemp(), token="secret")
        with self.assertRaises(urllib.error.HTTPError) as c:
            urllib.request.urlopen(base + "/api/state")
        self.assertEqual(c.exception.code, 401)
        self.assertEqual(urllib.request.urlopen(base + "/api/state?token=secret").status, 200)

    def test_corrupt_last_line_ignored(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "events.jsonl"), "w") as f:
            f.write(json.dumps({"type": "signal", "dir": "BUY"}) + "\n{broken")
        self.assertEqual(build_state(d)["counts"]["signals"], 1)


if __name__ == "__main__":
    unittest.main()


class Editing(unittest.TestCase):
    def serve(self, d):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(d, None, "pw123", loopback=True))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return "http://127.0.0.1:%d" % srv.server_address[1]

    def post(self, base, path, body, headers=None):
        h = {"Content-Type": "application/json", "X-Edit-Token": "pw123", **(headers or {})}
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers=h, method="POST")
        try:
            r = urllib.request.urlopen(req); return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_validate_limits(self):
        from xauusd_agents import live_settings as ls
        self.assertEqual(ls.validate({"lots": 0.1, "risk": 0.01, "paused": True})[1], [])
        self.assertTrue(ls.validate({"lots": 5})[1]);        self.assertTrue(ls.validate({"lots": 0})[1])
        self.assertTrue(ls.validate({"risk": 0.5})[1]);      self.assertTrue(ls.validate({"live_orders": True})[1])
        self.assertTrue(ls.validate({"lots": float("nan")})[1]); self.assertTrue(ls.validate({"paused": "yes"})[1])
        self.assertEqual(ls.validate({"lots": None})[0], {"lots": None})   # null = auto sizing

    def test_save_requires_password_and_valid_values(self):
        d = tempfile.mkdtemp(); base = self.serve(d)
        self.assertEqual(self.post(base, "/api/settings", {"lots": 0.1}, {"X-Edit-Token": "nope"})[0], 401)
        self.assertEqual(self.post(base, "/api/settings", {"lots": 9})[0], 400)
        self.assertEqual(self.post(base, "/api/settings", {"live_orders": True})[0], 400)
        self.assertFalse(os.path.exists(os.path.join(d, "settings.json")))
        code, j = self.post(base, "/api/settings", {"lots": 0.1, "max_risk": 0.1})
        self.assertEqual(code, 200); self.assertEqual(json.load(open(os.path.join(d, "settings.json")))["lots"], 0.1)
        st = json.load(urllib.request.urlopen(base + "/api/state"))
        self.assertEqual(st["editing"]["file"]["lots"], 0.1); self.assertEqual(len(st["editing"]["audit"]), 1)
        self.assertEqual(self.post(base, "/api/settings/reset", {})[0], 200)
        self.assertFalse(os.path.exists(os.path.join(d, "settings.json")))

    def test_cross_site_and_wrong_content_type_refused(self):
        base = self.serve(tempfile.mkdtemp())
        self.assertEqual(self.post(base, "/api/settings", {"lots": 0.1}, {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.post(base, "/api/settings", {"lots": 0.1}, {"Host": "evil.example"})[0], 403)
        self.assertEqual(self.post(base, "/api/settings", {"lots": 0.1}, {"Content-Type": "text/plain"})[0], 415)

    def test_token_file_created_once(self):
        from xauusd_agents.portal import edit_token
        d = tempfile.mkdtemp(); t = edit_token(d, None)
        self.assertGreaterEqual(len(t), 10); self.assertEqual(edit_token(d, None), t)
        self.assertEqual(edit_token(d, "mine"), "mine")


class BotAppliesEdits(unittest.TestCase):
    def mk(self):
        d = tempfile.mkdtemp()
        m = FakeMT5(); b = bot(m, dry=False); b.events = EventLog(d); b._logdir = d; b.connect()
        return d, m, b

    def test_fixed_lot_pause_and_reset(self):
        from xauusd_agents import live_settings as ls
        d, m, b = self.mk()
        ls.write(d, {"lots": 0.07, "max_risk": 0.0}, "test")
        out = b.step()
        self.assertTrue(out.startswith("PLACED BUY 0.07 "), out); self.assertEqual(m.sent[0]["volume"], 0.07)
        # pause: no new order even with a fresh strong signal
        m.pos, b.last_bar = [], None
        ls.write(d, {"paused": True}, "test")
        self.assertIn("paused from the portal", b.step()); self.assertEqual(len(m.sent), 1)
        # reset: back to startup settings (auto sizing, not paused)
        ls.reset(d, "test"); b.last_bar = None
        out = b.step()
        self.assertFalse(b.paused); self.assertIsNone(b.fixed_lots)
        self.assertTrue(out.startswith("PLACED BUY"), out)      # trading again, auto-sized
        ev = [json.loads(l) for l in open(os.path.join(d, "events.jsonl"))]
        self.assertTrue(any(e["type"] == "settings_changed" for e in ev))

    def test_bad_values_in_file_are_ignored(self):
        d, m, b = self.mk()
        with open(os.path.join(d, "settings.json"), "w") as f:
            json.dump({"lots": 99, "risk": 0.01}, f)           # lots out of range
        b.step()
        self.assertIsNone(b.fixed_lots)

    def test_cap_skips_wide_stop(self):
        from xauusd_agents import live_settings as ls
        d, m, b = self.mk()
        ls.write(d, {"lots": 1.0, "max_risk": 0.0001}, "test")
        self.assertIn("exceeds", b.step()); self.assertEqual(m.sent, [])


class Votes(unittest.TestCase):
    def test_validate_votes(self):
        from xauusd_agents import live_settings as ls
        self.assertEqual(ls.validate({"votes": 9})[0], {"votes": 9})
        for bad in (6, 11, 8.5, True, "8", None, float("nan")):
            self.assertTrue(ls.validate({"votes": bad})[1], bad)

    def test_bot_changes_required_votes_and_resets(self):
        from xauusd_agents import live_settings as ls
        d = tempfile.mkdtemp()
        m = FakeMT5(); b = bot(m, dry=False, buys=8); b.events = EventLog(d); b._logdir = d; b.connect()
        self.assertEqual(b.committee.required, 8)
        ls.write(d, {"votes": 9}, "test")
        self.assertEqual(b.step(), "HOLD"); self.assertEqual(b.committee.required, 9)   # 8 buys no longer enough
        self.assertEqual(m.sent, [])
        ls.reset(d, "test"); b.last_bar = None
        self.assertTrue(b.step().startswith("PLACED BUY")); self.assertEqual(b.committee.required, 8)
