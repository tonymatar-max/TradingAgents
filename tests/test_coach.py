import json
import os
import shutil
import tempfile
import unittest

from xauusd_agents import coach, live_settings
from xauusd_agents.data import synthetic_xauusd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fake_llm(suggestions):
    text = "let me think {not json} " + json.dumps({"suggestions": suggestions})
    return lambda url, headers, body, timeout: {"model": "fake-model", "choices": [{"message": {"content": text}}]}


def offline(url, headers, body, timeout):
    raise OSError("connection refused")


class Parsing(unittest.TestCase):
    bots = [{"name": "GBPUSD", "votes": 9, "sl_atr": 1.5, "tp_atr": 4.0}]

    def test_last_suggestions_object_wins(self):
        t = '{"suggestions":[{"bot":"A"}]} then {"suggestions":[{"bot":"B"}]}'
        self.assertEqual(coach.parse_suggestions(t), [{"bot": "B"}])
        self.assertEqual(coach.parse_suggestions("no json here"), [])

    def test_only_allowed_changes_survive(self):
        raw = [{"bot": "NOPE", "votes": 10}, {"bot": "gbpusd", "votes": 7}, {"bot": "GBPUSD", "sl_atr": 9},
               {"bot": "GBPUSD", "votes": 9}, {"bot": "GBPUSD", "lots": 5, "tp_atr": 3, "reason": "x"}, "junk"]
        self.assertEqual(coach.clean_suggestions(raw, self.bots),
                         [{"bot": "GBPUSD", "votes": 9, "sl_atr": 1.5, "tp_atr": 3.0, "reason": "x"}])

    def test_passes_needs_both_halves(self):
        cur = {"first": {"n": 50, "pf": 1.1}, "second": {"n": 50, "pf": 1.2}}
        self.assertTrue(coach.passes(cur, {"first": {"n": 40, "pf": 1.2}, "second": {"n": 40, "pf": 1.3}})[0])
        self.assertFalse(coach.passes(cur, {"first": {"n": 40, "pf": 1.5}, "second": {"n": 40, "pf": 1.1}})[0])
        self.assertFalse(coach.passes(cur, {"first": {"n": 10, "pf": 2.0}, "second": {"n": 40, "pf": 2.0}})[0])
        self.assertFalse(coach.passes({"first": {"n": 50, "pf": 0.8}, "second": {"n": 50, "pf": 0.9}},
                                      {"first": {"n": 40, "pf": 0.95}, "second": {"n": 40, "pf": 0.99}})[0])


class Run(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        shutil.copy(os.path.join(ROOT, "bots.json"), self.root)
        self.df = synthetic_xauusd(3000)
        self.loader = lambda sym: self.df

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_llm_offline_changes_nothing(self):
        out = coach.run(self.root, poster=offline, loader=self.loader)
        self.assertFalse(out["llm_ok"])
        self.assertEqual(out["items"], [])
        self.assertIn("skipped", out["note"])
        self.assertEqual(coach.load(self.root)["note"], out["note"])

    def test_suggestions_are_backtested_and_only_passed_ones_apply(self):
        out = coach.run(self.root, poster=fake_llm([{"bot": "USTEC", "votes": 10, "reason": "stricter"},
                                                    {"bot": "AAPL", "votes": 10}]),     # AAPL is off: ignored
                        loader=self.loader)
        self.assertTrue(out["llm_ok"])
        self.assertEqual(out["model"], "fake-model")
        self.assertEqual([i["bot"] for i in out["items"]], ["USTEC"])
        it = out["items"][0]
        self.assertEqual(it["change"], {"votes": 10, "sl_atr": 1.5, "tp_atr": 2.0})
        self.assertEqual(it["passed"], coach.passes(it["backtest_now"], it["backtest_new"])[0])
        logdir = os.path.join(self.root, "logs_ustec")
        it["passed"] = False
        coach.save(self.root, out)
        with self.assertRaises(ValueError):
            coach.apply(self.root, it["id"])                      # failed suggestions can never be applied
        self.assertIsNone(live_settings.read(logdir))
        it["passed"] = True
        coach.save(self.root, out)
        coach.apply(self.root, it["id"])
        self.assertEqual(live_settings.read(logdir)["votes"], 10)
        self.assertTrue(coach.load(self.root)["items"][0]["applied"])
        with self.assertRaises(ValueError):
            coach.apply(self.root, it["id"])                      # only once
        b = next(x for x in coach.load_bots(self.root) if x["name"] == "USTEC")
        self.assertEqual(b["votes"], 10)                          # next week starts from the applied settings
        with open(os.path.join(self.root, "coach", "history.jsonl")) as f:
            self.assertEqual([json.loads(x)["type"] for x in f], ["run", "apply"])

    def test_cli_reaches_coach(self):
        import subprocess
        import sys
        r = subprocess.run([sys.executable, "-m", "xauusd_agents", "coach", "--root", self.root, "--no-download",
                            "--ai-url", "http://127.0.0.1:9/v1"], capture_output=True, text=True, cwd=ROOT, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Nothing was changed", r.stdout)
        self.assertIsNotNone(coach.load(self.root))


if __name__ == "__main__":
    unittest.main()
