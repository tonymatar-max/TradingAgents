//+------------------------------------------------------------------+
//|                                        XAUUSD_Consensus_EA.mq5   |
//| 10 strategy agents vote; a trade needs 8, 9 or 10 of them to     |
//| agree. Port of the Python bot (xauusd_agents).                   |
//| DRY RUN by default. Orders are refused on real-money accounts.   |
//+------------------------------------------------------------------+
#property copyright "tradingagents"
#property version   "1.00"
#property description "10 strategy agents vote; a trade needs 8, 9 or 10 of them to agree."
#property description "DRY RUN by default. Order placement is refused on real-money accounts."

#include <Trade\Trade.mqh>

enum ENUM_VOTES_NEEDED { VOTES_8 = 8, VOTES_9 = 9, VOTES_10 = 10 };
enum ENUM_RUN_MODE     { RUN_DRY = 0, RUN_DEMO = 1 };
enum ENUM_LOT_MODE     { LOTS_FIXED = 0, LOTS_RISK = 1 };

//--- run mode
input group "Mode"
input ENUM_RUN_MODE      InpMode          = RUN_DRY;       // Dry run = log + paper trades only; Demo = send orders (demo accounts only)
input ulong              InpMagic         = 26100;         // Magic number (tags this EA's orders)
input ENUM_TIMEFRAMES    InpTF            = PERIOD_H1;     // Signal timeframe
input bool               InpActOnStart    = false;         // Act on the last closed candle when attached (else wait for the next one)
input int                InpHistoryBars   = 2500;          // Candles used to warm up the indicators

//--- committee
input group "Committee"
input ENUM_VOTES_NEEDED  InpVotes         = VOTES_9;       // Agents that must agree (8, 9 or 10 of the enabled agents)
input bool               InpA1            = true;          // Agent 1  EMA trend
input bool               InpA2            = true;          // Agent 2  MACD momentum
input bool               InpA3            = true;          // Agent 3  RSI regime
input bool               InpA4            = true;          // Agent 4  Bollinger walk
input bool               InpA5            = true;          // Agent 5  Donchian breakout
input bool               InpA6            = true;          // Agent 6  Stochastic bias
input bool               InpA7            = true;          // Agent 7  ADX / DI
input bool               InpA8            = true;          // Agent 8  Supertrend
input bool               InpA9            = true;          // Agent 9  Time-series momentum
input bool               InpA10           = true;          // Agent 10 Linear-regression slope

//--- higher timeframe filter
input group "Higher-timeframe confluence"
input bool               InpUseHtf        = true;          // Require the higher timeframe to agree
input ENUM_TIMEFRAMES    InpHtf           = PERIOD_H4;     // Higher timeframe
input int                InpHtfMin        = 6;             // Agents needed on the higher timeframe

//--- stops
input group "Stop and target (ATR multiples)"
input double             InpSlAtr         = 2.0;           // Stop distance  x ATR
input double             InpTpAtr         = 2.0;           // Target distance x ATR
input int                InpAtrPeriod     = 14;            // ATR period

//--- size
input group "Position size"
input ENUM_LOT_MODE      InpLotMode       = LOTS_FIXED;    // Fixed lots, or risk-based
input double             InpLots          = 0.3;           // Fixed lot size
input double             InpRiskPct       = 1.0;           // Risk per trade, % of equity (risk-based mode)
input double             InpMaxRiskPct    = 0.0;           // Fixed-lot cap: skip trades risking more than this % (0 = no cap)

//--- safety
input group "Safety"
input double             InpMaxDailyLossPct = 3.0;         // Stop opening trades after losing this % of balance today (0 = off)
input int                InpMaxSpreadPoints = 0;           // Skip when spread is wider than this many points (0 = off)
input int                InpDeviation     = 30;            // Max slippage in points

//--- news
input group "News (MT5 economic calendar)"
input bool               InpUseNews       = true;          // Block new trades around important events
input int                InpNewsBefore    = 30;            // Minutes before an event
input int                InpNewsAfter     = 30;            // Minutes after an event
input ENUM_CALENDAR_EVENT_IMPORTANCE InpNewsMinImportance = CALENDAR_IMPORTANCE_HIGH; // Minimum importance
input string             InpNewsCurrency  = "USD";         // Currency of the events

//--- output for the portal
input group "Records"
input bool               InpWriteEvents   = true;          // Write events.jsonl / status.json (Common\Files\xauusd_ea) for the portal

//--- agent parameters (defaults = the Python version)
input group "Agent 1 EMA trend"
input int    InpEmaFast = 20;
input int    InpEmaSlow = 50;
input int    InpEmaTrend = 200;
input group "Agent 2 MACD"
input int    InpMacdFast = 12;
input int    InpMacdSlow = 26;
input int    InpMacdSignal = 9;
input group "Agent 3 RSI"
input int    InpRsiPeriod = 14;
input double InpRsiBull = 55.0;
input double InpRsiBear = 45.0;
input double InpRsiOverbought = 80.0;
input double InpRsiOversold = 20.0;
input group "Agent 4 Bollinger"
input int    InpBbPeriod = 20;
input double InpBbK = 2.0;
input int    InpBbLag = 5;
input group "Agent 5 Donchian"
input int    InpDonEntry = 20;
input int    InpDonExit = 10;
input group "Agent 6 Stochastic"
input int    InpStochK = 14;
input int    InpStochD = 3;
input group "Agent 7 ADX"
input int    InpAdxPeriod = 14;
input double InpAdxMin = 20.0;
input group "Agent 8 Supertrend"
input int    InpStPeriod = 10;
input double InpStMult = 3.0;
input group "Agent 9 Momentum"
input int    InpMomLong = 60;
input int    InpMomShort = 20;
input double InpMomNoise = 3.0;
input group "Agent 10 Regression"
input int    InpLrPeriod = 30;
input double InpLrMinR2 = 0.5;

//+------------------------------------------------------------------+
#define NA_V (-1.0e300)
bool IsNum(const double v) { return v > -1.0e299; }

CTrade   g_trade;
datetime g_lastBar  = 0;
string   g_status   = "starting";
string   g_lastBarStr = "";
string   g_dir      = "xauusd_ea";
bool     g_en[10];
int      g_enabled  = 0;
string   g_names[10] = {"1_EMA_Trend", "2_MACD_Momentum", "3_RSI_Regime", "4_Bollinger_Walk", "5_Donchian_Breakout",
                        "6_Stochastic_Bias", "7_ADX_DI", "8_Supertrend", "9_TS_Momentum", "10_Linreg_Slope"};
string   g_pp = "";   // prefix of the paper-position global variables

//+------------------------------------------------------------------+
//| Indicator helpers (match pandas ewm(adjust=False) etc.)          |
//+------------------------------------------------------------------+
void EmaAlpha(const double &x[], double &out[], const int n, const double alpha)
{
   ArrayResize(out, n);
   if(n <= 0) return;
   out[0] = x[0];
   for(int i = 1; i < n; i++)
      out[i] = alpha * x[i] + (1.0 - alpha) * out[i - 1];
}

void EmaSpan(const double &x[], double &out[], const int n, const int span)
{
   EmaAlpha(x, out, n, 2.0 / (span + 1.0));
}

//+------------------------------------------------------------------+
//| Compute the 10 agents' votes (+1 buy, -1 sell, 0 neutral)         |
//| for every candle. Row n-1 is the forming candle: ignore it.        |
//+------------------------------------------------------------------+
void ComputeVotes(const MqlRates &r[], const int n, int &votes[][10], double &atrOut[])
{
   ArrayResize(votes, n);
   double h[], l[], c[];
   ArrayResize(h, n); ArrayResize(l, n); ArrayResize(c, n);
   for(int i = 0; i < n; i++)
   {
      h[i] = r[i].high; l[i] = r[i].low; c[i] = r[i].close;
      for(int k = 0; k < 10; k++) votes[i][k] = 0;
   }

   // true range and Wilder ATR
   double tr[];
   ArrayResize(tr, n);
   tr[0] = h[0] - l[0];
   for(int i = 1; i < n; i++)
   {
      double pc = c[i - 1];
      tr[i] = MathMax(h[i] - l[i], MathMax(MathAbs(h[i] - pc), MathAbs(l[i] - pc)));
   }
   EmaAlpha(tr, atrOut, n, 1.0 / InpAtrPeriod);

   // ---- Agent 1: EMA trend
   {
      double e1[], e2[], e3[];
      EmaSpan(c, e1, n, InpEmaFast); EmaSpan(c, e2, n, InpEmaSlow); EmaSpan(c, e3, n, InpEmaTrend);
      for(int i = 0; i < n; i++)
      {
         if(e1[i] > e2[i] && c[i] > e3[i]) votes[i][0] = 1;
         else if(e1[i] < e2[i] && c[i] < e3[i]) votes[i][0] = -1;
      }
   }
   // ---- Agent 2: MACD
   {
      double ef[], es[], line[], sig[];
      EmaSpan(c, ef, n, InpMacdFast); EmaSpan(c, es, n, InpMacdSlow);
      ArrayResize(line, n);
      for(int i = 0; i < n; i++) line[i] = ef[i] - es[i];
      EmaSpan(line, sig, n, InpMacdSignal);
      for(int i = 0; i < n; i++)
      {
         double hist = line[i] - sig[i];
         if(line[i] > sig[i] && hist > 0) votes[i][1] = 1;
         else if(line[i] < sig[i] && hist < 0) votes[i][1] = -1;
      }
   }
   // ---- Agent 3: RSI regime (Wilder)
   {
      double up[], dn[], upE[], dnE[];
      ArrayResize(up, n); ArrayResize(dn, n);
      up[0] = 0; dn[0] = 0;
      for(int i = 1; i < n; i++)
      {
         double d = c[i] - c[i - 1];
         up[i] = (d > 0) ? d : 0.0;
         dn[i] = (d < 0) ? -d : 0.0;
      }
      EmaAlpha(up, upE, n, 1.0 / InpRsiPeriod); EmaAlpha(dn, dnE, n, 1.0 / InpRsiPeriod);
      for(int i = 0; i < n; i++)
      {
         double rsi = 50.0;
         if(dnE[i] > 0) rsi = 100.0 - 100.0 / (1.0 + upE[i] / dnE[i]);
         if(rsi > InpRsiBull && rsi < InpRsiOverbought) votes[i][2] = 1;
         else if(rsi < InpRsiBear && rsi > InpRsiOversold) votes[i][2] = -1;
      }
   }
   // ---- Agent 4: Bollinger walk
   {
      double mid[], wid[];
      ArrayResize(mid, n); ArrayResize(wid, n);
      for(int i = 0; i < n; i++)
      {
         mid[i] = NA_V; wid[i] = NA_V;
         if(i >= InpBbPeriod - 1)
         {
            double s = 0;
            for(int k = 0; k < InpBbPeriod; k++) s += c[i - k];
            double m = s / InpBbPeriod, q = 0;
            for(int k = 0; k < InpBbPeriod; k++) q += (c[i - k] - m) * (c[i - k] - m);
            mid[i] = m;
            wid[i] = 2.0 * InpBbK * MathSqrt(q / InpBbPeriod);
         }
      }
      for(int i = 0; i < n; i++)
      {
         bool expanding = (i >= InpBbLag && IsNum(wid[i]) && IsNum(wid[i - InpBbLag]) && wid[i] > wid[i - InpBbLag]);
         if(!IsNum(mid[i]) || !expanding) continue;
         if(c[i] > mid[i]) votes[i][3] = 1;
         else if(c[i] < mid[i]) votes[i][3] = -1;
      }
   }
   // ---- Agent 5: Donchian breakout state machine
   {
      int prev = 0;
      for(int i = 0; i < n; i++)
      {
         double h20 = NA_V, l20 = NA_V, h10 = NA_V, l10 = NA_V;
         if(i >= InpDonEntry)
         {
            h20 = h[i - InpDonEntry]; l20 = l[i - InpDonEntry];
            for(int k = i - InpDonEntry; k < i; k++) { h20 = MathMax(h20, h[k]); l20 = MathMin(l20, l[k]); }
         }
         if(i >= InpDonExit)
         {
            h10 = h[i - InpDonExit]; l10 = l[i - InpDonExit];
            for(int k = i - InpDonExit; k < i; k++) { h10 = MathMax(h10, h[k]); l10 = MathMin(l10, l[k]); }
         }
         int st = 0;
         if(IsNum(h20) && c[i] > h20) st = 1;
         else if(IsNum(l20) && c[i] < l20) st = -1;
         else if(prev == 1 && !(IsNum(l10) && c[i] < l10)) st = 1;
         else if(prev == -1 && !(IsNum(h10) && c[i] > h10)) st = -1;
         votes[i][4] = st;
         prev = st;
      }
   }
   // ---- Agent 6: Stochastic bias
   {
      double kk[];
      ArrayResize(kk, n);
      for(int i = 0; i < n; i++)
      {
         kk[i] = NA_V;
         if(i >= InpStochK - 1)
         {
            double hh = h[i], ll = l[i];
            for(int k = 0; k < InpStochK; k++) { hh = MathMax(hh, h[i - k]); ll = MathMin(ll, l[i - k]); }
            if(hh != ll) kk[i] = 100.0 * (c[i] - ll) / (hh - ll);
         }
      }
      for(int i = 0; i < n; i++)
      {
         if(!IsNum(kk[i]) || i < InpStochD - 1) continue;
         double s = 0; bool ok = true;
         for(int k = 0; k < InpStochD; k++) { if(!IsNum(kk[i - k])) { ok = false; break; } s += kk[i - k]; }
         if(!ok) continue;
         double dd = s / InpStochD;
         if(kk[i] > dd && kk[i] > 50.0) votes[i][5] = 1;
         else if(kk[i] < dd && kk[i] < 50.0) votes[i][5] = -1;
      }
   }
   // ---- Agent 7: ADX with +DI / -DI
   {
      double plus[], minus[], a[], pE[], mE[], dx[], adx[];
      ArrayResize(plus, n); ArrayResize(minus, n); ArrayResize(dx, n);
      plus[0] = 0; minus[0] = 0;
      for(int i = 1; i < n; i++)
      {
         double upm = h[i] - h[i - 1], dnm = -(l[i] - l[i - 1]);
         plus[i]  = (upm > dnm && upm > 0) ? upm : 0.0;
         minus[i] = (dnm > upm && dnm > 0) ? dnm : 0.0;
      }
      EmaAlpha(tr, a, n, 1.0 / InpAdxPeriod);
      EmaAlpha(plus, pE, n, 1.0 / InpAdxPeriod); EmaAlpha(minus, mE, n, 1.0 / InpAdxPeriod);
      double pdi[], mdi[];
      ArrayResize(pdi, n); ArrayResize(mdi, n);
      double prevdx = 0;
      for(int i = 0; i < n; i++)
      {
         pdi[i] = (a[i] > 0) ? 100.0 * pE[i] / a[i] : 0.0;
         mdi[i] = (a[i] > 0) ? 100.0 * mE[i] / a[i] : 0.0;
         double sum = pdi[i] + mdi[i];
         dx[i] = (sum > 0) ? 100.0 * MathAbs(pdi[i] - mdi[i]) / sum : prevdx;
         prevdx = dx[i];
      }
      EmaAlpha(dx, adx, n, 1.0 / InpAdxPeriod);
      for(int i = 0; i < n; i++)
      {
         if(adx[i] > InpAdxMin && pdi[i] > mdi[i]) votes[i][6] = 1;
         else if(adx[i] > InpAdxMin && mdi[i] > pdi[i]) votes[i][6] = -1;
      }
   }
   // ---- Agent 8: Supertrend
   {
      double a[], ub[], lb[], fub[], flb[];
      int d[];
      EmaAlpha(tr, a, n, 1.0 / InpStPeriod);
      ArrayResize(ub, n); ArrayResize(lb, n); ArrayResize(fub, n); ArrayResize(flb, n); ArrayResize(d, n);
      for(int i = 0; i < n; i++)
      {
         double hl2 = (h[i] + l[i]) / 2.0;
         ub[i] = hl2 + InpStMult * a[i]; lb[i] = hl2 - InpStMult * a[i];
         fub[i] = ub[i]; flb[i] = lb[i]; d[i] = 0;
      }
      for(int i = 1; i < n; i++)
      {
         double hl2 = (h[i] + l[i]) / 2.0;
         fub[i] = (ub[i] < fub[i - 1] || c[i - 1] > fub[i - 1]) ? ub[i] : fub[i - 1];
         flb[i] = (lb[i] > flb[i - 1] || c[i - 1] < flb[i - 1]) ? lb[i] : flb[i - 1];
         if(d[i - 1] == 0)      d[i] = (c[i] >= hl2) ? 1 : -1;
         else if(d[i - 1] == 1) d[i] = (c[i] < flb[i]) ? -1 : 1;
         else                   d[i] = (c[i] > fub[i]) ? 1 : -1;
      }
      for(int i = 0; i < n; i++) votes[i][7] = d[i];
   }
   // ---- Agent 9: time-series momentum
   {
      for(int i = InpMomLong; i < n; i++)
      {
         double r60 = c[i] / c[i - InpMomLong] - 1.0;
         double r20 = c[i] / c[i - InpMomShort] - 1.0;
         double noise = atrOut[i] / c[i] * InpMomNoise;
         if(r60 > noise && r20 > 0) votes[i][8] = 1;
         else if(r60 < -noise && r20 < 0) votes[i][8] = -1;
      }
   }
   // ---- Agent 10: linear-regression slope with R^2
   {
      int w = InpLrPeriod;
      double xm = (w - 1) / 2.0, xv = 0;
      for(int k = 0; k < w; k++) xv += (k - xm) * (k - xm);
      for(int i = w - 1; i < n; i++)
      {
         double ym = 0;
         for(int k = 0; k < w; k++) ym += c[i - w + 1 + k];
         ym /= w;
         double sxy = 0, sst = 0;
         for(int k = 0; k < w; k++)
         {
            double y = c[i - w + 1 + k];
            sxy += (k - xm) * (y - ym);
            sst += (y - ym) * (y - ym);
         }
         double slope = sxy / xv, sse = 0;
         for(int k = 0; k < w; k++)
         {
            double y = c[i - w + 1 + k];
            double fit = ym + slope * (k - xm);
            sse += (y - fit) * (y - fit);
         }
         double r2 = (sst > 0) ? 1.0 - sse / sst : 0.0;
         if(slope > 0 && r2 > InpLrMinR2) votes[i][9] = 1;
         else if(slope < 0 && r2 > InpLrMinR2) votes[i][9] = -1;
      }
   }
}

//+------------------------------------------------------------------+
//| Helpers: strings / json / files                                  |
//+------------------------------------------------------------------+
string Q(string s)
{
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   return "\"" + s + "\"";
}

string BarStr(const datetime t)
{
   string s = TimeToString(t, TIME_DATE | TIME_SECONDS);   // 2026.11.02 10:00:00
   StringReplace(s, ".", "-");
   return s;                                               // 2026-11-02 10:00:00 (same as the Python log)
}

string IsoUtc()
{
   string s = TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS);
   StringReplace(s, ".", "-");
   StringReplace(s, " ", "T");
   return s + "Z";
}

string N(const double v, const int digits = 5) { return DoubleToString(v, digits); }

void AppendEvent(const string json)
{
   if(!InpWriteEvents) return;
   int h = FileOpen(g_dir + "\\events.jsonl", FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE) return;
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, json + "\n");
   FileClose(h);
}

//+------------------------------------------------------------------+
//| Paper position (dry-run) kept in terminal global variables       |
//+------------------------------------------------------------------+
bool PaperOpenNow() { return GlobalVariableCheck(g_pp + "open") && GlobalVariableGet(g_pp + "open") > 0.5; }

void PaperOpen(const bool buy, const double lots, const double entry, const double sl, const double tp,
               const double riskUsd, const double riskPct, const datetime barTime)
{
   GlobalVariableSet(g_pp + "open", 1);
   GlobalVariableSet(g_pp + "dir", buy ? 1 : -1);
   GlobalVariableSet(g_pp + "lots", lots);
   GlobalVariableSet(g_pp + "entry", entry);
   GlobalVariableSet(g_pp + "sl", sl);
   GlobalVariableSet(g_pp + "tp", tp);
   GlobalVariableSet(g_pp + "riskusd", riskUsd);
   GlobalVariableSet(g_pp + "riskpct", riskPct);
   GlobalVariableSet(g_pp + "bar", (double)barTime);
}

void PaperUpdate(const double high, const double low, const datetime closedBar)
{
   if(!PaperOpenNow()) return;
   double d = GlobalVariableGet(g_pp + "dir");
   double lots = GlobalVariableGet(g_pp + "lots"), entry = GlobalVariableGet(g_pp + "entry");
   double sl = GlobalVariableGet(g_pp + "sl"), tp = GlobalVariableGet(g_pp + "tp");
   datetime opened = (datetime)GlobalVariableGet(g_pp + "bar");
   if(closedBar <= opened) return;                       // only candles that closed after the signal candle
   bool hitSl = (d > 0) ? (low <= sl) : (high >= sl);
   bool hitTp = (d > 0) ? (high >= tp) : (low <= tp);
   if(!hitSl && !hitTp) return;
   double exitPx = hitSl ? sl : tp;                      // stop first when both are touched
   double pnl = 0;
   if(!OrderCalcProfit(d > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, lots, entry, exitPx, pnl))
      pnl = d * (exitPx - entry) / SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE)
            * SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE) * lots;
   GlobalVariableSet(g_pp + "open", 0);
   PrintFormat("PAPER trade closed: %s %.2f lots %.2f -> %.2f (%s) pnl %.2f", d > 0 ? "BUY" : "SELL", lots, entry, exitPx, hitSl ? "SL" : "TP", pnl);
   AppendEvent("{\"ts\":" + Q(IsoUtc()) + ",\"type\":\"paper_close\",\"dir\":" + Q(d > 0 ? "BUY" : "SELL") +
               ",\"lots\":" + N(lots, 2) + ",\"entry\":" + N(entry) + ",\"exit\":" + N(exitPx) +
               ",\"reason\":" + Q(hitSl ? "SL" : "TP") + ",\"pnl\":" + N(pnl, 2) +
               ",\"closed_bar\":" + Q(BarStr(closedBar)) + ",\"opened_bar\":" + Q(BarStr(opened)) + "}");
}

//+------------------------------------------------------------------+
//| Checks                                                           |
//+------------------------------------------------------------------+
bool HasPosition()
{
   if(InpMode == RUN_DRY && PaperOpenNow()) return true;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol && (ulong)PositionGetInteger(POSITION_MAGIC) == InpMagic) return true;
   }
   return false;
}

bool DailyLossHit()
{
   if(InpMaxDailyLossPct <= 0) return false;
   datetime now = TimeCurrent();
   MqlDateTime dt;
   TimeToStruct(now, dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   datetime start = StructToTime(dt);
   double pnl = 0;
   if(HistorySelect(start, now + 60))
   {
      for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
      {
         ulong tk = HistoryDealGetTicket(i);
         if(tk == 0) continue;
         if((ulong)HistoryDealGetInteger(tk, DEAL_MAGIC) != InpMagic) continue;
         pnl += HistoryDealGetDouble(tk, DEAL_PROFIT) + HistoryDealGetDouble(tk, DEAL_COMMISSION)
              + HistoryDealGetDouble(tk, DEAL_SWAP) + HistoryDealGetDouble(tk, DEAL_FEE);
      }
   }
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) == InpMagic)
         pnl += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
   }
   return pnl <= -InpMaxDailyLossPct / 100.0 * AccountInfoDouble(ACCOUNT_BALANCE);
}

// +1 / -1 / 0 = direction of the higher timeframe; 2 = candles unavailable
int HtfDirection()
{
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int n = CopyRates(_Symbol, InpHtf, 0, InpHistoryBars, r);
   if(n < 400) return 2;
   int hv[][10];
   double ha[];
   ComputeVotes(r, n, hv, ha);
   int idx = n - 2, buys = 0, sells = 0;                // last CLOSED higher-timeframe candle
   for(int k = 0; k < 10; k++)
   {
      if(!g_en[k]) continue;
      if(hv[idx][k] == 1) buys++;
      else if(hv[idx][k] == -1) sells++;
   }
   if(buys >= InpHtfMin && buys > sells) return 1;
   if(sells >= InpHtfMin && sells > buys) return -1;
   return 0;
}

bool NewsBlocked(string &why)
{
   if(!InpUseNews) return false;
   if(MQLInfoInteger(MQL_TESTER)) return false;          // the calendar is not available in the Strategy Tester
   datetime now = TimeTradeServer();
   MqlCalendarValue vals[];
   ResetLastError();
   bool ok = CalendarValueHistory(vals, now - InpNewsAfter * 60, now + InpNewsBefore * 60, NULL, InpNewsCurrency);
   int err = GetLastError();
   if(!ok && err != 0 && err != 5402)                     // 5402 = no events found
   {
      why = "news calendar unavailable (error " + IntegerToString(err) + ") - failing closed";
      return true;
   }
   for(int i = 0; i < ArraySize(vals); i++)
   {
      MqlCalendarEvent ev;
      if(!CalendarEventById(vals[i].event_id, ev)) continue;
      if((int)ev.importance >= (int)InpNewsMinImportance)
      {
         why = "news blackout: " + ev.name + " at " + TimeToString(vals[i].time, TIME_DATE | TIME_MINUTES);
         return true;
      }
   }
   return false;
}

double NormLots(const double lots, const bool roundNearest)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double mn = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), mx = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double v = roundNearest ? MathRound(lots / step) * step : MathFloor(lots / step + 1e-9) * step;
   v = MathMin(mx, MathMax(0.0, v));
   int digits = (int)MathMax(0.0, MathRound(-MathLog10(step)));
   v = NormalizeDouble(v, digits);
   if(v < mn) return 0.0;
   return v;
}

double LossPerLot(const bool buy, const double price, const double slDist)
{
   double prof = 0;
   double slPx = buy ? price - slDist : price + slDist;
   if(OrderCalcProfit(buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, 1.0, price, slPx, prof)) return MathAbs(prof);
   return slDist / SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE) * SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
}

//+------------------------------------------------------------------+
//| Try to open a trade for a confirmed committee signal             |
//+------------------------------------------------------------------+
string TryTrade(const string action, const double atr, const datetime barTime)
{
   if(HasPosition()) return action + " signal ignored: position already open";
   if(DailyLossHit()) return action + " signal ignored: daily loss limit reached";
   const bool buy = (action == "BUY");
   if(InpUseHtf)
   {
      int d = HtfDirection();
      if(d == 2) return action + " signal ignored: higher-timeframe candles unavailable";
      if(d != (buy ? 1 : -1)) return action + " signal ignored: " + EnumToString(InpHtf) + " does not confirm (" + IntegerToString(d) + ")";
   }
   string why = "";
   if(NewsBlocked(why)) return action + " signal ignored: " + why;
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK), bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(InpMaxSpreadPoints > 0 && (ask - bid) / point > InpMaxSpreadPoints)
      return action + " signal ignored: spread too wide";
   double price = buy ? ask : bid;
   double minDist = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;
   double slD = MathMax(InpSlAtr * atr, minDist), tpD = MathMax(InpTpAtr * atr, minDist);
   double sl = NormalizeDouble(buy ? price - slD : price + slD, _Digits);
   double tp = NormalizeDouble(buy ? price + tpD : price - tpD, _Digits);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double lpl = LossPerLot(buy, price, slD);
   double lots;
   if(InpLotMode == LOTS_FIXED) lots = NormLots(InpLots, true);
   else                         lots = NormLots(equity * InpRiskPct / 100.0 / lpl, false);
   if(lots <= 0) return action + " skipped: lot size below the broker minimum";
   double riskMoney = lots * lpl, riskPct = 100.0 * riskMoney / equity;
   if(InpLotMode == LOTS_FIXED && InpMaxRiskPct > 0 && riskPct > InpMaxRiskPct)
      return StringFormat("%s skipped: risk %.1f%% of equity exceeds the %.0f%% cap", action, riskPct, InpMaxRiskPct);
   double margin = 0;
   if(OrderCalcMargin(buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, lots, price, margin) && margin > AccountInfoDouble(ACCOUNT_MARGIN_FREE))
      return StringFormat("%s skipped: needs %.0f margin, only %.0f free", action, margin, AccountInfoDouble(ACCOUNT_MARGIN_FREE));

   string desc = StringFormat("%s %.2f %s @ %s SL %s TP %s [risk $%.0f = %.1f%% of equity]", action, lots, _Symbol,
                              DoubleToString(price, _Digits), DoubleToString(sl, _Digits), DoubleToString(tp, _Digits), riskMoney, riskPct);
   string mode = "dry";
   if(InpMode == RUN_DEMO)
   {
      mode = "live";
      bool sent = buy ? g_trade.Buy(lots, _Symbol, 0.0, sl, tp, "10-agent consensus")
                      : g_trade.Sell(lots, _Symbol, 0.0, sl, tp, "10-agent consensus");
      uint rc = g_trade.ResultRetcode();
      if(!sent || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_PLACED))
         return "ORDER FAILED retcode " + IntegerToString((int)rc) + " " + g_trade.ResultRetcodeDescription();
      desc = "PLACED " + desc;
   }
   else
   {
      PaperOpen(buy, lots, price, sl, tp, riskMoney, riskPct, barTime);
      desc = "DRY-RUN " + desc;
   }
   AppendEvent("{\"ts\":" + Q(IsoUtc()) + ",\"type\":\"signal\",\"mode\":" + Q(mode) + ",\"dir\":" + Q(action) +
               ",\"lots\":" + N(lots, 2) + ",\"entry\":" + N(price) + ",\"sl\":" + N(sl) + ",\"tp\":" + N(tp) +
               ",\"risk_usd\":" + N(riskMoney, 2) + ",\"risk_pct\":" + N(riskPct, 2) + ",\"opened_bar\":" + Q(BarStr(barTime)) + "}");
   return desc;
}

//+------------------------------------------------------------------+
//| Status file (for the portal) and on-chart text                   |
//+------------------------------------------------------------------+
void WriteStatus()
{
   if(!InpWriteEvents) return;
   int need = MathMin((int)InpVotes, g_enabled);
   double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE), ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   string paper = "null";
   if(PaperOpenNow())
      paper = "{\"dir\":" + Q(GlobalVariableGet(g_pp + "dir") > 0 ? "BUY" : "SELL") + ",\"lots\":" + N(GlobalVariableGet(g_pp + "lots"), 2) +
              ",\"entry\":" + N(GlobalVariableGet(g_pp + "entry")) + ",\"sl\":" + N(GlobalVariableGet(g_pp + "sl")) +
              ",\"tp\":" + N(GlobalVariableGet(g_pp + "tp")) + ",\"risk_usd\":" + N(GlobalVariableGet(g_pp + "riskusd"), 2) +
              ",\"risk_pct\":" + N(GlobalVariableGet(g_pp + "riskpct"), 2) +
              ",\"opened_bar\":" + Q(BarStr((datetime)GlobalVariableGet(g_pp + "bar"))) + "}";
   string lots = (InpLotMode == LOTS_FIXED) ? N(InpLots, 2) : "null";
   string json = "{\"updated\":" + IntegerToString((long)TimeGMT()) +
      ",\"mode\":" + Q(InpMode == RUN_DRY ? "DRY RUN (EA)" : "LIVE ORDERS (demo, EA)") + ",\"symbol\":" + Q(_Symbol) +
      ",\"last_bar\":" + Q(g_lastBarStr) + ",\"last_status\":" + Q(g_status) +
      ",\"settings\":{\"timeframe\":" + Q(EnumToString(InpTF)) + ",\"votes\":" + IntegerToString(need) +
      ",\"agents\":" + IntegerToString(g_enabled) + ",\"sl_atr\":" + N(InpSlAtr, 2) + ",\"tp_atr\":" + N(InpTpAtr, 2) +
      ",\"htf_min\":" + IntegerToString(InpUseHtf ? InpHtfMin : 0) + ",\"news\":" + (InpUseNews ? "true" : "false") +
      ",\"lots\":" + lots + ",\"risk\":" + N(InpRiskPct / 100.0, 4) + ",\"max_risk\":" + N(InpMaxRiskPct / 100.0, 4) +
      ",\"paused\":false,\"magic\":" + IntegerToString((long)InpMagic) + "}" +
      ",\"server_offset_h\":" + N((double)(TimeTradeServer() - TimeGMT()) / 3600.0, 1) +
      ",\"balance\":" + N(AccountInfoDouble(ACCOUNT_BALANCE), 2) + ",\"equity\":" + N(AccountInfoDouble(ACCOUNT_EQUITY), 2) +
      ",\"usd_per_dollar_per_lot\":" + N(ts > 0 ? tv / ts : 100.0, 2) +
      ",\"paper_open\":" + paper + ",\"paper_start_balance\":" + N(GlobalVariableCheck(g_pp + "startbal") ? GlobalVariableGet(g_pp + "startbal") : AccountInfoDouble(ACCOUNT_BALANCE), 2) + "}";
   int h = FileOpen(g_dir + "\\status.json", FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ);
   if(h == INVALID_HANDLE) return;
   FileWriteString(h, json);
   FileClose(h);
}

void SetStatus(const string s)
{
   g_status = s;
   WriteStatus();
   Comment("XAUUSD consensus EA  [", InpMode == RUN_DRY ? "DRY RUN" : "DEMO ORDERS", "]\n",
           "Need ", MathMin((int)InpVotes, g_enabled), " of ", g_enabled, " agents,  stop ", DoubleToString(InpSlAtr, 1),
           "x ATR,  target ", DoubleToString(InpTpAtr, 1), "x ATR\n", "Last candle: ", g_lastBarStr, "\n", "Status: ", g_status);
}

//+------------------------------------------------------------------+
//| New closed candle                                                |
//+------------------------------------------------------------------+
void OnNewBar()
{
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int n = CopyRates(_Symbol, InpTF, 0, InpHistoryBars, r);
   if(n < 400) { SetStatus("not enough candles (" + IntegerToString(n) + ")"); return; }
   int idx = n - 2;                                      // last CLOSED candle
   datetime barTime = r[idx].time;
   g_lastBarStr = BarStr(barTime);
   int psec = PeriodSeconds(InpTF);
   if(TimeCurrent() - (barTime + psec) > 3 * psec)
   {
      SetStatus("stale candle - skipped");
      AppendEvent("{\"ts\":" + Q(IsoUtc()) + ",\"type\":\"skip\",\"bar\":" + Q(g_lastBarStr) + ",\"status\":" + Q(g_status) + "}");
      return;
   }
   int votes[][10];
   double atr[];
   ComputeVotes(r, n, votes, atr);

   if(InpMode == RUN_DRY) PaperUpdate(r[idx].high, r[idx].low, barTime);

   int buys = 0, sells = 0;
   for(int k = 0; k < 10; k++)
   {
      if(!g_en[k]) continue;
      if(votes[idx][k] == 1) buys++;
      else if(votes[idx][k] == -1) sells++;
   }
   int need = MathMin((int)InpVotes, g_enabled);
   string action = "HOLD";
   if(buys >= need) action = "BUY";
   else if(sells >= need) action = "SELL";
   string status = action;
   if(action != "HOLD") status = TryTrade(action, atr[idx], barTime);

   string vj = "";
   for(int k = 0; k < 10; k++)
      vj += (k > 0 ? "," : "") + Q(g_names[k]) + ":" + IntegerToString(g_en[k] ? votes[idx][k] : 0);
   AppendEvent("{\"ts\":" + Q(IsoUtc()) + ",\"type\":\"candle\",\"bar\":" + Q(g_lastBarStr) + ",\"action\":" + Q(action) +
               ",\"buy\":" + IntegerToString(buys) + ",\"sell\":" + IntegerToString(sells) +
               ",\"neutral\":" + IntegerToString(g_enabled - buys - sells) + ",\"need\":" + IntegerToString(need) +
               ",\"votes\":{" + vj + "},\"status\":" + Q(status) + "}");
   PrintFormat("%s %s: %s (BUY %d / SELL %d, need %d) -> %s", _Symbol, g_lastBarStr, action, buys, sells, need, status);
   SetStatus(status);
}

//+------------------------------------------------------------------+
int OnInit()
{
   g_en[0] = InpA1; g_en[1] = InpA2; g_en[2] = InpA3; g_en[3] = InpA4; g_en[4] = InpA5;
   g_en[5] = InpA6; g_en[6] = InpA7; g_en[7] = InpA8; g_en[8] = InpA9; g_en[9] = InpA10;
   g_enabled = 0;
   for(int k = 0; k < 10; k++) if(g_en[k]) g_enabled++;
   if(g_enabled < (int)InpVotes)
   {
      Print("Not enough agents enabled: ", g_enabled, " enabled but ", (int)InpVotes, " votes required.");
      return INIT_PARAMETERS_INCORRECT;
   }
   if(InpMode == RUN_DEMO)
   {
      if((ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE) != ACCOUNT_TRADE_MODE_DEMO)
      {
         Print("REFUSING to trade: this is not a DEMO account. Use RUN_DRY or log in to a demo account.");
         return INIT_FAILED;
      }
      if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))
         Print("WARNING: algorithmic trading is switched off (toolbar button / EA properties). Orders will fail.");
   }
   if(InpHistoryBars < 600) { Print("InpHistoryBars should be at least 600."); return INIT_PARAMETERS_INCORRECT; }
   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(InpDeviation);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_dir = MQLInfoInteger(MQL_TESTER) ? "xauusd_ea_tester" : "xauusd_ea";
   FolderCreate(g_dir, FILE_COMMON);
   g_pp = "XEA_" + IntegerToString((long)InpMagic) + "_" + _Symbol + "_";
   if(!GlobalVariableCheck(g_pp + "startbal")) GlobalVariableSet(g_pp + "startbal", AccountInfoDouble(ACCOUNT_BALANCE));
   if(MQLInfoInteger(MQL_TESTER)) { GlobalVariableSet(g_pp + "open", 0); }   // fresh paper book for every test
   g_lastBar = InpActOnStart ? 0 : iTime(_Symbol, InpTF, 0);
   g_lastBarStr = BarStr(iTime(_Symbol, InpTF, 1));
   EventSetTimer(15);
   if(InpUseNews && !MQLInfoInteger(MQL_TESTER))
   {
      MqlCalendarValue v[];
      int cnt = CalendarValueHistory(v, TimeTradeServer(), TimeTradeServer() + 7 * 86400, NULL, InpNewsCurrency) ? ArraySize(v) : 0;
      PrintFormat("News calendar self-check: %d %s events in the next 7 days (0 may mean the calendar is not loaded).", cnt, InpNewsCurrency);
   }
   PrintFormat("XAUUSD consensus EA started: %s, need %d of %d agents, mode %s", _Symbol, MathMin((int)InpVotes, g_enabled), g_enabled,
               InpMode == RUN_DRY ? "DRY RUN" : "DEMO ORDERS");
   SetStatus("started - waiting for the next candle");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   Comment("");
}

void OnTimer() { WriteStatus(); }

void OnTick()
{
   datetime bt = iTime(_Symbol, InpTF, 0);
   if(bt == 0 || bt == g_lastBar) return;
   g_lastBar = bt;
   OnNewBar();
}
