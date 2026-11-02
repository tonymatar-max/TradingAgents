"""Line-by-line Python transliteration of ComputeVotes() in mt5/XAUUSD_Consensus_EA.mq5.
Purpose: prove the EA's ALGORITHMS equal the pandas agents (it cannot catch MQL5 syntax errors)."""
NA = -1.0e300


def is_num(v):
    return v > -1.0e299


def ema_alpha(x, alpha):
    out = [0.0] * len(x)
    if x:
        out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1.0 - alpha) * out[i - 1]
    return out


def ema_span(x, span):
    return ema_alpha(x, 2.0 / (span + 1.0))


def compute_votes(rows, P):
    """rows: list of (high, low, close). Returns votes[i][k], atr[i]."""
    n = len(rows)
    h = [r[0] for r in rows]; l = [r[1] for r in rows]; c = [r[2] for r in rows]
    votes = [[0] * 10 for _ in range(n)]
    tr = [0.0] * n
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        pc = c[i - 1]
        tr[i] = max(h[i] - l[i], max(abs(h[i] - pc), abs(l[i] - pc)))
    atr = ema_alpha(tr, 1.0 / P["atr"])

    e1, e2, e3 = ema_span(c, P["ema_fast"]), ema_span(c, P["ema_slow"]), ema_span(c, P["ema_trend"])
    for i in range(n):
        if e1[i] > e2[i] and c[i] > e3[i]: votes[i][0] = 1
        elif e1[i] < e2[i] and c[i] < e3[i]: votes[i][0] = -1

    ef, es = ema_span(c, P["macd_fast"]), ema_span(c, P["macd_slow"])
    line = [ef[i] - es[i] for i in range(n)]
    sig = ema_span(line, P["macd_signal"])
    for i in range(n):
        hist = line[i] - sig[i]
        if line[i] > sig[i] and hist > 0: votes[i][1] = 1
        elif line[i] < sig[i] and hist < 0: votes[i][1] = -1

    up = [0.0] * n; dn = [0.0] * n
    for i in range(1, n):
        d = c[i] - c[i - 1]
        up[i] = d if d > 0 else 0.0
        dn[i] = -d if d < 0 else 0.0
    upE, dnE = ema_alpha(up, 1.0 / P["rsi"]), ema_alpha(dn, 1.0 / P["rsi"])
    for i in range(n):
        rsi = 50.0
        if dnE[i] > 0: rsi = 100.0 - 100.0 / (1.0 + upE[i] / dnE[i])
        if rsi > P["rsi_bull"] and rsi < P["rsi_ob"]: votes[i][2] = 1
        elif rsi < P["rsi_bear"] and rsi > P["rsi_os"]: votes[i][2] = -1

    mid = [NA] * n; wid = [NA] * n
    for i in range(n):
        if i >= P["bb"] - 1:
            s = sum(c[i - k] for k in range(P["bb"])); m = s / P["bb"]
            q = sum((c[i - k] - m) ** 2 for k in range(P["bb"]))
            mid[i] = m; wid[i] = 2.0 * P["bb_k"] * (q / P["bb"]) ** 0.5
    for i in range(n):
        expanding = i >= P["bb_lag"] and is_num(wid[i]) and is_num(wid[i - P["bb_lag"]]) and wid[i] > wid[i - P["bb_lag"]]
        if not is_num(mid[i]) or not expanding: continue
        if c[i] > mid[i]: votes[i][3] = 1
        elif c[i] < mid[i]: votes[i][3] = -1

    prev = 0
    for i in range(n):
        h20 = l20 = h10 = l10 = NA
        if i >= P["don_entry"]:
            h20 = max(h[i - P["don_entry"]:i]); l20 = min(l[i - P["don_entry"]:i])
        if i >= P["don_exit"]:
            h10 = max(h[i - P["don_exit"]:i]); l10 = min(l[i - P["don_exit"]:i])
        st = 0
        if is_num(h20) and c[i] > h20: st = 1
        elif is_num(l20) and c[i] < l20: st = -1
        elif prev == 1 and not (is_num(l10) and c[i] < l10): st = 1
        elif prev == -1 and not (is_num(h10) and c[i] > h10): st = -1
        votes[i][4] = st; prev = st

    kk = [NA] * n
    for i in range(n):
        if i >= P["stoch_k"] - 1:
            hh = max(h[i - k] for k in range(P["stoch_k"])); ll = min(l[i - k] for k in range(P["stoch_k"]))
            if hh != ll: kk[i] = 100.0 * (c[i] - ll) / (hh - ll)
    for i in range(n):
        if not is_num(kk[i]) or i < P["stoch_d"] - 1: continue
        win = [kk[i - k] for k in range(P["stoch_d"])]
        if not all(is_num(v) for v in win): continue
        dd = sum(win) / P["stoch_d"]
        if kk[i] > dd and kk[i] > 50.0: votes[i][5] = 1
        elif kk[i] < dd and kk[i] < 50.0: votes[i][5] = -1

    plus = [0.0] * n; minus = [0.0] * n
    for i in range(1, n):
        upm = h[i] - h[i - 1]; dnm = -(l[i] - l[i - 1])
        plus[i] = upm if (upm > dnm and upm > 0) else 0.0
        minus[i] = dnm if (dnm > upm and dnm > 0) else 0.0
    a = ema_alpha(tr, 1.0 / P["adx"]); pE = ema_alpha(plus, 1.0 / P["adx"]); mE = ema_alpha(minus, 1.0 / P["adx"])
    pdi = [0.0] * n; mdi = [0.0] * n; dx = [0.0] * n; prevdx = 0.0
    for i in range(n):
        pdi[i] = 100.0 * pE[i] / a[i] if a[i] > 0 else 0.0
        mdi[i] = 100.0 * mE[i] / a[i] if a[i] > 0 else 0.0
        s = pdi[i] + mdi[i]
        dx[i] = 100.0 * abs(pdi[i] - mdi[i]) / s if s > 0 else prevdx
        prevdx = dx[i]
    adx = ema_alpha(dx, 1.0 / P["adx"])
    for i in range(n):
        if adx[i] > P["adx_min"] and pdi[i] > mdi[i]: votes[i][6] = 1
        elif adx[i] > P["adx_min"] and mdi[i] > pdi[i]: votes[i][6] = -1

    a2 = ema_alpha(tr, 1.0 / P["st_period"])
    ub = [0.0] * n; lb = [0.0] * n; fub = [0.0] * n; flb = [0.0] * n; d = [0] * n
    for i in range(n):
        hl2 = (h[i] + l[i]) / 2.0
        ub[i] = hl2 + P["st_mult"] * a2[i]; lb[i] = hl2 - P["st_mult"] * a2[i]; fub[i] = ub[i]; flb[i] = lb[i]
    for i in range(1, n):
        hl2 = (h[i] + l[i]) / 2.0
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1]) else flb[i - 1]
        if d[i - 1] == 0: d[i] = 1 if c[i] >= hl2 else -1
        elif d[i - 1] == 1: d[i] = -1 if c[i] < flb[i] else 1
        else: d[i] = 1 if c[i] > fub[i] else -1
    for i in range(n): votes[i][7] = d[i]

    atr_m = ema_alpha(tr, 1.0 / P["atr_mom"])
    for i in range(P["mom_long"], n):
        r60 = c[i] / c[i - P["mom_long"]] - 1.0; r20 = c[i] / c[i - P["mom_short"]] - 1.0
        noise = atr_m[i] / c[i] * P["mom_noise"]
        if r60 > noise and r20 > 0: votes[i][8] = 1
        elif r60 < -noise and r20 < 0: votes[i][8] = -1

    w = P["lr"]; xm = (w - 1) / 2.0; xv = sum((k - xm) ** 2 for k in range(w))
    for i in range(w - 1, n):
        ys = [c[i - w + 1 + k] for k in range(w)]; ym = sum(ys) / w
        sxy = sum((k - xm) * (ys[k] - ym) for k in range(w)); sst = sum((y - ym) ** 2 for y in ys)
        slope = sxy / xv
        sse = sum((ys[k] - (ym + slope * (k - xm))) ** 2 for k in range(w))
        r2 = 1.0 - sse / sst if sst > 0 else 0.0
        if slope > 0 and r2 > P["lr_r2"]: votes[i][9] = 1
        elif slope < 0 and r2 > P["lr_r2"]: votes[i][9] = -1
    return votes, atr


DEFAULTS = dict(atr=14, ema_fast=20, ema_slow=50, ema_trend=200, macd_fast=12, macd_slow=26, macd_signal=9, rsi=14,
                rsi_bull=55.0, rsi_bear=45.0, rsi_ob=80.0, rsi_os=20.0, bb=20, bb_k=2.0, bb_lag=5, don_entry=20, don_exit=10,
                stoch_k=14, stoch_d=3, adx=14, adx_min=20.0, st_period=10, st_mult=3.0, mom_long=60, mom_short=20,
                mom_noise=3.0, lr=30, lr_r2=0.5, atr_mom=14)

PERIOD_KEYS = ("ema_fast", "ema_slow", "ema_trend", "macd_fast", "macd_slow", "macd_signal", "rsi", "bb", "bb_lag", "don_entry",
               "don_exit", "stoch_k", "stoch_d", "adx", "st_period", "mom_long", "mom_short", "lr", "atr_mom")


def scaled(scale):
    """Same rule as Sc() in the EA: round half up, minimum 1. The stop ATR (key 'atr') is NOT scaled."""
    import math
    return {k: (max(1, int(math.floor(v * scale + 0.5))) if k in PERIOD_KEYS else v) for k, v in DEFAULTS.items()}
