import os
import subprocess
import sys
import tempfile
import unittest

import pandas as pd

from xauusd_agents import Committee, Config, run_backtest
from xauusd_agents.data import synthetic_xauusd
from xauusd_agents.htf import filter_signals, htf_direction, resample_ohlc
from xauusd_agents.tester_report import parse_report


def fmt(t):
    return t.strftime("%Y.%m.%d %H:%M:%S")


def write_report(path, trades, df, inputs, start, end, deposit=5000.0):
    """Minimal MT5-style tester report (UTF-16 HTML) with Orders and Deals tables."""
    def tr(*cells):
        return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    h = ["<html><body><table>", tr("Expert:", "TestEA"), tr("Symbol:", "XAUUSD"), tr("Period:", f"H1 ({start} - {end})"),
         tr("Inputs:", "Mode=")] + [tr(f"{k}={v}") for k, v in inputs.items()] + [
        tr("Company:", "Test"), tr("Initial Deposit:", f"{deposit:,.2f}".replace(",", " ")), tr("Leverage:", "1:100"),
        tr("History Quality:", "100% real ticks"), tr("Orders"), tr("Open Time", "Order", "Symbol", "Type")]
    deals, o = [], 1
    bal = deposit
    for _, r in trades.iterrows():
        o_in, o_out = o, o + 1; o += 2
        ot, ct = fmt(r["open"]), fmt(r["close"])
        side, opp = ("buy", "sell") if r["dir"] > 0 else ("sell", "buy")
        h.append(tr(ot, o_in, "XAUUSD", side, "0.3 / 0.3", "0.000", f"{r['sl']:.3f}", f"{r['tp']:.3f}", ot, "filled", "10-agent consensus"))
        h.append(tr(ct, o_out, "XAUUSD", opp, "0.3 / 0.3", "0.000", ct, "filled", f"{r['reason'].lower()} {r['exit']:.3f}"))
        bal += r["pnl"]
        deals.append(tr(ot, o_in, "XAUUSD", side, "in", "0.3", f"{r['entry']:.3f}", o_in, "-1.65", "0.00", "0.00", f"{bal:.2f}", "10-agent consensus"))
        deals.append(tr(ct, o_out, "XAUUSD", opp, "out", "0.3", f"{r['exit']:.3f}", o_out, "0.00", "0.00", f"{r['pnl']:.2f}", f"{bal:.2f}", f"{r['reason'].lower()} {r['exit']:.3f}"))
    h += [tr("Deals"), tr("Time", "Deal", "Symbol", "Type", "Direction", "Volume", "Price", "Order", "Commission", "Swap", "Profit", "Balance", "Comment")] + deals
    h.append("</table></body></html>")
    open(path, "w", encoding="utf-16").write("\n".join(h))


class ReportCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.df = synthetic_xauusd(6000)
        cls.csv = os.path.join(cls.dir, "h1.csv")
        cls.df.rename_axis("time").to_csv(cls.csv)
        com = Committee(threshold=0.8)
        sig = filter_signals(com.signals(cls.df), htf_direction(cls.df, resample_ohlc(cls.df, "4h"), 6))
        t, _, _ = run_backtest(cls.df, com, Config(balance=5000, sl_atr=2.0, tp_atr=2.0, spread=0.30, fixed_lots=0.3, leverage=100),
                               start="2024-03-01", signals=sig)
        t = t.assign(open=cls.df.index[t.entry_i.to_numpy()], close=cls.df.index[t.exit_i.to_numpy()], reason=t.reason.str.lower())
        cls.trades = t
        cls.inputs = {"InpMode": 1, "InpVotes": 8, "InpUseHtf": "true", "InpHtfMin": 6, "InpSlAtr": 2, "InpTpAtr": "2.0", "InpLotMode": 0, "InpLots": 0.3}
        cls.start, cls.end = "2024-03-01", str(cls.df.index[-1].date()).replace("-", ".")

    def test_parser_reads_settings_and_trades(self):
        p = os.path.join(self.dir, "r.html"); write_report(p, self.trades, self.df, self.inputs, "2024.03.01", self.end)
        r = parse_report(p)
        self.assertEqual(r["inputs"]["InpVotes"], "8"); self.assertEqual(r["meta"]["deposit"], 5000.0); self.assertEqual(r["meta"]["leverage"], 100.0)
        self.assertEqual(r["meta"]["start"], "2024-03-01")
        self.assertEqual(len(r["trades"]), len(self.trades)); self.assertEqual(r["trades"].reason.iloc[0], self.trades.reason.iloc[0])
        self.assertAlmostEqual(r["trades"].sl.iloc[0], self.trades.sl.iloc[0], places=2)

    def run_cli(self, report):
        out = subprocess.run([sys.executable, "-m", "xauusd_agents", "reportcheck", "--tester-report", report, "--csv", self.csv, "--spread", "0.30"],
                             capture_output=True, text=True, timeout=600)
        self.assertEqual(out.returncode, 0, out.stderr[-700:])
        return out.stdout

    def test_end_to_end_perfect_and_with_differences(self):
        n = len(self.trades)
        p = os.path.join(self.dir, "perfect.html"); write_report(p, self.trades, self.df, self.inputs, "2024.03.01", self.end)
        out = self.run_cli(p)
        self.assertIn(f"Same entry candle and direction in both: {n}  (EA-only 0, Python-only 0)", out)
        # drop the 3rd trade and add a fake one the Python side never took
        t2 = self.trades.drop(self.trades.index[2]).copy()
        fake = self.trades.iloc[0].copy(); fake["open"] = self.df.index[200]; fake["close"] = self.df.index[205]
        t2 = pd.concat([t2, fake.to_frame().T], ignore_index=True)
        p2 = os.path.join(self.dir, "diff.html"); write_report(p2, t2, self.df, self.inputs, "2024.03.01", self.end)
        out2 = self.run_cli(p2)
        self.assertIn("EA-only 1, Python-only 1", out2); self.assertIn("Python-only (first 8)", out2)


if __name__ == "__main__":
    unittest.main()
