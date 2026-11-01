"""Read-only local web portal: python -m xauusd_agents portal  ->  http://127.0.0.1:8765

Reads logs/events.jsonl, status.json and the latest news_*.txt written by the bot. Standard library only."""
from __future__ import annotations

import glob
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PAGE = Path(__file__).with_name("portal.html")


def _read_events(path: str) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue            # ignore a half-written last line
    except OSError:
        pass
    return out


def paper_stats(trades: list[dict], start_balance: float | None) -> dict:
    n = len(trades)
    if not n:
        return {"trades": 0}
    pnl = [t["pnl"] for t in trades]
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p <= 0]
    cum, peak, max_dd, curve = 0.0, 0.0, 0.0, []
    for t in trades:
        cum += t["pnl"]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        curve.append({"t": t.get("closed_bar", t.get("ts")), "cum": round(cum, 2)})
    base = (start_balance or 0) + peak
    return {"trades": n, "win_rate": round(100 * len(wins) / n, 1),
            "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None,
            "net": round(sum(pnl), 2), "max_dd_usd": round(max_dd, 2),
            "max_dd_pct": round(100 * max_dd / base, 1) if base > 0 else None,
            "best": round(max(pnl), 2), "worst": round(min(pnl), 2), "curve": curve}


def build_state(logdir: str) -> dict:
    ev = _read_events(os.path.join(logdir, "events.jsonl"))
    try:
        with open(os.path.join(logdir, "status.json"), encoding="utf-8") as f:
            status = json.load(f)
    except (OSError, ValueError):
        status = None
    news_files = sorted(glob.glob(os.path.join(logdir, "news_*.txt")))
    news = Path(news_files[-1]).read_text(encoding="utf-8") if news_files else None
    candles = [e for e in ev if e.get("type") in ("candle", "skip")]
    trades = [e for e in ev if e.get("type") == "paper_close"]
    signals = [e for e in ev if e.get("type") == "signal"]
    start = (status or {}).get("paper_start_balance")
    return {"server_time": time.time(), "status": status,
            "age_s": None if not status else round(time.time() - status["updated"]),
            "decisions": candles[-40:][::-1], "signals": signals[-15:][::-1],
            "paper": {"open": (status or {}).get("paper_open"), "trades": trades[-30:][::-1],
                      "stats": paper_stats(trades, start)},
            "counts": {"candles": len([c for c in candles if c["type"] == "candle"]),
                       "signals": len(signals),
                       "buy": len([c for c in candles if c.get("action") == "BUY"]),
                       "sell": len([c for c in candles if c.get("action") == "SELL"])},
            "news": news}


def make_handler(logdir: str, token: str | None):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if token:
                given = parse_qs(u.query).get("token", [""])[0] or self.headers.get("X-Token", "")
                if not hmac.compare_digest(given, token):
                    return self._send(401, b"token required: open /?token=YOUR_TOKEN", "text/plain")
            if u.path == "/api/state":
                return self._send(200, json.dumps(build_state(logdir)).encode(), "application/json")
            if u.path in ("/", "/index.html"):
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            self._send(404, b"not found", "text/plain")

    return H


def serve(logdir="logs", host="127.0.0.1", port=8765, token=None):
    srv = ThreadingHTTPServer((host, port), make_handler(logdir, token))
    where = "http://%s:%d/" % ("127.0.0.1" if host in ("127.0.0.1", "0.0.0.0") else host, port)
    print(f"Portal running at {where}{'?token=...' if token else ''}  (reading {os.path.abspath(logdir)})  Ctrl+C to stop")
    if host != "127.0.0.1" and not token:
        print("WARNING: reachable from other devices on your network with NO password. Use --token.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return srv
