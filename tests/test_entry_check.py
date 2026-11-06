import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from xauusd_agents.ai_review import AiReviewer
from xauusd_agents.data import synthetic_xauusd
from xauusd_agents.entry_check import CheckError, EntryChecker
from xauusd_agents.portal import make_handler

DF = synthetic_xauusd(1500)


def write_csv(df, path):
    out = df.copy(); out.insert(0, "time", out.index.strftime("%Y-%m-%d %H:%M:%S")); out.to_csv(path, index=False)


def allow(url, headers, body, timeout):
    return {"model": "fake/m", "choices": [{"message": {"content": '{"verdict":"ALLOW","reason":"looks fine"}'}}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.csv = os.path.join(self.d, "h1.csv")
        write_csv(DF, self.csv)

    def signal_time(self, ck, **kw):
        """A candle where the (loosened) committee wants a trade."""
        for t in DF.index[400::7]:
            r = ck.check(str(t), **kw)
            if r["entry"].startswith("WOULD"):
                return str(t), r
        self.fail("no signal found in synthetic data")


class Check(Base):
    def test_result_shape_and_agents(self):
        r = EntryChecker(self.csv, votes=9).check(str(DF.index[800]))
        self.assertEqual(len(r["agents"]), 10)
        self.assertEqual(r["bar"], str(DF.index[800]))
        c = r["committee"]; self.assertEqual(c["buy"] + c["sell"] + c["neutral"], 10)
        self.assertTrue(r["entry"].startswith(("WOULD", "NO ENTRY")))

    def test_picks_latest_candle_not_after_requested_time(self):
        r = EntryChecker(self.csv).check(str(DF.index[800] + __import__("pandas").Timedelta(minutes=20)))
        self.assertEqual(r["bar"], str(DF.index[800]))

    def test_no_look_ahead(self):
        """Same answer whether or not later candles exist in the file (the check must only use the past)."""
        t = DF.index[900]
        short = os.path.join(self.d, "short.csv"); write_csv(DF.loc[:t], short)
        a = EntryChecker(self.csv, votes=1, htf_min=3).check(str(t))
        b = EntryChecker(short, votes=1, htf_min=3).check(str(t))
        for k in ("agents", "committee", "atr", "close", "entry"):
            self.assertEqual(a[k], b[k], k)
        self.assertEqual(a.get("htf"), b.get("htf"))

    def test_plan_and_outcome_when_entry(self):
        ck = EntryChecker(self.csv)
        _, r = self.signal_time(ck, votes=1)
        p = r["plan"]
        if r["committee"]["action"] == "BUY":
            self.assertLess(p["stop"], p["approx_entry"]); self.assertGreater(p["target"], p["approx_entry"])
        else:
            self.assertGreater(p["stop"], p["approx_entry"]); self.assertLess(p["target"], p["approx_entry"])
        self.assertIn(r["outcome"]["result"], ("TP", "SL", "still open", "unknown"))

    def test_htf_can_block(self):
        ck = EntryChecker(self.csv)
        blocked = 0
        for t in DF.index[400::5]:
            r = ck.check(str(t), votes=1, htf_min=10)       # all 10 agents on H4 must agree: usually refuses
            if "does not confirm" in r["entry"]:
                blocked += 1
                self.assertNotIn("plan", r)
        self.assertGreater(blocked, 0)

    def test_errors_are_messages_not_crashes(self):
        ck = EntryChecker(self.csv)
        with self.assertRaises(CheckError): ck.check("not a time")
        with self.assertRaises(CheckError): ck.check("")
        with self.assertRaises(CheckError): ck.check(str(DF.index[10]))                       # not enough warm-up
        with self.assertRaises(CheckError): ck.check("2100-01-01 00:00")                      # beyond the file
        with self.assertRaises(CheckError): EntryChecker(None).check(str(DF.index[800]))      # no candle file
        with self.assertRaises(CheckError): EntryChecker(os.path.join(self.d, "nope.csv")).check(str(DF.index[800]))

    def test_caps(self):
        c = EntryChecker(self.csv, reviewer=AiReviewer(poster=allow)).caps()
        self.assertTrue(c["csv"] and c["ai"]); self.assertEqual(c["bars"], len(DF))
        self.assertFalse(EntryChecker(None).caps()["csv"])


class WithAi(Base):
    def test_ai_opinion_carries_past_date_warning(self):
        ck = EntryChecker(self.csv, reviewer=AiReviewer(poster=allow))
        t, _ = self.signal_time(ck, votes=1)
        r = ck.check(t, votes=1, use_ai=True)
        self.assertEqual(r["ai"]["verdict"], "ALLOW"); self.assertIn("not evidence", r["ai"]["warning"])

    def test_ai_skipped_when_no_trade(self):
        ck = EntryChecker(self.csv, reviewer=AiReviewer(poster=allow))
        for t in DF.index[400::7]:
            r = ck.check(str(t), votes=10, use_ai=True)
            if r["entry"].startswith("NO ENTRY: committee"):
                self.assertEqual(r["ai"]["verdict"], "SKIPPED"); return
        self.fail("expected at least one HOLD")

    def test_ai_outage_does_not_break_the_check(self):
        def boom(*a): raise OSError("down")
        ck = EntryChecker(self.csv, reviewer=AiReviewer(poster=boom))
        t, _ = self.signal_time(ck, votes=1)
        self.assertEqual(ck.check(t, votes=1, use_ai=True)["ai"]["verdict"], "ERROR")


class Endpoint(Base):
    def serve(self, checker):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.d, None, "pw123", loopback=True, checker=checker))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return "http://127.0.0.1:%d" % srv.server_address[1]

    def post(self, base, body, pw="pw123", ctype="application/json"):
        req = urllib.request.Request(base + "/api/check", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": ctype, "X-Edit-Token": pw})
        try:
            r = urllib.request.urlopen(req); return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_requires_password_and_json(self):
        base = self.serve(EntryChecker(self.csv))
        self.assertEqual(self.post(base, {"time": str(DF.index[800])}, pw="wrong")[0], 401)
        self.assertEqual(self.post(base, {"time": str(DF.index[800])}, ctype="text/plain")[0], 415)

    def test_ok_and_user_errors(self):
        base = self.serve(EntryChecker(self.csv, reviewer=AiReviewer(poster=allow)))
        code, j = self.post(base, {"time": str(DF.index[800])})
        self.assertEqual(code, 200); self.assertIn("entry", j); self.assertNotIn("ai", j)       # AI only when asked
        code, j = self.post(base, {"time": "garbage"}); self.assertEqual(code, 400); self.assertIn("Time not understood", j["error"])
        code, j = self.post(base, {"time": str(DF.index[800]), "votes": 99}); self.assertEqual(code, 200)   # bad votes ignored
        self.assertEqual(j["settings"]["votes"], 9)

    def test_ai_flag_ignored_without_reviewer(self):
        base = self.serve(EntryChecker(self.csv))
        code, j = self.post(base, {"time": str(DF.index[800]), "ai": True})
        self.assertEqual(code, 200); self.assertNotIn("ai", j)

    def test_off_without_candle_file_and_state_caps(self):
        base = self.serve(None)
        code, j = self.post(base, {"time": str(DF.index[800])})
        self.assertEqual(code, 400); self.assertIn("--csv", j["error"])
        self.assertFalse(json.load(urllib.request.urlopen(base + "/api/state"))["check"]["csv"])
        base2 = self.serve(EntryChecker(self.csv))
        self.assertTrue(json.load(urllib.request.urlopen(base2 + "/api/state"))["check"]["csv"])

    def test_page_has_the_button(self):
        base = self.serve(EntryChecker(self.csv))
        html = urllib.request.urlopen(base + "/").read().decode()
        self.assertIn('id="chk-go"', html); self.assertIn("api/check", html)


class Cli(unittest.TestCase):
    def test_portal_accepts_csv_through_real_command_line(self):
        """CLAUDE.md pitfall: a flag that parses but does nothing. Start the real portal command and call the endpoint."""
        import socket, subprocess, sys, time
        d = tempfile.mkdtemp(); csv = os.path.join(d, "h1.csv"); write_csv(DF, csv)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
        p = subprocess.Popen([sys.executable, "-m", "xauusd_agents", "portal", "--csv", csv, "--logdir", d, "--port", str(port),
                              "--token", "pw123"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(p.kill)
        base = "http://127.0.0.1:%d" % port
        for _ in range(60):
            try:
                st = json.load(urllib.request.urlopen(base + "/api/state?token=pw123")); break
            except Exception:
                time.sleep(0.25)
        else:
            self.fail("portal did not start")
        self.assertTrue(st["check"]["csv"])
        req = urllib.request.Request(base + "/api/check?token=pw123", data=json.dumps({"time": str(DF.index[800])}).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Edit-Token": "pw123"})
        self.assertEqual(json.load(urllib.request.urlopen(req))["bar"], str(DF.index[800]))


if __name__ == "__main__":
    unittest.main()
