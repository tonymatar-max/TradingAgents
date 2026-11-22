"""Indicator + signal logic. Pure pandas, no MT5 dependency (testable offline)."""
import pandas as pd


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, 1e-12)
    return 100 - 100 / (1 + rs)


def atr(df, n=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1).ewm(alpha=1 / n, adjust=False).mean()


def add_indicators(df, p):
    df = df.copy()
    df["ema_fast"] = ema(df["close"], p["ema_fast"])
    df["ema_slow"] = ema(df["close"], p["ema_slow"])
    df["ema_trend"] = ema(df["close"], p["ema_trend"])
    df["rsi"] = rsi(df["close"], p["rsi_period"])
    df["atr"] = atr(df, p["atr_period"])
    return df


def all_signals(df, p):
    """Indicators plus a 'signal' column (BUY/SELL/None) for every bar, used for charts."""
    d = add_indicators(df, p)
    above = d.ema_fast > d.ema_slow
    up = above & ~above.shift(fill_value=True)
    dn = ~above & above.shift(fill_value=False)
    buy = up & (d.close > d.ema_trend) & (d.rsi < p["rsi_buy_max"])
    sell = dn & (d.close < d.ema_trend) & (d.rsi > p["rsi_sell_min"])
    d["signal"] = None
    d.loc[buy, "signal"] = "BUY"
    d.loc[sell, "signal"] = "SELL"
    d.iloc[: p["ema_trend"], d.columns.get_loc("signal")] = None
    return d


def evaluate(df, p):
    """Return a signal dict for the last CLOSED bar, or None.

    BUY : fast EMA crosses above slow EMA, price above trend EMA, RSI < rsi_buy_max
    SELL: fast EMA crosses below slow EMA, price below trend EMA, RSI > rsi_sell_min
    """
    df = add_indicators(df, p)
    if len(df) < p["ema_trend"] + 2:
        return None
    prev, cur = df.iloc[-2], df.iloc[-1]
    cross_up = prev.ema_fast <= prev.ema_slow and cur.ema_fast > cur.ema_slow
    cross_dn = prev.ema_fast >= prev.ema_slow and cur.ema_fast < cur.ema_slow

    side = None
    if cross_up and cur.close > cur.ema_trend and cur.rsi < p["rsi_buy_max"]:
        side = "BUY"
    elif cross_dn and cur.close < cur.ema_trend and cur.rsi > p["rsi_sell_min"]:
        side = "SELL"
    if not side:
        return None

    a = cur.atr
    sign = 1 if side == "BUY" else -1
    return {
        "side": side,
        "price": float(cur.close),
        "sl": float(cur.close - sign * p["sl_atr"] * a),
        "tp": float(cur.close + sign * p["tp_atr"] * a),
        "rsi": round(float(cur.rsi), 1),
        "atr": float(a),
        "time": str(cur.name),
    }
