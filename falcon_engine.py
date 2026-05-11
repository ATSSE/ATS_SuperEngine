"""
🦅 FALCON HUNTER ENGINE
Strategi Falcon — Sharia Stock Hunter
Standalone engine, zero dependency on ATS scan_core.
Translated from Pine Script v6 falcon_strategy.pine 1:1

Setup:
  🟢 BREAKOUT : close > resistance 10D + vol > 1.8x avg20
  🔵 BOUNCE   : close near support 20D (≤4%) + vol < 0.85x avg20

Author: ATS SuperEngine team
"""

from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

LOG = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# DEFAULT WATCHLIST (30 ticker — mirror Pine Script)
# ─────────────────────────────────────────────────────────────
FALCON_DEFAULT_WATCHLIST: list[str] = [
    "ADRO", "ANTM", "BRIS", "BRPT", "ESSA",
    "EXCL", "ICBP", "INCO", "INDF", "INTP",
    "KLBF", "MDKA", "MYOR", "PGAS", "PTBA",
    "SMGR", "TLKM", "TPIA", "UNTR", "UNVR",
    "AKRA", "AMRT", "CPIN", "HRUM", "ITMG",
    "JPFA", "MAPI", "SIDO", "TINS", "MIKA",
]

# ─────────────────────────────────────────────────────────────
# PARAMETER DEFAULTS (mirror Pine Script inputs)
# ─────────────────────────────────────────────────────────────
@dataclass
class FalconParams:
    upper_shadow_max  : float = 0.25   # max upper shadow ratio
    body_min_ratio    : float = 0.50   # min body ratio
    vol_breakout_mult : float = 1.8    # breakout volume multiplier
    vol_bounce_max    : float = 0.85   # bounce max volume ratio
    rsi_max           : int   = 70     # RSI ceiling
    max_gap_pct       : float = 3.0    # max gap %
    breakout_lb       : int   = 10     # resistance lookback days
    support_lb        : int   = 20     # support lookback days
    support_dist_max  : float = 0.04   # max dist from support (4%)
    use_trend_filter  : bool  = True   # MA20/50/200 trend filter
    risk_pct          : float = 0.01   # risk per trade (1%)
    atr_sl_mult       : float = 1.5    # SL = entry - mult × ATR14
    rr_t1             : float = 1.0    # T1 in R-multiple
    rr_t2             : float = 2.0    # T2 in R-multiple
    max_hold_days     : int   = 5      # time stop

# ─────────────────────────────────────────────────────────────
# RESULT DATACLASS
# ─────────────────────────────────────────────────────────────
@dataclass
class FalconResult:
    ticker        : str
    setup         : str          # "BRK" | "BNC" | "-"
    close         : float = 0.0
    rsi           : float = 0.0
    vol_ratio     : float = 0.0
    upper_shadow  : float = 0.0  # as % of range
    body_ratio    : float = 0.0
    trend_score   : float = 0.0
    falcon_score  : float = 0.0
    entry         : float = 0.0
    sl            : float = 0.0
    t1            : float = 0.0
    t2            : float = 0.0
    lot           : int   = 0
    risk_rp       : float = 0.0  # risk in Rupiah
    gap_pct       : float = 0.0
    resistance    : float = 0.0
    support       : float = 0.0
    ma20          : float = 0.0
    ma50          : float = 0.0
    ma200         : float = 0.0
    error         : str   = ""

# ─────────────────────────────────────────────────────────────
# IHSG STATUS
# ─────────────────────────────────────────────────────────────
def get_ihsg_status() -> tuple[str, float]:
    """
    Return (status, ihsg_score).
    status : "BULLISH" | "NEUTRAL" | "BEARISH"
    score  : 1.0 / 0.5 / 0.2
    """
    try:
        df = yf.download("^JKSE", period="60d", interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or len(df) < 51:
            return "NEUTRAL", 0.5

        c   = df["Close"].squeeze()
        ma20 = float(c.rolling(20).mean().iloc[-1])
        ma50 = float(c.rolling(50).mean().iloc[-1])
        last = float(c.iloc[-1])

        flags = (1 if last > ma20 else 0) + (1 if last > ma50 else 0)
        if flags == 2:
            return "BULLISH", 1.0
        elif flags == 1:
            return "NEUTRAL", 0.5
        else:
            return "BEARISH", 0.2
    except Exception as e:
        LOG.warning(f"IHSG status error: {e}")
        return "NEUTRAL", 0.5


# ─────────────────────────────────────────────────────────────
# SINGLE TICKER SCAN
# ─────────────────────────────────────────────────────────────
def _scan_ticker(
    ticker: str,
    ihsg_score: float,
    balance: float,
    p: FalconParams,
) -> FalconResult:
    """Scan satu ticker, return FalconResult."""
    tkr_jk = ticker if ticker.endswith(".JK") else f"{ticker}.JK"
    res = FalconResult(ticker=ticker, setup="-")

    try:
        # ── Download data ───────────────────────────────────
        df = yf.download(tkr_jk, period="220d", interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or len(df) < 60:
            res.error = "data terlalu sedikit"
            return res

        df = df.copy()
        for col in ["Open","High","Low","Close","Volume"]:
            df[col] = pd.to_numeric(df[col].squeeze(), errors="coerce")
        df.dropna(subset=["Open","High","Low","Close","Volume"], inplace=True)
        if len(df) < 55:
            res.error = "data setelah clean terlalu sedikit"
            return res

        # ── Last bar values ─────────────────────────────────
        c     = float(df["Close"].iloc[-1])
        o_    = float(df["Open"].iloc[-1])
        h     = float(df["High"].iloc[-1])
        l     = float(df["Low"].iloc[-1])
        vol   = float(df["Volume"].iloc[-1])
        prev_c= float(df["Close"].iloc[-2])

        rng   = h - l
        up_s  = ((h - c) / rng) if rng > 0 else 1.0
        body  = (abs(c - o_) / rng) if rng > 0 else 0.0
        gap_pct = ((o_ - prev_c) / prev_c * 100) if prev_c > 0 else 0.0

        # ── Indicators ──────────────────────────────────────
        cl = df["Close"]
        vl = df["Volume"]

        ma20_s  = float(cl.rolling(20).mean().iloc[-1])
        ma50_s  = float(cl.rolling(50).mean().iloc[-1])
        ma200_s = float(cl.rolling(200).mean().iloc[-1])
        vol_avg = float(vl.rolling(20).mean().iloc[-1])
        vol_r   = (vol / vol_avg) if vol_avg > 0 else 0.0

        # RSI 14
        delta  = cl.diff()
        gain   = delta.clip(lower=0).rolling(14).mean()
        loss   = (-delta.clip(upper=0)).rolling(14).mean()
        rs     = gain / loss.replace(0, np.nan)
        rsi_v  = float((100 - 100 / (1 + rs)).iloc[-1])

        # ATR 14
        prev_cl = cl.shift(1)
        tr = pd.concat([
            df["High"] - df["Low"],
            (df["High"] - prev_cl).abs(),
            (df["Low"]  - prev_cl).abs(),
        ], axis=1).max(axis=1)
        atr14 = float(tr.rolling(14).mean().iloc[-1])

        # Resistance & Support (exclude last bar — mirror Pine [1])
        resistance = float(df["High"].iloc[-(p.breakout_lb+1):-1].max())
        support    = float(df["Low"].iloc[-(p.support_lb+1):-1].min())

        # Trend score (mirror Pine ts formula)
        ts = ((0.40 if c > ma20_s  else 0.0) +
              (0.35 if c > ma50_s  else 0.0) +
              (0.25 if c > ma200_s else 0.0))
        trend_ok = (not p.use_trend_filter) or (ts >= 0.5)

        # ── Filter checks ───────────────────────────────────
        candle_ok = (c > o_) and (up_s <= p.upper_shadow_max) and (body >= p.body_min_ratio)
        gap_ok    = abs(gap_pct) <= p.max_gap_pct
        rsi_ok    = rsi_v <= p.rsi_max

        filt = candle_ok and gap_ok and rsi_ok and trend_ok

        # ── Setup detection ─────────────────────────────────
        is_brk = (c > resistance) and (vol_r >= p.vol_breakout_mult)
        dist_sup = ((c - support) / support) if support > 0 else 1.0
        is_bnc = (dist_sup <= p.support_dist_max and
                  vol_r <= p.vol_bounce_max and
                  c > prev_c and
                  l <= support * 1.02)

        if filt and is_brk:
            setup = "BRK"
        elif filt and is_bnc:
            setup = "BNC"
        else:
            setup = "-"

        # ── Falcon Score (mirror Pine formula) ──────────────
        falcon_score = 0.0
        if filt:
            falcon_score = (ts * 0.30 +
                            (1.0 - up_s) * 0.25 +
                            min(vol_r / 2.0, 1.0) * 0.20 +
                            ihsg_score * 0.25)

        # ── Risk management (dari SOP Falcon) ──────────────
        # SL = max(swing_low_5 × 0.995, entry - 1.5×ATR14)
        swing_low_5 = float(df["Low"].iloc[-6:-1].min())
        sl_swing    = swing_low_5 * 0.995
        sl_atr      = c - p.atr_sl_mult * atr14
        sl          = max(sl_swing, sl_atr)
        sl          = round(sl / 10) * 10   # round ke fraksi IDX

        risk_per_lot = (c - sl) * 100       # 1 lot = 100 lembar
        risk_rp      = balance * p.risk_pct

        lot = 0
        if risk_per_lot > 0:
            lot = max(1, int(risk_rp / risk_per_lot))

        t1 = round((c + (c - sl) * p.rr_t1) / 10) * 10
        t2 = round((c + (c - sl) * p.rr_t2) / 10) * 10

        # ── Populate result ─────────────────────────────────
        res.setup        = setup
        res.close        = round(c, 0)
        res.rsi          = round(rsi_v, 1)
        res.vol_ratio    = round(vol_r, 2)
        res.upper_shadow = round(up_s * 100, 1)
        res.body_ratio   = round(body * 100, 1)
        res.trend_score  = round(ts, 2)
        res.falcon_score = round(falcon_score, 3)
        res.entry        = round(c, 0)
        res.sl           = round(sl, 0)
        res.t1           = t1
        res.t2           = t2
        res.lot          = lot
        res.risk_rp      = round(risk_rp, 0)
        res.gap_pct      = round(gap_pct, 2)
        res.resistance   = round(resistance, 0)
        res.support      = round(support, 0)
        res.ma20         = round(ma20_s, 0)
        res.ma50         = round(ma50_s, 0)
        res.ma200        = round(ma200_s, 0)

    except Exception as e:
        res.error = str(e)[:80]
        LOG.warning(f"Falcon scan {ticker} error: {e}")

    return res


# ─────────────────────────────────────────────────────────────
# FULL FALCON SCAN
# ─────────────────────────────────────────────────────────────
def run_falcon_scan(
    watchlist  : list[str],
    balance    : float,
    params     : Optional[FalconParams] = None,
    progress_cb= None,   # callable(i, n, ticker) — untuk Streamlit progress
) -> tuple[str, float, list[FalconResult]]:
    """
    Scan semua ticker di watchlist.
    Return: (ihsg_status, ihsg_score, list[FalconResult] sorted by falcon_score desc)
    """
    if params is None:
        params = FalconParams()

    ihsg_status, ihsg_score = get_ihsg_status()
    results: list[FalconResult] = []

    n = len(watchlist)
    for i, ticker in enumerate(watchlist):
        if progress_cb:
            progress_cb(i, n, ticker)
        res = _scan_ticker(ticker, ihsg_score, balance, params)
        results.append(res)
        time.sleep(0.05)   # polite delay

    if progress_cb:
        progress_cb(n, n, "selesai")

    # Sort: setup first (BRK > BNC > -), then falcon_score desc
    order = {"BRK": 2, "BNC": 1, "-": 0}
    results.sort(key=lambda r: (order.get(r.setup, 0), r.falcon_score), reverse=True)

    return ihsg_status, ihsg_score, results


# ─────────────────────────────────────────────────────────────
# TELEGRAM FORMAT
# ─────────────────────────────────────────────────────────────
def format_falcon_telegram(
    results    : list[FalconResult],
    ihsg_status: str,
    balance    : float,
) -> str:
    """Format Telegram message untuk sinyal Falcon."""
    from datetime import datetime
    import pytz
    WIB = pytz.timezone("Asia/Jakarta")
    ts  = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")

    setup_results = [r for r in results if r.setup != "-"]
    if not setup_results:
        return ""

    ihsg_em = {"BULLISH": "🟢", "NEUTRAL": "🟡", "BEARISH": "🔴"}.get(ihsg_status, "⬜")
    lines = [
        f"🦅 *FALCON ALERT — Setup Baru*",
        f"{'─' * 28}",
        f"{ihsg_em} IHSG: {ihsg_status}  |  ⏰ {ts}",
        f"{'─' * 28}",
    ]

    for r in setup_results:
        em = "🟢" if r.setup == "BRK" else "🔵"
        lines.append(
            f"{em} *{r.ticker}* [{r.setup}] @ {int(r.close):,}\n"
            f"   Entry: {int(r.entry):,} | SL: {int(r.sl):,} | "
            f"T1: {int(r.t1):,} | T2: {int(r.t2):,}\n"
            f"   Vol: {r.vol_ratio:.1f}x | RSI: {r.rsi} | "
            f"Score: {r.falcon_score:.2f} | Lot: {r.lot}"
        )

    lines.append(f"{'─' * 28}")
    lines.append("_Falcon — sabar, presisi, disiplin_ 🦅")
    return "\n".join(lines)
