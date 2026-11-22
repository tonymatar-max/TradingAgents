"""Two-model AI panel: Claude AND Grok must both say ALLOW before the committee's trade is entered.

Flow per signal:  committee (10 agents) proposes BUY/SELL  ->  Claude reviews  +  Grok reviews (in parallel)
                  ->  panel verdict:  ALLOW only if every model says ALLOW.

Modes (same as --ai-review):
  shadow : ask both, LOG both answers, never block  (run this first for a few weeks)
  veto   : enter only when BOTH say ALLOW. A VETO from either blocks.
           An ERROR (API down, timeout, refusal, bad reply) also blocks here, because "both agree" cannot be
           confirmed. This is the opposite of the single reviewer, which fails open - it is a deliberate choice
           for the panel. A trade skipped because an API was down is logged as such.

Keys (environment variables, never on the command line):
  ANTHROPIC_API_KEY  -> Claude (read by the official Anthropic SDK)
  XAI_API_KEY        -> Grok   (xAI's OpenAI-compatible endpoint, via the existing AiReviewer)

The models see exactly what the single reviewer sees (ai_review.build_context): prices, agent votes and the
planned stop/target distances. No balance, lots or account ids. Backtests of this are not meaningful (the models
have seen historical prices); only live shadow logs are fair evidence.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .ai_review import SYSTEM, AiReviewer, Review, parse_verdict

CLAUDE_MODEL = "claude-opus-5-5"
GROK_URL = "https://api.x.ai/v1"
GROK_MODEL = "grok-4"
GROK_KEY_ENV = "XAI_API_KEY"

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string", "enum": ["ALLOW", "VETO"]},
                   "reason": {"type": "string"}},
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}


class ClaudeReviewer:
    """Claude as a trade reviewer, through the official Anthropic SDK. Never raises: failures become ERROR."""

    def __init__(self, model: str = CLAUDE_MODEL, mode: str = "shadow", timeout: float = 60.0, client=None,
                 clock=time.monotonic):
        self.model, self.mode, self.timeout, self.clock = model, mode, timeout, clock
        self._client = client

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(timeout=self.timeout, max_retries=1)
        return self._client

    def review(self, ctx: dict) -> Review:
        t0 = self.clock()
        try:
            r = self._get_client().beta.messages.create(
                model=self.model,
                max_tokens=4000,
                system=SYSTEM,
                messages=[{"role": "user", "content": json.dumps(ctx, default=str)}],
                # a yes/no review: low effort; the JSON schema guarantees {"verdict","reason"}
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
                # if Claude declines for a safety reason, the API retries on its default fallback model
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if r.stop_reason == "refusal":
                cat = getattr(getattr(r, "stop_details", None), "category", None)
                verdict, reason = "ERROR", f"model declined (refusal, category {cat})"
            else:
                text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
                verdict, reason = parse_verdict(text)
            served = str(getattr(r, "model", self.model))
        except Exception as e:  # noqa: BLE001 - never stop the bot
            verdict, reason, served = "ERROR", f"{type(e).__name__}: {str(e)[:160]}", self.model
        return Review(verdict, reason, served, int((self.clock() - t0) * 1000), self.mode)


@dataclass
class PanelReview(Review):
    members: list = field(default_factory=list)   # [(name, Review), ...]

    @property
    def blocks(self) -> bool:
        """veto mode: anything other than a unanimous ALLOW blocks (VETO or ERROR). shadow: never blocks."""
        return self.mode == "veto" and self.verdict != "ALLOW"


class PanelReviewer:
    """Asks several reviewers in parallel; the trade needs ALLOW from all of them."""

    def __init__(self, reviewers: dict, mode: str = "shadow", clock=time.monotonic):
        if mode not in ("shadow", "veto"):
            raise ValueError("mode must be 'shadow' or 'veto'")
        if not reviewers:
            raise ValueError("panel needs at least one reviewer")
        self.reviewers, self.mode, self.clock = reviewers, mode, clock

    def review(self, ctx: dict) -> PanelReview:
        t0 = self.clock()
        with ThreadPoolExecutor(max_workers=len(self.reviewers)) as ex:
            futs = {name: ex.submit(rv.review, ctx) for name, rv in self.reviewers.items()}
            members = []
            for name, f in futs.items():
                try:
                    members.append((name, f.result()))
                except Exception as e:  # noqa: BLE001 - reviewers should not raise, but never trust it
                    members.append((name, Review("ERROR", f"{type(e).__name__}: {e}"[:160], name, 0, self.mode)))
        verdicts = [r.verdict for _, r in members]
        if all(v == "ALLOW" for v in verdicts):
            verdict = "ALLOW"
        elif "VETO" in verdicts:
            verdict = "VETO"
        else:
            verdict = "ERROR"
        reason = " | ".join(f"{n}: {r.verdict} ({r.reason})" for n, r in members)
        model = "+".join(r.model for _, r in members)
        return PanelReview(verdict, reason[:900], model, int((self.clock() - t0) * 1000), self.mode, members)


def build_panel(names: str, mode: str, claude_model: str = CLAUDE_MODEL, grok_model: str = GROK_MODEL,
                grok_url: str = GROK_URL) -> PanelReviewer:
    """names: comma list from --ai-panel, e.g. "claude,grok". Reviewers run in shadow internally; the panel decides."""
    reviewers = {}
    for n in [x.strip().lower() for x in names.split(",") if x.strip()]:
        if n == "claude":
            reviewers["claude"] = ClaudeReviewer(claude_model, "shadow")
        elif n == "grok":
            reviewers["grok"] = AiReviewer(grok_url, grok_model, "shadow", api_key=os.environ.get(GROK_KEY_ENV, ""))
        else:
            raise ValueError(f"unknown panel member {n!r} (use claude, grok)")
    return PanelReviewer(reviewers, mode)
