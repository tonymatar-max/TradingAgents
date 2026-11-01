"""Settings the portal may change while the bot runs. ONE place defines what is allowed and the hard limits.

Deliberately NOT editable from the web: dry-run/live switch, symbol, stop/target and the H4 filter.
`votes` = how many of the agents must agree (7-10; 8 is the default, 9 and 10 are stricter). Those were validated together by backtests; change them on the command line.
"""
from __future__ import annotations

import json
import math
import os

FILE = "settings.json"
AUDIT = "settings_audit.jsonl"
ALLOWED = ("lots", "risk", "max_risk", "paused", "votes")
LIMITS = {"lots": (0.01, 1.0), "risk": (0.001, 0.03), "max_risk": (0.0, 0.25), "votes": (7, 10)}


def _num(v, name):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{name} must be a number")
    return float(v)


def validate(raw: dict) -> tuple[dict, list[str]]:
    """Return (clean values, errors). Unknown keys are errors. lots=None means 'auto (risk-based)'."""
    clean, errs = {}, []
    if not isinstance(raw, dict):
        return {}, ["settings must be a JSON object"]
    for k in raw:
        if k not in ALLOWED:
            errs.append(f"'{k}' cannot be changed from the portal")
    for k in ALLOWED:
        if k not in raw:
            continue
        v = raw[k]
        try:
            if k == "paused":
                if not isinstance(v, bool):
                    raise ValueError("paused must be true or false")
                clean[k] = v
            elif k == "lots" and v is None:
                clean[k] = None
            elif k == "votes":
                x = _num(v, k)
                lo, hi = LIMITS[k]
                if x != int(x) or not lo <= x <= hi:
                    raise ValueError(f"votes must be a whole number between {lo} and {hi}")
                clean[k] = int(x)
            else:
                x = _num(v, k)
                lo, hi = LIMITS[k]
                if not lo <= x <= hi:
                    raise ValueError(f"{k} must be between {lo} and {hi}")
                clean[k] = round(x, 2) if k == "lots" else round(x, 4)
        except ValueError as e:
            errs.append(str(e))
    return clean, errs


def read(logdir: str) -> dict | None:
    try:
        with open(os.path.join(logdir, FILE), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write(logdir: str, clean: dict, by: str):
    import time
    p = os.path.join(logdir, FILE)
    cur = read(logdir) or {}
    new = {**{k: v for k, v in cur.items() if k in ALLOWED}, **clean, "updated": time.time(), "by": by}
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(new, f)
    os.replace(tmp, p)                      # atomic: the bot never reads a half-written file
    audit(logdir, {"action": "set", "old": {k: cur.get(k) for k in clean}, "new": clean, "by": by})
    return new


def reset(logdir: str, by: str):
    try:
        os.remove(os.path.join(logdir, FILE))
    except FileNotFoundError:
        pass
    audit(logdir, {"action": "reset", "by": by})


def audit(logdir: str, rec: dict):
    import time
    with open(os.path.join(logdir, AUDIT), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " UTC", **rec}) + "\n")
