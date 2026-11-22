import unittest
from types import SimpleNamespace

from xauusd_agents.ai_panel import ClaudeReviewer, PanelReviewer, build_panel
from xauusd_agents.ai_review import Review
from xauusd_agents.committee import Committee
from xauusd_agents.mt5_bot import Mt5Bot
from tests.test_mt5 import Always, FakeMT5


class Fixed:
    def __init__(self, verdict, reason="r"):
        self.verdict, self.reason = verdict, reason

    def review(self, ctx):
        return Review(self.verdict, self.reason, "fake", 1, "shadow")


class Raises:
    def review(self, ctx):
        raise RuntimeError("boom")


def fake_claude(text=None, stop="end_turn", exc=None):
    def create(**kw):
        if exc:
            raise exc
        return SimpleNamespace(stop_reason=stop, model=kw["model"], stop_details=SimpleNamespace(category="cyber"),
                               content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)])
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    return client


class TestPanel(unittest.TestCase):
    def verdict(self, a, b, mode="veto"):
        return PanelReviewer({"claude": Fixed(a), "grok": Fixed(b)}, mode).review({})

    def test_both_allow_enters(self):
        r = self.verdict("ALLOW", "ALLOW")
        self.assertEqual(r.verdict, "ALLOW"); self.assertFalse(r.blocks)

    def test_any_veto_blocks(self):
        for a, b in (("ALLOW", "VETO"), ("VETO", "ALLOW"), ("VETO", "VETO"), ("VETO", "ERROR")):
            r = self.verdict(a, b)
            self.assertEqual(r.verdict, "VETO"); self.assertTrue(r.blocks, (a, b))

    def test_error_blocks_in_veto_but_not_shadow(self):
        self.assertTrue(self.verdict("ALLOW", "ERROR").blocks)
        self.assertEqual(self.verdict("ALLOW", "ERROR").verdict, "ERROR")
        for a, b in (("ALLOW", "ERROR"), ("VETO", "ALLOW"), ("VETO", "VETO")):
            self.assertFalse(self.verdict(a, b, "shadow").blocks)

    def test_reason_names_each_model(self):
        r = PanelReviewer({"claude": Fixed("ALLOW", "fine"), "grok": Fixed("VETO", "exhausted")}, "veto").review({})
        self.assertIn("claude: ALLOW (fine)", r.reason); self.assertIn("grok: VETO (exhausted)", r.reason)

    def test_raising_member_is_error(self):
        r = PanelReviewer({"claude": Fixed("ALLOW"), "grok": Raises()}, "veto").review({})
        self.assertEqual(r.verdict, "ERROR"); self.assertTrue(r.blocks)

    def test_build_panel(self):
        p = build_panel("claude, grok", "veto")
        self.assertEqual(sorted(p.reviewers), ["claude", "grok"])
        self.assertEqual(p.reviewers["grok"].url, "https://api.x.ai/v1/chat/completions")
        with self.assertRaises(ValueError):
            build_panel("claude,gpt", "veto")


class TestClaudeReviewer(unittest.TestCase):
    def test_parses_structured_reply(self):
        r = ClaudeReviewer(client=fake_claude('{"verdict":"VETO","reason":"exhausted"}')).review({"x": 1})
        self.assertEqual((r.verdict, r.reason), ("VETO", "exhausted"))

    def test_refusal_and_errors_are_error(self):
        self.assertEqual(ClaudeReviewer(client=fake_claude("", stop="refusal")).review({}).verdict, "ERROR")
        self.assertEqual(ClaudeReviewer(client=fake_claude(exc=TimeoutError("slow"))).review({}).verdict, "ERROR")
        self.assertEqual(ClaudeReviewer(client=fake_claude("not json")).review({}).verdict, "ERROR")


class TestBotWithPanel(unittest.TestCase):
    def run_bot(self, a, b):
        m = FakeMT5()
        bot = Mt5Bot(m, committee=Committee([Always(1, i) for i in range(10)], 0.8), dry_run=True, clock=lambda: m.now,
                     ai_review=PanelReviewer({"claude": Fixed(a), "grok": Fixed(b)}, "veto"))
        return bot.step()

    def test_bot_enters_only_when_both_allow(self):
        self.assertNotIn("vetoed", self.run_bot("ALLOW", "ALLOW"))
        self.assertIn("vetoed", self.run_bot("ALLOW", "VETO"))
        self.assertIn("vetoed", self.run_bot("ALLOW", "ERROR"))


if __name__ == "__main__":
    unittest.main()
