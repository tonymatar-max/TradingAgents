"""Weekly strategy coach: a free LLM SUGGESTS setting changes, a backtest decides, the owner approves.

1. Collect: every committee bot's current settings, its live closed paper/demo trades and loss patterns,
   and the backtest of its current settings on ~2 years of H1 history.
2. Ask the LLM (OpenAI-compatible, e.g. the local Cobalt gateway) for at most 3 changes. It may only touch
   votes (8-10), stop and target in ATR (the same limits as the portal's edit panel). Anything else is dropped.
3. Test: each suggestion is backtested on the same history, split in two halves. It PASSES only if the profit
   factor is above 1 and better than the current settings in BOTH halves, with 30+ trades per half.
4. Report: coach/suggestions.json, shown in the agents portal. Nothing changes until the owner clicks Apply,
   which writes the bot's settings.json (the bot reloads it, new trades only). Every run and apply is logged
   in coach/history.jsonl.

Honest limits: the halves are the same data the LLM's ideas are judged on, so a pass is weak evidence, not proof.
Most suggestions are expected to fail. If the LLM is offline the run is skipped (nothing is changed).
Run:  python -m xauusd_agents coach --root C:\\tradingagents
"""
from __future__ import annotations

import json
import os
import re
import time

import pandas as pd

from . import live_settings as ls
from .ai_review import DEFAULT_URL, KEY_ENV, _http_post
from .backtest import Config, run_backtest
from .committee import Committee

DIR = "coach"
MIN_TRADES = 30
VOTES = (8, 9, 10)
# round-trip cost in price units (spread + commission); XAUUSD measured, others estimates (multi_symbol/run_costs.py)
COST = {"XAUUSD": 0.15, "XAGUSD": 0.030, "EURUSD": 0.00010, "GBPUSD": 0.00012, "USDJPY": 0.012, "USOIL": 0.040,
        "US500": 0.60, "USTEC": 2.00, "AAPL": 0.10, "NVDA": 0.15}

SYSTEM = (
    "You are a cautious quant reviewer for rule-based committee trading bots on a DEMO account. Ten indicator agents "
    "vote on each closed H1 candle; a trade needs `votes` of 10 to agree; stop and target are ATR multiples. "
    "From the statistics given, propose at most 3 setting changes worth TESTING. You may only change votes "
    "(8, 9 or 10), sl_atr (1.0-3.0) and tp_atr (1.0-4.0), for bots listed. Small live samples are noise: say so "
    "and prefer the backtest. Do not predict prices. Every suggestion will be backtested before anyone uses it. "
    'Reply with one JSON object only: {"suggestions":[{"bot":"NAME","votes":9,"sl_atr":1.5,"tp_atr":2.0,'
    '"reason":"one sentence"}]} (an empty list is a fine answer).'
)


# ------------------------------------------------------------------ bots and settings

def load_bots(root: str) -> list[dict]:
    """bots.json + instruments added in the portal (bots.local.json), with the on/off choice and live overrides."""
    with open(os.path.join(root, "bots.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    local = {}
    try:
        with open(os.path.join(root, "bots.local.json"), encoding="utf-8-sig") as f:
            local = json.load(f)
    except (OSError, ValueError):
        pass
    enabled = local.get("enabled", {}) if isinstance(local.get("enabled"), dict) else {}
    out = []
    for b in list(cfg["bots"]) + list(local.get("extra") or []):
        b = dict(b)
        b["on"] = bool(enabled.get(b["name"], b.get("enabled", True)))
        b["votes"] = round(cfg.get("threshold", 0.9) * 10)
        over = ls.read(os.path.join(root, b["logdir"])) or {}
        for k in ("votes", "sl_atr", "tp_atr"):
            if k in over:
                b[k] = over[k]
        out.append(b)
    return out


# ------------------------------------------------------------------ backtest of one setting

def history(root: str, symbol: str, download: bool = True, years: float = 2.0) -> pd.DataFrame | None:
    """H1 candles: refreshed from MT5 when possible (coach/data), else multi_symbol/<SYM>_H1.csv."""
    from .data import load_csv
    data = os.path.join(root, DIR, "data")
    os.makedirs(data, exist_ok=True)
    path = os.path.join(data, f"{symbol}_H1.csv")
    if download:
        try:
            from .mt5_export import export_csv
            start = (pd.Timestamp.now() - pd.Timedelta(days=int(365 * years))).strftime("%Y-%m-%d")
            export_csv(symbol, "H1", start, None, path)
        except BaseException as e:  # noqa: BLE001 - export uses SystemExit on MT5 errors; fall back to a saved file
            print(f"  {symbol}: MT5 download failed ({e}); using a saved file if there is one")
    for p in (path, os.path.join(root, "multi_symbol", f"{symbol}_H1.csv")):
        if os.path.isfile(p):
            return load_csv(p)
    return None


def evaluate(df: pd.DataFrame, table: pd.DataFrame, votes: int, sl: float, tp: float, cost: float) -> dict:
    """Profit factor and average R in each half. Fixed 1 unit and a huge balance: results in price units, no compounding."""
    com = Committee(threshold=votes / 10)
    sig = com.signals(df, table)
    warm = min(len(df) - 1, 650)                          # indicators need ~650 candles to settle
    start, split = df.index[warm], df.index[warm + (len(df) - warm) // 2]
    cfg = Config(balance=1e12, fixed_lots=1.0, contract_oz=1.0, spread=cost, sl_atr=sl, tp_atr=tp, max_lots=1.0)
    trades, _, _ = run_backtest(df, com, cfg, start=start, signals=sig)
    res = {"split": str(split)[:10]}
    for tag, part in (("first", trades[df.index[trades.entry_i] < split] if len(trades) else trades),
                      ("second", trades[df.index[trades.entry_i] >= split] if len(trades) else trades)):
        if not len(part):
            res[tag] = {"n": 0, "pf": None, "avg_R": None}
            continue
        w, lo = part.pnl[part.pnl > 0].sum(), -part.pnl[part.pnl <= 0].sum()
        risk = (part.entry - part.sl).abs()
        res[tag] = {"n": int(len(part)), "pf": round(float(w / lo), 2) if lo > 0 else None,
                    "avg_R": round(float((part.pnl / risk).mean()), 3)}
    return res


def passes(cur: dict, new: dict) -> tuple[bool, str]:
    for h in ("first", "second"):
        c, n = cur[h], new[h]
        if n["n"] < MIN_TRADES:
            return False, f"only {n['n']} trades in the {h} half (needs {MIN_TRADES})"
        if n["pf"] is None or n["pf"] <= 1.0:
            return False, f"loses money in the {h} half (PF {n['pf']})"
        if c["pf"] is not None and n["pf"] <= c["pf"]:
            return False, f"not better than now in the {h} half (PF {n['pf']} vs {c['pf']})"
    return True, "better than the current settings in both halves"


# ------------------------------------------------------------------ the LLM

def parse_suggestions(text: str) -> list[dict]:
    """Last JSON object with a 'suggestions' list in the reply (free models sometimes think aloud first)."""
    found, dec, i = [], json.JSONDecoder(), 0
    while True:
        i = text.find("{", i)
        if i < 0:
            break
        try:
            obj, end = dec.raw_decode(text, i)
            if isinstance(obj, dict) and isinstance(obj.get("suggestions"), list):
                found.append(obj["suggestions"])
            i = end
        except ValueError:
            i += 1
    return found[-1] if found else []


def clean_suggestions(raw: list, bots: list[dict], limit: int = 3) -> list[dict]:
    names = {b["name"].upper(): b for b in bots}
    out = []
    for s in raw:
        if not isinstance(s, dict):
            continue
        b = names.get(str(s.get("bot", "")).upper())
        if not b:
            continue
        change = {k: s[k] if k in s else b[k] for k in ("votes", "sl_atr", "tp_atr")}
        clean, errs = ls.validate(change)
        if errs or clean["votes"] not in VOTES:
            continue
        if all(float(clean[k]) == float(b[k]) for k in clean):
            continue                                       # "change" to what it already is
        out.append({"bot": b["name"], **clean, "reason": str(s.get("reason", ""))[:300]})
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ LLM settings (set in the portal)

SETTINGS = "llm_settings.json"


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows DPAPI (current user): only this Windows user on this machine can decrypt the key."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src, dst = Blob(len(data), buf), Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(dst)):   # 0x1 = no UI
        raise OSError("Windows could not " + ("encrypt" if protect else "decrypt") + " the key")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def load_llm_settings(root: str) -> dict:
    """{url, model, key}. The key: env AI_REVIEW_API_KEY wins, else the encrypted one saved from the portal."""
    import base64
    try:
        with open(_path(root, SETTINGS), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    key = os.environ.get(KEY_ENV, "")
    if not key and raw.get("key_dpapi"):
        try:
            key = _dpapi(base64.b64decode(raw["key_dpapi"]), False).decode()
        except (OSError, ValueError, AttributeError):
            key = ""
    return {"url": raw.get("url") or "", "model": raw.get("model") or "", "key": key}


URL_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?(/[A-Za-z0-9._~/\-]*)?$")


def save_llm_settings(root: str, url: str | None = None, model: str | None = None, key: str | None = None) -> dict:
    """Portal form. key=None keeps the saved key, key="" removes it. The key is never returned or logged."""
    import base64
    try:
        with open(_path(root, SETTINGS), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    if url is not None:
        if url and not URL_RE.match(url):
            raise ValueError("URL must look like http://127.0.0.1:3001/v1")
        raw["url"] = url
    if model is not None:
        if model and not re.match(r"^[A-Za-z0-9._:/\-]{1,80}$", model):
            raise ValueError("model: letters, digits and . _ : / - only")
        raw["model"] = model
    if key is not None:
        if len(key) > 500 or any(c.isspace() for c in key):
            raise ValueError("the key looks wrong (spaces or too long)")
        if key:
            raw["key_dpapi"] = base64.b64encode(_dpapi(key.encode(), True)).decode()
            raw["key_hint"] = "…" + key[-4:] if len(key) >= 12 else "set"
        else:
            raw.pop("key_dpapi", None); raw.pop("key_hint", None)
    tmp = _path(root, SETTINGS + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(raw, f)
    os.replace(tmp, _path(root, SETTINGS))
    return public_llm_settings(root)


def public_llm_settings(root: str) -> dict:
    """What the portal may show: never the key itself."""
    try:
        with open(_path(root, SETTINGS), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    return {"url": raw.get("url") or "", "model": raw.get("model") or "", "key_saved": bool(raw.get("key_dpapi")),
            "key_hint": raw.get("key_hint", ""), "key_in_env": bool(os.environ.get(KEY_ENV))}


def api_base(url: str) -> str:
    """'http://127.0.0.1:3001' -> 'http://127.0.0.1:3001/v1' (OpenAI-compatible servers live under /v1)."""
    from urllib.parse import urlparse
    url = url.strip().rstrip("/")
    return url + "/v1" if urlparse(url).path in ("", "/") else url


def ask_llm(summary: dict, url: str, model: str, timeout: float = 120.0, poster=None, key: str = "") -> tuple[list, str]:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    body = {"model": model, "temperature": 0.2, "max_tokens": 1200,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": json.dumps(summary, default=str)}]}
    res = (poster or _http_post)(api_base(url) + "/chat/completions", headers, body, timeout)
    return parse_suggestions(res["choices"][0]["message"]["content"]), str(res.get("model", model))


# ------------------------------------------------------------------ one weekly run

def live_stats(root: str, bot: dict) -> dict:
    from .learning import live_context, loss_report
    try:
        ctx = live_context(os.path.join(root, bot["logdir"]))
    except OSError:
        return {"closed_trades": 0}
    if ctx.empty:
        return {"closed_trades": 0}
    rep = loss_report(ctx)
    worst = rep.sort_values("avg_R").head(3)[["feature", "value", "n", "avg_R"]].to_dict("records") if len(rep) else []
    return {"closed_trades": len(ctx), "win_%": round(100 * float(ctx.win.mean()), 1),
            "avg_R": round(float(ctx.R.mean()), 3), "weakest_groups": worst}


def run(root: str, url: str = DEFAULT_URL, model: str = "auto", download: bool = True, poster=None,
        loader=None, now=time.time) -> dict:
    bots = [b for b in load_bots(root) if b["on"]]
    loader = loader or (lambda sym: history(root, sym, download))
    data, summary = {}, {"bots": []}
    for b in bots:
        df = loader(b["symbol"])
        if df is None or len(df) < 1500:
            print(f"  {b['name']}: not enough history, skipped")
            continue
        table = Committee().vote_table(df)
        cost = COST.get(b["symbol"], float(df.close.iloc[-1]) * 0.0004)
        data[b["name"]] = (df, table, cost)
        cur = evaluate(df, table, b["votes"], b["sl_atr"], b["tp_atr"], cost)
        b["backtest"] = cur
        summary["bots"].append({"bot": b["name"], "symbol": b["symbol"], "votes": b["votes"], "sl_atr": b["sl_atr"],
                                "tp_atr": b["tp_atr"], "backtest_now": cur, "live": live_stats(root, b)})
    st = load_llm_settings(root)                            # portal settings override the task's defaults
    url, model = st["url"] or url, st["model"] or model
    out = {"created": now(), "model": model, "llm_ok": False, "items": [], "note": ""}
    try:
        raw, out["model"] = ask_llm(summary, url, model, poster=poster, key=st["key"])
        out["llm_ok"] = True
    except Exception as e:  # noqa: BLE001 - LLM offline: skip this week, change nothing
        out["note"] = f"LLM not reachable, skipped this week ({type(e).__name__}: {str(e)[:150]})"
        raw = []
    tested = [b for b in bots if b["name"] in data]
    for n, s in enumerate(clean_suggestions(raw, tested)):
        b = next(x for x in tested if x["name"] == s["bot"])
        df, table, cost = data[b["name"]]
        new = evaluate(df, table, s["votes"], s["sl_atr"], s["tp_atr"], cost)
        ok, why = passes(b["backtest"], new)
        out["items"].append({"id": f"{int(out['created'])}-{n}", "bot": b["name"], "symbol": b["symbol"],
                             "logdir": b["logdir"], "now": {k: b[k] for k in ("votes", "sl_atr", "tp_atr")},
                             "change": {k: s[k] for k in ("votes", "sl_atr", "tp_atr")}, "reason": s["reason"],
                             "backtest_now": b["backtest"], "backtest_new": new, "passed": ok, "verdict": why,
                             "applied": None})
    if out["llm_ok"] and not out["items"]:
        out["note"] = "The LLM suggested no valid change this week."
    save(root, out)
    log(root, {"type": "run", "time": out["created"], "model": out["model"], "llm_ok": out["llm_ok"],
               "items": [{k: i[k] for k in ("id", "bot", "change", "passed", "verdict")} for i in out["items"]]})
    return out


# ------------------------------------------------------------------ files and Apply

def _path(root, name):
    os.makedirs(os.path.join(root, DIR), exist_ok=True)
    return os.path.join(root, DIR, name)


def save(root: str, out: dict):
    tmp = _path(root, "suggestions.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, default=str)
    os.replace(tmp, _path(root, "suggestions.json"))


def load(root: str) -> dict | None:
    try:
        with open(_path(root, "suggestions.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def log(root: str, rec: dict):
    with open(_path(root, "history.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")


ID_RE = re.compile(r"^\d+-\d$")


def apply(root: str, item_id: str, by: str = "coach, approved in the portal") -> dict:
    """Owner clicked Apply: write the bot's settings.json (only for a suggestion that passed the test)."""
    out = load(root)
    if not out or not ID_RE.match(item_id):
        raise ValueError("unknown suggestion")
    it = next((i for i in out["items"] if i["id"] == item_id), None)
    if it is None:
        raise ValueError("unknown suggestion")
    if not it["passed"]:
        raise ValueError("this suggestion failed the backtest and cannot be applied")
    if it["applied"]:
        raise ValueError("already applied")
    clean, errs = ls.validate(it["change"])
    if errs:
        raise ValueError("; ".join(errs))
    logdir = os.path.join(root, it["logdir"])
    os.makedirs(logdir, exist_ok=True)
    ls.write(logdir, clean, by)
    it["applied"] = time.time()
    save(root, out)
    log(root, {"type": "apply", "time": it["applied"], "id": item_id, "bot": it["bot"], "from": it["now"], "to": clean})
    return out
