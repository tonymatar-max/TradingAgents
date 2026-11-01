"""Structured run records for the portal: events.jsonl (append-only), status.json (heartbeat), paper_state.json."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EventLog:
    def __init__(self, logdir: str):
        self.dir = logdir
        os.makedirs(logdir, exist_ok=True)
        self.events_path = os.path.join(logdir, "events.jsonl")
        self.status_path = os.path.join(logdir, "status.json")

    def append(self, **ev):
        ev = {"ts": _now_iso(), **ev}
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, default=str) + "\n")

    def write_status(self, **st):
        st = {"updated": time.time(), **st}
        tmp = self.status_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, default=str)
        os.replace(tmp, self.status_path)  # atomic: the portal never reads a half-written file


class PaperBook:
    """Virtual position used in dry-run mode so results look like a live account (one position at a time)."""

    def __init__(self, logdir: str, start_balance: float | None = None):
        self.path = os.path.join(logdir, "paper_state.json")
        self.state = {"open": None, "last_checked": None, "start_balance": start_balance}
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    self.state.update(json.load(f))
            except (OSError, ValueError):
                pass
        if self.state.get("start_balance") is None:
            self.state["start_balance"] = start_balance
        self._save()

    @property
    def open(self):
        return self.state["open"]

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f)
        os.replace(tmp, self.path)

    def open_position(self, pos: dict, signal_bar: str):
        self.state["open"], self.state["last_checked"] = pos, signal_bar
        self._save()

    def update(self, df, info) -> list[dict]:
        """Walk candles that closed after the signal bar; close on SL/TP (SL first if both are touched)."""
        pos, closed = self.state["open"], []
        if not pos:
            return closed
        last = self.state["last_checked"]
        for t, row in df.iterrows():
            ts = t.strftime("%Y-%m-%d %H:%M:%S")
            if last and ts <= last:
                continue
            d = 1 if pos["dir"] == "BUY" else -1
            hit_sl = row["low"] <= pos["sl"] if d == 1 else row["high"] >= pos["sl"]
            hit_tp = row["high"] >= pos["tp"] if d == 1 else row["low"] <= pos["tp"]
            self.state["last_checked"] = ts
            if hit_sl or hit_tp:
                exit_px = pos["sl"] if hit_sl else pos["tp"]
                pnl = d * (exit_px - pos["entry"]) / info.trade_tick_size * info.trade_tick_value * pos["lots"]
                closed.append({**pos, "exit": exit_px, "reason": "SL" if hit_sl else "TP",
                               "pnl": round(pnl, 2), "closed_bar": ts})
                self.state["open"] = None
                break
        self._save()
        return closed
