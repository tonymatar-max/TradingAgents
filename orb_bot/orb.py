"""Opening-range breakout (ORB) rules, as described by day-trader-skill, rewritten for Exness MT5 data.

Rules (long only, like the original's default):
  - Opening range (OR) = high/low of the first 15 minutes after the 9:30 New York open (three M5 bars).
  - Entry: first M5 bar that CLOSES above the OR high, between 9:45 and ENTRY_CUTOFF -> buy at that close.
  - Stop = OR midpoint. Target = entry + 1R (R = entry - stop).
  - Flat at 15:55 New York time. One trade per symbol per day.
  - Size: shares = max_loss / R, rounded down to the broker's step (1 share = 0.01 lot on Exness stock CFDs).
"""
from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo("America/New_York")
OR_START, OR_END = "09:30", "09:45"     # range = bars starting 09:30, 09:35, 09:40
ENTRY_CUTOFF = "12:00"
FLAT_AT = "15:55"


@dataclass
class Params:
    target_r: float = 1.0
    allow_short: bool = False
    entry_cutoff: str = ENTRY_CUTOFF
    or_start: str = OR_START
    or_end: str = OR_END
    flat_at: str = FLAT_AT


def to_ny(df_utc: pd.DataFrame, tz=NY) -> pd.DataFrame:
    d = df_utc.copy()
    d.index = d.index.tz_localize("UTC").tz_convert(tz)
    return d


def session_trade(day: pd.DataFrame, p: Params, cost: float = 0.0) -> dict | None:
    """Simulate one New York session (M5 bars indexed in NY time). Returns a trade dict or None.
    `cost` = round-trip cost per unit in price (spread + commission), charged on entry."""
    t = day.index.strftime("%H:%M")
    rng = day[(t >= p.or_start) & (t < p.or_end)]
    if len(rng) < 3:
        return None
    hi, lo = rng.high.max(), rng.low.min()
    mid = (hi + lo) / 2
    after = day[(t >= p.or_end) & (t <= p.flat_at)]
    ta = after.index.strftime("%H:%M")
    for k in range(len(after)):
        if ta[k] >= p.entry_cutoff:
            return None
        c = after.close.iloc[k]
        side = 1 if c > hi else (-1 if (p.allow_short and c < lo) else 0)
        if not side:
            continue
        entry = c + side * cost
        stop = mid
        risk = side * (entry - stop)
        if risk <= 0:
            return None
        target = entry + side * p.target_r * risk
        rest = after.iloc[k + 1:]
        for j in range(len(rest)):
            b = rest.iloc[j]
            hit_sl = b.low <= stop if side == 1 else b.high >= stop
            hit_tp = b.high >= target if side == 1 else b.low <= target
            if hit_sl:   # stop first if both in one bar (conservative)
                return _t(after.index[k], rest.index[j], side, entry, stop, target, stop, "STOP", risk)
            if hit_tp:
                return _t(after.index[k], rest.index[j], side, entry, stop, target, target, "TARGET", risk)
        last = rest.iloc[-1] if len(rest) else after.iloc[k]
        return _t(after.index[k], last.name, side, entry, stop, target, last.close, "CLOSE", risk)
    return None


def _t(t_in, t_out, side, entry, stop, target, exit_px, reason, risk):
    return {"entry_time": t_in, "exit_time": t_out, "side": "BUY" if side == 1 else "SELL",
            "entry": entry, "stop": stop, "target": target, "exit": exit_px, "reason": reason,
            "risk_per_unit": risk, "R": side * (exit_px - entry) / risk}


def live_state(day: pd.DataFrame, p: Params):
    """For the live bot: given today's CLOSED M5 bars (NY time), return a signal dict for the latest bar or None."""
    t = day.index.strftime("%H:%M")
    rng = day[(t >= OR_START) & (t < OR_END)]
    if len(rng) < 3:
        return None
    hi, lo = rng.high.max(), rng.low.min()
    after = day[(t >= OR_END) & (t < p.entry_cutoff)]
    if after.empty:
        return None
    # signal only if the LATEST bar is the FIRST close beyond the range today
    up = after.close > hi
    dn = (after.close < lo) if p.allow_short else after.close < -1e18
    first = (up | dn)
    if not first.any() or first.idxmax() != after.index[-1]:
        return None
    side = 1 if up.iloc[-1] else -1
    return {"side": side, "range_high": hi, "range_low": lo, "stop": (hi + lo) / 2,
            "close": after.close.iloc[-1], "bar": after.index[-1]}
