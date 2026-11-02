"""Ten independent strategy agents. Each returns a Series of votes: +1 BUY, -1 SELL, 0 NEUTRAL.

Votes are *states* ("I currently favour long"), not one-bar events, so a committee
can measure agreement. All agents are causal (no look-ahead).
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod

import numpy as np
import pandas as pd

from . import indicators as ind


def _vote(long_cond: pd.Series, short_cond: pd.Series) -> pd.Series:
    return pd.Series(np.where(long_cond, 1, np.where(short_cond, -1, 0)), index=long_cond.index)


class Agent(ABC):
    name: str = "agent"
    style: str = ""

    def __init__(self, scale: float = 1.0):
        self.s = scale                      # multiplies every period (2.0 on M30 = the same time spans as on H1)

    def p(self, v: int) -> int:
        return max(1, int(math.floor(v * self.s + 0.5)))   # round half up (the EA uses the same rule)

    @abstractmethod
    def votes(self, df: pd.DataFrame) -> pd.Series: ...


class EmaTrend(Agent):
    name, style = "1_EMA_Trend", "trend: EMA20>EMA50 and price vs EMA200"

    def votes(self, df):
        c = df["close"]
        e20, e50, e200 = ind.ema(c, self.p(20)), ind.ema(c, self.p(50)), ind.ema(c, self.p(200))
        return _vote((e20 > e50) & (c > e200), (e20 < e50) & (c < e200))


class MacdMomentum(Agent):
    name, style = "2_MACD_Momentum", "momentum: MACD line vs signal, and sign of histogram"

    def votes(self, df):
        line, sig, hist = ind.macd(df["close"], self.p(12), self.p(26), self.p(9))
        return _vote((line > sig) & (hist > 0), (line < sig) & (hist < 0))


class RsiMomentumRegime(Agent):
    name, style = "3_RSI_Regime", "regime: RSI>55 bullish / <45 bearish (not overbought/oversold fade)"

    def votes(self, df):
        r = ind.rsi(df["close"], self.p(14))
        return _vote((r > 55) & (r < 80), (r < 45) & (r > 20))


class BollingerPosition(Agent):
    name, style = "4_Bollinger_Walk", "volatility: price above/below Bollinger midline, bands expanding"

    def votes(self, df):
        lo, mid, hi = ind.bollinger(df["close"], self.p(20))
        width = hi - lo
        expanding = width > width.shift(self.p(5))
        c = df["close"]
        return _vote((c > mid) & expanding, (c < mid) & expanding)


class DonchianBreakout(Agent):
    name, style = "5_Donchian_Breakout", "breakout: stays long after 20-bar high break until 10-bar low"

    def votes(self, df):
        n20, n10 = self.p(20), self.p(10)
        hi20, lo20 = df["high"].rolling(n20).max().shift(), df["low"].rolling(n20).min().shift()
        hi10, lo10 = df["high"].rolling(n10).max().shift(), df["low"].rolling(n10).min().shift()
        c = df["close"].to_numpy()
        h20, l20, h10, l10 = (x.to_numpy() for x in (hi20, lo20, hi10, lo10))
        state = np.zeros(len(df), dtype=int)
        for i in range(len(df)):
            prev = state[i - 1] if i else 0
            if c[i] > h20[i]:            # NaN comparisons are False
                state[i] = 1
            elif c[i] < l20[i]:
                state[i] = -1
            elif prev == 1 and not c[i] < l10[i]:
                state[i] = 1
            elif prev == -1 and not c[i] > h10[i]:
                state[i] = -1
        return pd.Series(state, index=df.index)


class StochasticBias(Agent):
    name, style = "6_Stochastic_Bias", "oscillator: %K above %D with %K>50 (or below/below)"

    def votes(self, df):
        k, d = ind.stochastic(df, self.p(14), self.p(3))
        return _vote((k > d) & (k > 50), (k < d) & (k < 50))


class AdxDirectional(Agent):
    name, style = "7_ADX_DI", "trend strength: ADX>20 with +DI/-DI dominance"

    def votes(self, df):
        a, pdi, mdi = ind.adx(df, self.p(14))
        return _vote((a > 20) & (pdi > mdi), (a > 20) & (mdi > pdi))


class SupertrendAgent(Agent):
    name, style = "8_Supertrend", "trend: ATR(10)x3 Supertrend direction"

    def votes(self, df):
        d = ind.supertrend_dir(df, self.p(10), 3.0)
        return _vote(d > 0, d < 0)


class TimeSeriesMomentum(Agent):
    name, style = "9_TS_Momentum", "momentum: 60-bar return agrees with 20-bar return, vs ATR noise"

    def votes(self, df):
        c = df["close"]
        r60, r20 = c.pct_change(self.p(60)), c.pct_change(self.p(20))
        noise = ind.atr(df, self.p(14)) / c * 3
        return _vote((r60 > noise) & (r20 > 0), (r60 < -noise) & (r20 < 0))


class RegressionChannel(Agent):
    name, style = "10_Linreg_Slope", "statistical: 30-bar regression slope with R^2>0.5"

    def votes(self, df):
        slope, r2 = ind.linreg_slope_r2(df["close"], self.p(30))
        return _vote((slope > 0) & (r2 > 0.5), (slope < 0) & (r2 > 0.5))


class InsideBarBreakout(Agent):
    """Inside bar (bar i-1 fully inside bar i-2) followed by a close beyond the mother bar,
    in the direction of EMA50. The state lasts `hold` bars or until price crosses EMA50 against it."""
    name, style = "11_Inside_Bar", "pattern: inside-bar breakout with EMA50 trend filter"

    def __init__(self, hold: int = 12):
        self.hold = hold

    def votes(self, df):
        h, l, c = (df[k].to_numpy() for k in ("high", "low", "close"))
        e50 = ind.ema(df["close"], 50).to_numpy()
        n, st, age = len(df), np.zeros(len(df), dtype=int), 0
        for i in range(2, n):
            prev = st[i - 1]
            age += 1
            inside = h[i - 1] <= h[i - 2] and l[i - 1] >= l[i - 2]
            if inside and c[i] > h[i - 2] and c[i] > e50[i]:
                st[i], age = 1, 0
            elif inside and c[i] < l[i - 2] and c[i] < e50[i]:
                st[i], age = -1, 0
            elif prev == 1 and age <= self.hold and c[i] > e50[i]:
                st[i] = 1
            elif prev == -1 and age <= self.hold and c[i] < e50[i]:
                st[i] = -1
        return pd.Series(st, index=df.index)


class InitialBalanceBreakout(Agent):
    """Initial Balance = high/low of the first `ib_bars` bars of each session (session starts at
    `start_hour` in the data's clock; for broker server time use your London/NY open hour).
    Votes +1 above the IB, -1 below, 0 inside it or while the IB is still forming."""
    name, style = "12_Initial_Balance", "session: break of first-hours range"

    def __init__(self, ib_bars: int = 2, start_hour: int = 0):
        self.ib_bars, self.start_hour = ib_bars, start_hour

    def votes(self, df):
        sess = (df.index - pd.Timedelta(hours=self.start_hour)).normalize()
        g = pd.Series(sess, index=df.index)
        n_in = df.groupby(g.values).cumcount()
        first = n_in < self.ib_bars
        ib_hi = df["high"].where(first).groupby(g.values).transform("max")
        ib_lo = df["low"].where(first).groupby(g.values).transform("min")
        formed = n_in >= self.ib_bars   # IB only used after its last bar has closed
        c = df["close"]
        return _vote(formed & (c > ib_hi), formed & (c < ib_lo))


def extended_agents(ib_bars: int = 2, ib_start_hour: int = 0) -> list[Agent]:
    return default_agents() + [InsideBarBreakout(), InitialBalanceBreakout(ib_bars, ib_start_hour)]


def default_agents(scale: float = 1.0) -> list[Agent]:
    """The 10 agents. scale multiplies every indicator period (use 2.0 on M30 to keep the same time spans as H1)."""
    return [c(scale) for c in (EmaTrend, MacdMomentum, RsiMomentumRegime, BollingerPosition, DonchianBreakout,
                               StochasticBias, AdxDirectional, SupertrendAgent, TimeSeriesMomentum, RegressionChannel)]
