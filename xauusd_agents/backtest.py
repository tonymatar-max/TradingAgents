"""Bar-by-bar backtester. Signal at close of bar i -> entry at open of bar i+1.
SL/TP are ATR-based and checked intrabar; if both are touched in one bar, SL is assumed first (conservative).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import indicators as ind
from .committee import Committee


@dataclass
class Config:
    balance: float = 10_000.0
    risk_pct: float = 0.01       # equity risked per trade
    sl_atr: float = 1.5
    tp_atr: float = 3.0
    spread: float = 0.30         # USD per oz, paid on entry
    contract_oz: float = 100.0   # 1 standard lot = 100 oz
    max_lots: float = 5.0
    leverage: float = 0.0        # e.g. 100 = 1:100; 0 = margin not modelled
    stopout_pct: float = 0.5     # broker closes when equity <= this fraction of used margin
    fixed_lots: float | None = None  # if set, ignore risk sizing and trade this many lots every time


def position_size(equity: float, stop_dist: float, cfg: Config) -> float:
    """Lots such that hitting the stop loses ~risk_pct of equity."""
    if stop_dist <= 0:
        return 0.0
    lots = equity * cfg.risk_pct / (stop_dist * cfg.contract_oz)
    return float(min(max(round(lots, 2), 0.0), cfg.max_lots))


def run_backtest(df: pd.DataFrame, committee: Committee | None = None, cfg: Config | None = None,
                 start=None, table=None, signals=None):
    """`start`: only open trades from this date on; earlier bars are used purely to warm up indicators."""
    cfg = cfg or Config()
    committee = committee or Committee()
    sig = signals if signals is not None else committee.signals(df, table)
    if start is not None:
        sig = sig.where(df.index >= pd.Timestamp(start), 0)
    a = ind.atr(df).to_numpy()
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    s = sig.to_numpy()
    equity, trades, pos = cfg.balance, [], None
    curve = np.full(len(df), cfg.balance, dtype=float)

    for i in range(1, len(df)):
        if pos:
            d = pos["dir"]
            hit_sl = (l[i] <= pos["sl"]) if d == 1 else (h[i] >= pos["sl"])
            hit_tp = (h[i] >= pos["tp"]) if d == 1 else (l[i] <= pos["tp"])
            exit_px = pos["sl"] if hit_sl else pos["tp"] if hit_tp else None
            if exit_px is not None:
                pnl = d * (exit_px - pos["entry"]) * pos["lots"] * cfg.contract_oz
                equity = max(equity + pnl, 0.0)  # broker negative-balance protection: cannot go below 0
                trades.append({**pos, "exit": exit_px, "exit_i": i, "pnl": pnl,
                               "reason": ("STOPOUT" if pos["so"] else "SL") if hit_sl else "TP"})
                pos = None
        if pos is None and equity > 0 and s[i - 1] != 0 and not np.isnan(a[i - 1]):
            d = int(s[i - 1])
            entry = o[i] + d * cfg.spread  # spread: fill is worse in both directions
            stop = cfg.sl_atr * a[i - 1]
            lots = cfg.fixed_lots if cfg.fixed_lots else position_size(equity, stop, cfg)
            sl, so = entry - d * stop, False
            if lots > 0 and cfg.leverage:
                units = lots * cfg.contract_oz
                margin = entry * units / cfg.leverage
                if margin > equity:
                    lots = 0                      # cannot afford the margin: broker rejects the order
                else:                             # price at which the broker force-closes (stop-out)
                    p_so = entry + d * (cfg.stopout_pct * margin - equity) / units
                    if (d == 1 and p_so > sl) or (d == -1 and p_so < sl):
                        sl, so = p_so, True       # stop-out comes before our own stop loss
            if lots > 0:
                pos = {"dir": d, "entry": entry, "entry_i": i, "lots": lots, "equity_before": equity,
                       "so": so, "sl": sl, "tp": entry + d * cfg.tp_atr * a[i - 1]}
        mtm = d * (c[i] - pos["entry"]) * pos["lots"] * cfg.contract_oz if pos else 0.0
        curve[i] = equity + mtm
    trades_df = pd.DataFrame(trades)
    return trades_df, pd.Series(curve, index=df.index), summarize(trades_df, curve, cfg)


def summarize(trades: pd.DataFrame, curve: np.ndarray, cfg: Config) -> dict:
    if trades.empty:
        return {"trades": 0, "note": "no trade reached the consensus threshold"}
    w, lo = trades[trades.pnl > 0].pnl, trades[trades.pnl <= 0].pnl
    peak = np.maximum.accumulate(curve)
    return {
        "trades": len(trades),
        "win_rate_%": round(100 * len(w) / len(trades), 1),
        "profit_factor": round(w.sum() / abs(lo.sum()), 2) if len(lo) and lo.sum() else float("inf"),
        "net_pnl": round(float(trades.pnl.sum()), 2),
        "return_%": round(100 * (curve[-1] / cfg.balance - 1), 2),
        "max_drawdown_%": round(float(100 * ((peak - curve) / np.where(peak > 0, peak, 1)).max()), 2),
        "final_balance": round(float(curve[-1]), 2),
        "worst_trade": round(float(trades.pnl.min()), 2),
        "account_blown": bool(curve.min() <= 0),
        "lowest_equity": round(float(curve.min()), 2),
        "worst_loss_%_of_equity": round(float((-trades.pnl / trades.equity_before).max() * 100), 1),
        "stop_outs": int((trades.reason == "STOPOUT").sum()),
    }
