"""MetaTrader 5 DEMO-account connector (Windows + MT5 terminal required).

Safety: refuses to run unless the logged-in account is a DEMO account; dry-run by default;
one position per magic number; daily loss limit; decisions only on CLOSED candles.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import pandas as pd

from . import indicators as ind
from .committee import Committee

log = logging.getLogger("mt5bot")
TF = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


def lot_size(equity, risk_pct, stop_dist, info) -> float:
    """Lots so that hitting the stop loses ~risk_pct of equity, honouring broker volume rules."""
    loss_per_lot = stop_dist / info.trade_tick_size * info.trade_tick_value
    if loss_per_lot <= 0:
        return 0.0
    lots = equity * risk_pct / loss_per_lot
    step = info.volume_step
    lots = int(lots / step) * step  # round DOWN to step: never exceed intended risk
    lots = min(lots, info.volume_max)
    return round(lots, 8) if lots >= info.volume_min else 0.0


class Mt5Bot:
    def __init__(self, mt5, symbol="XAUUSD", timeframe="H1", committee=None, risk_pct=0.01,
                 sl_atr=1.5, tp_atr=3.0, max_daily_loss_pct=0.03, magic=26001, dry_run=True,
                 bars=500, deviation=30, news=None):
        self.mt5, self.symbol, self.dry_run = mt5, symbol, dry_run
        self.tf = getattr(mt5, f"TIMEFRAME_{timeframe}")
        self.committee = committee or Committee()
        self.risk_pct, self.sl_atr, self.tp_atr = risk_pct, sl_atr, tp_atr
        self.max_daily_loss_pct, self.magic, self.bars, self.deviation = max_daily_loss_pct, magic, bars, deviation
        self.last_bar, self.news = None, news

    # ---- setup -------------------------------------------------------------
    def connect(self):
        m = self.mt5
        if not m.initialize():
            raise RuntimeError(f"MT5 initialize failed: {m.last_error()} (is the terminal open and logged in?)")
        acc = m.account_info()
        if acc is None or acc.trade_mode != m.ACCOUNT_TRADE_MODE_DEMO:
            m.shutdown()
            raise RuntimeError("Refusing to run: logged-in account is not a DEMO account.")
        if not m.symbol_select(self.symbol, True) or m.symbol_info(self.symbol) is None:
            raise RuntimeError(f"Symbol {self.symbol!r} not found - check your broker's gold symbol name.")
        log.info("Connected to DEMO account %s, balance %.2f, dry_run=%s", acc.login, acc.balance, self.dry_run)

    # ---- data --------------------------------------------------------------
    def fetch(self) -> pd.DataFrame:
        rates = self.mt5.copy_rates_from_pos(self.symbol, self.tf, 0, self.bars)
        if rates is None or len(rates) < 250:
            raise RuntimeError("Not enough candles from MT5 (need >= 250 for EMA200 etc.)")
        df = pd.DataFrame(rates)
        df.index = pd.to_datetime(df["time"], unit="s")
        return df[["open", "high", "low", "close"]].iloc[:-1]  # drop the still-forming candle

    # ---- risk --------------------------------------------------------------
    def has_position(self) -> bool:
        pos = self.mt5.positions_get(symbol=self.symbol) or []
        return any(p.magic == self.magic for p in pos)

    def daily_loss_hit(self) -> bool:
        now = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        deals = self.mt5.history_deals_get(start, now) or []
        pnl = sum(d.profit + d.commission + d.swap for d in deals if d.magic == self.magic)
        bal = self.mt5.account_info().balance
        return pnl <= -self.max_daily_loss_pct * bal

    # ---- trading -----------------------------------------------------------
    def step(self) -> str:
        """Run one decision cycle. Returns a short status string."""
        df = self.fetch()
        bar_time = df.index[-1]
        if bar_time == self.last_bar:
            return "waiting for new candle"
        self.last_bar = bar_time
        bar_s = (df.index[-1] - df.index[-2]).total_seconds()
        # compare in broker/server time: tick.time is the server clock, same clock as candle times
        now_s = self.mt5.symbol_info_tick(self.symbol).time
        age = now_s - bar_time.timestamp()
        if age > 3 * bar_s:
            log.info("Latest closed candle %s is stale (market closed?) - not trading it", bar_time)
            return "stale candle - skipped"
        d = self.committee.decide(df)
        log.info("%s bar %s: %s (BUY %d / SELL %d / NEUTRAL %d, need %d)",
                 self.symbol, bar_time, d.action, d.buy, d.sell, d.neutral, d.required)
        if d.action == "HOLD":
            return "HOLD"
        if self.has_position():
            return f"{d.action} signal ignored: position already open"
        if self.news is not None:
            blocked, why = self.news.blocked()
            if blocked:
                return f"{d.action} signal ignored: {why}"
        if self.daily_loss_hit():
            return f"{d.action} signal ignored: daily loss limit reached"
        return self.place(d.action, ind.atr(df).iloc[-1])

    def place(self, action, atr_val) -> str:
        m, info = self.mt5, self.mt5.symbol_info(self.symbol)
        tick = m.symbol_info_tick(self.symbol)
        buy = action == "BUY"
        price = tick.ask if buy else tick.bid
        min_dist = info.trade_stops_level * info.point
        sl_d, tp_d = max(self.sl_atr * atr_val, min_dist), max(self.tp_atr * atr_val, min_dist)
        sign = 1 if buy else -1
        sl, tp = round(price - sign * sl_d, info.digits), round(price + sign * tp_d, info.digits)
        lots = lot_size(m.account_info().equity, self.risk_pct, sl_d, info)
        if lots <= 0:
            return f"{action} skipped: risk budget below broker minimum lot"
        msg = f"{action} {lots} {self.symbol} @ {price} SL {sl} TP {tp}"
        if self.dry_run:
            log.info("DRY-RUN would place: %s", msg)
            return "DRY-RUN " + msg
        fill = info.filling_mode
        filling = m.ORDER_FILLING_FOK if fill & 1 else m.ORDER_FILLING_IOC if fill & 2 else m.ORDER_FILLING_RETURN
        res = m.order_send({
            "action": m.TRADE_ACTION_DEAL, "symbol": self.symbol, "volume": lots,
            "type": m.ORDER_TYPE_BUY if buy else m.ORDER_TYPE_SELL, "price": price,
            "sl": sl, "tp": tp, "deviation": self.deviation, "magic": self.magic,
            "comment": "10-agent consensus", "type_time": m.ORDER_TIME_GTC, "type_filling": filling,
        })
        if res is None or res.retcode != m.TRADE_RETCODE_DONE:
            log.error("Order failed: %s", res)
            return f"ORDER FAILED {res}"
        log.info("Order placed: %s", msg)
        return "PLACED " + msg

    def run(self, poll_seconds=15):
        self.connect()
        try:
            while True:
                try:
                    status = self.step()
                    if status != "waiting for new candle":
                        log.info("status: %s", status)
                except Exception:
                    log.exception("cycle error (will retry)")
                time.sleep(poll_seconds)
        except KeyboardInterrupt:
            log.info("stopped")
        finally:
            self.mt5.shutdown()
