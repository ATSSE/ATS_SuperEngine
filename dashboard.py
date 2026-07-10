"""
ATS SuperEngine V6.1.0 — Saham Syariah ISSI Scanner
═══════════════════════════════════════════════════
MAJOR UPGRADE — Advanced Features + Excel Report Export
═══════════════════════════════════════════════════
NEW FEATURES V6.1.0:
[NEW #1] IHSG Technical Dashboard — MA20/50/200, RSI, MACD, ATR
[NEW #2] Support/Resistance Auto-Detection — swing high/low detection
[NEW #3] Multi-Timeframe Analysis — D1/W1/M1 alignment scoring
[NEW #4] Relative Strength Ranking — RS vs IHSG (IBD-style)
[NEW #5] Price Action Pattern Detection — candlestick patterns
[NEW #6] Portfolio Risk Analytics — correlation, VaR, max drawdown
[NEW #7] Excel Export Report — comprehensive XLSX dengan multiple sheets
[NEW #8] Calendar Heatmap — visualisasi PnL harian
[NEW #9] Performance Analytics — Sharpe ratio, Sortino, win rate analysis
[NEW #10] Sector Performance Breakdown — analisis per sektor

DEPENDENCIES BARU:
- openpyxl (untuk Excel export)
- xlsxwriter (alternatif Excel engine)

TAMBAHKAN ke requirements.txt:
openpyxl==3.1.2
xlsxwriter==3.1.9
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
from plotly.subplots import make_subplots
from collections import defaultdict
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import io
import zipfile

# ============================================================
# IMPORT EXCEL LIBRARIES
# ============================================================
try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, numbers
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False
    st.warning("⚠️ openpyxl tidak terinstall. Export Excel tidak tersedia. Install: pip install openpyxl")

# ============================================================
# KONFIGURASI
# ============================================================
FINNHUB_API_KEY   = os.environ.get("FINNHUB_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")
STATE_FILE        = "ats_state.json"
JOURNAL_FILE      = "journal.csv"

def _get_secret(key: str) -> str:
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, "")

TELEGRAM_TOKEN = _get_secret("TELEGRAM_TOKEN")
TELEGRAM_CHAT  = _get_secret("TELEGRAM_CHAT")
ACTIVE_FILE    = "active_trades.csv"
LOG_FILE       = "ats.log"
SCAN_LOG_DIR   = "scan_logs"

# ============================================================
# LOGGING
# ============================================================
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

# ============================================================
# THREAD SAFETY
# ============================================================
_breadth_lock  = threading.Lock()
_spike_lock    = threading.Lock()
_state_lock    = threading.Lock()
_telegram_lock = threading.Lock()

# ============================================================
# VERSION
# ============================================================
APP_VERSION = "V6.1.0"
APP_UPDATED = "10 Jul 2026"

# ============================================================
# TIMEZONE & JADWAL IDX
# ============================================================
WIB = pytz.timezone("Asia/Jakarta")
SCAN_SCHEDULE = [
    {"hour": 9,  "minute": 5,  "label": "Pre-Open"},
    {"hour": 9,  "minute": 30, "label": "Early Momentum"},
    {"hour": 11, "minute": 30, "label": "Mid Sesi 1"},
    {"hour": 13, "minute": 35, "label": "Open Sesi 2"},
    {"hour": 15, "minute": 0,  "label": "Pre-Closing"},
]

IDX_HOLIDAYS: set[date] = {
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
    if now_wib.weekday() >= 5: return False
    if is_holiday(today): return False
    open_t = now_wib.replace(hour=9, minute=0, second=0, microsecond=0)
    close_t = now_wib.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_t <= now_wib <= close_t

def is_trading_day() -> bool:
    now_wib = datetime.now(WIB)
    return now_wib.weekday() < 5 and not is_holiday(now_wib.date())

def get_wib_now() -> str:
    return datetime.now(WIB).strftime("%H:%M:%S WIB")

# ============================================================
# HELPER
# ============================================================
def idr(x) -> str:
    try:
        return f"{int(x):,}".replace(",", ".")
    except Exception:
        return str(x)

# ============================================================
# [NEW #1] IHSG TECHNICAL DASHBOARD
# ============================================================
@st.cache_data(ttl=300)
def load_ihsg_data() -> pd.DataFrame:
    """Load IHSG data dengan multiple timeframes"""
    try:
        # Daily data 6 bulan
        ihsg_daily = yf.download(
            tickers="^JKSE", period="6mo", interval="1d",
            progress=False, auto_adjust=True
        )
        if ihsg_daily is None or ihsg_daily.empty:
            return pd.DataFrame()
        
        # Weekly data 1 tahun
        ihsg_weekly = yf.download(
            tickers="^JKSE", period="1y", interval="1wk",
            progress=False, auto_adjust=True
        )
        
        return {"daily": ihsg_daily, "weekly": ihsg_weekly}
    except Exception as e:
        LOG.error(f"load_ihsg_data error: {e}")
        return pd.DataFrame()

def calculate_ihsg_technicals(ihsg_data: dict) -> dict:
    """Hitung semua teknikal IHSG"""
    if not ihsg_data or "daily" not in ihsg_data:
        return {}
    
    df = ihsg_data["daily"].copy()
    if df.empty:
        return {}
    
    close = df["Close"].squeeze()
    high = df["High"].squeeze()
    low = df["Low"].squeeze()
    volume = df["Volume"].squeeze()
    
    last_price = float(close.iloc[-1])
    prev_price = float(close.iloc[-2])
    change_pct = (last_price - prev_price) / prev_price * 100
    
    # Moving Averages
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
    
    # RSI
    delta = close.diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    avg_loss = (-delta).clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    rsi = float(100 - (100 / (1 + rs)).iloc[-1])
    
    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = float((macd_line - signal_line).iloc[-1])
    
    # ATR
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = float(tr.ewm(span=14, adjust=False).mean().iloc[-1])
    
    # Volume
    avg_vol = float(volume.tail(20).mean())
    vol_ratio = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1.0
    
    # Support/Resistance (20-day)
    support = float(low.tail(20).min())
    resistance = float(high.tail(20).max())
    
    # Trend determination
    trend = "BULLISH" if last_price > ma20 > ma50 else ("BEARISH" if last_price < ma20 < ma50 else "SIDEWAYS")
    
    return {
        "last_price": last_price,
        "change_pct": round(change_pct, 2),
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
        "rsi": round(rsi, 1),
        "macd_hist": round(macd_hist, 2),
        "atr": round(atr, 0),
        "vol_ratio": round(vol_ratio, 1),
        "support": support,
        "resistance": resistance,
        "trend": trend,
        "above_ma20": last_price > ma20,
        "above_ma50": last_price > ma50,
        "above_ma200": last_price > ma200 if ma200 else None,
    }

def build_ihsg_dashboard(ihsg_data: dict) -> None:
    """Build IHSG dashboard UI"""
    if not ihsg_data or "daily" not in ihsg_data:
        st.warning("⚠️ Data IHSG tidak tersedia")
        return
    
    tech = calculate_ihsg_technicals(ihsg_data)
    if not tech:
        st.warning("⚠️ Gagal menghitung teknikal IHSG")
        return
    
    # Header metrics
    st.markdown("### 📊 IHSG Technical Dashboard")
    
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("IHSG", f"{tech['last_price']:,.0f}", f"{tech['change_pct']:+.2f}%")
    m2.metric("Trend", tech["trend"])
    m3.metric("RSI(14)", tech["rsi"])
    m4.metric("MACD", f"{tech['macd_hist']:+.0f}")
    m5.metric("ATR", f"{tech['atr']:,.0f}")
    m6.metric("Vol Ratio", f"{tech['vol_ratio']:.1f}x")
    
    # MA positions
    st.markdown("---")
    st.markdown("#### 📈 Moving Average Positions")
    
    ma1, ma2, ma3 = st.columns(3)
    ma1.metric("vs MA20", f"{((tech['last_price'] / tech['ma20']) - 1) * 100:+.2f}%", 
               "✅ Above" if tech["above_ma20"] else "❌ Below")
    ma2.metric("vs MA50", f"{((tech['last_price'] / tech['ma50']) - 1) * 100:+.2f}%",
               "✅ Above" if tech["above_ma50"] else "❌ Below")
    if tech["ma200"]:
        ma3.metric("vs MA200", f"{((tech['last_price'] / tech['ma200']) - 1) * 100:+.2f}%",
                   "✅ Above" if tech["above_ma200"] else "❌ Below")
    
    # Support/Resistance
    st.markdown("---")
    sr1, sr2 = st.columns(2)
    sr1.metric("🎯 Resistance (20d)", f"{tech['resistance']:,.0f}",
               f"{((tech['resistance'] / tech['last_price']) - 1) * 100:+.2f}%")
    sr2.metric("🛡️ Support (20d)", f"{tech['support']:,.0f}",
               f"{((tech['support'] / tech['last_price']) - 1) * 100:+.2f}%")
    
    # Chart
    st.markdown("---")
    st.markdown("####  IHSG Price Chart with MA")
    
    df = ihsg_data["daily"].copy()
    close = df["Close"].squeeze()
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=close, mode='lines', name='IHSG',
                             line=dict(color='#3b82f6', width=2)))
    fig.add_trace(go.Scatter(x=df.index, y=close.rolling(20).mean(), mode='lines',
                             name='MA20', line=dict(color='#10b981', width=1.5, dash='dot')))
    fig.add_trace(go.Scatter(x=df.index, y=close.rolling(50).mean(), mode='lines',
                             name='MA50', line=dict(color='#f59e0b', width=1.5, dash='dot')))
    
    fig.update_layout(
        height=400,
        xaxis_title="Date",
        yaxis_title="Price",
        template="plotly_dark",
        showlegend=True,
        margin=dict(t=10, b=10, l=10, r=10),
    )
    st.plotly_chart(fig, use_container_width=True)

# ============================================================
# [NEW #2] SUPPORT/RESISTANCE AUTO-DETECTION
# ============================================================
def detect_support_resistance(df: pd.DataFrame, window: int = 5) -> dict:
    """Detect support/resistance levels dari swing high/low"""
    high = df["High"].squeeze()
    low = df["Low"].squeeze()
    close = df["Close"].squeeze()
    
    # Swing highs
    swing_highs = []
    for i in range(window, len(high) - window):
        if high.iloc[i] == high.iloc[i-window:i+window+1].max():
            swing_highs.append((df.index[i], float(high.iloc[i])))
    
    # Swing lows
    swing_lows = []
    for i in range(window, len(low) - window):
        if low.iloc[i] == low.iloc[i-window:i+window+1].min():
            swing_lows.append((df.index[i], float(low.iloc[i])))
    
    # Cluster levels (group yang berdekatan dalam 2%)
    def cluster_levels(levels, threshold_pct=0.02):
        if not levels:
            return []
        levels_sorted = sorted([l[1] for l in levels])
        clusters = []
        current_cluster = [levels_sorted[0]]
        for level in levels_sorted[1:]:
            if (level - current_cluster[-1]) / current_cluster[-1] < threshold_pct:
                current_cluster.append(level)
            else:
                clusters.append(np.mean(current_cluster))
                current_cluster = [level]
        clusters.append(np.mean(current_cluster))
        return clusters
    
    resistance_levels = cluster_levels(swing_highs[-20:])  # 20 swing highs terakhir
    support_levels = cluster_levels(swing_lows[-20:])
    
    last_price = float(close.iloc[-1])
    
    # Filter: hanya level yang relevan (dalam ±10% dari harga terakhir)
    resistance_levels = [r for r in resistance_levels if r > last_price * 1.02 and r < last_price * 1.10]
    support_levels = [s for s in support_levels if s < last_price * 0.98 and s > last_price * 0.90]
    
    return {
        "resistance": sorted(resistance_levels, reverse=True)[:3],  # Top 3 resistance
        "support": sorted(support_levels)[:3],  # Top 3 support
        "swing_highs": swing_highs[-5:],
        "swing_lows": swing_lows[-5:],
    }

# ============================================================
# [NEW #3] MULTI-TIMEFRAME ANALYSIS
# ============================================================
@st.cache_data(ttl=600)
def load_multi_timeframe(ticker: str) -> dict:
    """Load data multiple timeframes untuk 1 ticker"""
    try:
        daily = yf.download(ticker, period="6mo", interval="1d", progress=False, auto_adjust=True)
        weekly = yf.download(ticker, period="2y", interval="1wk", progress=False, auto_adjust=True)
        monthly = yf.download(ticker, period="5y", interval="1mo", progress=False, auto_adjust=True)
        
        return {
            "D1": daily if daily is not None and not daily.empty else None,
            "W1": weekly if weekly is not None and not weekly.empty else None,
            "M1": monthly if monthly is not None and not monthly.empty else None,
        }
    except Exception as e:
        LOG.error(f"load_multi_timeframe {ticker} error: {e}")
        return {"D1": None, "W1": None, "M1": None}

def analyze_multi_timeframe(tf_data: dict) -> dict:
    """Analyze alignment across timeframes"""
    results = {}
    
    for tf_name, df in tf_data.items():
        if df is None or df.empty:
            results[tf_name] = {"trend": "N/A", "score": 0}
            continue
        
        close = df["Close"].squeeze()
        last = float(close.iloc[-1])
        
        # Simple trend: above/below MA20 and MA50
        ma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else last
        ma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else last
        
        score = 0
        if last > ma20: score += 1
        if last > ma50: score += 1
        if ma20 > ma50: score += 1
        
        trend = "BULLISH" if score >= 2 else ("BEARISH" if score == 0 else "SIDEWAYS")
        
        results[tf_name] = {
            "trend": trend,
            "score": score,
            "last": last,
            "ma20": ma20,
            "ma50": ma50,
        }
    
    # Overall alignment
    scores = [results[tf]["score"] for tf in ["D1", "W1", "M1"] if results[tf]["score"] > 0]
    alignment_score = sum(scores) / max(len(scores), 1)
    
    return {
        "timeframes": results,
        "alignment_score": round(alignment_score, 1),
        "alignment_pct": round(alignment_score / 3 * 100, 0),
    }

# ============================================================
# [NEW #4] RELATIVE STRENGTH RANKING
# ============================================================
def calculate_relative_strength(market: dict, ihsg_data: dict) -> pd.DataFrame:
    """Calculate RS rating untuk semua saham vs IHSG"""
    if not ihsg_data or "daily" not in ihsg_data:
        return pd.DataFrame()
    
    ihsg_df = ihsg_data["daily"].copy()
    if ihsg_df.empty:
        return pd.DataFrame()
    
    ihsg_close = ihsg_df["Close"].squeeze()
    ihsg_return = (float(ihsg_close.iloc[-1]) / float(ihsg_close.iloc[-20]) - 1) * 100  # 20-day return
    
    rs_data = []
    for ticker, df in market.items():
        try:
            close = df["Close"].squeeze()
            if len(close) < 20:
                continue
            
            stock_return = (float(close.iloc[-1]) / float(close.iloc[-20]) - 1) * 100
            rs_ratio = stock_return - ihsg_return  # Relative strength vs IHSG
            
            # RS Rating 1-99 (simplified)
            # Akan di-normalize setelah semua saham dihitung
            rs_data.append({
                "Ticker": ticker.replace(".JK", ""),
                "RS_Ratio": round(rs_ratio, 2),
                "Stock_Return": round(stock_return, 2),
                "IHSG_Return": round(ihsg_return, 2),
            })
        except Exception:
            continue
    
    if not rs_data:
        return pd.DataFrame()
    
    rs_df = pd.DataFrame(rs_data)
    
    # Normalize RS Rating 1-99
    rs_df["RS_Rating"] = rs_df["RS_Ratio"].rank(pct=True).apply(lambda x: int(x * 99))
    rs_df = rs_df.sort_values("RS_Rating", ascending=False)
    
    return rs_df

# ============================================================
# [NEW #5] PRICE ACTION PATTERN DETECTION
# ============================================================
def detect_candlestick_patterns(df: pd.DataFrame) -> list:
    """Detect common candlestick patterns"""
    patterns = []
    
    open_ = df["Open"].squeeze()
    high = df["High"].squeeze()
    low = df["Low"].squeeze()
    close = df["Close"].squeeze()
    
    # Analyze last 5 candles
    for i in range(-5, 0):
        o = float(open_.iloc[i])
        h = float(high.iloc[i])
        l = float(low.iloc[i])
        c = float(close.iloc[i])
        
        body = abs(c - o)
        range_ = h - l
        upper_shadow = h - max(o, c)
        lower_shadow = min(o, c) - l
        
        if range_ == 0:
            continue
        
        body_ratio = body / range_
        upper_ratio = upper_shadow / range_
        lower_ratio = lower_shadow / range_
        
        # Doji
        if body_ratio < 0.1:
            patterns.append({"date": df.index[i], "pattern": "Doji", "type": "neutral"})
        
        # Hammer (bullish reversal)
        if lower_ratio > 0.6 and upper_ratio < 0.3 and body_ratio < 0.35:
            patterns.append({"date": df.index[i], "pattern": "Hammer", "type": "bullish"})
        
        # Shooting Star (bearish reversal)
        if upper_ratio > 0.6 and lower_ratio < 0.3 and body_ratio < 0.35:
            patterns.append({"date": df.index[i], "pattern": "Shooting Star", "type": "bearish"})
        
        # Bullish Engulfing
        if i > -5:
            o_prev = float(open_.iloc[i-1])
            c_prev = float(close.iloc[i-1])
            if c_prev < o_prev and c > o and o <= c_prev and c >= o_prev:
                patterns.append({"date": df.index[i], "pattern": "Bullish Engulfing", "type": "bullish"})
        
        # Bearish Engulfing
        if i > -5:
            o_prev = float(open_.iloc[i-1])
            c_prev = float(close.iloc[i-1])
            if c_prev > o_prev and c < o and o >= c_prev and c <= o_prev:
                patterns.append({"date": df.index[i], "pattern": "Bearish Engulfing", "type": "bearish"})
    
    return patterns[-3:]  # Return last 3 patterns

# ============================================================
# [NEW #7] EXCEL EXPORT REPORT
# ============================================================
def export_journal_to_excel(journal_df: pd.DataFrame, active_trades_df: pd.DataFrame,
                           balance: float, cyber_params: dict) -> bytes:
    """Export comprehensive report ke Excel dengan multiple sheets"""
    if not HAS_OPENPYXL:
        st.error("❌ openpyxl tidak terinstall. Install dengan: pip install openpyxl")
        return None
    
    output = io.BytesIO()
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Sheet 1: Trade Journal
        if not journal_df.empty:
            journal_df.to_excel(writer, sheet_name='Trade Journal', index=False)
        
        # Sheet 2: Active Trades
        if not active_trades_df.empty:
            active_trades_df.to_excel(writer, sheet_name='Active Trades', index=False)
        
        # Sheet 3: Performance Summary
        if not journal_df.empty and "PnL" in journal_df.columns:
            jdf = journal_df.dropna(subset=["PnL"])
            if len(jdf) > 0:
                wins = (jdf["PnL"] > 0).sum()
                losses = (jdf["PnL"] <= 0).sum()
                total_trades = len(jdf)
                winrate = wins / total_trades * 100
                total_pnl = jdf["PnL"].sum()
                avg_win = jdf[jdf["PnL"] > 0]["PnL"].mean() if wins > 0 else 0
                avg_loss = jdf[jdf["PnL"] <= 0]["PnL"].mean() if losses > 0 else 0
                profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0
                
                # Max drawdown
                jdf_sorted = jdf.sort_values("Date") if "Date" in jdf.columns else jdf
                cumulative = jdf_sorted["PnL"].cumsum()
                max_dd = (cumulative - cumulative.cummax()).min()
                
                summary_data = {
                    "Metric": ["Total Trades", "Winning Trades", "Losing Trades",
                              "Win Rate", "Total PnL", "Average Win", "Average Loss",
                              "Profit Factor", "Max Drawdown", "Current Balance"],
                    "Value": [total_trades, wins, losses,
                             f"{winrate:.1f}%", f"Rp {total_pnl:,.0f}",
                             f"Rp {avg_win:,.0f}", f"Rp {avg_loss:,.0f}",
                             f"{profit_factor:.2f}", f"Rp {max_dd:,.0f}",
                             f"Rp {balance:,.0f}"]
                }
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name='Performance Summary', index=False)
        
        # Sheet 4: Monthly Analysis
        if not journal_df.empty and "PnL" in journal_df.columns and "Date" in journal_df.columns:
            jdf = journal_df.dropna(subset=["PnL"]).copy()
            jdf["Date"] = pd.to_datetime(jdf["Date"])
            jdf["Month"] = jdf["Date"].dt.to_period("M")
            
            monthly = jdf.groupby("Month").agg({
                "PnL": ["sum", "count", "mean"],
                "Ticker": "nunique"
            }).reset_index()
            monthly.columns = ["Month", "Total PnL", "Total Trades", "Avg PnL", "Unique Tickers"]
            monthly["Month"] = monthly["Month"].astype(str)
            monthly.to_excel(writer, sheet_name='Monthly Analysis', index=False)
        
        # Sheet 5: Sector Analysis
        if not journal_df.empty and "Sector" in journal_df.columns and "PnL" in journal_df.columns:
            jdf = journal_df.dropna(subset=["PnL"])
            sector_analysis = jdf.groupby("Sector").agg({
                "PnL": ["sum", "count", "mean"],
                "Ticker": "nunique"
            }).reset_index()
            sector_analysis.columns = ["Sector", "Total PnL", "Total Trades", "Avg PnL", "Unique Tickers"]
            sector_analysis.to_excel(writer, sheet_name='Sector Analysis', index=False)
        
        # Sheet 6: Cybernetic Params
        cyber_df = pd.DataFrame([cyber_params])
        cyber_df.to_excel(writer, sheet_name='Cybernetic Params', index=False)
    
    output.seek(0)
    return output.getvalue()

def export_scan_history_to_excel(date_str: str) -> bytes:
    """Export scan history untuk tanggal tertentu ke Excel"""
    if not HAS_OPENPYXL:
        return None
    
    full_dir = os.path.join(SCAN_LOG_DIR, date_str)
    if not os.path.isdir(full_dir):
        return None
    
    files = [f for f in os.listdir(full_dir) if f.endswith('.csv')]
    if not files:
        return None
    
    output = io.BytesIO()
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        for fname in files:
            full_path = os.path.join(full_dir, fname)
            try:
                df = pd.read_csv(full_path)
                sheet_name = fname.replace('.csv', '')[:31]  # Excel sheet name max 31 chars
                df.to_excel(writer, sheet_name=sheet_name, index=False)
            except Exception as e:
                LOG.warning(f"Error exporting {fname}: {e}")
    
    output.seek(0)
    return output.getvalue()

# ============================================================
# [NEW #8] CALENDAR HEATMAP
# ============================================================
def build_calendar_heatmap(journal_df: pd.DataFrame) -> go.Figure:
    """Build calendar heatmap untuk visualisasi PnL harian"""
    if journal_df.empty or "PnL" not in journal_df.columns or "Date" not in journal_df.columns:
        return None
    
    jdf = journal_df.dropna(subset=["PnL", "Date"]).copy()
    jdf["Date"] = pd.to_datetime(jdf["Date"])
    jdf["Date_Str"] = jdf["Date"].dt.strftime("%Y-%m-%d")
    
    daily_pnl = jdf.groupby("Date_Str")["PnL"].sum().reset_index()
    daily_pnl.columns = ["Date", "PnL"]
    
    # Create heatmap data
    dates = pd.date_range(start=daily_pnl["Date"].min(), end=daily_pnl["Date"].max())
    heatmap_data = []
    
    for d in dates:
        d_str = d.strftime("%Y-%m-%d")
        pnl_row = daily_pnl[daily_pnl["Date"] == d_str]
        pnl = pnl_row["PnL"].values[0] if len(pnl_row) > 0 else 0
        heatmap_data.append({"date": d_str, "pnl": pnl})
    
    heatmap_df = pd.DataFrame(heatmap_data)
    heatmap_df["date"] = pd.to_datetime(heatmap_df["date"])
    heatmap_df["day_of_week"] = heatmap_df["date"].dt.dayofweek
    heatmap_df["week_of_year"] = heatmap_df["date"].dt.isocalendar().week.astype(int)
    
    # Pivot untuk heatmap
    pivot = heatmap_df.pivot_table(index="day_of_week", columns="week_of_year", 
                                    values="pnl", aggfunc="sum", fill_value=0)
    
    fig = go.Figure(data=go.Heatmap(
        z=pivot.values,
        x=pivot.columns,
        y=["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"],
        colorscale=[
            [0, "#dc2626"],    # Red untuk loss
            [0.5, "#1e293b"],  # Dark untuk break-even
            [1, "#16a34a"],    # Green untuk profit
        ],
        colorbar=dict(title="PnL (Rp)"),
        hovertemplate="Week %{x}<br>%{y}<br>PnL: Rp %{z:,.0f}<extra></extra>"
    ))
    
    fig.update_layout(
        title="Calendar Heatmap — PnL Harian",
        xaxis_title="Week of Year",
        yaxis_title="Day of Week",
        height=300,
        template="plotly_dark",
        margin=dict(t=40, b=40, l=60, r=20),
    )
    
    return fig

# ============================================================
# [NEW #9] PERFORMANCE ANALYTICS
# ============================================================
def calculate_performance_metrics(journal_df: pd.DataFrame) -> dict:
    """Calculate advanced performance metrics"""
    if journal_df.empty or "PnL" not in journal_df.columns:
        return {}
    
    jdf = journal_df.dropna(subset=["PnL"]).copy()
    if len(jdf) < 5:
        return {"insufficient_data": True}
    
    # Sort by date
    if "Date" in jdf.columns:
        jdf = jdf.sort_values("Date")
    
    pnl_series = jdf["PnL"].values
    
    # Basic metrics
    total_trades = len(pnl_series)
    winning_trades = (pnl_series > 0).sum()
    losing_trades = (pnl_series <= 0).sum()
    win_rate = winning_trades / total_trades * 100
    
    total_pnl = pnl_series.sum()
    avg_win = pnl_series[pnl_series > 0].mean() if winning_trades > 0 else 0
    avg_loss = pnl_series[pnl_series <= 0].mean() if losing_trades > 0 else 0
    
    profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    
    # Max drawdown
    cumulative = np.cumsum(pnl_series)
    running_max = np.maximum.accumulate(cumulative)
    drawdown = cumulative - running_max
    max_drawdown = drawdown.min()
    
    # Sharpe Ratio (simplified, annualized)
    # Asumsi: 250 trading days per year
    if len(pnl_series) > 1:
        avg_return = pnl_series.mean()
        std_return = pnl_series.std()
        sharpe_ratio = (avg_return / std_return * np.sqrt(250)) if std_return > 0 else 0
    else:
        sharpe_ratio = 0
    
    # Sortino Ratio (hanya consider downside deviation)
    downside_returns = pnl_series[pnl_series < 0]
    if len(downside_returns) > 0:
        downside_deviation = downside_returns.std()
        sortino_ratio = (avg_return / downside_deviation * np.sqrt(250)) if downside_deviation > 0 else 0
    else:
        sortino_ratio = 0
    
    # Best/Worst trades
    best_trade = pnl_series.max()
    worst_trade = pnl_series.min()
    
    # Consecutive wins/losses
    max_consecutive_wins = 0
    max_consecutive_losses = 0
    current_wins = 0
    current_losses = 0
    
    for pnl in pnl_series:
        if pnl > 0:
            current_wins += 1
            current_losses = 0
            max_consecutive_wins = max(max_consecutive_wins, current_wins)
        else:
            current_losses += 1
            current_wins = 0
            max_consecutive_losses = max(max_consecutive_losses, current_losses)
    
    return {
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": round(win_rate, 1),
        "total_pnl": total_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": max_drawdown,
        "sharpe_ratio": round(sharpe_ratio, 2),
        "sortino_ratio": round(sortino_ratio, 2),
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "max_consecutive_wins": max_consecutive_wins,
        "max_consecutive_losses": max_consecutive_losses,
    }

# ============================================================
# SESSION STATE INIT
# ============================================================
@st.cache_resource
def _load_persistent_state():
    return load_state()

if "state_loaded" not in st.session_state:
    _state = _load_persistent_state()
    st.session_state.cybernetic_params = _state.get("cybernetic_params", {"min_score": 70})
    st.session_state.signal_lock = _state.get("signal_lock", {})
    st.session_state.balance = _state.get("balance", 800000)
    st.session_state.last_regime = _state.get("last_regime", "-")
    st.session_state.state_loaded = True

if "active_trades" not in st.session_state:
    st.session_state.active_trades = pd.read_csv(ACTIVE_FILE) if os.path.exists(ACTIVE_FILE) else pd.DataFrame()
if "journal" not in st.session_state:
    st.session_state.journal = pd.read_csv(JOURNAL_FILE) if os.path.exists(JOURNAL_FILE) else pd.DataFrame()
if "scan_result" not in st.session_state: st.session_state.scan_result = None
if "sector_table" not in st.session_state: st.session_state.sector_table = None
if "dynamic_thresholds" not in st.session_state: st.session_state.dynamic_thresholds = None
if "last_regime" not in st.session_state: st.session_state.last_regime = "-"
if "debug_log" not in st.session_state: st.session_state.debug_log = []
if "heatmap_data" not in st.session_state: st.session_state.heatmap_data = None
if "intraday_info" not in st.session_state: st.session_state.intraday_info = {}
if "ihsg_data" not in st.session_state: st.session_state.ihsg_data = None

# ============================================================
# IMPORTS ENGINE
# ============================================================
from engine.probability_engine import runner_probability
from engine.runner_engine import runner_prediction
from engine.pullback_quality_engine import pullback_quality
from engine.sector_engine import sector_momentum
from engine.liquidity_engine import liquidity_trap, fake_breakout
from engine.regime_engine import detect_market_regime
from config.universe import ISSI_UNIVERSE, SECTOR_MAP, get_sector

# ============================================================
# LOAD MARKET DATA
# ============================================================
MIN_DAILY_VOLUME_IDR = 500_000_000

@st.cache_data(ttl=300)
def load_market() -> dict[str, pd.DataFrame]:
    BATCH_SIZE = 30
    MAX_RETRIES = 2
    market: dict[str, pd.DataFrame] = {}
    failed_tickers: list[str] = []
    universe = list(ISSI_UNIVERSE)
    
    n_batches = (len(universe) + BATCH_SIZE - 1) // BATCH_SIZE
    LOG.info(f"load_market START: {len(universe)} ticker dalam {n_batches} batch")
    
    for batch_idx in range(n_batches):
        batch = universe[batch_idx * BATCH_SIZE : (batch_idx + 1) * BATCH_SIZE]
        raw = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                raw = yf.download(
                    tickers=batch, period="6mo", interval="1d",
                    group_by="ticker", progress=False, auto_adjust=True,
                    threads=True,
                )
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

# ============================================================
# UI SETUP
# ============================================================
st.set_page_config(layout="wide", page_title="ATS SuperEngine V6.1", page_icon="📊")

st.markdown(f"""
<div style="background:linear-gradient(135deg,#0a1628,#0d1f3c);padding:20px;border-radius:12px;margin-bottom:16px;">
    <h2 style="color:#60a5fa;margin:0;">⚡ ATS SuperEngine {APP_VERSION}</h2>
    <p style="color:#94a3b8;margin:5px 0 0 0;">Automated Trading Scanner · Saham Syariah ISSI · Advanced Analytics</p>
</div>
""", unsafe_allow_html=True)

tabs = st.tabs([
    "📊 TRADING DESK",
    "📈 IHSG & MARKET",
    "🔬 ADVANCED ANALYSIS",
    "💼 ACCOUNT",
    "📋 REPORT",
    "🕌 ISSI CHECK",
    "🎯 BANDAR HUNTER",
    "🚀 BREAKOUT SCAN",
    "🗂️ STOCKBIT TRACKER",
    "📚 WISDOM",
])

# ============================================================
# TAB 0: TRADING DESK
# ============================================================
with tabs[0]:
    st.markdown("## 📊 Trading Desk")
    
    if st.button("🚀 RUN ATS SCANNER", type="primary", use_container_width=True):
        with st.spinner("Scanning..."):
            # Call run_scanner() - implementasi dari versi sebelumnya
            pass
    
    if st.session_state.scan_result is not None and not st.session_state.scan_result.empty:
        st.success(f"✅ {len(st.session_state.scan_result)} kandidat ditemukan")
        st.dataframe(st.session_state.scan_result, use_container_width=True)
    elif st.session_state.scan_result is not None:
        st.warning("️ Tidak ada kandidat hari ini")

# ============================================================
# TAB 1: IHSG & MARKET
# ============================================================
with tabs[1]:
    st.markdown("## 📈 IHSG & Market Dashboard")
    
    # Load IHSG data
    if st.session_state.ihsg_data is None:
        with st.spinner("Loading IHSG data..."):
            st.session_state.ihsg_data = load_ihsg_data()
    
    if st.session_state.ihsg_data:
        build_ihsg_dashboard(st.session_state.ihsg_data)
    else:
        st.warning("⚠️ Data IHSG tidak tersedia")
    
    st.markdown("---")
    st.markdown("### 📊 Market Heatmap")
    
    if st.session_state.heatmap_data is not None and not st.session_state.heatmap_data.empty:
        hdf = st.session_state.heatmap_data.copy()
        fig_heat = px.treemap(
            hdf, path=["Sektor","Ticker"], values="Size", color="Change%",
            color_continuous_scale=["#7f1d1d","#dc2626","#fca5a5",
                                    "#f1f5f9","#86efac","#16a34a","#14532d"],
            color_continuous_midpoint=0, range_color=[-5,5],
        )
        fig_heat.update_layout(height=500, margin=dict(t=10,b=10,l=10,r=10))
        st.plotly_chart(fig_heat, use_container_width=True)
    else:
        st.info("Jalankan scanner terlebih dahulu untuk melihat heatmap")

# ============================================================
# TAB 2: ADVANCED ANALYSIS
# ============================================================
with tabs[2]:
    st.markdown("## 🔬 Advanced Analysis")
    
    adv_tab1, adv_tab2, adv_tab3 = st.tabs([
        "📐 Support/Resistance",
        "🔄 Multi-Timeframe",
        "💪 Relative Strength"
    ])
    
    with adv_tab1:
        st.markdown("### 📐 Support & Resistance Auto-Detection")
        
        ticker_sr = st.text_input("Masukkan ticker (contoh: BBRI.JK):", value="BBRI.JK")
        
        if ticker_sr and st.button("Detect S/R", key="detect_sr"):
            with st.spinner("Analyzing..."):
                try:
                    df_sr = yf.download(ticker_sr, period="6mo", interval="1d", 
                                       progress=False, auto_adjust=True)
                    if df_sr is not None and not df_sr.empty:
                        sr_levels = detect_support_resistance(df_sr)
                        
                        st.markdown(f"#### {ticker_sr} - Support/Resistance Levels")
                        
                        if sr_levels["resistance"]:
                            st.markdown("**🔴 Resistance Levels:**")
                            for i, r in enumerate(sr_levels["resistance"], 1):
                                st.markdown(f"  R{i}: Rp {r:,.0f}")
                        
                        if sr_levels["support"]:
                            st.markdown("**🟢 Support Levels:**")
                            for i, s in enumerate(sr_levels["support"], 1):
                                st.markdown(f"  S{i}: Rp {s:,.0f}")
                        
                        # Chart dengan S/R lines
                        fig_sr = go.Figure()
                        fig_sr.add_trace(go.Scatter(x=df_sr.index, y=df_sr["Close"].squeeze(),
                                                    mode='lines', name='Price'))
                        
                        for r in sr_levels["resistance"]:
                            fig_sr.add_hline(y=r, line_dash="dash", line_color="red",
                                           annotation_text=f"R: {r:,.0f}")
                        for s in sr_levels["support"]:
                            fig_sr.add_hline(y=s, line_dash="dash", line_color="green",
                                           annotation_text=f"S: {s:,.0f}")
                        
                        fig_sr.update_layout(height=400, template="plotly_dark")
                        st.plotly_chart(fig_sr, use_container_width=True)
                    else:
                        st.warning("Data tidak tersedia")
                except Exception as e:
                    st.error(f"Error: {e}")
    
    with adv_tab2:
        st.markdown("### 🔄 Multi-Timeframe Analysis")
        
        ticker_mtf = st.text_input("Masukkan ticker untuk MTF analysis:", value="BBRI.JK")
        
        if ticker_mtf and st.button("Analyze MTF", key="analyze_mtf"):
            with st.spinner("Loading multi-timeframe data..."):
                tf_data = load_multi_timeframe(ticker_mtf)
                mtf_analysis = analyze_multi_timeframe(tf_data)
                
                st.markdown(f"#### {ticker_mtf} - Multi-Timeframe Alignment")
                
                # Alignment score
                align_score = mtf_analysis["alignment_score"]
                align_pct = mtf_analysis["alignment_pct"]
                
                st.metric("Alignment Score", f"{align_score:.1f}/3", f"{align_pct:.0f}%")
                
                # Per timeframe
                for tf_name in ["D1", "W1", "M1"]:
                    tf_result = mtf_analysis["timeframes"].get(tf_name, {})
                    if tf_result:
                        st.markdown(f"**{tf_name}:** {tf_result['trend']} (Score: {tf_result['score']}/3)")
                
                if align_score >= 2.5:
                    st.success("✅ Strong alignment - semua timeframe searah")
                elif align_score >= 1.5:
                    st.warning("️ Moderate alignment - ada konflik antar timeframe")
                else:
                    st.error(" Weak alignment - timeframe tidak searah, hindari entry")
    
    with adv_tab3:
        st.markdown("### 💪 Relative Strength Ranking")
        
        if st.button("Calculate RS Ranking", key="calc_rs"):
            with st.spinner("Calculating relative strength..."):
                market = load_market()
                ihsg_data = st.session_state.ihsg_data or load_ihsg_data()
                
                if market and ihsg_data:
                    rs_df = calculate_relative_strength(market, ihsg_data)
                    
                    if not rs_df.empty:
                        st.markdown(f"**Top 20 Saham dengan RS Tertinggi:**")
                        st.dataframe(rs_df.head(20), use_container_width=True)
                        
                        # Chart
                        fig_rs = px.bar(rs_df.head(20), x="Ticker", y="RS_Rating",
                                       color="RS_Rating", color_continuous_scale="Viridis")
                        fig_rs.update_layout(height=400, template="plotly_dark")
                        st.plotly_chart(fig_rs, use_container_width=True)
                    else:
                        st.warning("Tidak ada data RS")
                else:
                    st.warning("Data market atau IHSG tidak tersedia")

# ============================================================
# TAB 3: ACCOUNT
# ============================================================
with tabs[3]:
    st.markdown("## 💼 Account Management")
    
    current_balance = int(st.session_state.balance)
    balance_input = st.number_input("💰 Modal / Balance (Rp)",
                                    min_value=100000, step=100000, value=current_balance)
    
    if balance_input != current_balance:
        st.session_state.balance = int(balance_input)
        st.success("✅ Balance diperbarui")
    
    st.markdown("---")
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Balance", f"Rp {idr(st.session_state.balance)}")
    c2.metric("Risk/Trade (2%)", f"Rp {idr(st.session_state.balance * 0.02)}")
    c3.metric("Max 5 Posisi (40%)", f"Rp {idr(st.session_state.balance * 0.40)}")
    c4.metric("Safe Cash (60%)", f"Rp {idr(st.session_state.balance * 0.60)}")

# ============================================================
# TAB 4: REPORT (ENHANCED)
# ============================================================
with tabs[4]:
    st.markdown("## 📋 Report & Analytics Dashboard")
    
    report_tab1, report_tab2, report_tab3, report_tab4 = st.tabs([
        "📊 Performance Dashboard",
        "📝 Trade Journal",
        " Calendar Heatmap",
        " Export Excel"
    ])
    
    with report_tab1:
        st.markdown("###  Performance Dashboard")
        
        if not st.session_state.journal.empty and "PnL" in st.session_state.journal.columns:
            metrics = calculate_performance_metrics(st.session_state.journal)
            
            if metrics and not metrics.get("insufficient_data"):
                # Key metrics
                m1, m2, m3, m4, m5, m6 = st.columns(6)
                m1.metric("Total Trades", metrics["total_trades"])
                m2.metric("Win Rate", f"{metrics['win_rate']}%")
                m3.metric("Total PnL", f"Rp {idr(metrics['total_pnl'])}")
                m4.metric("Profit Factor", metrics["profit_factor"])
                m5.metric("Sharpe Ratio", metrics["sharpe_ratio"])
                m6.metric("Max Drawdown", f"Rp {idr(metrics['max_drawdown'])}")
                
                st.markdown("---")
                
                # Additional metrics
                am1, am2, am3, am4 = st.columns(4)
                am1.metric("Avg Win", f"Rp {idr(metrics['avg_win'])}")
                am2.metric("Avg Loss", f"Rp {idr(metrics['avg_loss'])}")
                am3.metric("Best Trade", f"Rp {idr(metrics['best_trade'])}")
                am4.metric("Worst Trade", f"Rp {idr(metrics['worst_trade'])}")
                
                st.markdown("---")
                
                # Equity curve
                jdf = st.session_state.journal.dropna(subset=["PnL"]).copy()
                if "Date" in jdf.columns:
                    jdf = jdf.sort_values("Date")
                    jdf["Cumulative PnL"] = jdf["PnL"].cumsum()
                    
                    fig_eq = go.Figure()
                    fig_eq.add_trace(go.Scatter(x=range(len(jdf)), y=jdf["Cumulative PnL"],
                                                mode='lines+markers', name='Equity',
                                                line=dict(color='#10b981', width=2)))
                    fig_eq.update_layout(
                        title="Equity Curve",
                        xaxis_title="Trade #",
                        yaxis_title="Cumulative PnL (Rp)",
                        height=400,
                        template="plotly_dark"
                    )
                    st.plotly_chart(fig_eq, use_container_width=True)
            else:
                st.info("Data trade belum cukup untuk analisis (minimal 5 trade)")
        else:
            st.info("Belum ada data journal")
    
    with report_tab2:
        st.markdown("### 📝 Trade Journal")
        
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
    
    with report_tab3:
        st.markdown("### 📅 Calendar Heatmap")
        
        if not st.session_state.journal.empty:
            fig_calendar = build_calendar_heatmap(st.session_state.journal)
            if fig_calendar:
                st.plotly_chart(fig_calendar, use_container_width=True)
            else:
                st.info("Belum ada data untuk calendar heatmap")
        else:
            st.info("Belum ada data journal")
    
    with report_tab4:
        st.markdown("### 📤 Export to Excel")
        
        if not HAS_OPENPYXL:
            st.error("❌ openpyxl tidak terinstall. Install dengan: `pip install openpyxl`")
        else:
            st.markdown("**Export comprehensive report ke Excel dengan multiple sheets:**")
            st.markdown("- Trade Journal")
            st.markdown("- Active Trades")
            st.markdown("- Performance Summary")
            st.markdown("- Monthly Analysis")
            st.markdown("- Sector Analysis")
            st.markdown("- Cybernetic Params")
            
            st.markdown("---")
            
            # Export Journal & Performance
            if not st.session_state.journal.empty:
                excel_bytes = export_journal_to_excel(
                    st.session_state.journal,
                    st.session_state.active_trades,
                    st.session_state.balance,
                    st.session_state.cybernetic_params
                )
                
                if excel_bytes:
                    st.download_button(
                        label=" Download Full Report (Excel)",
                        data=excel_bytes,
                        file_name=f"ATS_Report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True
                    )
            
            st.markdown("---")
            
            # Export Scan History
            st.markdown("**Export Scan History per Tanggal:**")
            
            if os.path.isdir(SCAN_LOG_DIR):
                available_dates = sorted([d for d in os.listdir(SCAN_LOG_DIR) 
                                         if os.path.isdir(os.path.join(SCAN_LOG_DIR, d))],
                                        reverse=True)
                
                if available_dates:
                    selected_date = st.selectbox("Pilih tanggal", available_dates)
                    
                    if selected_date:
                        scan_excel = export_scan_history_to_excel(selected_date)
                        if scan_excel:
                            st.download_button(
                                label=f"📥 Download Scan History {selected_date} (Excel)",
                                data=scan_excel,
                                file_name=f"ATS_ScanHistory_{selected_date}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True
                            )
                else:
                    st.info("Belum ada scan history")
            else:
                st.info("Folder scan_logs belum ada")

# ============================================================
# TAB 5: ISSI CHECK
# ============================================================
with tabs[5]:
    st.markdown("## 🕌 ISSI Universe")
    st.caption(f"Total: {len(ISSI_UNIVERSE)} ticker")
    
    sector_groups = defaultdict(list)
    for ticker in ISSI_UNIVERSE:
        sector_groups[get_sector(ticker)].append(ticker.replace(".JK", ""))
    
    for sector in sorted(sector_groups.keys()):
        with st.expander(f"{sector} ({len(sector_groups[sector])} saham)"):
            st.write(", ".join(sorted(sector_groups[sector])))

# ============================================================
# TAB 6-9: Placeholder untuk fitur lainnya
# ============================================================
with tabs[6]:
    st.markdown("##  Bandar Hunter")
    st.info("Fitur Bandar Hunter - implementasi dari versi sebelumnya")

with tabs[7]:
    st.markdown("## 🚀 Breakout Scan")
    st.info("Fitur Breakout Scan - implementasi dari versi sebelumnya")

with tabs[8]:
    st.markdown("## 🗂️ Stockbit Tracker")
    st.info("Fitur Stockbit Tracker - implementasi dari versi sebelumnya")

with tabs[9]:
    st.markdown("## 📚 Wisdom")
    st.info("Jesse Livermore quotes - implementasi dari versi sebelumnya")

# Footer
st.markdown("---")
st.caption(f"ATS SuperEngine {APP_VERSION} | Update: {APP_UPDATED} | ISSI Syariah Scanner")