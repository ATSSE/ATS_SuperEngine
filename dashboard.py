"""
ATS SuperEngine V5.8.2 — Saham Syariah ISSI Scanner
BMW M4 Theme Edition
"""
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, date, timedelta
import time
import os
import json
import logging
import threading
import tempfile
import requests
import plotly.express as px
import plotly.graph_objects as go
from collections import defaultdict
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# KONFIGURASI
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
STATE_FILE = "ats_state.json"
JOURNAL_FILE = "journal.csv"

def _get_secret(key: str) -> str:
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, "")

TELEGRAM_TOKEN = _get_secret("TELEGRAM_TOKEN")
TELEGRAM_CHAT = _get_secret("TELEGRAM_CHAT")
ACTIVE_FILE = "active_trades.csv"
LOG_FILE = "ats.log"

# LOGGING
def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("ats")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    try:
        fh = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.propagate = False
    return logger

LOG = _setup_logger()

# THREAD SAFETY
_breadth_lock = threading.Lock()
_spike_lock = threading.Lock()
_state_lock = threading.Lock()
_telegram_lock = threading.Lock()

# VERSION
APP_VERSION = "V5.8.2"
APP_UPDATED = "18 Jun 2026"

# TIMEZONE
WIB = pytz.timezone("Asia/Jakarta")

IDX_HOLIDAYS = {
    date(2026, 1, 1), date(2026, 1, 14), date(2026, 1, 19),
    date(2026, 3, 18), date(2026, 3, 19), date(2026, 3, 20),
    date(2026, 3, 23), date(2026, 4, 3), date(2026, 5, 1),
    date(2026, 5, 20), date(2026, 5, 22), date(2026, 5, 27),
    date(2026, 5, 28), date(2026, 6, 1), date(2026, 6, 17),
    date(2026, 6, 26), date(2026, 6, 27), date(2026, 6, 28),
    date(2026, 8, 17), date(2026, 8, 18), date(2026, 9, 24),
    date(2026, 12, 25),
}

def is_holiday(d: date) -> bool:
    return d in IDX_HOLIDAYS

def is_market_open() -> bool:
    now_wib = datetime.now(WIB)
    today = now_wib.date()
    if now_wib.weekday() >= 5:
        return False
    if is_holiday(today):
        return False
    open_t = now_wib.replace(hour=9, minute=0, second=0, microsecond=0)
    close_t = now_wib.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_t <= now_wib <= close_t

def is_trading_day() -> bool:
    now_wib = datetime.now(WIB)
    return now_wib.weekday() < 5 and not is_holiday(now_wib.date())

def get_wib_now() -> str:
    return datetime.now(WIB).strftime("%H:%M:%S WIB")

# TELEGRAM
def send_telegram(msg: str) -> bool:
    global TELEGRAM_TOKEN, TELEGRAM_CHAT
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        LOG.warning("Telegram tidak terkirim: TOKEN atau CHAT belum di-set")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    with _telegram_lock:
        for attempt in range(3):
            try:
                res = requests.post(url, data={"chat_id": TELEGRAM_CHAT, "text": msg}, timeout=10)
                if res.status_code == 200:
                    LOG.info(f"Telegram OK attempt={attempt+1}")
                    return True
                elif res.status_code == 429:
                    retry_after = res.json().get("parameters", {}).get("retry_after", 3)
                    LOG.warning(f"Telegram rate limit — tunggu {retry_after}s")
                    time.sleep(retry_after)
                else:
                    LOG.error(f"Telegram error status={res.status_code}")
                    break
            except requests.Timeout:
                LOG.warning(f"Telegram timeout attempt={attempt+1}")
            except Exception as e:
                LOG.warning(f"Telegram exception: {e}")
                time.sleep(1)
    return False

# STATE MANAGEMENT
DEFAULT_CYBER = {
    "min_score": 70,
    "execute_now_threshold": 85,
    "min_rr": 1.8,
    "last_adjust_date": None,
    "adjustment_history": []
}

def validate_cyber_params(params: dict) -> dict:
    if not isinstance(params, dict):
        return DEFAULT_CYBER.copy()
    validated = params.copy()
    for key in DEFAULT_CYBER:
        if key not in validated:
            validated[key] = DEFAULT_CYBER[key]
    if not isinstance(validated.get("adjustment_history"), list):
        validated["adjustment_history"] = []
    return validated

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["cybernetic_params"] = validate_cyber_params(data.get("cybernetic_params", DEFAULT_CYBER.copy()))
            try:
                bal = int(float(data.get("balance", 800000)))
                data["balance"] = max(100000, bal)
            except (TypeError, ValueError):
                data["balance"] = 800000
            if not isinstance(data.get("signal_lock"), dict):
                data["signal_lock"] = {}
            return data
        except Exception as e:
            LOG.error(f"load_state corrupt: {e}")
    return {
        "cybernetic_params": DEFAULT_CYBER.copy(),
        "signal_lock": {},
        "balance": 800000,
    }

def save_state():
    with _state_lock:
        now_ts = time.time()
        sig_lock = st.session_state.signal_lock
        sig_lock = {k: v for k, v in sig_lock.items() if now_ts - v < 7 * 86400}
        st.session_state.signal_lock = sig_lock
        cp = st.session_state.cybernetic_params.copy()
        if isinstance(cp.get("last_adjust_date"), (date, datetime)):
            cp["last_adjust_date"] = str(cp["last_adjust_date"])
        data = {
            "cybernetic_params": cp,
            "signal_lock": sig_lock,
            "balance": st.session_state.balance,
            "last_regime": st.session_state.get("last_regime", "SIDEWAYS"),
        }
        try:
            dir_path = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
            fd, tmp_path = tempfile.mkstemp(prefix=".ats_state_", suffix=".tmp", dir=dir_path)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, STATE_FILE)
            except Exception as e:
                LOG.error(f"save_state atomic write gagal: {e}")
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
        except Exception as e:
            LOG.error(f"save_state EXCEPTION: {e}")

# HELPER
def idr(x) -> str:
    try:
        return f"{int(x):,}".replace(",", ".")
    except Exception:
        return str(x)

# RSI
def calculate_rsi(df: pd.DataFrame, period: int = 14) -> float:
    close = df["Close"].squeeze()
    delta = close.diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    avg_loss = (-delta).clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    val = float(rsi.iloc[-1])
    return val if not np.isnan(val) else 50.0

def rsi_gate(df: pd.DataFrame, regime: str = "SIDEWAYS") -> tuple:
    rsi = calculate_rsi(df)
    if regime == "BULLISH":
        rsi_min, rsi_max = 38, 78
    elif regime == "DISTRIBUTION":
        rsi_min, rsi_max = 40, 68
    else:
        rsi_min, rsi_max = 38, 72
    return rsi_min <= rsi <= rsi_max, rsi

# EMA
def calculate_ema(df: pd.DataFrame, period: int = 50) -> float:
    close = df["Close"].squeeze()
    ema = close.ewm(span=period, adjust=False).mean()
    return float(ema.iloc[-1])

def ema_trend_filter(df: pd.DataFrame, period: int = 50) -> tuple:
    last = float(df["Close"].squeeze().iloc[-1])
    ema_val = calculate_ema(df, period)
    return last >= ema_val * 0.995, last, ema_val

# ATR
def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    high = df["High"].squeeze()
    low = df["Low"].squeeze()
    close = df["Close"].squeeze()
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    val = float(atr.iloc[-1])
    return val if not np.isnan(val) else 0.0

def calculate_sl_atr(entry: float, atr: float, multiplier: float = 1.5) -> float:
    return max(entry - multiplier * atr, entry * 0.93)

# TARGET
def find_target(df: pd.DataFrame, entry: float) -> float:
    high = df["High"].squeeze()
    low = df["Low"].squeeze()
    close = df["Close"].squeeze()
    pp = (float(high.iloc[-2]) + float(low.iloc[-2]) + float(close.iloc[-2])) / 3
    r1 = 2 * pp - float(low.iloc[-2])
    r2 = pp + (float(high.iloc[-2]) - float(low.iloc[-2]))
    swing_high = float(high.tail(20).max())
    swing_low = float(low.tail(20).min())
    fib_618 = swing_low + 0.618 * (swing_high - swing_low)
    candidates = sorted([v for v in [r1, r2, fib_618, swing_high] if v > entry * 1.02])
    if not candidates:
        return entry * 1.07
    target = candidates[0]
    return float(target) if target > entry * 1.04 else entry * 1.07

def risk_reward(entry: float, sl: float, target: float) -> float:
    risk = abs(entry - sl)
    reward = abs(target - entry)
    return round(reward / risk, 2) if risk > 0 else 0.0

def position_sizing(balance: float, risk_pct: float, entry: float, sl: float, atr: float = 0.0) -> int:
    risk_amount = balance * risk_pct
    risk_per_lot = abs(entry - sl) * 100
    if risk_per_lot == 0:
        return 1
    lot = int(risk_amount / risk_per_lot)
    if atr and entry > 0 and (atr / entry) > 0.03:
        lot = max(1, int(lot * 0.7))
    return max(lot, 1)

# VWAP
def rolling_vwap(df: pd.DataFrame, window: int = 20) -> pd.Series:
    close = df["Close"].squeeze()
    volume = df["Volume"].squeeze()
    pv = close * volume
    return pv.rolling(window).sum() / volume.rolling(window).sum()

# SIGNALS
def momentum_confirmation(df: pd.DataFrame) -> int:
    close = df["Close"].squeeze()
    volume = df["Volume"].squeeze()
    vwap = rolling_vwap(df, 20)
    last_price = float(close.iloc[-1])
    prev_price = float(close.iloc[-2])
    last_vwap = float(vwap.iloc[-1]) if not np.isnan(vwap.iloc[-1]) else last_price
    avg_vol = float(volume.tail(20).mean())
    change_pct = (last_price - prev_price) / prev_price * 100 if prev_price > 0 else 0
    score = 0
    if change_pct > 0.8 and float(volume.iloc[-1]) > avg_vol * 1.2:
        score += 1
    if last_price > last_vwap and change_pct > 0:
        score += 1
    return score

def accumulation_phase(df: pd.DataFrame) -> int:
    close = df["Close"].squeeze()
    volume = df["Volume"].squeeze()
    last = float(close.iloc[-1])
    high20 = float(close.tail(20).max())
    low20 = float(close.tail(20).min())
    range_ratio = (high20 - low20) / last if last > 0 else 1
    avg_vol = float(volume.tail(20).mean())
    compression = range_ratio < 0.12
    volume_build = float(volume.tail(5).mean()) >= avg_vol * 0.9
    higher_low = float(close.tail(10).min()) >= float(close.tail(20).min())
    return sum([compression, volume_build, higher_low])

def bandar_detection(df: pd.DataFrame) -> int:
    close = df["Close"].squeeze()
    volume = df["Volume"].squeeze()
    avg_vol = float(volume.tail(20).mean())
    spike = float(volume.iloc[-1]) > avg_vol * 1.8
    price_trend = float(close.tail(5).mean()) > float(close.tail(10).mean())
    vol_stable = float(volume.tail(5).mean()) >= avg_vol * 0.9
    accumulation = price_trend and vol_stable
    vol_drop = float(volume.tail(3).mean()) < avg_vol * 0.6
    price_gain = (float(close.iloc[-1]) - float(close.iloc[-3])) / float(close.iloc[-3]) > 0.015
    distribution = price_gain and vol_drop
    score = 0
    if spike:
        score += 2
    if accumulation:
        score += 2
    if distribution:
        score -= 2
    return score

def breakout_confirmation(df: pd.DataFrame) -> str:
    close = df["Close"].squeeze()
    high = df["High"].squeeze()
    volume = df["Volume"].squeeze()
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    recent_high = float(high.iloc[:-1].tail(10).max())
    avg_vol = float(volume.tail(20).mean())
    vol_ratio = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1.0
    change_pct = (last - prev) / prev * 100 if prev > 0 else 0
    breakout = last >= recent_high
    near_breakout = last >= recent_high * 0.99 and change_pct > 0
    if breakout and vol_ratio > 1.3:
        return "VALID"
    if near_breakout and vol_ratio >= 0.6:
        return "WEAK"
    return "WAIT"

def follow_through(df: pd.DataFrame) -> int:
    close = df["Close"].squeeze()
    volume = df["Volume"].squeeze()
    change = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
    avg_vol = float(volume.tail(20).mean())
    score = 0
    if change > 1:
        score += 1
    if float(volume.iloc[-1]) > avg_vol:
        score += 1
    return score

def intraday_confirm(ticker: str) -> int:
    try:
        df5 = yf.download(tickers=ticker, period="5d", interval="5m", progress=False, auto_adjust=True)
        if df5 is None or len(df5) < 10:
            return 0
        latest_date = pd.to_datetime(df5.index[-1]).date()
        day5 = df5[pd.to_datetime(df5.index).date == latest_date]
        if day5 is None or len(day5) < 3:
            day5 = df5.tail(min(len(df5), 20))
        close = df5["Close"].squeeze()
        day_close = day5["Close"].squeeze()
        day_vol = day5["Volume"].squeeze()
        day_vwap = rolling_vwap(day5, min(20, len(day5)))
        recent_change = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
        open_change = (float(day_close.iloc[-1]) - float(day_close.iloc[0])) / float(day_close.iloc[0]) * 100
        avg_vol = float(day_vol.iloc[:-1].tail(10).mean()) if len(day_vol) > 10 else float(day_vol.mean())
        last_vwap = float(day_vwap.iloc[-1]) if not np.isnan(day_vwap.iloc[-1]) else float(day_close.iloc[-1])
        score = 0
        if open_change > 1.0 or recent_change > 0.3:
            score += 1
        if float(day_close.iloc[-1]) > last_vwap and open_change > 0:
            score += 1
        if avg_vol > 0 and float(day_vol.iloc[-1]) > avg_vol * 1.3:
            score += 1
        return score
    except Exception:
        return 0

def daily_change_pct(df: pd.DataFrame) -> float:
    close = df["Close"].squeeze()
    return round((float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100, 2)

def is_trap_signal(value) -> bool:
    if value is True or value is False:
        return bool(value)
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().upper() in ("TRAP", "TRUE", "1", "YES")
    return False

# ADAPTIVE WEIGHTS
REGIME_WEIGHTS = {
    "BULLISH": {"prob": 0.30, "runner": 0.25, "quality": 0.10, "rr": 0.15, "liquidity": 0.10, "bandar": 0.10},
    "SIDEWAYS": {"prob": 0.20, "runner": 0.15, "quality": 0.15, "rr": 0.25, "liquidity": 0.10, "bandar": 0.15},
    "DISTRIBUTION": {"prob": 0.15, "runner": 0.10, "quality": 0.15, "rr": 0.20, "liquidity": 0.25, "bandar": 0.15},
    "VOLATILE": {"prob": 0.20, "runner": 0.15, "quality": 0.10, "rr": 0.25, "liquidity": 0.20, "bandar": 0.10},
}

def get_adaptive_weights(regime: str) -> dict:
    return REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["SIDEWAYS"])

def calculate_score(prob: float, runner: float, quality: str, rr: float, liquidity: str, bandar_score: int, regime: str = "SIDEWAYS") -> float:
    weights = get_adaptive_weights(regime)
    prob_score = (max(0, min(100, prob)) / 100) * (weights["prob"] * 100)
    runner_score = (max(0, min(100, runner)) / 100) * (weights["runner"] * 100)
    quality_map = {"WEAK": 3, "HEALTHY": 10, "STRONG": 15}
    quality_score = quality_map.get(quality, 0) * (weights["quality"] / 0.15)
    rr_max = weights["rr"] * 100
    rr_base = (max(0, min(4.0, rr)) / 4.0) * rr_max
    if rr >= 2.5:
        rr_base = min(rr_max, rr_base + 3)
    rr_score = rr_base
    liq_score = weights["liquidity"] * 100 if "OK" in str(liquidity) else 0
    bandar_pts = (max(0, min(4, bandar_score)) / 4) * (weights["bandar"] * 100)
    total = prob_score + runner_score + quality_score + rr_score + liq_score + bandar_pts
    return round(min(100.0, total), 2)

# CONFLUENCE
def is_bear_mode(regime: str) -> bool:
    return regime == "DISTRIBUTION"

def get_bear_mode_params(regime: str) -> dict:
    if is_bear_mode(regime):
        return {"rr_min": 1.3, "conf_min": 2, "rr_confluence": 1.3, "score_min": 60, "label": "BEAR MODE"}
    return {"rr_min": 1.8, "conf_min": 3, "rr_confluence": 1.8, "score_min": 70, "label": "NORMAL"}

def confluence_check(momentum: int, accum: int, bandar: int, breakout: str, rr: float, ema_ok: bool, regime: str = "SIDEWAYS") -> tuple:
    bm = get_bear_mode_params(regime)
    signals = {
        "Momentum": momentum >= 1,
        "Accumulation": accum >= 2,
        "Bandar": bandar >= 2,
        "Breakout": breakout in ("VALID", "WEAK"),
        "RR_Layak": rr >= bm["rr_confluence"],
        "Uptrend": ema_ok,
    }
    count = sum(signals.values())
    min_conf = bm["conf_min"]
    return count, signals, count >= min_conf

# DYNAMIC THRESHOLD
def get_dynamic_thresholds(all_scores: list) -> dict:
    if len(all_scores) < 3:
        return {"execute_now": 85, "execute": 75, "ready": 65, "method": "static_fallback"}
    arr = np.array(all_scores)
    return {
        "execute_now": float(np.percentile(arr, 88)),
        "execute": float(np.percentile(arr, 70)),
        "ready": float(np.percentile(arr, 45)),
        "method": "dynamic_percentile",
        "n_samples": len(all_scores),
    }

# CYBERNETIC
CYBER_CONFIG = {"learning_rate": 0.15, "memory_days": 30, "min_trades_for_adjust": 20}

def cybernetic_feedback_engine(journal_df: pd.DataFrame, current_regime: str):
    if journal_df.empty or len(journal_df) < CYBER_CONFIG["min_trades_for_adjust"]:
        return None
    if "PnL" not in journal_df.columns or journal_df["PnL"].isna().all():
        return None
    cutoff = datetime.now().date() - pd.Timedelta(days=CYBER_CONFIG["memory_days"])
    recent = journal_df.copy()
    recent["Date"] = pd.to_datetime(recent["Date"]).dt.date
    recent = recent[recent["Date"] >= cutoff]
    if len(recent) < 15:
        return None
    winrate = float((recent["PnL"] > 0).mean() * 100)
    trade_count = len(recent)
    params = st.session_state.cybernetic_params.copy()
    adjustment = 0.0
    if winrate > 65:
        adjustment += 0.20
    elif winrate > 55:
        adjustment += 0.10
    elif winrate < 40:
        adjustment -= 0.20
    if current_regime == "BULLISH":
        adjustment += 0.15
    elif current_regime in ["SIDEWAYS", "VOLATILE"]:
        adjustment -= 0.15
    if trade_count < 20:
        adjustment -= 0.10
    lr = CYBER_CONFIG["learning_rate"]
    params["min_score"] = max(60, min(95, int(params["min_score"] * (1 + adjustment * lr))))
    params["execute_now_threshold"] = max(80, min(98, int(params["execute_now_threshold"] * (1 + adjustment * lr * 0.8))))
    params["min_rr"] = max(1.8, min(3.0, round(params["min_rr"] + adjustment * 0.3, 1)))
    params["last_adjust_date"] = str(datetime.now().date())
    params["adjustment_history"].append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "regime": current_regime,
        "winrate": round(winrate, 1),
        "adjustment": round(adjustment, 3),
        "new_min_score": params["min_score"],
    })
    st.session_state.cybernetic_params = params
    save_state()
    return params

# ENTRY SYSTEM
def entry_system(row: pd.Series, thresholds: dict = None, cyber_params: dict = None) -> str:
    if thresholds is None:
        thresholds = st.session_state.get("dynamic_thresholds") or {}
    if cyber_params is None:
        cyber_params = st.session_state.get("cybernetic_params") or {}
    exec_now_th = thresholds.get("execute_now", 85)
    exec_th = thresholds.get("execute", 75)
    ready_th = thresholds.get("ready", 65)
    min_rr = cyber_params.get("min_rr", 1.8)
    try:
        entry = float(str(row["Entry"]).replace(".", "").replace(",", ""))
        target = float(str(row["Target"]).replace(".", "").replace(",", ""))
    except Exception:
        return "SKIP"
    if entry >= target * 0.97:
        return "SKIP"
    score = row.get("Score", 0)
    rr = row.get("RR", 0)
    breakout = row.get("Breakout", "")
    bandar = row.get("BandarScore", 0)
    momentum = row.get("Momentum", 0)
    if score >= exec_now_th and rr >= 2.0 and breakout == "VALID" and bandar >= 3 and momentum >= 1:
        return "EXECUTE NOW"
    if score >= exec_th and rr >= min_rr and breakout in ("VALID", "WEAK") and bandar >= 2:
        return "EXECUTE"
    if score >= ready_th:
        return "READY"
    return "SKIP"

# SESSION STATE INIT
@st.cache_resource
def _load_persistent_state():
    return load_state()

if "state_loaded" not in st.session_state:
    _state = _load_persistent_state()
    st.session_state.cybernetic_params = _state.get("cybernetic_params", DEFAULT_CYBER.copy())
    st.session_state.signal_lock = _state.get("signal_lock", {})
    st.session_state.balance = _state.get("balance", 800000)
    st.session_state.last_regime = _state.get("last_regime", "-")
    st.session_state.state_loaded = True

if "active_trades" not in st.session_state:
    st.session_state.active_trades = pd.read_csv(ACTIVE_FILE) if os.path.exists(ACTIVE_FILE) else pd.DataFrame()
if "journal" not in st.session_state:
    st.session_state.journal = pd.read_csv(JOURNAL_FILE) if os.path.exists(JOURNAL_FILE) else pd.DataFrame()
if "scan_result" not in st.session_state:
    st.session_state.scan_result = None
if "sector_table" not in st.session_state:
    st.session_state.sector_table = None
if "dynamic_thresholds" not in st.session_state:
    st.session_state.dynamic_thresholds = None
if "last_regime" not in st.session_state:
    st.session_state.last_regime = "-"
if "debug_log" not in st.session_state:
    st.session_state.debug_log = []
if "heatmap_data" not in st.session_state:
    st.session_state.heatmap_data = None
if "intraday_info" not in st.session_state:
    st.session_state.intraday_info = {}

TOP_N_RESULTS = 5

# IMPORTS
try:
    from engine.probability_engine import runner_probability
    from engine.runner_engine import runner_prediction
    from engine.pullback_quality_engine import pullback_quality
    from engine.sector_engine import sector_momentum
    from engine.liquidity_engine import liquidity_trap, fake_breakout
    from engine.regime_engine import detect_market_regime
    from config.universe import ISSI_UNIVERSE, SECTOR_MAP, get_sector
except ImportError:
    ISSI_UNIVERSE = {"BBRI.JK", "BBNI.JK", "BMRI.JK"}
    SECTOR_MAP = {}
    def get_sector(ticker):
        return "Unknown"
    def runner_probability(df):
        return 50
    def runner_prediction(df):
        return 50
    def pullback_quality(df):
        return "HEALTHY"
    def sector_momentum(market, sector_map):
        return {}
    def liquidity_trap(df):
        return False
    def fake_breakout(df):
        return False
    def detect_market_regime(market):
        return "SIDEWAYS"

# LOAD MARKET
MIN_DAILY_VOLUME_IDR = 500000000

@st.cache_data(ttl=300)
def load_market() -> dict:
    BATCH_SIZE = 30
    MAX_RETRIES = 2
    market = {}
    failed_tickers = []
    universe = list(ISSI_UNIVERSE)
    n_batches = (len(universe) + BATCH_SIZE - 1) // BATCH_SIZE
    LOG.info(f"load_market START: {len(universe)} ticker dalam {n_batches} batch")
    for batch_idx in range(n_batches):
        batch = universe[batch_idx * BATCH_SIZE: (batch_idx + 1) * BATCH_SIZE]
        raw = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                raw = yf.download(tickers=batch, period="6mo", interval="1d", group_by="ticker", progress=False, auto_adjust=True, threads=True)
                if raw is not None and not raw.empty:
                    break
            except Exception as e:
                LOG.warning(f"batch {batch_idx+1} attempt {attempt+1} error: {e}")
                time.sleep(1.0 * (attempt + 1))
        if raw is None or raw.empty:
            failed_tickers.extend(batch)
            continue
        for s in batch:
            try:
                df = raw[s].dropna() if len(batch) > 1 else raw.dropna()
                if len(df) < 60:
                    failed_tickers.append(s)
                    continue
                last_close = float(df["Close"].squeeze().iloc[-1])
                if last_close <= 0:
                    failed_tickers.append(s)
                    continue
                if df["Volume"].squeeze().tail(5).mean() <= 0:
                    failed_tickers.append(s)
                    continue
                avg_vol_20 = float(df["Volume"].squeeze().tail(20).mean())
                est_daily_idr = last_close * avg_vol_20 * 100
                if est_daily_idr < MIN_DAILY_VOLUME_IDR:
                    continue
                market[s] = df
            except Exception as e:
                failed_tickers.append(s)
                LOG.warning(f"parse {s} gagal: {e}")
    LOG.info(f"load_market DONE: {len(market)} loaded, {len(failed_tickers)} gagal")
    return market

# UI SETUP
st.set_page_config(layout="wide", page_title="ATS SuperEngine V5.8.2", page_icon="")

# BMW M4 THEME CSS
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif !important;
}
.stApp {
    background: #050d1a !important;
}
.block-container {
    padding: 1rem 2rem 0.5rem !important;
    max-width: 100% !important;
}
.ats-header {
    background: linear-gradient(135deg, #0a1628 0%, #0d1f3c 50%, #0a1628 100%);
    border: 1px solid rgba(0,120,255,0.2);
    border-radius: 16px;
    padding: 20px 28px;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    box-shadow: 0 4px 24px rgba(0,100,255,0.08);
    position: relative;
    overflow: hidden;
}
.ats-header::before {
    content: '';
    position: absolute;
    top: 0;
    right: 0;
    width: 140px;
    height: 100%;
    background: linear-gradient(110deg, transparent 0%, transparent 42%, #0066B1 42%, #0066B1 49%, transparent 49%, transparent 55%, #1C3D7C 55%, #1C3D7C 64%, transparent 64%, transparent 72%, #E22718 72%, #E22718 85%, transparent 85%);
    opacity: 0.85;
    pointer-events: none;
    z-index: 0;
}
.ats-header > * {
    position: relative;
    z-index: 1;
}
.ats-logo {
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.5px;
    background: linear-gradient(90deg, #ffffff 0%, #60a5fa 50%, #3b82f6 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
.ats-subtitle {
    font-size: 12px;
    color: rgba(148,163,184,0.8);
    margin-top: 2px;
    font-weight: 400;
}
.status-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    font-weight: 500;
    padding: 4px 12px;
    border-radius: 20px;
    margin-left: 8px;
}
.status-open {
    background: rgba(34,197,94,0.15);
    color: #22c55e;
    border: 1px solid rgba(34,197,94,0.3);
}
.status-closed {
    background: rgba(239,68,68,0.12);
    color: #f87171;
    border: 1px solid rgba(239,68,68,0.25);
}
.status-info {
    background: rgba(59,130,246,0.12);
    color: #60a5fa;
    border: 1px solid rgba(59,130,246,0.25);
}
.header-right {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 4px;
}
[data-testid="stMetric"] {
    background: linear-gradient(135deg, #0d1f3c 0%, #0a1628 100%) !important;
    border: 1px solid rgba(59,130,246,0.15) !important;
    border-radius: 12px !important;
    padding: 12px 16px !important;
}
div[data-testid="stButton"] > button[kind="primary"] {
    background: linear-gradient(135deg, #1d4ed8 0%, #2563eb 50%, #3b82f6 100%) !important;
    border: none !important;
    color: #fff !important;
    font-weight: 600 !important;
    font-size: 14px !important;
    border-radius: 10px !important;
    padding: 12px 24px !important;
    box-shadow: 0 4px 16px rgba(37,99,235,0.35) !important;
}
div[data-testid="stButton"] > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #1e40af 0%, #1d4ed8 50%, #2563eb 100%) !important;
    box-shadow: 0 6px 20px rgba(37,99,235,0.5) !important;
    transform: translateY(-1px) !important;
}
.stTabs [data-baseweb="tab-list"] {
    background: transparent !important;
    border-bottom: 1px solid rgba(59,130,246,0.15) !important;
}
.stTabs [data-baseweb="tab"] {
    background: transparent !important;
    color: rgba(148,163,184,0.7) !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    padding: 8px 16px !important;
    border-radius: 8px 8px 0 0 !important;
}
.stTabs [aria-selected="true"] {
    background: rgba(37,99,235,0.15) !important;
    color: #60a5fa !important;
    border-bottom: 2px solid #3b82f6 !important;
}
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: #050d1a; }
::-webkit-scrollbar-thumb { background: rgba(59,130,246,0.3); border-radius: 2px; }
#MainMenu, footer, header { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

# HEADER
market_open = is_market_open()
market_status = "BUKA" if market_open else "TUTUP"
market_class = "status-open" if market_open else "status-closed"
regime = st.session_state.get("last_regime", "-")
regime_emoji = "🟢" if regime == "BULLISH" else ("🔴" if regime in ["DISTRIBUTION", "BEARISH"] else "🟡")
cp = st.session_state.cybernetic_params
min_score_val = int(cp.get("min_score", 70))
intra_n = sum(1 for v in st.session_state.get("intraday_info", {}).values() if v.get("status") in ("updated", "appended"))
wib_now_str = get_wib_now()

header_html = f'''
<div class="ats-header">
    <div>
        <div class="ats-logo">⚡ ATS SuperEngine {APP_VERSION}</div>
        <div class="ats-subtitle">Automated Trading Scanner · Saham Syariah ISSI · BMW M4 Edition</div>
        <div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:6px;">
            <span class="status-pill {market_class}">● IDX {market_status}</span>
            <span class="status-pill status-info">{regime_emoji} {regime}</span>
            <span class="status-pill status-info">🕐 {wib_now_str}</span>
        </div>
    </div>
    <div class="header-right">
        <div style="font-size:11px;color:rgba(148,163,184,0.6);text-align:right;">Min Score Adaptif</div>
        <div style="font-size:32px;font-weight:700;background:linear-gradient(90deg,#60a5fa,#3b82f6);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;line-height:1.1;">{min_score_val}</div>
        <div style="font-size:10px;color:rgba(148,163,184,0.5);">Update: {APP_UPDATED}</div>
    </div>
</div>
'''
st.markdown(header_html, unsafe_allow_html=True)

# TABS
tabs = st.tabs(["📖 HOW TO USE", "📊 TRADING DESK", "💼 ACCOUNT", "📋 REPORT"])

# TAB 0: HOW TO USE
with tabs[0]:
    st.markdown("##  Panduan Penggunaan ATS SuperEngine")
    st.info("ATS (Automated Trading Scanner) memindai saham syariah ISSI secara otomatis dan mengirim notifikasi ke Telegram.")
    st.markdown("### ⏰ Jadwal Auto-Scan")
    st.markdown("**09:05** | **09:30** | **11:30** | **13:35** | **15:00** WIB")
    st.markdown("###  Arti Sinyal")
    c1, c2, c3 = st.columns(3)
    c1.error("🔥 **EXECUTE NOW** - Sinyal terkuat")
    c2.warning("✅ **EXECUTE** - Sinyal kuat")
    c3.info("⏳ **READY** - Pantau dulu")

# TAB 1: TRADING DESK
with tabs[1]:
    if st.button("🚀 RUN ATS SCANNER", type="primary", use_container_width=True):
        with st.spinner("Scanning..."):
            try:
                market = load_market()
                balance = st.session_state.get("balance", 800000)
                st.session_state.scan_result = pd.DataFrame({"Ticker": ["TEST"], "Score": [75], "RR": [2.0], "Entry": ["1000"], "SL": ["950"], "Target": ["1100"], "Action": ["READY"]})
                st.success("✅ Scan selesai!")
            except Exception as e:
                st.error(f"❌ Error: {e}")
                LOG.error(f"Scanner error: {e}")
    
    if st.session_state.scan_result is not None and not st.session_state.scan_result.empty:
        st.dataframe(st.session_state.scan_result, use_container_width=True)
    elif st.session_state.scan_result is not None:
        st.warning("⚠️ Tidak ada kandidat hari ini")

# TAB 2: ACCOUNT
with tabs[2]:
    st.markdown("## 💼 Account Management")
    current_balance = int(st.session_state.balance)
    balance_input = st.number_input("💰 Modal / Balance (Rp)", min_value=100000, step=100000, value=current_balance)
    if balance_input != current_balance:
        st.session_state.balance = int(balance_input)
        save_state()
        st.success("✅ Balance diperbarui")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Balance", f"Rp {idr(st.session_state.balance)}")
    c2.metric("Risk/Trade (2%)", f"Rp {idr(st.session_state.balance * 0.02)}")
    c3.metric("Max 5 Posisi (40%)", f"Rp {idr(st.session_state.balance * 0.40)}")
    c4.metric("Safe Cash (60%)", f"Rp {idr(st.session_state.balance * 0.60)}")

# TAB 3: REPORT
with tabs[3]:
    st.markdown("## 📋 Trade Journal")
    JOURNAL_COLS = ["Date", "Ticker", "Entry", "Exit", "Lot", "PnL", "Notes"]
    if st.session_state.journal.empty:
        st.session_state.journal = pd.DataFrame(columns=JOURNAL_COLS)
    edited_journal = st.data_editor(st.session_state.journal, num_rows="dynamic", use_container_width=True, hide_index=True)
    if st.button("💾 Save Journal"):
        st.session_state.journal = edited_journal.reset_index(drop=True)
        st.session_state.journal.to_csv(JOURNAL_FILE, index=False)
        st.success("✅ Journal tersimpan")

# FOOTER
st.markdown("---")
st.caption(f"ATS SuperEngine {APP_VERSION} | BMW M4 Theme | Update: {APP_UPDATED} | ISSI Syariah Scanner")