"""
ATS SuperEngine V4.1 — Saham Syariah ISSI Scanner
═══════════════════════════════════════════════════
Perbaikan kritis dari V4.0 (hasil evaluasi mendalam):

BUG FIXES:
  [B1] entry_system tidak lagi baca st.session_state di background thread
       → terima thresholds & cyber_params sebagai parameter eksplisit
  [B2] Score overflow diperbaiki: min(100, score) setelah SEMUA bonus
  [B3] EXECUTE NOW tidak lagi minta timing == EXECUTE NOW (self-contradictory)
       → diganti kondisi lebih logis: score tinggi + bandar kuat + breakout VALID
  [B4] IDX holidays diperluas ke 2025+2026, fungsi is_holiday() extensible

KALIBRASI:
  [K1] RSI gate adaptif per regime: BULLISH 45-78, SIDEWAYS 42-72, DIST 40-68
  [K2] Confluence minimum adaptif: BULLISH 4/6, SIDEWAYS/DIST 3/6
  [K3] Bandar spike threshold 2.0x → 1.8x (lebih realistis untuk mid-cap)
  [K4] Accumulation compression 8% → 12% (lebih realistis)
  [K5] intraday_confirm period 2d → 5d (cukup data untuk VWAP stabil)
  [K6] Hapus duplikasi sector_momentum di run_scanner
  [K7] scan_core return sector_df sekalian (single source of truth)
  [K8] Cybernetic recent cutoff 10 → 15 trades (konsisten dengan min 20)
"""

import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, date, timedelta
import time
import os
import json
import requests
import plotly.express as px
import plotly.graph_objects as go
from collections import defaultdict
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# ============================================================
# KONFIGURASI
# ============================================================
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT   = os.environ.get("TELEGRAM_CHAT", "")
STATE_FILE      = "ats_state.json"
JOURNAL_FILE    = "journal.csv"
ACTIVE_FILE     = "active_trades.csv"

# ============================================================
# TIMEZONE & JADWAL IDX
# ============================================================
WIB = pytz.timezone("Asia/Jakarta")

SCAN_SCHEDULE = [
    {"hour": 9,  "minute": 5,  "label": "Pre-Open"},
    {"hour": 11, "minute": 30, "label": "Mid Sesi 1"},
    {"hour": 13, "minute": 35, "label": "Open Sesi 2"},
    {"hour": 15, "minute": 0,  "label": "Pre-Closing"},
]

# [B4] Hari libur IDX — covers 2025 & 2026, extensible untuk tahun berikutnya
IDX_HOLIDAYS: set[date] = {
    # 2025
    date(2025, 1, 1),   date(2025, 1, 27),  date(2025, 1, 29),
    date(2025, 3, 28),  date(2025, 3, 31),  date(2025, 4, 1),
    date(2025, 4, 2),   date(2025, 4, 3),   date(2025, 4, 4),
    date(2025, 4, 18),  date(2025, 5, 1),   date(2025, 5, 12),
    date(2025, 5, 29),  date(2025, 6, 1),   date(2025, 6, 6),
    date(2025, 6, 27),  date(2025, 8, 17),  date(2025, 9, 5),
    date(2025, 12, 25), date(2025, 12, 26),
    # 2026
    date(2026, 1, 1),   date(2026, 1, 14),  date(2026, 1, 19),
    date(2026, 3, 18),  date(2026, 3, 19),  date(2026, 3, 20),
    date(2026, 3, 23),  date(2026, 4, 3),   date(2026, 5, 1),
    date(2026, 5, 20),  date(2026, 5, 22),  date(2026, 6, 1),
    date(2026, 6, 17),  date(2026, 8, 17),  date(2026, 8, 18),
    date(2026, 9, 24),  date(2026, 12, 25),
}

def is_holiday(d: date) -> bool:
    """Cek apakah tanggal adalah hari libur IDX."""
    return d in IDX_HOLIDAYS

def is_market_open() -> bool:
    now_wib = datetime.now(WIB)
    today   = now_wib.date()
    if now_wib.weekday() >= 5:    return False
    if is_holiday(today):         return False
    open_t  = now_wib.replace(hour=9,  minute=0,  second=0, microsecond=0)
    close_t = now_wib.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_t <= now_wib <= close_t

def is_trading_day() -> bool:
    now_wib = datetime.now(WIB)
    return now_wib.weekday() < 5 and not is_holiday(now_wib.date())

def get_wib_now() -> str:
    return datetime.now(WIB).strftime("%H:%M:%S WIB")

# ============================================================
# TELEGRAM
# ============================================================
def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT, "text": message}, timeout=5)
    except Exception:
        pass

# ============================================================
# PERSISTENSI STATE  [F2][F5][I5]
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
    return {
        "cybernetic_params": DEFAULT_CYBER.copy(),
        "signal_lock": {},
        "balance": 800_000,   # [I5] balance persist
    }

def save_state():
    # [F5] Bersihkan signal_lock yang sudah > 7 hari
    now_ts    = time.time()
    sig_lock  = st.session_state.signal_lock
    sig_lock  = {k: v for k, v in sig_lock.items() if now_ts - v < 7 * 86400}
    st.session_state.signal_lock = sig_lock

    cp = st.session_state.cybernetic_params.copy()
    if isinstance(cp.get("last_adjust_date"), (date, datetime)):
        cp["last_adjust_date"] = str(cp["last_adjust_date"])

    data = {
        "cybernetic_params": cp,
        "signal_lock":       sig_lock,
        "balance":           st.session_state.balance,   # [I5]
    }
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

# ============================================================
# HELPER FORMAT
# ============================================================
def idr(x) -> str:
    try:
        return f"{int(x):,}".replace(",", ".")
    except Exception:
        return str(x)

# ============================================================
# RSI — Wilder's Smoothing
# ============================================================
def calculate_rsi(df: pd.DataFrame, period: int = 14) -> float:
    close    = df["Close"].squeeze()
    delta    = close.diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    avg_loss = (-delta).clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, 1e-10)
    rsi      = 100 - (100 / (1 + rs))
    val      = float(rsi.iloc[-1])
    return val if not np.isnan(val) else 50.0

# [K1] RSI gate adaptif per regime — dipanggil dengan regime saat ini
def rsi_gate(df: pd.DataFrame, regime: str = "SIDEWAYS") -> tuple[bool, float]:
    rsi = calculate_rsi(df)
    if regime == "BULLISH":
        rsi_min, rsi_max = 45, 78   # Lebih toleran overbought saat bullish
    elif regime == "DISTRIBUTION":
        rsi_min, rsi_max = 40, 68   # Lebih ketat saat distribusi
    else:                            # SIDEWAYS / VOLATILE / unknown
        rsi_min, rsi_max = 42, 72
    return rsi_min <= rsi <= rsi_max, rsi

# ============================================================
# EMA
# ============================================================
def calculate_ema(df: pd.DataFrame, period: int = 50) -> float:
    close = df["Close"].squeeze()
    ema   = close.ewm(span=period, adjust=False).mean()
    return float(ema.iloc[-1])

def ema_trend_filter(df: pd.DataFrame, period: int = 50) -> tuple[bool, float, float]:
    last    = float(df["Close"].squeeze().iloc[-1])
    ema_val = calculate_ema(df, period)
    return last >= ema_val * 0.995, last, ema_val

# ============================================================
# ATR — Wilder's
# ============================================================
def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()
    close = df["Close"].squeeze()
    tr    = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    val = float(atr.iloc[-1])
    return val if not np.isnan(val) else 0.0

# ============================================================
# STOP LOSS
# ============================================================
def calculate_sl_atr(entry: float, atr: float, multiplier: float = 1.5) -> float:
    return max(entry - multiplier * atr, entry * 0.93)

# ============================================================
# TARGET — [F3] Pivot Point DIPERBAIKI: (H+L+C)/3 satu candle
# ============================================================
def find_target(df: pd.DataFrame, entry: float) -> float:
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()
    close = df["Close"].squeeze()

    # [F3] Pivot Point benar: dari 1 candle terakhir (bukan mean 5 hari)
    pp = (float(high.iloc[-1]) + float(low.iloc[-1]) + float(close.iloc[-1])) / 3
    r1 = 2 * pp - float(low.iloc[-1])
    r2 = pp + (float(high.iloc[-1]) - float(low.iloc[-1]))

    # Fibonacci dari swing 20 hari
    swing_high = float(high.tail(20).max())
    swing_low  = float(low.tail(20).min())
    fib_618    = swing_low + 0.618 * (swing_high - swing_low)

    candidates = [v for v in [r1, r2, fib_618, swing_high] if v > entry * 1.02]
    if not candidates:
        return entry * 1.07
    target = min(candidates)
    return float(target) if target > entry * 1.04 else entry * 1.07

# ============================================================
# RISK / REWARD
# ============================================================
def risk_reward(entry: float, sl: float, target: float) -> float:
    risk   = abs(entry - sl)
    reward = abs(target - entry)
    return round(reward / risk, 2) if risk > 0 else 0.0

# ============================================================
# LOT SIZING — ATR-adjusted
# ============================================================
def position_sizing(balance: float, risk_pct: float,
                    entry: float, sl: float, atr: float = 0.0) -> int:
    risk_amount  = balance * risk_pct
    risk_per_lot = abs(entry - sl) * 100
    if risk_per_lot == 0:
        return 1
    lot = int(risk_amount / risk_per_lot)
    if atr and entry > 0 and (atr / entry) > 0.03:
        lot = max(1, int(lot * 0.7))
    return max(lot, 1)

# ============================================================
# ROLLING VWAP 20 hari
# ============================================================
def rolling_vwap(df: pd.DataFrame, window: int = 20) -> pd.Series:
    close  = df["Close"].squeeze()
    volume = df["Volume"].squeeze()
    pv     = close * volume
    return pv.rolling(window).sum() / volume.rolling(window).sum()

# ============================================================
# SINYAL — 6 Komponen
# ============================================================
def momentum_confirmation(df: pd.DataFrame) -> int:
    close      = df["Close"].squeeze()
    volume     = df["Volume"].squeeze()
    vwap       = rolling_vwap(df, 20)
    last_price = float(close.iloc[-1])
    last_vwap  = float(vwap.iloc[-1]) if not np.isnan(vwap.iloc[-1]) else last_price
    avg_vol    = float(volume.tail(20).mean())
    score = 0
    if float(volume.iloc[-1]) > avg_vol * 1.5: score += 1
    if last_price > last_vwap:                  score += 1
    return score

def accumulation_phase(df: pd.DataFrame) -> int:
    close        = df["Close"].squeeze()
    volume       = df["Volume"].squeeze()
    last         = float(close.iloc[-1])
    high20       = float(close.tail(20).max())
    low20        = float(close.tail(20).min())
    range_ratio  = (high20 - low20) / last if last > 0 else 1
    avg_vol      = float(volume.tail(20).mean())
    compression  = range_ratio < 0.12          # [K4] 8% → 12%, lebih realistis
    volume_build = float(volume.tail(5).mean()) >= avg_vol * 0.9
    higher_low   = float(close.tail(10).min()) >= float(close.tail(20).min())
    return sum([compression, volume_build, higher_low])

def bandar_detection(df: pd.DataFrame) -> int:
    close        = df["Close"].squeeze()
    volume       = df["Volume"].squeeze()
    avg_vol      = float(volume.tail(20).mean())
    spike        = float(volume.iloc[-1]) > avg_vol * 1.8   # [K3] 2.0x → 1.8x
    price_trend  = float(close.tail(5).mean()) > float(close.tail(10).mean())
    vol_stable   = float(volume.tail(5).mean()) >= avg_vol * 0.9
    accumulation = price_trend and vol_stable
    vol_drop     = float(volume.tail(3).mean()) < avg_vol * 0.6
    price_gain   = (float(close.iloc[-1]) - float(close.iloc[-3])) / float(close.iloc[-3]) > 0.015
    distribution = price_gain and vol_drop
    score = 0
    if spike:        score += 2
    if accumulation: score += 2
    if distribution: score -= 2
    return score

def breakout_confirmation(df: pd.DataFrame) -> str:
    close       = df["Close"].squeeze()
    volume      = df["Volume"].squeeze()
    last        = float(close.iloc[-1])
    recent_high = float(close.tail(10).max())
    avg_vol     = float(volume.tail(20).mean())
    breakout    = last >= recent_high * 0.98
    if breakout and float(volume.iloc[-1]) > avg_vol * 1.3:
        return "VALID"
    if breakout:
        return "WEAK"
    return "WAIT"

def follow_through(df: pd.DataFrame) -> int:
    close   = df["Close"].squeeze()
    volume  = df["Volume"].squeeze()
    change  = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
    avg_vol = float(volume.tail(20).mean())
    score = 0
    if change > 1:                        score += 1
    if float(volume.iloc[-1]) > avg_vol:  score += 1
    return score

def intraday_confirm(ticker: str) -> int:
    try:
        df5 = yf.download(tickers=ticker, period="5d", interval="5m",  # [K5] 2d → 5d
                          progress=False, auto_adjust=True)
        if df5 is None or len(df5) < 10:
            return 0
        close     = df5["Close"].squeeze()
        volume    = df5["Volume"].squeeze()
        vwap      = rolling_vwap(df5, min(20, len(df5)))
        change    = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
        avg_vol   = float(volume.tail(10).mean())
        last_vwap = float(vwap.iloc[-1]) if not np.isnan(vwap.iloc[-1]) else float(close.iloc[-1])
        score = 0
        if change > 0.3:                        score += 1
        if float(close.iloc[-1]) > last_vwap:   score += 1
        if float(volume.iloc[-1]) > avg_vol:    score += 1
        return score
    except Exception:
        return 0

def entry_timing(df: pd.DataFrame) -> str:
    close   = df["Close"].squeeze()
    volume  = df["Volume"].squeeze()
    change  = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
    avg_vol = float(volume.tail(20).mean())
    if change > 2 and float(volume.iloc[-1]) > avg_vol * 1.5:
        return "🔥 EXECUTE NOW"
    if change < 1:
        return "⏳ WAIT PULLBACK"
    return "⚠️ DELAY"

def daily_change_pct(df: pd.DataFrame) -> float:
    close = df["Close"].squeeze()
    return round((float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100, 2)

# ============================================================
# SCORE
# ============================================================
def calculate_score(prob: float, runner: float, quality: str,
                    rr: float, liquidity: str, bandar_score: int) -> float:
    prob_score    = (max(0, min(100, prob)) / 100) * 25
    runner_score  = (max(0, min(10, runner)) / 10) * 20
    quality_map   = {"WEAK": 3, "HEALTHY": 10, "STRONG": 15}
    quality_score = quality_map.get(quality, 0)
    rr_score      = min(20, (max(0, min(4.0, rr)) / 4.0) * 20)
    if rr >= 2.5: rr_score = min(20, rr_score + 3)
    liq_score  = 10 if "OK" in str(liquidity) else 0
    bandar_pts = (max(0, min(4, bandar_score)) / 4) * 10
    return round(prob_score + runner_score + quality_score + rr_score + liq_score + bandar_pts, 2)

# ============================================================
# CONFLUENCE
# ============================================================
# [K2] Confluence check dengan minimum adaptif per regime
def confluence_check(momentum: int, accum: int, bandar: int,
                     breakout: str, rr: float, ema_ok: bool,
                     regime: str = "SIDEWAYS") -> tuple[int, dict, bool]:
    signals = {
        "Momentum":     momentum >= 1,
        "Accumulation": accum >= 2,
        "Bandar":       bandar >= 2,
        "Breakout":     breakout in ("VALID", "WEAK"),
        "RR_Layak":     rr >= 1.8,
        "Uptrend":      ema_ok,
    }
    count = sum(signals.values())
    # Minimum adaptif: BULLISH butuh 4/6, kondisi lain cukup 3/6
    min_conf = 4 if regime == "BULLISH" else 3
    return count, signals, count >= min_conf

# ============================================================
# DYNAMIC THRESHOLD
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
# CYBERNETIC  [F6] min_trades 8 → 20
# ============================================================
CYBER_CONFIG = {
    "learning_rate":         0.15,
    "memory_days":           30,
    "min_trades_for_adjust": 20,   # [F6] dinaikkan dari 8 → 20
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
    if len(recent) < 15:   # [K8] konsisten: butuh min 15 recent dari total 20
        return None
    winrate     = float((recent["PnL"] > 0).mean() * 100)
    trade_count = len(recent)
    params      = st.session_state.cybernetic_params.copy()
    adjustment  = 0.0
    if winrate > 65:   adjustment += 0.20
    elif winrate > 55: adjustment += 0.10
    elif winrate < 40: adjustment -= 0.20
    if current_regime == "BULLISH":              adjustment += 0.15
    elif current_regime in ["SIDEWAYS","VOLATILE"]: adjustment -= 0.15
    if trade_count < 20: adjustment -= 0.10
    lr = CYBER_CONFIG["learning_rate"]
    params["min_score"]             = max(60, min(95, int(params["min_score"] * (1 + adjustment * lr))))
    params["execute_now_threshold"] = max(80, min(98, int(params["execute_now_threshold"] * (1 + adjustment * lr * 0.8))))
    params["min_rr"]                = max(1.8, min(3.0, round(params["min_rr"] + adjustment * 0.3, 1)))
    params["last_adjust_date"]      = str(datetime.now().date())
    params["adjustment_history"].append({
        "date": datetime.now().strftime("%Y-%m-%d"), "regime": current_regime,
        "winrate": round(winrate, 1), "adjustment": round(adjustment, 3),
        "new_min_score": params["min_score"],
    })
    st.session_state.cybernetic_params = params
    save_state()
    return params

# ============================================================
# ENTRY SYSTEM
# ============================================================
# [B1] entry_system terima thresholds & cyber_params secara eksplisit
# agar aman dipanggil dari background thread (tidak ada st.session_state)
def entry_system(row: pd.Series,
                 thresholds: dict | None = None,
                 cyber_params: dict | None = None) -> str:
    # Fallback ke session_state hanya jika dipanggil dari UI context
    if thresholds is None:
        thresholds = st.session_state.get("dynamic_thresholds") or {}
    if cyber_params is None:
        cyber_params = st.session_state.get("cybernetic_params") or {}

    exec_now_th = thresholds.get("execute_now", 85)
    exec_th     = thresholds.get("execute", 75)
    ready_th    = thresholds.get("ready", 65)
    min_rr      = cyber_params.get("min_rr", 1.8)

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

    # [B3] EXECUTE NOW: tidak lagi pakai timing (self-contradictory)
    # Cukup: score sangat tinggi + bandar kuat + breakout valid + momentum ok
    if (score >= exec_now_th and rr >= 2.0 and
            breakout == "VALID" and bandar >= 3 and momentum >= 1):
        return "🔥 EXECUTE NOW"

    if score >= exec_th and rr >= min_rr and breakout in ("VALID", "WEAK") and bandar >= 2:
        return "✅ EXECUTE"

    if score >= ready_th:
        return "⏳ READY"

    return "❌ SKIP"

# ============================================================
# SESSION STATE INIT
# ============================================================
@st.cache_resource
def _load_persistent_state():
    return load_state()

if "state_loaded" not in st.session_state:
    _state = _load_persistent_state()
    st.session_state.cybernetic_params = _state.get("cybernetic_params", DEFAULT_CYBER.copy())
    st.session_state.signal_lock       = _state.get("signal_lock", {})
    st.session_state.balance           = _state.get("balance", 800_000)  # [I5]
    st.session_state.state_loaded      = True

if "active_trades"      not in st.session_state:
    st.session_state.active_trades = pd.read_csv(ACTIVE_FILE) if os.path.exists(ACTIVE_FILE) else pd.DataFrame()
if "journal"            not in st.session_state:
    st.session_state.journal = pd.read_csv(JOURNAL_FILE) if os.path.exists(JOURNAL_FILE) else pd.DataFrame()
if "scan_result"        not in st.session_state: st.session_state.scan_result        = None
if "sector_table"       not in st.session_state: st.session_state.sector_table       = None
if "dynamic_thresholds" not in st.session_state: st.session_state.dynamic_thresholds = None
if "last_regime"        not in st.session_state: st.session_state.last_regime        = "-"
if "debug_log"          not in st.session_state: st.session_state.debug_log          = []
TOP_N_RESULTS = 5   # Fixed: selalu tampilkan 5 kandidat terbaik siap eksekusi

# ============================================================
# IMPORTS ENGINE & CONFIG  [F4] dead imports dihapus
# ============================================================
from engine.probability_engine    import runner_probability
from engine.runner_engine         import runner_prediction
from engine.pullback_quality_engine import pullback_quality
from engine.sector_engine         import sector_momentum
from engine.liquidity_engine      import liquidity_trap
from engine.regime_engine         import detect_market_regime
from config.universe              import ISSI_UNIVERSE, SECTOR_MAP, get_sector

# ============================================================
# LOAD MARKET DATA
# ============================================================
@st.cache_data(ttl=300)
def load_market() -> dict[str, pd.DataFrame]:
    raw = yf.download(
        tickers=ISSI_UNIVERSE, period="6mo", interval="1d",
        group_by="ticker", progress=False, auto_adjust=True,
    )
    market = {}
    for s in ISSI_UNIVERSE:
        try:
            df = raw[s].dropna()
            if len(df) < 60:                              continue
            if df["Close"].squeeze().iloc[-1] <= 0:       continue
            if df["Volume"].squeeze().tail(5).mean() <= 0: continue
            market[s] = df
        except Exception:
            continue
    return market

# ============================================================
# [F1] SCAN CORE — satu fungsi inti, dipakai run_scanner & auto_scan
# ============================================================
def scan_core(market: dict, balance: float, top_n: int = 5,
              show_progress: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, dict, str, pd.DataFrame]:
    """
    Inti scanner.
    Return: (scan_df, debug_df, thresholds, regime, sector_df)
    """
    regime       = detect_market_regime(market)
    sector_power = sector_momentum(market, SECTOR_MAP)
    sector_df    = pd.DataFrame(
        [{"Sector": k, "Strength": round(v, 2)} for k, v in sector_power.items()]
    ).sort_values("Strength", ascending=False)

    positive_sectors = {row["Sector"] for _, row in sector_df.iterrows() if row["Strength"] > 0}

    candidates = []
    debug_log  = []
    total      = len([t for t in ISSI_UNIVERSE if t in market])
    count      = 0
    prog       = st.progress(0, text="Scanning...") if show_progress else None

    for ticker, df in market.items():
        if ticker not in ISSI_UNIVERSE:
            continue
        count += 1
        if prog:
            prog.progress(count / max(total, 1), text=f"Scanning {ticker}...")

        try:
            sector    = get_sector(ticker)
            tkr_clean = ticker.replace(".JK", "")

            # Filter 1: Sektor
            if sector not in positive_sectors:
                sec_str = next((r["Strength"] for _, r in sector_df.iterrows()
                                if r["Sector"] == sector), None)
                debug_log.append({"Ticker": tkr_clean, "Sector": sector,
                    "RSI": "-", "EMA_OK": "-", "Bandar": "-", "Breakout": "-",
                    "Confluence": "-", "RR": "-", "Score": "-",
                    "❌ Gugur di": f"Sektor lemah (strength={sec_str})"})
                continue

            # Filter 2: RSI — [K1] adaptif per regime
            rsi_ok, rsi_value = rsi_gate(df, regime)
            if not rsi_ok:
                debug_log.append({"Ticker": tkr_clean, "Sector": sector,
                    "RSI": round(rsi_value, 1), "EMA_OK": "-", "Bandar": "-",
                    "Breakout": "-", "Confluence": "-", "RR": "-", "Score": "-",
                    "❌ Gugur di": f"RSI out of range ({rsi_value:.1f}) untuk regime {regime}"})
                continue

            ema_ok, last_price, ema_val = ema_trend_filter(df)
            atr    = calculate_atr(df)
            entry  = last_price
            sl     = calculate_sl_atr(entry, atr)
            target = find_target(df, entry)
            rr     = risk_reward(entry, sl, target)
            lot    = position_sizing(balance, 0.02, entry, sl, atr)

            momentum = momentum_confirmation(df)
            accum    = accumulation_phase(df)
            bandar   = bandar_detection(df)
            breakout = breakout_confirmation(df)
            ft       = follow_through(df)
            timing   = entry_timing(df)
            chg_pct  = daily_change_pct(df)

            # Filter 3: Bandar & Breakout
            if bandar < 2 or breakout == "WAIT":
                reason = []
                if bandar < 2:         reason.append(f"Bandar rendah ({bandar})")
                if breakout == "WAIT": reason.append("Breakout WAIT")
                debug_log.append({"Ticker": tkr_clean, "Sector": sector,
                    "RSI": round(rsi_value, 1), "EMA_OK": "✅" if ema_ok else "❌",
                    "Bandar": bandar, "Breakout": breakout,
                    "Confluence": "-", "RR": round(rr, 1), "Score": "-",
                    "❌ Gugur di": " | ".join(reason)})
                continue

            intraday = intraday_confirm(ticker)
            prob     = runner_probability(df)
            runner   = runner_prediction(df)
            quality  = pullback_quality(df)
            liq_raw  = liquidity_trap(df)
            liq_str  = "🔴 TRAP" if liq_raw == "TRAP" else "🟢 OK"

            # Filter 4: Confluence — [K2] min adaptif per regime
            conf_count, conf_signals, conf_passed = confluence_check(
                momentum, accum, bandar, breakout, rr, ema_ok, regime)
            if not conf_passed:
                failed = [k for k, v in conf_signals.items() if not v]
                min_c  = 4 if regime == "BULLISH" else 3
                debug_log.append({"Ticker": tkr_clean, "Sector": sector,
                    "RSI": round(rsi_value, 1), "EMA_OK": "✅" if ema_ok else "❌",
                    "Bandar": bandar, "Breakout": breakout,
                    "Confluence": f"{conf_count}/6", "RR": round(rr, 1), "Score": "-",
                    "❌ Gugur di": f"Confluence {conf_count}/6 < {min_c} (gagal: {', '.join(failed)})"})
                continue

            # Filter 5: RR
            if rr < 1.8:
                debug_log.append({"Ticker": tkr_clean, "Sector": sector,
                    "RSI": round(rsi_value, 1), "EMA_OK": "✅" if ema_ok else "❌",
                    "Bandar": bandar, "Breakout": breakout,
                    "Confluence": f"{conf_count}/6", "RR": round(rr, 1), "Score": "-",
                    "❌ Gugur di": f"RR terlalu rendah ({rr:.1f}, min 1.8)"})
                continue

            # Score + [B2] cap yang benar setelah SEMUA bonus
            score  = calculate_score(prob, runner, quality, rr, liq_str, bandar)
            score += momentum * 0.8 + accum * 0.9 + ft * 0.7 + intraday * 0.5
            if momentum == 2:               score += 1
            if ft == 2:                     score += 1
            if last_price > ema_val * 1.01: score += 1
            score = min(100.0, score)       # [B2] cap SETELAH semua bonus

            debug_log.append({"Ticker": tkr_clean, "Sector": sector,
                "RSI": round(rsi_value, 1), "EMA_OK": "✅" if ema_ok else "❌",
                "Bandar": bandar, "Breakout": breakout,
                "Confluence": f"{conf_count}/6", "RR": round(rr, 1),
                "Score": round(score, 1), "❌ Gugur di": "✅ LOLOS — masuk kandidat"})

            candidates.append({
                "BUY": False, "Ticker": tkr_clean, "Sector": sector,
                "Action": "", "Score": round(score, 2),
                "Probability": int(prob), "RunnerScore": int(runner),
                "PullbackQuality": quality, "Liquidity": liq_str,
                "RSI": round(rsi_value, 1), "RR": round(rr, 1),
                "Change%": chg_pct,
                "Momentum": momentum, "Accumulation": accum,
                "BandarScore": bandar, "Breakout": breakout,
                "FT": ft, "INTRA": intraday, "Confluence": conf_count,
                "Entry": idr(entry), "SL": idr(sl), "Target": idr(target),
                "Lot": lot, "Timing": timing, "ATR": round(atr, 0),
                "EMA50": round(ema_val, 0),
            })

        except Exception as e:
            debug_log.append({"Ticker": ticker.replace(".JK", ""),
                "Sector": get_sector(ticker), "RSI": "-", "EMA_OK": "-",
                "Bandar": "-", "Breakout": "-", "Confluence": "-", "RR": "-",
                "Score": "-", "❌ Gugur di": f"⚠️ Exception: {str(e)[:60]}"})

    if prog:
        prog.empty()

    if not candidates:
        return pd.DataFrame(), pd.DataFrame(debug_log), {}, regime, sector_df

    thresholds = get_dynamic_thresholds([c["Score"] for c in candidates])

    # [B1] Pass thresholds eksplisit ke entry_system agar aman di background thread
    cyber_params = {}
    try:
        cyber_params = st.session_state.cybernetic_params
    except Exception:
        _st = load_state()
        cyber_params = _st.get("cybernetic_params", DEFAULT_CYBER.copy())

    scan_df = pd.DataFrame(candidates).sort_values("Score", ascending=False)
    scan_df["Action"] = scan_df.apply(
        lambda r: entry_system(r, thresholds=thresholds, cyber_params=cyber_params),
        axis=1
    )
    scan_df = scan_df[scan_df["Action"] != "❌ SKIP"].head(top_n)

    return scan_df, pd.DataFrame(debug_log), thresholds, regime, sector_df

# ============================================================
# RUN SCANNER (UI) — [F1] pakai scan_core
# ============================================================
def run_scanner():
    market = load_market()
    if not market:
        st.error("Gagal memuat data market. Cek koneksi internet.")
        return

    cybernetic_feedback_engine(st.session_state.journal,
                               st.session_state.get("last_regime", "-"))

    # [K6] scan_core handle sector_momentum sekaligus — tidak duplikat
    scan_df, debug_df, thresholds, regime, sector_df = scan_core(
        market, st.session_state.balance,
        top_n=TOP_N_RESULTS, show_progress=True
    )

    st.session_state.last_regime        = regime
    st.session_state.dynamic_thresholds = thresholds
    st.session_state.debug_log          = debug_df.to_dict("records") if not debug_df.empty else []
    st.session_state.scan_result        = scan_df
    st.session_state.sector_table       = sector_df   # [K7] dari scan_core langsung

    if scan_df.empty:
        return

    # Telegram alert
    now_ts    = time.time()
    lock_time = 3600
    sent      = []

    for _, row in scan_df.iterrows():
        tkr    = row["Ticker"]
        action = row.get("Action", "")
        if action not in ("🔥 EXECUTE NOW", "✅ EXECUTE"):
            continue
        if now_ts - st.session_state.signal_lock.get(tkr, 0) < lock_time:
            continue

        chg = row.get("Change%", 0)
        msg = (
            f"{'🔥 ATS EXECUTE NOW' if 'NOW' in action else '✅ ATS EXECUTE'}\n\n"
            f"Ticker     : {tkr}\n"
            f"Action     : {action}\n"
            f"Score      : {row.get('Score', 0):.1f}\n"
            f"RR         : {row.get('RR', 0):.1f}\n"
            f"Change     : {chg:+.2f}%\n"   # [I6]
            f"Confluence : {row.get('Confluence', 0)}/6\n"
            f"RSI        : {row.get('RSI', 0):.1f}\n"
            f"Breakout   : {row.get('Breakout', '-')}\n"
            f"Regime     : {regime}\n"
            f"Sector     : {row.get('Sector', '-')}\n\n"
            f"Entry   : {row.get('Entry', '-')}\n"
            f"SL      : {row.get('SL', '-')}\n"
            f"Target  : {row.get('Target', '-')}\n"
            f"Lot     : {row.get('Lot', '-')}\n\n"
            f"{'⚡ LANGSUNG EKSEKUSI' if 'NOW' in action else '✅ TUNGGU KONFIRMASI'}\n"
            f"⚠️ Ikuti SL. No FOMO.\n\nATS SuperEngine V4.0"
        )
        send_telegram(msg)
        st.session_state.signal_lock[tkr] = now_ts
        sent.append(tkr)

    # [I7] Scan summary Telegram
    if not sent and not scan_df.empty:
        top = scan_df.iloc[0]
        send_telegram(
            f"📊 ATS Scan Selesai — {get_wib_now()}\n"
            f"Kandidat: {len(scan_df)} | Regime: {regime}\n"
            f"Top: {top['Ticker']} (Score {top['Score']:.1f}, RR {top['RR']:.1f})\n"
            f"Belum ada sinyal EXECUTE hari ini."
        )

    save_state()
    if sent:
        st.success(f"✅ Alert Telegram: {', '.join(sent)}")

# ============================================================
# AUTO SCAN BACKGROUND — [F1] pakai scan_core, [F2] baca balance
# ============================================================
def auto_scan_background():
    if not is_market_open():
        return

    now_label = datetime.now(WIB).strftime("%H:%M WIB")
    send_telegram(f"🤖 ATS AutoScan dimulai — {now_label}")

    try:
        raw = yf.download(
            tickers=ISSI_UNIVERSE, period="6mo", interval="1d",
            group_by="ticker", progress=False, auto_adjust=True,
        )
        market = {}
        for s in ISSI_UNIVERSE:
            try:
                df = raw[s].dropna()
                if len(df) < 60: continue
                if df["Close"].squeeze().iloc[-1] <= 0: continue
                if df["Volume"].squeeze().tail(5).mean() <= 0: continue
                market[s] = df
            except Exception:
                continue

        if not market:
            send_telegram("⚠️ ATS AutoScan: Gagal load market data.")
            return

        # [F2] Baca balance dari state file, bukan hardcode
        _state  = load_state()
        balance = _state.get("balance", 800_000)
        sig_lock = _state.get("signal_lock", {})

        # [B1] scan_core sekarang return 5-tuple termasuk sector_df
        scan_df, _, thresholds, regime, _ = scan_core(
            market, balance, top_n=5, show_progress=False
        )

        if scan_df.empty:
            send_telegram(f"📭 ATS AutoScan {now_label}: Tidak ada kandidat hari ini. Regime: {regime}")
            return

        # Simpan thresholds ke session state agar entry_system bisa baca
        # (background thread tidak punya session state, pakai dict langsung)
        now_ts    = time.time()
        lock_time = 3600
        sent_any  = False

        for _, row in scan_df.iterrows():
            tkr    = row["Ticker"]
            action = row.get("Action", "")
            if action not in ("🔥 EXECUTE NOW", "✅ EXECUTE"): continue
            if now_ts - sig_lock.get(tkr, 0) < lock_time: continue

            chg = row.get("Change%", 0)
            msg = (
                f"🤖 ATS AUTO-SCAN — {now_label}\n\n"
                f"Ticker     : {tkr}\n"
                f"Action     : {action}\n"
                f"Score      : {row.get('Score', 0):.1f}\n"
                f"RR         : {row.get('RR', 0):.1f}\n"
                f"Change     : {chg:+.2f}%\n"
                f"Confluence : {row.get('Confluence', 0)}/6\n"
                f"RSI        : {row.get('RSI', 0):.1f}\n"
                f"Breakout   : {row.get('Breakout', '-')}\n"
                f"Regime     : {regime}\n"
                f"Sector     : {row.get('Sector', '-')}\n\n"
                f"Entry   : {row.get('Entry', '-')}\n"
                f"SL      : {row.get('SL', '-')}\n"
                f"Target  : {row.get('Target', '-')}\n"
                f"Lot     : {row.get('Lot', '-')}\n\n"
                f"{'⚡ LANGSUNG EKSEKUSI' if 'NOW' in action else '✅ TUNGGU KONFIRMASI'}\n"
                f"⚠️ No FOMO. Gunakan SL.\n\nATS SuperEngine V4.0"
            )
            send_telegram(msg)
            sig_lock[tkr] = now_ts
            sent_any = True

        # [I7] Summary kalau tidak ada EXECUTE
        if not sent_any:
            top = scan_df.iloc[0]
            send_telegram(
                f"📊 AutoScan {now_label} — {len(scan_df)} kandidat\n"
                f"Regime: {regime}\n"
                f"Top: {top['Ticker']} Score {top['Score']:.1f} RR {top['RR']:.1f}\n"
                f"Belum ada sinyal EXECUTE (score belum cukup / lock period)."
            )

        # Update signal lock
        try:
            with open(STATE_FILE, "r") as f:
                st_data = json.load(f)
        except Exception:
            st_data = {}
        st_data["signal_lock"] = sig_lock
        with open(STATE_FILE, "w") as f:
            json.dump(st_data, f, indent=2)

    except Exception as e:
        send_telegram(f"❌ ATS AutoScan ERROR: {str(e)[:200]}")

# ============================================================
# SCHEDULER
# ============================================================
@st.cache_resource
def start_scheduler():
    scheduler = BackgroundScheduler(timezone=WIB)
    for sched in SCAN_SCHEDULE:
        scheduler.add_job(
            func=auto_scan_background,
            trigger=CronTrigger(day_of_week="mon-fri",
                                hour=sched["hour"], minute=sched["minute"],
                                timezone=WIB),
            id=f"scan_{sched['label'].replace(' ', '_')}",
            name=f"ATS Scan {sched['label']}",
            replace_existing=True, misfire_grace_time=120,
        )
    scheduler.start()

    # [I2] Notifikasi saat app start/restart
    send_telegram(
        f"🟢 ATS SuperEngine V4.0 — SERVER ONLINE\n"
        f"⏰ {datetime.now(WIB).strftime('%Y-%m-%d %H:%M WIB')}\n"
        f"Jadwal: 09:05 | 11:30 | 13:35 | 15:00 WIB (Senin–Jumat)\n"
        f"Auto-scan aktif ✅"
    )
    return scheduler

_scheduler = start_scheduler()

# ============================================================
# UI
# ============================================================
st.set_page_config(layout="wide", page_title="ATS SuperEngine V4.0")

def next_scan_label() -> str:
    now_wib = datetime.now(WIB)
    if now_wib.weekday() >= 5 or is_holiday(now_wib.date()):
        return "Hari bursa berikutnya 09:05 WIB"
    for sched in SCAN_SCHEDULE:
        t = now_wib.replace(hour=sched["hour"], minute=sched["minute"], second=0)
        if now_wib < t:
            return f"{sched['hour']:02d}:{sched['minute']:02d} WIB ({sched['label']})"
    return "Besok 09:05 WIB"

st.markdown("""
    <style>
    div[data-testid="stButton"] > button[kind="primary"] {
        background-color: #16a34a !important; border-color: #16a34a !important; color: #fff !important;
    }
    div[data-testid="stButton"] > button[kind="primary"]:hover {
        background-color: #15803d !important; border-color: #15803d !important;
    }
    </style>
""", unsafe_allow_html=True)

col_title, col_info = st.columns([3, 1])
with col_title:
    st.title("ATS SuperEngine V4.0")
    market_status = "🟢 BUKA" if is_market_open() else "🔴 TUTUP"
    holiday_note  = " 🏖️ Libur" if is_holiday(datetime.now(WIB).date()) else ""
    st.caption(
        f"🕐 {get_wib_now()}  |  Bursa IDX: {market_status}{holiday_note}  |  "
        f"Regime: {st.session_state.get('last_regime', '-')}  |  "
        f"⏰ Auto-scan: {next_scan_label()}"
    )
with col_info:
    cp = st.session_state.cybernetic_params
    st.metric("Min Score (Adaptif)", cp["min_score"])

tabs = st.tabs(["📖 HOW TO USE", "📊 TRADING DESK", "💼 ACCOUNT", "📋 REPORT", "🕌 ISSI CHECK"])

# ─────────────────────────────────────────────────────────────
# TAB 0 — HOW TO USE
# ─────────────────────────────────────────────────────────────
with tabs[0]:
    st.markdown("## 📖 Panduan Penggunaan ATS SuperEngine V4.0")
    st.markdown("#### *Scanner Saham Syariah Otomatis — Mudah, Disiplin, Berkah*")
    st.markdown("---")

    # APA ITU ATS
    st.markdown("### 🤖 Apa itu ATS SuperEngine?")
    st.info(
        "ATS (Automated Trading Scanner) adalah sistem yang **secara otomatis memindai "
        "98 saham syariah ISSI** setiap hari kerja dan memberitahu kamu saham mana yang "
        "layak dibeli hari ini. Kamu tidak perlu analisis manual — sistem sudah mengerjakan "
        "semuanya dan mengirim notifikasi langsung ke **Telegram HP** kamu."
    )
    st.markdown("---")

    # CARA KERJA
    st.markdown("### ⚙️ Cara Kerja Sistem")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown("**1️⃣ Scan Otomatis**")
        st.markdown("Setiap Senin–Jumat, 98 saham syariah dipindai di **4 waktu** berbeda setiap harinya")
    with c2:
        st.markdown("**2️⃣ Filter 5 Lapis**")
        st.markdown("Setiap saham difilter ketat: Sektor → RSI → Bandar → Confluence → Risk/Reward")
    with c3:
        st.markdown("**3️⃣ Scoring 0–100**")
        st.markdown("Saham yang lolos diberi nilai. Hanya **5 terbaik** yang ditampilkan setiap hari")
    with c4:
        st.markdown("**4️⃣ Notif Telegram**")
        st.markdown("Sinyal kuat dikirim otomatis ke HP kamu. Tidak perlu buka laptop sama sekali")
    st.markdown("---")

    # JADWAL
    st.markdown("### ⏰ Jadwal Auto-Scan Harian")
    st.markdown(
        "Sistem berjalan **otomatis di server** setiap hari Senin–Jumat "
        "termasuk hari libur nasional IDX yang sudah diprogram. "
        "Kamu tidak perlu melakukan apapun."
    )
    j1, j2, j3, j4 = st.columns(4)
    j1.success("**09:05 WIB**\n\nPre-Open\n\n*Segera setelah bursa buka*")
    j2.success("**11:30 WIB**\n\nMid Sesi 1\n\n*Tengah sesi pagi*")
    j3.success("**13:35 WIB**\n\nOpen Sesi 2\n\n*Setelah jeda ishoma*")
    j4.success("**15:00 WIB**\n\nPre-Closing\n\n*Peluang terakhir hari ini*")
    st.markdown("---")

    # SINYAL
    st.markdown("### 📊 Arti Sinyal di Kolom Action")
    a1, a2, a3, a4 = st.columns(4)
    with a1:
        st.error("🔥 **EXECUTE NOW**\n\nSinyal **terkuat**. Semua indikator hijau sempurna. Bisa langsung beli.")
    with a2:
        st.warning("✅ **EXECUTE**\n\nSinyal **kuat**. Layak beli. Boleh langsung eksekusi atau konfirmasi chart dulu.")
    with a3:
        st.info("⏳ **READY**\n\nSinyal **cukup baik** tapi belum optimal. Pantau dulu, tunggu momentum masuk.")
    with a4:
        st.info("⏸ **WAIT PULLBACK**\n\nHarga sedang naik tinggi. Tunggu koreksi kecil sebelum beli.")
    st.markdown("---")

    # KOLOM TABEL
    st.markdown("### 🔍 Penjelasan Kolom Tabel Hasil Scan")
    t1, t2 = st.columns(2)
    with t1:
        st.markdown("""
| Kolom | Artinya |
|---|---|
| **Score** | Nilai total 0–100. Makin tinggi makin bagus |
| **RR** | Risk/Reward. Min 1.8x. Potensi untung 1.8× dari risiko |
| **RSI** | Kekuatan tren harga. Zona ideal: 42–72 |
| **Breakout** | VALID = sudah tembus resistance dengan volume tinggi |
| **BandarScore** | Deteksi aktivitas investor besar / institusi |
| **Confluence** | Jumlah sinyal yang sepakat (dari 6). Min harus 4/6 |
        """)
    with t2:
        st.markdown("""
| Kolom | Artinya |
|---|---|
| **Change%** | Perubahan harga hari ini dalam persen |
| **Entry** | Harga masuk yang disarankan |
| **SL** | Stop Loss — batas kerugian. Wajib dipasang! |
| **Target** | Harga target ambil profit |
| **Lot** | Jumlah lot disarankan sesuai modal & risiko 2% |
| **ATR** | Volatilitas harian. Dipakai untuk hitung SL & Lot |
        """)
    st.markdown("---")

    # LANGKAH HARIAN
    st.markdown("### 🚀 Rutinitas Harian Pakai ATS")
    st.markdown("""
**📱 Pagi hari — cek Telegram:**
1. Buka notifikasi Telegram dari ATS
2. Ada sinyal **🔥 EXECUTE NOW** atau **✅ EXECUTE**? → Buka dashboard untuk konfirmasi chart
3. Cek chart TradingView di tab **📊 Trading Desk** → pastikan tren sesuai
4. Kalau yakin → centang kolom **BUY** di tabel → klik **Save Active Trades**
5. Eksekusi di aplikasi broker kamu (RHB, Ajaib, IPOT, Stockbit, dll)

**📋 Setelah beli:**
1. Pasang **Stop Loss** di broker sesuai kolom SL di tabel — **jangan skip langkah ini**
2. Pasang **Take Profit** sesuai kolom Target
3. Catat di tab **📋 Report → Trade Journal** (penting untuk Cybernetic Engine belajar)

**🌙 Sore hari:**
1. Update status di **Active Trades** (ubah ke CLOSED jika sudah selesai)
2. Isi PnL di Journal untuk trade yang ditutup hari ini
    """)
    st.markdown("---")

    # MANAJEMEN RISIKO
    st.markdown("### ⚠️ Aturan Manajemen Risiko — WAJIB DIPATUHI")
    st.error("""
🚨  3 ATURAN EMAS yang TIDAK BOLEH dilanggar:

1. SELALU pasang Stop Loss — Jika harga turun ke level SL, langsung jual. Tanpa alasan. Tanpa "nanti balik".

2. Maksimal 5 posisi terbuka sekaligus — Sistem hanya tampilkan 5 kandidat terbaik untuk alasan ini. Jangan buka lebih.

3. Risk per trade maksimal 2% dari modal — Sistem sudah menghitung lot yang aman. Jangan beli melebihi lot yang disarankan.
    """)
    rr1, rr2, rr3 = st.columns(3)
    rr1.metric("Risk per Trade", "Maks 2% Modal")
    rr2.metric("Posisi Terbuka", "Maks 5 Saham")
    rr3.metric("Min Risk/Reward", "1 : 1.8")
    st.markdown("---")

    # SETUP PERTAMA
    st.markdown("### 🛠️ Setup Pertama Kali (Lakukan Sekali Saja)")
    st.markdown("""
1. **Set Modal** → Tab **💼 Account** → isi kolom *Modal/Balance* sesuai modal trading kamu
2. **Test koneksi** → Klik **RUN ATS SCANNER** sekali → cek apakah notifikasi masuk di Telegram
3. **Kenali universe** → Tab **🕌 ISSI CHECK** → lihat semua saham yang ada di scanner
    """)
    st.markdown("---")

    # FAQ
    st.markdown("### ❓ Pertanyaan yang Sering Ditanyakan")

    with st.expander("Apakah semua saham di sini sudah pasti halal/syariah?"):
        st.markdown("""
Ya. ATS hanya scan saham yang masuk **Indeks Saham Syariah Indonesia (ISSI)** —
daftar resmi OJK yang diperbarui setiap 6 bulan. Saham bank konvensional (BRI, BNI, Mandiri),
rokok (Sampoerna), dan saham dengan rasio utang riba berlebih **sudah otomatis dikeluarkan**
dari universe scanner ini.
        """)

    with st.expander("Kenapa tidak ada sinyal hari ini?"):
        st.markdown("""
Beberapa kemungkinan penyebabnya:
- **Market sedang DISTRIBUTION** — kondisi tidak kondusif, lebih baik tidak masuk
- **Semua sektor lemah** — sistem filter otomatis hanya masuk ke sektor yang sedang momentum positif
- **Saham sudah overbought** — RSI di atas 72, terlalu mahal untuk entry baru

👉 Buka expander **🔍 Scan Debug** setelah scan untuk lihat detail alasan setiap saham gugur.
Tidak ada sinyal = **sistem melindungi modal kamu** dari kondisi yang tidak aman.
        """)

    with st.expander("Berapa modal minimum yang disarankan?"):
        st.markdown("""
- **Rp 1.000.000** → Risk/trade Rp 20.000 — cocok untuk belajar
- **Rp 5.000.000** → Risk/trade Rp 100.000 — mulai terasa signifikan
- **Rp 10.000.000+** → Optimal untuk sistem ini bekerja maksimal

Di bawah Rp 1.000.000 perhitungan lot bisa jadi 1 terus karena terlalu kecil untuk diversifikasi.
        """)

    with st.expander("Apakah sinyal ATS dijamin akurat / pasti profit?"):
        st.markdown("""
**Tidak ada sistem trading yang 100% akurat — termasuk ATS.**

Yang membuat sistem ini bekerja bukan karena selalu benar, tapi karena:
- **Risk/Reward minimal 1:1.8** — bahkan dengan win rate 40% kamu masih bisa profit jangka panjang
- **Stop Loss wajib** — kerugian dari sinyal yang salah selalu terbatas
- **Filter ketat 5 lapis** — meminimalkan sinyal buruk

Gunakan ATS sebagai **alat bantu analisis**, bukan jaminan profit. Keputusan final tetap di tangan kamu.
        """)

    with st.expander("Apa itu Cybernetic Engine dan kapan aktif?"):
        st.markdown("""
Cybernetic Engine adalah fitur **adaptif** — sistem belajar dari riwayat trading kamu sendiri.

Setelah kamu punya minimal **20 trade** di Journal dengan kolom PnL terisi, sistem akan otomatis
menyesuaikan threshold score berdasarkan performa aktual:
- Win rate **> 65%** → threshold dinaikkan (lebih selektif, cari yang terbaik saja)
- Win rate **< 40%** → threshold diturunkan (lebih fleksibel di kondisi susah)

Semakin rajin kamu isi Journal, semakin pintar sistemnya. 🧠
        """)

    with st.expander("Bagaimana cara mengisi Trade Journal dengan benar?"):
        st.markdown("""
Tab **📋 Report** → isi tabel Journal:

| Kolom | Contoh | Keterangan |
|---|---|---|
| **Date** | 2025-04-25 | Tanggal beli |
| **Ticker** | BRIS | Kode saham |
| **Entry** | 1450 | Harga beli per lembar |
| **Exit** | 1600 | Harga jual (isi setelah tutup posisi) |
| **Lot** | 5 | Jumlah lot yang dibeli |
| **PnL** | 750000 | Untung/rugi dalam Rupiah (negatif = rugi) |
| **Notes** | Signal V4.0 | Catatan bebas |

Klik **💾 Save Journal** setelah selesai mengisi.
        """)

    st.markdown("---")
    st.caption(
        "ATS SuperEngine V4.0 | Scanner Saham Syariah ISSI | "
        "Bukan rekomendasi investasi — selalu lakukan riset mandiri | "
        "Gunakan dengan manajemen risiko yang ketat 🤲"
    )

# ─────────────────────────────────────────────────────────────
# TAB 1 — TRADING DESK
# ─────────────────────────────────────────────────────────────
with tabs[1]:
    st.subheader("🔍 Scanner Saham Syariah ISSI")

    b1, b2, b3 = st.columns(3)
    b1.metric("💰 Balance", f"Rp {idr(st.session_state.balance)}")
    b2.metric("⚠️ Risk/Trade (2%)", f"Rp {idr(st.session_state.balance * 0.02)}")
    b3.metric("📊 Regime", st.session_state.get("last_regime", "-"))
    st.caption("_Ubah balance di tab **💼 Account**_")
    st.markdown("---")

    if st.button("🚀 RUN ATS SCANNER V4.0", type="primary", use_container_width=True):
        with st.spinner("ATS scanning seluruh universe ISSI..."):
            run_scanner()
        st.success("✅ Scan selesai — menampilkan 5 kandidat terbaik siap eksekusi")

    if st.session_state.dynamic_thresholds:
        th = st.session_state.dynamic_thresholds
        st.info(
            f"📊 **Threshold dinamis** — "
            f"Execute Now ≥ {th['execute_now']:.0f} | "
            f"Execute ≥ {th['execute']:.0f} | "
            f"Ready ≥ {th['ready']:.0f}  "
            f"*(dari {th.get('n_samples', 0)} kandidat)*"
        )

    if st.session_state.scan_result is not None and not st.session_state.scan_result.empty:
        df = st.session_state.scan_result.copy()

        # TradingView
        st.markdown("---")
        selected = st.selectbox("📈 Pilih saham untuk chart", df["Ticker"].tolist())
        st.components.v1.html(
            f'<iframe src="https://s.tradingview.com/widgetembed/?symbol=IDX:{selected}'
            f'&interval=D&theme=dark&style=1&locale=id" '
            f'width="100%" height="550" frameborder="0"></iframe>', height=560)

        st.markdown("---")
        m1, m2, m3, m4, m5 = st.columns(5)
        best = df.iloc[0]
        m1.metric("Top Score",  f"{best['Score']:.1f}")
        m2.metric("Top RR",     f"{best['RR']:.1f}x")
        m3.metric("Top Ticker", best["Ticker"])
        m4.metric("Confluence", f"{best['Confluence']}/6")
        m5.metric("Change",     f"{best.get('Change%', 0):+.2f}%")   # [I6]

        st.subheader("🏆 Top Runner")
        cols_show = ["BUY","Action","Ticker","Sector","Score","RR","Change%",
                     "Confluence","RSI","Breakout","BandarScore","Momentum",
                     "Accumulation","Entry","SL","Target","Lot","Timing","ATR"]
        cols_show = [c for c in cols_show if c in df.columns]

        edited = st.data_editor(df[cols_show], use_container_width=True, hide_index=True,
            column_config={
                "BUY":          st.column_config.CheckboxColumn("BUY"),
                "Action":       st.column_config.TextColumn("Action"),
                "Score":        st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f"),
                "Confluence":   st.column_config.NumberColumn("Conf/6", min_value=0, max_value=6),
                "BandarScore":  st.column_config.NumberColumn("Bandar", min_value=-4, max_value=4),
                "Momentum":     st.column_config.NumberColumn("Mom", min_value=0, max_value=2),
                "Accumulation": st.column_config.NumberColumn("Accum", min_value=0, max_value=3),
                "Breakout":     st.column_config.TextColumn("Breakout"),
                "Timing":       st.column_config.TextColumn("Timing"),
                "RR":           st.column_config.NumberColumn("RR", format="%.1f"),
                "RSI":          st.column_config.NumberColumn("RSI", format="%.1f"),
                "Change%":      st.column_config.NumberColumn("Chg%", format="%.2f"),
                "Lot":          st.column_config.NumberColumn("Lot"),
                "ATR":          st.column_config.NumberColumn("ATR"),
            })

        buy_rows = edited[edited["BUY"] == True]
        if len(buy_rows) > 0:
            existing = st.session_state.active_trades["Ticker"].tolist() \
                if not st.session_state.active_trades.empty else []
            new_trades = buy_rows[~buy_rows["Ticker"].isin(existing)].copy()
            if len(new_trades) > 0:
                new_trades["Status"]    = "OPEN"
                new_trades["EntryTime"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                new_trades["ExitPrice"] = None   # [I3]
                new_trades["ExitDate"]  = None   # [I3]
                new_trades["PnL"]       = None
                st.session_state.active_trades = pd.concat(
                    [st.session_state.active_trades, new_trades], ignore_index=True)
                st.session_state.active_trades.to_csv(ACTIVE_FILE, index=False)
                st.success(f"✅ {len(new_trades)} trade masuk Active Trades")
            else:
                st.warning("Semua ticker sudah ada di Active Trades")

        if not st.session_state.active_trades.empty:
            st.markdown("---")
            st.subheader("📌 Active Trades")
            active_edited = st.data_editor(
                st.session_state.active_trades, num_rows="dynamic",
                use_container_width=True, hide_index=True)
            if st.button("💾 Save Active Trades"):
                st.session_state.active_trades = active_edited.reset_index(drop=True)
                st.session_state.active_trades.to_csv(ACTIVE_FILE, index=False)
                st.success("✅ Active Trades tersimpan")

    elif st.session_state.scan_result is not None:
        st.warning("⚠️ Tidak ada kandidat berkualitas hari ini. Coba saat regime BULLISH.")

    # Debug expander
    if st.session_state.debug_log:
        debug_df = pd.DataFrame(st.session_state.debug_log)
        gugur_counts = (
            debug_df[debug_df["❌ Gugur di"] != "✅ LOLOS — masuk kandidat"]["❌ Gugur di"]
            .str.extract(r"^([^(|]+)")[0].str.strip().value_counts().reset_index()
        )
        gugur_counts.columns = ["Alasan Gugur", "Jumlah Ticker"]

        with st.expander("🔍 Scan Debug — Kenapa saham tidak lolos?", expanded=False):
            st.caption(
                f"Total: **{len(debug_df)}** | "
                f"Lolos: **{(debug_df['❌ Gugur di'] == '✅ LOLOS — masuk kandidat').sum()}** | "
                f"Gugur: **{(debug_df['❌ Gugur di'] != '✅ LOLOS — masuk kandidat').sum()}**"
            )
            if not gugur_counts.empty:
                fig_d = px.bar(gugur_counts, x="Jumlah Ticker", y="Alasan Gugur",
                               orientation="h", color="Jumlah Ticker",
                               color_continuous_scale=["#22c55e","#f59e0b","#ef4444"],
                               title="Distribusi Alasan Gugur")
                fig_d.update_layout(height=300, showlegend=False, yaxis=dict(autorange="reversed"))
                st.plotly_chart(fig_d, use_container_width=True)

            col_f1, col_f2 = st.columns(2)
            with col_f1:
                filter_sektor = st.selectbox("Sektor", ["Semua"] +
                    sorted(debug_df["Sector"].dropna().unique().tolist()), key="dbg_sec")
            with col_f2:
                filter_status = st.selectbox("Status", ["Semua","✅ Lolos","❌ Gugur"], key="dbg_stat")

            filtered = debug_df.copy()
            if filter_sektor != "Semua":
                filtered = filtered[filtered["Sector"] == filter_sektor]
            if filter_status == "✅ Lolos":
                filtered = filtered[filtered["❌ Gugur di"] == "✅ LOLOS — masuk kandidat"]
            elif filter_status == "❌ Gugur":
                filtered = filtered[filtered["❌ Gugur di"] != "✅ LOLOS — masuk kandidat"]

            def color_rows(row):
                if row["❌ Gugur di"] == "✅ LOLOS — masuk kandidat":
                    return ["background-color: rgba(34,197,94,0.12)"] * len(row)
                return ["background-color: rgba(239,68,68,0.08)"] * len(row)

            st.dataframe(filtered.style.apply(color_rows, axis=1),
                use_container_width=True, hide_index=True,
                column_config={c: st.column_config.TextColumn(c) for c in filtered.columns})

    if st.session_state.sector_table is not None:
        st.markdown("---")
        st.subheader("🗺️ Sector Leader Radar")
        fig = px.bar(st.session_state.sector_table, x="Strength", y="Sector",
                     orientation="h", color="Strength",
                     color_continuous_scale=["#ef4444","#f59e0b","#22c55e"],
                     title="Kekuatan Sektor")
        fig.update_layout(height=400, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

# ─────────────────────────────────────────────────────────────
# TAB 2 — ACCOUNT
# ─────────────────────────────────────────────────────────────
with tabs[2]:
    st.subheader("💼 Manajemen Akun")
    col_inp, col_pad = st.columns([2, 3])
    with col_inp:
        balance_input = st.number_input("💰 Modal / Balance (Rp)",
            min_value=100_000, step=100_000, value=st.session_state.balance,
            key="balance_account_input",
            help="Modal trading. Dipakai untuk kalkulasi lot & risk per trade.")
        if balance_input != st.session_state.balance:
            st.session_state.balance = balance_input
            save_state()   # [I5] langsung simpan ke JSON
            st.success("✅ Balance diperbarui & tersimpan")
            time.sleep(0.4)
            st.rerun()
    st.markdown("---")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Balance",            f"Rp {idr(st.session_state.balance)}")
    c2.metric("Risk/Trade (2%)",    f"Rp {idr(st.session_state.balance * 0.02)}")
    c3.metric("Max 5 Posisi (40%)", f"Rp {idr(st.session_state.balance * 0.40)}")
    c4.metric("Safe Cash (60%)",    f"Rp {idr(st.session_state.balance * 0.60)}")

    st.markdown("---")
    st.subheader("🧠 Cybernetic Parameters")
    params = st.session_state.cybernetic_params
    cc1, cc2, cc3, cc4 = st.columns(4)
    cc1.metric("Min Score",       params["min_score"])
    cc2.metric("Execute Now Th.", params["execute_now_threshold"])
    cc3.metric("Min RR",          params["min_rr"])
    cc4.metric("Last Adjust",     str(params.get("last_adjust_date", "-")))
    st.caption(f"⚙️ Cybernetic aktif setelah **{CYBER_CONFIG['min_trades_for_adjust']} trade** di journal.")

    if params.get("adjustment_history"):
        st.markdown("**Riwayat Penyesuaian:**")
        st.dataframe(pd.DataFrame(params["adjustment_history"]).tail(10),
                     use_container_width=True, hide_index=True)

# ─────────────────────────────────────────────────────────────
# TAB 3 — REPORT
# ─────────────────────────────────────────────────────────────
with tabs[3]:
    st.subheader("📋 Trade Journal")

    # [I4] Validasi kolom wajib
    JOURNAL_COLS = ["Date", "Ticker", "Entry", "Exit", "Lot", "PnL", "Notes"]
    if st.session_state.journal.empty:
        st.session_state.journal = pd.DataFrame(columns=JOURNAL_COLS)

    edited_journal = st.data_editor(
        st.session_state.journal, num_rows="dynamic",
        use_container_width=True, hide_index=True,
        column_config={"PnL": st.column_config.NumberColumn("PnL (Rp)", format="%.0f")})

    if st.button("💾 Save Journal"):
        st.session_state.journal = edited_journal.reset_index(drop=True)
        st.session_state.journal.to_csv(JOURNAL_FILE, index=False)
        st.success("✅ Journal tersimpan")

    if not edited_journal.empty and "PnL" in edited_journal.columns:
        jdf = edited_journal.dropna(subset=["PnL"])
        if len(jdf) > 0:
            st.markdown("---")
            st.subheader("📈 Statistik Performa")
            wins         = (jdf["PnL"] > 0).sum()
            losses       = (jdf["PnL"] <= 0).sum()
            total_trades = len(jdf)
            winrate      = wins / total_trades * 100
            total_pnl    = jdf["PnL"].sum()
            avg_win      = jdf[jdf["PnL"] > 0]["PnL"].mean() if wins > 0 else 0
            avg_loss     = jdf[jdf["PnL"] <= 0]["PnL"].mean() if losses > 0 else 0
            pf           = abs(avg_win / avg_loss) if avg_loss != 0 else 0

            s1,s2,s3,s4,s5 = st.columns(5)
            s1.metric("Total Trade",   total_trades)
            s2.metric("Win Rate",      f"{winrate:.1f}%")
            s3.metric("Total PnL",     f"Rp {idr(total_pnl)}")
            s4.metric("Profit Factor", f"{pf:.2f}")
            s5.metric("Avg W/L",       f"{abs(avg_win/avg_loss):.2f}x" if avg_loss != 0 else "-")

            st.markdown("---")
            st.subheader("📉 Equity Curve & Drawdown")
            jdf_s = jdf.copy()
            if "Date" in jdf_s.columns:
                jdf_s = jdf_s.sort_values("Date")
            jdf_s["Cumulative PnL"] = jdf_s["PnL"].cumsum()
            jdf_s["Trade#"]         = range(1, len(jdf_s) + 1)

            fig_eq = go.Figure()
            fig_eq.add_trace(go.Scatter(x=jdf_s["Trade#"], y=jdf_s["Cumulative PnL"],
                mode="lines+markers", name="Equity",
                line=dict(color="#22c55e", width=2),
                fill="tozeroy", fillcolor="rgba(34,197,94,0.1)"))
            fig_eq.update_layout(title="Equity Curve", xaxis_title="Trade #",
                                 yaxis_title="PnL Kumulatif (Rp)", height=300)
            st.plotly_chart(fig_eq, use_container_width=True)

            cum_pnl = jdf_s["Cumulative PnL"]
            dd      = cum_pnl - cum_pnl.cummax()
            st.metric("Max Drawdown", f"Rp {idr(dd.min())}")
            fig_dd = go.Figure()
            fig_dd.add_trace(go.Bar(x=jdf_s["Trade#"], y=dd,
                name="Drawdown", marker_color="#ef4444"))
            fig_dd.update_layout(title="Drawdown per Trade", height=220)
            st.plotly_chart(fig_dd, use_container_width=True)

# ─────────────────────────────────────────────────────────────
# TAB 4 — ISSI CHECK
# ─────────────────────────────────────────────────────────────
with tabs[4]:
    st.subheader("🕌 ISSI Universe — Saham Syariah")
    st.caption(f"Total: {len(ISSI_UNIVERSE)} ticker verified syariah")
    sector_groups = defaultdict(list)
    for ticker in ISSI_UNIVERSE:
        sector_groups[get_sector(ticker)].append(ticker.replace(".JK", ""))
    for sector in sorted(sector_groups.keys()):
        with st.expander(f"**{sector}** ({len(sector_groups[sector])} saham)"):
            st.write(", ".join(sorted(sector_groups[sector])))

st.divider()
st.caption("ATS SuperEngine V4.0 | ISSI Syariah Scanner | Bukan rekomendasi investasi")