"""Optional AI second opinion before a trade (DEMO / dry-run research tool; NOT part of the 10-agent vote).

Design rules (see README "AI review"):
  * VETO-ONLY. The model can answer ALLOW or VETO. It never opens a trade and never changes size, stop or target.
  * FAILS OPEN. If the gateway is down, slow or returns something unparseable, the verdict is ERROR and the
    validated rule-based decision stands. (The news filter fails closed because it is part of the tested design;
    this review is not, so its outage must not change behaviour.)
  * Modes: "shadow" = ask and LOG only, never block (use this first); "veto" = a VETO blocks the entry.
  * Only prices, votes and the planned stop/target distance are sent. No balance, equity, lots or account ids.
  * The API key is read from the AI_REVIEW_API_KEY environment variable, never from the command line or logs.

Backtests of this are NOT meaningful: a language model has seen historical gold prices and news in training.
Only live shadow records (logs/events.jsonl, type "ai_review") are fair evidence; see `aireport`.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass

DEFAULT_URL = "http://localhost:3001/v1"
KEY_ENV = "AI_REVIEW_API_KEY"

SYSTEM = (
    "You are a cautious risk reviewer for an automated XAUUSD (gold) strategy running on a DEMO account. "
    "A rule-based committee of indicator agents has proposed a trade. You may only reply ALLOW or VETO. "
    "VETO only when you see a concrete reason in the data given (for example the move already looks exhausted, "
    "price is pressing into a recent extreme against the proposed direction, or the supplied news context warns of "
    "unusual risk). Do not predict prices. When unsure, reply ALLOW. "
    'Reply with one JSON object only, no prose and no code fences: {"verdict":"ALLOW|VETO","reason":"one short sentence"}'
)


@dataclass
class Review:
    verdict: str            # "ALLOW" | "VETO" | "ERROR"
    reason: str
    model: str = ""
    latency_ms: int = 0
    mode: str = "shadow"

    @property
    def blocks(self) -> bool:
        """True only in veto mode with a clean VETO. ERROR and ALLOW never block."""
        return self.mode == "veto" and self.verdict == "VETO"


def parse_verdict(raw: str) -> tuple[str, str]:
    """Defensive parse of the model reply. Anything not clearly ALLOW or VETO becomes ERROR (fail open)."""
    if not isinstance(raw, str):
        return "ERROR", "reply was not text"
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`").removeprefix("json").strip()
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b < a:
        return "ERROR", "no JSON in reply"
    try:
        o = json.loads(s[a:b + 1])
    except ValueError:
        return "ERROR", "invalid JSON in reply"
    v = str(o.get("verdict", "")).strip().upper() if isinstance(o, dict) else ""
    if v not in ("ALLOW", "VETO"):
        return "ERROR", "verdict missing or not ALLOW/VETO"
    return v, str(o.get("reason", ""))[:300]


def build_context(action: str, decision, df, atr_val: float, sl_atr: float, tp_atr: float, htf: str = "",
                  news: str = "", bars: int = 24) -> dict:
    """What the reviewer sees. Deliberately small and free of account data."""
    tail = df.tail(bars)
    return {
        "proposed_action": action,
        "agents_buy": decision.buy, "agents_sell": decision.sell, "agents_neutral": decision.neutral,
        "agents_required": decision.required,
        "agent_votes": {k: int(v) for k, v in (decision.votes or {}).items()},
        "higher_timeframe_confirmation": htf or "not used",
        "news_context": news or "none supplied",
        "atr": round(float(atr_val), 2),
        "planned_stop_distance": round(sl_atr * float(atr_val), 2),
        "planned_target_distance": round(tp_atr * float(atr_val), 2),
        f"last_{len(tail)}_closed_candles_oldest_first": [
            [str(t), round(float(r.open), 2), round(float(r.high), 2), round(float(r.low), 2), round(float(r.close), 2)]
            for t, r in tail.iterrows()],
    }


def _http_post(url: str, headers: dict, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


class AiReviewer:
    def __init__(self, base_url: str = DEFAULT_URL, model: str = "auto", mode: str = "shadow",
                 api_key: str | None = None, timeout: float = 25.0, poster=None, clock=time.monotonic):
        if mode not in ("shadow", "veto"):
            raise ValueError("mode must be 'shadow' or 'veto'")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.mode, self.timeout = model, mode, timeout
        self.api_key = api_key if api_key is not None else os.environ.get(KEY_ENV, "")
        self.poster, self.clock = poster or _http_post, clock

    def review(self, ctx: dict) -> Review:
        """Ask the model. NEVER raises: any failure returns verdict ERROR."""
        t0 = self.clock()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        body = {"model": self.model, "temperature": 0.1, "max_tokens": 300,
                "messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": json.dumps(ctx, default=str)}]}
        try:
            res = self.poster(self.url, headers, body, self.timeout)
            text = res["choices"][0]["message"]["content"]
            verdict, reason = parse_verdict(text)
            served = str(res.get("model", self.model))
        except Exception as e:  # noqa: BLE001 - fail open on anything
            verdict, reason, served = "ERROR", f"{type(e).__name__}: {str(e)[:160]}", self.model
        return Review(verdict, reason, served, int((self.clock() - t0) * 1000), self.mode)
