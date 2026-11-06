import json
import os
import tempfile
import unittest

from xauusd_agents.ai_report import joined, report, summary
from xauusd_agents.ai_review import AiReviewer, Review, build_context, parse_verdict
from xauusd_agents.committee import Committee
from xauusd_agents.mt5_bot import Mt5Bot
from tests.test_mt5 import Always, FakeMT5


def reply(text, model="qwen/test"):
    return lambda url, headers, body, timeout: {"model": model, "choices": [{"message": {"content": text}}]}


def boom(*a, **k):
    raise OSError("gateway down")


def make_bot(m, reviewer, logdir=None):
    ags = [Always(1, i) for i in range(10)]
    return Mt5Bot(m, committee=Committee(ags, 0.8), dry_run=True, clock=lambda: m.now, ai_review=reviewer, logdir=logdir)


class TestParse(unittest.TestCase):
    def test_clean_and_fenced(self):
        self.assertEqual(parse_verdict('{"verdict":"VETO","reason":"exhausted"}'), ("VETO", "exhausted"))
        self.assertEqual(parse_verdict('```json\n{"verdict":"allow","reason":""}\n```')[0], "ALLOW")

    def test_thinking_aloud_models(self):
        """Seen live: a free model echoed the template, then answered. The echoed 'ALLOW|VETO' must not count; the last real verdict wins."""
        echo = 'I must reply as {"verdict":"ALLOW|VETO","reason":"one short sentence"}. Looking at the data... '
        self.assertEqual(parse_verdict(echo)[0], "ERROR")                                   # echo only (e.g. cut off) = fail open
        self.assertEqual(parse_verdict(echo + '{"verdict":"VETO","reason":"exhausted"}'), ("VETO", "exhausted"))
        self.assertEqual(parse_verdict('{"verdict":"VETO","reason":"a"} on reflection {"verdict":"ALLOW","reason":"b"}'), ("ALLOW", "b"))

    def test_garbage_is_error_not_veto(self):
        for bad in ("", "I think we should not trade", "{broken", '{"verdict":"MAYBE"}', '{"reason":"x"}', None, 5):
            self.assertEqual(parse_verdict(bad)[0], "ERROR", bad)


class TestReviewer(unittest.TestCase):
    def test_modes(self):
        self.assertTrue(AiReviewer(mode="veto", poster=reply('{"verdict":"VETO","reason":"r"}')).review({}).blocks)
        self.assertFalse(AiReviewer(mode="shadow", poster=reply('{"verdict":"VETO","reason":"r"}')).review({}).blocks)
        self.assertFalse(AiReviewer(mode="veto", poster=reply('{"verdict":"ALLOW","reason":"r"}')).review({}).blocks)
        with self.assertRaises(ValueError):
            AiReviewer(mode="auto")

    def test_failure_fails_open(self):
        r = AiReviewer(mode="veto", poster=boom).review({})
        self.assertEqual(r.verdict, "ERROR"); self.assertFalse(r.blocks); self.assertIn("gateway down", r.reason)
        self.assertFalse(AiReviewer(mode="veto", poster=reply("not json")).review({}).blocks)
        self.assertFalse(AiReviewer(mode="veto", poster=lambda *a: {"unexpected": 1}).review({}).blocks)

    def test_key_from_env_only_and_request_shape(self):
        seen = {}

        def poster(url, headers, body, timeout):
            seen.update(url=url, headers=headers, body=body)
            return {"choices": [{"message": {"content": '{"verdict":"ALLOW","reason":""}'}}]}
        os.environ["AI_REVIEW_API_KEY"] = "k-test"
        try:
            AiReviewer("http://x/v1/", "m1", poster=poster).review({"a": 1})
        finally:
            del os.environ["AI_REVIEW_API_KEY"]
        self.assertEqual(seen["url"], "http://x/v1/chat/completions")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer k-test")
        self.assertEqual(seen["body"]["model"], "m1")

    def test_context_has_no_account_data(self):
        m = FakeMT5(); b = make_bot(m, None); b.connect()
        df = b.fetch(); d = b.committee.decide(df)
        ctx = build_context("BUY", d, df, 5.0, 1.5, 3.0)
        txt = json.dumps(ctx).lower()
        for word in ("balance", "equity", "login", "lots", "magic"):
            self.assertNotIn(word, txt)
        self.assertEqual(ctx["planned_stop_distance"], 7.5)
        self.assertEqual(len([k for k in ctx if k.startswith("last_")]), 1)


class TestBot(unittest.TestCase):
    def test_shadow_logs_but_never_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            m = FakeMT5(); b = make_bot(m, AiReviewer(mode="shadow", poster=reply('{"verdict":"VETO","reason":"exhausted"}')), d)
            b.connect()
            self.assertTrue(b.step().startswith("DRY-RUN BUY"))
            ev = [json.loads(x) for x in open(os.path.join(d, "events.jsonl"), encoding="utf-8")]
            op = [e for e in ev if e["type"] == "ai_review"]
            self.assertEqual(len(op), 1)
            self.assertEqual((op[0]["verdict"], op[0]["blocked"], op[0]["mode"]), ("VETO", False, "shadow"))

    def test_veto_mode_blocks_entry(self):
        m = FakeMT5(); b = make_bot(m, AiReviewer(mode="veto", poster=reply('{"verdict":"VETO","reason":"exhausted"}')))
        b.connect()
        out = b.step()
        self.assertIn("AI review vetoed", out); self.assertIn("exhausted", out)
        self.assertFalse(b.has_position())

    def test_veto_mode_outage_still_trades(self):
        m = FakeMT5(); b = make_bot(m, AiReviewer(mode="veto", poster=boom)); b.connect()
        self.assertTrue(b.step().startswith("DRY-RUN BUY"))

    def test_reviewer_bug_cannot_stop_the_bot(self):
        class Broken:
            def review(self, ctx):
                raise RuntimeError("bug")
        m = FakeMT5(); b = make_bot(m, Broken()); b.connect()
        self.assertTrue(b.step().startswith("DRY-RUN BUY"))

    def test_no_reviewer_unchanged_and_hold_not_reviewed(self):
        calls = []
        rv = AiReviewer(mode="veto", poster=lambda *a: calls.append(1) or {"choices": [{"message": {"content": "{}"}}]})
        m = FakeMT5()
        ags = [Always(1, i) for i in range(7)] + [Always(0, 50 + i) for i in range(3)]
        b = Mt5Bot(m, committee=Committee(ags, 0.8), dry_run=True, clock=lambda: m.now, ai_review=rv); b.connect()
        self.assertEqual(b.step(), "HOLD"); self.assertEqual(calls, [])


class TestReport(unittest.TestCase):
    def _write(self, d, rows):
        with open(os.path.join(d, "events.jsonl"), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def test_join_and_summary(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, [
                {"type": "ai_review", "bar": "b1", "verdict": "VETO", "reason": "x", "model": "m", "latency_ms": 900},
                {"type": "ai_review", "bar": "b2", "verdict": "ALLOW", "reason": "", "model": "m", "latency_ms": 700},
                {"type": "ai_review", "bar": "b3", "verdict": "ERROR", "reason": "down", "model": "m", "latency_ms": 20},
                {"type": "paper_close", "opened_bar": "b1", "pnl": -100.0, "risk_usd": 100.0, "dir": "BUY", "reason": "SL"},
                {"type": "paper_close", "opened_bar": "b2", "pnl": 100.0, "risk_usd": 100.0, "dir": "BUY", "reason": "TP"},
                {"type": "paper_close", "opened_bar": "b9", "pnl": 5.0, "risk_usd": 10.0, "dir": "BUY", "reason": "TP"},  # no opinion
            ])
            j = joined(d)
            self.assertEqual(len(j), 2)
            s = summary(j)
            self.assertEqual(s.loc["VETO", "avg_R"], -1.0); self.assertEqual(s.loc["ALLOW", "avg_R"], 1.0)
            txt = report(d)
            self.assertIn("AI opinions: 3", txt); self.assertIn("fewer than 30", txt); self.assertIn("vetoed -1.00 vs allowed +1.00", txt)

    def test_empty_log_message(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("No AI review records", report(d))


class TestCli(unittest.TestCase):
    def test_flags_are_wired_through_the_real_command_line(self):
        """Pitfall in CLAUDE.md: a flag that parses but does nothing. Check the parser and the aireport command end to end."""
        import subprocess, sys
        with tempfile.TemporaryDirectory() as d:
            self._ok(d)
            out = subprocess.run([sys.executable, "-m", "xauusd_agents", "aireport", "--logdir", d], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("AI opinions: 1", out.stdout)
        bad = subprocess.run([sys.executable, "-m", "xauusd_agents", "mt5", "--ai-review", "bogus"], capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)

    def _ok(self, d):
        with open(os.path.join(d, "events.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "ai_review", "bar": "b1", "verdict": "ALLOW", "reason": "", "model": "m", "latency_ms": 5}) + "\n")


if __name__ == "__main__":
    unittest.main()
