"""Read an MT5 Strategy Tester report (ReportTester-*.html, UTF-16) and compare it with the Python backtest."""
from __future__ import annotations

import re
from html.parser import HTMLParser

import pandas as pd


def _rows(path: str) -> list[list[str]]:
    raw = open(path, "rb").read()
    for enc in ("utf-16", "utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeError:
            continue

    class P(HTMLParser):
        def __init__(self):
            super().__init__(); self.rows = []; self.cur = None; self.cell = None; self.skip = 0

        def handle_starttag(self, t, a):
            if t in ("script", "style"): self.skip += 1
            if t == "tr": self.cur = []
            if t in ("td", "th"): self.cell = []

        def handle_endtag(self, t):
            if t in ("script", "style"): self.skip -= 1
            if t in ("td", "th") and self.cell is not None and self.cur is not None:
                self.cur.append(re.sub(r"\s+", " ", "".join(self.cell)).strip()); self.cell = None
            if t == "tr" and self.cur is not None:
                if any(self.cur): self.rows.append([c for c in self.cur if c != ""])
                self.cur = None

        def handle_data(self, d):
            if self.skip == 0 and self.cell is not None: self.cell.append(d)

    p = P(); p.feed(text)
    return p.rows


def _num(s: str) -> float:
    return float(s.replace(" ", "").replace(" ", "").replace(",", ""))


def parse_report(path: str) -> dict:
    rows = _rows(path)
    inputs, meta = {}, {}
    for r in rows:
        for cell in r:
            m = re.match(r"^(Inp\w+)=(.*)$", cell)
            if m:
                inputs[m.group(1)] = m.group(2)
        if r[0] == "Expert:" and len(r) > 1: meta["expert"] = r[1]
        if r[0] == "Symbol:" and len(r) > 1: meta["symbol"] = r[1]
        if r[0] == "Initial Deposit:" and len(r) > 1: meta["deposit"] = _num(r[1])
        if r[0] == "Leverage:" and len(r) > 1: meta["leverage"] = float(r[1].split(":")[-1])
        if r[0] == "History Quality:" and len(r) > 1: meta["quality"] = r[1]
        if r[0] == "Period:" and len(r) > 1:
            m = re.search(r"\((\d{4})\.(\d\d)\.(\d\d) - (\d{4})\.(\d\d)\.(\d\d)\)", r[1])
            if m:
                meta["start"], meta["end"] = "-".join(m.groups()[0:3]), "-".join(m.groups()[3:6])
            meta["period"] = r[1]
    i_deals = next((i for i, r in enumerate(rows) if r[0] == "Deals"), None)
    i_orders = next((i for i, r in enumerate(rows) if r[0] == "Orders"), None)
    if i_deals is None or i_orders is None:
        raise ValueError("no Orders/Deals tables found: is this a Strategy Tester report?")
    levels = {}
    for r in rows[i_orders + 2:i_deals]:
        if len(r) >= 10 and re.match(r"\d{4}\.", r[0]) and r[3] in ("buy", "sell"):
            try:
                levels[int(r[1])] = (_num(r[6]), _num(r[7]))
            except ValueError:
                pass
    deals = []
    for r in rows[i_deals + 2:]:
        if len(r) >= 12 and re.match(r"\d{4}\.", r[0]) and r[4] in ("in", "out"):
            deals.append({"time": pd.Timestamp(r[0].replace(".", "-", 2)), "side": r[3], "dirn": r[4], "price": _num(r[6]), "order": int(r[7]),
                          "comm": _num(r[8]), "swap": _num(r[9]), "profit": _num(r[10]), "bal": _num(r[11]), "comment": r[12] if len(r) > 12 else ""})
    d = pd.DataFrame(deals)
    ins, outs = d[d.dirn == "in"].reset_index(drop=True), d[d.dirn == "out"].reset_index(drop=True)
    n = min(len(ins), len(outs))
    ins, outs = ins.iloc[:n], outs.iloc[:n]
    t = pd.DataFrame({"open": ins.time, "close": outs.time, "dir": ins.side.map({"buy": 1, "sell": -1}), "entry": ins.price, "exit": outs.price,
                      "profit": outs.profit, "net": outs.profit + ins.comm + outs.comm + outs.swap,
                      "reason": outs.comment.str.extract(r"^(sl|tp|end of test)", expand=False).fillna("?")})
    t["sl"] = ins.order.map(lambda o: levels.get(o, (None, None))[0]); t["tp"] = ins.order.map(lambda o: levels.get(o, (None, None))[1])
    return {"meta": meta, "inputs": inputs, "trades": t}


def _stats(net: pd.Series) -> dict:
    w, lo = net[net > 0].sum(), -net[net <= 0].sum()
    return {"trades": len(net), "win%": round(100 * (net > 0).mean(), 1) if len(net) else None,
            "pf": round(w / lo, 2) if lo else None, "net": round(net.sum(), 2)}


def compare(report: dict, py_trades: pd.DataFrame, index: pd.DatetimeIndex) -> dict:
    ea = report["trades"]
    py = py_trades.copy()
    py["open"] = index[py["entry_i"].to_numpy()]
    py["net"] = py["pnl"]
    key = lambda df: set(zip(df["open"].astype("datetime64[s]"), df["dir"].astype(int)))
    ke, kp = key(ea), key(py)
    both = ke & kp
    m = ea.merge(py[["open", "dir", "exit", "reason", "net", "entry"]], on=["open", "dir"], suffixes=("_ea", "_py"))
    return {"ea": _stats(ea.net), "python": _stats(py.net), "matched": len(both), "ea_only": sorted(ke - kp), "py_only": sorted(kp - ke),
            "same_exit_reason_%": round(100 * (m.reason_ea.str.upper() == m.reason_py.str.upper()).mean(), 1) if len(m) else None,
            "entry_price_diff_median": round((m.entry_ea - m.entry_py).abs().median(), 3) if len(m) else None}
