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
