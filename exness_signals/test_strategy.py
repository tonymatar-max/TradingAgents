"""Offline test of signal logic with synthetic prices (no MT5 needed)."""
import numpy as np, pandas as pd
from strategy import evaluate

P = dict(ema_fast=20, ema_slow=50, ema_trend=200, rsi_period=14, rsi_buy_max=70,
         rsi_sell_min=30, atr_period=14, sl_atr=1.5, tp_atr=3.0)


def frame(close):
    c = np.asarray(close, dtype=float)
    return pd.DataFrame({"open": c, "high": c + 0.5, "low": c - 0.5, "close": c},
                        index=pd.date_range("2026-01-01", periods=len(c), freq="h"))


def find(close):
    sigs = [evaluate(frame(close[:i]), P) for i in range(210, len(close) + 1)]
    return [s for s in sigs if s]


# uptrend with a pullback, then resume -> expect a BUY
rng = np.random.default_rng(7)
up = np.r_[np.linspace(100, 160, 260), np.linspace(160, 150, 40), np.linspace(150, 175, 60)] + rng.normal(0, 1.2, 360)
b = find(up)
assert any(s["side"] == "BUY" for s in b), "expected BUY"
s = [x for x in b if x["side"] == "BUY"][0]
assert s["sl"] < s["price"] < s["tp"]

# mirror -> expect a SELL
dn = 300 - up
sl = find(dn)
assert any(s["side"] == "SELL" for s in sl), "expected SELL"
s = [x for x in sl if x["side"] == "SELL"][0]
assert s["tp"] < s["price"] < s["sl"]

# flat noise-free -> no signals
assert not find(np.full(300, 100.0))
print("all strategy tests passed:", b[0], sl[0], sep="\n")


