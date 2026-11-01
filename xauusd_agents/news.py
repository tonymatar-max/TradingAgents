"""High-impact USD news blackout filter (live bot only; NOT part of the 90% vote).

Source: free weekly calendar JSON (unofficial, community feed; rate-limited, so we cache on disk).
FAILS CLOSED: if no usable event data is available the filter blocks trading rather than guessing.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def parse_events(raw: list, countries=("USD",), impacts=("High",)) -> list[dict]:
    """Defensive parse: skips malformed rows. Returns [{'time': aware-UTC datetime, 'title': str}]."""
    out = []
    for r in raw:
        try:
            if r.get("country") not in countries or r.get("impact") not in impacts:
                continue
            t = datetime.fromisoformat(str(r["date"]).replace("Z", "+00:00"))
            if t.tzinfo is None:
                continue  # can't trust a time without offset
            out.append({"time": t.astimezone(timezone.utc), "title": str(r.get("title", "event"))})
        except (KeyError, ValueError, AttributeError, TypeError):
            continue
    return sorted(out, key=lambda e: e["time"])


class NewsFilter:
    def __init__(self, before_min=30, after_min=30, cache_path="news_cache.json", refresh_min=60,
                 url=FEED, fetcher=None, clock=None):
        self.before, self.after = timedelta(minutes=before_min), timedelta(minutes=after_min)
        self.cache_path, self.refresh_s, self.url = cache_path, refresh_min * 60, url
        self.fetcher = fetcher or self._http_get
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.events: list[dict] = []
        self.loaded_at: datetime | None = None
        self._last_try = 0.0

    def _http_get(self):
        req = urllib.request.Request(self.url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())

    def refresh(self, force=False):
        if not force and time.monotonic() - self._last_try < self.refresh_s and self.loaded_at:
            return
        self._last_try = time.monotonic()
        try:
            raw = self.fetcher()
            events = parse_events(raw)
            if not isinstance(raw, list) or not raw:
                raise ValueError("empty/unexpected feed")
            self.events, self.loaded_at = events, self.clock()
            with open(self.cache_path, "w") as f:
                json.dump(raw, f)
            return
        except Exception as e:  # network, rate limit (429), format change...
            err = e
        if os.path.exists(self.cache_path):  # fall back to cache if it is fresh enough
            age = self.clock() - datetime.fromtimestamp(os.path.getmtime(self.cache_path), timezone.utc)
            if age < timedelta(days=7):
                with open(self.cache_path) as f:
                    self.events, self.loaded_at = parse_events(json.load(f)), self.clock() - age
                return
        self.events, self.loaded_at = [], None
        self.last_error = err

    def blocked(self) -> tuple[bool, str]:
        """(True, reason) if new trades must not be opened right now."""
        self.refresh()
        now = self.clock()
        if self.loaded_at is None:
            return True, "news data unavailable - failing closed"
        if now - self.loaded_at > timedelta(days=7):
            return True, "news data older than 7 days - failing closed"
        for e in self.events:
            if e["time"] - self.before <= now <= e["time"] + self.after:
                return True, f"news blackout: {e['title']} at {e['time']:%a %H:%M} UTC"
        return False, ""

    def upcoming(self, n=8):
        self.refresh(force=True)
        now = self.clock()
        return [e for e in self.events if e["time"] >= now - self.after][:n]


def brief(nf: "NewsFilter", hours: int = 36) -> str:
    """Human-readable daily briefing of upcoming high-impact USD events."""
    nf.refresh(force=True)
    now = nf.clock()
    head = f"News brief {now:%Y-%m-%d %H:%M} UTC - high-impact USD events, next {hours}h"
    if nf.loaded_at is None:
        return head + "\nNEWS DATA UNAVAILABLE - the bot will not open trades until it loads (fails closed)."
    lim = now + timedelta(hours=hours)
    ev = [e for e in nf.events if now - nf.after <= e["time"] <= lim]
    lines = [head, f"Bot blackout: {int(nf.before.total_seconds() // 60)} min before / "
                   f"{int(nf.after.total_seconds() // 60)} min after each event (no NEW trades).", ""]
    if not ev:
        lines.append("No high-impact USD events in this window.")
    for e in ev:
        t = e["time"]
        lines.append(f"  {t:%a %d %b %H:%M} UTC  (your PC time {t.astimezone():%H:%M})  {e['title']}")
    blocked, why = nf.blocked()
    lines += ["", f"Trading blocked right now: {'YES - ' + why if blocked else 'no'}"]
    return "\n".join(lines)
