"""MetaTrader 5 DEMO-account connector (Windows + MT5 terminal required).

Safety: refuses to run unless the logged-in account is a DEMO account; dry-run by default;
one position per magic number; daily loss limit; decisions only on CLOSED candles.
"""
from __future__ import annotations

import logging
import os
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
                 bars=500, deviation=30, news=None, htf_min=0, htf_tf="H4", htf_agents=None, fixed_lots=None, max_risk_pct=0.10,
                 server_utc_offset_hours=None, clock=time.time, logdir=None, describe=None):
        self.mt5, self.symbol, self.dry_run = mt5, symbol, dry_run
        self.tf = getattr(mt5, f"TIMEFRAME_{timeframe}")
        self.committee = committee or Committee()
        self.risk_pct, self.sl_atr, self.tp_atr = risk_pct, sl_atr, tp_atr
        self.max_daily_loss_pct, self.magic, self.bars, self.deviation = max_daily_loss_pct, magic, bars, deviation
        self.last_bar, self.news = None, news
        self.htf_min, self.htf_tf, self.htf_agents = htf_min, htf_tf, htf_agents
        self.fixed_lots, self.max_risk_pct = fixed_lots, max_risk_pct
        self.describe = describe or {}
        self.events = self.paper = None
        if logdir:
            from .events import EventLog
            self.events = EventLog(logdir)
            self._logdir = logdir
        self._fresh = None
        self.paused = False
        self._startup = {"fixed_lots": fixed_lots, "risk_pct": risk_pct, "max_risk_pct": max_risk_pct, "paused": False,
                         "votes": self.committee.required}
        self._ov_mtime = "unset"
        self.clock = clock                      # local UTC epoch seconds (injectable for tests)
        self.fixed_offset = server_utc_offset_hours is not None
        self.server_offset = None if server_utc_offset_hours is None else server_utc_offset_hours * 3600

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
        if self.events and self.dry_run:
            from .events import PaperBook
            self.paper = PaperBook(self._logdir, start_balance=acc.balance)
        self._status("connected")

    def _learn_server_offset(self):
        """Broker (server) clock minus UTC. Only trusted from a FRESH tick: a live tick differs from our UTC clock
        by a whole number of half-hours (+- a few seconds); a frozen weekend tick does not."""
        tick = self.mt5.symbol_info_tick(self.symbol)
        off = tick.time - self.clock()
        snapped = round(off / 1800) * 1800
        if abs(off - snapped) < 30 and abs(snapped) <= 14 * 3600:
            self.server_offset = snapped

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
        if self.paper is not None and self.paper.open:
            return True
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
    def _apply_overrides(self):
        """Pick up portal edits (logs/settings.json): startup values + validated overrides. Never trust the file."""
        if not self.events:
            return
        from . import live_settings as ls
        path = os.path.join(self._logdir, ls.FILE)
        try:
            mt = os.path.getmtime(path) if os.path.exists(path) else None
        except OSError:
            return
        if mt == self._ov_mtime:
            return
        self._ov_mtime = mt
        vals = dict(self._startup)
        if mt is not None:
            clean, errs = ls.validate({k: v for k, v in (ls.read(self._logdir) or {}).items() if k in ls.ALLOWED})
            for e in errs:
                log.warning("settings.json ignored a bad value: %s", e)
            for k, attr in (("lots", "fixed_lots"), ("risk", "risk_pct"), ("max_risk", "max_risk_pct"),
                            ("paused", "paused"), ("votes", "votes")):
                if k in clean:
                    vals[attr] = clean[k]
        before = (self.fixed_lots, self.risk_pct, self.max_risk_pct, self.paused, self.committee.required)
        self.fixed_lots, self.risk_pct, self.max_risk_pct, self.paused = (
            vals["fixed_lots"], vals["risk_pct"], vals["max_risk_pct"], vals["paused"])
        n = len(self.committee.agents)
        self.committee.required = max(1, min(int(vals["votes"]), n))     # how many agents must agree
        self.committee.threshold = self.committee.required / n
        after = (self.fixed_lots, self.risk_pct, self.max_risk_pct, self.paused, self.committee.required)
        if before != after:
            log.info("SETTINGS CHANGED from portal: lots=%s risk=%s max_risk=%s paused=%s votes=%s", *after)
            self.events.append(type="settings_changed", lots=self.fixed_lots, risk=self.risk_pct,
                               max_risk=self.max_risk_pct, paused=self.paused, votes=self.committee.required)

    def _status(self, last_status):
        if not self.events:
            return
        try:
            acc = self.mt5.account_info()
            info = self.mt5.symbol_info(self.symbol)
            self.events.write_status(
                mode="DRY RUN" if self.dry_run else "LIVE ORDERS (demo)", symbol=self.symbol,
                last_bar=str(self.last_bar), last_status=last_status,
                settings={**self.describe, "lots": self.fixed_lots, "risk": self.risk_pct,
                          "max_risk": self.max_risk_pct, "paused": self.paused,
                          "votes": self.committee.required, "agents": len(self.committee.agents)},
                usd_per_dollar_per_lot=info.trade_tick_value / info.trade_tick_size,
                server_offset_h=None if self.server_offset is None else self.server_offset / 3600,
                balance=getattr(acc, "balance", None), equity=getattr(acc, "equity", None),
                paper_open=self.paper.open if self.paper else None,
                paper_start_balance=self.paper.state.get("start_balance") if self.paper else None)
        except Exception:
            log.exception("could not write status")

    def step(self) -> str:
        """Run one decision cycle (records events for the portal). Returns a short status string."""
        self._fresh = None
        self._apply_overrides()
        status = self._step()
        if self.events and status != "waiting for new candle":
            if self._fresh:
                bar, d = self._fresh
                self.events.append(type="candle", bar=str(bar), action=d.action, buy=d.buy, sell=d.sell,
                                   neutral=d.neutral, need=d.required, votes=d.votes, status=status)
            else:
                self.events.append(type="skip", bar=str(self.last_bar), status=status)
        self._status(status)
        return status

    def _step(self) -> str:
        """Run one decision cycle. Returns a short status string."""
        df = self.fetch()
        bar_time = df.index[-1]
        if bar_time == self.last_bar:
            return "waiting for new candle"
        self.last_bar = bar_time
        bar_s = (df.index[-1] - df.index[-2]).total_seconds()
        if not self.fixed_offset:
            self._learn_server_offset()
        if self.server_offset is None:
            log.info("Broker clock offset unknown (market closed?) - not trading. Use --server-utc-offset to set it.")
            return "stale/unknown broker clock - skipped"
        age = (self.clock() + self.server_offset) - bar_time.timestamp()
        if age > 3 * bar_s:
            log.info("Latest closed candle %s is stale (market closed?) - not trading it", bar_time)
            return "stale candle - skipped"
        if self.paper is not None:
            for c in self.paper.update(df, self.mt5.symbol_info(self.symbol)):
                self.events.append(type="paper_close", **c)
                log.info("PAPER trade closed: %s %s %.2f -> %.2f (%s) pnl $%.2f", c["dir"], c["lots"], c["entry"],
                         c["exit"], c["reason"], c["pnl"])
        d = self.committee.decide(df)
        self._fresh = (bar_time, d)
        log.info("%s bar %s: %s (BUY %d / SELL %d / NEUTRAL %d, need %d)",
                 self.symbol, bar_time, d.action, d.buy, d.sell, d.neutral, d.required)
        if d.action == "HOLD":
            return "HOLD"
        if self.paused:
            return f"{d.action} signal ignored: paused from the portal"
        if self.has_position():
            return f"{d.action} signal ignored: position already open"
        if self.htf_min:
            from .htf import last_closed_direction
            rates = self.mt5.copy_rates_from_pos(self.symbol, getattr(self.mt5, f"TIMEFRAME_{self.htf_tf}"), 0, self.bars)
            if rates is None or len(rates) < 250:
                return f"{d.action} signal ignored: {self.htf_tf} candles unavailable"
            h = pd.DataFrame(rates)
            h.index = pd.to_datetime(h["time"], unit="s")
            h = h[["open", "high", "low", "close"]].iloc[:-1]  # drop forming candle
            want = 1 if d.action == "BUY" else -1
            got = last_closed_direction(h, self.htf_min, self.htf_agents)
            if got != want:
                return f"{d.action} signal ignored: {self.htf_tf} does not confirm ({got:+d})"
            log.info("%s confirms %s", self.htf_tf, d.action)
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
        equity = m.account_info().equity
        if self.fixed_lots:
            step = info.volume_step
            lots = round(min(info.volume_max, max(info.volume_min, round(self.fixed_lots / step) * step)), 8)
        else:
            lots = lot_size(equity, self.risk_pct, sl_d, info)
        if lots <= 0:
            return f"{action} skipped: risk budget below broker minimum lot"
        risk_money = lots * sl_d / info.trade_tick_size * info.trade_tick_value
        risk_pct = risk_money / equity
        if self.fixed_lots and self.max_risk_pct and risk_pct > self.max_risk_pct:
            log.info("%s skipped: stop %.2f away would risk $%.0f = %.1f%% of equity (cap %.0f%%)",
                     action, sl_d, risk_money, 100 * risk_pct, 100 * self.max_risk_pct)
            return f"{action} skipped: risk {risk_pct:.1%} of equity exceeds the {self.max_risk_pct:.0%} cap"
        msg = (f"{action} {lots} {self.symbol} @ {price} SL {sl} TP {tp}  "
               f"[risk ${risk_money:.0f} = {risk_pct:.1%} of equity]")
        sig = {"dir": action, "lots": lots, "entry": price, "sl": sl, "tp": tp, "risk_usd": round(risk_money, 2),
               "risk_pct": round(100 * risk_pct, 2), "opened_bar": str(self.last_bar)}
        if self.dry_run:
            log.info("DRY-RUN would place: %s", msg)
            if self.paper is not None:
                self.paper.open_position(sig, str(self.last_bar))
                self.events.append(type="signal", mode="dry", **sig)
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
        if self.events:
            self.events.append(type="signal", mode="live", **sig)
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
