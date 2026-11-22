"""Portal "check an entry at a particular time": what would the committee (and optionally the AI reviewer) have said?

Replays the bot's decision on the candle you pick, from a candle CSV: the same last-500-bars window the live bot uses, the
same vote count, the same higher-timeframe confirmation (H4 candles only once closed), the same ATR stop and target.
It never trades and never touches MT5.

The optional AI opinion on a PAST candle is not evidence: the model may remember what happened. It is shown with that warning.
"""
from __future__ import annotations

import os

import pandas as pd

from . import indicators as ind
from .agents import default_agents
from .committee import Committee
from .data import load_csv
from .htf import RULES, bar_delta, last_closed_direction, resample_ohlc

WINDOW = 500            # the live bot fetches 500 bars
MIN_BARS = 250          # EMA200 etc. need warm-up (same limit as the live bot)
MAX_AHEAD = 500         # bars scanned for "what happened next"


class CheckError(ValueError):
    """A problem the user can fix (bad time, no data); the portal shows the message."""


class EntryChecker:
    def __init__(self, csv_path=None, htf_csv=None, htf_tf="H4", reviewer=None, votes=9, htf_min=0, sl_atr=1.5, tp_atr=3.0,
                 logdir="logs", news=None, agents=None):
        self.csv_path, self.htf_csv, self.htf_tf, self.reviewer = csv_path, htf_csv, htf_tf, reviewer
        self.agents = agents                       # None = the 10 validated agents (tests inject fixed votes)
        self.logdir, self.news = logdir, news     # logdir: where the running bot saves its live snapshot
        self.defaults = {"votes": votes, "htf_min": htf_min, "sl_atr": sl_atr, "tp_atr": tp_atr}
        self._cache = {}

    # ---- data --------------------------------------------------------------
    def _load(self, path):
        mt = os.path.getmtime(path)
        hit = self._cache.get(path)
        if hit and hit[0] == mt:
            return hit[1]
        df = load_csv(path)
        self._cache[path] = (mt, df)
        return df

    def caps(self) -> dict:
        """What the portal page may offer (cheap; no heavy work)."""
        out = {"csv": False, "ai": self.reviewer is not None, "defaults": self.defaults,
               "live": os.path.exists(os.path.join(self.logdir, "live_price.json"))}
        if self.csv_path and os.path.exists(self.csv_path):
            try:
                df = self._load(self.csv_path)
                out.update(csv=True, first=str(df.index[min(MIN_BARS, len(df) - 1)]), last=str(df.index[-1]), bars=len(df))
            except Exception as e:  # noqa: BLE001
                out["error"] = f"cannot read candle file: {e}"
        return out

    # ---- the check ---------------------------------------------------------
    def check(self, when: str, votes=None, htf_min=None, sl_atr=None, tp_atr=None, use_ai=False) -> dict:
        if not self.csv_path or not os.path.exists(self.csv_path):
            raise CheckError("No candle file. Start the portal with --csv FILE (for example XAUUSD_H1_long.csv).")
        try:
            t = pd.Timestamp(str(when).strip()[:40])
        except (ValueError, TypeError):
            raise CheckError("Time not understood. Use a form like 2026-03-02 14:00.")
        if pd.isna(t):
            raise CheckError("Time not understood. Use a form like 2026-03-02 14:00.")
        votes = int(votes or self.defaults["votes"])
        htf_min = int(self.defaults["htf_min"] if htf_min is None else htf_min)
        sl_atr = float(sl_atr or self.defaults["sl_atr"])
        tp_atr = float(tp_atr or self.defaults["tp_atr"])
        full = self._load(self.csv_path)
        upto = full.loc[:t]
        if len(upto) < MIN_BARS:
            raise CheckError(f"Need at least {MIN_BARS} candles before that time (data starts {full.index[0]}).")
        delta = bar_delta(full.index)
        if t - upto.index[-1] > delta * 3:
            raise CheckError(f"The candle file ends at {upto.index[-1]}; pick an earlier time.")
        bar = upto.index[-1]
        df = upto.tail(WINDOW)

        agents = self.agents or default_agents()
        n = len(agents)
        com = Committee(agents)
        com.required = max(1, min(votes, n))
        com.threshold = com.required / n
        d = com.decide(df)
        atr_val = float(ind.atr(df).iloc[-1])
        close = float(df["close"].iloc[-1])

        res = {"bar": str(bar), "bar_closes": str(bar + delta), "requested": str(t), "close": round(close, 2),
               "settings": {"votes": com.required, "of": n, "htf_min": htf_min, "sl_atr": sl_atr, "tp_atr": tp_atr},
               "committee": {"action": d.action, "buy": d.buy, "sell": d.sell, "neutral": d.neutral, "need": d.required},
               "agents": [{"name": k, "vote": int(v)} for k, v in d.votes.items()], "atr": round(atr_val, 2)}

        htf_txt, blocked = "", None
        if d.action != "HOLD" and htf_min:
            direction = self._htf_direction(upto, bar, delta, htf_min)
            want = 1 if d.action == "BUY" else -1
            res["htf"] = {"tf": self.htf_tf, "needs": htf_min, "direction": direction, "confirms": direction == want}
            htf_txt = f"{self.htf_tf} confirms (>= {htf_min} agents)"
            if direction != want:
                blocked = f"{self.htf_tf} does not confirm ({direction:+d})"
        res["entry"] = ("NO ENTRY: committee says HOLD" if d.action == "HOLD"
                        else f"NO ENTRY: {d.action} signal ignored, {blocked}" if blocked else f"WOULD ENTER {d.action}")

        if d.action != "HOLD" and not blocked:
            sign = 1 if d.action == "BUY" else -1
            res["plan"] = {"approx_entry": round(close, 2), "stop": round(close - sign * sl_atr * atr_val, 2),
                           "target": round(close + sign * tp_atr * atr_val, 2),
                           "note": "approximate: the bot enters at the next price, not this close"}
            res["outcome"] = self._outcome(full, bar, d.action, sl_atr * atr_val, tp_atr * atr_val)
            if use_ai:
                res["ai"] = self._ai(d, df, atr_val, sl_atr, tp_atr, htf_txt)
        elif use_ai:
            res["ai"] = {"verdict": "SKIPPED", "reason": "no trade proposed on this candle, so there is nothing to review"}
        return res

    # ---- check right now ---------------------------------------------------
    def check_now(self, votes=None, htf_min=None, sl_atr=None, tp_atr=None, use_ai=False, status=None) -> dict:
        """The bot's decision on the latest CLOSED candle, plus the current price: same votes, same higher-timeframe
        confirmation, same news blackout, same ATR stop and target. Reads what the running bot saved in logs/.
        `status` is the bot's status.json (pause / open paper position are reported, not enforced)."""
        import json
        import time
        from .htf import last_closed_direction
        price_p = os.path.join(self.logdir, "live_price.json")
        cand_p = os.path.join(self.logdir, "live_candles.csv")
        if not (os.path.exists(price_p) and os.path.exists(cand_p)):
            raise CheckError("No live data yet. The bot writes it every cycle: restart the bot with this version and wait ~15 s.")
        with open(price_p, encoding="utf-8") as f:
            px = json.load(f)
        age = time.time() - float(px["ts"])
        if age > 180:
            raise CheckError(f"The bot's live data is {age / 60:.0f} min old: the bot is not running.")
        votes = int(votes or self.defaults["votes"])
        htf_min = int(self.defaults["htf_min"] if htf_min is None else htf_min)
        sl_atr = float(sl_atr or self.defaults["sl_atr"])
        tp_atr = float(tp_atr or self.defaults["tp_atr"])
        df = self._load(cand_p).tail(WINDOW)
        if len(df) < MIN_BARS:
            raise CheckError(f"Only {len(df)} live candles saved so far (need {MIN_BARS}).")
        agents = self.agents or default_agents()
        com = Committee(agents)
        com.required = max(1, min(votes, len(agents)))
        com.threshold = com.required / len(agents)
        d = com.decide(df)
        atr_val = float(ind.atr(df).iloc[-1])
        close = float(df["close"].iloc[-1])
        bid, ask = float(px["bid"]), float(px["ask"])
        delta = bar_delta(df.index)
        res = {"live": True, "bar": str(df.index[-1]), "bar_closes": str(df.index[-1] + delta), "close": round(close, 2),
               "price": {"bid": bid, "ask": ask, "spread": round(ask - bid, 2), "age_s": round(age)},
               "settings": {"votes": com.required, "of": len(agents), "htf_min": htf_min, "sl_atr": sl_atr, "tp_atr": tp_atr},
               "committee": {"action": d.action, "buy": d.buy, "sell": d.sell, "neutral": d.neutral, "need": d.required},
               "agents": [{"name": k, "vote": int(v)} for k, v in d.votes.items()], "atr": round(atr_val, 2)}
        blocked, htf_txt, notes = None, "", []
        if d.action != "HOLD" and htf_min:
            hp = os.path.join(self.logdir, "live_htf.csv")
            direction = last_closed_direction(self._load(hp).tail(WINDOW), htf_min) if os.path.exists(hp) else 0
            want = 1 if d.action == "BUY" else -1
            res["htf"] = {"tf": self.htf_tf, "needs": htf_min, "direction": direction, "confirms": direction == want}
            htf_txt = f"{self.htf_tf} confirms (>= {htf_min} agents)"
            if direction != want:
                blocked = f"{self.htf_tf} does not confirm ({direction:+d})"
        if d.action != "HOLD" and self.news is not None:
            nb, why = self.news.blocked()
            if nb:
                blocked = blocked or why
        st = status or {}
        if d.action != "HOLD":
            if (st.get("settings") or {}).get("paused"):
                notes.append("the bot is paused from the portal: it would not open this")
            if st.get("paper_open"):
                notes.append("a paper position is already open: the bot would ignore this signal")
        res["notes"] = notes
        res["entry"] = ("NO ENTRY NOW: committee says HOLD" if d.action == "HOLD"
                        else f"NO ENTRY NOW: {d.action} signal blocked, {blocked}" if blocked else f"ENTRY OK NOW: {d.action}")
        if d.action != "HOLD" and not blocked:
            sign, p = (1, ask) if d.action == "BUY" else (-1, bid)
            res["plan"] = {"approx_entry": round(p, 2), "stop": round(p - sign * sl_atr * atr_val, 2),
                           "target": round(p + sign * tp_atr * atr_val, 2),
                           "note": "from the current price; the bot decides once per closed candle"}
            if use_ai:
                res["ai"] = self._ai(d, df, atr_val, sl_atr, tp_atr, htf_txt, live=True)
        elif use_ai:
            res["ai"] = {"verdict": "SKIPPED", "reason": "no trade is proposed right now, so there is nothing to review"}
        return res

    def _htf_direction(self, upto, bar, delta, htf_min) -> int:
        if self.htf_csv and os.path.exists(self.htf_csv):
            h = self._load(self.htf_csv)
        else:
            h = resample_ohlc(upto, RULES[self.htf_tf])
        hd = bar_delta(h.index) if len(h) > 2 else pd.Timedelta(RULES[self.htf_tf])
        h = h[h.index + hd <= bar + delta].tail(WINDOW)      # only higher-timeframe candles already closed
        if len(h) < MIN_BARS:
            return 0
        return last_closed_direction(h, htf_min)

    @staticmethod
    def _outcome(full, bar, action, sl_d, tp_d) -> dict:
        """Entry at the next candle's open; stop first if both are touched (same rules as the paper book)."""
        nxt = full.loc[full.index > bar].head(MAX_AHEAD)
        if nxt.empty:
            return {"result": "unknown", "why": "no later candles in the file"}
        sign = 1 if action == "BUY" else -1
        entry = float(nxt["open"].iloc[0])
        sl, tp = entry - sign * sl_d, entry + sign * tp_d
        for t, r in nxt.iterrows():
            hit_sl = r["low"] <= sl if sign == 1 else r["high"] >= sl
            hit_tp = r["high"] >= tp if sign == 1 else r["low"] <= tp
            if hit_sl or hit_tp:
                return {"result": "SL" if hit_sl else "TP", "R": -1.0 if hit_sl else round(tp_d / sl_d, 2), "at": str(t),
                        "entry": round(entry, 2), "stop": round(sl, 2), "target": round(tp, 2),
                        "note": "ignores spread and slippage"}
        return {"result": "still open", "entry": round(entry, 2), "stop": round(sl, 2), "target": round(tp, 2),
                "why": f"neither level was touched in the next {len(nxt)} candles"}

    def _ai(self, d, df, atr_val, sl_atr, tp_atr, htf_txt, live=False) -> dict:
        from .ai_review import build_context
        r = self.reviewer.review(build_context(d.action, d, df, atr_val, sl_atr, tp_atr, htf=htf_txt))
        out = {"verdict": r.verdict, "reason": r.reason, "model": r.model, "latency_ms": r.latency_ms}
        if not live:
            out["warning"] = "This is a past candle: the model may remember what happened, so its opinion here is not evidence."
        return out
