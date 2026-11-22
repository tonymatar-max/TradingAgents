"""Exness Signal Bot dashboard.  Run:  streamlit run app.py

Pages: Overview (account, positions, P&L) | Scanner (signals across watchlist)
       Chart (price + EMAs + RSI + signal markers) | History | Settings
If MT5 is not running, the app falls back to DEMO DATA so you can still explore it.
"""
import datetime as dt
import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml
from plotly.subplots import make_subplots

from strategy import all_signals, evaluate

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "config.yaml")

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

st.set_page_config(page_title="Exness Signal Bot", page_icon="📈", layout="wide")


def load_cfg():
    with open(CFG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


cfg = load_cfg()


# ---------------- data layer ----------------
@st.cache_resource
def mt5_connect():
    return bool(mt5 and mt5.initialize())


LIVE = mt5_connect()
TFMAP = {"M15": "TIMEFRAME_M15", "M30": "TIMEFRAME_M30", "H1": "TIMEFRAME_H1",
         "H4": "TIMEFRAME_H4", "D1": "TIMEFRAME_D1"}
FREQ = {"M15": "15min", "M30": "30min", "H1": "h", "H4": "4h", "D1": "D"}


@st.cache_data(ttl=30)
def get_bars(symbol, tf, n):
    if LIVE:
        mt5.symbol_select(symbol, True)
        r = mt5.copy_rates_from_pos(symbol, getattr(mt5, TFMAP[tf]), 1, n)
        if r is None or len(r) == 0:
            return None
        df = pd.DataFrame(r)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df.set_index("time")[["open", "high", "low", "close"]]
    # demo data: deterministic random walk per symbol
    rng = np.random.default_rng(abs(hash(symbol)) % 2**32)
    base = 100 + rng.random() * 100
    c = base * np.exp(np.cumsum(rng.normal(0.0002, 0.006, n)))
    o = np.r_[c[0], c[:-1]]
    w = np.abs(rng.normal(0, 0.003, n)) * c
    idx = pd.date_range(end=pd.Timestamp.now().floor(FREQ[tf]), periods=n, freq=FREQ[tf])
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + w, "low": np.minimum(o, c) - w,
                         "close": c}, index=idx)


def account():
    if LIVE and (a := mt5.account_info()):
        return {"login": a.login, "server": a.server, "balance": a.balance, "equity": a.equity,
                "margin_free": a.margin_free, "currency": a.currency,
                "mode": "DEMO" if a.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "REAL"}
    return {"login": "-", "server": "not connected", "balance": 10000.0, "equity": 10000.0,
            "margin_free": 10000.0, "currency": "USD", "mode": "DEMO DATA"}


def positions():
    if not LIVE:
        return pd.DataFrame()
    ps = mt5.positions_get() or []
    if not ps:
        return pd.DataFrame()
    df = pd.DataFrame([p._asdict() for p in ps])
    df["type"] = df["type"].map({0: "BUY", 1: "SELL"})
    df["bot"] = df["magic"] == cfg["execution"]["magic"]
    return df[["ticket", "symbol", "type", "volume", "price_open", "price_current", "sl", "tp",
               "profit", "bot"]]


def deals(days):
    if not LIVE:
        return pd.DataFrame()
    ds = mt5.history_deals_get(dt.datetime.now() - dt.timedelta(days=days), dt.datetime.now()) or []
    if not ds:
        return pd.DataFrame()
    df = pd.DataFrame([d._asdict() for d in ds])
    df = df[df["symbol"] != ""]
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df["net"] = df["profit"] + df["swap"] + df["commission"]
    df["bot"] = df["magic"] == cfg["execution"]["magic"]
    return df[["time", "symbol", "type", "volume", "price", "net", "bot", "comment"]]


# ---------------- sidebar ----------------
st.sidebar.title("📈 Exness Signal Bot")
acct = account()
if LIVE:
    st.sidebar.success(f"MT5 connected · {acct['mode']}")
else:
    st.sidebar.warning("MT5 not connected: showing DEMO DATA.\nOpen MetaTrader 5, log in, then reload.")
st.sidebar.caption(f"Execution: {'ON' if cfg['execution']['enabled'] else 'OFF (signals only)'}")
page = st.sidebar.radio("Page", ["Overview", "Scanner", "Chart", "History", "Settings"])
if st.sidebar.button("🔄 Refresh data"):
    st.cache_data.clear()
    st.rerun()


# ---------------- pages ----------------
def scan_all():
    rows = []
    for sym in cfg["watchlist"]:
        df = get_bars(sym, cfg["timeframe"], cfg["bars"])
        if df is None:
            rows.append({"Symbol": sym, "Signal": "⚠️ no data"})
            continue
        d = all_signals(df, cfg["strategy"])
        last = d.iloc[-1]
        s = evaluate(df, cfg["strategy"])
        past = d[d.signal.notna()]
        trend = "▲ Up" if last.close > last.ema_trend else "▼ Down"
        rows.append({
            "Symbol": sym, "Price": round(last.close, 5),
            "Signal": f"{'🟢' if s['side']=='BUY' else '🔴'} {s['side']}" if s else "—",
            "SL": round(s["sl"], 5) if s else None, "TP": round(s["tp"], 5) if s else None,
            "Trend": trend, "RSI": round(last.rsi, 1),
            "Last signal": f"{past.signal.iloc[-1]} @ {past.index[-1]:%d %b %H:%M}" if len(past) else "—",
        })
    return pd.DataFrame(rows)


if page == "Overview":
    st.header("Overview")
    c = st.columns(5)
    c[0].metric("Account", f"{acct['login']}", acct["server"])
    c[1].metric("Balance", f"{acct['balance']:,.2f} {acct['currency']}")
    c[2].metric("Equity", f"{acct['equity']:,.2f}", f"{acct['equity']-acct['balance']:+,.2f}")
    d = deals(1)
    today = d[d.bot]["net"].sum() if len(d) else 0.0
    c[3].metric("Bot P&L today", f"{today:+,.2f}")
    c[4].metric("Mode", acct["mode"])

    st.subheader("Open positions")
    p = positions()
    if len(p):
        st.dataframe(p, width="stretch", hide_index=True)
    else:
        st.info("No open positions.")

    st.subheader("Signals right now")
    sc = scan_all()
    live = sc[sc.Signal.str.contains("BUY|SELL", na=False)]
    if len(live):
        st.dataframe(live, width="stretch", hide_index=True)
    else:
        st.info(f"No new signals on the last closed {cfg['timeframe']} bar.")

elif page == "Scanner":
    st.header(f"Scanner · {cfg['timeframe']} · {len(cfg['watchlist'])} symbols")
    st.dataframe(scan_all(), width="stretch", hide_index=True)
    st.caption("Signal = triggered on the last closed bar. Last signal = most recent in the loaded history.")

elif page == "Chart":
    c1, c2, c3 = st.columns([2, 1, 1])
    sym = c1.selectbox("Symbol", cfg["watchlist"])
    tf = c2.selectbox("Timeframe", list(TFMAP), index=list(TFMAP).index(cfg["timeframe"]))
    n = c3.number_input("Bars", 250, 2000, max(cfg["bars"], 400), step=50)
    df = get_bars(sym, tf, int(n))
    if df is None:
        st.error("No data for this symbol. Check the name on the Settings page.")
    else:
        d = all_signals(df, cfg["strategy"])
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25],
                            vertical_spacing=0.03)
        fig.add_trace(go.Candlestick(x=d.index, open=d.open, high=d.high, low=d.low, close=d.close,
                                     name=sym), 1, 1)
        for col, color in [("ema_fast", "#f5a623"), ("ema_slow", "#4a90e2"), ("ema_trend", "#9b9b9b")]:
            fig.add_trace(go.Scatter(x=d.index, y=d[col], name=col, line=dict(width=1.2, color=color)), 1, 1)
        for side, sym_, color, y in [("BUY", "triangle-up", "#16a34a", d.low * 0.998),
                                     ("SELL", "triangle-down", "#dc2626", d.high * 1.002)]:
            m = d.signal == side
            fig.add_trace(go.Scatter(x=d.index[m], y=y[m], mode="markers", name=side,
                                     marker=dict(symbol=sym_, size=12, color=color)), 1, 1)
        fig.add_trace(go.Scatter(x=d.index, y=d.rsi, name="RSI", line=dict(color="#8b5cf6")), 2, 1)
        for lvl in (30, 70):
            fig.add_hline(y=lvl, line_dash="dot", line_color="gray", row=2, col=1)
        fig.update_layout(height=650, xaxis_rangeslider_visible=False, margin=dict(t=20, b=10),
                          legend=dict(orientation="h"))
        st.plotly_chart(fig, width="stretch")
        sigs = d[d.signal.notna()][["signal", "close", "rsi", "atr"]].tail(15)[::-1]
        st.subheader("Signals in this window")
        st.dataframe(sigs, width="stretch")

elif page == "History":
    st.header("Trade history")
    days = st.slider("Days", 1, 90, 30)
    only_bot = st.checkbox("Only bot trades", True)
    d = deals(days)
    if not len(d):
        st.info("No deals (or MT5 not connected).")
    else:
        if only_bot:
            d = d[d.bot]
        c = st.columns(3)
        c[0].metric("Net P&L", f"{d.net.sum():+,.2f}")
        closed = d[d.net != 0]
        c[1].metric("Win rate", f"{(closed.net > 0).mean()*100:.0f}%" if len(closed) else "—")
        c[2].metric("Deals", len(d))
        if len(d):
            eq = d.sort_values("time").assign(cum=lambda x: x.net.cumsum())
            st.line_chart(eq.set_index("time")["cum"])
        st.dataframe(d.sort_values("time", ascending=False), width="stretch", hide_index=True)

elif page == "Settings":
    st.header("Settings")
    with st.form("cfg"):
        wl = st.text_area("Watchlist (one symbol per line)", "\n".join(cfg["watchlist"]), height=220)
        c = st.columns(3)
        tf = c[0].selectbox("Timeframe", list(TFMAP), index=list(TFMAP).index(cfg["timeframe"]))
        s = cfg["strategy"]
        ef = c[1].number_input("EMA fast", 2, 200, s["ema_fast"])
        es = c[2].number_input("EMA slow", 3, 400, s["ema_slow"])
        c = st.columns(3)
        et = c[0].number_input("EMA trend", 10, 500, s["ema_trend"])
        sl = c[1].number_input("SL × ATR", 0.2, 10.0, float(s["sl_atr"]), 0.1)
        tp = c[2].number_input("TP × ATR", 0.2, 20.0, float(s["tp_atr"]), 0.1)
        st.markdown("**Execution** (orders are only placed by `python bot.py run`)")
        ex = cfg["execution"]
        c = st.columns(4)
        en = c[0].toggle("Place orders", ex["enabled"])
        demo = c[1].toggle("Demo accounts only", ex["demo_only"])
        risk = c[2].number_input("Risk % / trade", 0.05, 5.0, float(ex["risk_per_trade_pct"]), 0.05)
        dl = c[3].number_input("Max daily loss %", 0.5, 20.0, float(ex["max_daily_loss_pct"]), 0.5)
        if st.form_submit_button("💾 Save"):
            cfg["watchlist"] = [x.strip() for x in wl.splitlines() if x.strip()]
            cfg["timeframe"] = tf
            s.update(ema_fast=int(ef), ema_slow=int(es), ema_trend=int(et), sl_atr=sl, tp_atr=tp)
            ex.update(enabled=en, demo_only=demo, risk_per_trade_pct=risk, max_daily_loss_pct=dl)
            with open(CFG_PATH, "w", encoding="utf-8") as f:
                yaml.safe_dump(cfg, f, sort_keys=False)
            st.cache_data.clear()
            st.success("Saved. Restart `bot.py run` for execution changes to take effect.")
    if LIVE:
        with st.expander("Symbols available on your account"):
            q = st.text_input("Filter", "XAU")
            st.write([x.name for x in mt5.symbols_get() if q.upper() in x.name.upper()][:200])
