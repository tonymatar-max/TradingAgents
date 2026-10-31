"""Causal technical indicators (value at bar i uses only bars <= i)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift()
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False).mean()


def macd(close: pd.Series, fast=12, slow=26, signal=9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bollinger(close: pd.Series, n=20, k=2.0):
    mid = sma(close, n)
    sd = close.rolling(n).std(ddof=0)
    return mid - k * sd, mid, mid + k * sd


def stochastic(df: pd.DataFrame, n=14, d=3):
    lo, hi = df["low"].rolling(n).min(), df["high"].rolling(n).max()
    k = 100 * (df["close"] - lo) / (hi - lo).replace(0, np.nan)
    return k, k.rolling(d).mean()


def adx(df: pd.DataFrame, n: int = 14):
    up, dn = df["high"].diff(), -df["low"].diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = atr(df, n)
    pdi = 100 * plus.ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * minus.ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean(), pdi, mdi


def supertrend_dir(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> pd.Series:
    """+1 / -1 supertrend direction."""
    a = atr(df, n).to_numpy()
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    close = df["close"].to_numpy()
    ub, lb = hl2 + mult * a, hl2 - mult * a
    fub, flb = ub.copy(), lb.copy()
    d = np.zeros(len(df))
    for i in range(1, len(df)):
        if np.isnan(a[i]):
            continue
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or close[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or close[i - 1] < flb[i - 1]) else flb[i - 1]
        if d[i - 1] == 0:
            d[i] = 1 if close[i] >= hl2[i] else -1
        elif d[i - 1] == 1:
            d[i] = -1 if close[i] < flb[i] else 1
        else:
            d[i] = 1 if close[i] > fub[i] else -1
    return pd.Series(d, index=df.index)


def linreg_slope_r2(s: pd.Series, n: int = 30):
    x = np.arange(n, dtype=float)
    xm, xv = x.mean(), ((x - x.mean()) ** 2).sum()

    def _slope(w):
        return ((x - xm) * (w - w.mean())).sum() / xv

    def _r2(w):
        sl = _slope(w)
        fit = w.mean() + sl * (x - xm)
        ss_res, ss_tot = ((w - fit) ** 2).sum(), ((w - w.mean()) ** 2).sum()
        return 1 - ss_res / ss_tot if ss_tot else 0.0

    return s.rolling(n).apply(_slope, raw=True), s.rolling(n).apply(_r2, raw=True)
