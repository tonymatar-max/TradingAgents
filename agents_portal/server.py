"""Agents portal: a read-only graphical view of every running committee bot.

It finds each bot's log folder (any folder next to the repo with a status.json), recomputes the 10 agents' votes on the
bot's own last 500 closed candles (live_candles.csv) and serves them to index.html. It never talks to MT5 and never
changes a setting or an order; the bots keep running exactly as they are.

Run:  python agents_portal/server.py            then open http://127.0.0.1:8780
      python agents_portal/server.py --port 8790 --root C:\\tradingagents
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from xauusd_agents.agents import default_agents  # noqa: E402
from xauusd_agents import coach  # noqa: E402
from xauusd_agents.data import load_csv  # noqa: E402

STALE_AFTER_S = 2 * 3600  # an H1 bot updates at least every hour


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def find_bots(root: str) -> list[str]:
    out = []
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if not name.startswith("old_") and os.path.isdir(d) and os.path.isfile(os.path.join(d, "status.json")):  # old_*: archived
            out.append(name)
    return out


def bot_summary(root: str, name: str) -> dict:
    st = _read_json(os.path.join(root, name, "status.json")) or {}
    price = _read_json(os.path.join(root, name, "live_price.json")) or {}
    s = st.get("settings", {})
    age = time.time() - float(st.get("updated", 0) or 0)
    return {"id": name, "symbol": st.get("symbol", "?"), "mode": st.get("mode", "?"), "timeframe": s.get("timeframe"),
            "votes_needed": s.get("votes"), "sl_atr": s.get("sl_atr"), "tp_atr": s.get("tp_atr"), "magic": s.get("magic"),
            "paused": s.get("paused"), "balance": st.get("balance"), "equity": st.get("equity"),
            "last_bar": st.get("last_bar"), "last_status": st.get("last_status"), "paper_open": st.get("paper_open"),
            "bid": price.get("bid"), "ask": price.get("ask"), "updated_age_s": round(age),
            "running": age < STALE_AFTER_S}


def read_events(path: str, limit: int = 3000) -> list[dict]:
    ev = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    ev.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return ev[-limit:]


def _num(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else x


def bot_detail(root: str, name: str, bars: int = 150) -> dict:
    d = os.path.join(root, name)
    summ = bot_summary(root, name)
    out = {"bot": summ, "agents": [], "candles": [], "events": []}
    csv = os.path.join(d, "live_candles.csv")
    if os.path.isfile(csv):
        df = load_csv(csv)
        agents = default_agents()
        table = {a.name: a.votes(df) for a in agents}
        tail = df.iloc[-bars:]
        out["candles"] = [{"t": str(t), "o": r.open, "h": r.high, "l": r.low, "c": r.close} for t, r in tail.iterrows()]
        need = summ.get("votes_needed") or 9
        buys = sells = None
        for a in agents:
            v = table[a.name].iloc[-bars:].fillna(0).astype(int).tolist()
            out["agents"].append({"name": a.name, "info": getattr(a, "style", ""), "votes": v, "now": v[-1] if v else 0})
            buys = [x == 1 for x in v] if buys is None else [b + (x == 1) for b, x in zip(buys, v)]
            sells = [x == -1 for x in v] if sells is None else [s + (x == -1) for s, x in zip(sells, v)]
        out["buy_count"] = [int(b) for b in (buys or [])]
        out["sell_count"] = [int(s) for s in (sells or [])]
        out["signal"] = [1 if b >= need else -1 if s >= need else 0 for b, s in zip(out["buy_count"], out["sell_count"])]
    for e in read_events(os.path.join(d, "events.jsonl")):
        if e.get("type") in ("signal", "paper_close", "close", "skip", "ai_review", "news_block"):
            out["events"].append({k: _num(v) for k, v in e.items() if k != "votes"})
    out["events"] = out["events"][-60:]
    return out


def orb_detail(root: str) -> dict | None:
    """The ORB bot's own status file (orb_bot/logs/status_orb.json) plus its daily paper results."""
    d = os.path.join(root, "orb_bot", "logs")
    st = _read_json(os.path.join(d, "status_orb.json"))
    if st is None:
        return None
    age = time.time() - float(st.get("updated", 0) or 0)
    st["updated_age_s"], st["running"] = round(age), age < 300      # it writes every ~20 s
    hist = read_events(os.path.join(d, "orb_paper.jsonl"), limit=2000)
    st["history"] = hist[-200:]
    st["history_total"] = {"days": len({h.get("date") for h in hist}), "trades": len(hist),
                           "wins": sum(1 for h in hist if (h.get("R") or 0) > 0),
                           "R": round(sum(h.get("R") or 0 for h in hist), 2),
                           "usd": round(sum(h.get("usd") or 0 for h in hist), 2)}
    return st


import re
import subprocess

SYMBOL_RE = re.compile(r"^[A-Za-z0-9._#]{2,20}$")
NAME_RE = re.compile(r"^[A-Za-z0-9]{1,20}$")


def run_toggle(root: str, args: list[str]) -> dict:
    """Runs scripts/toggle_bot.ps1 (the same script as the command line) and returns its JSON view."""
    script = os.path.join(root, "scripts", "toggle_bot.ps1")
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, *args, "-Json"],
                       capture_output=True, text=True, timeout=120, cwd=root)
    out = r.stdout.strip().splitlines()
    if r.returncode != 0 or not out:
        msg = (r.stderr or r.stdout).strip().splitlines()
        raise RuntimeError(next((m for m in msg if m.strip() and not m.startswith(("At ", "+", " "))), "toggle_bot.ps1 failed"))
    return json.loads(out[-1])


def symbol_check(symbol: str) -> str | None:
    """None if MT5 knows the symbol (or MT5 is not reachable from here); else a message."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return None
    if not mt5.initialize():
        return None
    try:
        return None if mt5.symbol_info(symbol) is not None else f"{symbol} is not a symbol on this MT5 account (check the exact name in Market Watch)"
    finally:
        mt5.shutdown()


class Handler(BaseHTTPRequestHandler):
    root = "."
    toggle = staticmethod(run_toggle)
    check_symbol = staticmethod(symbol_check)

    def _may_change(self) -> str | None:
        """Changes only from this machine, from the portal's own page (a custom header blocks other websites)."""
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return "changes are only allowed from the machine the portal runs on"
        if self.headers.get("X-Portal") != "1":
            return "missing X-Portal header"
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost", "::1"):
            return "wrong origin"
        return None

    def do_POST(self):
        u = urlparse(self.path)
        if u.path not in ("/api/instruments", "/api/coach"):
            return self._send(404, b"not found", "text/plain")
        why = self._may_change()
        if why:
            return self._send(403, json.dumps({"error": why}).encode(), "application/json")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(min(n, 10_000)) or b"{}")
            if u.path == "/api/coach":
                if req.get("action") != "apply":
                    raise ValueError("unknown action")
                data = coach.apply(self.root, str(req.get("id", "")))
            elif req.get("action") == "toggle":
                name = str(req.get("name", ""))
                if not NAME_RE.match(name):
                    raise ValueError("bad name")
                data = self.toggle(self.root, ["-Name", name, "-On" if req.get("on") else "-Off"])
            elif req.get("action") == "add":
                sym = str(req.get("symbol", "")).strip()
                sl, tp = float(req.get("sl_atr", 1.5)), float(req.get("tp_atr", 2.0))
                if not SYMBOL_RE.match(sym):
                    raise ValueError("symbol: letters, digits and . _ # only")
                if not (0 < sl <= 10 and 0 < tp <= 20):
                    raise ValueError("stop must be 0-10 ATR and target 0-20 ATR")
                bad = self.check_symbol(sym)
                if bad:
                    raise ValueError(bad)
                data = self.toggle(self.root, ["-Add", "-Symbol", sym, "-SlAtr", str(sl), "-TpAtr", str(tp)])
            else:
                raise ValueError("unknown action")
            return self._send(200, json.dumps(data).encode(), "application/json")
        except (ValueError, RuntimeError, subprocess.TimeoutExpired) as e:
            return self._send(400, json.dumps({"error": str(e)}).encode(), "application/json")

    def log_message(self, *args):
        pass

    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if u.path == "/api/bots":
                data = [bot_summary(self.root, n) for n in find_bots(self.root)]
            elif u.path == "/api/instruments":
                data = self.toggle(self.root, ["-List"])
            elif u.path == "/api/coach":
                data = coach.load(self.root)
                if data is None:
                    return self._send(404, b'{"error":"no suggestions yet: the coach runs weekly (Svc-Coach), or run: python -m xauusd_agents coach"}', "application/json")
            elif u.path == "/api/orb":
                data = orb_detail(self.root)
                if data is None:
                    return self._send(404, b'{"error":"no ORB status yet (orb_bot/logs/status_orb.json)"}', "application/json")
            elif u.path == "/api/bot":
                name = q.get("id", [""])[0]
                if name not in find_bots(self.root):      # only known folders: no path tricks
                    return self._send(404, b'{"error":"unknown bot"}', "application/json")
                data = bot_detail(self.root, name)
            else:
                return self._send(404, b"not found", "text/plain")
            return self._send(200, json.dumps(data, default=str).encode(), "application/json")
        except Exception as e:  # noqa: BLE001 - show the error, keep serving
            return self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}).encode(), "application/json")


def main():
    p = argparse.ArgumentParser(description="Read-only graphical portal of the committee agents")
    p.add_argument("--root", default=os.path.dirname(HERE), help="folder that holds the bots' log folders")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8780)
    a = p.parse_args()
    Handler.root = os.path.abspath(a.root)
    print(f"Agents portal on http://{a.host}:{a.port}  (bots found: {', '.join(find_bots(Handler.root)) or 'none'})")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
