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

# ============================================================
# KONFIGURASI
# ============================================================
FINNHUB_API_KEY    = os.environ.get("FINNHUB_API_KEY", "")
TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT      = os.environ.get("TELEGRAM_CHAT", "")
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
STATE_FILE         = "ats_state.json"
JOURNAL_FILE       = "journal.csv"
ACTIVE_FILE        = "active_trades.csv"
LOG_FILE           = "ats.log"

# ============================================================
# [Task 1] STRUCTURED LOGGING — ats.log
# ============================================================
def _setup_logger() -> logging.Logger:
    """Setup file logger untuk ats.log dengan format terstruktur."""
    logger = logging.getLogger("ats")
    if logger.handlers:
        return logger   # sudah di-setup, hindari duplicate handler

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler
    try:
        fh = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass

    # Console handler (juga ke stdout untuk Railway logs)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    logger.propagate = False
    return logger

LOG = _setup_logger()

def log_scan_event(ticker: str, status: str, score: float | None = None,
                   reason: str = "", regime: str = "-",
                   rr: float | None = None, conf: int | None = None,
                   extra: dict | None = None):
    """Structured logging satu event scan."""
    parts = [f"ticker={ticker}", f"status={status}", f"regime={regime}"]
    if score is not None: parts.append(f"score={score:.1f}")
    if rr    is not None: parts.append(f"rr={rr:.1f}")
    if conf  is not None: parts.append(f"conf={conf}")
    if reason:            parts.append(f"reason='{reason}'")
    if extra:
        for k, v in extra.items():
            parts.append(f"{k}={v}")
    LOG.info(" | ".join(parts))

# ============================================================
# [Task 3] THREAD SAFETY — locks untuk shared state
# ============================================================
_breadth_lock = threading.Lock()
_spike_lock   = threading.Lock()
_state_lock   = threading.Lock()
_telegram_lock = threading.Lock()

# ============================================================
# VERSION HISTORY
# ============================================================
APP_VERSION  = "V5.6.3"
APP_UPDATED  = "06 Mei 2026"

VERSION_HISTORY = [
    {
        "versi":   "V5.6.3",
        "tanggal": "06 Mei 2026",
        "tipe":    "Filter Calibration — Signal Drought Fix",
        "ringkasan": "Pisah bandar dari hard gate + WEAK breakout threshold turun 0.8→0.6 untuk atasi 1 bulan zero signal",
        "detail": [
            "[FIX #1] breakout_confirmation: WEAK vol threshold 0.8 → 0.6",
            "  Sebelumnya: akumulasi diam-diam (vol 0.6-0.8x avg) tidak terdeteksi sebagai WEAK",
            "  Sekarang: threshold lebih rendah menangkap pola accumulation sebelum breakout penuh",
            "[FIX #2] scan_core: bandar dipisah dari hard gate — breakout WAIT saja yang jadi blocker",
            "  Sebelumnya: bandar < 2 OR breakout WAIT → dual hard gate, berkorelasi tinggi → 0 signal",
            "  Sekarang: bandar tetap masuk confluence (1/6) + scoring, bukan mandatory blocker",
            "[FIX #3] mini_scan_spike: konsisten dengan scan_core — bandar bukan hard gate",
        ]
    },
    {
        "versi":   "V5.6.2",
        "tanggal": "05 Mei 2025",
        "tipe":    "Bug Fix — Stabilization",
        "ringkasan": "4 surgical bug fixes: scoring consistency, liquidity filter sync, atomic state write, starvation logic",
        "detail": [
            "[FIX #1] mini_scan_spike: scoring sekarang regime-aware via get_adaptive_weights()",
            "  Sebelumnya: calculate_score tanpa regime — default SIDEWAYS selamanya",
            "  Sekarang: pass spike_regime + ada_weights — konsisten dengan scan_core",
            "[FIX #2] auto_scan_background: tambah MIN_DAILY_VOLUME_IDR filter",
            "  Sebelumnya: saham illiquid bisa lolos auto-scan tapi tidak lolos manual scan",
            "  Sekarang: universe identik antara auto-scan dan manual scan UI",
            "[FIX #3] auto_scan_background: signal_lock write sekarang atomic",
            "  Sebelumnya: bare open('w') — race condition dengan UI thread save_state()",
            "  Sekarang: _state_lock + os.replace — zero corruption risk",
            "[FIX #4] check_opportunity_starvation: TIGHT loosen flag diset False",
            "  Sebelumnya: loosen=True di TIGHT tapi tidak pernah di-honor di scan_core",
            "  Sekarang: loosen=False — API return value konsisten dengan behavior",
        ]
    },
    {
        "versi":   "V5.6.1",
        "tanggal": "01 Mei 2025",
        "tipe":    "Visual Identity",
        "ringkasan": "BMW M Sport racing stripes di header — motivation accent",
        "detail": [
            "Diagonal racing stripes di pojok kanan header card",
            "3 warna BMW M Sport authentic:",
            "  Light Blue #0066B1 (BMW Motorsport)",
            "  Dark Blue #1C3D7C (BMW M)",
            "  Red #E22718 (BMW M signature)",
            "Pure CSS pseudo-element — zero logic touch, zero performance impact",
            "Stripes opacity 0.85 dengan diagonal 110deg untuk racing livery feel",
            "Konten header tetap readable dengan z-index layering",
            "Reminder visual untuk motivasi capai target trading 🏎️",
        ]
    },
    {
        "versi":   "V5.6",
        "tanggal": "01 Mei 2025",
        "tipe":    "Auto-Backup Feature",
        "ringkasan": "Auto-save scan log harian — analisis pattern tanpa manual download",
        "detail": [
            "Auto-save setiap scan ke folder scan_logs/YYYY-MM-DD/",
            "Per scan tersimpan 3 file: debug_full, summary alasan gugur, candidates",
            "Filename: HH-MM_label_REGIME (e.g. 09-05_pre_open_BULLISH_debug.csv)",
            "Berlaku untuk manual scan + auto-scan 5x/hari",
            "Tab Report: section 'Scan History' baru — pilih tanggal, download ZIP/file",
            "Tombol 'Download Semua (ZIP)' untuk bulk download per hari",
            "Auto cleanup: hapus folder > 30 hari setiap jam 16:00 WIB",
            "Tidak mengganggu logic scan — purely append observability",
        ]
    },
    {
        "versi":   "V5.5.3",
        "tanggal": "01 Mei 2025",
        "tipe":    "Data-Driven Calibration",
        "ringkasan": "2 kalibrasi targeted berbasis analisis CSV debug 93 ticker",
        "detail": [
            "[FIX A] RSI gate SIDEWAYS lower bound: 42 → 38",
            "  Root cause: 26/28 saham gugur RSI di range 35-42 — bluechip syariah",
            "  base recovery (TLKM 37.7, ICBP 34.7, MTEL 39.6, MYOR 39.8, dll)",
            "  Risk: sistem buta total terhadap base recovery di sideways",
            "  RSI 38 masih jauh dari true oversold (< 30) — aman untuk swing entry",
            "[FIX B] Entry freshness untuk Breakout WAIT: 3.0% → 4.5%",
            "  Root cause: 8 saham momentum kuat ditolak (TINS+6.4%, CPIN+4.2%,",
            "  INDF+3.3%, MAPI+3.3%, ISAT+3.8%, ADMR+3.2%, PNLF+3.3%, SCMA+3.2%)",
            "  Risk: selalu telat 1 hari di market gradual movement",
            "  4.5% adalah balance: tidak FOMO (di bawah 5% WEAK), tidak terlalu strict",
            "  Tetap ada hierarchy: WAIT 4.5 < WEAK 5.0 < WEAK+momentum 6.0 < VALID 7.0",
            "Estimasi dampak: 0-3 kandidat di market sideways (sebelumnya 0 total)",
            "Karakter sistem: tetap konservatif, tidak ada banjir kandidat",
        ]
    },
    {
        "versi":   "V5.5.2",
        "tanggal": "01 Mei 2025",
        "tipe":    "UX Enhancement",
        "ringkasan": "Download CSV untuk Scan Debug — analisis offline tanpa screenshot",
        "detail": [
            "Tombol '📥 Download Full Debug Log (CSV)' — semua ticker + alasan gugur",
            "Tombol '📊 Download Summary Gugur (CSV)' — distribusi alasan gugur",
            "Tombol download untuk hasil filter — kalau user filter sektor/status",
            "Filename otomatis: ats_debug_full_YYYYMMDD_HHMM_REGIME.csv",
            "Memudahkan analisis offline dan share log untuk audit",
            "Tidak ada perubahan logic scan — purely visibility enhancement",
        ]
    },
    {
        "versi":   "V5.5.1",
        "tanggal": "30 Apr 2025",
        "tipe":    "Targeted Audit Fix",
        "ringkasan": "3 critical fix dari deep technical audit — no architecture rewrite, minimal delta",
        "detail": [
            "[FIX #1] mini_scan_spike() tidak lagi hardcode 'SIDEWAYS' regime",
            "  Sebelumnya: spike di market BULLISH pakai RSI gate 42-72 (salah)",
            "  Sekarang: regime di-pass dari intraday_refresh_job via state file",
            "  Spike alert konsisten dengan main scanner di semua regime",
            "[FIX #2] Score overflow di BULLISH regime dieliminasi",
            "  Sebelumnya: max base_score = 103 sebelum cap min(100)",
            "  Sekarang: RR bonus +3 di-clamp dalam rr_max, total dijamin <= 100",
            "  Distribusi score top-end tidak lagi terkompresi → ranking diskriminatif",
            "[FIX #3] Defensive coding terhadap engine eksternal black box",
            "  Helper baru is_trap_signal() — normalize bool/string/int return",
            "  liquidity_trap & fake_breakout tetap aman jika engine ubah return type",
            "Validation: 0% score distortion untuk 8 test case realistic",
            "save_state sekarang simpan last_regime untuk continuity intraday refresh",
        ]
    },
    {
        "versi":   "V5.5",
        "tanggal": "30 Apr 2025",
        "tipe":    "Feature + Bug Fix",
        "ringkasan": "Gemini API support (FREE) + Fix balance MixedNumericTypesError",
        "detail": [
            "Multi-provider AI: Anthropic Claude (premium) + Google Gemini (FREE tier)",
            "Auto-pilih provider — Anthropic prioritas, fallback ke Gemini",
            "Gemini 2.0 Flash: gratis, 60 request/menit, kualitas analisis bagus",
            "Caption Deep Analysis tampilkan provider aktif (🟣 Claude / 🔵 Gemini)",
            "Setup Gemini di info card — link langsung ke aistudio.google.com/apikey",
            "Fix MixedNumericTypesError: balance dipaksa int agar konsisten dengan number_input",
            "Root cause di load_state — balance simpan as int, bukan float",
            "Cache deep analysis sekarang track provider mana yang dipakai",
        ]
    },
    {
        "versi":   "V4.2",
        "tanggal": "27 Apr 2025",
        "tipe":    "Upgrade",
        "ringkasan": "Tambah scan 09:30 Early Momentum + perkuat deteksi early mover",
        "detail": [
            "Scan ke-5 ditambahkan jam 09:30 WIB — tangkap momentum 30 menit pertama",
            "Entry freshness diperkuat: VALID breakout boleh +7%, WEAK +5%, tanpa breakout +3%",
            "Early mover bonus score (di-revert di V4.2b karena FOMO bias)",
            "Jadwal How To Use diperbarui menampilkan 5 waktu scan",
        ]
    },
    {
        "versi":   "V4.1",
        "tanggal": "27 Apr 2025",
        "tipe":    "Bug Fix + Kalibrasi",
        "ringkasan": "4 bug kritis + 6 kalibrasi + 3 upgrade akurasi",
        "detail": [
            "[B1] entry_system tidak lagi baca session_state di background thread",
            "[B2] Score overflow diperbaiki: min(100) setelah semua bonus",
            "[B3] EXECUTE NOW tidak lagi butuh timing self-contradictory",
            "[B4] IDX holidays diperluas ke 2026 + fungsi is_holiday() extensible",
            "[K1] RSI gate adaptif per regime: BULLISH 45-78, DIST 40-68",
            "[K2] Confluence minimum adaptif: BULLISH 4/6, lainnya 3/6",
            "[K3] Bandar spike threshold 2.0x → 1.8x",
            "[K4] Accumulation compression 8% → 12%",
            "[K5] Intraday period 2d → 5d untuk VWAP stabil",
            "[K6] Hapus duplikasi sector_momentum di run_scanner",
            "Fix Pivot Point: pakai candle kemarin (iloc[-2]) bukan hari ini",
            "Volume tier filter: skip saham < Rp 500 juta/hari",
            "Entry freshness: skip jika harga sudah naik > 3% tanpa breakout valid",
        ]
    },
    {
        "versi":   "V4.0",
        "tanggal": "26 Apr 2025",
        "tipe":    "Major Release",
        "ringkasan": "Refactor besar + How To Use baru + UI profesional",
        "detail": [
            "Refactor: scan_core() satu fungsi untuk UI dan auto-scan",
            "Balance hardcode 800k → baca dari ats_state.json",
            "Dead code dihapus: finnhub_quote, pullback_zone, lot_size",
            "Signal lock auto-expire 7 hari",
            "Cybernetic min_trades 8 → 20 (lebih valid statistik)",
            "Hari libur IDX 2025 ditambahkan ke filter market",
            "How To Use ditulis ulang lengkap untuk user awam",
            "Top N input dihapus, fixed 5 kandidat terbaik",
            "Balance tersimpan di JSON (persist across restart)",
            "Active trades tambah kolom ExitPrice, ExitDate",
            "Journal tambah validasi kolom wajib",
            "Telegram summary meski tidak ada sinyal EXECUTE",
            "Server health check: notif Telegram saat restart",
        ]
    },
    {
        "versi":   "V3.0",
        "tanggal": "22 Apr 2025",
        "tipe":    "Major Release",
        "ringkasan": "Deploy ke Railway + auto-scheduler + banyak perbaikan",
        "detail": [
            "VWAP kumulatif 6 bulan → rolling 20 hari",
            "RSI simple rolling → Wilder's smoothing (standar industri)",
            "ATR Wilder's EWM untuk stop loss lebih akurat",
            "Pivot Point formula diperbaiki",
            "Bug BUY logic diperbaiki: tambah yang belum ada di active trades",
            "Validasi data ticker: skip < 60 bar, harga/vol nol",
            "Filter sektor: hanya sektor momentum positif",
            "Scan debug expander: alasan gugur per ticker + bar chart",
            "Equity curve & drawdown chart di tab Report",
            "APScheduler: auto-scan 4x sehari jam IDX",
            "Deploy ke Railway: 24/7 tanpa buka komputer",
            "Tombol scanner warna hijau",
            "Input balance dipindah ke tab Account",
        ]
    },
    {
        "versi":   "V2.1",
        "tanggal": "21 Apr 2025",
        "tipe":    "Initial Release",
        "ringkasan": "Versi pertama ATS SuperEngine",
        "detail": [
            "Scanner saham syariah ISSI berbasis multi-layer filter",
            "6 sinyal: momentum, accumulation, bandar, breakout, follow through, intraday",
            "Dynamic threshold percentile P88/P70/P45",
            "Cybernetic feedback engine adaptif",
            "Telegram alert untuk sinyal EXECUTE & EXECUTE NOW",
            "TradingView chart embed",
            "Sector leader radar",
            "Active trades & trade journal",
        ]
    },
]

# ============================================================
# TIMEZONE & JADWAL IDX
# ============================================================
WIB = pytz.timezone("Asia/Jakarta")

SCAN_SCHEDULE = [
    {"hour": 9,  "minute": 5,  "label": "Pre-Open"},
    {"hour": 9,  "minute": 30, "label": "Early Momentum"},   # ← catch early mover
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
def send_telegram(message: str, retries: int = 2):
    """
    [Task 5] Kirim Telegram dengan logging dan retry ringan.
    Tidak lagi silent fail — semua error tercatat di ats.log.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        LOG.warning("Telegram tidak terkirim: TOKEN atau CHAT belum di-set")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    with _telegram_lock:   # [Task 3] thread-safe send
        for attempt in range(retries + 1):
            try:
                resp = requests.post(
                    url,
                    data={"chat_id": TELEGRAM_CHAT, "text": message},
                    timeout=10,
                )
                if resp.status_code == 200:
                    if attempt > 0:
                        LOG.info(f"Telegram berhasil terkirim (retry {attempt})")
                    return True
                else:
                    LOG.warning(
                        f"Telegram failed status={resp.status_code} "
                        f"body={resp.text[:200]} attempt={attempt+1}/{retries+1}"
                    )
            except requests.Timeout:
                LOG.warning(f"Telegram timeout attempt={attempt+1}/{retries+1}")
            except Exception as e:
                LOG.warning(f"Telegram error: {type(e).__name__}: {str(e)[:200]} attempt={attempt+1}")

            # Retry dengan backoff
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))

        LOG.error(f"Telegram FAILED setelah {retries+1} percobaan: {message[:80]}...")
        return False


# ============================================================
# AI PROVIDER ABSTRACTION — Anthropic Claude / Google Gemini
# ============================================================
def get_ai_provider() -> str:
    """Return provider yang aktif: 'anthropic', 'gemini', atau 'none'."""
    if ANTHROPIC_API_KEY: return "anthropic"
    if GEMINI_API_KEY:    return "gemini"
    return "none"

def call_ai_anthropic(system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> tuple[bool, str]:
    """Call Anthropic Claude API. Return (success, text_or_error)."""
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model":      "claude-sonnet-4-20250514",
                "max_tokens": max_tokens,
                "system":     system_prompt,
                "messages":   [{"role": "user", "content": user_prompt}],
            },
            timeout=90,
        )
        if resp.status_code == 200:
            data = resp.json()
            text = "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
            return True, text
        else:
            err = f"Anthropic API error {resp.status_code}: {resp.text[:300]}"
            LOG.error(err)
            return False, err
    except Exception as e:
        err = f"Anthropic exception: {type(e).__name__}: {str(e)[:200]}"
        LOG.error(err)
        return False, err

def call_ai_gemini(system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> tuple[bool, str]:
    """
    Call Google Gemini API (free tier: 60 req/min).
    Return (success, text_or_error).
    """
    try:
        # Gemini menggabungkan system + user dalam satu prompt
        full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        )
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{
                    "parts": [{"text": full_prompt}]
                }],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "temperature":     0.7,
                }
            },
            timeout=90,
        )
        if resp.status_code == 200:
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return False, "Gemini: tidak ada candidates di response"
            parts = candidates[0].get("content", {}).get("parts", [])
            text  = "".join(p.get("text", "") for p in parts)
            return True, text
        else:
            err = f"Gemini API error {resp.status_code}: {resp.text[:300]}"
            LOG.error(err)
            return False, err
    except Exception as e:
        err = f"Gemini exception: {type(e).__name__}: {str(e)[:200]}"
        LOG.error(err)
        return False, err

def call_ai(system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> tuple[bool, str, str]:
    """
    Multi-provider AI call. Auto-pilih Anthropic kalau tersedia,
    fallback ke Gemini, atau return error kalau keduanya kosong.
    Return (success, text_or_error, provider_used).
    """
    provider = get_ai_provider()
    if provider == "anthropic":
        ok, text = call_ai_anthropic(system_prompt, user_prompt, max_tokens)
        return ok, text, "anthropic"
    elif provider == "gemini":
        ok, text = call_ai_gemini(system_prompt, user_prompt, max_tokens)
        return ok, text, "gemini"
    else:
        return False, "Tidak ada AI provider yang aktif", "none"


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

# [Task 7] CONFIG VALIDATION — guard against invalid persisted state
CONFIG_RANGES = {
    "min_score":             (50, 95),
    "execute_now_threshold": (70, 98),
    "min_rr":                (1.0, 5.0),
}

def validate_cyber_params(params: dict) -> dict:
    """
    [Task 7] Validasi cybernetic params, clamp ke range valid.
    Return params yang sudah divalidasi (tidak modify input).
    Log warning kalau ada nilai yang harus di-clamp.
    """
    if not isinstance(params, dict):
        LOG.warning(f"validate_cyber_params: bukan dict ({type(params)}), pakai DEFAULT")
        return DEFAULT_CYBER.copy()

    validated = params.copy()

    for key, (lo, hi) in CONFIG_RANGES.items():
        try:
            val = float(validated.get(key, DEFAULT_CYBER[key]))
        except (TypeError, ValueError):
            LOG.warning(f"config invalid type {key}={validated.get(key)}, fallback default")
            validated[key] = DEFAULT_CYBER[key]
            continue

        if val < lo or val > hi:
            clamped = max(lo, min(hi, val))
            LOG.warning(f"config {key}={val} di luar range [{lo},{hi}], clamp ke {clamped}")
            validated[key] = clamped
        else:
            validated[key] = val

    # Pastikan keys lain ada
    for key in DEFAULT_CYBER:
        if key not in validated:
            validated[key] = DEFAULT_CYBER[key]

    # adjustment_history harus list
    if not isinstance(validated.get("adjustment_history"), list):
        validated["adjustment_history"] = []

    return validated

def load_state() -> dict:
    """[Task 7] Load state dengan validasi config + logging."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["cybernetic_params"] = validate_cyber_params(
                data.get("cybernetic_params", DEFAULT_CYBER.copy())
            )
            try:
                bal = int(float(data.get("balance", 800_000)))
                if bal < 100_000:
                    LOG.warning(f"balance terlalu kecil ({bal}), set ke 800.000")
                    bal = 800_000
                data["balance"] = bal
            except (TypeError, ValueError):
                LOG.warning("balance corrupt, fallback ke 800.000")
                data["balance"] = 800_000
            if not isinstance(data.get("signal_lock"), dict):
                data["signal_lock"] = {}
            return data
        except Exception as e:
            LOG.error(f"load_state corrupt: {type(e).__name__}: {e} — pakai default")
    return {
        "cybernetic_params": DEFAULT_CYBER.copy(),
        "signal_lock":       {},
        "balance":           800_000,
    }

def save_state():
    """
    [Task 4] Atomic JSON save — mencegah corrupt saat concurrent write.
    Pattern: write to temp → flush → fsync → os.replace.
    [Task 3] Thread-safe via _state_lock.
    """
    with _state_lock:
        # Bersihkan signal_lock yang sudah > 7 hari
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
            "balance":           st.session_state.balance,
            "last_regime":       st.session_state.get("last_regime", "SIDEWAYS"),
        }

        # Atomic write
        try:
            dir_path = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
            fd, tmp_path = tempfile.mkstemp(
                prefix=".ats_state_", suffix=".tmp", dir=dir_path
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, STATE_FILE)   # atomic rename
            except Exception as e:
                LOG.error(f"save_state atomic write gagal: {type(e).__name__}: {e}")
                if os.path.exists(tmp_path):
                    try: os.remove(tmp_path)
                    except Exception: pass
                raise
        except Exception as e:
            LOG.error(f"save_state EXCEPTION: {type(e).__name__}: {str(e)[:200]}")

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
        rsi_min, rsi_max = 42, 78   # Lebih toleran overbought saat bullish
    elif regime == "DISTRIBUTION":
        rsi_min, rsi_max = 40, 68   # Lebih ketat saat distribusi
    else:                            # SIDEWAYS / VOLATILE / unknown
        # [V5.5.3 FIX A] Lower bound 42 → 38 berdasarkan analisis CSV debug:
        # 26 dari 28 saham gugur RSI di range 35-42 (bluechip syariah base recovery,
        # bukan true oversold). Range 38-72 lebih realistic untuk pasar IDX.
        rsi_min, rsi_max = 38, 72
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
# TARGET — Pivot Point dari candle SEBELUMNYA (lebih akurat)
# ============================================================
def find_target(df: pd.DataFrame, entry: float) -> float:
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()
    close = df["Close"].squeeze()

    # Pivot Point standar: dari candle SEBELUMNYA (iloc[-2]),
    # bukan candle hari ini — karena hari ini belum tutup
    pp = (float(high.iloc[-2]) + float(low.iloc[-2]) + float(close.iloc[-2])) / 3
    r1 = 2 * pp - float(low.iloc[-2])
    r2 = pp + (float(high.iloc[-2]) - float(low.iloc[-2]))
    r3 = float(high.iloc[-2]) + 2 * (pp - float(low.iloc[-2]))  # R3 tambahan

    # Fibonacci retracement dari swing 20 hari
    swing_high = float(high.tail(20).max())
    swing_low  = float(low.tail(20).min())
    fib_618    = swing_low + 0.618 * (swing_high - swing_low)
    fib_100    = swing_high   # 100% = swing high itu sendiri

    # Kumpulkan semua kandidat yang valid (min 2% di atas entry)
    candidates = sorted([
        v for v in [r1, r2, r3, fib_618, fib_100]
        if v > entry * 1.02
    ])

    if not candidates:
        return entry * 1.07   # fallback minimal 7%

    # Ambil target terdekat yang masih memberikan RR layak (min 4% dari entry)
    target = candidates[0]
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
    prev_price = float(close.iloc[-2])
    last_vwap  = float(vwap.iloc[-1]) if not np.isnan(vwap.iloc[-1]) else last_price
    avg_vol    = float(volume.tail(20).mean())
    change_pct = (last_price - prev_price) / prev_price * 100 if prev_price > 0 else 0
    score = 0
    if change_pct > 0.8 and float(volume.iloc[-1]) > avg_vol * 1.2: score += 1
    if last_price > last_vwap and change_pct > 0:                   score += 1
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
    high        = df["High"].squeeze()
    volume      = df["Volume"].squeeze()
    last        = float(close.iloc[-1])
    prev        = float(close.iloc[-2])
    recent_high = float(high.iloc[:-1].tail(10).max())
    avg_vol     = float(volume.tail(20).mean())
    vol_ratio   = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1.0
    change_pct  = (last - prev) / prev * 100 if prev > 0 else 0
    breakout    = last >= recent_high
    near_breakout = last >= recent_high * 0.99 and change_pct > 0
    if breakout and vol_ratio > 1.3:
        return "VALID"
    if near_breakout and vol_ratio >= 0.6:   # [V5.6.3] 0.8 → 0.6: akumulasi diam-diam valid
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
        latest_date = pd.to_datetime(df5.index[-1]).date()
        day5 = df5[pd.to_datetime(df5.index).date == latest_date]
        if day5 is None or len(day5) < 3:
            day5 = df5.tail(min(len(df5), 20))

        close     = df5["Close"].squeeze()
        day_close = day5["Close"].squeeze()
        day_vol   = day5["Volume"].squeeze()
        day_vwap  = rolling_vwap(day5, min(20, len(day5)))
        recent_change = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
        open_change   = (float(day_close.iloc[-1]) - float(day_close.iloc[0])) / float(day_close.iloc[0]) * 100
        avg_vol       = float(day_vol.iloc[:-1].tail(10).mean()) if len(day_vol) > 10 else float(day_vol.mean())
        last_vwap     = float(day_vwap.iloc[-1]) if not np.isnan(day_vwap.iloc[-1]) else float(day_close.iloc[-1])
        score = 0
        if open_change > 1.0 or recent_change > 0.3:                  score += 1
        if float(day_close.iloc[-1]) > last_vwap and open_change > 0: score += 1
        if avg_vol > 0 and float(day_vol.iloc[-1]) > avg_vol * 1.3:   score += 1
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


# [FIX #3] Helper untuk normalize return value engine eksternal
# Engine bisa return: bool True/False, string "TRAP"/"OK", atau int 0/1
# Defensive coding — tidak bergantung pada specific return type
def is_trap_signal(value) -> bool:
    """Normalize liquidity_trap / fake_breakout return ke boolean."""
    if value is True or value is False:
        return bool(value)
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        # Hanya string yang explicit "TRAP" yang dianggap True
        # String "OK" / "" / lainnya → False
        return value.strip().upper() in ("TRAP", "TRUE", "1", "YES")
    return False

# ============================================================
# ▓▓▓ DEEP ARCHITECTURE UPGRADE — V5.1 ▓▓▓
# Modular engines, backward compatible, no redesign
# ============================================================

# ── PRIORITAS 4: ADAPTIVE WEIGHT ENGINE ─────────────────────
# Bobot scoring adaptif per regime — tidak lagi hardcode statis
REGIME_WEIGHTS: dict[str, dict] = {
    "BULLISH": {
        "prob":     0.30,   # momentum paling penting saat bullish
        "runner":   0.25,
        "quality":  0.10,
        "rr":       0.15,
        "liquidity":0.10,
        "bandar":   0.10,
        # bonus multipliers untuk scan_core
        "momentum_w": 1.2,
        "accum_w":    0.8,
        "ft_w":       1.0,
        "intraday_w": 0.8,
    },
    "SIDEWAYS": {
        "prob":     0.20,
        "runner":   0.15,
        "quality":  0.15,
        "rr":       0.25,   # RR paling penting saat sideways
        "liquidity":0.10,
        "bandar":   0.15,
        "momentum_w": 0.8,
        "accum_w":    1.2,  # akumulasi lebih penting
        "ft_w":       0.8,
        "intraday_w": 1.0,
    },
    "DISTRIBUTION": {
        "prob":     0.15,
        "runner":   0.10,
        "quality":  0.15,
        "rr":       0.20,
        "liquidity":0.25,   # likuiditas paling penting saat distribusi
        "bandar":   0.15,
        "momentum_w": 0.6,
        "accum_w":    1.0,
        "ft_w":       0.6,
        "intraday_w": 1.2,
    },
    "VOLATILE": {
        "prob":     0.20,
        "runner":   0.15,
        "quality":  0.10,
        "rr":       0.25,
        "liquidity":0.20,
        "bandar":   0.10,
        "momentum_w": 0.7,
        "accum_w":    0.9,
        "ft_w":       0.8,
        "intraday_w": 1.3,
    },
}

def get_adaptive_weights(regime: str) -> dict:
    """Return bobot scoring adaptif berdasarkan regime."""
    return REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["SIDEWAYS"])


# ── PRIORITAS 6: OVERFITTING CONTROL ────────────────────────
def check_opportunity_starvation(debug_log: list, n_universe: int) -> dict:
    """
    Deteksi apakah sistem terlalu selektif (opportunity starvation).
    Kalau terlalu sedikit kandidat → longgarkan threshold dinamis.
    Return: dict dengan rekomendasi penyesuaian.
    """
    if not debug_log:
        return {"status": "NO_DATA", "loosen": False}

    lolos = sum(1 for d in debug_log if "LOLOS" in str(d.get("❌ Gugur di", "")))
    pct_lolos = lolos / max(n_universe, 1) * 100

    # Hitung distribusi alasan gugur
    reasons: dict[str, int] = {}
    for d in debug_log:
        reason = str(d.get("❌ Gugur di", ""))
        if "LOLOS" not in reason:
            key = reason.split("(")[0].split(":")[0].strip()[:30]
            reasons[key] = reasons.get(key, 0) + 1

    # Deteksi dominan satu filter
    top_reason_count = max(reasons.values()) if reasons else 0
    single_filter_dominant = top_reason_count > n_universe * 0.5

    status = "HEALTHY"
    loosen = False

    if pct_lolos == 0:
        status = "STARVATION"    # tidak ada kandidat sama sekali
        loosen = True
    elif pct_lolos < 2:
        status = "TIGHT"         # sangat sedikit kandidat — monitor, jangan longgarkan
        loosen = False           # [FIX #4] threshold loosening hanya untuk true STARVATION (0 kandidat)
                                 # TIGHT = kondisi selektif valid, bukan alasan longgarkan filter
    elif pct_lolos < 5:
        status = "SELECTIVE"     # cukup selektif, normal
    else:
        status = "HEALTHY"

    return {
        "status":                  status,
        "pct_lolos":               round(pct_lolos, 1),
        "loosen":                  loosen,
        "single_filter_dominant":  single_filter_dominant,
        "top_reasons":             dict(sorted(reasons.items(), key=lambda x: -x[1])[:3]),
    }


# ── PRIORITAS 7: PERFORMANCE — Cache deduplikasi ────────────
_breadth_cache: dict = {"data": None, "ts": 0, "ttl": 600}  # cache 10 menit

def get_market_breadth_cached(market: dict) -> tuple[float, str, dict]:
    """Market breadth dengan cache 10 menit — hemat komputasi."""
    now = time.time()
    if _breadth_cache["data"] and now - _breadth_cache["ts"] < _breadth_cache["ttl"]:
        return _breadth_cache["data"]
    result = calculate_market_breadth(market)
    _breadth_cache["data"] = result
    _breadth_cache["ts"]   = now
    return result


# ============================================================
# SCORE — upgrade dengan adaptive weights
# ============================================================
def calculate_score(prob: float, runner: float, quality: str,
                    rr: float, liquidity: str, bandar_score: int,
                    regime: str = "SIDEWAYS") -> float:
    """
    Scoring dengan adaptive weights per regime.
    Backward compatible — regime default SIDEWAYS = perilaku lama.

    [FIX #2] RR bonus +3 untuk RR>=2.5 sekarang clamp ke maksimum
    (weights["rr"] * 100) — sebelumnya bisa overflow saat BULLISH.
    Dengan fix ini, max base_score dijamin <= 100.0 di semua regime.
    """
    weights = get_adaptive_weights(regime)

    prob_score    = (max(0, min(100, prob)) / 100) * (weights["prob"] * 100)
    runner_score  = (max(0, min(100, runner)) / 100) * (weights["runner"] * 100)
    quality_map   = {"WEAK": 3, "HEALTHY": 10, "STRONG": 15}
    quality_score = quality_map.get(quality, 0) * (weights["quality"] / 0.15)

    # RR base score — dijamin <= weights["rr"] * 100
    rr_max  = weights["rr"] * 100
    rr_base = (max(0, min(4.0, rr)) / 4.0) * rr_max
    if rr >= 2.5:
        rr_base = min(rr_max, rr_base + 3)   # bonus, tapi tetap di-cap di rr_max
    rr_score = rr_base

    liq_score  = weights["liquidity"] * 100 if "OK" in str(liquidity) else 0
    bandar_pts = (max(0, min(4, bandar_score)) / 4) * (weights["bandar"] * 100)

    total = prob_score + runner_score + quality_score + rr_score + liq_score + bandar_pts
    # Safety cap: dengan adaptive weights, max teoretis bisa sedikit > 100
    # karena quality_map.STRONG=15 di-scale dengan multiplier (W/0.15).
    # Cap di 100 untuk konsistensi distribusi score.
    return round(min(100.0, total), 2)



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
    st.session_state.cybernetic_params = _state.get("cybernetic_params", ...)
    st.session_state.signal_lock       = _state.get("signal_lock", {})
    st.session_state.balance           = _state.get("balance", 800_000)
    st.session_state.last_regime       = _state.get("last_regime", "-")  # ← ini yang ditambah
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
if "heatmap_data"       not in st.session_state: st.session_state.heatmap_data       = None
if "intraday_info"      not in st.session_state: st.session_state.intraday_info      = {}
TOP_N_RESULTS = 5   # Fixed: selalu tampilkan 5 kandidat terbaik siap eksekusi

# ============================================================
# IMPORTS ENGINE & CONFIG  [F4] dead imports dihapus
# ============================================================
from engine.probability_engine    import runner_probability
from engine.runner_engine         import runner_prediction
from engine.pullback_quality_engine import pullback_quality
from engine.sector_engine         import sector_momentum
from engine.liquidity_engine      import liquidity_trap, fake_breakout
from engine.regime_engine         import detect_market_regime
from config.universe              import ISSI_UNIVERSE, SECTOR_MAP, get_sector

# ============================================================
# LOAD MARKET DATA
# ============================================================
# Volume minimum harian dalam rupiah — saham di bawah ini terlalu sepi
# untuk swing trading (spread lebar, sulit keluar posisi)
MIN_DAILY_VOLUME_IDR = 500_000_000   # Rp 500 juta/hari

@st.cache_data(ttl=300)
def load_market() -> dict[str, pd.DataFrame]:
    """
    [Task 6] Batch download yfinance — 30 ticker per batch dengan retry.
    Mengurangi missing ticker seperti TKIM/INKP yang sebelumnya gagal fetch
    saat seluruh universe didownload sekaligus.
    """
    BATCH_SIZE      = 30
    MAX_RETRIES     = 2
    market: dict[str, pd.DataFrame] = {}
    failed_tickers: list[str] = []

    universe = list(ISSI_UNIVERSE)
    n_batches = (len(universe) + BATCH_SIZE - 1) // BATCH_SIZE

    LOG.info(f"load_market START: {len(universe)} ticker dalam {n_batches} batch")

    for batch_idx in range(n_batches):
        batch = universe[batch_idx * BATCH_SIZE : (batch_idx + 1) * BATCH_SIZE]

        last_err = None
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
                last_err = e
                LOG.warning(f"batch {batch_idx+1}/{n_batches} attempt {attempt+1} error: {type(e).__name__}: {str(e)[:120]}")
                time.sleep(1.0 * (attempt + 1))

        if raw is None or raw.empty:
            LOG.error(f"batch {batch_idx+1}/{n_batches} GAGAL setelah {MAX_RETRIES+1} percobaan: {last_err}")
            failed_tickers.extend(batch)
            continue

        # Parse setiap ticker di batch ini
        for s in batch:
            try:
                # Kalau batch hanya 1 ticker, struktur df berbeda
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
                avg_vol_20    = float(df["Volume"].squeeze().tail(20).mean())
                est_daily_idr = last_close * avg_vol_20 * 100
                if est_daily_idr < MIN_DAILY_VOLUME_IDR:
                    continue   # likuiditas terlalu rendah, bukan failure
                market[s] = df
            except Exception as e:
                failed_tickers.append(s)
                LOG.warning(f"parse {s} gagal: {type(e).__name__}: {str(e)[:80]}")

    n_loaded = len(market)
    n_failed = len(failed_tickers)
    LOG.info(f"load_market DONE: {n_loaded} ticker loaded, {n_failed} gagal")
    if failed_tickers:
        LOG.warning(f"Ticker gagal fetch: {', '.join(failed_tickers[:20])}{'...' if len(failed_tickers)>20 else ''}")

    return market


# ============================================================
# INJECT TODAY INTRADAY — Fix Fatal #1
# Update baris terakhir daily data dengan harga AKTUAL hari ini
# sehingga semua kalkulasi (RSI, breakout, bandar, change%)
# menggunakan data real bukan closing kemarin
# ============================================================
@st.cache_data(ttl=60)   # cache 1 menit — intraday harus fresh
def _fetch_today_intraday_raw(tickers_tuple: tuple) -> dict:
    """
    Download data intraday hari ini untuk semua ticker sekaligus.
    Return dict: {ticker_jk: {O, H, L, C, V}} atau {} jika gagal.
    Menggunakan tuple sebagai argument agar bisa di-cache oleh st.cache_data.
    """
    result = {}
    if not is_trading_day():
        return result   # Bukan hari bursa, skip

    try:
        raw = yf.download(
            tickers=list(tickers_tuple),
            period="1d",
            interval="5m",
            group_by="ticker",
            progress=False,
            auto_adjust=True,
        )
        if raw is None or raw.empty:
            return result

        for tkr in tickers_tuple:
            try:
                if len(tickers_tuple) == 1:
                    df5 = raw.dropna()
                else:
                    df5 = raw[tkr].dropna()

                if df5 is None or len(df5) < 3:
                    continue

                close5  = df5["Close"].squeeze()
                high5   = df5["High"].squeeze()
                low5    = df5["Low"].squeeze()
                vol5    = df5["Volume"].squeeze()
                open5   = df5["Open"].squeeze()

                # Ringkasan OHLCV hari ini
                result[tkr] = {
                    "Open":   float(open5.iloc[0]),
                    "High":   float(high5.max()),
                    "Low":    float(low5.min()),
                    "Close":  float(close5.iloc[-1]),
                    "Volume": float(vol5.sum()),
                    "n_bars": len(df5),
                    "last_time": str(close5.index[-1]),
                }
            except Exception:
                continue
    except Exception:
        pass

    return result


def inject_today_intraday(market: dict) -> tuple[dict, dict]:
    """
    Update baris terakhir setiap DataFrame di market dengan
    data intraday aktual hari ini.

    Return:
        updated_market : dict dengan data terbaru
        intraday_info  : dict info update per ticker untuk debug
    """
    if not is_market_open() and not is_trading_day():
        return market, {}

    tickers_tuple = tuple(sorted(market.keys()))
    today_data    = _fetch_today_intraday_raw(tickers_tuple)

    if not today_data:
        return market, {}

    updated_market = {}
    intraday_info  = {}
    today_date     = datetime.now(WIB).date()

    for ticker, df in market.items():
        if ticker not in today_data:
            updated_market[ticker] = df
            continue

        td = today_data[ticker]
        try:
            # Cek apakah baris terakhir daily sudah hari ini
            last_date = pd.to_datetime(df.index[-1]).date()

            new_row = pd.DataFrame({
                "Open":   [td["Open"]],
                "High":   [td["High"]],
                "Low":    [td["Low"]],
                "Close":  [td["Close"]],
                "Volume": [td["Volume"]],
            }, index=[pd.Timestamp(today_date)])

            if last_date == today_date:
                # Update baris terakhir (hari ini) dengan data intraday terkini
                df_updated = df.copy()
                df_updated.iloc[-1] = new_row.iloc[0]
                intraday_info[ticker] = {
                    "status":    "updated",
                    "close":     td["Close"],
                    "n_bars_5m": td["n_bars"],
                    "last_time": td["last_time"],
                }
            else:
                # Append baris baru untuk hari ini
                df_updated = pd.concat([df, new_row])
                intraday_info[ticker] = {
                    "status":    "appended",
                    "close":     td["Close"],
                    "n_bars_5m": td["n_bars"],
                    "last_time": td["last_time"],
                }

            updated_market[ticker] = df_updated

        except Exception:
            updated_market[ticker] = df

    n_updated = sum(1 for v in intraday_info.values() if v.get("status") in ("updated", "appended"))
    return updated_market, intraday_info

# ============================================================
# MARKET HEATMAP — Treemap visual kondisi semua saham ISSI
# ============================================================
def build_heatmap_data(market: dict) -> pd.DataFrame:
    """
    Bangun dataframe untuk treemap heatmap.
    Size = estimasi nilai transaksi harian (harga × volume × 100 lot)
    Color = % change harga hari ini
    """
    rows = []
    for ticker, df in market.items():
        if ticker not in ISSI_UNIVERSE:
            continue
        try:
            close      = df["Close"].squeeze()
            volume     = df["Volume"].squeeze()
            tkr_clean  = ticker.replace(".JK", "")
            sector     = get_sector(ticker)
            chg        = daily_change_pct(df)
            last_price = float(close.iloc[-1])
            avg_vol    = float(volume.tail(20).mean())

            # Nilai transaksi harian dalam miliar IDR (sebagai ukuran kotak)
            size_val = max((last_price * avg_vol * 100) / 1_000_000_000, 0.1)

            # Label yang tampil di dalam kotak
            label = f"{tkr_clean}  {chg:+.2f}%"

            rows.append({
                "Sektor":   sector,
                "Ticker":   tkr_clean,
                "Label":    label,
                "Change%":  round(chg, 2),
                "Size":     round(size_val, 4),
            })
        except Exception:
            continue

    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ============================================================
# [P1] TECHNICAL CONTEXT BUILDER — untuk Telegram enriched
# ============================================================
def build_technical_context(df: pd.DataFrame) -> dict:
    """Ringkasan teknikal cepat untuk Telegram alert."""
    try:
        close     = df["Close"].squeeze()
        high      = df["High"].squeeze()
        low       = df["Low"].squeeze()
        volume    = df["Volume"].squeeze()
        last      = float(close.iloc[-1])
        ema20     = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50     = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
        ema12     = close.ewm(span=12, adjust=False).mean()
        ema26     = close.ewm(span=26, adjust=False).mean()
        macd_h    = float((ema12 - ema26 - (ema12 - ema26).ewm(span=9, adjust=False).mean()).iloc[-1])
        bb_mid    = float(close.tail(20).mean())
        bb_std    = float(close.tail(20).std())
        bb_pos    = (last - bb_mid) / (2 * bb_std) * 100 if bb_std > 0 else 0
        rsi       = calculate_rsi(df)
        avg_vol   = float(volume.tail(20).mean())
        vol_ratio = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1.0
        resistance= float(high.tail(20).max())
        support   = float(low.tail(20).min())
        dist_to_r = (resistance - last) / last * 100 if last > 0 else 0

        # Alignment: berapa indikator bullish dari 6
        alignment = sum([
            last > ema20, last > ema50, ema20 > ema50,
            macd_h > 0, 42 <= rsi <= 72, vol_ratio >= 1.0,
        ])
        return {
            "rsi":        round(rsi, 1),
            "macd_dir":   "↑ Bullish" if macd_h > 0 else "↓ Bearish",
            "bb_zone":    "Atas" if bb_pos > 33 else ("Bawah" if bb_pos < -33 else "Tengah"),
            "vol_ratio":  round(vol_ratio, 1),
            "ema_trend":  "Golden ✅" if ema20 > ema50 else "Death ⚠️",
            "alignment":  alignment,
            "resistance": resistance,
            "support":    support,
            "dist_to_r":  round(dist_to_r, 1),
            "ok":         True,
        }
    except Exception:
        return {"ok": False}


def notify_regime_change(prev: str, curr: str) -> None:
    """
    Kirim Telegram alert jika regime pasar berubah.
    Dipanggil dari run_scanner() dan auto_scan_background().
    Guard: tidak kirim jika prev kosong/belum ada, atau sama dengan curr.
    """
    if not prev or prev in ("-", "") or prev == curr:
        return

    emoji_map = {
        "BULLISH":      "🟢",
        "SIDEWAYS":     "🟡",
        "DISTRIBUTION": "🔴",
        "VOLATILE":     "⚡",
    }
    action_map = {
        "BULLISH":      "Scanner aktif — setup valid mulai dicari. Perketat position sizing.",
        "SIDEWAYS":     "Filter moderat — selektif, tunggu konfirmasi sebelum entry.",
        "DISTRIBUTION": "Market distribusi — scanner defensif. Sinyal ditekan, hindari buy baru.",
        "VOLATILE":     "Volatilitas tinggi — position sizing sangat ketat, SL wajib.",
    }

    e_prev   = emoji_map.get(prev, "⬜")
    e_curr   = emoji_map.get(curr, "⬜")
    guidance = action_map.get(curr, "Monitor kondisi market.")
    ts       = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")

    msg = (
        f"🔄 *REGIME CHANGE*\n"
        f"{'─' * 28}\n"
        f"{e_prev} {prev}  →  {e_curr} {curr}\n"
        f"⏰ {ts}\n\n"
        f"📌 {guidance}"
    )
    send_telegram(msg)
    LOG.info(f"REGIME CHANGE: {prev} → {curr}")


def format_telegram_signal(row: dict, regime: str, market: dict) -> str:
    """
    [P1] Build Telegram message yang kaya konteks teknikal.
    Enriched dengan ringkasan dari build_technical_context.
    """
    tkr    = row.get("Ticker", "-")
    action = row.get("Action", "-")
    is_now = "NOW" in action

    # Ambil teknikal context
    ticker_jk = tkr + ".JK"
    tech      = {}
    if ticker_jk in market or tkr + ".JK" in market:
        df_t = market.get(ticker_jk, market.get(tkr + ".JK"))
        if df_t is not None:
            tech = build_technical_context(df_t)

    # Header
    header = "🔥 EXECUTE NOW" if is_now else "✅ EXECUTE"

    # Base info
    base = (
        f"{header} — ATS V{APP_VERSION}\n"
        f"{'━'*30}\n"
        f"📌 {tkr}  |  {row.get('Sector', '-')}\n"
        f"⏰ {datetime.now(WIB).strftime('%H:%M WIB')}  |  Regime: {regime}\n\n"
        f"📊 ATS SIGNAL\n"
        f"Score      : {row.get('Score', 0):.1f}/100\n"
        f"RR         : {row.get('RR', 0):.1f}x\n"
        f"Confluence : {row.get('Confluence', 0)}/6\n"
        f"Change     : {row.get('Change%', 0):+.2f}%\n"
        f"Breakout   : {row.get('Breakout', '-')}\n\n"
        f"💰 LEVEL TRADING\n"
        f"Entry  : {row.get('Entry', '-')}\n"
        f"SL     : {row.get('SL', '-')}\n"
        f"Target : {row.get('Target', '-')}\n"
        f"Lot    : {row.get('Lot', '-')}\n"
    )

    # Tambahkan context teknikal jika tersedia
    if tech.get("ok"):
        alignment_bar = "█" * tech["alignment"] + "░" * (6 - tech["alignment"])
        tech_section = (
            f"\n📈 TEKNIKAL CONTEXT\n"
            f"RSI        : {tech['rsi']} "
            f"{'⚠️OB' if tech['rsi'] > 70 else ('⚠️OS' if tech['rsi'] < 30 else '✅')}\n"
            f"MACD       : {tech['macd_dir']}\n"
            f"Bollinger  : Zona {tech['bb_zone']}\n"
            f"EMA Trend  : {tech['ema_trend']}\n"
            f"Volume     : {tech['vol_ratio']:.1f}x rata-rata\n"
            f"Alignment  : [{alignment_bar}] {tech['alignment']}/6\n"
            f"Jarak ke R : {tech['dist_to_r']:.1f}%\n"
        )
    else:
        tech_section = ""

    # Footer
    footer = (
        f"\n{'━'*30}\n"
        f"{'⚡ LANGSUNG EKSEKUSI' if is_now else '✅ KONFIRMASI CHART DULU'}\n"
        f"⚠️ Pasang SL. No FOMO. Disiplin.\n"
        f"🔬 Deep Analysis: buka tab dashboard"
    )

    return base + tech_section + footer


def format_telegram_signal_bg(row: dict, regime: str) -> str:
    """
    [P1] Versi background thread — tanpa market dict (tidak ada teknikal context).
    Dipakai oleh auto_scan_background.
    """
    tkr    = row.get("Ticker", "-")
    action = row.get("Action", "-")
    is_now = "NOW" in action
    header = "🔥 EXECUTE NOW" if is_now else "✅ EXECUTE"

    return (
        f"{header} — ATS V{APP_VERSION}\n"
        f"{'━'*30}\n"
        f"📌 {tkr}  |  {row.get('Sector', '-')}\n"
        f"⏰ {datetime.now(WIB).strftime('%H:%M WIB')}  |  Regime: {regime}\n\n"
        f"📊 ATS SIGNAL\n"
        f"Score      : {row.get('Score', 0):.1f}/100\n"
        f"RR         : {row.get('RR', 0):.1f}x\n"
        f"Confluence : {row.get('Confluence', 0)}/6\n"
        f"Change     : {row.get('Change%', 0):+.2f}%\n"
        f"Breakout   : {row.get('Breakout', '-')}\n"
        f"RSI        : {row.get('RSI', 0):.1f}\n\n"
        f"💰 LEVEL TRADING\n"
        f"Entry  : {row.get('Entry', '-')}\n"
        f"SL     : {row.get('SL', '-')}\n"
        f"Target : {row.get('Target', '-')}\n"
        f"Lot    : {row.get('Lot', '-')}\n"
        f"{'━'*30}\n"
        f"{'⚡ LANGSUNG EKSEKUSI' if is_now else '✅ KONFIRMASI CHART DULU'}\n"
        f"⚠️ Pasang SL. No FOMO."
    )


def scan_core(market: dict, balance: float, top_n: int = 5,
              show_progress: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, dict, str, pd.DataFrame]:
    """
    Inti scanner V5.1 — semua engine terintegrasi.
    Return: (scan_df, debug_df, thresholds, regime, sector_df)
    """
    regime       = detect_market_regime(market)
    sector_power = sector_momentum(market, SECTOR_MAP)
    sector_df    = pd.DataFrame(
        [{"Sector": k, "Strength": round(v, 2)} for k, v in sector_power.items()]
    ).sort_values("Strength", ascending=False)

    # ── P4: Adaptive weights berdasarkan regime ──────────────
    ada_weights = get_adaptive_weights(regime)

    # Buat sector strength lookup
    sector_strength_map = {
        row["Sector"]: row["Strength"]
        for _, row in sector_df.iterrows()
    }

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

            # Filter 1: Sektor — SOFT PENALTY (bukan hard filter)
            # Sektor lemah dapat penalty score, tapi tidak langsung dibuang
            # Hanya skip jika sektor SANGAT lemah (strength < -0.05)
            sec_strength = sector_strength_map.get(sector, 0.0)
            if sec_strength < -0.05:
                debug_log.append({"Ticker": tkr_clean, "Sector": sector,
                    "RSI": "-", "EMA_OK": "-", "Bandar": "-", "Breakout": "-",
                    "Confluence": "-", "RR": "-", "Score": "-",
                    "❌ Gugur di": f"Sektor sangat lemah (strength={sec_strength:.2f} < -0.05)"})
                continue

            # Hitung sector penalty untuk scoring nanti
            # Sektor positif: 0 penalty
            # Sektor netral (0 sampai -0.05): -5 poin
            # Sektor kuat (>0.03): +3 bonus
            if sec_strength > 0.03:
                sector_score_adj = 3.0    # bonus sektor kuat
            elif sec_strength > 0:
                sector_score_adj = 0.0    # sektor positif, netral
            elif sec_strength >= -0.05:
                sector_score_adj = -5.0   # sektor lemah, penalty ringan
            else:
                sector_score_adj = -10.0  # tidak akan sampai sini (sudah di-skip atas)

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

            # Entry freshness — batas bergerak tergantung kekuatan breakout:
            # VALID breakout  → boleh sampai +7% (momentum kuat, masih layak masuk)
            # WEAK breakout   → boleh sampai +5%
            # Tanpa breakout  → maksimal +3% (entry sudah terlambat)
            # [V5.5.3 FIX B] WAIT freshness 3.0 → 4.5 berdasarkan analisis CSV debug:
            # 8 saham momentum kuat (TINS+6.4%, CPIN+4.2%, INDF+3.3%, dll) gugur padahal
            # masih dalam zona reasonable entry. 4.5% adalah balance antara FOMO protection
            # dan tidak melewatkan momentum gradual. Tetap < WEAK (5.0%) by design.
            strong_daily_momentum = momentum == 2 or ft == 2
            freshness_limit = (
                9.0 if breakout == "VALID" and strong_daily_momentum else
                7.0 if breakout == "VALID" else
                6.0 if breakout == "WEAK" and strong_daily_momentum else
                5.0 if breakout == "WEAK" else
                4.5   # WAIT: 3.0 → 4.5
            )
            if chg_pct > freshness_limit:
                debug_log.append({"Ticker": tkr_clean, "Sector": sector,
                    "RSI": round(rsi_value, 1), "EMA_OK": "✅" if ema_ok else "❌",
                    "Bandar": "-", "Breakout": breakout,
                    "Confluence": "-", "RR": round(rr, 1), "Score": "-",
                    "❌ Gugur di": f"Entry expired: naik {chg_pct:.1f}% (batas {freshness_limit:.1f}% untuk breakout {breakout})"})
                continue

            # Filter 3: Breakout gate — [V5.6.3] Bandar dipisah dari hard gate.
            # Bandar tetap masuk confluence (1/6) + scoring, tapi bukan blocker mandatory.
            # Root cause: bandar & breakout berkorelasi tinggi → dual hard gate → 0 signal
            # selama pasar konsolidasi/akumulasi. Fix: hanya breakout yg jadi hard gate.
            if breakout == "WAIT":
                debug_log.append({"Ticker": tkr_clean, "Sector": sector,
                    "RSI": round(rsi_value, 1), "EMA_OK": "✅" if ema_ok else "❌",
                    "Bandar": bandar, "Breakout": breakout,
                    "Confluence": "-", "RR": round(rr, 1), "Score": "-",
                    "❌ Gugur di": "Breakout WAIT"})
                continue

            intraday = intraday_confirm(ticker)
            prob     = runner_probability(df)
            runner   = runner_prediction(df)
            quality  = pullback_quality(df)
            liq_raw  = liquidity_trap(df)
            fake_bo  = fake_breakout(df)
            # [FIX #3] Normalize return value — defensive coding terhadap engine eksternal
            is_liq_trap = is_trap_signal(liq_raw)
            is_fake_bo  = is_trap_signal(fake_bo)
            liq_str     = "🔴 TRAP" if is_liq_trap else "🟢 OK"

            if is_liq_trap or is_fake_bo:
                reason = "Liquidity trap" if is_liq_trap else "Fake breakout"
                debug_log.append({"Ticker": tkr_clean, "Sector": sector,
                    "RSI": round(rsi_value, 1), "EMA_OK": "✅" if ema_ok else "❌",
                    "Bandar": bandar, "Breakout": breakout,
                    "Confluence": "-", "RR": round(rr, 1), "Score": "-",
                    "❌ Gugur di": reason})
                continue

            if breakout == "WEAK" and not (momentum == 2 or intraday >= 2 or ft == 2):
                debug_log.append({"Ticker": tkr_clean, "Sector": sector,
                    "RSI": round(rsi_value, 1), "EMA_OK": "✅" if ema_ok else "❌",
                    "Bandar": bandar, "Breakout": breakout,
                    "Confluence": "-", "RR": round(rr, 1), "Score": "-",
                    "❌ Gugur di": "WEAK breakout tanpa momentum/intraday/follow-through kuat"})
                continue

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

            # Filter 6: Slow Mover Detection — perketat untuk saham seperti SCCO, DMAS
            # Multi-criteria: ATR% + average daily move + range konsistensi
            atr_pct      = (atr / entry * 100) if entry > 0 else 0

            # Hitung average daily move 20 hari (dari closing changes)
            close_series = df["Close"].squeeze()
            daily_changes = close_series.pct_change().tail(20).abs() * 100
            avg_daily_move = float(daily_changes.mean())   # rata-rata % gerakan harian
            days_active   = int((daily_changes > 1.0).sum())  # berapa hari yang gerak >1%

            # Threshold yang lebih ketat
            MIN_ATR_PCT       = 2.0   # naik dari 1.5 → 2.0 (SCCO 2.54% akan lolos tapi DMAS gugur)
            MIN_AVG_DAILY     = 1.2   # rata-rata harus bergerak min 1.2% per hari
            MIN_ACTIVE_DAYS   = 8     # minimal 8 dari 20 hari harus bergerak >1%

            slow_mover_reasons = []
            if atr_pct < MIN_ATR_PCT:
                slow_mover_reasons.append(f"ATR {atr_pct:.2f}% < {MIN_ATR_PCT}%")
            if avg_daily_move < MIN_AVG_DAILY:
                slow_mover_reasons.append(f"avg daily {avg_daily_move:.2f}% < {MIN_AVG_DAILY}%")
            if days_active < MIN_ACTIVE_DAYS:
                slow_mover_reasons.append(f"hanya {days_active}/20 hari aktif")

            if slow_mover_reasons:
                debug_log.append({"Ticker": tkr_clean, "Sector": sector,
                    "RSI": round(rsi_value, 1), "EMA_OK": "✅" if ema_ok else "❌",
                    "Bandar": bandar, "Breakout": breakout,
                    "Confluence": f"{conf_count}/6", "RR": round(rr, 1), "Score": "-",
                    "❌ Gugur di": (
                        f"Saham slow mover ({', '.join(slow_mover_reasons)}) "
                        f"— modal bisa stuck"
                    )})
                continue

            # ── SCORING V5.1 — P4 Adaptive + P6 Starvation ─
            # P4: Adaptive score dengan regime weights
            base_score = calculate_score(prob, runner, quality, rr, liq_str, bandar, regime)

            # Adaptive bonus multipliers per regime
            momentum_bonus = momentum * 0.8 * ada_weights["momentum_w"]
            accum_bonus    = accum    * 0.9 * ada_weights["accum_w"]
            ft_bonus       = ft       * 0.7 * ada_weights["ft_w"]
            intra_bonus    = intraday * 0.5 * ada_weights["intraday_w"]

            extra_bonus = 0.0
            if momentum == 2:               extra_bonus += 1
            if ft == 2:                     extra_bonus += 1
            if last_price > ema_val * 1.01: extra_bonus += 1

            score = (base_score + momentum_bonus + accum_bonus +
                     ft_bonus + intra_bonus + extra_bonus + sector_score_adj)
            score = min(100.0, max(0.0, score))

            # [Task 2] Score breakdown untuk explainability
            score_breakdown = {
                "base":      round(base_score, 1),
                "momentum":  round(momentum_bonus, 1),
                "accum":     round(accum_bonus, 1),
                "ft":        round(ft_bonus, 1),
                "intraday":  round(intra_bonus, 1),
                "extra":     round(extra_bonus, 1),
                "sector":    round(sector_score_adj, 1),
                "final":     round(score, 1),
            }

            # Info sektor untuk debug
            sector_note = "✅" if sec_strength > 0 else f"⚠️ adj{sector_score_adj:+.0f}"

            # [Task 1] Log scan event lolos
            log_scan_event(
                ticker=tkr_clean, status="LOLOS",
                score=score, regime=regime, rr=rr, conf=conf_count,
                extra={
                    "sector": sector,
                    "breakout": breakout,
                    "bandar": bandar,
                    "rsi": round(rsi_value, 1),
                    "atr_pct": round(atr_pct, 2),
                    "chg": round(chg_pct, 2),
                }
            )

            debug_log.append({"Ticker": tkr_clean, "Sector": sector,
                "RSI": round(rsi_value, 1), "EMA_OK": "✅" if ema_ok else "❌",
                "Bandar": bandar, "Breakout": breakout,
                "Confluence": f"{conf_count}/6", "RR": round(rr, 1),
                "Score": round(score, 1),
                "❌ Gugur di": f"✅ LOLOS — masuk kandidat ({sector_note} sektor)"})

            candidates.append({
                "BUY": False, "Ticker": tkr_clean, "Sector": sector,
                "Action": "", "Score": round(score, 2),
                "Probability": int(prob), "RunnerScore": int(runner),
                "PullbackQuality": quality, "Liquidity": liq_str,
                "RSI": round(rsi_value, 1), "RR": round(rr, 1),
                "Change%": chg_pct,
                "ATR%": round(atr_pct, 2),
                "Momentum": momentum, "Accumulation": accum,
                "BandarScore": bandar, "Breakout": breakout,
                "FT": ft, "INTRA": intraday, "Confluence": conf_count,
                "Entry": idr(entry), "SL": idr(sl), "Target": idr(target),
                "Lot": lot, "Timing": timing, "ATR": round(atr, 0),
                "EMA50": round(ema_val, 0),
                "ScoreBreakdown": score_breakdown,   # [Task 2]
            })

        except Exception as e:
            debug_log.append({"Ticker": ticker.replace(".JK", ""),
                "Sector": get_sector(ticker), "RSI": "-", "EMA_OK": "-",
                "Bandar": "-", "Breakout": "-", "Confluence": "-", "RR": "-",
                "Score": "-", "❌ Gugur di": f"⚠️ Exception: {str(e)[:60]}"})

    if prog:
        prog.empty()

    if not candidates:
        # P6: Opportunity starvation check
        check_opportunity_starvation(debug_log, total)
        return pd.DataFrame(), pd.DataFrame(debug_log), {}, regime, sector_df

    # P6: Overfitting control
    starvation_info = check_opportunity_starvation(debug_log, total)

    thresholds = get_dynamic_thresholds([c["Score"] for c in candidates])

    # P6: Kalau starvation, longgarkan threshold
    if starvation_info.get("loosen") and starvation_info.get("status") == "STARVATION":
        thresholds["execute_now"]    = max(thresholds["execute_now"] * 0.90, 70)
        thresholds["execute"]        = max(thresholds["execute"]     * 0.90, 60)
        thresholds["ready"]          = max(thresholds["ready"]       * 0.90, 50)
        thresholds["loosen_applied"] = True

    thresholds["starvation"] = starvation_info

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
# [V5.6] AUTO SCAN LOG — backup otomatis hasil scan ke disk
# ============================================================
SCAN_LOG_DIR = "scan_logs"

def save_scan_log(scan_df: pd.DataFrame, debug_df: pd.DataFrame,
                  regime: str, scan_label: str = "manual") -> dict:
    """
    Auto-save hasil scan ke folder scan_logs/YYYY-MM-DD/.
    Disimpan: full debug log, summary alasan gugur, kandidat (jika ada).

    Args:
        scan_df:    DataFrame kandidat yang lolos
        debug_df:   DataFrame full debug log
        regime:     Market regime saat scan
        scan_label: Label scan (e.g. "Pre-Open", "manual", "Mid Sesi 1")

    Returns:
        dict info file yang berhasil disimpan
    """
    result = {"saved": [], "errors": [], "dir": None}
    try:
        now    = datetime.now(WIB)
        date_dir = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H-%M")
        clean_label = scan_label.replace(" ", "_").replace("/", "_").lower()
        regime_str  = regime.upper() if regime else "UNKNOWN"

        # Buat folder kalau belum ada
        full_dir = os.path.join(SCAN_LOG_DIR, date_dir)
        os.makedirs(full_dir, exist_ok=True)
        result["dir"] = full_dir

        base_name = f"{time_str}_{clean_label}_{regime_str}"

        # 1. Full debug log
        if debug_df is not None and not debug_df.empty:
            f1 = os.path.join(full_dir, f"{base_name}_debug.csv")
            debug_df.to_csv(f1, index=False)
            result["saved"].append(f1)

            # 2. Summary alasan gugur
            try:
                gugur_only = debug_df[debug_df["❌ Gugur di"] != "✅ LOLOS — masuk kandidat"]
                if not gugur_only.empty:
                    summary = (gugur_only["❌ Gugur di"]
                              .str.extract(r"^([^(|]+)")[0]
                              .str.strip()
                              .value_counts()
                              .reset_index())
                    summary.columns = ["Alasan_Gugur", "Jumlah_Ticker"]
                    f2 = os.path.join(full_dir, f"{base_name}_summary.csv")
                    summary.to_csv(f2, index=False)
                    result["saved"].append(f2)
            except Exception as e:
                result["errors"].append(f"summary: {e}")

        # 3. Kandidat yang lolos (kalau ada)
        if scan_df is not None and not scan_df.empty:
            f3 = os.path.join(full_dir, f"{base_name}_candidates.csv")
            # Hilangkan kolom ScoreBreakdown (tidak compatible dengan CSV langsung)
            df_save = scan_df.copy()
            if "ScoreBreakdown" in df_save.columns:
                df_save = df_save.drop(columns=["ScoreBreakdown"])
            df_save.to_csv(f3, index=False)
            result["saved"].append(f3)

        LOG.info(f"save_scan_log OK — {len(result['saved'])} file → {full_dir}")
        return result

    except Exception as e:
        LOG.error(f"save_scan_log FAILED: {type(e).__name__}: {e}")
        result["errors"].append(str(e))
        return result


def cleanup_old_scan_logs(days_to_keep: int = 30):
    """
    Hapus folder scan_logs/YYYY-MM-DD/ yang lebih dari N hari.
    Dipanggil sekali per hari untuk hemat disk.
    """
    try:
        if not os.path.isdir(SCAN_LOG_DIR):
            return
        cutoff = (datetime.now(WIB).date() - timedelta(days=days_to_keep))
        deleted = 0
        for entry in os.listdir(SCAN_LOG_DIR):
            full_path = os.path.join(SCAN_LOG_DIR, entry)
            if not os.path.isdir(full_path):
                continue
            try:
                folder_date = datetime.strptime(entry, "%Y-%m-%d").date()
                if folder_date < cutoff:
                    import shutil
                    shutil.rmtree(full_path)
                    deleted += 1
            except (ValueError, OSError):
                continue
        if deleted > 0:
            LOG.info(f"cleanup_old_scan_logs: {deleted} folder lama dihapus (> {days_to_keep} hari)")
    except Exception as e:
        LOG.warning(f"cleanup_old_scan_logs error: {e}")


def list_scan_log_dates() -> list:
    """Return list of dates yang punya scan log, sorted descending."""
    if not os.path.isdir(SCAN_LOG_DIR):
        return []
    dates = []
    for entry in os.listdir(SCAN_LOG_DIR):
        full_path = os.path.join(SCAN_LOG_DIR, entry)
        if os.path.isdir(full_path):
            try:
                datetime.strptime(entry, "%Y-%m-%d")
                dates.append(entry)
            except ValueError:
                continue
    return sorted(dates, reverse=True)


def get_scan_log_files(date_str: str) -> list:
    """Return list of files dalam folder scan_logs/date_str/."""
    full_dir = os.path.join(SCAN_LOG_DIR, date_str)
    if not os.path.isdir(full_dir):
        return []
    return sorted([f for f in os.listdir(full_dir) if f.endswith('.csv')])


def create_zip_for_date(date_str: str) -> bytes | None:
    """Bundle semua CSV di folder tanggal tertentu jadi ZIP bytes."""
    import io
    import zipfile

    full_dir = os.path.join(SCAN_LOG_DIR, date_str)
    if not os.path.isdir(full_dir):
        return None
    files = [f for f in os.listdir(full_dir) if f.endswith('.csv')]
    if not files:
        return None

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            full_path = os.path.join(full_dir, f)
            zf.write(full_path, arcname=f)
    return buf.getvalue()

# ============================================================
# RUN SCANNER (UI) — [F1] pakai scan_core
# ============================================================
def run_scanner():
    LOG.info("=" * 60)
    LOG.info(f"SCAN MANUAL START | {datetime.now(WIB).strftime('%Y-%m-%d %H:%M:%S WIB')}")
    market = load_market()
    if not market:
        LOG.error("SCAN ABORT: gagal load market data")
        st.error("Gagal memuat data market. Cek koneksi internet.")
        return

    # ── Inject data intraday hari ini ────────────────────────
    if is_trading_day():
        with st.spinner("🔄 Mengupdate harga dengan data intraday hari ini..."):
            market, intra_info = inject_today_intraday(market)
        n_upd = sum(1 for v in intra_info.values() if v.get("status") in ("updated","appended"))
        if n_upd > 0:
            wib_time = datetime.now(WIB).strftime("%H:%M WIB")
            st.caption(f"✅ Data diperbarui dengan harga intraday terkini — {n_upd} ticker | {wib_time}")
        else:
            st.caption("ℹ️ Data intraday tidak tersedia — menggunakan closing kemarin")

    cybernetic_feedback_engine(st.session_state.journal,
                               st.session_state.get("last_regime", "-"))

    # [K6] scan_core handle sector_momentum sekaligus — tidak duplikat
    prev_regime = st.session_state.get("last_regime", "-")
    scan_df, debug_df, thresholds, regime, sector_df = scan_core(
        market, st.session_state.balance,
        top_n=TOP_N_RESULTS, show_progress=True
    )
    notify_regime_change(prev_regime, regime)   # alert jika regime berubah

    st.session_state.last_regime        = regime
    st.session_state.dynamic_thresholds = thresholds
    st.session_state.debug_log          = debug_df.to_dict("records") if not debug_df.empty else []
    st.session_state.scan_result        = scan_df
    st.session_state.sector_table       = sector_df
    st.session_state.intraday_info      = intra_info if is_trading_day() else {}

    # Build heatmap dari market yang sudah diupdate intraday
    st.session_state.heatmap_data = build_heatmap_data(market)

    # [V5.6] Auto-save scan log ke disk
    try:
        save_scan_log(scan_df, debug_df, regime, scan_label="manual")
    except Exception as e:
        LOG.warning(f"auto-save scan log gagal (manual): {e}")

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
        msg = format_telegram_signal(row, regime, market)   # [P1] enriched
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
    LOG.info("=" * 60)
    LOG.info(f"AUTOSCAN START | {datetime.now(WIB).strftime('%Y-%m-%d %H:%M:%S WIB')}")
    if not is_market_open():
        LOG.info("AUTOSCAN SKIP: market tutup")
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
                last_close = float(df["Close"].squeeze().iloc[-1])
                if last_close <= 0: continue
                if df["Volume"].squeeze().tail(5).mean() <= 0: continue
                # [FIX #2] Sync dengan load_market(): filter likuiditas min Rp 500 juta/hari
                # Tanpa ini, saham illiquid bisa lolos auto-scan tapi tidak lolos manual scan
                avg_vol_20    = float(df["Volume"].squeeze().tail(20).mean())
                est_daily_idr = last_close * avg_vol_20 * 100
                if est_daily_idr < MIN_DAILY_VOLUME_IDR:
                    continue
                market[s] = df
            except Exception:
                continue

        if not market:
            send_telegram("⚠️ ATS AutoScan: Gagal load market data.")
            return

        # ── Inject intraday hari ini ─────────────────────────
        market, intra_info = inject_today_intraday(market)
        n_upd = sum(1 for v in intra_info.values() if v.get("status") in ("updated","appended"))

        # [F2] Baca balance dari state file, bukan hardcode
        _state   = load_state()
        balance  = _state.get("balance", 800_000)
        sig_lock = _state.get("signal_lock", {})

        # [B1] scan_core sekarang return 5-tuple termasuk sector_df
        prev_regime_bg = _state.get("last_regime", "-")
        scan_df, debug_df, thresholds, regime, _ = scan_core(
            market, balance, top_n=5, show_progress=False
        )
        notify_regime_change(prev_regime_bg, regime)   # alert jika regime berubah

        # [V5.6] Auto-save scan log — cari label dari SCAN_SCHEDULE
        try:
            now_wib = datetime.now(WIB)
            cur_label = "auto_scan"
            for sched in SCAN_SCHEDULE:
                if sched["hour"] == now_wib.hour and abs(sched["minute"] - now_wib.minute) <= 5:
                    cur_label = sched["label"]
                    break
            save_scan_log(scan_df, debug_df, regime, scan_label=cur_label)
        except Exception as e:
            LOG.warning(f"auto-save scan log gagal (autoscan): {e}")

        if scan_df.empty:
            send_telegram(
                f"📭 ATS AutoScan {now_label}: Tidak ada kandidat. "
                f"Regime: {regime} | Intraday: {n_upd} ticker diperbarui"
            )
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
            msg = format_telegram_signal_bg(row, regime)   # [P1] enriched background
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

        # [FIX #3] Update signal lock — atomic write via _state_lock + os.replace
        # Sebelumnya: non-atomic open("w") bisa timpa state dari UI thread (race condition)
        # Sekarang: pakai pola yang sama dengan save_state() — read-modify-write atomic
        try:
            with _state_lock:
                try:
                    with open(STATE_FILE, "r") as f:
                        st_data = json.load(f)
                except Exception:
                    st_data = {}
                st_data["signal_lock"]       = sig_lock
                st_data["last_regime"]       = regime
                st_data["last_scan_tickers"] = scan_df["Ticker"].tolist() if not scan_df.empty else []
                tmp_path = STATE_FILE + ".tmp_bg"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(st_data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, STATE_FILE)
        except Exception as e:
            LOG.error(f"auto_scan signal_lock write FAILED: {type(e).__name__}: {e}")

    except Exception as e:
        send_telegram(f"❌ ATS AutoScan ERROR: {str(e)[:200]}")

# ============================================================
# SCHEDULER
# ============================================================
# INTRADAY REFRESH — setiap 15 menit jam bursa
# Refresh harga terkini tanpa full scan
# ============================================================

# State shared untuk tracking spike antar thread
_spike_state: dict = {
    "last_prices":    {},   # {ticker: last_close} dari refresh sebelumnya
    "spike_alerts":   [],   # list ticker yang sudah dapat alert spike hari ini
    "last_spike_date": None,
}

def intraday_refresh_job():
    """
    Dijalankan setiap 15 menit saat jam bursa.
    1. Download intraday terbaru semua ticker
    2. Deteksi spike: ticker yang naik > 3% sejak refresh terakhir
    3. Kalau ada spike dengan breakout → trigger mini_scan_spike
    """
    if not is_market_open():
        return

    now_wib = datetime.now(WIB)
    today   = now_wib.date()

    # [Task 3] Thread-safe access ke _spike_state
    with _spike_lock:
        if _spike_state["last_spike_date"] != today:
            _spike_state["spike_alerts"]    = []
            _spike_state["last_spike_date"] = today
            _spike_state["last_prices"]     = {}

    try:
        # Download bulk intraday terbaru — ringan karena hanya 1d/5m
        raw5 = yf.download(
            tickers=ISSI_UNIVERSE,
            period="1d", interval="5m",
            group_by="ticker", progress=False, auto_adjust=True,
        )
        if raw5 is None or raw5.empty:
            return

        spike_candidates = []

        for tkr in ISSI_UNIVERSE:
            try:
                if len(ISSI_UNIVERSE) > 1:
                    df5 = raw5[tkr].dropna()
                else:
                    df5 = raw5.dropna()

                if df5 is None or len(df5) < 5:
                    continue

                close5      = df5["Close"].squeeze()
                volume5     = df5["Volume"].squeeze()
                current_px  = float(close5.iloc[-1])
                open_px     = float(close5.iloc[0])   # harga open hari ini
                chg_from_open = (current_px - open_px) / open_px * 100

                # Cek perubahan dari snapshot sebelumnya
                with _spike_lock:
                    prev_px     = _spike_state["last_prices"].get(tkr, open_px)
                    chg_recent  = (current_px - prev_px) / prev_px * 100 if prev_px > 0 else 0
                    # Update snapshot harga
                    _spike_state["last_prices"][tkr] = current_px

                    # Cek apakah ticker ini sudah pernah dapat alert hari ini
                    already_alerted = tkr in _spike_state["spike_alerts"]

                # Deteksi spike: naik > 3% dari open ATAU > 2% dari 15 menit lalu
                avg_vol = float(volume5.tail(20).mean()) if len(volume5) >= 20 else float(volume5.mean())
                vol_now = float(volume5.iloc[-1])
                vol_ratio = vol_now / avg_vol if avg_vol > 0 else 1.0

                is_price_spike  = chg_from_open > 3.0 or chg_recent > 2.0
                is_volume_spike = vol_ratio > 1.5

                if is_price_spike and is_volume_spike and not already_alerted:
                    spike_candidates.append({
                        "ticker":        tkr,
                        "current_px":    current_px,
                        "chg_from_open": round(chg_from_open, 2),
                        "chg_recent":    round(chg_recent, 2),
                        "vol_ratio":     round(vol_ratio, 1),
                    })

            except Exception:
                continue

        # Kalau ada spike candidate → trigger mini scan
        # [FIX #1] Baca regime dari state file (di-set oleh scan_core terakhir)
        # — fallback "SIDEWAYS" jika belum ada scan
        if spike_candidates:
            try:
                _state = load_state()
                regime_for_spike = _state.get("last_regime", "SIDEWAYS")
            except Exception:
                regime_for_spike = "SIDEWAYS"
            mini_scan_spike(spike_candidates, regime=regime_for_spike)

    except Exception as e:
        pass   # Silent fail — jangan spam Telegram untuk refresh error


def mini_scan_spike(spike_candidates: list, regime: str = "SIDEWAYS"):
    """
    Mini scan khusus untuk ticker yang terdeteksi spike.
    Hanya download daily data ticker tersebut, inject intraday,
    lalu jalankan full scoring pipeline.
    Kirim Telegram hanya kalau lolos semua filter.

    [FIX #1] regime parameter — sebelumnya hardcode 'SIDEWAYS' yang menyebabkan
    spike alert inkonsisten dengan main scanner saat market BULLISH.
    """
    if not spike_candidates:
        return

    now_label  = datetime.now(WIB).strftime("%H:%M WIB")
    sig_lock   = _spike_state.get("spike_alerts", [])

    for sp in spike_candidates:
        tkr    = sp["ticker"]
        tkr_jk = tkr

        try:
            # Download daily data untuk ticker ini saja
            df_daily = yf.download(
                tickers=tkr_jk, period="6mo", interval="1d",
                progress=False, auto_adjust=True
            )
            if df_daily is None or len(df_daily) < 60:
                continue

            df_daily = df_daily.dropna()

            # Inject harga terkini hari ini
            today      = datetime.now(WIB).date()
            last_date  = pd.to_datetime(df_daily.index[-1]).date()

            # Ambil intraday detail untuk ticker ini
            df5 = yf.download(
                tickers=tkr_jk, period="1d", interval="5m",
                progress=False, auto_adjust=True
            )

            if df5 is not None and len(df5) >= 5:
                close5 = df5["Close"].squeeze()
                high5  = df5["High"].squeeze()
                low5   = df5["Low"].squeeze()
                vol5   = df5["Volume"].squeeze()
                open5  = df5["Open"].squeeze()

                new_row = pd.DataFrame({
                    "Open":   [float(open5.iloc[0])],
                    "High":   [float(high5.max())],
                    "Low":    [float(low5.min())],
                    "Close":  [float(close5.iloc[-1])],
                    "Volume": [float(vol5.sum())],
                }, index=[pd.Timestamp(today)])

                if last_date == today:
                    df_daily.iloc[-1] = new_row.iloc[0]
                else:
                    df_daily = pd.concat([df_daily, new_row])

            # Jalankan pipeline scoring
            _state       = load_state()
            balance      = _state.get("balance", 800_000)
            # [FIX #1] Baca regime dari state — sudah tersedia sebagai parameter fungsi ini
            # Gunakan untuk adaptive weights agar konsisten dengan scan_core
            spike_regime = regime   # parameter fungsi mini_scan_spike sudah bawa regime
            sector       = get_sector(tkr_jk)
            rsi_ok, rsi_value = rsi_gate(df_daily, regime)
            if not rsi_ok:
                continue

            ema_ok, last_price, ema_val = ema_trend_filter(df_daily)
            atr     = calculate_atr(df_daily)
            entry   = last_price
            sl      = calculate_sl_atr(entry, atr)
            target  = find_target(df_daily, entry)
            rr      = risk_reward(entry, sl, target)

            if rr < 1.8:
                continue

            momentum = momentum_confirmation(df_daily)
            accum    = accumulation_phase(df_daily)
            bandar   = bandar_detection(df_daily)
            breakout = breakout_confirmation(df_daily)
            ft       = follow_through(df_daily)
            chg_pct  = daily_change_pct(df_daily)

            if breakout == "WAIT":   # [V5.6.3] Bandar bukan hard gate — konsisten dengan scan_core
                continue

            strong_daily_momentum = momentum == 2 or ft == 2
            # [V5.5.3 FIX B] Sync dengan scan_core: WAIT 3.0 → 4.5
            # (Note: di mini_scan_spike branch ini tidak tercapai karena
            #  WAIT sudah di-skip di filter sebelumnya, tapi tetap di-update
            #  untuk konsistensi behavioral kedua scanner)
            freshness_limit = (
                9.0 if breakout == "VALID" and strong_daily_momentum else
                7.0 if breakout == "VALID" else
                6.0 if breakout == "WEAK" and strong_daily_momentum else
                5.0 if breakout == "WEAK" else
                4.5
            )
            if chg_pct > freshness_limit:
                continue

            intraday = intraday_confirm(tkr_jk)
            prob     = runner_probability(df_daily)
            runner   = runner_prediction(df_daily)
            quality  = pullback_quality(df_daily)
            liq_raw  = liquidity_trap(df_daily)
            fake_bo  = fake_breakout(df_daily)
            # [FIX #3] Normalize return value — defensive coding
            is_liq_trap = is_trap_signal(liq_raw)
            is_fake_bo  = is_trap_signal(fake_bo)
            liq_str     = "🔴 TRAP" if is_liq_trap else "🟢 OK"

            if is_liq_trap or is_fake_bo:
                continue

            if breakout == "WEAK" and not (momentum == 2 or intraday >= 2 or ft == 2):
                continue

            conf_count, _, conf_passed = confluence_check(
                momentum, accum, bandar, breakout, rr, ema_ok, regime
            )
            if not conf_passed:
                continue

            # [FIX #1] Scoring regime-aware — konsisten dengan scan_core
            # Sebelumnya: calculate_score tanpa regime (default SIDEWAYS) + hardcoded multipliers
            # Sekarang: pass spike_regime ke calculate_score + pakai ada_weights untuk bonus
            ada_w  = get_adaptive_weights(spike_regime)
            score  = calculate_score(prob, runner, quality, rr, liq_str, bandar, spike_regime)
            score += (momentum * 0.8 * ada_w["momentum_w"] +
                      accum    * 0.9 * ada_w["accum_w"]    +
                      ft       * 0.7 * ada_w["ft_w"]       +
                      intraday * 0.5 * ada_w["intraday_w"])
            if momentum == 2:               score = min(100, score + 1)
            if ft == 2:                     score = min(100, score + 1)
            if last_price > ema_val * 1.01: score = min(100, score + 1)
            score = min(100.0, score)
            lot   = position_sizing(balance, 0.02, entry, sl, atr)

            # Kirim Telegram spike alert
            alignment_val = sum([
                last_price > ema20_val if (ema20_val := float(df_daily["Close"].squeeze().ewm(span=20,adjust=False).mean().iloc[-1])) else False,
                last_price > ema_val,
                momentum >= 1,
                bandar >= 2,
                breakout in ("VALID","WEAK"),
                rr >= 1.8,
            ])
            alignment_bar = "█" * alignment_val + "░" * (6 - alignment_val)

            msg = (
                f"⚡ SPIKE ALERT — ATS {APP_VERSION}\n"
                f"{'━'*30}\n"
                f"📌 {tkr_jk.replace('.JK','')}  |  {sector}\n"
                f"⏰ {now_label}  |  🔄 Intraday Refresh\n\n"
                f"📈 SPIKE DETECTED\n"
                f"Change hari ini : {chg_pct:+.2f}%\n"
                f"Change 15 menit : {sp['chg_recent']:+.2f}%\n"
                f"Volume          : {sp['vol_ratio']:.1f}x rata-rata\n"
                f"Breakout        : {breakout}\n\n"
                f"📊 SCORING\n"
                f"Score      : {score:.1f}/100\n"
                f"RR         : {rr:.1f}x\n"
                f"Confluence : {conf_count}/6\n"
                f"RSI        : {rsi_value:.1f}\n"
                f"Alignment  : [{alignment_bar}] {alignment_val}/6\n\n"
                f"💰 LEVEL\n"
                f"Entry  : {idr(entry)}\n"
                f"SL     : {idr(sl)}\n"
                f"Target : {idr(target)}\n"
                f"Lot    : {lot}\n"
                f"{'━'*30}\n"
                f"⚠️ SPIKE ALERT — verifikasi chart sebelum entry\n"
                f"Pasang SL. No FOMO."
            )
            send_telegram(msg)
            with _spike_lock:
                _spike_state["spike_alerts"].append(tkr)

        except Exception:
            continue


# ============================================================
@st.cache_resource
def start_scheduler():
    scheduler = BackgroundScheduler(timezone=WIB)

    # ── Full scan 5x sehari ──────────────────────────────────
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

    # ── Intraday refresh setiap 15 menit jam 09:05–15:30 ────
    # Ringan: hanya download 5m data + deteksi spike
    # Tidak full scan — jauh lebih hemat resource
    scheduler.add_job(
        func=intraday_refresh_job,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour="9-15",          # jam 09:00 sampai 15:00
            minute="5,20,35,50",  # menit ke 5, 20, 35, 50 → setiap 15 menit
            timezone=WIB,
        ),
        id="intraday_refresh",
        name="ATS Intraday Refresh 15min",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # [V5.6] Cleanup scan logs lama setiap hari jam 16:00 WIB
    scheduler.add_job(
        func=lambda: cleanup_old_scan_logs(days_to_keep=30),
        trigger=CronTrigger(hour=16, minute=0, timezone=WIB),
        id="cleanup_scan_logs",
        name="ATS Cleanup Scan Logs > 30 hari",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # [BH] Bandar Hunter — scan otomatis setiap 15 menit
    # Universe: ATS candidates (kalau ada) + base watchlist tetap
    # Tidak tergantung output ATS — radar independen
    from bandar_hunter import bandar_hunter_job, BANDAR_BASE_WATCHLIST
    def _bandar_hunter_auto():
        try:
            _st = load_state()
            # Ambil ATS candidates dari state terakhir (bisa kosong)
            ats_tickers = _st.get("last_scan_tickers", [])[:8]
        except Exception:
            ats_tickers = []
        bandar_hunter_job(
            ats_tickers    = ats_tickers,
            send_telegram_fn = send_telegram,
        )

    scheduler.add_job(
        func    = _bandar_hunter_auto,
        trigger = CronTrigger(
            day_of_week    = "mon-fri",
            hour           = "9-14",
            minute         = "10,25,40,55",  # versetzt 5 menit dari intraday refresh
            timezone       = WIB,
        ),
        id               = "bandar_hunter_auto",
        name             = "ATS Bandar Hunter 15min",
        replace_existing = True,
        misfire_grace_time = 60,
    )

    scheduler.start()

    # Notifikasi startup
    send_telegram(
        f"🟢 ATS SuperEngine {APP_VERSION} — SERVER ONLINE\n"
        f"⏰ {datetime.now(WIB).strftime('%Y-%m-%d %H:%M WIB')}\n"
        f"Full scan: 09:05 | 09:30 | 11:30 | 13:35 | 15:00 WIB\n"
        f"Intraday refresh: setiap 15 menit jam bursa\n"
        f"⚡ Spike detection aktif — tidak akan ketinggalan pergerakan ✅"
    )
    return scheduler

_scheduler = start_scheduler()

# ============================================================
# UI
# ============================================================
st.set_page_config(
    layout="wide",
    page_title="ATS SuperEngine — Syariah Scanner",
    page_icon="📊"
)

def next_scan_label() -> str:
    now_wib  = datetime.now(WIB)
    hari_map = {0:"Senin",1:"Selasa",2:"Rabu",3:"Kamis",4:"Jumat",5:"Sabtu",6:"Minggu"}
    if now_wib.weekday() >= 5 or is_holiday(now_wib.date()):
        next_day = now_wib + timedelta(days=1)
        while next_day.weekday() >= 5 or is_holiday(next_day.date()):
            next_day += timedelta(days=1)
        return f"{hari_map[next_day.weekday()]} 09:05 WIB"
    for sched in SCAN_SCHEDULE:
        t = now_wib.replace(hour=sched["hour"], minute=sched["minute"], second=0)
        if now_wib < t:
            return f"{sched['hour']:02d}:{sched['minute']:02d} WIB ({sched['label']})"
    next_day = now_wib + timedelta(days=1)
    while next_day.weekday() >= 5 or is_holiday(next_day.date()):
        next_day += timedelta(days=1)
    return f"{hari_map[next_day.weekday()]} 09:05 WIB"

# ── GLOBAL CSS — NOVA Dark + BMW M5 Accent ───────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

/* ── Base ── */
html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
}
.stApp {
    background: #050d1a !important;
}
.block-container {
    padding: 1rem 2rem 0.5rem !important;
    max-width: 100% !important;
}

/* ── Header custom ── */
.ats-header {
    background: linear-gradient(135deg, #0a1628 0%, #0d1f3c 50%, #0a1628 100%);
    border: 1px solid rgba(0,120,255,0.2);
    border-radius: 16px;
    padding: 20px 28px;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    box-shadow: 0 4px 24px rgba(0,100,255,0.08), 0 0 0 1px rgba(255,255,255,0.04);
    position: relative;
    overflow: hidden;
}
/* BMW M Sport diagonal stripes — racing livery accent */
.ats-header::before {
    content: '';
    position: absolute;
    top: 0;
    right: 0;
    width: 140px;
    height: 100%;
    background: linear-gradient(
        110deg,
        transparent             0%,
        transparent            42%,
        #0066B1                42%,   /* BMW Motorsport Light Blue */
        #0066B1                49%,
        transparent            49%,
        transparent            55%,
        #1C3D7C                55%,   /* BMW M Dark Blue */
        #1C3D7C                64%,
        transparent            64%,
        transparent            72%,
        #E22718                72%,   /* BMW M Red */
        #E22718                85%,
        transparent            85%
    );
    opacity: 0.85;
    pointer-events: none;
    z-index: 0;
}
.ats-header > * {
    position: relative;
    z-index: 1;   /* konten tetap di atas stripes */
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
.header-stats {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    justify-content: flex-end;
}

/* ── Metrics cards ── */
[data-testid="stMetric"] {
    background: linear-gradient(135deg, #0d1f3c 0%, #0a1628 100%) !important;
    border: 1px solid rgba(59,130,246,0.15) !important;
    border-radius: 12px !important;
    padding: 12px 16px !important;
    transition: border-color 0.2s ease !important;
}
[data-testid="stMetric"]:hover {
    border-color: rgba(59,130,246,0.35) !important;
}
[data-testid="stMetricLabel"] {
    font-size: 11px !important;
    font-weight: 500 !important;
    color: rgba(148,163,184,0.8) !important;
    text-transform: uppercase !important;
    letter-spacing: 0.04em !important;
}
[data-testid="stMetricValue"] {
    font-size: 18px !important;
    font-weight: 600 !important;
    color: #f1f5f9 !important;
}
[data-testid="stMetricDelta"] {
    font-size: 11px !important;
}

/* ── Tombol scan — BMW M5 Blue accent ── */
div[data-testid="stButton"] > button[kind="primary"] {
    background: linear-gradient(135deg, #1d4ed8 0%, #2563eb 50%, #3b82f6 100%) !important;
    border: none !important;
    color: #fff !important;
    font-weight: 600 !important;
    font-size: 14px !important;
    letter-spacing: 0.03em !important;
    border-radius: 10px !important;
    padding: 12px 24px !important;
    box-shadow: 0 4px 16px rgba(37,99,235,0.35), 0 0 0 1px rgba(255,255,255,0.08) !important;
    transition: all 0.2s ease !important;
}
div[data-testid="stButton"] > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #1e40af 0%, #1d4ed8 50%, #2563eb 100%) !important;
    box-shadow: 0 6px 20px rgba(37,99,235,0.5) !important;
    transform: translateY(-1px) !important;
}
div[data-testid="stButton"] > button[kind="primary"]:active {
    transform: translateY(0px) !important;
}

/* ── Secondary buttons ── */
div[data-testid="stButton"] > button:not([kind="primary"]) {
    background: rgba(30,58,138,0.2) !important;
    border: 1px solid rgba(59,130,246,0.25) !important;
    color: #93c5fd !important;
    border-radius: 8px !important;
    font-size: 12px !important;
}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    background: transparent !important;
    border-bottom: 1px solid rgba(59,130,246,0.15) !important;
    gap: 4px !important;
}
.stTabs [data-baseweb="tab"] {
    background: transparent !important;
    color: rgba(148,163,184,0.7) !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    padding: 8px 16px !important;
    border-radius: 8px 8px 0 0 !important;
    transition: all 0.2s !important;
}
.stTabs [aria-selected="true"] {
    background: rgba(37,99,235,0.15) !important;
    color: #60a5fa !important;
    border-bottom: 2px solid #3b82f6 !important;
}
.stTabs [data-baseweb="tab"]:hover {
    color: #93c5fd !important;
    background: rgba(37,99,235,0.08) !important;
}

/* ── Dataframe ── */
.stDataFrame {
    border: 1px solid rgba(59,130,246,0.15) !important;
    border-radius: 10px !important;
    overflow: hidden !important;
}
[data-testid="stDataFrameResizable"] {
    font-size: 12px !important;
}

/* ── Info / Warning / Success boxes ── */
[data-testid="stAlert"] {
    border-radius: 10px !important;
    border-left-width: 3px !important;
    font-size: 13px !important;
}

/* ── Expander ── */
[data-testid="stExpander"] {
    background: rgba(13,31,60,0.5) !important;
    border: 1px solid rgba(59,130,246,0.12) !important;
    border-radius: 10px !important;
}
[data-testid="stExpander"]:hover {
    border-color: rgba(59,130,246,0.25) !important;
}

/* ── Selectbox ── */
[data-testid="stSelectbox"] > div > div {
    background: #0d1f3c !important;
    border: 1px solid rgba(59,130,246,0.2) !important;
    border-radius: 8px !important;
    color: #e2e8f0 !important;
    font-size: 13px !important;
}

/* ── Caption text ── */
.stCaption, [data-testid="stCaptionContainer"] {
    color: rgba(148,163,184,0.6) !important;
    font-size: 11px !important;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: #050d1a; }
::-webkit-scrollbar-thumb { background: rgba(59,130,246,0.3); border-radius: 2px; }
::-webkit-scrollbar-thumb:hover { background: rgba(59,130,246,0.5); }

/* ── Hide Streamlit branding ── */
#MainMenu, footer, header { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

# ── HEADER premium — semua variabel di-extract dulu ─────────
market_open    = is_market_open()
market_status  = "BUKA" if market_open else "TUTUP"
market_class   = "status-open" if market_open else "status-closed"
holiday_note   = " · Libur Nasional" if is_holiday(datetime.now(WIB).date()) else ""
regime         = st.session_state.get("last_regime", "-")
regime_emoji   = "🟢" if regime == "BULLISH" else ("🔴" if regime in ["DISTRIBUTION","BEARISH"] else "🟡")
cp             = st.session_state.cybernetic_params
min_score_val  = int(cp.get("min_score", 70))          # [fix] hindari cp["key"] di f-string
intra_n        = sum(1 for v in st.session_state.get("intraday_info",{}).values()
                     if v.get("status") in ("updated","appended"))
wib_now_str    = get_wib_now()
next_scan_str  = next_scan_label()
app_ver_str    = str(APP_VERSION)
app_upd_str    = str(APP_UPDATED)

# Intraday pill — dibangun terpisah agar tidak ada nested quote di f-string
intra_pill = (
    f'<span class="status-pill status-open">⚡ Intraday {intra_n} ticker</span>'
    if intra_n > 0 else ""
)

# Min score style — CSS dipisah ke variabel agar tidak multi-line di f-string
score_style = (
    "font-size:32px;font-weight:700;"
    "background:linear-gradient(90deg,#60a5fa,#3b82f6);"
    "-webkit-background-clip:text;"
    "-webkit-text-fill-color:transparent;"
    "background-clip:text;"
    "line-height:1.1;"
)

# Build HTML header sepenuhnya terpisah dari st.markdown
header_html = (
    '<div class="ats-header">'
      '<div>'
        f'<div class="ats-logo">⚡ ATS SuperEngine {app_ver_str}</div>'
        '<div class="ats-subtitle">Automated Trading Scanner · Saham Syariah ISSI · AI-Powered</div>'
        '<div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:6px;">'
          f'<span class="status-pill {market_class}">● IDX {market_status}{holiday_note}</span>'
          f'<span class="status-pill status-info">{regime_emoji} {regime}</span>'
          f'<span class="status-pill status-info">🕐 {wib_now_str}</span>'
          f'<span class="status-pill status-info">⏰ {next_scan_str}</span>'
          f'{intra_pill}'
        '</div>'
      '</div>'
      '<div class="header-right">'
        '<div style="font-size:11px;color:rgba(148,163,184,0.6);text-align:right;">Min Score Adaptif</div>'
        f'<div style="{score_style}">{min_score_val}</div>'
        f'<div style="font-size:10px;color:rgba(148,163,184,0.5);">Update: {app_upd_str}</div>'
      '</div>'
    '</div>'
)

st.markdown(header_html, unsafe_allow_html=True)

tabs = st.tabs(["📖 HOW TO USE", "📊 TRADING DESK", "💼 ACCOUNT", "📋 REPORT", "🕌 ISSI CHECK", "🔬 DEEP ANALYSIS", "🦅 FALCON HUNTER", "🎯 BANDAR HUNTER"])

# ─────────────────────────────────────────────────────────────
# TAB 0 — HOW TO USE
# ─────────────────────────────────────────────────────────────
with tabs[0]:
    st.markdown(f"## 📖 Panduan Penggunaan ATS SuperEngine {APP_VERSION}")
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
    j1, j2, j3, j4, j5 = st.columns(5)
    j1.success("**09:05 WIB**\n\nPre-Open\n\n*Segera setelah bursa buka*")
    j2.success("**09:30 WIB**\n\nEarly Momentum\n\n*Tangkap mover 30 menit pertama*")
    j3.success("**11:30 WIB**\n\nMid Sesi 1\n\n*Tengah sesi pagi*")
    j4.success("**13:35 WIB**\n\nOpen Sesi 2\n\n*Setelah jeda ishoma*")
    j5.success("**15:00 WIB**\n\nPre-Closing\n\n*Peluang terakhir hari ini*")
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

    # FALCON HUNTER SECTION
    # ─────────────────────────────────────────────────────────
    st.markdown("## 🦅 Panduan Falcon Hunter")
    st.info(
        "**Falcon Hunter** adalah scanner saham syariah kedua yang berjalan **terpisah dari ATS**. "
        "Filosofinya berbeda: Falcon mendeteksi dua jenis setup spesifik — **Breakout** dan **Bounce** — "
        "dengan target profit harian ≥1% di H+1. Engine-nya independen, tidak mempengaruhi scanner ATS."
    )

    st.markdown("### 🦅 Filosofi Falcon")
    st.markdown(
        "> *Berburu seperti elang — sabar mengamati, presisi saat menyergap, "
        "cepat keluar saat target tercapai atau salah baca situasi. FOMO adalah racun elang.*"
    )

    # Dua mode setup
    st.markdown("### 🎯 Dua Mode Setup Falcon")
    fc1, fc2 = st.columns(2)
    with fc1:
        st.success("""
**🟢 BREAKOUT — Menyergap mangsa yang lari**

- Close **menembus resistance 10 hari** terakhir
- Volume **> 1.8× rata-rata** 20 hari (buyer agresif)
- Candle bullish — close dekat high
- Trend di atas MA20 / MA50 / MA200
        """)
    with fc2:
        st.info("""
**🔵 BOUNCE — Menunggu mangsa di sarang**

- Close **pantul dari support 20 hari** (jarak ≤ 4%)
- Volume **sepi < 0.85×** rata-rata (akumulasi diam-diam)
- Close > prev close (konfirmasi pantulan)
- Identifikasi akumulasi institusi
        """)

    st.markdown("### 🔒 Filter Wajib (Kedua Setup Harus Lolos)")
    ff1, ff2 = st.columns(2)
    with ff1:
        st.markdown("""
| Filter | Nilai |
|---|---|
| Upper shadow candle | ≤ 25% range |
| Body candle | ≥ 50% range |
| RSI(14) | ≤ 70 (hindari overbought) |
        """)
    with ff2:
        st.markdown("""
| Filter | Nilai |
|---|---|
| Gap pembukaan | ≤ 3% (hindari kejar pump) |
| Trend struktur | Di atas MA20/50/200 |
| IHSG status | Tidak bearish |
        """)

    # IHSG Decision matrix
    st.markdown("### 🌐 Decision Matrix IHSG")
    ih1, ih2, ih3 = st.columns(3)
    ih1.success("**🟢 BULLISH**\n\nIHSG > MA20 & MA50\n\nFull size, scan agresif, target normal")
    ih2.warning("**🟡 NEUTRAL**\n\nIHSG salah satu di atas\n\nSize ½ — hanya pick Falcon Score terbaik")
    ih3.error("**🔴 BEARISH**\n\nIHSG di bawah MA20 & MA50\n\n🛑 Falcon istirahat — paper trade only")

    # Risk management
    st.markdown("### 💰 Manajemen Risiko Falcon")
    st.markdown("---")
    rm1, rm2 = st.columns(2)
    with rm1:
        st.markdown("""
**📐 Position Sizing:**
```
Risk per trade  = Modal × 1%
Lot             = Risk / (Entry - SL) / 100
Max posisi      = 3 terbuka simultan
Max per saham   = 5% modal
Max total       = 30% modal
```

**🛑 Stop Loss Formula:**
```
SL = max(swing low 5 hari × 0.995,
         entry - 1.5 × ATR14)
Hard stop di broker — BUKAN mental stop
```
        """)
    with rm2:
        st.markdown("""
**🎯 Target & Exit:**
```
T1 = Entry + 1R  → sell 50%, geser SL ke breakeven
T2 = Entry + 2R  → sell sisa atau trailing
Trailing         = SL di low 3 hari terakhir
Time stop        = maksimal hold 5 hari
```

**📊 Gap Management:**
```
Gap up ≤ 1%    → entry sesuai plan ✅
Gap up 1–2%   → tunggu pullback ⏸
Gap up > 2%   → SKIP — R:R rusak ❌
Gap down ≤ 1% → opportunity bagus 🎯
Gap down > 2% → re-evaluate 🔍
```
        """)

    # Rutinitas harian Falcon
    st.markdown("### 📅 Rutinitas Harian Falcon")
    st.markdown("---")
    fd1, fd2, fd3 = st.columns(3)
    with fd1:
        st.markdown("""
**🌆 H-1 Sore (16:00 WIB)**

1. Cek konteks makro (Dow, Nikkei, Rupiah)
2. Buka tab **🦅 Falcon Hunter**
3. Klik **Jalankan Falcon Scan**
4. Lihat IHSG status → tentukan size
5. Baca ranking tabel — filter Score ≥ 0.50
6. Validasi manual chart top 3 di TradingView
7. Shortlist 1–3 saham untuk besok
        """)
    with fd2:
        st.markdown("""
**🌅 H Pagi (08:30 WIB)**

1. Cek futures Asia & USD/IDR
2. Hitung gap pembukaan kandidat:
   - Gap > 2% → **SKIP**
   - Gap ≤ 1% → lanjut
3. Konfirmasi entry zone masih valid
4. Scan ulang jika perlu
        """)
    with fd3:
        st.markdown("""
**⚡ H Entry (09:00–10:30 WIB)**

1. Pasang order sesuai plan
2. **Langsung pasang hard SL di broker**
3. Set alert T1 & T2
4. Tutup chart — jangan micromanage
5. Evaluasi sore setelah close
6. Update Journal Falcon
        """)

    # Rules tidak bisa ditawar
    st.markdown("### 🚫 5 Aturan Falcon yang TIDAK BISA Ditawar")
    st.error("""
1. ❌  Tidak entry tanpa SL ter-set di sistem broker — TANPA PENGECUALIAN
2. ❌  Tidak average down saham yang loss
3. ❌  Tidak revenge trading setelah loss beruntun
4. ❌  Tidak skip cut loss karena "yakin akan balik"
5. ❌  Tidak melebihi 1% risk per trade, apapun feeling-nya

Loss beruntun 3× → STOP trading hari itu. Review dulu, lanjut besok dengan size ½.
Profit beruntun bukan alasan menaikkan size mendadak — streak bagus = market cocok, bukan kamu sakti.
    """)

    # Perbedaan ATS vs Falcon
    st.markdown("### ⚖️ ATS SuperEngine vs Falcon Hunter — Perbedaan")
    st.markdown("---")
    pt1, pt2 = st.columns(2)
    with pt1:
        st.markdown("""
**🤖 ATS SuperEngine**
- Universe: 98 saham ISSI (luas)
- Setup: Breakout + Bandar detection
- Hold: Tidak ada batas waktu
- Target: Single target, RR min 1.8×
- Output: Score 0–100, EXECUTE/READY
- Regime: BULLISH/SIDEWAYS/DISTRIBUTION
- Best for: High-conviction, sabar
        """)
    with pt2:
        st.markdown("""
**🦅 Falcon Hunter**
- Universe: 30 ticker watchlist (fokus)
- Setup: Breakout + Bounce (dua mode)
- Hold: Maksimal 5 hari
- Target: T1 (1R) + T2 (2R), partial exit
- Output: Falcon Score 0–1, BRK/BNC
- Regime: BULLISH/NEUTRAL/BEARISH (IHSG)
- Best for: Setup harian, agresif terukur
        """)

    st.success("""
💡 **Cara pakai optimal:** Kalau ATS dan Falcon **keduanya** menyebut ticker yang sama → 
itu sinyal paling kuat. Dua sistem berbeda dengan logika berbeda, tapi setuju. Conviction tertinggi.
    """)

    # Cara baca tabel Falcon
    st.markdown("### 🔍 Cara Baca Tabel & Card Falcon Hunter")
    st.markdown("""
| Kolom | Artinya |
|---|---|
| **Setup** | 🟢 BRK = Breakout, 🔵 BNC = Bounce, — = tidak ada setup |
| **Score 🦅** | Falcon Score 0–1. Di atas 0.5 layak dipertimbangkan |
| **Vol×** | Rasio volume vs rata-rata 20 hari. BRK butuh ≥ 1.8×, BNC butuh ≤ 0.85× |
| **RSI** | Harus ≤ 70. Di atas itu overbought, skip |
| **Trend** | Skor 0–1 posisi harga vs MA20/50/200. Min 0.5 |
| **Entry** | Harga close kemarin = level entry hari ini |
| **SL** | Stop Loss. Wajib dipasang hard stop di broker |
| **T1** | Target pertama (1R). Sell 50% di sini, geser SL ke breakeven |
| **T2** | Target kedua (2R). Sell sisa atau trailing |
| **RR** | Risk/Reward aktual. Min 1.5× untuk lanjut |
| **Lot** | Kalkulasi otomatis berdasarkan 1% risk dari modal yang diset |
    """)

    st.markdown("---")
    st.caption(
        "ATS SuperEngine V4.0 | Scanner Saham Syariah ISSI | "
        "Bukan rekomendasi investasi — selalu lakukan riset mandiri | "
        "Gunakan dengan manajemen risiko yang ketat 🤲"
    )


    # ── Bandar Hunter How To Use ───────────────────────────────
    st.markdown("---")
    st.markdown("## 🎯 Panduan Bandar Hunter")
    st.info(
        "**Bandar Hunter** adalah detector pergerakan institusional berbasis data 5 menit. "
        "Berjalan terpisah dari ATS dan Falcon. Tujuannya bukan memberikan sinyal beli/jual, "
        "tapi **mendidik kamu membaca jejak pergerakan uang besar** di market IDX."
    )

    bh_ed1, bh_ed2 = st.columns(2)
    with bh_ed1:
        st.markdown("""
**🔄 4 Fase Siklus yang Dideteksi:**

| Sinyal | Fase | Aksi |
|---|---|---|
| ⚡ Initial Markup | Bandar push harga | Monitor entry H1 |
| 🤫 Akumulasi Senyap | Bandar kumpul saham | Setup entry terbaik |
| 🔊 Volume Anomali | Arah belum jelas | Tunggu konfirmasi |
| 🔴 Distribusi | Bandar mulai jual | Hindari entry baru |
        """)
    with bh_ed2:
        st.markdown("""
**⚙️ Cara Pakai:**

1. Tab **🎯 Bandar Hunter** — ticker otomatis dari kandidat ATS scan
2. Klik **Scan Bandar Sekarang**
3. Lihat sinyal yang muncul + baca edukasi di setiap card
4. **Konfirmasi di D1 chart** sebelum eksekusi apapun
5. Telegram alert otomatis kalau ada sinyal actionable

**❗ Yang tidak boleh:**
- Langsung entry hanya dari sinyal Bandar Hunter
- Abaikan SL karena "bandar pasti lanjut naik"
- Trading di luar jam 09:30–15:00 WIB
        """)

    st.warning(
        "⚠️ **Keterbatasan:** Bandar Hunter menggunakan data yfinance 5m "
        "sebagai **proxy** pergerakan institusional. "
        "Ini bukan broker flow data sesungguhnya. "
        "False positive mungkin terjadi di saham tidak likuid."
    )

    # ── Changelog ──────────────────────────────────────────────
    # ── Changelog ──────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📋 Riwayat Update & Versi")

    for v in VERSION_HISTORY:
        tipe_color = {
            "Major Release": "🟣",
            "Bug Fix + Kalibrasi": "🟠",
            "Upgrade": "🟢",
        }.get(v["tipe"], "⚪")

        with st.expander(
            f"{tipe_color} **{v['versi']}** — {v['tanggal']}  |  {v['ringkasan']}",
            expanded=(v["versi"] == APP_VERSION)
        ):
            st.markdown(f"**Tipe:** {v['tipe']}")
            st.markdown("**Perubahan:**")
            for item in v["detail"]:
                st.markdown(f"- {item}")

# ─────────────────────────────────────────────────────────────
# TAB 1 — TRADING DESK (Compact One-Screen Layout)
# ─────────────────────────────────────────────────────────────
with tabs[1]:

    # ── CSS compact Trading Desk ─────────────────────────────
    st.markdown("""
    <style>
    .block-container { padding-top: 0.4rem !important; }
    [data-testid="stMetricValue"] { font-size: 15px !important; }
    [data-testid="stMetricLabel"] { font-size: 10px !important; }
    .stDataFrame { font-size: 12px !important; }
    </style>
    """, unsafe_allow_html=True)

    # ── ROW 1: Status bar kompak ─────────────────────────────
    market_status = "🟢 BUKA" if is_market_open() else "🔴 TUTUP"
    intra_n = sum(1 for v in st.session_state.intraday_info.values()
                  if v.get("status") in ("updated","appended")) if st.session_state.intraday_info else 0
    regime  = st.session_state.get("last_regime", "-")

    r1c1, r1c2, r1c3, r1c4, r1c5, r1c6 = st.columns(6)
    r1c1.metric("Bursa", market_status)
    r1c2.metric("Regime", regime)
    r1c3.metric("Balance", f"Rp {idr(st.session_state.balance)}")
    r1c4.metric("Risk/Trade", f"Rp {idr(st.session_state.balance * 0.02)}")
    r1c5.metric("⚡ Intraday", f"{intra_n} ticker" if intra_n > 0 else "Offline")
    r1c6.metric("Next Scan", next_scan_label().split(" (")[0])

    # ── ROW 2: Tombol scan ───────────────────────────────────
    if st.button(f"🚀 RUN ATS SCANNER {APP_VERSION}",
                 type="primary", use_container_width=True):
        with st.spinner("Scanning..."):
            run_scanner()

    # ── ROW 3: Threshold + intraday status (1 baris) ─────────
    row3_parts = []
    if st.session_state.dynamic_thresholds:
        th = st.session_state.dynamic_thresholds
        row3_parts.append(
            f"📊 EN≥{th['execute_now']:.0f} | EX≥{th['execute']:.0f} | RD≥{th['ready']:.0f}"
            f" *({th.get('n_samples',0)} kandidat)*"
        )
    if intra_n > 0:
        row3_parts.append(f"⚡ Intraday {intra_n} ticker | {get_wib_now()}")
    if row3_parts:
        st.caption("  ·  ".join(row3_parts))

    # ── HASIL SCAN ────────────────────────────────────────────
    if st.session_state.scan_result is not None and not st.session_state.scan_result.empty:
        df   = st.session_state.scan_result.copy()
        best = df.iloc[0]

        # ROW 4: Summary metrics top candidate
        st.markdown("---")
        sm1, sm2, sm3, sm4, sm5, sm6 = st.columns(6)
        sm1.metric("🏆 Ticker",    best["Ticker"])
        sm2.metric("📊 Score",     f"{best['Score']:.1f}")
        sm3.metric("⚖️ RR",        f"{best['RR']:.1f}x")
        sm4.metric("🎯 Conf",      f"{best['Confluence']}/6")
        sm5.metric("📈 Change",    f"{best.get('Change%',0):+.2f}%")
        sm6.metric("💥 Action",    best.get("Action",""))

        # ROW 5: Layout 2 kolom — kiri: tabel, kanan: chart
        col_left, col_right = st.columns([1, 1])

        with col_left:
            st.markdown("**🏆 Top Kandidat**")
            cols_show = ["BUY","Action","Ticker","Score","RR",
                         "Change%","ATR%","Confluence","RSI","Breakout",
                         "Entry","SL","Target","Lot"]
            cols_show = [c for c in cols_show if c in df.columns]

            edited = st.data_editor(
                df[cols_show],
                use_container_width=True,
                hide_index=True,
                height=220,
                column_config={
                    "BUY":       st.column_config.CheckboxColumn("BUY", width="small"),
                    "Action":    st.column_config.TextColumn("Action", width="medium"),
                    "Score":     st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f"),
                    "Confluence":st.column_config.NumberColumn("Conf", min_value=0, max_value=6, width="small"),
                    "Breakout":  st.column_config.TextColumn("BO", width="small"),
                    "RR":        st.column_config.NumberColumn("RR", format="%.1f", width="small"),
                    "RSI":       st.column_config.NumberColumn("RSI", format="%.0f", width="small"),
                    "Change%":   st.column_config.NumberColumn("Chg%", format="%.1f", width="small"),
                    "ATR%":      st.column_config.NumberColumn("ATR%", format="%.2f", width="small",
                                     help="Volatilitas harian (%). Min 1.5% untuk swing trade layak"),
                    "Lot":       st.column_config.NumberColumn("Lot", width="small"),
                })

            # BUY logic
            buy_rows = edited[edited["BUY"] == True]
            if len(buy_rows) > 0:
                existing    = st.session_state.active_trades["Ticker"].tolist() \
                    if not st.session_state.active_trades.empty else []
                new_trades  = buy_rows[~buy_rows["Ticker"].isin(existing)].copy()
                if len(new_trades) > 0:
                    new_trades["Status"]    = "OPEN"
                    new_trades["EntryTime"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                    new_trades["ExitPrice"] = None
                    new_trades["ExitDate"]  = None
                    new_trades["PnL"]       = None
                    st.session_state.active_trades = pd.concat(
                        [st.session_state.active_trades, new_trades], ignore_index=True)
                    st.session_state.active_trades.to_csv(ACTIVE_FILE, index=False)

                    # Journal auto-fill
                    JOURNAL_COLS = ["Date","Ticker","Entry","SL","Target",
                                    "Lot","Sector","Action","RR","Score","Exit","PnL","Notes"]
                    journal_entries = []
                    for _, tr in new_trades.iterrows():
                        journal_entries.append({
                            "Date":   datetime.now().strftime("%Y-%m-%d"),
                            "Ticker": tr.get("Ticker","-"), "Entry": tr.get("Entry","-"),
                            "SL":     tr.get("SL","-"),     "Target": tr.get("Target","-"),
                            "Lot":    tr.get("Lot","-"),     "Sector": tr.get("Sector","-"),
                            "Action": tr.get("Action","-"), "RR":     tr.get("RR","-"),
                            "Score":  tr.get("Score","-"),  "Exit":   None, "PnL": None,
                            "Notes":  f"Auto ATS {APP_VERSION} | {datetime.now().strftime('%H:%M WIB')}",
                        })
                    if journal_entries:
                        new_j = pd.DataFrame(journal_entries)
                        for col in JOURNAL_COLS:
                            if col not in new_j.columns: new_j[col] = None
                        if st.session_state.journal.empty:
                            st.session_state.journal = new_j[JOURNAL_COLS]
                        else:
                            for col in JOURNAL_COLS:
                                if col not in st.session_state.journal.columns:
                                    st.session_state.journal[col] = None
                            st.session_state.journal = pd.concat(
                                [st.session_state.journal, new_j[JOURNAL_COLS]], ignore_index=True)
                        st.session_state.journal.to_csv(JOURNAL_FILE, index=False)
                    st.success(f"✅ {len(new_trades)} trade masuk | Journal terisi otomatis")
                else:
                    st.warning("Ticker sudah ada di Active Trades")

            # Active trades compact
            if not st.session_state.active_trades.empty:
                st.markdown("**📌 Active Trades**")
                act_cols = ["Ticker","Entry","SL","Target","Status","PnL"]
                act_show = [c for c in act_cols if c in st.session_state.active_trades.columns]
                active_edited = st.data_editor(
                    st.session_state.active_trades[act_show] if act_show else st.session_state.active_trades,
                    num_rows="dynamic", use_container_width=True,
                    hide_index=True, height=150)
                if st.button("💾 Save", key="save_active"):
                    st.session_state.active_trades = active_edited.reset_index(drop=True)
                    st.session_state.active_trades.to_csv(ACTIVE_FILE, index=False)
                    st.success("✅ Tersimpan")

        with col_right:
            # Chart TradingView compact
            ticker_opts = df["Ticker"].tolist()
            selected    = st.selectbox("📈 Chart", ticker_opts, label_visibility="collapsed")
            st.components.v1.html(
                f'<iframe src="https://s.tradingview.com/widgetembed/?symbol=IDX:{selected}'
                f'&interval=D&theme=dark&style=1&locale=id&hide_top_toolbar=1&hide_side_toolbar=1" '
                f'width="100%" height="370" frameborder="0"></iframe>',
                height=375
            )

    elif st.session_state.scan_result is not None:
        st.warning("⚠️ Tidak ada kandidat hari ini — market sedang tidak kondusif.")

    # ── [Task 2] Score Breakdown — Explainability ────────────
    if (st.session_state.scan_result is not None and
        not st.session_state.scan_result.empty and
        "ScoreBreakdown" in st.session_state.scan_result.columns):
        with st.expander("🔬 Score Breakdown — Bagaimana score dihitung?", expanded=False):
            st.caption(
                "Penjabaran komponen score per kandidat. "
                "Berguna untuk debug — kenapa saham X mendapat score tertentu."
            )
            for _, row in st.session_state.scan_result.iterrows():
                bd = row.get("ScoreBreakdown", {})
                if not isinstance(bd, dict):
                    continue
                tkr = row.get("Ticker", "-")

                lines_md = []
                lines_md.append(f"**{tkr}** (Score: {bd.get('final', 0):.1f})")

                # Format component dengan + atau -
                def fmt(v: float, label: str) -> str:
                    if v > 0:    return f"  + {label}: **+{v:.1f}**"
                    elif v < 0:  return f"  − {label}: **{v:.1f}**"
                    else:        return f"  · {label}: 0"

                lines_md.append(f"  base score: {bd.get('base', 0):.1f}")
                if bd.get("momentum", 0) != 0: lines_md.append(fmt(bd["momentum"], "momentum"))
                if bd.get("accum", 0)    != 0: lines_md.append(fmt(bd["accum"],    "accumulation"))
                if bd.get("ft", 0)       != 0: lines_md.append(fmt(bd["ft"],       "follow-through"))
                if bd.get("intraday", 0) != 0: lines_md.append(fmt(bd["intraday"], "intraday"))
                if bd.get("extra", 0)    != 0: lines_md.append(fmt(bd["extra"],    "bonus tambahan"))
                if bd.get("sector", 0)   != 0: lines_md.append(fmt(bd["sector"],   "sector adjustment"))
                lines_md.append(f"  ─────────────────────────────")
                lines_md.append(f"  **Final: {bd.get('final', 0):.1f}**")
                st.markdown("\n".join(lines_md))
                st.markdown("")   # spacing

    # ── Heatmap langsung (tidak di expander) ─────────────────
    has_heatmap = st.session_state.heatmap_data is not None and not st.session_state.heatmap_data.empty
    has_sector  = st.session_state.sector_table is not None

    if has_heatmap:
        st.markdown("---")
        hdf = st.session_state.heatmap_data.copy()
        n_green   = (hdf["Change%"] > 0).sum()
        n_red     = (hdf["Change%"] < 0).sum()
        n_flat    = (hdf["Change%"] == 0).sum()
        best_tkr  = hdf.loc[hdf["Change%"].idxmax(), "Ticker"]
        best_chg  = hdf["Change%"].max()
        worst_tkr = hdf.loc[hdf["Change%"].idxmin(), "Ticker"]
        worst_chg = hdf["Change%"].min()
        avg_chg   = hdf["Change%"].mean()

        hm1, hm2, hm3, hm4, hm5 = st.columns(5)
        hm1.metric("Naik",       f"{n_green} saham", f"{avg_chg:+.2f}% avg")
        hm2.metric("Turun",      f"{n_red} saham")
        hm3.metric("Flat",       f"{n_flat} saham")
        hm4.metric("Top Gainer", best_tkr,  f"{best_chg:+.2f}%")
        hm5.metric("Top Loser",  worst_tkr, f"{worst_chg:+.2f}%")

        fig_heat = px.treemap(
            hdf, path=["Sektor","Ticker"], values="Size", color="Change%",
            color_continuous_scale=["#7f1d1d","#dc2626","#fca5a5",
                                    "#f1f5f9","#86efac","#16a34a","#14532d"],
            color_continuous_midpoint=0, range_color=[-5,5],
            custom_data=["Change%","Sektor"],
        )
        fig_heat.update_traces(
            texttemplate="<b>%{label}</b><br>%{customdata[0]:+.2f}%",
            textfont_size=10,
            hovertemplate="<b>%{label}</b><br>%{customdata[1]}<br>%{customdata[0]:+.2f}%<extra></extra>",
            marker_line_width=0.5,
        )
        fig_heat.update_layout(
            height=400, margin=dict(t=5,b=5,l=5,r=5),
            paper_bgcolor="rgba(0,0,0,0)",
            coloraxis_colorbar=dict(
                title="Chg%", tickvals=[-5,-3,-1,0,1,3,5],
                ticktext=["-5%","-3%","-1%","0","+1%","+3%","+5%"], len=0.8,
            ),
        )
        st.plotly_chart(fig_heat, use_container_width=True)
        st.caption("Ukuran kotak = estimasi nilai transaksi harian (Rp miliar) · Klik sektor untuk zoom")

    if has_sector:
        fig_sec = px.bar(
            st.session_state.sector_table,
            x="Strength", y="Sector", orientation="h", color="Strength",
            color_continuous_scale=["#dc2626","#f59e0b","#16a34a"],
        )
        fig_sec.add_vline(x=0, line_width=1, line_color="rgba(255,255,255,0.15)")
        fig_sec.update_layout(
            height=320, showlegend=False,
            margin=dict(t=5,b=5,l=5,r=5),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(gridcolor="rgba(255,255,255,0.05)"),
            yaxis=dict(gridcolor="rgba(0,0,0,0)"),
        )
        st.plotly_chart(fig_sec, use_container_width=True)

    # Debug dalam expander
    if st.session_state.debug_log:
        debug_df = pd.DataFrame(st.session_state.debug_log)
        gugur_counts = (
            debug_df[debug_df["❌ Gugur di"] != "✅ LOLOS — masuk kandidat"]["❌ Gugur di"]
            .str.extract(r"^([^(|]+)")[0].str.strip().value_counts().reset_index()
        )
        gugur_counts.columns = ["Alasan Gugur", "Jumlah Ticker"]

        with st.expander("🔍 Scan Debug", expanded=False):
            st.caption(
                f"Total: **{len(debug_df)}** | "
                f"Lolos: **{(debug_df['❌ Gugur di'] == '✅ LOLOS — masuk kandidat').sum()}** | "
                f"Gugur: **{(debug_df['❌ Gugur di'] != '✅ LOLOS — masuk kandidat').sum()}**"
            )

            # ── [NEW] Tombol download CSV — full debug log ───
            try:
                csv_full    = debug_df.to_csv(index=False).encode("utf-8")
                csv_summary = gugur_counts.to_csv(index=False).encode("utf-8")
                ts_str      = datetime.now(WIB).strftime("%Y%m%d_%H%M")
                regime_str  = st.session_state.get("last_regime", "-")

                dl_c1, dl_c2 = st.columns(2)
                with dl_c1:
                    st.download_button(
                        label="📥 Download Full Debug Log (CSV)",
                        data=csv_full,
                        file_name=f"ats_debug_full_{ts_str}_{regime_str}.csv",
                        mime="text/csv",
                        use_container_width=True,
                        help="Semua ticker dengan alasan gugur lengkap — untuk analisis offline",
                    )
                with dl_c2:
                    st.download_button(
                        label="📊 Download Summary Gugur (CSV)",
                        data=csv_summary,
                        file_name=f"ats_debug_summary_{ts_str}_{regime_str}.csv",
                        mime="text/csv",
                        use_container_width=True,
                        help="Distribusi alasan gugur per kategori",
                    )
            except Exception as e:
                LOG.warning(f"download_button error: {type(e).__name__}: {e}")

            if not gugur_counts.empty:
                fig_d = px.bar(gugur_counts, x="Jumlah Ticker", y="Alasan Gugur",
                               orientation="h", color="Jumlah Ticker",
                               color_continuous_scale=["#22c55e","#f59e0b","#ef4444"])
                fig_d.update_layout(height=220, showlegend=False,
                                    margin=dict(t=10,b=10,l=10,r=10),
                                    yaxis=dict(autorange="reversed"))
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

            # Tombol download untuk hasil filter (kalau user filter)
            if filter_sektor != "Semua" or filter_status != "Semua":
                try:
                    csv_filtered = filtered.to_csv(index=False).encode("utf-8")
                    suffix = []
                    if filter_sektor != "Semua": suffix.append(filter_sektor)
                    if filter_status != "Semua": suffix.append(filter_status.replace("✅ ","").replace("❌ ",""))
                    suffix_str = "_".join(suffix) if suffix else "filtered"
                    st.download_button(
                        label=f"📥 Download Filtered CSV ({len(filtered)} rows)",
                        data=csv_filtered,
                        file_name=f"ats_debug_{suffix_str}_{ts_str}.csv",
                        mime="text/csv",
                        help="Hanya hasil filter yang sedang ditampilkan",
                    )
                except Exception:
                    pass

            def color_rows(row):
                if row["❌ Gugur di"] == "✅ LOLOS — masuk kandidat":
                    return ["background-color: rgba(34,197,94,0.12)"] * len(row)
                return ["background-color: rgba(239,68,68,0.08)"] * len(row)

            st.dataframe(filtered.style.apply(color_rows, axis=1),
                use_container_width=True, hide_index=True,
                column_config={c: st.column_config.TextColumn(c) for c in filtered.columns})

# ─────────────────────────────────────────────────────────────
# TAB 2 — ACCOUNT
# ─────────────────────────────────────────────────────────────
with tabs[2]:
    st.subheader("💼 Manajemen Akun")
    col_inp, col_pad = st.columns([2, 3])
    with col_inp:
        # Cast ke int agar konsisten dengan min_value dan step
        current_balance = int(st.session_state.balance)
        balance_input = st.number_input("💰 Modal / Balance (Rp)",
            min_value=100_000, step=100_000, value=current_balance,
            key="balance_account_input",
            help="Modal trading. Dipakai untuk kalkulasi lot & risk per trade.")
        if balance_input != current_balance:
            st.session_state.balance = int(balance_input)
            save_state()
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
    # ── [V5.6] Scan History — auto-saved logs ──────────────
    st.subheader("📂 Scan History — Auto-Backup Log")
    st.caption(
        "Setiap scan otomatis ter-backup ke disk. "
        "Download per-hari (ZIP) atau per-file untuk analisis offline."
    )

    available_dates = list_scan_log_dates()
    if not available_dates:
        st.info("Belum ada scan log tersimpan. Jalankan scan dulu untuk mulai mengumpulkan data.")
    else:
        sh_col1, sh_col2 = st.columns([1, 3])
        with sh_col1:
            selected_date = st.selectbox(
                "Pilih tanggal", available_dates,
                format_func=lambda d: f"📅 {d}",
                key="scan_history_date"
            )
        with sh_col2:
            files = get_scan_log_files(selected_date)
            st.caption(f"**{len(files)} file** tersimpan untuk tanggal {selected_date}")

        # Tombol download ZIP semua file di tanggal ini
        if files:
            try:
                zip_bytes = create_zip_for_date(selected_date)
                if zip_bytes:
                    st.download_button(
                        label=f"📦 Download Semua ({len(files)} file) — {selected_date}.zip",
                        data=zip_bytes,
                        file_name=f"ats_scan_logs_{selected_date}.zip",
                        mime="application/zip",
                        use_container_width=True,
                    )
            except Exception as e:
                st.warning(f"ZIP error: {e}")

            # Daftar file individual
            with st.expander(f"📄 Daftar {len(files)} file individual", expanded=False):
                for fname in files:
                    full_path = os.path.join(SCAN_LOG_DIR, selected_date, fname)
                    try:
                        with open(full_path, "rb") as f:
                            file_bytes = f.read()
                        size_kb = len(file_bytes) / 1024
                        col_a, col_b = st.columns([3, 1])
                        with col_a:
                            st.text(f"  {fname}  ({size_kb:.1f} KB)")
                        with col_b:
                            st.download_button(
                                label="⬇️", data=file_bytes,
                                file_name=fname, mime="text/csv",
                                key=f"dl_{selected_date}_{fname}"
                            )
                    except Exception:
                        continue

    st.markdown("---")
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


# ─────────────────────────────────────────────────────────────
# TAB 5 — DEEP ANALYSIS  V2 (Zero Contradiction Edition)
# Fix: C1 data label, C2 2y data, C3 max_tokens, C4 MA200,
#      C5 import, C6 conflict detection, C7 weekly fallback,
#      C8 sample size, C9 tokens, C10 Indonesia context,
#      C11 Fibonacci outlier, C13 min data, C14 scan_row guard
# ─────────────────────────────────────────────────────────────
import calendar as _cal   # [C5] import di level modul, bukan dalam if block

with tabs[5]:
    st.subheader("🔬 Deep Analysis — AI Second Opinion")
    st.info(
        "**Cara pakai:** Jalankan scanner dulu di tab Trading Desk → "
        "pilih ticker dari hasil scan → klik Jalankan. "
        "AI akan menganalisis berdasarkan data teknikal dan memberikan second opinion."
    )

    # [C1] Disclaimer data sangat jelas di atas
    st.warning(
        "⚠️ **Penting dibaca sebelum menggunakan:**\n\n"
        "1. **Data berbasis closing kemarin** — analisis ini menggunakan harga penutupan hari terakhir "
        "yang tersedia, bukan harga real-time hari ini. Verifikasi kondisi chart sebelum eksekusi.\n\n"
        "2. **Bukan rekomendasi investasi** — AI adalah second opinion berbasis data teknikal. "
        "Keputusan tetap di tangan kamu.\n\n"
        "3. **Jika ATS dan AI berbeda pendapat** — sistem akan tampilkan peringatan konflik. "
        "Baca penjelasannya sebelum memutuskan."
    )
    st.markdown("---")

    # ── Pilihan ticker ──────────────────────────────────────
    scan_tickers = []
    if st.session_state.scan_result is not None and not st.session_state.scan_result.empty:
        scan_tickers = st.session_state.scan_result["Ticker"].tolist()

    all_issi      = sorted([t.replace(".JK", "") for t in ISSI_UNIVERSE])
    default_ticker = scan_tickers[0] if scan_tickers else "BRIS"

    col_t1, col_t2 = st.columns([2, 1])
    with col_t1:
        ticker_input = st.selectbox(
            "🎯 Pilih saham untuk dianalisis",
            options=all_issi,
            index=all_issi.index(default_ticker) if default_ticker in all_issi else 0,
            help="Ticker dari hasil scan ATS otomatis muncul sebagai default"
        )
    with col_t2:
        analysis_type = st.selectbox(
            "Tipe Analisis",
            ["Full Analysis", "Citadel Technical", "Bridgewater Risk", "Renaissance Pattern"]
        )

    # ── ATS signal context (dengan guard C14) ───────────────
    has_ats_signal = scan_tickers and ticker_input in scan_tickers
    scan_row       = None
    if has_ats_signal:
        try:
            scan_row = st.session_state.scan_result[
                st.session_state.scan_result["Ticker"] == ticker_input
            ].iloc[0]
            sc1, sc2, sc3, sc4, sc5 = st.columns(5)
            sc1.metric("ATS Score",  f"{float(scan_row.get('Score', 0)):.1f}")
            sc2.metric("ATS Action", str(scan_row.get("Action", "-")))
            sc3.metric("RR",         f"{float(scan_row.get('RR', 0)):.1f}x")
            sc4.metric("Confluence", f"{scan_row.get('Confluence', '-')}/6")
            sc5.metric("Change",     f"{float(scan_row.get('Change%', 0)):+.2f}%")
        except Exception:
            has_ats_signal = False
            scan_row       = None
    else:
        st.caption("💡 Jalankan scanner dulu agar ATS signal bisa dibandingkan dengan analisis AI.")

    st.markdown("---")

    # ── Cek AI provider — Anthropic atau Gemini ──────────────
    ai_provider = get_ai_provider()
    api_key_available = ai_provider != "none"

    if not api_key_available:
        st.info(
            "💎 **Deep Analysis adalah fitur opsional**\n\n"
            "Fitur ini menggunakan AI untuk generate analisis "
            "Citadel + Bridgewater + Renaissance secara mendalam.\n\n"
            "**Sistem ATS utama tetap berjalan normal tanpa fitur ini** — "
            "auto-scan, Telegram alert, heatmap, dan semua fitur core tidak terpengaruh.\n\n"
            "**Pilihan Provider:**\n\n"
            "🆓 **Google Gemini (GRATIS — Direkomendasikan)**\n"
            "1. Buka [aistudio.google.com/apikey](https://aistudio.google.com/apikey)\n"
            "2. Login dengan akun Google → Klik 'Create API Key'\n"
            "3. Copy API key-nya\n"
            "4. Buka Railway → Variables → tambah `GEMINI_API_KEY`\n"
            "5. Save dan tunggu redeploy\n\n"
            "💰 **Anthropic Claude (BERBAYAR — Premium)**\n"
            "1. [console.anthropic.com](https://console.anthropic.com/) → buat API key\n"
            "2. Top up credit (~$5 USD = ~300 analisis)\n"
            "3. Set `ANTHROPIC_API_KEY` di Railway"
        )
        st.button(
            "🔒 JALANKAN DEEP ANALYSIS (Set API Key dulu)",
            type="secondary", use_container_width=True, disabled=True
        )
        run_analysis = False
    else:
        # Tampilkan info provider yang aktif
        provider_label = {
            "anthropic": "🟣 Claude (Anthropic)",
            "gemini":    "🔵 Gemini (Google) — FREE tier",
        }.get(ai_provider, ai_provider)
        st.caption(f"Provider AI aktif: **{provider_label}**")

        run_analysis = st.button(
            "🔬 JALANKAN DEEP ANALYSIS", type="primary", use_container_width=True
        )

    if run_analysis:
        with st.spinner(f"Mengambil data dan menganalisis {ticker_input}..."):
            try:
                ticker_jk = ticker_input + ".JK"

                # [C2] Ambil 2 tahun data harian untuk seasonal yang lebih valid
                df_raw = yf.download(
                    tickers=ticker_jk, period="2y", interval="1d",
                    progress=False, auto_adjust=True
                )
                df_weekly = yf.download(
                    tickers=ticker_jk, period="3y", interval="1wk",
                    progress=False, auto_adjust=True
                )

                if df_raw is None or len(df_raw) < 60:
                    st.error(f"Data tidak cukup untuk {ticker_input} (butuh min 60 hari). Coba ticker lain.")
                    st.stop()

                close  = df_raw["Close"].squeeze()
                high   = df_raw["High"].squeeze()
                low    = df_raw["Low"].squeeze()
                volume = df_raw["Volume"].squeeze()
                n_bars = len(close)

                last_price = float(close.iloc[-1])
                prev_price = float(close.iloc[-2])
                data_date  = pd.to_datetime(close.index[-1]).strftime("%d %b %Y")
                chg_last   = (last_price - prev_price) / prev_price * 100

                # [C1] Label jelas bahwa ini data terakhir, bukan hari ini
                data_label = f"Data closing: {data_date}"

                # Moving averages
                ma20  = float(close.tail(20).mean())
                ma50  = float(close.tail(50).mean())
                # [C4] MA100 & MA200 hanya dihitung jika data cukup, else N/A
                ma100 = float(close.tail(100).mean()) if n_bars >= 100 else None
                ma200 = float(close.tail(200).mean()) if n_bars >= 200 else None
                ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
                ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])

                ma100_str = f"Rp {idr(ma100)}" if ma100 else "N/A (data < 100 hari)"
                ma200_str = f"Rp {idr(ma200)}" if ma200 else "N/A (data < 200 hari)"

                # Indikator
                rsi_val    = calculate_rsi(df_raw)
                atr_val    = calculate_atr(df_raw)
                ema12      = close.ewm(span=12, adjust=False).mean()
                ema26      = close.ewm(span=26, adjust=False).mean()
                macd_line  = ema12 - ema26
                sig_line   = macd_line.ewm(span=9, adjust=False).mean()
                macd_val   = float(macd_line.iloc[-1])
                signal_val = float(sig_line.iloc[-1])
                macd_hist  = macd_val - signal_val
                bb_mid     = float(close.tail(20).mean())
                bb_std     = float(close.tail(20).std())
                bb_up      = bb_mid + 2 * bb_std
                bb_low     = bb_mid - 2 * bb_std
                avg_vol_20 = float(volume.tail(20).mean())
                vol_last   = float(volume.iloc[-1])
                vol_ratio  = vol_last / avg_vol_20 if avg_vol_20 > 0 else 1.0

                # Support & Resistance
                n52 = min(252, n_bars)
                # [C11] Gunakan percentile 95/5 untuk filter outlier spike
                high_52w = float(np.percentile(high.tail(n52), 95))
                low_52w  = float(np.percentile(low.tail(n52),  5))
                high_20  = float(high.tail(20).max())
                low_20   = float(low.tail(20).min())

                # Fibonacci
                fib_range = high_52w - low_52w
                fib_236   = high_52w - 0.236 * fib_range
                fib_382   = high_52w - 0.382 * fib_range
                fib_500   = high_52w - 0.500 * fib_range
                fib_618   = high_52w - 0.618 * fib_range

                # Weekly trend [C7] fallback yang benar
                weekly_trend = "DATA TIDAK CUKUP"
                if df_weekly is not None and len(df_weekly) >= 20:
                    try:
                        wk_close     = df_weekly["Close"].squeeze()
                        wk_ma20      = float(wk_close.tail(20).mean())
                        wk_last      = float(wk_close.iloc[-1])
                        weekly_trend = "UPTREND" if wk_last > wk_ma20 else "DOWNTREND"
                    except Exception:
                        weekly_trend = "TIDAK BISA DIHITUNG"

                # [C8] Seasonal — hanya tampilkan jika sample cukup (min 100 hari)
                seasonal_note = ""
                best_day_str  = "Data kurang"
                worst_day_str = "Data kurang"
                best_month_str = "Data kurang"
                n_seasonal     = n_bars

                if n_bars >= 100:
                    df_seas       = df_raw.copy()
                    df_seas["day"] = pd.to_datetime(df_seas.index).dayofweek
                    df_seas["ret"] = close.pct_change() * 100
                    dow_avg        = df_seas.groupby("day")["ret"].mean()
                    dow_count      = df_seas.groupby("day")["ret"].count()
                    day_names      = {0:"Senin",1:"Selasa",2:"Rabu",3:"Kamis",4:"Jumat"}
                    best_day_num   = int(dow_avg.idxmax())
                    worst_day_num  = int(dow_avg.idxmin())
                    best_day_str   = f"{day_names.get(best_day_num,'-')} (avg {dow_avg[best_day_num]:+.2f}%, n={dow_count.get(best_day_num,0)})"
                    worst_day_str  = f"{day_names.get(worst_day_num,'-')} (avg {dow_avg[worst_day_num]:+.2f}%, n={dow_count.get(worst_day_num,0)})"

                    df_seas["month"] = pd.to_datetime(df_seas.index).month
                    month_avg        = df_seas.groupby("month")["ret"].mean()
                    month_count      = df_seas.groupby("month")["ret"].count()
                    best_month_num   = int(month_avg.idxmax())
                    best_month_str   = f"{_cal.month_name[best_month_num]} (avg {month_avg[best_month_num]:+.2f}%, n={month_count.get(best_month_num,0)} hari)"

                    if n_bars < 250:
                        seasonal_note = f"⚠️ Catatan: Pola statistik dari {n_bars} hari data — perlu >500 hari untuk validitas penuh."
                    else:
                        seasonal_note = f"Data dari {n_bars} hari ({n_bars//252} tahun). Cukup untuk pola awal."
                else:
                    seasonal_note = "⚠️ Data < 100 hari — pola statistik tidak dapat dihitung secara valid."

                # ATS context (dengan guard C14)
                ats_score  = "-"
                ats_action = "Belum scan"
                ats_rr_str = "-"
                ats_entry  = "-"
                ats_sl     = "-"
                ats_target = "-"
                if has_ats_signal and scan_row is not None:
                    ats_score  = f"{float(scan_row.get('Score', 0)):.1f}"
                    ats_action = str(scan_row.get("Action", "-"))
                    ats_rr_str = f"{float(scan_row.get('RR', 0)):.1f}"
                    ats_entry  = str(scan_row.get("Entry", "-"))
                    ats_sl     = str(scan_row.get("SL", "-"))
                    ats_target = str(scan_row.get("Target", "-"))

                # Sektor
                sector = get_sector(ticker_jk)

                # ── Focus label ──────────────────────────────
                focus_map = {
                    "Citadel Technical":  ("CITADEL",    "Citadel Technical Analysis"),
                    "Bridgewater Risk":   ("BRIDGEWATER","Bridgewater Risk Assessment"),
                    "Renaissance Pattern":("RENAISSANCE","Renaissance Statistical Pattern"),
                    "Full Analysis":      ("FULL",       "Full Deep Analysis"),
                }
                focus, focus_label = focus_map.get(analysis_type, ("FULL", "Full Deep Analysis"))

                # [C3] max_tokens sesuai kebutuhan analisis
                token_map = {"CITADEL": 1800, "BRIDGEWATER": 1500, "RENAISSANCE": 1500, "FULL": 3000}
                max_tok   = token_map.get(focus, 2000)

                # ── Bangun prompt bersih ─────────────────────
                citadel_section = """
=== CITADEL TECHNICAL ANALYSIS ===
Analisis teknikal mendalam:
1. Trend direction: daily (EMA20 vs EMA50), weekly, kesimpulan overall
2. Key support & resistance dengan harga exact Rupiah
3. RSI: level sekarang, arah, sinyal (overbought/oversold/momentum)
4. MACD: apakah bullish crossover atau bearish, histogram trend
5. Bollinger Bands: posisi harga (atas/tengah/bawah), squeeze atau expansion
6. Volume: apakah mendukung pergerakan harga?
7. Fibonacci: level bounce terdekat dan resistance terdekat
8. Rekomendasi entry, SL, target dengan RR yang jelas
9. CONFIDENCE RATING akhir: STRONG BUY / BUY / NEUTRAL / SELL / STRONG SELL
""" if focus in ["CITADEL", "FULL"] else ""

                bridgewater_section = """
=== BRIDGEWATER RISK ASSESSMENT ===
1. Top 3 risiko utama posisi ini saat ini
2. Worst case scenario: jika harga turun ke mana dan berapa % loss?
3. Stress test: jika IHSG koreksi 5%, estimasi dampak ke saham ini
4. Apakah ATR saat ini mendukung position sizing yang aman?
5. Rekomendasi: apakah setup ini layak dari perspektif risk/reward?
""" if focus in ["BRIDGEWATER", "FULL"] else ""

                renaissance_section = f"""
=== RENAISSANCE STATISTICAL PATTERN ===
{seasonal_note}
1. Day-of-week pattern: {best_day_str} (terbaik), {worst_day_str} (terburuk)
2. Monthly pattern: {best_month_str}
3. Apakah volume {vol_ratio:.1f}x rata-rata hari ini statistik anomali?
4. Apakah perubahan harga {chg_last:+.2f}% termasuk outlier historis?
5. Berikan catatan jujur: apakah sample size cukup untuk kesimpulan valid?
""" if focus in ["RENAISSANCE", "FULL"] else ""

                # [C6] Deteksi konflik ATS vs AI — instruksikan AI untuk flagging
                conflict_instruction = ""
                if has_ats_signal:
                    conflict_instruction = f"""
PENTING — DETEKSI KONFLIK:
ATS SuperEngine memberi sinyal: {ats_action} (Score: {ats_score})
Jika analisis teknikal kamu berbeda dengan ATS signal (misal: kamu SELL tapi ATS EXECUTE),
WAJIB tulis blok konflik seperti ini:
⚠️ KONFLIK SIGNAL: ATS={ats_action} vs AI=[rating kamu]
Penjelasan: [jelaskan kenapa berbeda dan mana yang lebih kamu percaya berdasarkan data]
Rekomendasi: [apa yang sebaiknya dilakukan trader?]
"""

                prompt = f"""Kamu adalah quantitative analyst senior spesialis saham Indonesia yang jujur dan tidak pernah memberi false hope.

KONTEKS PASAR INDONESIA (wajib dipertimbangkan):
- Pasar IHSG dipengaruhi: BI Rate, nilai tukar rupiah, sentimen asing (net buy/sell), aksi korporasi
- Banyak saham mid-cap ISSI dipengaruhi "bandar lokal" — volume spike tidak selalu smart money global
- Likuiditas lebih rendah dari bursa global — spread lebih lebar, slippage lebih besar
- Data yang kamu terima adalah CLOSING HARGA TERAKHIR, bukan real-time hari ini

SAHAM: {ticker_input} | SEKTOR: {sector}
{data_label}

DATA TEKNIKAL:
Harga terakhir  : Rp {idr(last_price)}
Perubahan       : {chg_last:+.2f}% (closing {data_date} vs sebelumnya)
Volume rasio    : {vol_ratio:.2f}x rata-rata 20 hari

MOVING AVERAGES:
EMA20 : Rp {idr(ema20)} — harga {'ATAS' if last_price > ema20 else 'BAWAH'} EMA20
EMA50 : Rp {idr(ema50)} — harga {'ATAS' if last_price > ema50 else 'BAWAH'} EMA50
MA50  : Rp {idr(ma50)} | MA100: {ma100_str} | MA200: {ma200_str}
Trend mingguan  : {weekly_trend}

INDIKATOR:
RSI(14)  : {rsi_val:.1f} — {'⚠️ Overbought' if rsi_val > 70 else ('⚠️ Oversold' if rsi_val < 30 else 'Normal')}
MACD     : {macd_val:.4f} | Signal: {signal_val:.4f} | Hist: {macd_hist:.4f} ({'Bullish' if macd_hist > 0 else 'Bearish'})
Bollinger: Upper {idr(bb_up)} | Mid {idr(bb_mid)} | Lower {idr(bb_low)}
ATR(14)  : Rp {idr(atr_val)} ({atr_val/last_price*100:.2f}% dari harga)

SUPPORT & RESISTANCE (outlier-adjusted):
52W High (P95): Rp {idr(high_52w)} | 52W Low (P5): Rp {idr(low_52w)}
Resistance 20H: Rp {idr(high_20)} | Support 20H: Rp {idr(low_20)}

FIBONACCI (dari range 52W adjusted):
23.6%: Rp {idr(fib_236)} | 38.2%: Rp {idr(fib_382)} | 50%: Rp {idr(fib_500)} | 61.8%: Rp {idr(fib_618)}

ATS SUPERENGINE SIGNAL:
Action : {ats_action} | Score: {ats_score} | RR: {ats_rr_str}
Entry  : {ats_entry} | SL: {ats_sl} | Target: {ats_target}

{conflict_instruction}

{citadel_section}
{bridgewater_section}
{renaissance_section}

ATURAN OUTPUT WAJIB:
- Bahasa Indonesia yang jelas
- Semua harga dalam Rupiah (format: Rp 1.500 bukan 1500)
- Jangan memberi false confidence — jika data tidak cukup, katakan apa adanya
- Akhiri dengan: KESIMPULAN FINAL: [MASUK / TUNDA / HINDARI] + alasan 1-2 kalimat
- Jika ada konflik ATS vs AI, wajib tampilkan blok konflik seperti instruksi di atas"""

                # ── Panggil AI provider (Claude/Gemini) ──────
                system_prompt = (
                    "Kamu adalah quantitative analyst senior pasar saham Indonesia. "
                    "Jujur, berbasis data, tidak ada hype, selalu sebut risiko. "
                    "Jika data tidak cukup untuk kesimpulan valid, katakan tegas."
                )

                ok, result, provider_used = call_ai(
                    system_prompt=system_prompt,
                    user_prompt=prompt,
                    max_tokens=max_tok,
                )

                if ok:
                    # ── Tampilkan hasil ──────────────────────
                    provider_emoji = "🟣" if provider_used == "anthropic" else "🔵"
                    st.markdown(f"### 🔬 {focus_label} — {ticker_input}")
                    st.caption(
                        f"Dianalisis: {datetime.now(WIB).strftime('%d %b %Y %H:%M WIB')}  |  "
                        f"{data_label}  |  {n_bars} hari data  |  "
                        f"AI: {provider_emoji} {provider_used.title()}"
                    )

                    # [C6] Deteksi konflik otomatis dari output AI
                    if "KONFLIK SIGNAL" in result or "konflik" in result.lower():
                        st.error(
                            "⚠️ **PERHATIAN: AI mendeteksi konflik antara sinyal ATS dan analisis teknikal!** "
                            "Baca penjelasan konflik di bawah sebelum mengambil keputusan."
                        )

                    st.markdown("---")

                    # Mini metrics
                    d1, d2, d3, d4, d5 = st.columns(5)
                    d1.metric("RSI",       f"{rsi_val:.1f}",
                              "Overbought" if rsi_val > 70 else ("Oversold" if rsi_val < 30 else "Normal"))
                    d2.metric("MACD",      "Bullish" if macd_hist > 0 else "Bearish",
                              f"hist {macd_hist:+.4f}")
                    d3.metric("vs EMA50",  f"{((last_price/ema50)-1)*100:+.1f}%",
                              "Di atas" if last_price > ema50 else "Di bawah")
                    d4.metric("Vol Ratio", f"{vol_ratio:.1f}x",
                              "⚠️ Spike" if vol_ratio > 1.8 else "Normal")
                    d5.metric("Weekly",    weekly_trend,
                              f"Data {data_date}")

                    st.markdown("---")
                    st.markdown(result)

                    # Cache
                    if "deep_analysis_cache" not in st.session_state:
                        st.session_state.deep_analysis_cache = {}
                    st.session_state.deep_analysis_cache[ticker_input] = {
                        "result":     result,
                        "time":       datetime.now(WIB).strftime("%d %b %Y %H:%M"),
                        "type":       focus_label,
                        "data_date":  data_date,
                        "n_bars":     n_bars,
                        "provider":   provider_used,
                    }

                else:
                    st.error(f"❌ AI error: {result}")

            except Exception as e:
                st.error(f"Error analisis {ticker_input}: {str(e)}")

    # ── Riwayat analisis ─────────────────────────────────────
    if "deep_analysis_cache" in st.session_state and st.session_state.deep_analysis_cache:
        st.markdown("---")
        st.markdown("### 📁 Riwayat Analisis Sesi Ini")
        for tkr, cache in st.session_state.deep_analysis_cache.items():
            label = (f"**{tkr}** — {cache['type']} — {cache['time']} "
                     f"| Data: {cache.get('data_date','-')} | {cache.get('n_bars','-')} hari")
            with st.expander(label):
                st.markdown(cache["result"])


# ─────────────────────────────────────────────────────────────
# TAB 6 — 🦅 FALCON HUNTER
# ─────────────────────────────────────────────────────────────
with tabs[6]:
    from falcon_engine import (
        FalconParams, FalconResult,
        run_falcon_scan, format_falcon_telegram,
        FALCON_DEFAULT_WATCHLIST,
    )

    st.markdown("## 🦅 Falcon Hunter — Sharia Stock Scanner")
    st.caption("Strategi Falcon: BREAKOUT + BOUNCE | Engine terpisah dari ATS | Risk 1% per trade")

    # ── Sidebar config ────────────────────────────────────────
    with st.expander("⚙️ Pengaturan Falcon", expanded=False):
        col_w1, col_w2 = st.columns(2)
        with col_w1:
            falcon_balance = st.number_input(
                "Modal (Rp)", min_value=100_000, max_value=1_000_000_000,
                value=int(st.session_state.get("balance", 800_000)),
                step=100_000, format="%d",
                help="Modal aktif untuk kalkulasi lot Falcon"
            )
            vol_brk = st.slider("Vol Breakout minimum (×avg20)", 1.0, 4.0, 1.8, 0.1)
            rsi_cap = st.slider("RSI maksimum", 55, 85, 70, 1)
        with col_w2:
            risk_pct_falcon = st.slider("Risk per trade (%)", 0.5, 3.0, 1.0, 0.25)
            gap_max  = st.slider("Max gap open (%)", 1.0, 5.0, 3.0, 0.5)
            use_trend = st.checkbox("Gunakan filter trend MA20/50/200", value=True)

        st.markdown("**Watchlist (pisahkan dengan koma):**")
        wl_raw = st.text_area(
            "Ticker",
            value=", ".join(FALCON_DEFAULT_WATCHLIST),
            height=100, label_visibility="collapsed"
        )
        watchlist_input = [t.strip().upper() for t in wl_raw.split(",") if t.strip()]

    # ── Scan button ───────────────────────────────────────────
    col_btn1, col_btn2, _ = st.columns([1, 1, 3])
    with col_btn1:
        do_scan = st.button("🦅 Jalankan Falcon Scan", type="primary",
                            use_container_width=True)
    with col_btn2:
        only_setup = st.checkbox("Tampilkan setup saja", value=True)

    # ── Run scan ──────────────────────────────────────────────
    if do_scan:
        falcon_params = FalconParams(
            vol_breakout_mult = vol_brk,
            rsi_max           = rsi_cap,
            risk_pct          = risk_pct_falcon / 100,
            max_gap_pct       = gap_max,
            use_trend_filter  = use_trend,
        )

        prog_bar  = st.progress(0, text="🦅 Memulai scan Falcon...")
        status_ph = st.empty()

        def _progress(i, n, tkr):
            pct = int(i / n * 100) if n > 0 else 100
            prog_bar.progress(pct, text=f"🦅 Scanning {tkr}... ({i}/{n})")

        ihsg_status, ihsg_score, falcon_results = run_falcon_scan(
            watchlist  = watchlist_input,
            balance    = falcon_balance,
            params     = falcon_params,
            progress_cb= _progress,
        )
        prog_bar.empty()

        # Simpan ke session state
        st.session_state["falcon_results"]     = falcon_results
        st.session_state["falcon_ihsg_status"] = ihsg_status
        st.session_state["falcon_ihsg_score"]  = ihsg_score

        # Kirim Telegram kalau ada setup
        setup_list = [r for r in falcon_results if r.setup != "-"]
        if setup_list:
            msg = format_falcon_telegram(falcon_results, ihsg_status, falcon_balance)
            if msg:
                send_telegram(msg)
                status_ph.success(f"🦅 Telegram terkirim — {len(setup_list)} setup ditemukan")
        else:
            status_ph.info("🦅 Scan selesai — tidak ada setup hari ini")

    # ── Display results ───────────────────────────────────────
    if "falcon_results" in st.session_state and st.session_state.falcon_results:
        falcon_results = st.session_state["falcon_results"]
        ihsg_status    = st.session_state.get("falcon_ihsg_status", "NEUTRAL")
        ihsg_score     = st.session_state.get("falcon_ihsg_score", 0.5)

        # IHSG banner
        ihsg_color = {"BULLISH": "🟢", "NEUTRAL": "🟡", "BEARISH": "🔴"}
        ihsg_em    = ihsg_color.get(ihsg_status, "⬜")
        ihsg_action = {
            "BULLISH": "Full size, scan agresif — market mendukung",
            "NEUTRAL": "Size ½ — hanya ambil Falcon Score terbaik",
            "BEARISH": "🛑 Falcon istirahat — paper trade only",
        }.get(ihsg_status, "-")

        col_ih1, col_ih2, col_ih3 = st.columns(3)
        col_ih1.metric("IHSG Status", f"{ihsg_em} {ihsg_status}")
        setup_count = sum(1 for r in falcon_results if r.setup != "-")
        brk_count   = sum(1 for r in falcon_results if r.setup == "BRK")
        bnc_count   = sum(1 for r in falcon_results if r.setup == "BNC")
        col_ih2.metric("Setup Ditemukan", f"{setup_count} / {len(falcon_results)}")
        col_ih3.metric("BRK / BNC", f"{brk_count} / {bnc_count}")
        st.info(f"📌 **Falcon guidance:** {ihsg_action}")
        st.markdown("---")

        # Filter display
        display_list = (
            [r for r in falcon_results if r.setup != "-"]
            if only_setup else falcon_results
        )

        if not display_list:
            st.warning("Tidak ada setup yang memenuhi kriteria Falcon saat ini.")
        else:
            # ── Setup cards ─────────────────────────────────
            setup_results = [r for r in display_list if r.setup != "-"]
            if setup_results:
                st.markdown("### 🎯 Setup Aktif")
                for r in setup_results:
                    em   = "🟢" if r.setup == "BRK" else "🔵"
                    mode = "BREAKOUT" if r.setup == "BRK" else "BOUNCE"
                    rr_actual = round((r.t2 - r.entry) / max(r.entry - r.sl, 1), 1)

                    with st.container(border=True):
                        c1, c2, c3, c4, c5 = st.columns([2, 1.5, 1.5, 1.5, 1.5])
                        c1.markdown(f"### {em} {r.ticker}")
                        c1.caption(f"{mode} | Score: **{r.falcon_score:.2f}**")
                        c2.metric("Entry", f"Rp {int(r.entry):,}")
                        c2.caption(f"Vol: {r.vol_ratio:.1f}× | RSI: {r.rsi}")
                        c3.metric("Stop Loss", f"Rp {int(r.sl):,}",
                                  delta=f"{((r.sl-r.entry)/r.entry*100):.1f}%",
                                  delta_color="inverse")
                        c4.metric("T1 (1R)", f"Rp {int(r.t1):,}")
                        c4.metric("T2 (2R)", f"Rp {int(r.t2):,}")
                        c5.metric("RR aktual", f"{rr_actual}×")
                        c5.metric("Lot", f"{r.lot} lot")

                        # Detail row
                        with st.expander("Detail teknikal"):
                            d1, d2, d3 = st.columns(3)
                            d1.markdown(f"**Trend Score:** {r.trend_score:.2f}")
                            d1.markdown(f"**MA20:** {int(r.ma20):,}")
                            d1.markdown(f"**MA50:** {int(r.ma50):,}")
                            d1.markdown(f"**MA200:** {int(r.ma200):,}")
                            d2.markdown(f"**Upper shadow:** {r.upper_shadow:.1f}%")
                            d2.markdown(f"**Body ratio:** {r.body_ratio:.1f}%")
                            d2.markdown(f"**Gap open:** {r.gap_pct:+.2f}%")
                            d3.markdown(f"**Resistance 10D:** {int(r.resistance):,}")
                            d3.markdown(f"**Support 20D:** {int(r.support):,}")
                            d3.markdown(f"**Risk/trade:** Rp {int(r.risk_rp):,}")

            # ── Full ranking table ───────────────────────────
            st.markdown("---")
            st.markdown("### 📊 Ranking Falcon Score — Semua Ticker")

            table_data = []
            for r in display_list:
                em = ("🟢 BRK" if r.setup == "BRK"
                      else "🔵 BNC" if r.setup == "BNC"
                      else "—")
                rr_t2 = round((r.t2 - r.entry) / max(r.entry - r.sl, 1), 1) if r.entry > r.sl else 0
                table_data.append({
                    "Ticker"       : r.ticker,
                    "Setup"        : em,
                    "Close"        : f"Rp {int(r.close):,}" if r.close else "-",
                    "Score 🦅"     : f"{r.falcon_score:.2f}",
                    "Vol×"         : f"{r.vol_ratio:.2f}×",
                    "RSI"          : f"{r.rsi:.1f}",
                    "Trend"        : f"{r.trend_score:.2f}",
                    "Entry"        : f"Rp {int(r.entry):,}" if r.entry else "-",
                    "SL"           : f"Rp {int(r.sl):,}"    if r.sl    else "-",
                    "T1"           : f"Rp {int(r.t1):,}"    if r.t1    else "-",
                    "T2"           : f"Rp {int(r.t2):,}"    if r.t2    else "-",
                    "RR"           : f"{rr_t2}×"            if rr_t2   else "-",
                    "Lot"          : r.lot if r.lot else "-",
                    "Gap%"         : f"{r.gap_pct:+.1f}%",
                    "Ket"          : r.error if r.error else "✅",
                })

            df_tbl = pd.DataFrame(table_data)
            st.dataframe(
                df_tbl, use_container_width=True, hide_index=True,
                column_config={
                    "Score 🦅": st.column_config.ProgressColumn(
                        "Score 🦅", min_value=0, max_value=1, format="%.2f"
                    ),
                    "Setup": st.column_config.TextColumn("Setup", width="small"),
                }
            )

            # ── Falcon SOP reminder ──────────────────────────
            st.markdown("---")
            with st.expander("📋 Falcon SOP — Rules Tidak Bisa Ditawar", expanded=False):
                st.markdown("""
**Entry rules:**
- Gap up > 2% → **SKIP**, R:R rusak
- Gap up 1–2% → tunggu pullback dulu
- Gap down ≤ 1% → opportunity bagus
- Entry hanya 09:00 – 10:30 WIB (prime time)

**Exit rules:**
- **T1 (1R):** Sell 50% + geser SL ke breakeven
- **T2 (2R):** Sell sisa atau aktifkan trailing
- **Time stop:** 5 hari tidak ke T1 → exit di close H+5
- **Pattern break:** Bearish engulfing + vol tinggi → exit penuh

**Risk rules (tidak bisa dilanggar):**
- ❌ Tidak entry tanpa SL hard stop di broker
- ❌ Tidak average down
- ❌ Tidak revenge trading
- ❌ Loss 3× beruntun → stop hari itu
- ❌ IHSG BEARISH → Falcon istirahat
""")



# ─────────────────────────────────────────────────────────────
# TAB 7 — 🎯 BANDAR HUNTER
# ─────────────────────────────────────────────────────────────
with tabs[7]:
    from bandar_hunter import (
        run_bandar_scan, bandar_hunter_job,
        format_bandar_telegram, BandarSignal,
        SIGNAL_EDUCATION,
    )

    st.markdown("## 🎯 Bandar Hunter — Institutional Movement Detector")
    st.caption(
        "Deteksi pergerakan institusional via anomali volume + price pada data 5 menit. "
        "Input dari kandidat ATS scan hari ini. Bukan broker flow — ini adalah proxy."
    )

    # ── Edukasi banner ─────────────────────────────────────────
    with st.expander("📚 Memahami Pergerakan Bandar — Baca Dulu", expanded=False):
        st.markdown("""
### Bagaimana Bandar Bergerak?

Institusi / bandar tidak bisa membeli jutaan lembar saham sekaligus — 
harga akan melonjak sendiri sebelum mereka selesai akumulasi. 
Karena itu mereka bergerak dengan **pola yang bisa dideteksi**:

---

#### 🔄 4 Fase Siklus Bandar (Wyckoff)

| Fase | Nama | Ciri Volume | Ciri Harga | Aksi |
|---|---|---|---|---|
| 1 | **Akumulasi** | Naik perlahan, konsisten | Sideways, volatilitas rendah | Monitor — siapkan entry |
| 2 | **Markup Awal** | Meledak tiba-tiba | Loncat impulsif, minim pullback | ⚡ Entry zone terbaik |
| 3 | **Distribusi** | Naik tapi mulai turun | Harga masih naik/flat | ⚠️ Profit taking |
| 4 | **Markdown** | Rendah atau spike sesaat | Turun konsisten | ❌ Jangan masuk |

---

#### 🔍 Yang Bisa Dideteksi di Sini (Data 5 Menit)

**⚡ Initial Markup** — Paling actionable
> Volume meledak 4×+ dalam satu candle, harga loncat >1% dalam 15 menit, 
> tidak ada pullback signifikan. Ini momen bandar mulai *push* harga.

**🤫 Akumulasi Senyap** — Setup terbaik untuk entry
> Volume di atas rata-rata 3-5 candle berturut-turut, harga bergerak 
> sideways atau naik tipis. Bandar sedang *kumpulkan* saham diam-diam.

**🔊 Volume Anomali** — Perlu konfirmasi arah
> Volume ekstrem (5×+) tapi harga tidak bergerak. Bisa akumulasi, 
> bisa distribusi. **Tunggu konfirmasi** sebelum aksi apapun.

**🔴 Distribusi** — Sinyal waspada
> Harga masih naik tapi volume mulai turun. Bandar sudah mulai *jual* 
> ke retail yang FOMO. Hindari entry baru.

---

#### ⚠️ Keterbatasan yang Harus Dipahami

1. **Ini bukan data broker flow** — broker flow (RTI/Stockbit) tidak tersedia
2. **False positive di saham tidak likuid** — spread lebar bisa memicu sinyal palsu
3. **Selalu konfirmasi di D1** sebelum eksekusi — jangan trading hanya dari sinyal ini
4. **Ini adalah alat deteksi, bukan rekomendasi beli/jual**
        """)

    st.markdown("---")

    # ── Input ticker ───────────────────────────────────────────
    col_inp1, col_inp2 = st.columns([2, 1])

    with col_inp1:
        # Auto-populate dari hasil scan ATS hari ini
        ats_candidates = []
        if "scan_result" in st.session_state and not st.session_state.scan_result.empty:
            ats_candidates = st.session_state.scan_result["Ticker"].tolist()[:8]

        default_tickers = (
            ", ".join(ats_candidates) if ats_candidates
            else "BRIS, ADRO, ANTM, TLKM, UNTR, INDF, BBRI, BMRI"
        )

        bh_tickers_raw = st.text_input(
            "📌 Ticker yang dipantau (pisahkan dengan koma)",
            value=default_tickers,
            help="Default: kandidat dari ATS scan hari ini. Edit sesuai kebutuhan."
        )
        bh_tickers = [t.strip().upper() for t in bh_tickers_raw.split(",") if t.strip()]

    with col_inp2:
        min_signal_filter = st.selectbox(
            "Filter minimum sinyal",
            options=["MARKUP_AWAL", "AKUMULASI_SENYAP", "VOLUME_ANOMALI", "NONE"],
            index=1,
            format_func=lambda x: {
                "MARKUP_AWAL"     : "⚡ Initial Markup saja",
                "AKUMULASI_SENYAP": "🤫 Akumulasi + Markup",
                "VOLUME_ANOMALI"  : "🔊 Semua anomali",
                "NONE"            : "😴 Semua (termasuk normal)",
            }.get(x, x)
        )

    col_btn_bh1, col_btn_bh2, _ = st.columns([1, 1, 3])
    with col_btn_bh1:
        do_bh_scan = st.button("🎯 Scan Bandar Sekarang",
                               type="primary", use_container_width=True)
    with col_btn_bh2:
        send_tg_bh = st.checkbox("Kirim Telegram", value=True)

    # ── Run scan ──────────────────────────────────────────────
    if do_bh_scan:
        if not bh_tickers:
            st.warning("Masukkan minimal 1 ticker.")
        else:
            bh_prog  = st.progress(0, text="🎯 Memulai Bandar Hunter scan...")
            bh_ph    = st.empty()

            def _bh_progress(i, n, tkr):
                pct = int(i / n * 100) if n > 0 else 100
                bh_prog.progress(pct, text=f"🎯 Scanning {tkr}... ({i}/{n})")

            bh_results = run_bandar_scan(
                tickers    = bh_tickers,
                min_signal = min_signal_filter,
                progress_cb= _bh_progress,
            )
            bh_prog.empty()

            st.session_state["bh_results"]    = bh_results
            st.session_state["bh_scan_time"]  = datetime.now(WIB).strftime("%H:%M WIB")
            st.session_state["bh_tickers"]    = bh_tickers

            actionable = [r for r in bh_results if r.is_actionable and not r.error]
            if actionable and send_tg_bh:
                msg = format_bandar_telegram(actionable)
                if msg:
                    send_telegram(msg)
                    bh_ph.success(f"🎯 Telegram terkirim — {len(actionable)} sinyal actionable")
            elif not actionable:
                bh_ph.info("🎯 Scan selesai — tidak ada sinyal actionable saat ini")

    # ── Display results ───────────────────────────────────────
    if "bh_results" in st.session_state and st.session_state.bh_results:
        bh_results  = st.session_state["bh_results"]
        bh_scantime = st.session_state.get("bh_scan_time", "-")

        st.caption(f"Hasil scan terakhir: {bh_scantime} | {len(bh_results)} ticker dipantau")
        st.markdown("---")

        # ── Sinyal cards ───────────────────────────────────────
        actionable = [r for r in bh_results if r.is_actionable and not r.error]
        if actionable:
            st.markdown("### 🚨 Sinyal Actionable")
            for s in actionable:
                edu = s.education
                conf_color = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "⚪"}
                c_em = conf_color.get(s.confidence, "⚪")

                with st.container(border=True):
                    bh_c1, bh_c2, bh_c3, bh_c4 = st.columns([2, 1.5, 1.5, 2])
                    bh_c1.markdown(f"### {edu['icon']} {s.ticker}")
                    bh_c1.markdown(f"**{edu['label']}**")
                    bh_c1.caption(f"{c_em} Confidence: {s.confidence}")
                    bh_c2.metric("Harga", f"Rp {int(s.last_price):,}")
                    bh_c2.metric("Vol Ratio", f"{s.vol_ratio:.1f}×")
                    bh_c3.metric("Chg 3 candle", f"{s.price_chg_3c:+.2f}%")
                    bh_c3.metric("Vol Trend", s.vol_trend)
                    bh_c4.info(f"**Artinya:** {edu['arti']}")
                    bh_c4.success(f"**Aksi:** {edu['aksi']}")

                    with st.expander("📊 Detail + Edukasi"):
                        d1, d2, d3 = st.columns(3)
                        d1.markdown(f"**Pullback ratio:** {s.pullback:.2f}")
                        d1.markdown(f"**Consec vol naik:** {s.consec_above} candle")
                        d1.markdown(f"**FVG:** {'✅ Ada' if s.fvg else '❌ Tidak'}")
                        d2.markdown(f"**Chg 1 candle:** {s.price_chg_1c:+.2f}%")
                        d2.markdown(f"**Scan time:** {s.timestamp}")
                        d3.warning(f"⚠️ **Risiko:** {edu['risiko']}")
                        d3.markdown(f"**Pola:** {edu['pola']}")

        # ── Full summary table ─────────────────────────────────
        st.markdown("---")
        st.markdown("### 📊 Semua Ticker yang Dipantau")

        tbl_data = []
        for s in bh_results:
            edu = s.education
            tbl_data.append({
                "Ticker"    : s.ticker,
                "Sinyal"    : f"{edu['icon']} {edu['label']}",
                "Confidence": s.confidence,
                "Harga"     : f"Rp {int(s.last_price):,}" if s.last_price else "-",
                "Vol×"      : f"{s.vol_ratio:.1f}×",
                "Chg 3C"    : f"{s.price_chg_3c:+.2f}%",
                "Vol Trend" : s.vol_trend,
                "Consec↑"   : s.consec_above,
                "FVG"       : "✅" if s.fvg else "-",
                "Pullback"  : f"{s.pullback:.2f}",
                "Aksi"      : edu["aksi"] if s.is_actionable else "-",
                "Error"     : s.error if s.error else "✅",
            })

        df_bh = pd.DataFrame(tbl_data)
        st.dataframe(df_bh, use_container_width=True, hide_index=True)

        # ── Legenda ────────────────────────────────────────────
        st.markdown("---")
        with st.expander("📖 Legenda Kolom", expanded=False):
            st.markdown("""
| Kolom | Penjelasan |
|---|---|
| **Vol×** | Rasio volume candle terakhir vs rata-rata 20 candle. >4× = anomali |
| **Chg 3C** | Perubahan harga 3 candle (15 menit) terakhir |
| **Vol Trend** | Apakah volume 5 candle terakhir NAIK, TURUN, atau FLAT |
| **Consec↑** | Berapa candle berturut-turut volume di atas rata-rata |
| **FVG** | Fair Value Gap — ada gap harga yang belum terisi |
| **Pullback** | 0.0 = tidak ada pullback (bullish). 1.0 = full pullback (bearish) |
| **Confidence** | HIGH = semua kondisi kuat. MEDIUM = sebagian. LOW = lemah |
            """)

st.divider()
st.caption(
    f"ATS SuperEngine {APP_VERSION}  |  Update terakhir: {APP_UPDATED}  |  "
    "ISSI Syariah Scanner  |  Bukan rekomendasi investasi"
)