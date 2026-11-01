from __future__ import annotations

import numpy as np
import pandas as pd

COLS = ["open", "high", "low", "close"]


def load_csv(path: str) -> pd.DataFrame:
    """CSV with columns time/date + open,high,low,close (any case)."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    tcol = next((c for c in ("time", "datetime", "date", "timestamp") if c in df.columns), None)
    if tcol:
        df.index = pd.to_datetime(df.pop(tcol))
    missing = [c for c in COLS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV missing columns: {missing}")
    return df[COLS].astype(float).sort_index()


def synthetic_xauusd(n: int = 5000, seed: int = 7, start_price: float = 2000.0, freq: str = "h") -> pd.DataFrame:
    """Synthetic regime-switching hourly gold-like data. FOR DEMO/TESTS ONLY - not real prices."""
    rng = np.random.default_rng(seed)
    drift = np.zeros(n)
    i = 0
    while i < n:
        L = int(rng.integers(100, 400))
        drift[i:i + L] = rng.choice([-1, 0, 1]) * 0.00015
        i += L
    ret = drift + rng.normal(0, 0.0012, n)
    close = start_price * np.exp(np.cumsum(ret))
    open_ = np.r_[start_price, close[:-1]]
    rng_ = np.abs(rng.normal(0, 0.0008, n)) * close
    high = np.maximum(open_, close) + rng_
    low = np.minimum(open_, close) - rng_
    idx = pd.date_range("2024-01-01", periods=n, freq=freq)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)
