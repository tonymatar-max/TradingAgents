"""Ten independent strategy agents. Each returns a Series of votes: +1 BUY, -1 SELL, 0 NEUTRAL.

Votes are *states* ("I currently favour long"), not one-bar events, so a committee
can measure agreement. All agents are causal (no look-ahead).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd

from . import indicators as ind


def _vote(long_cond: pd.Series, short_cond: pd.Series) -> pd.Series:
    return pd.Series(np.where(long_cond, 1, np.where(short_cond, -1, 0)), index=long_cond.index)


class Agent(ABC):
    name: str = "agent"
    style: str = ""

    @abstractmethod
    def votes(self, df: pd.DataFrame) -> pd.Series: ...


class EmaTrend(Agent):
    name, style = "1_EMA_Trend", "trend: EMA20>EMA50 and price vs EMA200"

    def votes(self, df):
        c = df["close"]
        e20, e50, e200 = ind.ema(c, 20), ind.ema(c, 50), ind.ema(c, 200)
        return _vote((e20 > e50) & (c > e200), (e20 < e50) & (c < e200))


class MacdMomentum(Agent):
    name, style = "2_MACD_Momentum", "momentum: MACD line vs signal, and sign of histogram"

    def votes(self, df):
        line, sig, hist = ind.macd(df["close"])
        return _vote((line > sig) & (hist > 0), (line < sig) & (hist < 0))


class RsiMomentumRegime(Agent):
    name, style = "3_RSI_Regime", "regime: RSI>55 bullish / <45 bearish (not overbought/oversold fade)"

    def votes(self, df):
        r = ind.rsi(df["close"], 14)
        return _vote((r > 55) & (r < 80), (r < 45) & (r > 20))


class BollingerPosition(Agent):
    name, style = "4_Bollinger_Walk", "volatility: price above/below Bollinger midline, bands expanding"

    def votes(self, df):
        lo, mid, hi = ind.bollinger(df["close"])
        width = hi - lo
        expanding = width > width.shift(5)
        c = df["close"]
        return _vote((c > mid) & expanding, (c < mid) & expanding)


class DonchianBreakout(Agent):
    name, style = "5_Donchian_Breakout", "breakout: stays long after 20-bar high break until 10-bar low"

    def votes(self, df):
        hi20, lo20 = df["high"].rolling(20).max().shift(), df["low"].rolling(20).min().shift()
        hi10, lo10 = df["high"].rolling(10).max().shift(), df["low"].rolling(10).min().shift()
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
        k, d = ind.stochastic(df)
        return _vote((k > d) & (k > 50), (k < d) & (k < 50))


class AdxDirectional(Agent):
    name, style = "7_ADX_DI", "trend strength: ADX>20 with +DI/-DI dominance"

    def votes(self, df):
        a, pdi, mdi = ind.adx(df)
        return _vote((a > 20) & (pdi > mdi), (a > 20) & (mdi > pdi))


class SupertrendAgent(Agent):
    name, style = "8_Supertrend", "trend: ATR(10)x3 Supertrend direction"

    def votes(self, df):
        d = ind.supertrend_dir(df)
        return _vote(d > 0, d < 0)


class TimeSeriesMomentum(Agent):
    name, style = "9_TS_Momentum", "momentum: 60-bar return agrees with 20-bar return, vs ATR noise"

    def votes(self, df):
        c = df["close"]
        r60, r20 = c.pct_change(60), c.pct_change(20)
        noise = ind.atr(df) / c * 3
        return _vote((r60 > noise) & (r20 > 0), (r60 < -noise) & (r20 < 0))


class RegressionChannel(Agent):
    name, style = "10_Linreg_Slope", "statistical: 30-bar regression slope with R^2>0.5"

    def votes(self, df):
        slope, r2 = ind.linreg_slope_r2(df["close"], 30)
        return _vote((slope > 0) & (r2 > 0.5), (slope < 0) & (r2 > 0.5))


def default_agents() -> list[Agent]:
    return [EmaTrend(), MacdMomentum(), RsiMomentumRegime(), BollingerPosition(),
            DonchianBreakout(), StochasticBias(), AdxDirectional(), SupertrendAgent(),
            TimeSeriesMomentum(), RegressionChannel()]
