import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents_portal"))
import server  # noqa: E402

from xauusd_agents.data import synthetic_xauusd  # noqa: E402


def make_bot(root, name, updated):
    d = os.path.join(root, name)
    os.makedirs(d)
    with open(os.path.join(d, "status.json"), "w") as f:
        json.dump({"updated": updated, "mode": "DRY RUN", "symbol": "XAUUSD", "settings": {"timeframe": "H1", "votes": 9, "magic": 1}}, f)
    synthetic_xauusd(500).rename_axis("time").to_csv(os.path.join(d, "live_candles.csv"))
    with open(os.path.join(d, "events.jsonl"), "w") as f:
        f.write(json.dumps({"type": "candle", "votes": {"a": 1}}) + "\n")
        f.write(json.dumps({"type": "signal", "dir": "BUY", "entry": 1.0}) + "\nnot json\n")


class Portal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        make_bot(cls.root, "bot_live", time.time())
        make_bot(cls.root, "bot_old", time.time() - 3 * 86400)
        os.makedirs(os.path.join(cls.root, "not_a_bot"))

    def test_find_and_running(self):
        self.assertEqual(server.find_bots(self.root), ["bot_live", "bot_old"])
        self.assertTrue(server.bot_summary(self.root, "bot_live")["running"])
        self.assertFalse(server.bot_summary(self.root, "bot_old")["running"])

    def test_detail_matches_committee(self):
        from xauusd_agents.committee import Committee
        from xauusd_agents.data import load_csv
        d = server.bot_detail(self.root, "bot_live", bars=100)
        self.assertEqual(len(d["candles"]), 100)
        self.assertEqual(len(d["agents"]), 10)
        self.assertTrue(all(len(a["votes"]) == 100 for a in d["agents"]))
        sig = Committee(threshold=0.9).signals(load_csv(os.path.join(self.root, "bot_live", "live_candles.csv")))
        self.assertEqual(d["signal"], sig.iloc[-100:].tolist())      # same rule as the bot (9 of 10)
        self.assertEqual([e["type"] for e in d["events"]], ["signal"])  # candles and bad lines are skipped

    def test_http_and_unknown_id(self):
        server.Handler.root = self.root
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            self.assertIn(b"Committee Agents", urllib.request.urlopen(base + "/").read())
            self.assertEqual(len(json.loads(urllib.request.urlopen(base + "/api/bots").read())), 2)
            self.assertEqual(json.loads(urllib.request.urlopen(base + "/api/bot?id=bot_live").read())["bot"]["id"], "bot_live")
            for bad in ("../etc", "not_a_bot", ""):
                with self.assertRaises(urllib.error.HTTPError) as c:
                    urllib.request.urlopen(base + "/api/bot?id=" + bad)
                self.assertEqual(c.exception.code, 404)
        finally:
            httpd.shutdown()


if __name__ == "__main__":
    unittest.main()
