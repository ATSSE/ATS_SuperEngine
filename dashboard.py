"""
ATS SuperEngine V3.0 — Saham Syariah ISSI Scanner
Perbaikan dari V2.1:
  - VWAP dihitung rolling 20 hari (bukan kumulatif 6 bulan)
  - Cybernetic params + signal_lock persisten ke JSON
  - Bug active_trades BUY logic diperbaiki
  - Validasi & skip ticker dengan data tidak lengkap
  - Target resistance berbasis Pivot Point + Fibonacci + swing high
  - RSI menggunakan Wilder's smoothing (lebih akurat)
  - Bandar detection distribusi logic diperketat
  - Tambahan: Position sizing berbasis ATR (volatility-adjusted)
  - Tambahan: Drawdown & equity curve di Report tab
  - Tambahan: Sektor filter — hanya sektor dengan momentum positif
"""

import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, date
import time
import os
import json
import requests
import plotly.express as px
import plotly.graph_objects as go
from collections import defaultdict

# ============================================================
# KONFIGURASI
# ============================================================
FINNHUB_API_KEY = r"d7j0adhr01qn2qavlnn0d7j0adhr01qn2qavlnng"
TELEGRAM_TOKEN  = "8515068517:AAGqJnRX-9ccCKN9jcopGyi9tVojZyHZDYo"
TELEGRAM_CHAT   = "936786417"
STATE_FILE      = "ats_state.json"   # persistensi cybernetic + signal lock
JOURNAL_FILE    = "journal.csv"
ACTIVE_FILE     = "active_trades.csv"

# ============================================================
# TELEGRAM
# ============================================================
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT, "text": message}, timeout=5)
    except Exception:
        pass

# ============================================================
# PERSISTENSI STATE (cybernetic params + signal lock)
# ============================================================
DEFAULT_CYBER = {
    "min_score": 70,
    "execute_now_threshold": 85,
    "min_rr": 1.8,
    "last_adjust_date": None,
    "adjustment_history": []
}

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"cybernetic_params": DEFAULT_CYBER.copy(), "signal_lock": {}}

def save_state():
    data = {
        "cybernetic_params": st.session_state.cybernetic_params,
        "signal_lock": st.session_state.signal_lock,
    }
    # Convert date objects ke string agar JSON serializable
    cp = data["cybernetic_params"].copy()
    if isinstance(cp.get("last_adjust_date"), (date, datetime)):
        cp["last_adjust_date"] = str(cp["last_adjust_date"])
    data["cybernetic_params"] = cp
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

# ============================================================
# FINNHUB
# ============================================================
def finnhub_quote(ticker_jk: str) -> dict | None:
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol={ticker_jk}&token={FINNHUB_API_KEY}"
        r = requests.get(url, timeout=4)
        if r.status_code == 200:
            d = r.json()
            if d.get("c"):
                return {"current": d["c"]}
    except Exception:
        pass
    return None

# ============================================================
# HELPER FORMAT
# ============================================================
def idr(x) -> str:
    try:
        return f"{int(x):,}".replace(",", ".")
    except Exception:
        return str(x)

# ============================================================
# RSI — Wilder's Smoothing (lebih akurat dari simple rolling mean)
# ============================================================
def calculate_rsi(df: pd.DataFrame, period: int = 14) -> float:
    close = df["Close"].squeeze()
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    # Wilder's smoothing
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs  = avg_gain / avg_loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    val = float(rsi.iloc[-1])
    return val if not np.isnan(val) else 50.0

def rsi_gate(df: pd.DataFrame, rsi_min=42, rsi_max=72) -> tuple[bool, float]:
    rsi = calculate_rsi(df)
    return rsi_min <= rsi <= rsi_max, rsi

# ============================================================
# EMA & TREND
# ============================================================
def calculate_ema(df: pd.DataFrame, period: int = 50) -> float:
    close = df["Close"].squeeze()
    ema = close.ewm(span=period, adjust=False).mean()
    return float(ema.iloc[-1])

def ema_trend_filter(df: pd.DataFrame, period: int = 50) -> tuple[bool, float, float]:
    last    = float(df["Close"].squeeze().iloc[-1])
    ema_val = calculate_ema(df, period)
    return last >= ema_val * 0.995, last, ema_val

# ============================================================
# ATR
# ============================================================
def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()
    close = df["Close"].squeeze()
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()   # Wilder's ATR
    val = float(atr.iloc[-1])
    return val if not np.isnan(val) else 0.0

# ============================================================
# STOP LOSS
# ============================================================
def calculate_sl_atr(entry: float, atr: float, multiplier: float = 1.5) -> float:
    sl = entry - multiplier * atr
    return max(sl, entry * 0.93)   # floor 7% loss

# ============================================================
# TARGET — Pivot Point + Fibonacci + Swing High (lebih akurat)
# ============================================================
def find_target(df: pd.DataFrame, entry: float) -> float:
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()
    close = df["Close"].squeeze()

    # Pivot Point (PP) klasik berbasis 5 hari terakhir
    pp = (high.tail(5).mean() + low.tail(5).mean() + close.tail(5).mean()) / 3
    r1 = 2 * pp - low.tail(5).mean()   # Resistance 1
    r2 = pp + (high.tail(5).mean() - low.tail(5).mean())  # Resistance 2

    # Fibonacci 61.8% dari swing low ke swing high 20 hari
    swing_high = float(high.tail(20).max())
    swing_low  = float(low.tail(20).min())
    fib_618    = swing_low + 0.618 * (swing_high - swing_low)
    fib_100    = swing_high

    # Kumpulkan kandidat yang valid (> entry + 2%)
    candidates = [v for v in [r1, r2, fib_618, fib_100, swing_high]
                  if v > entry * 1.02]

    if not candidates:
        return entry * 1.07   # fallback minimal 7%

    target = min(candidates)
    return float(target) if target > entry * 1.04 else entry * 1.07

# ============================================================
# RISK / REWARD
# ============================================================
def risk_reward(entry: float, sl: float, target: float) -> float:
    risk   = abs(entry - sl)
    reward = abs(target - entry)
    if risk == 0:
        return 0.0
    return round(reward / risk, 2)

# ============================================================
# LOT SIZING — Volatility-adjusted (ATR-based)
# ============================================================
def position_sizing(balance: float, risk_pct: float, entry: float,
                    sl: float, atr: float | None = None) -> int:
    risk_amount  = balance * risk_pct
    risk_per_lot = abs(entry - sl) * 100
    if risk_per_lot == 0:
        return 1
    lot = int(risk_amount / risk_per_lot)
    # ATR penalty: jika volatilitas tinggi (ATR > 3% harga), kurangi lot
    if atr and entry > 0 and (atr / entry) > 0.03:
        lot = max(1, int(lot * 0.7))
    return max(lot, 1)

# ============================================================
# VWAP ROLLING 20-HARI (bukan kumulatif — ini kunci perbaikan)
# ============================================================
def rolling_vwap(df: pd.DataFrame, window: int = 20) -> pd.Series:
    close  = df["Close"].squeeze()
    volume = df["Volume"].squeeze()
    tp     = close   # typical price simplified (bisa (H+L+C)/3 juga)
    pv     = tp * volume
    return pv.rolling(window).sum() / volume.rolling(window).sum()

# ============================================================
# MOMENTUM CONFIRMATION (pakai rolling VWAP)
# ============================================================
def momentum_confirmation(df: pd.DataFrame) -> int:
    close  = df["Close"].squeeze()
    volume = df["Volume"].squeeze()
    vwap   = rolling_vwap(df, 20)

    last_price = float(close.iloc[-1])
    last_vwap  = float(vwap.iloc[-1]) if not np.isnan(vwap.iloc[-1]) else last_price
    avg_vol    = float(volume.tail(20).mean())
    vol_now    = float(volume.iloc[-1])

    score = 0
    if vol_now > avg_vol * 1.5:    score += 1
    if last_price > last_vwap:     score += 1
    return score

# ============================================================
# ACCUMULATION PHASE
# ============================================================
def accumulation_phase(df: pd.DataFrame) -> int:
    close  = df["Close"].squeeze()
    volume = df["Volume"].squeeze()

    high20      = float(close.tail(20).max())
    low20       = float(close.tail(20).min())
    last        = float(close.iloc[-1])
    range_ratio = (high20 - low20) / last if last > 0 else 1

    compression  = range_ratio < 0.08
    avg_vol      = float(volume.tail(20).mean())
    vol_recent   = float(volume.tail(5).mean())
    volume_build = vol_recent >= avg_vol * 0.9
    higher_low   = float(close.tail(10).min()) >= float(close.tail(20).min())

    return sum([compression, volume_build, higher_low])

# ============================================================
# BANDAR DETECTION (distribusi logic diperketat)
# ============================================================
def bandar_detection(df: pd.DataFrame) -> int:
    close  = df["Close"].squeeze()
    volume = df["Volume"].squeeze()

    avg_vol   = float(volume.tail(20).mean())
    vol_now   = float(volume.iloc[-1])
    spike     = vol_now > avg_vol * 2.0          # dinaikkan dari 1.5 → 2.0 (lebih ketat)

    price_trend  = float(close.tail(5).mean()) > float(close.tail(10).mean())
    vol_stable   = float(volume.tail(5).mean()) >= avg_vol * 0.9
    accumulation = price_trend and vol_stable

    # Distribusi: harga naik signifikan (>1.5%) tapi volume SANGAT turun (<60%) → lebih ketat
    vol_drop    = float(volume.tail(3).mean()) < avg_vol * 0.6   # dari 0.8 → 0.6
    price_gain  = (float(close.iloc[-1]) - float(close.iloc[-3])) / float(close.iloc[-3]) > 0.015
    distribution = price_gain and vol_drop

    score = 0
    if spike:        score += 2
    if accumulation: score += 2
    if distribution: score -= 2   # dikurangi dari -3 agar tidak terlalu agresif negatif
    return score

# ============================================================
# BREAKOUT CONFIRMATION
# ============================================================
def breakout_confirmation(df: pd.DataFrame) -> str:
    close  = df["Close"].squeeze()
    volume = df["Volume"].squeeze()

    last        = float(close.iloc[-1])
    recent_high = float(close.tail(10).max())
    breakout    = last >= recent_high * 0.98
    avg_vol     = float(volume.tail(20).mean())
    vol_now     = float(volume.iloc[-1])

    if breakout and vol_now > avg_vol * 1.3:
        return "VALID"
    if breakout:
        return "WEAK"
    return "WAIT"

# ============================================================
# FOLLOW THROUGH
# ============================================================
def follow_through(df: pd.DataFrame) -> int:
    close  = df["Close"].squeeze()
    volume = df["Volume"].squeeze()

    change  = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
    avg_vol = float(volume.tail(20).mean())

    score = 0
    if change > 1:                       score += 1
    if float(volume.iloc[-1]) > avg_vol: score += 1
    return score

# ============================================================
# INTRADAY CONFIRMATION (5m data)
# ============================================================
def intraday_confirm(ticker: str) -> int:
    try:
        df5 = yf.download(tickers=ticker, period="5d", interval="5m",
                          progress=False, auto_adjust=True)
        if df5 is None or len(df5) < 10:
            return 0
        close  = df5["Close"].squeeze()
        volume = df5["Volume"].squeeze()
        vwap   = rolling_vwap(df5, min(20, len(df5)))

        change   = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
        avg_vol  = float(volume.tail(10).mean())
        last_vwap = float(vwap.iloc[-1]) if not np.isnan(vwap.iloc[-1]) else float(close.iloc[-1])

        score = 0
        if change > 0.3:                              score += 1
        if float(close.iloc[-1]) > last_vwap:         score += 1
        if float(volume.iloc[-1]) > avg_vol:          score += 1
        return score
    except Exception:
        return 0

# ============================================================
# ENTRY TIMING
# ============================================================
def entry_timing(df: pd.DataFrame) -> str:
    close  = df["Close"].squeeze()
    volume = df["Volume"].squeeze()

    change  = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
    avg_vol = float(volume.tail(20).mean())
    vol_now = float(volume.iloc[-1])

    if change > 2 and vol_now > avg_vol * 1.5:
        return "🔥 EXECUTE NOW"
    if change < 1:
        return "⏳ WAIT PULLBACK"
    return "⚠️ DELAY"

# ============================================================
# SCORE CALCULATION
# ============================================================
def calculate_score(prob: float, runner: float, quality: str,
                    rr: float, liquidity: str, bandar_score: int) -> float:
    prob_score    = (max(0, min(100, prob)) / 100) * 25
    runner_score  = (max(0, min(10, runner)) / 10) * 20
    quality_map   = {"WEAK": 3, "HEALTHY": 10, "STRONG": 15}
    quality_score = quality_map.get(quality, 0)
    rr_score      = min(20, (max(0, min(4.0, rr)) / 4.0) * 20)
    if rr >= 2.5:
        rr_score = min(20, rr_score + 3)
    liq_score  = 10 if "OK" in str(liquidity) else 0
    bandar_pts = (max(0, min(4, bandar_score)) / 4) * 10
    return round(prob_score + runner_score + quality_score + rr_score + liq_score + bandar_pts, 2)

# ============================================================
# CONFLUENCE CHECK
# ============================================================
def confluence_check(momentum: int, accum: int, bandar: int,
                     breakout: str, rr: float, ema_ok: bool) -> tuple[int, dict, bool]:
    signals = {
        "Momentum":    momentum >= 1,
        "Accumulation": accum >= 2,
        "Bandar":      bandar >= 2,
        "Breakout":    breakout in ("VALID", "WEAK"),
        "RR_Layak":    rr >= 1.8,
        "Uptrend":     ema_ok,
    }
    count = sum(signals.values())
    return count, signals, count >= 4

# ============================================================
# DYNAMIC THRESHOLD (percentile-based)
# ============================================================
def get_dynamic_thresholds(all_scores: list) -> dict:
    if len(all_scores) < 3:
        return {"execute_now": 85, "execute": 75, "ready": 65, "method": "static_fallback"}
    arr = np.array(all_scores)
    return {
        "execute_now": float(np.percentile(arr, 88)),
        "execute":     float(np.percentile(arr, 70)),
        "ready":       float(np.percentile(arr, 45)),
        "method":      "dynamic_percentile",
        "n_samples":   len(all_scores),
    }

# ============================================================
# CYBERNETIC FEEDBACK ENGINE
# ============================================================
CYBER_CONFIG = {
    "learning_rate":           0.15,
    "memory_days":             30,
    "min_trades_for_adjust":   8,
}

def cybernetic_feedback_engine(journal_df: pd.DataFrame, current_regime: str):
    if journal_df.empty or len(journal_df) < CYBER_CONFIG["min_trades_for_adjust"]:
        return None
    if "PnL" not in journal_df.columns or journal_df["PnL"].isna().all():
        return None

    cutoff = datetime.now().date() - pd.Timedelta(days=CYBER_CONFIG["memory_days"])
    recent = journal_df.copy()
    recent["Date"] = pd.to_datetime(recent["Date"]).dt.date
    recent = recent[recent["Date"] >= cutoff]
    if len(recent) < 5:
        return None

    winrate     = float((recent["PnL"] > 0).mean() * 100)
    trade_count = len(recent)

    params     = st.session_state.cybernetic_params.copy()
    adjustment = 0.0

    if winrate > 65:   adjustment += 0.20
    elif winrate > 55: adjustment += 0.10
    elif winrate < 40: adjustment -= 0.20

    if current_regime == "BULLISH":
        adjustment += 0.15
    elif current_regime in ["SIDEWAYS", "VOLATILE"]:
        adjustment -= 0.15

    if trade_count < 8:
        adjustment -= 0.10

    lr = CYBER_CONFIG["learning_rate"]
    params["min_score"]             = max(60, min(95, int(params["min_score"] * (1 + adjustment * lr))))
    params["execute_now_threshold"] = max(80, min(98, int(params["execute_now_threshold"] * (1 + adjustment * lr * 0.8))))
    params["min_rr"]                = max(1.8, min(3.0, round(params["min_rr"] + adjustment * 0.3, 1)))
    params["last_adjust_date"]      = str(datetime.now().date())
    params["adjustment_history"].append({
        "date":          datetime.now().strftime("%Y-%m-%d"),
        "regime":        current_regime,
        "winrate":       round(winrate, 1),
        "adjustment":    round(adjustment, 3),
        "new_min_score": params["min_score"],
    })

    st.session_state.cybernetic_params = params
    save_state()
    return params

# ============================================================
# ENTRY SYSTEM ACTION
# ============================================================
def entry_system(row: pd.Series) -> str:
    thresholds   = st.session_state.get("dynamic_thresholds") or {}
    exec_now_th  = thresholds.get("execute_now", 85)
    exec_th      = thresholds.get("execute", 75)
    ready_th     = thresholds.get("ready", 65)
    min_rr       = st.session_state.cybernetic_params.get("min_rr", 1.8)

    try:
        entry  = float(str(row["Entry"]).replace(".", "").replace(",", ""))
        target = float(str(row["Target"]).replace(".", "").replace(",", ""))
    except Exception:
        return "❌ SKIP"

    if entry >= target * 0.97:
        return "❌ SKIP"

    score    = row.get("Score", 0)
    rr       = row.get("RR", 0)
    breakout = row.get("Breakout", "")
    bandar   = row.get("BandarScore", 0)
    momentum = row.get("Momentum", 0)
    accum    = row.get("Accumulation", 0)
    timing   = row.get("Timing", "")

    if (score >= exec_now_th and momentum >= 2 and accum >= 2 and rr >= 2.0 and
            breakout == "VALID" and bandar >= 3 and timing == "🔥 EXECUTE NOW"):
        return "🔥 EXECUTE NOW"

    if score >= exec_th and rr >= min_rr and breakout in ("VALID", "WEAK") and bandar >= 2:
        return "✅ EXECUTE"

    if score >= ready_th:
        return "⏳ READY"

    if timing == "⏳ WAIT PULLBACK":
        return "⏸ WAIT PULLBACK"

    return "❌ SKIP"

# ============================================================
# SESSION STATE INIT
# ============================================================
@st.cache_resource
def _load_persistent_state():
    return load_state()

if "state_loaded" not in st.session_state:
    _state = _load_persistent_state()
    st.session_state.cybernetic_params  = _state.get("cybernetic_params", DEFAULT_CYBER.copy())
    st.session_state.signal_lock        = _state.get("signal_lock", {})
    st.session_state.state_loaded       = True

if "active_trades" not in st.session_state:
    st.session_state.active_trades = (
        pd.read_csv(ACTIVE_FILE) if os.path.exists(ACTIVE_FILE) else pd.DataFrame()
    )
if "journal" not in st.session_state:
    st.session_state.journal = (
        pd.read_csv(JOURNAL_FILE) if os.path.exists(JOURNAL_FILE) else pd.DataFrame()
    )
if "scan_result"        not in st.session_state: st.session_state.scan_result        = None
if "sector_table"       not in st.session_state: st.session_state.sector_table       = None
if "balance"            not in st.session_state: st.session_state.balance            = 800_000
if "dynamic_thresholds" not in st.session_state: st.session_state.dynamic_thresholds = None
if "last_regime"        not in st.session_state: st.session_state.last_regime        = "-"
if "debug_log"          not in st.session_state: st.session_state.debug_log          = []

# ============================================================
# IMPORTS ENGINE & CONFIG
# ============================================================
from engine.probability_engine import runner_probability
from engine.runner_engine import runner_prediction
from engine.pullback_engine import pullback_zone
from engine.pullback_quality_engine import pullback_quality
from engine.sector_engine import sector_momentum
from engine.liquidity_engine import liquidity_trap
from engine.regime_engine import detect_market_regime
from modules.howto import show_howto
from modules.account import lot_size, risk_reward as acct_rr
from config.universe import ISSI_UNIVERSE, SECTOR_MAP, get_sector

# ============================================================
# LOAD MARKET DATA
# ============================================================
@st.cache_data(ttl=300)
def load_market() -> dict[str, pd.DataFrame]:
    raw = yf.download(
        tickers=ISSI_UNIVERSE,
        period="6mo",
        interval="1d",
        group_by="ticker",
        progress=False,
        auto_adjust=True,
    )
    market = {}
    for s in ISSI_UNIVERSE:
        try:
            df = raw[s].dropna()
            # Validasi: minimal 60 bar, harga & volume tidak nol
            if len(df) < 60:
                continue
            if df["Close"].squeeze().iloc[-1] <= 0:
                continue
            if df["Volume"].squeeze().tail(5).mean() <= 0:
                continue
            market[s] = df
        except Exception:
            continue
    return market

# ============================================================
# SCANNER UTAMA
# ============================================================
def run_scanner():
    market = load_market()
    if not market:
        st.error("Gagal memuat data market. Cek koneksi internet.")
        return

    regime = detect_market_regime(market)
    st.session_state.last_regime = regime

    cybernetic_feedback_engine(st.session_state.journal, regime)

    sector_power = sector_momentum(market, SECTOR_MAP)
    st.session_state.sector_table = pd.DataFrame(
        [{"Sector": k, "Strength": round(v, 2)} for k, v in sector_power.items()]
    ).sort_values("Strength", ascending=False)

    # Sektor dengan momentum positif saja
    positive_sectors = set(
        row["Sector"] for _, row in st.session_state.sector_table.iterrows()
        if row["Strength"] > 0
    )

    candidates = []
    debug_log  = []   # ← kumpulkan alasan gugur setiap ticker
    progress_bar = st.progress(0, text="Scanning...")
    total = len([t for t in ISSI_UNIVERSE if t in market])
    count = 0

    for ticker, df in market.items():
        if ticker not in ISSI_UNIVERSE:
            continue
        count += 1
        progress_bar.progress(count / max(total, 1), text=f"Scanning {ticker}...")

        try:
            sector     = get_sector(ticker)
            tkr_clean  = ticker.replace(".JK", "")

            # ── Filter 1: Sektor ─────────────────────────────
            if sector not in positive_sectors:
                sec_strength = next(
                    (row["Strength"] for _, row in st.session_state.sector_table.iterrows()
                     if row["Sector"] == sector), None
                )
                debug_log.append({
                    "Ticker": tkr_clean, "Sector": sector,
                    "RSI": "-", "EMA_OK": "-", "Bandar": "-",
                    "Breakout": "-", "Confluence": "-", "RR": "-",
                    "Score": "-",
                    "❌ Gugur di": f"Sektor lemah (strength={sec_strength})",
                })
                continue

            # ── Filter 2: RSI ────────────────────────────────
            rsi_ok, rsi_value = rsi_gate(df)
            if not rsi_ok:
                debug_log.append({
                    "Ticker": tkr_clean, "Sector": sector,
                    "RSI": round(rsi_value, 1), "EMA_OK": "-",
                    "Bandar": "-", "Breakout": "-",
                    "Confluence": "-", "RR": "-", "Score": "-",
                    "❌ Gugur di": f"RSI out of range ({rsi_value:.1f}, batas 42–72)",
                })
                continue

            ema_ok, last_price, ema_val = ema_trend_filter(df)

            # ── Kalkulasi teknikal ───────────────────────────
            atr    = calculate_atr(df)
            entry  = last_price
            sl     = calculate_sl_atr(entry, atr)
            target = find_target(df, entry)
            rr     = risk_reward(entry, sl, target)
            lot    = position_sizing(st.session_state.balance, 0.02, entry, sl, atr)

            # ── Sinyal ──────────────────────────────────────
            momentum = momentum_confirmation(df)
            accum    = accumulation_phase(df)
            bandar   = bandar_detection(df)
            breakout = breakout_confirmation(df)
            ft       = follow_through(df)
            timing   = entry_timing(df)

            # ── Filter 3: Bandar & Breakout ──────────────────
            if bandar < 2 or breakout == "WAIT":
                reason = []
                if bandar < 2:    reason.append(f"Bandar rendah ({bandar})")
                if breakout == "WAIT": reason.append("Breakout WAIT")
                debug_log.append({
                    "Ticker": tkr_clean, "Sector": sector,
                    "RSI": round(rsi_value, 1),
                    "EMA_OK": "✅" if ema_ok else "❌",
                    "Bandar": bandar, "Breakout": breakout,
                    "Confluence": "-", "RR": round(rr, 1), "Score": "-",
                    "❌ Gugur di": " | ".join(reason),
                })
                continue

            intraday = intraday_confirm(ticker)

            prob    = runner_probability(df)
            runner  = runner_prediction(df)
            quality = pullback_quality(df)
            liq_raw = liquidity_trap(df)
            liq_str = "🔴 TRAP" if liq_raw == "TRAP" else "🟢 OK"

            # ── Filter 4: Confluence ─────────────────────────
            conf_count, conf_signals, conf_passed = confluence_check(
                momentum, accum, bandar, breakout, rr, ema_ok
            )
            if not conf_passed:
                failed = [k for k, v in conf_signals.items() if not v]
                debug_log.append({
                    "Ticker": tkr_clean, "Sector": sector,
                    "RSI": round(rsi_value, 1),
                    "EMA_OK": "✅" if ema_ok else "❌",
                    "Bandar": bandar, "Breakout": breakout,
                    "Confluence": f"{conf_count}/6", "RR": round(rr, 1),
                    "Score": "-",
                    "❌ Gugur di": f"Confluence {conf_count}/6 (gagal: {', '.join(failed)})",
                })
                continue

            # ── Filter 5: RR minimum ─────────────────────────
            if rr < 1.8:
                debug_log.append({
                    "Ticker": tkr_clean, "Sector": sector,
                    "RSI": round(rsi_value, 1),
                    "EMA_OK": "✅" if ema_ok else "❌",
                    "Bandar": bandar, "Breakout": breakout,
                    "Confluence": f"{conf_count}/6", "RR": round(rr, 1),
                    "Score": "-",
                    "❌ Gugur di": f"RR terlalu rendah ({rr:.1f}, min 1.8)",
                })
                continue

            # ── Score ────────────────────────────────────────
            score = calculate_score(prob, runner, quality, rr, liq_str, bandar)
            score += momentum * 0.8 + accum * 0.9 + ft * 0.7 + intraday * 0.5
            if momentum == 2: score = min(100, score + 1)
            if ft == 2:       score = min(100, score + 1)
            if last_price > ema_val * 1.01: score = min(100, score + 1)

            confidence = momentum * 10 + accum * 10 + bandar * 5 + ft * 5 + intraday * 5

            # ── Debug: lolos semua filter ────────────────────
            debug_log.append({
                "Ticker": tkr_clean, "Sector": sector,
                "RSI": round(rsi_value, 1),
                "EMA_OK": "✅" if ema_ok else "❌",
                "Bandar": bandar, "Breakout": breakout,
                "Confluence": f"{conf_count}/6", "RR": round(rr, 1),
                "Score": round(score, 1),
                "❌ Gugur di": "✅ LOLOS — masuk kandidat",
            })

            candidates.append({
                "BUY":          False,
                "Ticker":       ticker.replace(".JK", ""),
                "Sector":       sector,
                "Action":       "",
                "Score":        round(score, 2),
                "Confidence":   confidence,
                "Probability":  int(prob),
                "RunnerScore":  int(runner),
                "PullbackQuality": quality,
                "Liquidity":    liq_str,
                "RSI":          round(rsi_value, 1),
                "RR":           round(rr, 1),
                "Momentum":     momentum,
                "Accumulation": accum,
                "BandarScore":  bandar,
                "Breakout":     breakout,
                "FT":           ft,
                "INTRA":        intraday,
                "Confluence":   conf_count,
                "Entry":        idr(entry),
                "SL":           idr(sl),
                "Target":       idr(target),
                "Lot":          lot,
                "Timing":       timing,
                "ATR":          round(atr, 0),
                "EMA50":        round(ema_val, 0),
            })

        except Exception as e:
            debug_log.append({
                "Ticker": ticker.replace(".JK", ""), "Sector": get_sector(ticker),
                "RSI": "-", "EMA_OK": "-", "Bandar": "-", "Breakout": "-",
                "Confluence": "-", "RR": "-", "Score": "-",
                "❌ Gugur di": f"⚠️ Exception: {str(e)[:60]}",
            })
            continue

    progress_bar.empty()
    st.session_state.debug_log = debug_log   # ← simpan untuk ditampilkan di UI

    if not candidates:
        st.session_state.scan_result = pd.DataFrame()
        return

    all_scores = [c["Score"] for c in candidates]
    thresholds = get_dynamic_thresholds(all_scores)
    st.session_state.dynamic_thresholds = thresholds

    scan = pd.DataFrame(candidates).sort_values("Score", ascending=False)
    scan["Action"] = scan.apply(entry_system, axis=1)
    scan = scan[scan["Action"] != "❌ SKIP"]
    st.session_state.scan_result = scan.head(5)

    # ── Telegram Alert ────────────────────────────────────────
    now       = time.time()
    lock_time = 3600
    sent      = []

    for _, row in st.session_state.scan_result.iterrows():
        tkr    = row["Ticker"]
        action = row.get("Action", "")
        if action not in ("🔥 EXECUTE NOW", "✅ EXECUTE"):
            continue
        last_t = st.session_state.signal_lock.get(tkr, 0)
        if now - last_t < lock_time:
            continue

        msg = f"""
🔥 ATS SUPERENGINE V3.0 🔥

Ticker     : {tkr}
Action     : {action}
Score      : {row.get('Score', 0):.1f}
RR         : {row.get('RR', 0):.1f}
Confluence : {row.get('Confluence', 0)}/6
RSI        : {row.get('RSI', 0):.1f}
Breakout   : {row.get('Breakout', '-')}
Bandar     : {row.get('BandarScore', 0)}
Sector     : {row.get('Sector', '-')}
Regime     : {regime}

Entry   : {row.get('Entry', '-')}
SL      : {row.get('SL', '-')}
Target  : {row.get('Target', '-')}
Lot     : {row.get('Lot', '-')}
ATR     : {row.get('ATR', '-')}

{'⚡ LANGSUNG EKSEKUSI' if 'EXECUTE NOW' in action else '✅ TUNGGU KONFIRMASI'}
⚠️ Ikuti sistem. No FOMO. Gunakan SL.

ATS SuperEngine V3.0
        """.strip()

        send_telegram(msg)
        st.session_state.signal_lock[tkr] = now
        sent.append(tkr)

    save_state()
    if sent:
        st.success(f"✅ Alert Telegram terkirim: {', '.join(sent)}")

# ============================================================
# UI LAYOUT
# ============================================================
st.set_page_config(layout="wide", page_title="ATS SuperEngine V3.0")

col_title, col_info = st.columns([3, 1])
with col_title:
    st.title("ATS SuperEngine V3.0")
    st.caption(f"Market clock: {datetime.now().strftime('%H:%M:%S WIB')}  |  Regime: {st.session_state.get('last_regime', '-')}")
with col_info:
    cp = st.session_state.cybernetic_params
    st.metric("Min Score (Adaptif)", cp["min_score"])

tabs = st.tabs(["📖 HOW TO USE", "📊 TRADING DESK", "💼 ACCOUNT", "📋 REPORT", "🕌 ISSI CHECK"])

# ─────────────────────────────────────────────────────────────
# TAB 0 — HOW TO USE
# ─────────────────────────────────────────────────────────────
with tabs[0]:
    show_howto()

# ─────────────────────────────────────────────────────────────
# TAB 1 — TRADING DESK
# ─────────────────────────────────────────────────────────────
with tabs[1]:
    st.subheader("🔍 Scanner Saham Syariah ISSI")

    # Info balance (read-only, edit di tab Account)
    b1, b2, b3 = st.columns(3)
    b1.metric("💰 Balance", f"Rp {idr(st.session_state.balance)}")
    b2.metric("⚠️ Risk/Trade (2%)", f"Rp {idr(st.session_state.balance * 0.02)}")
    b3.metric("📊 Regime", st.session_state.get("last_regime", "-"))

    st.caption("_Ubah balance di tab **💼 Account**_")
    st.markdown("---")

    st.markdown("""
        <style>
        div[data-testid="stButton"] > button[kind="primary"] {
            background-color: #16a34a !important;
            border-color: #16a34a !important;
            color: #ffffff !important;
        }
        div[data-testid="stButton"] > button[kind="primary"]:hover {
            background-color: #15803d !important;
            border-color: #15803d !important;
            color: #ffffff !important;
        }
        div[data-testid="stButton"] > button[kind="primary"]:active {
            background-color: #166534 !important;
            border-color: #166534 !important;
        }
        </style>
    """, unsafe_allow_html=True)

    if st.button("🚀 RUN ATS SCANNER V3.0", type="primary", use_container_width=True):
        with st.spinner("ATS scanning seluruh universe ISSI..."):
            run_scanner()
        st.success("✅ Scan selesai")

    # Threshold info
    if st.session_state.dynamic_thresholds:
        th = st.session_state.dynamic_thresholds
        st.info(
            f"📊 **Threshold dinamis hari ini** — "
            f"Execute Now ≥ {th['execute_now']:.0f} | "
            f"Execute ≥ {th['execute']:.0f} | "
            f"Ready ≥ {th['ready']:.0f}  "
            f"*(dari {th.get('n_samples', 0)} kandidat, metode: {th['method']})*"
        )

    # Hasil scan
    if st.session_state.scan_result is not None and not st.session_state.scan_result.empty:
        df = st.session_state.scan_result.copy()

        # TradingView Chart
        st.markdown("---")
        ticker_list = df["Ticker"].tolist()
        selected    = st.selectbox("📈 Pilih saham untuk chart", ticker_list)
        symbol      = f"IDX:{selected}"
        st.components.v1.html(
            f'<iframe src="https://s.tradingview.com/widgetembed/?symbol={symbol}'
            f'&interval=D&theme=dark&style=1&locale=id" '
            f'width="100%" height="550" frameborder="0"></iframe>',
            height=560
        )

        # Summary metrics
        st.markdown("---")
        m1, m2, m3, m4 = st.columns(4)
        best = df.iloc[0]
        m1.metric("Top Score",    f"{best['Score']:.1f}")
        m2.metric("Top RR",       f"{best['RR']:.1f}x")
        m3.metric("Top Ticker",   best["Ticker"])
        m4.metric("Confluence",   f"{best['Confluence']}/6")

        # Tabel kandidat
        st.subheader("🏆 Top Runner")
        cols_show = [
            "BUY", "Action", "Ticker", "Sector", "Score", "RR",
            "Confluence", "RSI", "Breakout", "BandarScore",
            "Momentum", "Accumulation", "Entry", "SL", "Target", "Lot",
            "Probability", "RunnerScore", "Timing", "ATR",
        ]
        cols_show = [c for c in cols_show if c in df.columns]

        edited = st.data_editor(
            df[cols_show],
            use_container_width=True,
            hide_index=True,
            column_config={
                "BUY":          st.column_config.CheckboxColumn("BUY"),
                "Action":       st.column_config.TextColumn("Action"),
                "Score":        st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f"),
                "Confluence":   st.column_config.NumberColumn("Conf/6", min_value=0, max_value=6),
                "BandarScore":  st.column_config.NumberColumn("Bandar", min_value=-4, max_value=4),
                "Momentum":     st.column_config.NumberColumn("Mom", min_value=0, max_value=2),
                "Accumulation": st.column_config.NumberColumn("Accum", min_value=0, max_value=3),
                "FT":           st.column_config.NumberColumn("FT", min_value=0, max_value=2),
                "INTRA":        st.column_config.NumberColumn("Intra", min_value=0, max_value=3),
                "Breakout":     st.column_config.TextColumn("Breakout"),
                "Timing":       st.column_config.TextColumn("Timing"),
                "Liquidity":    st.column_config.TextColumn("Liquidity"),
                "RR":           st.column_config.NumberColumn("RR", format="%.1f"),
                "RSI":          st.column_config.NumberColumn("RSI", format="%.1f"),
                "Lot":          st.column_config.NumberColumn("Lot"),
                "ATR":          st.column_config.NumberColumn("ATR"),
                "Probability":  st.column_config.NumberColumn("Prob%"),
                "RunnerScore":  st.column_config.NumberColumn("Runner"),
            },
        )

        # BUY logic (DIPERBAIKI: tambah yang BELUM ada, bukan yang sudah ada)
        buy_rows = edited[edited["BUY"] == True]
        if len(buy_rows) > 0:
            existing_tickers = (
                st.session_state.active_trades["Ticker"].tolist()
                if not st.session_state.active_trades.empty else []
            )
            new_trades = buy_rows[~buy_rows["Ticker"].isin(existing_tickers)].copy()
            if len(new_trades) > 0:
                new_trades["Status"]    = "OPEN"
                new_trades["EntryTime"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                new_trades["PnL"]       = None
                st.session_state.active_trades = pd.concat(
                    [st.session_state.active_trades, new_trades], ignore_index=True
                )
                st.session_state.active_trades.to_csv(ACTIVE_FILE, index=False)
                st.success(f"✅ {len(new_trades)} trade masuk Active Trades")
            else:
                st.warning("Semua ticker yang dipilih sudah ada di Active Trades")

        # Active trades
        if not st.session_state.active_trades.empty:
            st.markdown("---")
            st.subheader("📌 Active Trades")
            active_edited = st.data_editor(
                st.session_state.active_trades,
                num_rows="dynamic",
                use_container_width=True,
                hide_index=True,
            )
            if st.button("💾 Save Active Trades"):
                st.session_state.active_trades = active_edited.reset_index(drop=True)
                st.session_state.active_trades.to_csv(ACTIVE_FILE, index=False)
                st.success("✅ Active Trades tersimpan")

    elif st.session_state.scan_result is not None:
        st.warning("⚠️ ATS tidak menemukan kandidat saham berkualitas hari ini. Coba besok atau saat regime BULLISH.")

    # ── 🔍 Scan Debug Expander ───────────────────────────────────
    if st.session_state.debug_log:
        debug_df = pd.DataFrame(st.session_state.debug_log)

        # Hitung ringkasan gugur
        gugur_counts = (
            debug_df[debug_df["❌ Gugur di"] != "✅ LOLOS — masuk kandidat"]["❌ Gugur di"]
            .str.extract(r"^([^(|]+)")[0]
            .str.strip()
            .value_counts()
            .reset_index()
        )
        gugur_counts.columns = ["Alasan Gugur", "Jumlah Ticker"]

        with st.expander("🔍 Scan Debug — Kenapa saham tidak lolos?", expanded=False):
            st.caption(
                f"Total ticker diproses: **{len(debug_df)}** | "
                f"Lolos: **{(debug_df['❌ Gugur di'] == '✅ LOLOS — masuk kandidat').sum()}** | "
                f"Gugur: **{(debug_df['❌ Gugur di'] != '✅ LOLOS — masuk kandidat').sum()}**"
            )

            # Ringkasan bar chart
            if not gugur_counts.empty:
                fig_debug = px.bar(
                    gugur_counts,
                    x="Jumlah Ticker", y="Alasan Gugur",
                    orientation="h",
                    color="Jumlah Ticker",
                    color_continuous_scale=["#22c55e", "#f59e0b", "#ef4444"],
                    title="Distribusi Alasan Gugur"
                )
                fig_debug.update_layout(height=300, showlegend=False,
                                        yaxis=dict(autorange="reversed"))
                st.plotly_chart(fig_debug, use_container_width=True)

            # Filter per sektor
            st.markdown("**Filter berdasarkan Sektor:**")
            sektor_list = ["Semua"] + sorted(debug_df["Sector"].dropna().unique().tolist())
            col_f1, col_f2 = st.columns([2, 2])
            with col_f1:
                filter_sektor = st.selectbox(
                    "Pilih Sektor", sektor_list, key="debug_sector_filter"
                )
            with col_f2:
                filter_status = st.selectbox(
                    "Filter Status", ["Semua", "✅ Lolos", "❌ Gugur"],
                    key="debug_status_filter"
                )

            filtered = debug_df.copy()
            if filter_sektor != "Semua":
                filtered = filtered[filtered["Sector"] == filter_sektor]
            if filter_status == "✅ Lolos":
                filtered = filtered[filtered["❌ Gugur di"] == "✅ LOLOS — masuk kandidat"]
            elif filter_status == "❌ Gugur":
                filtered = filtered[filtered["❌ Gugur di"] != "✅ LOLOS — masuk kandidat"]

            # Warna baris: hijau = lolos, merah = gugur
            def color_rows(row):
                if row["❌ Gugur di"] == "✅ LOLOS — masuk kandidat":
                    return ["background-color: rgba(34,197,94,0.12)"] * len(row)
                return ["background-color: rgba(239,68,68,0.08)"] * len(row)

            st.dataframe(
                filtered.style.apply(color_rows, axis=1),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Ticker":      st.column_config.TextColumn("Ticker"),
                    "Sector":      st.column_config.TextColumn("Sektor"),
                    "RSI":         st.column_config.TextColumn("RSI"),
                    "EMA_OK":      st.column_config.TextColumn("EMA OK"),
                    "Bandar":      st.column_config.TextColumn("Bandar"),
                    "Breakout":    st.column_config.TextColumn("Breakout"),
                    "Confluence":  st.column_config.TextColumn("Conf"),
                    "RR":          st.column_config.TextColumn("RR"),
                    "Score":       st.column_config.TextColumn("Score"),
                    "❌ Gugur di": st.column_config.TextColumn("Status / Alasan Gugur", width="large"),
                }
            )

    # Sector Radar
    if st.session_state.sector_table is not None:
        st.markdown("---")
        st.subheader("🗺️ Sector Leader Radar")
        sector_df = st.session_state.sector_table.copy()
        colors    = ["#22c55e" if v > 0 else "#ef4444" for v in sector_df["Strength"]]
        fig = px.bar(
            sector_df, x="Strength", y="Sector", orientation="h",
            color="Strength",
            color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
            title="Kekuatan Sektor (positif = bullish)"
        )
        fig.update_layout(height=400, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

# ─────────────────────────────────────────────────────────────
# TAB 2 — ACCOUNT
# ─────────────────────────────────────────────────────────────
with tabs[2]:
    st.subheader("💼 Manajemen Akun")

    # Input balance — satu-satunya tempat edit
    col_inp, col_pad = st.columns([2, 3])
    with col_inp:
        balance_input = st.number_input(
            "💰 Modal / Balance (Rp)",
            min_value=100_000,
            step=100_000,
            value=st.session_state.balance,
            key="balance_account_input",
            help="Masukkan total modal trading kamu. Nilai ini dipakai untuk kalkulasi lot & risk per trade."
        )
        if balance_input != st.session_state.balance:
            st.session_state.balance = balance_input
            st.success("✅ Balance diperbarui")

    st.markdown("---")

    # Metrics ringkasan
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Balance",             f"Rp {idr(st.session_state.balance)}")
    c2.metric("Risk/Trade (2%)",     f"Rp {idr(st.session_state.balance * 0.02)}")
    c3.metric("Max 5 Posisi (40%)",  f"Rp {idr(st.session_state.balance * 0.40)}")
    c4.metric("Safe Cash (60%)",     f"Rp {idr(st.session_state.balance * 0.60)}")

    st.markdown("---")
    st.subheader("🧠 Cybernetic Parameters")
    params = st.session_state.cybernetic_params
    cc1, cc2, cc3, cc4 = st.columns(4)
    cc1.metric("Min Score",       params["min_score"])
    cc2.metric("Execute Now Th.", params["execute_now_threshold"])
    cc3.metric("Min RR",          params["min_rr"])
    cc4.metric("Last Adjust",     str(params.get("last_adjust_date", "-")))

    if params.get("adjustment_history"):
        st.markdown("**Riwayat Penyesuaian Cybernetic:**")
        hist_df = pd.DataFrame(params["adjustment_history"]).tail(10)
        st.dataframe(hist_df, use_container_width=True, hide_index=True)

# ─────────────────────────────────────────────────────────────
# TAB 3 — REPORT
# ─────────────────────────────────────────────────────────────
with tabs[3]:
    st.subheader("📋 Trade Journal")
    edited_journal = st.data_editor(
        st.session_state.journal,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "PnL": st.column_config.NumberColumn("PnL (Rp)", format="%.0f"),
        }
    )
    if st.button("💾 Save Journal"):
        st.session_state.journal = edited_journal.reset_index(drop=True)
        st.session_state.journal.to_csv(JOURNAL_FILE, index=False)
        st.success("✅ Journal tersimpan")

    # Analytics
    if not edited_journal.empty and "PnL" in edited_journal.columns:
        jdf = edited_journal.dropna(subset=["PnL"])
        if len(jdf) > 0:
            st.markdown("---")
            st.subheader("📈 Statistik Performa")

            total_trades = len(jdf)
            wins         = (jdf["PnL"] > 0).sum()
            losses       = (jdf["PnL"] <= 0).sum()
            winrate      = wins / total_trades * 100
            total_pnl    = jdf["PnL"].sum()
            avg_win      = jdf[jdf["PnL"] > 0]["PnL"].mean() if wins > 0 else 0
            avg_loss     = jdf[jdf["PnL"] <= 0]["PnL"].mean() if losses > 0 else 0
            profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0

            s1, s2, s3, s4, s5 = st.columns(5)
            s1.metric("Total Trade",    total_trades)
            s2.metric("Win Rate",       f"{winrate:.1f}%")
            s3.metric("Total PnL",      f"Rp {idr(total_pnl)}")
            s4.metric("Profit Factor",  f"{profit_factor:.2f}")
            s5.metric("Avg Win/Loss",   f"{abs(avg_win/avg_loss):.2f}x" if avg_loss != 0 else "-")

            # Equity Curve
            st.markdown("---")
            st.subheader("📉 Equity Curve")
            jdf_sorted = jdf.copy()
            if "Date" in jdf_sorted.columns:
                jdf_sorted = jdf_sorted.sort_values("Date")
            jdf_sorted["Cumulative PnL"] = jdf_sorted["PnL"].cumsum()
            jdf_sorted["Trade#"]         = range(1, len(jdf_sorted) + 1)

            fig_eq = go.Figure()
            fig_eq.add_trace(go.Scatter(
                x=jdf_sorted["Trade#"],
                y=jdf_sorted["Cumulative PnL"],
                mode="lines+markers",
                name="Equity",
                line=dict(color="#22c55e", width=2),
                fill="tozeroy",
                fillcolor="rgba(34,197,94,0.1)"
            ))
            fig_eq.update_layout(
                title="Cumulative PnL per Trade",
                xaxis_title="Trade #",
                yaxis_title="PnL Kumulatif (Rp)",
                height=350,
            )
            st.plotly_chart(fig_eq, use_container_width=True)

            # Drawdown
            cum_pnl = jdf_sorted["Cumulative PnL"]
            peak    = cum_pnl.cummax()
            dd      = cum_pnl - peak
            max_dd  = float(dd.min())

            st.metric("Max Drawdown", f"Rp {idr(max_dd)}")

            fig_dd = go.Figure()
            fig_dd.add_trace(go.Bar(
                x=jdf_sorted["Trade#"],
                y=dd,
                name="Drawdown",
                marker_color="#ef4444",
            ))
            fig_dd.update_layout(title="Drawdown per Trade", height=250)
            st.plotly_chart(fig_dd, use_container_width=True)

# ─────────────────────────────────────────────────────────────
# TAB 4 — ISSI CHECK
# ─────────────────────────────────────────────────────────────
with tabs[4]:
    st.subheader("🕌 ISSI Universe — Saham Syariah")
    st.caption(f"Total: {len(ISSI_UNIVERSE)} ticker terdaftar")
    sector_groups = defaultdict(list)
    for ticker in ISSI_UNIVERSE:
        sector_groups[get_sector(ticker)].append(ticker.replace(".JK", ""))
    for sector in sorted(sector_groups.keys()):
        with st.expander(f"**{sector}** ({len(sector_groups[sector])} saham)"):
            st.write(", ".join(sorted(sector_groups[sector])))

# ─────────────────────────────────────────────────────────────
# FOOTER
# ─────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "ATS SuperEngine V3.0 | ISSI Syariah Scanner | "
    "Gunakan dengan manajemen risiko ketat | Bukan rekomendasi investasi"
)