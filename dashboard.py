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
try:
    import pdfplumber
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False
# ============================================================
# HOTFIX V5.6.9: HARDCODE BYPASS UNTUK BACKGROUND THREAD
# ============================================================
import os
import streamlit as st

# ============================================================
# KONFIGURASI
# ============================================================
FINNHUB_API_KEY   = os.environ.get("FINNHUB_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")
STATE_FILE        = "ats_state.json"
JOURNAL_FILE      = "journal.csv"

# Baca Telegram dari st.secrets (Streamlit Cloud) atau os.environ (Railway/Render)
def _get_secret(key: str) -> str:
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, "")

TELEGRAM_TOKEN = _get_secret("TELEGRAM_TOKEN")
TELEGRAM_CHAT  = _get_secret("TELEGRAM_CHAT")
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
APP_VERSION  = "V6.3.0"
APP_UPDATED  = "11 Jul 2026"

VERSION_HISTORY = [
    {
        "versi":   "V6.0.0",
        "tanggal": "10 Jul 2026",
        "tipe":    "Major Tuning — 9 Komponen Engine (V5.8.2 → V6.0.0)",
        "ringkasan": "Rewrite total logika analisa: scoring 5 komponen, weighted confluence, multi-TF breakout, bandar 2.5x + price action, dynamic freshness, RSI divergence, dynamic signal lock, intraday retry, leading regime detection",
        "detail": [
            "[1] SCORING: stack bonus (momentum/accum/ft/intraday/extra) DIHAPUS — double counting",
            "    5 komponen: Trend(prob+runner), Bandar, Breakout, Momentum(mom+ft), RR. Bobot regime-adaptif, sum=1.0",
            "    Liquidity DIHAPUS dari score (konstan utk semua survivor = inflasi murni, zero discrimination)",
            "    Max score dijamin <= 100 by construction, bukan by cap",
            "[2] CONFLUENCE: count sederhana → weighted. Bandar 2x, Breakout 2x (WEAK=1x), Momentum/RR/Accum/Uptrend 1x",
            "    Max 8 poin. Pass: >= 70% (5.6/8) normal, >= 55% (4.4/8) bear mode",
            "[3] BREAKOUT: multi-TF 10d/20d + candle body quality → STRONG/VALID/WEAK/WAIT",
            "    STRONG: tembus 20d high, vol>1.5x, body>=50% range, close upper half",
            "    VALID: tembus 10d high, vol>1.3x, close bukan di ekor bawah",
            "[4] BANDAR: spike threshold 1.8x → 2.5x + WAJIB price action confirmation",
            "    Spike tanpa konfirmasi harga (close merah/ekor bawah) = 0 poin, bukan +2",
            "[5] FRESHNESS: hard cap statis → dynamic limit (ATR-scaled) + pullback detection",
            "    Harga turun >=1.5% dari high hari ini = pullback sehat → limit +2%",
            "[6] RSI GATE: range adaptif + bearish divergence detection (RSI>65, price HH vs RSI LH → reject)",
            "[7] SIGNAL LOCK: fixed 600s → dynamic. EXECUTE NOW=60min, EXECUTE=20min + upgrade bypass",
            "    (EXECUTE → EXECUTE NOW menembus lock, downgrade tidak)",
            "[8] INTRADAY: retry 2x + fallback interval 15m saat 5m gagal — no more silent fail",
            "[9] REGIME: reaktif (breadth only) → leading (breadth + new high/low 20d + %above EMA20)",
        ]
    },
    {
        "versi":   "V5.8.2",
        "tanggal": "18 Jun 2026",
        "tipe":    "Bug Fix + Feature — Near-Low Alert",
        "ringkasan": "Hapus 18 Jun dari IDX holiday (IDX buka normal) + Near-Low intraday alert ke Telegram",
        "detail": [
            "[FIX #1] Hapus date(2026, 6, 18) dari IDX_HOLIDAYS",
            "  Root cause: 18 Juni dikategorikan cuti bersama Idul Adha tapi IDX confirmed buka",
            "  Dampak: header menampilkan 'Libur Nasional', auto-scan skip hari ini",
            "  Fix: hanya 17 Juni (Idul Adha) yang libur, 18 Juni buka normal",
            "[FIX #2] Near-Low Early Warning di intraday_refresh_job (setiap 15 menit)",
            "  Trigger: harga intraday dalam 0.5% dari Low kemarin (test support) atau di bawahnya (breakdown)",
            "  Threshold 0.5%: lebih kecil = noise spread IDX; lebih besar = sudah terlambat",
            "  Alert 1x per ticker per hari (pakai near_low_alerts state)",
            "  Telegram format: harga saat ini, low kemarin, jarak %, status BREAKDOWN vs TEST SUPPORT",
            "  Pesan: HINDARI BUY + evaluasi SL jika pegang posisi",
            "  Silent fail — tidak crash intraday refresh jika daily data gagal fetch",
        ]
    },
    {
        "versi":   "V5.8.1",
        "tanggal": "10 Jun 2026",
        "tipe":    "Filter Calibration — Zero Signal Fix + Breakdown Scanner",
        "ringkasan": "4 surgical fix untuk root cause zero signal saat bullish + tambah breakdown scanner (break low kemarin)",
        "detail": [
            "[FIX #1] RSI gate BULLISH: min 42 → 38",
            "  Root cause: saham pre-breakout sering RSI 35-42 saat akumulasi",
            "  42 terlalu tinggi untuk menangkap setup sebelum breakout terjadi",
            "  38 = konsisten dengan SIDEWAYS, tidak ada alasan BULLISH lebih ketat",
            "[FIX #2] Confluence minimum BULLISH: 4/6 → 3/6",
            "  Root cause: ironi — saat market BULLISH sistem justur paling ketat",
            "  Saat bullish bandar sering score 0 (volume sudah flat setelah spike kemarin)",
            "  Dengan bandar=0: max 5 signal. Butuh 4 tapi borderline, terlalu sering gagal",
            "  3/6 di semua regime = lebih konsisten dan tidak bias terhadap kondisi terbaik",
            "[FIX #3] WEAK breakout guard dilonggarkan: OR(momentum==2, intraday>=2, ft==2) → akumulasi sufficient",
            "  Root cause: intraday sering return 0 di awal sesi (data 5m belum cukup)",
            "  Kondisi tiga-way OR semua jarang terpenuhi jam 09:05-09:30",
            "  Fix: WEAK lolos jika bandar >= 1 OR momentum >= 1 (bukan strict 2)",
            "  Ini cukup — liquidity trap dan fake_breakout sudah guard false signal",
            "[FIX #4] get_bear_mode_params: conf_min BULLISH konsisten",
            "  bear_mode_params tidak override conf_min BULLISH dengan benar sebelumnya",
            "[NEW] scan_breakout_yesterday_low(): breakdown scanner — close < low H-1",
            "  Terpisah dari scanner utama (bearish thesis, beda philosophy)",
            "  Kirim Telegram dengan label BREAKDOWN untuk diferensiasi",
            "  Ditampilkan di tab Breakout Scan sebagai section kedua",
        ]
    },
    {
        "versi":   "V5.8.0",
        "tanggal": "4 Jun 2026",
        "tipe":    "Feature — Breakout Yesterday High Scanner",
        "ringkasan": "Scanner baru: breakout harga tertinggi kemarin, scan otomatis per 15 menit jam 09:00–10:00, konfirmasi jam 10:00, report ke Telegram",
        "detail": [
            "[NEW] scan_breakout_yesterday_high(): loop ISSI universe, close > high H-1",
            "[NEW] Scheduler: scan tiap 15 menit jam 09:00-10:00 WIB (09:00, 09:15, 09:30, 09:45, 10:00)",
            "[NEW] Tab 🚀 BREAKOUT SCAN: UI manual trigger + live result table",
            "[NEW] Format Telegram: list breakout sorted by % breakout terbesar",
            "[NEW] Filter volume > 0 untuk skip saham zombie",
            "[INFO] Raw breakout — tidak ada filter tambahan, konfirmasi manual TF 15m",
        ]
    },
    {
        "versi":   "V5.7.0",
        "tanggal": "2 Jun 2026",
        "tipe":    "Bear Mode — Adaptive Threshold for DISTRIBUTION Regime",
        "ringkasan": "Threshold otomatis longgar saat regime DISTRIBUTION: RR 1.8→1.3, Confluence 3→2, Breakout WAIT+bandar diizinkan",
        "detail": [
            "[BEAR MODE] is_bear_mode() aktif saat regime=DISTRIBUTION",
            "[BEAR MODE] RR minimum: 1.8 → 1.3 saat DISTRIBUTION",
            "[BEAR MODE] Confluence minimum: 3/6 → 2/6 saat DISTRIBUTION",
            "[BEAR MODE] Breakout WAIT + bandar>=2 diizinkan lolos saat DISTRIBUTION",
            "[BEAR MODE] Threshold kembali normal otomatis saat regime SIDEWAYS/BULLISH",
            "[FIX] get_bear_mode_params() sebagai single source of truth semua threshold",
        ]
    },
    {
        
        "versi":   "V5.6.6",
        "tanggal": "21 Mei 2026",
        "tipe":    "Docs — Strict Rules BH + Pine Script BH v2",
        "ringkasan": "Update How To Use BH dengan 7 strict rules dari pengalaman live trading + Pine Script v2 fix label floating",
        "detail": [
            "[DOCS] 7 Strict Rules BH berdasarkan pelajaran live trading:",
            "  Rule 1: Satu hari sinyal belum cukup — tunggu 3-5 hari konsisten",
            "  Rule 2: Konfirmasi IPOT wajib (bid ratio, accum/dist, broker summary)",
            "  Rule 3: Konfirmasi D1 wajib sebelum entry",
            "  Rule 4: Wash trading detection — 1 broker beli=jual = sinyal palsu",
            "  Rule 5: Vol spike + harga turun = distribusi, bukan akumulasi",
            "  Rule 6: Cek corporate action sebelum interpretasi",
            "  Rule 7: SL terpasang langsung setelah fill tanpa pengecualian",
            "[DOCS] Kasus nyata: ADRO pump&dump, KBLI wash trading, AKRA/BRIS ex-dividen",
            "[DOCS] Alur 6 step penggunaan BH yang benar",
            "[DOCS] Panduan BH Pine Script TV + setup alert",
            "[PINE] Bandar Hunter v2.0 — fix label yloc.belowbar/abovebar",
        ]
    },
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
    date(2026, 5, 20),  date(2026, 5, 22),
    date(2026, 5, 27),  date(2026, 5, 28),  # Idul Adha + cuti bersama
    date(2026, 6, 1),
    date(2026, 6, 17),                        # Idul Adha — 18 Juni IDX buka normal
    date(2026, 6, 26),  date(2026, 6, 27),  date(2026, 6, 28),  # Cuti bersama Idul Adha
    date(2026, 8, 17),  date(2026, 8, 18),
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
def send_telegram(msg: str) -> bool:
    """Kirim pesan ke Telegram. V5.6.9: hapus parse_mode Markdown (silent 400 error)."""
    global TELEGRAM_TOKEN, TELEGRAM_CHAT

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        LOG.warning("Telegram tidak terkirim: TOKEN atau CHAT belum di-set")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    with _telegram_lock:
        for attempt in range(3):
            try:
                res = requests.post(
                    url,
                    data={"chat_id": TELEGRAM_CHAT, "text": msg},
                    timeout=10,
                )
                if res.status_code == 200:
                    LOG.info(f"Telegram OK attempt={attempt+1}")
                    return True
                elif res.status_code == 429:
                    retry_after = res.json().get("parameters", {}).get("retry_after", 3)
                    LOG.warning(f"Telegram rate limit — tunggu {retry_after}s")
                    time.sleep(retry_after)
                else:
                    LOG.error(f"Telegram error status={res.status_code} body={res.text[:300]}")
                    break
            except requests.Timeout:
                LOG.warning(f"Telegram timeout attempt={attempt+1}")
            except Exception as e:
                LOG.warning(f"Telegram exception attempt={attempt+1}: {e}")
                time.sleep(1)
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
            "signal_action":     st.session_state.get("signal_action", {}),
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
def calculate_rsi_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """[V6.0.0] Full RSI series — dibutuhkan untuk divergence detection."""
    close    = df["Close"].squeeze()
    delta    = close.diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    avg_loss = (-delta).clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))

def calculate_rsi(df: pd.DataFrame, period: int = 14) -> float:
    rsi = calculate_rsi_series(df, period)
    val = float(rsi.iloc[-1])
    return val if not np.isnan(val) else 50.0

def bearish_divergence(df: pd.DataFrame, lookback: int = 14) -> bool:
    """
    [V6.0.0 #6] Bearish divergence: harga bikin higher high, RSI bikin lower high.
    Hanya relevan saat RSI sudah tinggi (>65) — di zona rendah divergence tidak
    predictive dan hanya menambah false reject.
    Window: bandingkan 5 candle terakhir vs 9 candle sebelumnya (total 14).
    """
    try:
        if len(df) < lookback + 2:
            return False
        rsi_s  = calculate_rsi_series(df).tail(lookback)
        high_s = df["High"].squeeze().tail(lookback)
        if float(rsi_s.iloc[-1]) <= 65:
            return False   # gate hanya aktif di zona overbought
        recent_ph, prior_ph = float(high_s.tail(5).max()), float(high_s.head(lookback - 5).max())
        recent_rh, prior_rh = float(rsi_s.tail(5).max()),  float(rsi_s.head(lookback - 5).max())
        # Price higher high + RSI lower high (margin 1.0 poin RSI utk hindari noise)
        return recent_ph > prior_ph and recent_rh < prior_rh - 1.0
    except Exception:
        return False   # data error → jangan blokir, filter lain tetap jalan

# [K1] RSI gate adaptif per regime — dipanggil dengan regime saat ini
def rsi_gate(df: pd.DataFrame, regime: str = "SIDEWAYS") -> tuple[bool, float]:
    rsi = calculate_rsi(df)
    if regime == "BULLISH":
        # [V5.8.1 FIX #1] Lower bound 42 → 38 untuk BULLISH
        # Root cause: saham pre-breakout sering RSI 35-42 saat fase akumulasi akhir.
        # Dengan min 42, sistem buta terhadap setup terbaik yang belum breakout.
        # 38 = konsisten dengan SIDEWAYS. Tidak ada alasan BULLISH harus lebih ketat.
        # Upper bound tetap 78 — toleran overbought saat trend kuat.
        rsi_min, rsi_max = 38, 78
    elif regime == "DISTRIBUTION":
        rsi_min, rsi_max = 40, 68   # Lebih ketat saat distribusi
    else:                            # SIDEWAYS / VOLATILE / unknown
        rsi_min, rsi_max = 38, 72
    if not (rsi_min <= rsi <= rsi_max):
        return False, rsi
    # [V6.0.0 #6] Reject bearish divergence — entry tepat sebelum reversal
    if bearish_divergence(df):
        return False, rsi
    return True, rsi

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
    high         = df["High"].squeeze()
    low          = df["Low"].squeeze()
    avg_vol      = float(volume.tail(20).mean())
    # [V6.0.0 #4] Spike threshold 1.8x → 2.5x + WAJIB price action confirmation.
    # Spike volume di harga turun / close di ekor bawah = distribusi/churning,
    # bukan akumulasi (ADRO lesson). Tanpa konfirmasi harga → 0 poin.
    last_c, last_h, last_l = float(close.iloc[-1]), float(high.iloc[-1]), float(low.iloc[-1])
    day_range   = max(last_h - last_l, 1e-9)
    close_pos   = (last_c - last_l) / day_range          # posisi close dlm range hari ini
    up_day      = last_c > float(close.iloc[-2])
    price_confirm = up_day and close_pos >= 0.5          # hijau + close di paruh atas
    spike        = (float(volume.iloc[-1]) > avg_vol * 2.5) and price_confirm
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
    """
    [V6.0.0 #3] Multi-timeframe (10d/20d) + candle body quality.
    Hierarchy: STRONG > VALID > WEAK > WAIT.
    - STRONG : tembus 20d high, volume >1.5x, body >=50% range, close paruh atas
               → breakout struktural, bukan fakeout intraday
    - VALID  : tembus 10d high, volume >1.3x, close TIDAK di ekor bawah (>=40% range)
               → filter bull trap: spike lalu ditinggal (long upper wick) tidak lolos VALID
    - WEAK   : near breakout (>=99% dari 10d high) + hijau + volume >=0.6x
    - WAIT   : lainnya
    """
    close       = df["Close"].squeeze()
    high        = df["High"].squeeze()
    low         = df["Low"].squeeze()
    opn         = df["Open"].squeeze()
    volume      = df["Volume"].squeeze()
    last        = float(close.iloc[-1])
    prev        = float(close.iloc[-2])
    high_10     = float(high.iloc[:-1].tail(10).max())
    high_20     = float(high.iloc[:-1].tail(20).max())
    avg_vol     = float(volume.tail(20).mean())
    vol_ratio   = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1.0
    change_pct  = (last - prev) / prev * 100 if prev > 0 else 0

    # Candle body quality hari ini
    d_high, d_low, d_open = float(high.iloc[-1]), float(low.iloc[-1]), float(opn.iloc[-1])
    day_range  = max(d_high - d_low, 1e-9)
    body_ratio = abs(last - d_open) / day_range      # dominasi body vs wick
    close_pos  = (last - d_low) / day_range          # posisi close dlm range

    if (last >= high_20 and vol_ratio > 1.5
            and body_ratio >= 0.5 and close_pos >= 0.5 and last > d_open):
        return "STRONG"
    if last >= high_10 and vol_ratio > 1.3 and close_pos >= 0.4:
        return "VALID"
    if last >= high_10 * 0.99 and change_pct > 0 and vol_ratio >= 0.6:
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
        # [V6.0.0 #8] Retry 2x — yfinance 5m sering gagal transient di jam sibuk
        df5 = None
        for attempt in range(2):
            df5 = yf.download(tickers=ticker, period="5d", interval="5m",
                              progress=False, auto_adjust=True)
            if df5 is not None and len(df5) >= 10:
                break
            time.sleep(1.5)
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
    # [V6.0.0 #1] 5 komponen utama, bobot per regime SELALU sum = 1.0
    # → max score dijamin <= 100 by construction (bukan by cap)
    # trend    = (prob + runner) / 2  → kekuatan struktur & potensi runner
    # bandar   = akumulasi institusi  → bobot berat (2x kelas ringan)
    # breakout = kualitas breakout multi-TF
    # momentum = momentum D1 + follow-through (digabung, single counting)
    # rr       = risk/reward setup
    "BULLISH":      {"trend": 0.30, "bandar": 0.15, "breakout": 0.20, "momentum": 0.25, "rr": 0.10},
    "SIDEWAYS":     {"trend": 0.20, "bandar": 0.25, "breakout": 0.20, "momentum": 0.10, "rr": 0.25},
    "DISTRIBUTION": {"trend": 0.15, "bandar": 0.30, "breakout": 0.20, "momentum": 0.05, "rr": 0.30},
    "VOLATILE":     {"trend": 0.20, "bandar": 0.20, "breakout": 0.20, "momentum": 0.10, "rr": 0.30},
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
# [V6.0.0 #9] REGIME DETECTION — LEADING INDICATORS
# ============================================================
def detect_market_regime_v6(market: dict) -> str:
    """
    Regime detection dengan leading indicators, bukan hanya breadth 1-hari (reaktif):
    1. Breadth      : up vs down hari ini (lama — tetap dipakai, cepat tapi noisy)
    2. NH/NL ratio  : new 20d high vs new 20d low — struktur, bukan noise harian
    3. %>EMA20      : persentase universe di atas EMA20 — trend partisipasi
    Kombinasi 2 & 3 mendeteksi perubahan regime SEBELUM breadth harian berbalik penuh.
    Fallback ke engine lama jika data error.
    """
    try:
        up = down = nh = nl = above_ema20 = total = 0
        for _, df in market.items():
            close = df["Close"].squeeze()
            if len(close) < 25:
                continue
            total += 1
            last, prev = float(close.iloc[-1]), float(close.iloc[-2])
            if last > prev: up += 1
            else:           down += 1
            hi20 = float(df["High"].squeeze().iloc[:-1].tail(20).max())
            lo20 = float(df["Low"].squeeze().iloc[:-1].tail(20).min())
            if last >= hi20: nh += 1
            if last <= lo20: nl += 1
            ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
            if last > ema20: above_ema20 += 1

        if total < 10:
            return detect_market_regime(market)   # data terlalu tipis → fallback

        pct_above = above_ema20 / total * 100
        nh_dom    = nh >= max(2, nl * 2)          # NH dominan (min 2 utk hindari 1v0)
        nl_dom    = nl >= max(2, nh * 2)

        # BULLISH: breadth kuat ATAU struktur leading (NH dominan + partisipasi tinggi)
        if up > down * 1.4 or (nh_dom and pct_above > 55):
            return "BULLISH"
        # DISTRIBUTION: breadth negatif kuat ATAU struktur rusak lebih dulu
        if down > up * 1.5 or (nl_dom and pct_above < 40):
            return "DISTRIBUTION"
        return "SIDEWAYS"
    except Exception:
        return detect_market_regime(market)


# ============================================================
# SCORE — upgrade dengan adaptive weights
# ============================================================
def calculate_score(prob: float, runner: float, breakout: str,
                    momentum: int, ft: int, rr: float,
                    bandar_score: int, regime: str = "SIDEWAYS") -> float:
    """
    [V6.0.0 #1] Scoring disederhanakan: 5 komponen utama, weighted, sum bobot = 1.0.
    Perubahan vs V5.8.2:
    - Stack bonus eksternal (momentum/accum/ft/intraday/extra) DIHAPUS → double counting
      momentum dihitung 3x (base prob, momentum_bonus, ft_bonus) di versi lama
    - Liquidity DIHAPUS dari score: semua kandidat yang sampai scoring sudah lolos
      trap filter → liq selalu "OK" → konstanta yang mengkompresi diskriminasi score
    - Quality DIHAPUS dari score: tetap tampil sbg kolom info, tidak menggandakan trend
    - Max teoretis = 100.0 persis (semua komponen 0-100, bobot sum 1.0)
    """
    w = get_adaptive_weights(regime)

    trend_c    = (max(0, min(100, prob)) + max(0, min(100, runner))) / 2
    bandar_c   = (max(0, min(4, bandar_score)) / 4) * 100
    breakout_c = {"STRONG": 100, "VALID": 80, "WEAK": 45}.get(breakout, 0)
    mom_c      = (min(2, max(0, momentum)) / 2 * 0.6 + min(2, max(0, ft)) / 2 * 0.4) * 100
    rr_c       = (max(0, min(4.0, rr)) / 4.0) * 100

    total = (trend_c    * w["trend"]    +
             bandar_c   * w["bandar"]   +
             breakout_c * w["breakout"] +
             mom_c      * w["momentum"] +
             rr_c       * w["rr"])
    return round(min(100.0, max(0.0, total)), 2)



# ============================================================
# CONFLUENCE
# ============================================================
# [K2] Confluence check dengan minimum adaptif per regime
def is_bear_mode(regime: str) -> bool:
    """
    [BEAR MODE V5.7.0] Aktif saat regime DISTRIBUTION.
    Longgarkan threshold agar signal tetap muncul di bear market.
    Threshold kembali ketat otomatis saat regime berubah ke SIDEWAYS/BULLISH.
    """
    return regime == "DISTRIBUTION"

def get_bear_mode_params(regime: str) -> dict:
    """Return threshold params sesuai kondisi market."""
    if is_bear_mode(regime):
        return {
            "rr_min":       1.3,   # turun dari 1.8
            "conf_ratio":   0.55,  # [V6.0.0 #2] weighted confluence: 55% saat bear
            "rr_confluence": 1.3,  # RR_Layak di confluence
            "score_min":    60,    # turun dari 70
            "label":        "🐻 BEAR MODE",
        }
    return {
        "rr_min":       1.8,
        # [V6.0.0 #2] Count sederhana (3/6) → weighted ratio 70%.
        # Rationale: 3/6 memperlakukan semua sinyal sama penting — Momentum+RR+Uptrend
        # bisa lolos tanpa Bandar & Breakout sama sekali (sinyal ringan semua).
        # Weighted: Bandar 2x, Breakout 2x (WEAK=1x), sisanya 1x. Max 8 poin.
        # 70% = 5.6/8 → sinyal WAJIB punya minimal satu komponen berat.
        "conf_ratio":   0.70,
        "rr_confluence": 1.8,
        "score_min":    70,
        "label":        "NORMAL",
    }

def confluence_check(momentum: int, accum: int, bandar: int,
                     breakout: str, rr: float, ema_ok: bool,
                     regime: str = "SIDEWAYS") -> tuple[float, dict, bool]:
    """
    [V6.0.0 #2] Weighted confluence. Return (weighted_score, signals, passed).
    Bobot: Bandar 2, Breakout STRONG/VALID 2 (WEAK 1), Momentum/Accum/RR/Uptrend 1.
    Max = 8. Pass jika weighted/8 >= conf_ratio (0.70 normal, 0.55 bear).
    """
    bm = get_bear_mode_params(regime)
    breakout_w = 2 if breakout in ("STRONG", "VALID") else (1 if breakout == "WEAK" else 0)
    signals = {
        "Momentum":     momentum >= 1,
        "Accumulation": accum >= 2,
        "Bandar":       bandar >= 2,
        "Breakout":     breakout_w > 0,
        "RR_Layak":     rr >= bm["rr_confluence"],
        "Uptrend":      ema_ok,
    }
    weighted = (
        (2 if signals["Bandar"] else 0) +
        breakout_w +
        (1 if signals["Momentum"]     else 0) +
        (1 if signals["Accumulation"] else 0) +
        (1 if signals["RR_Layak"]     else 0) +
        (1 if signals["Uptrend"]      else 0)
    )
    CONF_MAX = 8.0
    passed = (weighted / CONF_MAX) >= bm["conf_ratio"]
    return float(weighted), signals, passed

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
# [V6.0.0 #7] DYNAMIC SIGNAL LOCK
# ============================================================
SIGNAL_RANK = {"🔥 EXECUTE NOW": 2, "✅ EXECUTE": 1}

def get_signal_lock_time(action: str) -> int:
    """
    Dynamic lock 10-60 menit berdasarkan kekuatan sinyal:
    - EXECUTE NOW (kuat) → 3600s: sinyal sudah maksimal, re-alert = spam murni
    - EXECUTE            → 1200s: masih bisa berkembang, jangan lock terlalu lama
    Upgrade bypass ditangani via can_send_signal() — EXECUTE → EXECUTE NOW
    menembus lock (informasi baru), downgrade tidak.
    """
    return 3600 if action == "🔥 EXECUTE NOW" else 1200

def can_send_signal(tkr: str, action: str, now_ts: float,
                    sig_lock: dict, sig_action: dict) -> bool:
    """Cek lock + upgrade bypass. Kedua dict dimodifikasi caller setelah kirim."""
    last_ts     = sig_lock.get(tkr, 0)
    last_action = sig_action.get(tkr, "")
    lock_dur    = get_signal_lock_time(last_action or action)
    if now_ts - last_ts >= lock_dur:
        return True
    # Masih dalam lock — hanya lolos jika UPGRADE kekuatan sinyal
    return SIGNAL_RANK.get(action, 0) > SIGNAL_RANK.get(last_action, 0)

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
            breakout in ("STRONG", "VALID") and bandar >= 3 and momentum >= 1):
        return "🔥 EXECUTE NOW"

    if score >= exec_th and rr >= min_rr and breakout in ("STRONG", "VALID", "WEAK") and bandar >= 2:
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
    st.session_state.signal_action     = _state.get("signal_action", {})
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
        # [V6.0.0 #8] Retry 2x + fallback interval 15m — hilangkan silent fail
        raw = None
        for attempt in range(2):
            raw = yf.download(
                tickers=list(tickers_tuple), period="1d", interval="5m",
                group_by="ticker", progress=False, auto_adjust=True,
            )
            if raw is not None and not raw.empty:
                break
            LOG.warning(f"intraday 5m fetch gagal (attempt {attempt+1}/2), retry...")
            time.sleep(2)
        if raw is None or raw.empty:
            LOG.warning("intraday 5m gagal total — fallback ke interval 15m")
            raw = yf.download(
                tickers=list(tickers_tuple), period="1d", interval="15m",
                group_by="ticker", progress=False, auto_adjust=True,
            )
        if raw is None or raw.empty:
            LOG.error("intraday fetch gagal termasuk fallback 15m — scanner pakai data D1 kemarin")
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
        f"Confluence : {row.get('Confluence', 0):.0f}/8\n"
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
        f"Confluence : {row.get('Confluence', 0):.0f}/8\n"
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
    regime       = detect_market_regime_v6(market)   # [V6.0.0 #9] leading indicators
    sector_power = sector_momentum(market, SECTOR_MAP)
    sector_df    = pd.DataFrame(
        [{"Sector": k, "Strength": round(v, 2)} for k, v in sector_power.items()]
    ).sort_values("Strength", ascending=False)

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

            # [V6.0.0 #5] Entry freshness — dynamic limit + pullback detection.
            # Base tier per kekuatan breakout, lalu:
            # (a) ATR-scaled: saham volatil (ATR% tinggi) wajar bergerak lebih besar
            # (b) Pullback detection: harga sudah turun >=1.5% dari high hari ini
            #     = koreksi sehat sedang berjalan → limit +2% (entry saat pullback
            #     BUKAN chasing, justru entry yang diinginkan)
            strong_daily_momentum = momentum == 2 or ft == 2
            base_limit = (
                9.0 if breakout == "STRONG" else
                8.0 if breakout == "VALID" and strong_daily_momentum else
                7.0 if breakout == "VALID" else
                6.0 if breakout == "WEAK" and strong_daily_momentum else
                5.0 if breakout == "WEAK" else
                4.5
            )
            atr_pct_f  = (atr / entry * 100) if entry > 0 else 0
            atr_adj    = min(2.0, max(0.0, (atr_pct_f - 3.0) * 0.5))   # ATR>3% → +0..2%
            day_high   = float(df["High"].squeeze().iloc[-1])
            off_high   = (day_high - last_price) / day_high * 100 if day_high > 0 else 0
            is_pullback   = off_high >= 1.5 and chg_pct > 0   # turun dari high tapi masih hijau
            pullback_adj  = 2.0 if is_pullback else 0.0
            freshness_limit = base_limit + atr_adj + pullback_adj
            if chg_pct > freshness_limit:
                debug_log.append({"Ticker": tkr_clean, "Sector": sector,
                    "RSI": round(rsi_value, 1), "EMA_OK": "✅" if ema_ok else "❌",
                    "Bandar": "-", "Breakout": breakout,
                    "Confluence": "-", "RR": round(rr, 1), "Score": "-",
                    "❌ Gugur di": f"Entry expired: naik {chg_pct:.1f}% (limit dinamis {freshness_limit:.1f}%: base {base_limit:.1f} +ATR {atr_adj:.1f} +pullback {pullback_adj:.1f})"})
                continue

            # Filter 3: Breakout gate — [BEAR MODE V5.7.0]
            # Bear mode: WAIT + bandar >= 2 = boleh lolos (akumulasi senyap di bear market)
            # Normal mode: WAIT = hard gate seperti sebelumnya
            if breakout == "WAIT":
                bear_allow = is_bear_mode(regime) and bandar >= 2
                if not bear_allow:
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

            if breakout == "WEAK" and not (momentum >= 1 or bandar >= 1 or ft >= 1):
                # [V5.8.1 FIX #3] Guard dilonggarkan: OR(momentum==2, intraday>=2, ft==2) → OR(momentum>=1, bandar>=1, ft>=1)
                # Root cause #1: intraday_confirm() sering return 0 di awal sesi jam 09:05-09:30
                #   karena data 5m belum cukup atau API call gagal → kondisi intraday>=2 hampir selalu False
                # Root cause #2: kondisi ==2 atau >=2 terlalu ketat untuk early session
                # Fix: cukup satu sinyal lemah (momentum OR bandar OR follow-through) yang confirm
                # Safety: liquidity_trap dan fake_breakout sudah cukup memfilter false signal di atas
                debug_log.append({"Ticker": tkr_clean, "Sector": sector,
                    "RSI": round(rsi_value, 1), "EMA_OK": "✅" if ema_ok else "❌",
                    "Bandar": bandar, "Breakout": breakout,
                    "Confluence": "-", "RR": round(rr, 1), "Score": "-",
                    "❌ Gugur di": "WEAK breakout tanpa konfirmasi apapun (momentum/bandar/ft semua 0)"})
                continue

            # Filter 4: Confluence — min adaptif via get_bear_mode_params
            conf_count, conf_signals, conf_passed = confluence_check(
                momentum, accum, bandar, breakout, rr, ema_ok, regime)
            if not conf_passed:
                failed = [k for k, v in conf_signals.items() if not v]
                bm_c   = get_bear_mode_params(regime)
                min_w  = bm_c["conf_ratio"] * 8   # [V6.0.0 #2] weighted, max 8
                debug_log.append({"Ticker": tkr_clean, "Sector": sector,
                    "RSI": round(rsi_value, 1), "EMA_OK": "✅" if ema_ok else "❌",
                    "Bandar": bandar, "Breakout": breakout,
                    "Confluence": f"{conf_count:.0f}/8", "RR": round(rr, 1), "Score": "-",
                    "❌ Gugur di": f"Weighted confluence {conf_count:.0f}/8 < {min_w:.1f} (gagal: {', '.join(failed)})"})
                continue

            # Filter 5: RR — [BEAR MODE V5.7.0] threshold adaptif
            bm_params = get_bear_mode_params(regime)
            rr_min = bm_params["rr_min"]
            if rr < rr_min:
                debug_log.append({"Ticker": tkr_clean, "Sector": sector,
                    "RSI": round(rsi_value, 1), "EMA_OK": "✅" if ema_ok else "❌",
                    "Bandar": bandar, "Breakout": breakout,
                    "Confluence": f"{conf_count:.0f}/8", "RR": round(rr, 1), "Score": "-",
                    "❌ Gugur di": f"RR terlalu rendah ({rr:.1f}, min {rr_min} [{bm_params[chr(39)]+'label'+chr(39)}])"})
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
                    "Confluence": f"{conf_count:.0f}/8", "RR": round(rr, 1), "Score": "-",
                    "❌ Gugur di": (
                        f"Saham slow mover ({', '.join(slow_mover_reasons)}) "
                        f"— modal bisa stuck"
                    )})
                continue

            # ── [V6.0.0 #1] SCORING — 5 komponen, no bonus stack ─
            # Double counting dihapus: momentum tidak lagi dihitung 3x
            # (base + momentum_bonus + ft_bonus di V5.8.2). Intraday & accum
            # tetap jadi filter/confluence, tidak menginflasi score.
            base_score = calculate_score(prob, runner, breakout,
                                         momentum, ft, rr, bandar, regime)
            score = min(100.0, max(0.0, base_score + sector_score_adj))

            # [Task 2] Score breakdown untuk explainability
            w_bd = get_adaptive_weights(regime)
            score_breakdown = {
                "trend":     round((prob + runner) / 2 * w_bd["trend"], 1),
                "bandar":    round(max(0, min(4, bandar)) / 4 * 100 * w_bd["bandar"], 1),
                "breakout":  round({"STRONG": 100, "VALID": 80, "WEAK": 45}.get(breakout, 0) * w_bd["breakout"], 1),
                "momentum":  round((min(2, momentum) / 2 * 0.6 + min(2, ft) / 2 * 0.4) * 100 * w_bd["momentum"], 1),
                "rr":        round(max(0, min(4.0, rr)) / 4.0 * 100 * w_bd["rr"], 1),
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
                "Confluence": f"{conf_count:.0f}/8", "RR": round(rr, 1),
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
# RESULT SURGERY: REPLACEMENT FOR run_scanner() IN dashboard.py
# ============================================================
def run_scanner():
    """
    Eksekusi main scanner ATS. 
    Hasil surgery: lock_time diturunkan ke 10 menit agar anti-miss momentum intraday,
    dan ditambah atomic save_state() agar status lock tidak hilang saat Railway sleep/restart.
    """
    st.session_state.scan_result = None
    st.session_state.sector_table = None
    st.session_state.dynamic_thresholds = None
    
    # Ambil data universe dan running scan core
    with st.spinner("Mengunduh data bursa & menghitung matriks kuantitatif..."):
        try:
            # Gunakan ISSI_UNIVERSE langsung
            tickers = list(ISSI_UNIVERSE)
            if not tickers:
                st.error("❌ Gagal memuat universe saham syariah ISSI.")
                return
                
            # Jalankan Core Engine Scanner
            market  = load_market()
            balance = st.session_state.get("balance", 800_000)
            scan_df, debug_df, thresholds, regime, sector_df = scan_core(
                market, balance, show_progress=True
            )
            
            st.session_state.scan_result = scan_df
            st.session_state.debug_log = debug_df
            st.session_state.dynamic_thresholds = thresholds
            st.session_state.last_regime = regime
            st.session_state.sector_table = sector_df
            # [FIX HEATMAP] Build heatmap data dari market yang sudah di-load
            try:
                st.session_state.heatmap_data = build_heatmap_data(market)
            except Exception as _hm_err:
                LOG.warning(f"build_heatmap_data gagal: {_hm_err}")
                st.session_state.heatmap_data = None
            
        except Exception as e:
            st.error(f"❌ Scanner Crash: {type(e).__name__}: {str(e)}")
            LOG.error(f"run_scanner crash: {e}")
            return

    # ============================================================
    # 🚨 BLOK SURGERY PENGIRIMAN ALERT TELEGRAM CRITICAL FIX
    # ============================================================
    if scan_df is not None and not scan_df.empty:
        now_ts = time.time()

        # [V6.0.0 #7] Dynamic lock per kekuatan sinyal + upgrade bypass
        if "signal_action" not in st.session_state:
            st.session_state.signal_action = {}
        sent = []

        for _, row in scan_df.iterrows():
            tkr = row["Ticker"]
            action = row.get("Action", "")

            if action not in ("🔥 EXECUTE NOW", "✅ EXECUTE"):
                continue

            if not can_send_signal(tkr, action, now_ts,
                                   st.session_state.signal_lock,
                                   st.session_state.signal_action):
                continue

            msg = format_telegram_signal(row, regime, thresholds)

            if send_telegram(msg):
                st.session_state.signal_lock[tkr]   = now_ts
                st.session_state.signal_action[tkr] = action
                sent.append(tkr)
        
        # Jika ada alert baru yang berhasil lolos kualifikasi
        if sent:
            st.success(f"🚀 [MONITOR] Alert Telegram Terkirim: {', '.join(sent)}")
            
            # ATOMIC BACKUP: Amankan data lock_time langsung ke JSON disk ats_state.json
            # Ini mencegah kehilangan status tracker jika server Railway mendadak spindown/idle
            try:
                save_state()
            except Exception as e:
                LOG.error(f"Gagal mengamankan state sesaat setelah send alert: {e}")

    # Auto-save scan log berkas fisik harian ke folder scan_logs/
    try:
        save_scan_log(st.session_state.scan_result, st.session_state.debug_log, regime, "manual")
    except Exception as e:
        LOG.error(f"Auto backup scan log gagal: {e}")

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
        now_ts     = time.time()
        sig_action = _state.get("signal_action", {})
        if not isinstance(sig_action, dict):
            sig_action = {}
        sent_any   = False

        for _, row in scan_df.iterrows():
            tkr    = row["Ticker"]
            action = row.get("Action", "")
            if action not in ("🔥 EXECUTE NOW", "✅ EXECUTE"): continue
            # [V6.0.0 #7] Dynamic lock + upgrade bypass
            if not can_send_signal(tkr, action, now_ts, sig_lock, sig_action): continue

            chg = row.get("Change%", 0)
            msg = format_telegram_signal_bg(row, regime)   # [P1] enriched background
            send_telegram(msg)
            sig_lock[tkr]   = now_ts
            sig_action[tkr] = action
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
                st_data["signal_action"]     = sig_action
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
    "near_low_alerts": [],  # list ticker yang sudah dapat alert near-low hari ini
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
            _spike_state["near_low_alerts"] = []
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

                # ── NEAR-LOW EARLY WARNING ───────────────────
                # Deteksi harga mendekati atau menyentuh Low kemarin.
                # Logic: ambil low kemarin dari data 5m (bar paling awal hari ini
                # tidak reliable — pakai daily data yang sudah ada di intraday
                # context). Proxy: gunakan open_px sebagai worst-case low hari ini,
                # bandingkan dengan low 5m bar pertama sebagai support level.
                # CATATAN: data 5m hanya berisi hari ini, tidak ada "kemarin" di sini.
                # Low kemarin harus diambil dari daily data — dilakukan di luar loop
                # setelah semua ticker diproses (lihat near_low_check di bawah).

            except Exception:
                continue

        # ── NEAR-LOW BATCH CHECK ─────────────────────────────
        # Ambil daily data untuk semua ticker yang intraday px tersedia
        # Cek: current_px <= low_kemarin * 1.005 (dalam 0.5% dari low kemarin)
        # Threshold 0.5% dipilih karena:
        # - Terlalu kecil (0.1%) → false alarm terus, noise spread IDX
        # - Terlalu besar (2%) → sudah terlambat, harga sudah breakdown
        # - 0.5% = zona support test yang actionable, masih ada waktu untuk antisipasi
        now_label_nl = datetime.now(WIB).strftime("%H:%M WIB")
        try:
            raw_daily_nl = yf.download(
                tickers=ISSI_UNIVERSE,
                period="5d", interval="1d",
                group_by="ticker", progress=False, auto_adjust=True,
            )
            if raw_daily_nl is not None and not raw_daily_nl.empty:
                for tkr_nl in ISSI_UNIVERSE:
                    try:
                        # Ambil current price dari snapshot yang baru di-update
                        with _spike_lock:
                            cur_px_nl = _spike_state["last_prices"].get(tkr_nl, 0)
                            already_near_low = tkr_nl in _spike_state["near_low_alerts"]

                        if cur_px_nl <= 0 or already_near_low:
                            continue

                        # Ambil low kemarin dari daily data
                        if len(ISSI_UNIVERSE) > 1:
                            df_d = raw_daily_nl[tkr_nl].dropna()
                        else:
                            df_d = raw_daily_nl.dropna()

                        if df_d is None or len(df_d) < 2:
                            continue

                        low_yesterday = float(df_d["Low"].squeeze().iloc[-2])
                        if low_yesterday <= 0:
                            continue

                        # Hitung jarak harga saat ini ke low kemarin
                        dist_to_low_pct = (cur_px_nl - low_yesterday) / low_yesterday * 100

                        # Trigger: harga dalam 0.5% di atas low kemarin, atau sudah di bawahnya
                        # Artinya: -0.5% ≤ dist ≤ 0.5%
                        if -0.5 <= dist_to_low_pct <= 0.5:
                            tkr_clean_nl = tkr_nl.replace(".JK", "")
                            sector_nl    = get_sector(tkr_nl)
                            status_tag   = "🔴 BREAKDOWN" if dist_to_low_pct < 0 else "⚠️ TEST SUPPORT"

                            msg_nl = (
                                f"🚨 NEAR LOW ALERT — ATS V{APP_VERSION}\n"
                                f"{'━'*30}\n"
                                f"📌 {tkr_clean_nl}  |  {sector_nl}\n"
                                f"⏰ {now_label_nl}  |  🔄 Intraday Refresh\n\n"
                                f"{status_tag}\n"
                                f"Harga saat ini : Rp {idr(cur_px_nl)}\n"
                                f"Low kemarin    : Rp {idr(low_yesterday)}\n"
                                f"Jarak          : {dist_to_low_pct:+.2f}%\n\n"
                                f"{'🔴 Harga SUDAH BREAKDOWN — Low kemarin ditembus!' if dist_to_low_pct < 0 else '⚠️ Harga MENDEKATI Low kemarin — zona support kritis!'}\n"
                                f"{'━'*30}\n"
                                f"❌ HINDARI BUY saat ini\n"
                                f"Jika pegang posisi — evaluasi SL kamu.\n"
                                f"⚠️ No FOMO. Tunggu konfirmasi arah."
                            )
                            send_telegram(msg_nl)
                            with _spike_lock:
                                _spike_state["near_low_alerts"].append(tkr_nl)
                            LOG.info(f"near_low_alert sent: {tkr_clean_nl} px={cur_px_nl} low_yst={low_yesterday} dist={dist_to_low_pct:+.2f}%")

                    except Exception:
                        continue
        except Exception:
            pass   # Silent fail — near-low check tidak boleh crash intraday refresh

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

            # [V6.0.0 #5] Sync dynamic freshness dengan scan_core
            strong_daily_momentum = momentum == 2 or ft == 2
            base_limit = (
                9.0 if breakout == "STRONG" else
                8.0 if breakout == "VALID" and strong_daily_momentum else
                7.0 if breakout == "VALID" else
                6.0 if breakout == "WEAK" and strong_daily_momentum else
                5.0 if breakout == "WEAK" else
                4.5
            )
            atr_pct_f = (atr / entry * 100) if entry > 0 else 0
            atr_adj   = min(2.0, max(0.0, (atr_pct_f - 3.0) * 0.5))
            day_high  = float(df_daily["High"].squeeze().iloc[-1])
            off_high  = (day_high - last_price) / day_high * 100 if day_high > 0 else 0
            pullback_adj = 2.0 if (off_high >= 1.5 and chg_pct > 0) else 0.0
            freshness_limit = base_limit + atr_adj + pullback_adj
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

            # [V6.0.0] Sync dengan scan_core V5.8.1: cukup satu sinyal lemah confirm
            if breakout == "WEAK" and not (momentum >= 1 or bandar >= 1 or ft >= 1):
                continue

            conf_count, _, conf_passed = confluence_check(
                momentum, accum, bandar, breakout, rr, ema_ok, regime
            )
            if not conf_passed:
                continue

            # [V6.0.0 #1] Scoring 5 komponen — identik dengan scan_core, no bonus stack
            score = calculate_score(prob, runner, breakout,
                                    momentum, ft, rr, bandar, spike_regime)
            lot   = position_sizing(balance, 0.02, entry, sl, atr)

            # Kirim Telegram spike alert
            alignment_val = sum([
                last_price > ema20_val if (ema20_val := float(df_daily["Close"].squeeze().ewm(span=20,adjust=False).mean().iloc[-1])) else False,
                last_price > ema_val,
                momentum >= 1,
                bandar >= 2,
                breakout in ("STRONG","VALID","WEAK"),
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
                f"Confluence : {conf_count:.0f}/8\n"
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
# 🚀 BREAKOUT YESTERDAY HIGH SCANNER — V5.8.0
# Logic  : close hari ini > high daily kemarin
# Filter : volume > 0 (skip saham zombie)
# Output : sorted by breakout_pct DESC → Telegram + UI
# Schedule: tiap 15 menit jam 09:00–10:00 WIB
# ============================================================

def scan_breakout_yesterday_high(universe: list) -> list:
    """
    Scan semua ticker di universe.
    Return list of dict sorted by breakout_pct DESC.

    [FIX] Gunakan filter tanggal eksplisit (bukan iloc[-2])
    untuk menghindari mismatch timezone yfinance vs IDX.
    - "Kemarin" = hari bursa terakhir sebelum hari ini WIB
    - "Hari ini" = candle dengan tanggal == today WIB
    - Kalau candle hari ini belum ada (pre-market), pakai iloc[-1] vs iloc[-2]
      tapi tetap validasi tanggalnya.
    """
    import pytz
    results  = []
    tz_wib   = pytz.timezone("Asia/Jakarta")
    today_wib = datetime.now(tz_wib).date()

    for ticker in universe:
        try:
            df = yf.download(
                ticker, period="7d", interval="1d",   # 7d biar dapat minimal 3 hari bursa
                progress=False, auto_adjust=True,
            )
            if df is None or len(df) < 2:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # Normalisasi index ke date (hilangkan timezone UTC yfinance)
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            df = df[df.index.date <= today_wib]  # buang candle masa depan
            df = df.sort_index()

            if len(df) < 2:
                continue

            # Cari candle hari ini (jika sudah ada) atau candle terbaru
            last_date  = df.index[-1].date()
            is_today   = (last_date == today_wib)

            if is_today and len(df) >= 2:
                # Ada candle hari ini → ambil H-1 = candle sebelumnya
                row_today     = df.iloc[-1]
                row_kemarin   = df.iloc[-2]
            else:
                # Candle hari ini belum ada (pre-open) → skip, belum bisa compare
                continue

            high_kemarin    = float(row_kemarin["High"])
            close_hari_ini  = float(row_today["Close"])
            volume_hari_ini = float(row_today["Volume"])

            # Validasi: high_kemarin harus masuk akal (> 0)
            if high_kemarin <= 0 or volume_hari_ini <= 0:
                continue

            # Filter minimum breakout 0.5% untuk hindari floating point noise
            if close_hari_ini > high_kemarin:
                pct = (close_hari_ini - high_kemarin) / high_kemarin * 100
                if pct < 0.5:
                    continue
                results.append({
                    "ticker":       ticker.replace(".JK", ""),
                    "harga":        close_hari_ini,
                    "high_kemarin": high_kemarin,
                    "breakout_pct": round(pct, 2),
                    "volume_b":     round(volume_hari_ini / 1e9, 1),
                    "tgl_kemarin":  row_kemarin.name.strftime("%d/%m"),  # [DEBUG] log tanggal H-1
                })
        except Exception as e:
            LOG.warning(f"breakout_scan | {ticker} | {e}")
            continue

    results.sort(key=lambda x: x["breakout_pct"], reverse=True)
    LOG.info(f"breakout_scan | scanned={len(universe)} breakout={len(results)}")
    return results


def scan_breakout_yesterday_low(universe: list) -> list:
    """
    [V5.8.1 NEW] Breakdown scanner — close < low H-1.
    Terpisah dari scanner utama karena bearish thesis berbeda philosophy.
    Ini bukan sinyal beli — ini sinyal bahwa saham sedang DISTRIBUSI atau JUAL.
    Berguna untuk: avoid buy, watchlist short opportunity (jika relevan), risk awareness.

    Return list of dict sorted by breakdown_pct DESC (paling dalam breakdown duluan).
    """
    import pytz
    results   = []
    tz_wib    = pytz.timezone("Asia/Jakarta")
    today_wib = datetime.now(tz_wib).date()

    for ticker in universe:
        try:
            df = yf.download(
                ticker, period="7d", interval="1d",
                progress=False, auto_adjust=True,
            )
            if df is None or len(df) < 2:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            df = df[df.index.date <= today_wib]
            df = df.sort_index()

            if len(df) < 2:
                continue

            last_date = df.index[-1].date()
            if last_date != today_wib or len(df) < 2:
                continue   # Candle hari ini belum ada, skip

            row_today   = df.iloc[-1]
            row_kemarin = df.iloc[-2]

            low_kemarin     = float(row_kemarin["Low"])
            close_hari_ini  = float(row_today["Close"])
            volume_hari_ini = float(row_today["Volume"])

            if low_kemarin <= 0 or volume_hari_ini <= 0:
                continue

            # Breakdown: close hari ini < low kemarin, min 0.5% untuk filter noise
            if close_hari_ini < low_kemarin:
                pct = (low_kemarin - close_hari_ini) / low_kemarin * 100
                if pct < 0.5:
                    continue
                results.append({
                    "ticker":       ticker.replace(".JK", ""),
                    "harga":        close_hari_ini,
                    "low_kemarin":  low_kemarin,
                    "breakdown_pct": round(pct, 2),
                    "volume_b":     round(volume_hari_ini / 1e9, 1),
                    "tgl_kemarin":  row_kemarin.name.strftime("%d/%m"),
                })
        except Exception as e:
            LOG.warning(f"breakdown_scan | {ticker} | {e}")
            continue

    results.sort(key=lambda x: x["breakdown_pct"], reverse=True)
    LOG.info(f"breakdown_scan | scanned={len(universe)} breakdown={len(results)}")
    return results


def format_breakdown_telegram(results: list, label: str = "") -> str:
    """Format hasil breakdown scan jadi pesan Telegram."""
    now_str = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    tag = f" [{label}]" if label else ""

    if not results:
        return (
            f"📉 BREAKDOWN SCAN{tag} — {now_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Tidak ada saham ISSI breakdown low kemarin."
        )

    lines = [
        f"📉 BREAKDOWN SCAN{tag} — {now_str}",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"Close < Low H-1  |  {len(results)} saham\n",
    ]
    for i, r in enumerate(results, 1):
        lines.append(
            f"{i}. {r['ticker']}  -{r['breakdown_pct']:.1f}%\n"
            f"   Harga {r['harga']:,.0f}  |  Low H-1({r.get('tgl_kemarin','?')}) {r['low_kemarin']:,.0f}"
            f"  |  Vol {r['volume_b']:.1f}B"
        )
    lines.append("\n⚠ BREAKDOWN — HINDARI BUY. Konfirmasi TF 15m sebelum keputusan apapun.")
    return "\n".join(lines)


def format_breakout_telegram(results: list, label: str = "") -> str:
    """Format hasil breakout scan jadi pesan Telegram."""
    now_str = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    tag = f" [{label}]" if label else ""

    if not results:
        return (
            f"🔍 BREAKOUT SCAN{tag} — {now_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Tidak ada saham ISSI breakout high kemarin."
        )

    lines = [
        f"🚀 BREAKOUT SCAN{tag} — {now_str}",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"Close > High H-1  |  {len(results)} saham\n",
    ]
    for i, r in enumerate(results, 1):
        lines.append(
            f"{i}. {r['ticker']}  +{r['breakout_pct']:.1f}%\n"
            f"   Harga {r['harga']:,.0f}  |  H-1({r.get('tgl_kemarin','?')}) {r['high_kemarin']:,.0f}"
            f"  |  Vol {r['volume_b']:.1f}B"
        )
    lines.append("\n⚠ Raw breakout — konfirmasi TF 15m sebelum entry")
    return "\n".join(lines)


# ============================================================
# ============================================================
# [IPOT PDF PARSER] — Import statement broker IPOT
# Parse transaksi Buy/Sell, group by ticker, hitung avg PnL
# ============================================================

def parse_ipot_pdf(pdf_bytes: bytes) -> pd.DataFrame:
    """
    Parse PDF statement IPOT.
    Return DataFrame dengan kolom:
    date, ticker, action, price, volume, amount, description
    """
    if not PDF_AVAILABLE:
        raise ImportError("pdfplumber tidak tersedia. Pastikan sudah install.")

    import io
    import re

    rows = []
    current_date = None

    # Pattern ticker IDX: 2-4 huruf kapital
    ticker_pattern = re.compile(r'\b([A-Z]{2,5})\b')
    # Pattern Buy/Sell dari description
    buy_sell_pattern = re.compile(r'(Buy|Sell)\s+([A-Z]{2,5})', re.IGNORECASE)
    # Pattern tanggal: DD-Mon-YY atau DD-Mon-YYYY
    date_pattern = re.compile(r'(\d{2}-(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{2,4})', re.IGNORECASE)
    # Pattern harga dan volume: angka dengan titik sebagai ribuan
    num_pattern = re.compile(r'[\d,\.]+')

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        full_text = ""
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"

    lines = full_text.split('\n')

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Cari baris yang mengandung Buy atau Sell
        bs_match = buy_sell_pattern.search(line)
        if not bs_match:
            continue

        action = bs_match.group(1).upper()  # BUY atau SELL
        ticker = bs_match.group(2).upper()

        # Cari tanggal di sekitar baris ini (dari baris sebelumnya atau baris ini)
        date_match = date_pattern.search(line)
        if date_match:
            try:
                current_date = pd.to_datetime(date_match.group(1), dayfirst=True).date()
            except Exception:
                pass

        # Extract angka dari baris — format: Price Volume Amount
        # Ambil semua angka di baris
        numbers = []
        for token in line.split():
            # Bersihkan titik ribuan dan koma desimal
            clean = token.replace('.', '').replace(',', '')
            try:
                numbers.append(float(clean))
            except ValueError:
                continue

        # Heuristik: angka pertama yang reasonable = price (100-100000)
        # angka kedua = volume (100-100000)
        # angka ketiga = amount (dalam ribuan ke atas)
        price, volume, amount = None, None, None
        price_candidates = [n for n in numbers if 50 <= n <= 500000]
        vol_candidates   = [n for n in numbers if 100 <= n <= 1000000 and n % 100 == 0]

        if price_candidates:
            price = price_candidates[0]
        if len(vol_candidates) >= 1:
            volume = vol_candidates[0]
        if price and volume:
            amount = price * volume

        if ticker and action in ("BUY", "SELL"):
            rows.append({
                "date":        str(current_date) if current_date else "",
                "ticker":      ticker,
                "action":      action,
                "price":       price,
                "volume":      volume,
                "amount":      amount,
                "description": line[:80],
            })

    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["date", "ticker", "action", "price", "volume", "amount", "description"]
    )


def parse_ipot_text(text: str) -> pd.DataFrame:
    """
    Parse teks copy-paste dari mutasi rekening IPOT web.
    Format kolom: TrxDate | DueDate | Transaction | Price | Volume | Amount | Balance | Days | Penalty
    Contoh: '08 Jul 26  10 Jul 26  Pembelian Saham KLBF  735  1,900  -1,456,262  ...'
    Return DataFrame dengan kolom: date, ticker, action, price, volume, amount
    """
    import re

    rows = []
    lines = text.strip().split('\n')

    # Pattern tanggal: DD Mon YY atau DD Mon YYYY
    date_pat   = re.compile(r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{2,4})', re.IGNORECASE)
    # Pattern transaksi Buy/Sell
    buy_pat    = re.compile(r'Pembelian\s+Saham\s+([A-Z]{2,5})', re.IGNORECASE)
    sell_pat   = re.compile(r'Penjualan\s+Saham\s+([A-Z]{2,5})', re.IGNORECASE)

    month_map = {
        'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
        'may': '05', 'jun': '06', 'jul': '07', 'aug': '08',
        'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12'
    }

    def parse_date(s):
        parts = s.strip().split()
        if len(parts) == 3:
            day = parts[0].zfill(2)
            mon = month_map.get(parts[1].lower()[:3], '01')
            yr  = parts[2]
            if len(yr) == 2:
                yr = '20' + yr
            return f"{yr}-{mon}-{day}"
        return ""

    def clean_num(s):
        """Bersihkan angka dari koma/titik ribuan"""
        s = s.replace(',', '').replace(' ', '')
        try:
            return float(s)
        except Exception:
            return None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Skip baris header
        if any(h in line for h in ['TrxDate', 'Transaction', 'Penalty']):
            continue

        # Cek apakah baris ini Buy atau Sell
        buy_match  = buy_pat.search(line)
        sell_match = sell_pat.search(line)

        if not buy_match and not sell_match:
            continue

        action = "BUY"  if buy_match  else "SELL"
        ticker = buy_match.group(1).upper() if buy_match else sell_match.group(1).upper()

        # Cari tanggal pertama di baris (TrxDate)
        dates = date_pat.findall(line)
        trx_date = parse_date(dates[0]) if dates else ""

        # Extract angka — ambil semua token numerik di baris
        # Format baris: ... Pembelian Saham KLBF  735  1,900  -1,456,262  ...
        # Setelah ticker, angka pertama = price, kedua = volume, ketiga = amount
        # Cari posisi ticker dulu
        ticker_pos = line.upper().find(ticker)
        after_ticker = line[ticker_pos + len(ticker):]

        # Extract semua angka (termasuk negatif)
        num_tokens = re.findall(r'-?[\d,]+(?:\.\d+)?', after_ticker)
        numbers = []
        for t in num_tokens:
            n = clean_num(t)
            if n is not None:
                numbers.append(n)

        price  = numbers[0] if len(numbers) > 0 else None
        volume = numbers[1] if len(numbers) > 1 else None
        amount = numbers[2] if len(numbers) > 2 else None

        # Validasi basic
        if price and price < 0:
            price = abs(price)
        if volume and volume < 0:
            volume = abs(volume)

        rows.append({
            "date":        trx_date,
            "ticker":      ticker,
            "action":      action,
            "price":       price,
            "volume":      volume,
            "amount":      amount,
            "description": line[:80],
        })

    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["date", "ticker", "action", "price", "volume", "amount", "description"]
    )


def group_ipot_trades(df: pd.DataFrame) -> list:
    """
    Group transaksi Buy/Sell per ticker.
    Hitung avg entry, total volume, realized PnL via weighted average.
    Return list of trade dicts siap masuk ke inv_trades.
    """
    if df.empty:
        return []

    results = []
    tickers = df["ticker"].unique()

    for ticker in tickers:
        t_df = df[df["ticker"] == ticker].copy()
        buys  = t_df[t_df["action"] == "BUY"]
        sells = t_df[t_df["action"] == "SELL"]

        if buys.empty or sells.empty:
            continue

        # Weighted average entry
        total_buy_vol  = buys["volume"].sum()
        total_buy_amt  = (buys["price"] * buys["volume"]).sum()
        avg_entry      = total_buy_amt / total_buy_vol if total_buy_vol > 0 else 0

        # Weighted average exit
        total_sell_vol = sells["volume"].sum()
        total_sell_amt = (sells["price"] * sells["volume"]).sum()
        avg_exit       = total_sell_amt / total_sell_vol if total_sell_vol > 0 else 0

        # Volume yang matched (min buy vs sell)
        matched_vol = min(total_buy_vol, total_sell_vol)
        lots        = int(matched_vol / 100)

        # Realized PnL — hanya dari volume yang sudah dijual
        sell_fraction = min(total_sell_vol / total_buy_vol, 1.0) if total_buy_vol > 0 else 1.0
        realized_cost = avg_entry * total_sell_vol
        realized_rev  = total_sell_amt
        pnl_gross     = realized_rev - realized_cost
        # Estimasi komisi IPOT: 0.15% beli + 0.25% jual
        komisi = (total_buy_amt * 0.0015) + (total_sell_amt * 0.0025)
        pnl_net = pnl_gross - komisi

        status = "WIN" if pnl_net > 0 else ("LOSS" if pnl_net < 0 else "BE")

        # Open position kalau sell volume < buy volume
        is_open = total_sell_vol < total_buy_vol
        if is_open:
            status = "OPEN"

        # Tanggal entry = tanggal buy pertama, exit = tanggal sell terakhir
        entry_date = buys["date"].min() if not buys.empty else ""
        exit_date  = sells["date"].max() if not sells.empty else ""

        results.append({
            "date":         entry_date,
            "ticker":       ticker,
            "entry":        round(avg_entry, 0),
            "sl":           0,
            "tp":           0,
            "lot":          max(lots, 1),
            "exit":         round(avg_exit, 0) if not is_open else None,
            "status":       status,
            "note":         f"Import IPOT | Buy {int(total_buy_vol/100)}lot @ avg{avg_entry:.0f} | Sell {int(total_sell_vol/100)}lot @ avg{avg_exit:.0f}",
            "pnl_gross":    round(pnl_gross, 0),
            "pnl_net":      round(pnl_net, 0),
            "komisi_est":   round(komisi, 0),
            "total_buy_vol":  total_buy_vol,
            "total_sell_vol": total_sell_vol,
            "buy_dates":    buys["date"].tolist(),
            "sell_dates":   sells["date"].tolist(),
        })

    # Sort by entry date
    results.sort(key=lambda x: x["date"])
    return results


# [MACD DIVERGENCE SCANNER] — 4 tipe divergence untuk IDX
# Setting 5/35/5 sesuai rekomendasi untuk saham IDX
# Bullish, Bearish, Hidden Bullish, Hidden Bearish
# ============================================================

def detect_macd_divergence(df: pd.DataFrame,
                            fast: int = 5,
                            slow: int = 35,
                            signal: int = 5,
                            lookback: int = 5) -> dict:
    """
    Deteksi 4 tipe MACD divergence dari DataFrame daily.
    Return dict berisi tipe divergence yang aktif.
    """
    if len(df) < slow + signal + lookback * 2:
        return {"type": None}

    close = df["Close"].squeeze()
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()

    # MACD 5/35/5
    ema_fast    = close.ewm(span=fast,   adjust=False).mean()
    ema_slow    = close.ewm(span=slow,   adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist        = macd_line - signal_line

    price_arr = close.values
    high_arr  = high.values
    low_arr   = low.values
    macd_arr  = macd_line.values
    n = lookback

    price_pivots_high, price_pivots_low   = [], []
    macd_pivots_high,  macd_pivots_low    = [], []

    for i in range(n, len(price_arr) - n):
        if high_arr[i] == max(high_arr[i-n:i+n+1]):
            price_pivots_high.append((i, high_arr[i]))
        if low_arr[i] == min(low_arr[i-n:i+n+1]):
            price_pivots_low.append((i, low_arr[i]))
        if macd_arr[i] == max(macd_arr[i-n:i+n+1]):
            macd_pivots_high.append((i, macd_arr[i]))
        if macd_arr[i] == min(macd_arr[i-n:i+n+1]):
            macd_pivots_low.append((i, macd_arr[i]))

    result = {
        "type":             None,
        "bull_div":         False,
        "bear_div":         False,
        "hidden_bull_div":  False,
        "hidden_bear_div":  False,
        "macd_bull":        bool(float(hist.iloc[-1]) > float(hist.iloc[-2])),
        "macd_above_zero":  bool(float(macd_line.iloc[-1]) > 0),
        "macd_val":         round(float(macd_line.iloc[-1]), 4),
        "hist_val":         round(float(hist.iloc[-1]), 4),
    }

    if len(price_pivots_low) >= 2 and len(macd_pivots_low) >= 2:
        p1_idx, p1_price = price_pivots_low[-2]
        p2_idx, p2_price = price_pivots_low[-1]
        m1_idx, m1_macd  = macd_pivots_low[-2]
        m2_idx, m2_macd  = macd_pivots_low[-1]

        if abs(p2_idx - p1_idx) >= lookback * 2:
            # Bullish Divergence: price lower low, MACD higher low
            if p2_price < p1_price and m2_macd > m1_macd:
                result["bull_div"] = True
                result["type"] = "BULLISH_DIV"
            # Hidden Bullish: price higher low, MACD lower low
            if p2_price > p1_price and m2_macd < m1_macd:
                result["hidden_bull_div"] = True
                if result["type"] is None:
                    result["type"] = "HIDDEN_BULL_DIV"

    if len(price_pivots_high) >= 2 and len(macd_pivots_high) >= 2:
        p1_idx, p1_price = price_pivots_high[-2]
        p2_idx, p2_price = price_pivots_high[-1]
        m1_idx, m1_macd  = macd_pivots_high[-2]
        m2_idx, m2_macd  = macd_pivots_high[-1]

        if abs(p2_idx - p1_idx) >= lookback * 2:
            # Bearish Divergence: price higher high, MACD lower high
            if p2_price > p1_price and m2_macd < m1_macd:
                result["bear_div"] = True
                if result["type"] is None:
                    result["type"] = "BEARISH_DIV"
            # Hidden Bearish: price lower high, MACD higher high
            if p2_price < p1_price and m2_macd > m1_macd:
                result["hidden_bear_div"] = True
                if result["type"] is None:
                    result["type"] = "HIDDEN_BEAR_DIV"

    return result


def scan_macd_divergence(universe: list) -> list:
    """
    Scan ISSI universe untuk MACD divergence (5/35/5).
    Return list of dict sorted by priority:
    BULLISH_DIV > HIDDEN_BULL_DIV > HIDDEN_BEAR_DIV > BEARISH_DIV
    """
    results   = []
    tz_wib    = pytz.timezone("Asia/Jakarta")
    today_wib = datetime.now(tz_wib).date()

    for ticker in universe:
        try:
            df = yf.download(
                ticker, period="90d", interval="1d",
                progress=False, auto_adjust=True,
            )
            if df is None or len(df) < 50:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            df = df[df.index.date <= today_wib].sort_index()

            if len(df) < 50:
                continue

            div = detect_macd_divergence(df)
            if div["type"] is None:
                continue

            close    = df["Close"].squeeze()
            high_s   = df["High"].squeeze()
            low_s    = df["Low"].squeeze()
            volume   = df["Volume"].squeeze()
            vol_ma   = float(volume.tail(20).mean())
            vol_last = float(volume.iloc[-1])
            c_last   = float(close.iloc[-1])
            ema9     = float(close.ewm(span=9, adjust=False).mean().iloc[-1])

            # Weekly VWAP sederhana
            hlc3   = (high_s + low_s + close) / 3
            df2    = df.copy()
            df2["dow"]          = pd.to_datetime(df2.index).dayofweek
            df2["is_new_week"]  = (df2["dow"] == 0) & (df2["dow"].shift(1) != 0)
            df2["tpvol"]        = hlc3 * volume
            _cv, _ct = 0.0, 0.0
            vwap_vals = []
            for i in range(len(df2)):
                if df2["is_new_week"].iloc[i] or i == 0:
                    _cv = float(volume.iloc[i])
                    _ct = float(df2["tpvol"].iloc[i])
                else:
                    _cv += float(volume.iloc[i])
                    _ct += float(df2["tpvol"].iloc[i])
                vwap_vals.append(_ct / _cv if _cv > 0 else float("nan"))
            vwap = vwap_vals[-1] if vwap_vals else c_last

            bull_struct = c_last > ema9 and ema9 > vwap

            results.append({
                "ticker":          ticker.replace(".JK", ""),
                "harga":           round(c_last, 0),
                "div_type":        div["type"],
                "bull_div":        div["bull_div"],
                "hidden_bull_div": div["hidden_bull_div"],
                "bear_div":        div["bear_div"],
                "hidden_bear_div": div["hidden_bear_div"],
                "macd_bull":       div["macd_bull"],
                "macd_zero":       div["macd_above_zero"],
                "macd_val":        div["macd_val"],
                "bull_struct":     bull_struct,
                "vol_ratio":       round(vol_last / vol_ma, 1) if vol_ma > 0 else 0,
            })

        except Exception as e:
            LOG.warning(f"macd_div_scan | {ticker} | {e}")
            continue

    priority = {
        "BULLISH_DIV":     0,
        "HIDDEN_BULL_DIV": 1,
        "HIDDEN_BEAR_DIV": 2,
        "BEARISH_DIV":     3,
    }
    results.sort(key=lambda x: priority.get(x["div_type"], 9))
    LOG.info(f"macd_div_scan | scanned={len(universe)} found={len(results)}")
    return results


def format_macd_div_telegram(results: list, label: str = "") -> str:
    """Format hasil MACD divergence scan jadi pesan Telegram."""
    now_str = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    tag     = f" [{label}]" if label else ""

    bull   = [r for r in results if r["div_type"] == "BULLISH_DIV"]
    h_bull = [r for r in results if r["div_type"] == "HIDDEN_BULL_DIV"]
    h_bear = [r for r in results if r["div_type"] == "HIDDEN_BEAR_DIV"]
    bear   = [r for r in results if r["div_type"] == "BEARISH_DIV"]

    if not results:
        return (
            f"📊 MACD DIV SCAN{tag} — {now_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Tidak ada divergence terdeteksi hari ini."
        )

    lines = [
        f"📊 MACD DIVERGENCE SCAN{tag} — {now_str}",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"Setting 5/35/5  |  {len(results)} divergence\n",
    ]

    def fmt_row(r):
        struct  = "✅ BULL" if r["bull_struct"] else "⚠ NEUT/BEAR"
        macd_z  = "ZERO+" if r["macd_zero"] else "ZERO-"
        macd_h  = "HIST▲" if r["macd_bull"] else "HIST▼"
        vol_tag = f"Vol {r['vol_ratio']}x"
        return (
            f"  {r['ticker']}  Harga {r['harga']:,.0f}\n"
            f"  Struktur: {struct} | {macd_h} {macd_z} | {vol_tag}"
        )

    if bull:
        lines.append(f"🟢 BULLISH DIVERGENCE ({len(bull)}) — Potensi Reversal Naik")
        lines.append("  Price Lower Low | MACD Higher Low")
        for r in bull:
            lines.append(fmt_row(r))
        lines.append("")

    if h_bull:
        lines.append(f"🔵 HIDDEN BULL DIV ({len(h_bull)}) — Trend Continuation Naik")
        lines.append("  Price Higher Low | MACD Lower Low")
        for r in h_bull:
            lines.append(fmt_row(r))
        lines.append("")

    if h_bear:
        lines.append(f"🟠 HIDDEN BEAR DIV ({len(h_bear)}) — Trend Continuation Turun")
        lines.append("  Price Lower High | MACD Higher High")
        for r in h_bear:
            lines.append(fmt_row(r))
        lines.append("")

    if bear:
        lines.append(f"🔴 BEARISH DIVERGENCE ({len(bear)}) — Potensi Reversal Turun")
        lines.append("  Price Higher High | MACD Lower High")
        for r in bear:
            lines.append(fmt_row(r))
        lines.append("")

    lines.append(
        "⚠ Konfirmasi manual wajib sebelum entry\n"
        "🟢=Reversal naik 🔵=Lanjut naik 🟠=Lanjut turun 🔴=Reversal turun"
    )
    return "\n".join(lines)


_macd_div_last: dict = {"results": [], "ts": None, "label": ""}

def _macd_div_job(label: str = ""):
    """Job scheduler — scan MACD divergence + kirim Telegram."""
    if not is_trading_day():
        return
    results = scan_macd_divergence(ISSI_UNIVERSE)
    msg     = format_macd_div_telegram(results, label)
    send_telegram(msg)
    with _state_lock:
        _macd_div_last["results"] = results
        _macd_div_last["ts"]      = datetime.now(WIB).strftime("%H:%M WIB")
        _macd_div_last["label"]   = label
    LOG.info(f"_macd_div_job | label={label} found={len(results)}")


# [VWAP+EMA SCANNER] — Signal A detection Daily
_vwap_ema_last: dict = {"results": [], "ts": None, "label": ""}

def scan_vwap_ema_signal(universe: list) -> list:
    """
    Scan ISSI universe untuk signal A (9EMA + Weekly VWAP cross).
    Logic: close > EMA9 > Weekly VWAP + volume > 1.5x avg + OBV rising
    """
    results   = []
    tz_wib    = pytz.timezone("Asia/Jakarta")
    today_wib = datetime.now(tz_wib).date()

    for ticker in universe:
        try:
            df = yf.download(
                ticker, period="60d", interval="1d",
                progress=False, auto_adjust=True,
            )
            if df is None or len(df) < 20:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            df = df[df.index.date <= today_wib].sort_index()

            if len(df) < 20:
                continue

            close  = df["Close"].squeeze()
            high   = df["High"].squeeze()
            low    = df["Low"].squeeze()
            volume = df["Volume"].squeeze()
            hlc3   = (high + low + close) / 3

            ema9 = close.ewm(span=9, adjust=False).mean()

            # Weekly Anchored VWAP
            df2 = df.copy()
            df2["dow"]         = pd.to_datetime(df2.index).dayofweek
            df2["is_new_week"] = (df2["dow"] == 0) & (df2["dow"].shift(1) != 0)
            df2["tpvol"]       = hlc3 * volume
            _cv, _ct = 0.0, 0.0
            vwap_vals = []
            for i in range(len(df2)):
                if df2["is_new_week"].iloc[i] or i == 0:
                    _cv = float(volume.iloc[i])
                    _ct = float(df2["tpvol"].iloc[i])
                else:
                    _cv += float(volume.iloc[i])
                    _ct += float(df2["tpvol"].iloc[i])
                vwap_vals.append(_ct / _cv if _cv > 0 else float("nan"))
            weekly_vwap = pd.Series(vwap_vals, index=df.index)

            # OBV
            obv_dir    = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
            obv        = (volume * obv_dir).cumsum()
            vol_ma     = volume.rolling(20).mean()

            c_last   = float(close.iloc[-1])
            c_prev   = float(close.iloc[-2])
            e_last   = float(ema9.iloc[-1])
            e_prev   = float(ema9.iloc[-2])
            v_last   = float(weekly_vwap.iloc[-1]) if not np.isnan(weekly_vwap.iloc[-1]) else 0
            v_prev   = float(weekly_vwap.iloc[-2]) if not np.isnan(weekly_vwap.iloc[-2]) else 0
            vol_last = float(volume.iloc[-1])
            vol_ma_v = float(vol_ma.iloc[-1]) if not np.isnan(vol_ma.iloc[-1]) else 1
            obv_last = float(obv.iloc[-1])
            obv_prev = float(obv.iloc[-2])

            curr_bull  = c_last > e_last and e_last > v_last
            prev_bull  = c_prev > e_prev and e_prev > v_prev
            bull_cross = curr_bull and not prev_bull

            if not curr_bull:
                continue

            high_vol   = vol_last >= vol_ma_v * 1.5
            obv_rising = obv_last > obv_prev

            o_last = float(df["Open"].iloc[-1])
            o_prev = float(df["Open"].iloc[-2])
            body     = abs(c_last - o_last)
            low_wick = min(c_last, o_last) - float(low.iloc[-1])
            up_wick  = float(high.iloc[-1]) - max(c_last, o_last)
            is_hammer    = body > 0 and low_wick >= 2*body and up_wick <= body*0.5 and c_last > o_last
            is_engulfing = c_last > o_last and o_prev > c_prev and c_last > o_prev and o_last < c_prev
            bullish_candle = is_hammer or is_engulfing

            # MACD 8/17/9
            ema_fast    = close.ewm(span=8,  adjust=False).mean()
            ema_slow    = close.ewm(span=17, adjust=False).mean()
            macd        = ema_fast - ema_slow
            macd_signal = macd.ewm(span=9, adjust=False).mean()
            hist_macd   = macd - macd_signal
            macd_bull      = float(hist_macd.iloc[-1]) > float(hist_macd.iloc[-2])
            macd_above_zero = float(macd.iloc[-1]) > 0

            if bull_cross and high_vol and bullish_candle and obv_rising:
                grade = "A+"
            elif bull_cross and (high_vol or bullish_candle) and obv_rising:
                grade = "A"
            elif bull_cross:
                grade = "B"
            else:
                grade = "HOLD"

            score = 0
            if curr_bull:       score += 30
            if bull_cross:      score += 20
            if high_vol:        score += 20
            if obv_rising:      score += 15
            if bullish_candle:  score += 10
            if macd_bull:       score += 5

            results.append({
                "ticker":       ticker.replace(".JK", ""),
                "harga":        round(c_last, 0),
                "ema9":         round(e_last, 0),
                "vwap":         round(v_last, 0),
                "grade":        grade,
                "score":        score,
                "high_vol":     high_vol,
                "obv_rising":   obv_rising,
                "bull_cross":   bull_cross,
                "macd_bull":    macd_bull,
                "macd_zero":    macd_above_zero,
                "vol_ratio":    round(vol_last / vol_ma_v, 1) if vol_ma_v > 0 else 0,
            })

        except Exception as e:
            LOG.warning(f"vwap_ema_scan | {ticker} | {e}")
            continue

    grade_order = {"A+": 0, "A": 1, "B": 2, "HOLD": 3}
    results.sort(key=lambda x: (grade_order.get(x["grade"], 9), -x["score"]))
    LOG.info(f"vwap_ema_scan | scanned={len(universe)} signals={len(results)}")
    return results


def format_vwap_ema_telegram(results: list, label: str = "") -> str:
    """Format hasil VWAP+EMA scan jadi pesan Telegram."""
    now_str = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    tag     = f" [{label}]" if label else ""
    show    = [r for r in results if r["grade"] in ("A+", "A", "B")]

    if not show:
        return (
            f"📊 VWAP+EMA SCAN{tag} — {now_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Tidak ada signal A+/A/B hari ini."
        )

    grade_emoji = {"A+": "🟢", "A": "🟡", "B": "🔵"}
    lines = [
        f"📊 VWAP+EMA SCAN{tag} — {now_str}",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"9EMA > VWAP Cross  |  {len(show)} signal\n",
    ]
    for r in show:
        em       = grade_emoji.get(r["grade"], "⚪")
        vol_tag  = f"Vol {r['vol_ratio']}x" if r["high_vol"] else "Vol rendah"
        obv_tag  = "OBV▲" if r["obv_rising"] else "OBV▼"
        macd_tag = "MACD▲" if r["macd_bull"] else "MACD▼"
        zero_tag = "ZERO+" if r["macd_zero"] else "ZERO-"
        cross_tag = "CROSS✓" if r["bull_cross"] else "HOLD"
        lines.append(
            f"{em} {r['ticker']}  [{r['grade']}]  Score:{r['score']}\n"
            f"   Harga {r['harga']:,.0f}  EMA9 {r['ema9']:,.0f}  VWAP {r['vwap']:,.0f}\n"
            f"   {cross_tag} | {vol_tag} | {obv_tag} | {macd_tag} {zero_tag}"
        )

    lines.append(
        "\n⚠ Konfirmasi manual: cek MACD Daily + tabel TF sebelum entry\n"
        "🟢=A+ (full) 🟡=A (normal) 🔵=B (wait/kecil)"
    )
    return "\n".join(lines)


def _vwap_ema_job(label: str = ""):
    """Job scheduler — scan VWAP+EMA + kirim Telegram."""
    if not is_trading_day():
        return
    results = scan_vwap_ema_signal(ISSI_UNIVERSE)
    msg     = format_vwap_ema_telegram(results, label)
    send_telegram(msg)
    with _state_lock:
        _vwap_ema_last["results"] = results
        _vwap_ema_last["ts"]      = datetime.now(WIB).strftime("%H:%M WIB")
        _vwap_ema_last["label"]   = label
    LOG.info(f"_vwap_ema_job | label={label} signals={len(results)}")


# State simpan hasil breakout terakhir (thread-safe via _state_lock)
_breakout_last: dict = {"results": [], "ts": None, "label": ""}

def _breakout_job(label: str = ""):
    """Job dipanggil scheduler — scan + kirim Telegram."""
    if not is_trading_day():
        return
    results = scan_breakout_yesterday_high(ISSI_UNIVERSE)
    msg     = format_breakout_telegram(results, label)
    send_telegram(msg)
    with _state_lock:
        _breakout_last["results"] = results
        _breakout_last["ts"]      = datetime.now(WIB).strftime("%H:%M WIB")
        _breakout_last["label"]   = label
    LOG.info(f"_breakout_job | label={label} results={len(results)}")


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

    # ── [V5.8.0] Breakout Yesterday High — scan per 15 menit 09:00–10:00 ──
    # Tangkap breakout di jam panas pembukaan bursa
    # 09:00 = pre-breakout (data kemarin vs open hari ini)
    # 09:15, 09:30, 09:45 = monitor progression
    # 10:00 = konfirmasi final 1 jam pertama
    _breakout_schedule = [
        {"hour": 9,  "minute": 0,  "label": "09:00 Open"},
        {"hour": 9,  "minute": 15, "label": "09:15"},
        {"hour": 9,  "minute": 30, "label": "09:30"},
        {"hour": 9,  "minute": 45, "label": "09:45"},
        {"hour": 10, "minute": 0,  "label": "10:00 Konfirmasi"},
    ]
    for bs in _breakout_schedule:
        scheduler.add_job(
            func    = lambda lbl=bs["label"]: _breakout_job(lbl),
            trigger = CronTrigger(
                day_of_week = "mon-fri",
                hour        = bs["hour"],
                minute      = bs["minute"],
                timezone    = WIB,
            ),
            id               = f"breakout_{bs['hour']:02d}{bs['minute']:02d}",
            name             = f"Breakout Scan {bs['label']}",
            replace_existing = True,
            misfire_grace_time = 60,
        )

    # ── [MACD DIVERGENCE] Scan 2x sehari ────────────────────
    _div_schedule = [
        {"hour": 8,  "minute": 30, "label": "08:30 Pre-Market"},
        {"hour": 15, "minute": 45, "label": "15:45 Post-Close"},
    ]
    for ds in _div_schedule:
        scheduler.add_job(
            func    = lambda lbl=ds["label"]: _macd_div_job(lbl),
            trigger = CronTrigger(
                day_of_week = "mon-fri",
                hour        = ds["hour"],
                minute      = ds["minute"],
                timezone    = WIB,
            ),
            id               = f"macd_div_{ds['hour']:02d}{ds['minute']:02d}",
            name             = f"MACD Div Scan {ds['label']}",
            replace_existing = True,
            misfire_grace_time = 120,
        )

    # ── [VWAP+EMA] Signal scan 3x sehari ────────────────────
    _vwap_schedule = [
        {"hour": 8,  "minute": 45, "label": "08:45 Pre-Market"},
        {"hour": 11, "minute": 30, "label": "11:30 Mid-Session"},
        {"hour": 15, "minute": 30, "label": "15:30 Post-Close"},
    ]
    for vs in _vwap_schedule:
        scheduler.add_job(
            func    = lambda lbl=vs["label"]: _vwap_ema_job(lbl),
            trigger = CronTrigger(
                day_of_week = "mon-fri",
                hour        = vs["hour"],
                minute      = vs["minute"],
                timezone    = WIB,
            ),
            id               = f"vwap_ema_{vs['hour']:02d}{vs['minute']:02d}",
            name             = f"VWAP+EMA Scan {vs['label']}",
            replace_existing = True,
            misfire_grace_time = 120,
        )

    scheduler.start()

    # Notifikasi startup
    send_telegram(
        f"🟢 ATS SuperEngine {APP_VERSION} — SERVER ONLINE\n"
        f"⏰ {datetime.now(WIB).strftime('%Y-%m-%d %H:%M WIB')}\n"
        f"Full scan: 09:05 | 09:30 | 11:30 | 13:35 | 15:00 WIB\n"
        f"Intraday refresh: setiap 15 menit jam bursa\n"
        f"🚀 Breakout scan: 09:00 | 09:15 | 09:30 | 09:45 | 10:00 WIB\n"
        f"📊 VWAP+EMA scan: 08:45 | 11:30 | 15:30 WIB\n"
        f"📈 MACD Div scan: 08:30 | 15:45 WIB\n"
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

tabs = st.tabs(["📖 HOW TO USE", "📊 TRADING DESK", "💼 ACCOUNT", "📋 REPORT", "🕌 ISSI CHECK", "🚀 BREAKOUT SCAN", "📚 WISDOM"])

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
        "**Bandar Hunter** adalah radar awal pergerakan institusional — "
        "bukan sinyal beli/jual. "
        "Tugasnya mendeteksi jejak bandar SEBELUM ATS dan Falcon konfirmasi. "
        "Selalu butuh konfirmasi berlapis sebelum eksekusi apapun."
    )

    # ── 4 Sinyal ─────────────────────────────────────────────
    st.markdown("### 📡 4 Sinyal yang Dideteksi")
    bh_s1, bh_s2, bh_s3, bh_s4 = st.columns(4)
    bh_s1.success("**⚡ MARKUP**\n\nBandar mulai push harga agresif.\nVol spike 4×+ dalam 1 candle.")
    bh_s2.info("**🤫 AKUMULASI**\n\nBandar kumpul diam-diam.\nVol naik 3+ hari, harga sideways.")
    bh_s3.warning("**🔊 ANOMALI**\n\nVol ekstrem tapi harga flat.\nArah belum jelas.")
    bh_s4.error("**🔴 DISTRIBUSI**\n\nBandar jual ke retail.\nHarga naik tapi vol turun.")

    st.markdown("---")

    # ── STRICT RULES ─────────────────────────────────────────
    st.markdown("### 🚫 STRICT RULES — Tidak Ada Pengecualian")
    st.error("""
**RULE 1 — Satu hari sinyal BELUM CUKUP**
Sinyal BH harus konsisten minimal 3–5 hari berturut sebelum dipertimbangkan.
Contoh: ADRO detect akumulasi 1 hari → langsung masuk = SALAH.
Tunggu BH scan hari berikutnya — apakah sinyal berlanjut?

**RULE 2 — Konfirmasi IPOT wajib sebelum aksi apapun**
Setelah BH detect, buka IPOT dan cek:
- Bid ratio > 45% ✅
- Accum/Dist positif dan naik ✅
- Broker summary: tidak ada wash trading (bukan 1 broker beli = jual) ✅
- Foreign net tidak negatif besar ✅
Kalau salah satu merah → SKIP.

**RULE 3 — Konfirmasi D1 wajib sebelum entry**
BH pakai data 5 menit. Keputusan entry harus dari D1.
Cek di TradingView: harga di atas MA50? Eagle/Phoenix ada signal? Support terdekat di mana?
Tanpa konfirmasi D1 → JANGAN ENTRY.

**RULE 4 — Wash trading = abaikan sinyal**
Kalau broker summary menunjukkan 1 broker dengan buy lot = sell lot persis identik → itu manipulasi volume, bukan bandar genuine.
Contoh nyata: KBLI hari ini — 1 broker beli 5.640 lot = jual 5.640 lot = sinyal palsu.

**RULE 5 — Volume spike + harga turun = DISTRIBUSI, bukan akumulasi**
Kalau BH detect markup tapi harga justru turun di IPOT → thesis salah.
Contoh nyata: ADRO pagi ini — BH detect akumulasi, tapi harga turun -2.1% dengan vol besar = distribusi.
Percaya data IPOT, bukan BH saja.

**RULE 6 — Cek corporate action sebelum interpretasi**
Saham ex-dividen akan turun adjusted — ini bukan distribusi, ini mekanisme pasar.
Selalu cek tab Corp. Action di IPOT sebelum baca sinyal BH.
Contoh: AKRA dan BRIS ex-dividen hari ini — penurunan harga wajar, bukan sinyal bearish.

**RULE 7 — SL terpasang langsung setelah fill**
Ini bukan rule BH — ini rule trading universal yang TIDAK BISA DILANGGAR.
Apapun sinyalnya, SL hard stop di broker segera setelah order fill.
Tidak ada mental stop. Tidak ada "nanti aku pasang".
    """)

    st.markdown("---")

    # ── ALUR PENGGUNAAN ──────────────────────────────────────
    st.markdown("### ✅ Alur Penggunaan yang Benar")
    st.markdown("""
```
STEP 1 — BH Scan (ATS tab atau TV Pine)
         Deteksi sinyal: Markup / Akumulasi / Anomali / Distribusi
         ↓
STEP 2 — Cek konsistensi (apakah sinyal sudah 2-3 hari?)
         1 hari → watchlist saja
         2-3 hari → lanjut ke step 3
         ↓
STEP 3 — Konfirmasi IPOT real-time
         Bid ratio > 45%?
         Accum/Dist positif?
         Broker summary genuine (bukan wash trading)?
         Corporate action ada?
         ↓
STEP 4 — Konfirmasi D1 di TradingView
         Harga di atas MA50?
         Eagle/Phoenix/Falcon ada signal?
         Support dan resistance jelas?
         ↓
STEP 5 — Tunggu ATS scan konfirmasi
         Kalau ATS juga keluarkan sinyal = ATS+BH = conviction tinggi
         ↓
STEP 6 — Entry dengan SL langsung terpasang
         Hard stop di broker segera setelah fill
         Tidak ada pengecualian
```
    """)

    st.markdown("---")

    # ── PELAJARAN DARI LIVE TRADING ──────────────────────────
    st.markdown("### 📚 Pelajaran dari Live Trading")
    st.warning("""
**Kasus nyata yang sudah terjadi — jangan diulang:**

**ADRO 20 Mei 2026:**
BH detect akumulasi jam 10:20 — broker besar BK 249B masuk, bid ratio 54%.
Entry di 2.300. Jam 11:00 harga jatuh ke 2.230.
**Pelajaran:** Institusi besar masuk untuk markup sesaat lalu jual ke retail (pump & dump).
Filter tambahan: kalau saham sudah naik >3-4% dalam 50 menit sebelum entry → HIGH RISK, tunggu konsolidasi dulu.

**KBLI hari ini:**
BH detect vol 8.9× — terlihat kuat.
Cek IPOT broker summary: 1 broker beli 5.640 lot = jual 5.640 lot persis.
**Pelajaran:** Wash trading. Volume palsu. Selalu cek broker summary sebelum percaya sinyal BH.

**AKRA & BRIS:**
Harga turun signifikan — terlihat seperti distribusi di BH.
Ternyata ex-dividen.
**Pelajaran:** Selalu cek corporate action sebelum interpretasi sinyal.
    """)

    st.markdown("---")

    # ── BH + PINE TV ─────────────────────────────────────────
    st.markdown("### 🖥️ Bandar Hunter Pine Script (TradingView)")
    st.info("""
**Bandar Hunter juga tersedia sebagai Pine Script untuk TradingView H1.**

Label yang muncul di chart:
- **[MARKUP]** orange — di bawah candle
- **[AKUMULASI]** teal — di bawah candle
- **[ANOMALI]** kuning — di bawah candle
- **[DISTRIBUSI]** merah — di atas candle

Panel bawah: Vol Ratio dengan garis threshold 4× (markup) dan 1.5× (akumulasi).

**Alert setup:** Klik bell icon TV → pilih Bandar Hunter → Any alert() call → aktifkan notif HP.
Setelah TV Pro aktif, alert real-time tanpa delay.

**Cara pakai di TV:** Pasang di chart H1 saham yang sudah masuk radar BH ATS.
Konfirmasi dengan Phoenix H1 yang sudah ada — kalau keduanya setuju di candle yang sama = conviction tertinggi.
    """)

    st.warning(
        "⚠️ **Keterbatasan:** BH ATS pakai data yfinance 5m — bukan broker flow data sesungguhnya. "
        "BH Pine TV pakai data delayed 15 menit di free plan. "
        "False positive tinggi di saham tidak likuid dan wash trading. "
        "SELALU konfirmasi dengan IPOT broker summary sebelum aksi apapun."
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

                # [V6.0.0 #1] Breakdown 5 komponen weighted (bukan base+bonus stack)
                if bd.get("trend", 0)    != 0: lines_md.append(fmt(bd["trend"],    "trend (prob+runner)"))
                if bd.get("bandar", 0)   != 0: lines_md.append(fmt(bd["bandar"],   "bandar"))
                if bd.get("breakout", 0) != 0: lines_md.append(fmt(bd["breakout"], "breakout"))
                if bd.get("momentum", 0) != 0: lines_md.append(fmt(bd["momentum"], "momentum (mom+ft)"))
                if bd.get("rr", 0)       != 0: lines_md.append(fmt(bd["rr"],       "risk/reward"))
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
    _debug = st.session_state.debug_log
    _has_debug = (
        _debug is not None and (
            (isinstance(_debug, list) and len(_debug) > 0) or
            (hasattr(_debug, '__len__') and len(_debug) > 0)
        )
    )
    if _has_debug:
        debug_df = pd.DataFrame(_debug) if isinstance(_debug, list) else _debug
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
    st.markdown("## 💼 Account & Investor Dashboard")

    # ── Session state untuk investor trades ──────────────────
    if "inv_trades" not in st.session_state:
        st.session_state.inv_trades = []
    if "inv_modal" not in st.session_state:
        st.session_state.inv_modal = int(st.session_state.get("balance", 6000000))
    if "inv_mutasi" not in st.session_state:
        st.session_state.inv_mutasi = []

    INV_FILE = "investor_trades.json"

    def inv_save():
        try:
            data = {
                "modal":   st.session_state.inv_modal,
                "trades":  st.session_state.inv_trades,
                "mutasi":  st.session_state.inv_mutasi,
            }
            with open(INV_FILE, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            st.error(f"Gagal simpan: {e}")

    def inv_load():
        try:
            if os.path.exists(INV_FILE):
                with open(INV_FILE) as f:
                    data = json.load(f)
                st.session_state.inv_modal  = data.get("modal", 6000000)
                st.session_state.inv_trades = data.get("trades", [])
                st.session_state.inv_mutasi = data.get("mutasi", [])
                # Sync balance ke ATS engine
                st.session_state.balance = int(st.session_state.inv_modal)
        except Exception:
            pass

    def inv_calc_pnl(t):
        if not t.get("exit") or t.get("status") == "OPEN":
            return None
        return (float(t["exit"]) - float(t["entry"])) * int(t.get("lot", 1)) * 100

    def inv_calc_rr(t):
        try:
            entry, sl, tp = float(t["entry"]), float(t["sl"]), float(t["tp"])
            if entry == sl:
                return None
            return round(abs(tp - entry) / abs(entry - sl), 2)
        except Exception:
            return None

    def inv_fmt_idr(n):
        if n is None or (isinstance(n, float) and np.isnan(n)):
            return "—"
        abs_n = abs(n)
        s = f"{abs_n:,.0f}"
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
        sign = "-" if n < 0 else ""
        return f"{sign}Rp. {s}"

    # Load data saat pertama kali
    if "inv_loaded" not in st.session_state:
        inv_load()
        st.session_state.inv_loaded = True

    trades = st.session_state.inv_trades

    # ── KPI STRIP ─────────────────────────────────────────────
    closed = [t for t in trades if t.get("status") != "OPEN"]
    wins   = [t for t in closed if t.get("status") == "WIN"]
    losses = [t for t in closed if t.get("status") == "LOSS"]
    open_t = [t for t in trades if t.get("status") == "OPEN"]

    total_pnl = sum(inv_calc_pnl(t) or 0 for t in closed)
    equity    = st.session_state.inv_modal + total_pnl
    wr        = round(len(wins) / len(closed) * 100) if closed else 0
    rr_vals   = [inv_calc_rr(t) for t in trades if inv_calc_rr(t)]
    avg_rr    = round(sum(rr_vals) / len(rr_vals), 1) if rr_vals else 0
    pnl_pct   = round(total_pnl / st.session_state.inv_modal * 100, 2) if st.session_state.inv_modal else 0

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Modal",       inv_fmt_idr(st.session_state.inv_modal))
    k2.metric("Equity",      inv_fmt_idr(equity),
              delta=f"{pnl_pct:+.2f}%",
              delta_color="normal")
    k3.metric("Total PnL",   inv_fmt_idr(total_pnl),
              delta_color="normal")
    k4.metric("Win Rate",    f"{wr}%",
              delta=f"{len(wins)}P / {len(losses)}L")
    k5.metric("Avg RR",      f"{avg_rr}R")
    k6.metric("Open",        len(open_t))

    st.markdown("---")

    # ── EQUITY CURVE ──────────────────────────────────────────
    if closed:
        st.subheader("📈 Equity Curve")
        pnl_list  = [inv_calc_pnl(t) or 0 for t in closed]
        cum_pnl   = []
        running   = 0
        for p in pnl_list:
            running += p
            cum_pnl.append(running)

        eq_df = pd.DataFrame({
            "Trade#": list(range(1, len(cum_pnl) + 1)),
            "PnL Kumulatif": cum_pnl,
        })
        fig_eq = go.Figure()
        fig_eq.add_trace(go.Scatter(
            x=eq_df["Trade#"], y=eq_df["PnL Kumulatif"],
            mode="lines+markers", name="Equity",
            line=dict(color="#00c896", width=2),
            fill="tozeroy", fillcolor="rgba(0,200,150,0.1)"
        ))
        fig_eq.update_layout(
            height=220, margin=dict(l=0, r=0, t=10, b=0),
            xaxis_title="Trade #", yaxis_title="PnL Kumulatif (Rp)",
            plot_bgcolor="#0d1117", paper_bgcolor="#0d1117",
            font_color="#d4dce8",
        )
        st.plotly_chart(fig_eq, use_container_width=True)

        # Drawdown
        cum_arr = np.array(cum_pnl)
        dd      = cum_arr - np.maximum.accumulate(cum_arr)
        max_dd  = dd.min()
        st.metric("Max Drawdown", inv_fmt_idr(max_dd))

    st.markdown("---")

    # ── INPUT TRADE ───────────────────────────────────────────
    acc_tab1, acc_tab2, acc_tab3, acc_tab4, acc_tab5 = st.tabs(
        ["📋 Trade Log", "➕ Input Trade", "📥 Import IPOT", "💰 Modal & Mutasi", "🧠 Cybernetic"]
    )

    with acc_tab1:
        st.caption(f"**{len(trades)} trade** tersimpan")
        if not trades:
            st.info("Belum ada trade. Tambah di tab **Input Trade**.")
        else:
            rows = []
            for i, t in enumerate(trades):
                pnl = inv_calc_pnl(t)
                rr  = inv_calc_rr(t)
                rows.append({
                    "#":        i + 1,
                    "Tanggal":  t.get("date", "—"),
                    "Ticker":   t.get("ticker", "—"),
                    "Entry":    t.get("entry", 0),
                    "SL":       t.get("sl", 0),
                    "TP":       t.get("tp", 0),
                    "RR":       f"{rr}R" if rr else "—",
                    "Exit":     t.get("exit", "—"),
                    "PnL":      pnl,
                    "PnL%":     round((float(t.get("exit", 0)) - float(t["entry"])) / float(t["entry"]) * 100, 2)
                                if t.get("exit") and t.get("status") != "OPEN" else None,
                    "Status":   t.get("status", "OPEN"),
                    "Keterangan": t.get("note", ""),
                })
            df_trades = pd.DataFrame(rows)

            st.dataframe(
                df_trades,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "PnL":  st.column_config.NumberColumn("PnL (Rp)", format="%.0f"),
                    "PnL%": st.column_config.NumberColumn("PnL %",    format="%.2f%%"),
                }
            )

            # Download CSV
            csv_data = df_trades.to_csv(index=False).encode()
            st.download_button(
                "📥 Download Trade Log (CSV)", csv_data,
                file_name=f"ats_trades_{datetime.now(WIB).strftime('%Y%m%d')}.csv",
                mime="text/csv"
            )

            # Delete trade
            with st.expander("🗑️ Hapus Trade"):
                del_idx = st.number_input("Nomor trade yang mau dihapus (#)", min_value=1,
                                          max_value=len(trades), step=1, key="inv_del_idx")
                if st.button("Hapus Trade", type="secondary"):
                    st.session_state.inv_trades.pop(int(del_idx) - 1)
                    inv_save()
                    st.success("Trade dihapus")
                    st.rerun()

        # ── Statistik ──────────────────────────────────────────
        if closed:
            st.markdown("---")
            st.subheader("📊 Statistik Performa")
            avg_win  = np.mean([inv_calc_pnl(t) for t in wins  if inv_calc_pnl(t)]) if wins  else 0
            avg_loss = np.mean([inv_calc_pnl(t) for t in losses if inv_calc_pnl(t)]) if losses else 0
            pf       = abs(avg_win / avg_loss) if avg_loss else 0
            best     = max((inv_calc_pnl(t) or 0 for t in closed), default=0)
            worst    = min((inv_calc_pnl(t) or 0 for t in closed), default=0)

            s1, s2, s3, s4 = st.columns(4)
            s1.metric("Profit Factor", f"{pf:.2f}")
            s2.metric("Avg Win",       inv_fmt_idr(avg_win))
            s3.metric("Avg Loss",      inv_fmt_idr(avg_loss))
            s4.metric("Best Trade",    inv_fmt_idr(best))

            s5, s6, s7, s8 = st.columns(4)
            s5.metric("Worst Trade",   inv_fmt_idr(worst))
            s6.metric("Total Trades",  len(closed))
            s7.metric("Total Win",     len(wins))
            s8.metric("Total Loss",    len(losses))

    with acc_tab2:
        st.subheader("➕ Input Trade Baru")
        with st.form("inv_trade_form", clear_on_submit=True):
            fc1, fc2 = st.columns(2)
            with fc1:
                t_date   = st.date_input("Tanggal", value=datetime.now(WIB).date())
                t_ticker = st.text_input("Ticker", placeholder="BBCA").upper()
                t_entry  = st.number_input("Entry", min_value=1, step=1)
                t_sl     = st.number_input("Stop Loss", min_value=1, step=1)
            with fc2:
                t_tp     = st.number_input("Take Profit", min_value=1, step=1)
                t_lot    = st.number_input("Lot", min_value=1, step=1, value=1)
                t_exit   = st.number_input("Exit Price (0 = masih open)", min_value=0, step=1)
                t_status = st.selectbox("Status", ["OPEN", "WIN", "LOSS", "BE"])
            t_note = st.text_input("Keterangan", placeholder="BOD, AboveVWAP, MSS Bull...")

            submitted = st.form_submit_button("💾 Simpan Trade", type="primary",
                                              use_container_width=True)
            if submitted:
                if not t_ticker:
                    st.error("Ticker wajib diisi")
                else:
                    new_trade = {
                        "date":   str(t_date),
                        "ticker": t_ticker,
                        "entry":  t_entry,
                        "sl":     t_sl,
                        "tp":     t_tp,
                        "lot":    t_lot,
                        "exit":   t_exit if t_exit > 0 else None,
                        "status": t_status,
                        "note":   t_note,
                    }
                    st.session_state.inv_trades.append(new_trade)
                    inv_save()
                    # Sync journal ATS juga
                    pnl_val = inv_calc_pnl(new_trade)
                    new_row = pd.DataFrame([{
                        "Date":   str(t_date),
                        "Ticker": t_ticker,
                        "Entry":  t_entry,
                        "Exit":   t_exit if t_exit > 0 else None,
                        "Lot":    t_lot,
                        "PnL":    pnl_val,
                        "Notes":  t_note,
                    }])
                    st.session_state.journal = pd.concat(
                        [st.session_state.journal, new_row], ignore_index=True
                    )
                    st.session_state.journal.to_csv(JOURNAL_FILE, index=False)
                    st.success(f"✅ Trade {t_ticker} tersimpan & sync ke Journal")
                    st.rerun()

    with acc_tab3:
        st.subheader("📥 Import Mutasi IPOT")
        st.caption(
            "Copy-paste semua baris dari halaman Mutasi Rekening IPOT (web/app). "
            "Sistem akan parse semua transaksi: Pembelian, Penjualan, Deviden, WD, Setor, XRDN — "
            "lalu tampilkan laporan keuangan lengkap dan trade log otomatis."
        )

        imp_tab1, imp_tab2 = st.tabs(["📋 Paste Mutasi", "📊 Laporan Keuangan"])

        with imp_tab1:
            st.markdown("**Cara pakai:**")
            st.markdown(
                "**Opsi A — Upload File (Lebih Akurat):**  \n"
                "1. Copy mutasi dari IPOT web  \n"
                "2. Paste ke Qwen/ChatGPT: *'rapikan data ini jadi tabel rapi, jangan ubah angka'*  \n"
                "3. Copy hasilnya → simpan ke file .txt → upload di sini  \n\n"
                "**Opsi B — Paste Langsung:**  \n"
                "1. Buka IPOT web → Account → Mutasi Rekening  \n"
                "2. Pilih periode → Select All → Copy → Paste  \n"
                "3. Klik **Proses**"
            )

            # Input method: upload file atau paste teks
            input_method = st.radio(
                "Metode Input",
                ["📁 Upload File (.txt)", "📋 Paste Teks"],
                horizontal=True,
                key="mutasi_input_method"
            )

            mutasi_text = ""
            if input_method == "📁 Upload File (.txt)":
                st.caption("Upload file .txt hasil export dari Qwen/Notepad. Format: fixed-width dengan spasi sebagai separator.")
                uploaded_txt = st.file_uploader(
                    "Upload file mutasi (.txt)",
                    type=["txt"],
                    key="mutasi_txt_upload"
                )
                if uploaded_txt is not None:
                    mutasi_text = uploaded_txt.read().decode("utf-8", errors="ignore")
                    st.success(f"✅ File '{uploaded_txt.name}' berhasil dibaca — {len(mutasi_text.splitlines())} baris")
                    with st.expander("Preview 5 baris pertama"):
                        st.code("\n".join(mutasi_text.splitlines()[:5]))
            else:
                mutasi_text = st.text_area(
                    "Paste data mutasi di sini",
                    height=200,
                    placeholder="Copy dari IPOT web lalu paste di sini...",
                    key="ipot_mutasi_text"
                )

            col_p1, col_p2 = st.columns(2)
            with col_p1:
                mutasi_label = st.text_input("Label periode (opsional)", placeholder="Jun 2026", key="mutasi_label")
            with col_p2:
                overwrite_mutasi = st.checkbox("Hapus data sebelumnya", value=False, key="mutasi_overwrite")

            if st.button("🔍 Proses Mutasi", type="primary", use_container_width=True, key="btn_proses_mutasi"):
                if not mutasi_text.strip():
                    st.warning("Upload file atau paste data mutasi dulu.")
                else:
                    with st.spinner("Memproses..."):
                        try:
                            # Parse tab-separated
                            import io
                            lines = mutasi_text.strip().split("\n")
                            parsed_rows = []

                            import re
                            month_map = {
                                "jan":"01","feb":"02","mar":"03","apr":"04",
                                "may":"05","jun":"06","jul":"07","aug":"08",
                                "sep":"09","oct":"10","nov":"11","dec":"12"
                            }

                            def parse_dt(s):
                                s = s.strip()
                                parts = s.split()
                                if len(parts) == 3:
                                    d = parts[0].zfill(2)
                                    m = month_map.get(parts[1].lower()[:3], "01")
                                    y = parts[2]
                                    if len(y) == 2: y = "20" + y
                                    return f"{y}-{m}-{d}"
                                return s

                            def clean_num(s):
                                if not s or s.strip() == "": return None
                                s = s.strip().replace(",", "").replace(" ", "")
                                try: return float(s)
                                except: return None

                            def categorize(trx):
                                trx = trx.strip()
                                tl  = trx.lower()
                                if "pembelian saham" in tl:
                                    m = re.search(r"pembelian saham ([A-Z]{2,5})", trx, re.IGNORECASE)
                                    return "BUY", m.group(1).upper() if m else ""
                                elif "penjualan saham" in tl:
                                    m = re.search(r"penjualan saham ([A-Z]{2,5})", trx, re.IGNORECASE)
                                    return "SELL", m.group(1).upper() if m else ""
                                elif "deviden" in tl:
                                    m = re.search(r"deviden\s+\w+\s+([A-Z]{2,5})", trx, re.IGNORECASE)
                                    return "DIVIDEN", m.group(1).upper() if m else ""
                                elif "penarikan" in tl or "wd" in tl or "withdrawal" in tl:
                                    return "TARIK", ""
                                elif "receive payment" in tl or "setor" in tl or "deposit" in tl:
                                    return "SETOR", ""
                                elif "placement xrdn" in tl:
                                    return "XRDN_IN", ""
                                elif "liquidation xrdn" in tl:
                                    return "XRDN_OUT", ""
                                elif "biaya" in tl or "penalty" in tl:
                                    return "BIAYA", ""
                                else:
                                    return "LAINNYA", ""

                            for line in lines:
                                line = line.strip()
                                if not line: continue
                                if "Trx Date" in line or "TrxDate" in line: continue

                                cols = line.split("\t")
                                if len(cols) < 6: continue

                                trx_date = parse_dt(cols[0])
                                due_date  = parse_dt(cols[1]) if len(cols) > 1 else ""
                                trx_desc  = cols[2].strip() if len(cols) > 2 else ""
                                price     = clean_num(cols[3]) if len(cols) > 3 else None
                                volume    = clean_num(cols[4]) if len(cols) > 4 else None
                                amount    = clean_num(cols[5]) if len(cols) > 5 else None
                                balance   = clean_num(cols[6]) if len(cols) > 6 else None
                                days      = clean_num(cols[7]) if len(cols) > 7 else None
                                penalty   = clean_num(cols[8]) if len(cols) > 8 else None

                                cat, ticker = categorize(trx_desc)

                                parsed_rows.append({
                                    "trx_date":  trx_date,
                                    "due_date":  due_date,
                                    "desc":      trx_desc,
                                    "category":  cat,
                                    "ticker":    ticker,
                                    "price":     price,
                                    "volume":    volume,
                                    "amount":    amount,
                                    "balance":   balance,
                                    "days":      days,
                                    "penalty":   penalty,
                                    "label":     mutasi_label,
                                })

                            if not parsed_rows:
                                st.warning("Tidak ada data yang berhasil diparsing. Pastikan format tab-separated.")
                            else:
                                df_raw = pd.DataFrame(parsed_rows)

                                # Simpan ke session state
                                if overwrite_mutasi:
                                    st.session_state.inv_mutasi_raw = parsed_rows
                                else:
                                    existing = st.session_state.get("inv_mutasi_raw", [])
                                    st.session_state.inv_mutasi_raw = existing + parsed_rows

                                # Simpan ke file
                                mutasi_file = "investor_mutasi.json"
                                import json as _json
                                with open(mutasi_file, "w") as f:
                                    _json.dump(st.session_state.inv_mutasi_raw, f, default=str)

                                st.success(f"✅ {len(parsed_rows)} baris berhasil diproses!")

                                # Preview
                                st.dataframe(df_raw[[
                                    "trx_date","desc","category","ticker",
                                    "price","volume","amount","balance"
                                ]], use_container_width=True, hide_index=True)

                                # Auto-extract trade log dari BUY/SELL
                                buys  = [r for r in parsed_rows if r["category"] == "BUY"]
                                sells = [r for r in parsed_rows if r["category"] == "SELL"]

                                if buys or sells:
                                    st.markdown("---")
                                    st.markdown(f"**Trade terdeteksi:** {len(buys)} pembelian, {len(sells)} penjualan")

                                    # Group by ticker untuk trade log
                                    trade_df = pd.DataFrame(buys + sells)
                                    grouped_trades = group_ipot_trades(trade_df.rename(columns={
                                        "trx_date": "date", "category": "action"
                                    }))

                                    if grouped_trades and st.button("📥 Import ke Trade Log", key="btn_import_from_mutasi"):
                                        clean = [{
                                            "date":   g["date"],
                                            "ticker": g["ticker"],
                                            "entry":  g["entry"],
                                            "sl":     0,
                                            "tp":     0,
                                            "lot":    g["lot"],
                                            "exit":   g["exit"],
                                            "status": g["status"],
                                            "note":   g["note"],
                                        } for g in grouped_trades]

                                        existing_t = {t["ticker"] for t in st.session_state.inv_trades}
                                        new_t = [t for t in clean if t["ticker"] not in existing_t]
                                        st.session_state.inv_trades.extend(new_t)
                                        inv_save()
                                        st.success(f"✅ {len(new_t)} trade diimport ke Trade Log")
                                        st.rerun()

                        except Exception as e:
                            st.error(f"❌ Error: {e}")

        with imp_tab2:
            st.subheader("📊 Laporan Keuangan")

            # Load data mutasi
            mutasi_file = "investor_mutasi.json"
            import json as _json
            if "inv_mutasi_raw" not in st.session_state:
                try:
                    if os.path.exists(mutasi_file):
                        with open(mutasi_file) as f:
                            st.session_state.inv_mutasi_raw = _json.load(f)
                    else:
                        st.session_state.inv_mutasi_raw = []
                except Exception:
                    st.session_state.inv_mutasi_raw = []

            all_mutasi = st.session_state.get("inv_mutasi_raw", [])

            if not all_mutasi:
                st.info("Belum ada data. Paste mutasi di tab **Paste Mutasi** dulu.")
            else:
                df_m = pd.DataFrame(all_mutasi)

                # Filter periode
                labels = sorted(set(r.get("label","") for r in all_mutasi if r.get("label","")))
                labels = ["Semua"] + labels
                sel_label = st.selectbox("Filter Periode", labels, key="lap_label_filter")

                if sel_label != "Semua":
                    df_m = df_m[df_m["label"] == sel_label]

                if df_m.empty:
                    st.warning("Tidak ada data untuk periode ini.")
                else:
                    # Hitung summary per kategori
                    def sum_amt(cat):
                        rows = df_m[df_m["category"] == cat]["amount"]
                        return rows.sum() if not rows.empty else 0

                    total_beli    = abs(sum_amt("BUY"))
                    total_jual    = sum_amt("SELL")
                    total_dividen = sum_amt("DIVIDEN")
                    total_tarik   = abs(sum_amt("TARIK"))
                    total_setor   = sum_amt("SETOR")
                    total_biaya   = abs(sum_amt("BIAYA"))
                    trading_pnl   = total_jual - total_beli
                    saldo_akhir   = df_m["balance"].dropna().iloc[-1] if not df_m["balance"].dropna().empty else 0

                    # KPI
                    st.markdown("### 💰 Ringkasan Keuangan")
                    r1c1, r1c2, r1c3, r1c4 = st.columns(4)
                    r1c1.metric("Total Setor",   inv_fmt_idr(total_setor))
                    r1c2.metric("Total Tarik",   inv_fmt_idr(total_tarik))
                    r1c3.metric("Total Dividen", inv_fmt_idr(total_dividen))
                    r1c4.metric("Total Biaya",   inv_fmt_idr(total_biaya))

                    r2c1, r2c2, r2c3, r2c4 = st.columns(4)
                    r2c1.metric("Total Pembelian", inv_fmt_idr(total_beli))
                    r2c2.metric("Total Penjualan", inv_fmt_idr(total_jual))
                    r2c3.metric("Trading PnL",     inv_fmt_idr(trading_pnl),
                                delta_color="normal")
                    r2c4.metric("Saldo Akhir",     inv_fmt_idr(saldo_akhir))

                    st.markdown("---")

                    # PnL per ticker
                    st.markdown("### 📈 PnL per Saham")
                    tickers = df_m[df_m["ticker"] != ""]["ticker"].unique()
                    ticker_pnl = []
                    for t in tickers:
                        t_buy  = df_m[(df_m["ticker"] == t) & (df_m["category"] == "BUY")]
                        t_sell = df_m[(df_m["ticker"] == t) & (df_m["category"] == "SELL")]
                        buy_amt  = abs(t_buy["amount"].sum())  if not t_buy.empty  else 0
                        sell_amt = t_sell["amount"].sum()       if not t_sell.empty else 0
                        pnl      = sell_amt - buy_amt
                        buy_vol  = t_buy["volume"].sum()        if not t_buy.empty  else 0
                        sell_vol = t_sell["volume"].sum()       if not t_sell.empty else 0
                        status   = "OPEN" if buy_vol > sell_vol else ("WIN" if pnl > 0 else "LOSS")
                        ticker_pnl.append({
                            "Ticker":      t,
                            "Total Beli":  buy_amt,
                            "Total Jual":  sell_amt,
                            "PnL (Rp)":   pnl,
                            "Status":      status,
                        })

                    if ticker_pnl:
                        df_pnl = pd.DataFrame(ticker_pnl).sort_values("PnL (Rp)", ascending=False)
                        st.dataframe(
                            df_pnl,
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                "Total Beli": st.column_config.NumberColumn(format="Rp %.0f"),
                                "Total Jual": st.column_config.NumberColumn(format="Rp %.0f"),
                                "PnL (Rp)":  st.column_config.NumberColumn(format="Rp %.0f"),
                            }
                        )

                    st.markdown("---")

                    # Detail semua mutasi
                    st.markdown("### 📋 Detail Mutasi")
                    cat_filter = st.multiselect(
                        "Filter Kategori",
                        ["BUY","SELL","DIVIDEN","TARIK","SETOR","XRDN_IN","XRDN_OUT","BIAYA","LAINNYA"],
                        default=["BUY","SELL","DIVIDEN","TARIK","SETOR"],
                        key="mutasi_cat_filter"
                    )
                    df_detail = df_m[df_m["category"].isin(cat_filter)][[
                        "trx_date","desc","category","ticker","price","volume","amount","balance"
                    ]].copy()
                    df_detail.columns = ["Tanggal","Keterangan","Kategori","Ticker","Harga","Volume","Amount","Saldo"]

                    st.dataframe(df_detail, use_container_width=True, hide_index=True,
                        column_config={
                            "Amount": st.column_config.NumberColumn(format="Rp %.0f"),
                            "Saldo":  st.column_config.NumberColumn(format="Rp %.0f"),
                        }
                    )

                    # Download
                    csv_exp = df_detail.to_csv(index=False).encode()
                    st.download_button(
                        "📥 Download Laporan (CSV)", csv_exp,
                        file_name=f"laporan_mutasi_{sel_label.replace(' ','_')}.csv",
                        mime="text/csv"
                    )

                    # Reset data
                    if st.button("🗑️ Hapus Semua Data Mutasi", type="secondary"):
                        st.session_state.inv_mutasi_raw = []
                        import json as _json
                        with open(mutasi_file, "w") as f:
                            _json.dump([], f)
                        st.success("Data mutasi dihapus")
                        st.rerun()
    with acc_tab4:
        st.subheader("💰 Modal & Rekonsiliasi")

        col_m1, col_m2 = st.columns(2)
        with col_m1:
            new_modal = st.number_input(
                "Modal Awal (Rp)",
                min_value=100_000, step=100_000,
                value=int(st.session_state.inv_modal),
                key="inv_modal_input"
            )
            if st.button("💾 Update Modal"):
                st.session_state.inv_modal  = int(new_modal)
                st.session_state.balance    = int(new_modal)
                save_state()
                inv_save()
                st.success("✅ Modal diperbarui")
                st.rerun()

        with col_m2:
            st.markdown("**Risk Management**")
            bal = st.session_state.inv_modal
            st.metric("Risk/Trade (2%)",    inv_fmt_idr(bal * 0.02))
            st.metric("Max 5 Posisi (40%)", inv_fmt_idr(bal * 0.40))
            st.metric("Safe Cash (60%)",    inv_fmt_idr(bal * 0.60))

        st.markdown("---")
        st.subheader("📊 Rekonsiliasi")
        rk1, rk2, rk3 = st.columns(3)
        setor  = sum(m.get("jumlah", 0) for m in st.session_state.inv_mutasi if m.get("tipe") == "SETOR")
        tarik  = sum(m.get("jumlah", 0) for m in st.session_state.inv_mutasi if m.get("tipe") == "TARIK")
        biaya  = sum(m.get("jumlah", 0) for m in st.session_state.inv_mutasi if m.get("tipe") == "BIAYA")
        rk1.metric("Total Setor",  inv_fmt_idr(setor))
        rk2.metric("Total Tarik",  inv_fmt_idr(tarik))
        rk3.metric("Total Biaya",  inv_fmt_idr(biaya))

        st.markdown("---")
        st.subheader("➕ Input Mutasi")
        with st.form("inv_mutasi_form", clear_on_submit=True):
            mc1, mc2, mc3 = st.columns(3)
            with mc1:
                m_tipe    = st.selectbox("Tipe", ["SETOR", "TARIK", "BIAYA"])
            with mc2:
                m_jumlah  = st.number_input("Jumlah (Rp)", min_value=0, step=10000)
            with mc3:
                m_ket     = st.text_input("Keterangan", placeholder="Setoran awal, komisi, dll")
            if st.form_submit_button("Simpan Mutasi"):
                st.session_state.inv_mutasi.append({
                    "tipe":       m_tipe,
                    "jumlah":     m_jumlah,
                    "keterangan": m_ket,
                    "date":       str(datetime.now(WIB).date()),
                })
                inv_save()
                st.success("✅ Mutasi tersimpan")
                st.rerun()

    with acc_tab5:
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
# TAB 7 — 🎯 BANDAR HUNTER
# ─────────────────────────────────────────────────────────────
with tabs[5]:
    st.markdown("## 🚀 Breakout Yesterday High — ISSI Scanner")
    st.caption(
        "Scan otomatis setiap 15 menit jam 09:00–10:00 WIB. "
        "Logic: **Close hari ini > High daily kemarin**. "
        "Raw breakout — filter manual TF 15m sebelum entry."
    )

    # ── Info jadwal ───────────────────────────────────────────
    st.info(
        "**Jadwal scan otomatis:** 09:00 | 09:15 | 09:30 | 09:45 | **10:00 (Konfirmasi)**\n\n"
        "Hasil dikirim langsung ke Telegram. Tab ini untuk trigger manual dan lihat hasil terakhir."
    )

    col_b1, col_b2, _ = st.columns([1, 1, 3])
    with col_b1:
        do_breakout_scan = st.button(
            "🚀 Scan Breakout Sekarang",
            type="primary", use_container_width=True,
        )
    with col_b2:
        send_tg_bo = st.checkbox("Kirim Telegram", value=True, key="bo_send_tg")

    # ── Manual trigger ────────────────────────────────────────
    if do_breakout_scan:
        prog_bo = st.progress(0, text="🔍 Scanning ISSI universe...")
        bo_results = scan_breakout_yesterday_high(ISSI_UNIVERSE)
        prog_bo.progress(100, text=f"✅ Selesai — {len(bo_results)} breakout ditemukan")

        # Update shared state
        with _state_lock:
            _breakout_last["results"] = bo_results
            _breakout_last["ts"]      = datetime.now(WIB).strftime("%H:%M WIB")
            _breakout_last["label"]   = "Manual"

        if st.session_state.get("bo_send_tg", True):
            msg = format_breakout_telegram(bo_results, "Manual")
            ok  = send_telegram(msg)
            if ok:
                st.success(f"✅ Telegram terkirim — {len(bo_results)} breakout")
            else:
                st.warning("⚠️ Scan selesai tapi Telegram gagal — cek Railway Variables")
        time.sleep(0.3)
        prog_bo.empty()

    # ── Display hasil terakhir ────────────────────────────────
    last_results = _breakout_last.get("results", [])
    last_ts      = _breakout_last.get("ts", None)
    last_label   = _breakout_last.get("label", "")

    st.markdown("---")
    if not last_results and not last_ts:
        st.info("Belum ada data. Klik **Scan Breakout Sekarang** atau tunggu scan otomatis jam 09:00 WIB.")
    else:
        st.caption(
            f"Scan terakhir: **{last_ts or '-'}** [{last_label}]  |  "
            f"**{len(last_results)} saham** breakout high kemarin"
        )

        if not last_results:
            st.warning("Tidak ada saham ISSI yang breakout high kemarin saat scan terakhir.")
        else:
            # Summary metrics
            m1, m2, m3 = st.columns(3)
            m1.metric("Total Breakout", len(last_results))
            m2.metric("Breakout Terbesar", f"{last_results[0]['ticker']}  +{last_results[0]['breakout_pct']:.1f}%")
            m3.metric("Breakout Terkecil", f"{last_results[-1]['ticker']}  +{last_results[-1]['breakout_pct']:.1f}%")

            st.markdown("---")

            # Tabel hasil
            df_bo = pd.DataFrame(last_results)
            df_bo = df_bo.drop(columns=["tgl_kemarin"], errors="ignore")
            df_bo.columns = ["Ticker", "Harga", "High H-1", "Breakout %", "Vol (B)"]
            df_bo.index   = range(1, len(df_bo) + 1)

            st.dataframe(
                df_bo.style.format({
                    "Harga":       "{:,.0f}",
                    "High H-1":    "{:,.0f}",
                    "Breakout %":  "{:.2f}%",
                    "Vol (B)":     "{:.1f}B",
                }),
                use_container_width=True,
            )

            st.caption(
                "⚠️ **Raw breakout tanpa filter tambahan.** "
                "Konfirmasi wajib di chart TF 15m sebelum entry: "
                "candle close di atas level High H-1, volume naik, tidak overbought."
            )

            # Kirim ulang Telegram
            if st.button("📤 Kirim Ulang ke Telegram", key="bo_resend"):
                msg = format_breakout_telegram(last_results, f"Resend {last_ts or ''}")
                ok  = send_telegram(msg)
                st.success("✅ Telegram terkirim") if ok else st.error("❌ Telegram gagal")

    # ── BREAKDOWN SECTION — break low kemarin ────────────────
    st.markdown("---")
    st.markdown("## 📉 Breakdown Yesterday Low — HINDARI BUY")
    st.caption(
        "Saham yang close hari ini **di bawah Low daily kemarin**. "
        "**Bukan sinyal beli.** Gunakan untuk: skip saham ini di ATS scanner, "
        "risk awareness, atau konfirmasi exit posisi yang sudah ada."
    )
    st.warning(
        "⚠️ **Breakdown = distribusi / selling pressure aktif.** "
        "Jangan beli saham yang ada di daftar ini, meskipun ATS menampilkan sinyal."
    )

    col_bd1, col_bd2, _ = st.columns([1, 1, 3])
    with col_bd1:
        do_breakdown_scan = st.button(
            "📉 Scan Breakdown Sekarang",
            type="secondary", use_container_width=True,
        )
    with col_bd2:
        send_tg_bd = st.checkbox("Kirim Telegram", value=False, key="bd_send_tg")

    if do_breakdown_scan:
        prog_bd = st.progress(0, text="🔍 Scanning breakdown ISSI...")
        bd_results = scan_breakout_yesterday_low(ISSI_UNIVERSE)
        prog_bd.progress(100, text=f"✅ Selesai — {len(bd_results)} breakdown ditemukan")

        with _state_lock:
            _breakout_last["bd_results"] = bd_results
            _breakout_last["bd_ts"]      = datetime.now(WIB).strftime("%H:%M WIB")

        if st.session_state.get("bd_send_tg", False):
            msg = format_breakdown_telegram(bd_results, "Manual")
            ok  = send_telegram(msg)
            if ok:
                st.success(f"✅ Telegram terkirim — {len(bd_results)} breakdown")
            else:
                st.warning("⚠️ Scan selesai tapi Telegram gagal")
        time.sleep(0.3)
        prog_bd.empty()

    # Display hasil breakdown
    last_bd_results = _breakout_last.get("bd_results", [])
    last_bd_ts      = _breakout_last.get("bd_ts", None)

    if not last_bd_results and not last_bd_ts:
        st.info("Klik **Scan Breakdown Sekarang** untuk lihat saham yang breakdown low kemarin.")
    else:
        st.caption(
            f"Scan terakhir: **{last_bd_ts or '-'}**  |  "
            f"**{len(last_bd_results)} saham** breakdown low kemarin"
        )
        if last_bd_results:
            bm1, bm2, bm3 = st.columns(3)
            bm1.metric("Total Breakdown", len(last_bd_results))
            bm2.metric("Breakdown Terdalam", f"{last_bd_results[0]['ticker']}  -{last_bd_results[0]['breakdown_pct']:.1f}%")
            bm3.metric("Breakdown Terkecil", f"{last_bd_results[-1]['ticker']}  -{last_bd_results[-1]['breakdown_pct']:.1f}%")

            st.markdown("---")
            df_bd = pd.DataFrame(last_bd_results)
            df_bd = df_bd.drop(columns=["tgl_kemarin"], errors="ignore")
            df_bd.columns = ["Ticker", "Harga", "Low H-1", "Breakdown %", "Vol (B)"]
            df_bd.index   = range(1, len(df_bd) + 1)

            st.dataframe(
                df_bd.style.format({
                    "Harga":        "{:,.0f}",
                    "Low H-1":      "{:,.0f}",
                    "Breakdown %":  "{:.2f}%",
                    "Vol (B)":      "{:.1f}B",
                }),
                use_container_width=True,
            )
            st.caption(
                "⚠️ **HINDARI BUY semua ticker di daftar ini.** "
                "Break low kemarin = tekanan jual masih aktif. "
                "Tunggu minimal 2 hari konfirmasi sebelum reconsider."
            )

            if st.button("📤 Kirim Ulang ke Telegram", key="bd_resend"):
                msg = format_breakdown_telegram(last_bd_results, f"Resend {last_bd_ts or ''}")
                ok  = send_telegram(msg)
                st.success("✅ Telegram terkirim") if ok else st.error("❌ Telegram gagal")

# ── TAB 9 — WISDOM ────────────────────────────────────────────
with tabs[6]:
    st.markdown("## 📚 Jesse Livermore — Wisdom & Vocabulary")
    st.caption(
        "Kutipan legendaris dari Reminiscences of a Stock Operator & How to Trade in Stocks. "
        "Baca sambil menunggu sinyal — setiap kata relevan untuk trading hari ini."
    )

    QUOTES = [
        {
            "cat": "sabar", "cat_label": "Sabar & Timing",
            "en": '"The big money is not in the buying and the selling, but in the waiting."',
            "id": '"Uang besar bukan dari beli dan jual, tapi dari menunggu."',
            "konteks": "Relevan setiap kali mau FOMO masuk sebelum konfirmasi. BH detect sinyal, ATS belum confirm — tunggu.",
            "source": "Reminiscences of a Stock Operator"
        },
        {
            "cat": "sabar", "cat_label": "Sabar & Timing",
            "en": '"It never was my thinking that made the big money for me. It always was my sitting. Men who can both be right and sit tight are uncommon."',
            "id": '"Bukan pemikiranku yang menghasilkan uang besar. Selalu kesabaranku. Orang yang bisa benar sekaligus sabar menunggu itu sangat langka."',
            "konteks": "Setup bagus + sabar menunggu target tercapai = formula Livermore. Bukan berapa banyak trade, tapi seberapa presisi.",
            "source": "Reminiscences of a Stock Operator"
        },
        {
            "cat": "sabar", "cat_label": "Sabar & Timing",
            "en": '"There is a time to go long, a time to go short, and a time to go fishing."',
            "id": '"Ada saatnya beli, ada saatnya short, dan ada saatnya pergi memancing."',
            "konteks": "DISTRIBUTION regime + IHSG turun 20% = saatnya memancing. Cash adalah posisi yang valid.",
            "source": "How to Trade in Stocks"
        },
        {
            "cat": "sabar", "cat_label": "Sabar & Timing",
            "en": '"Do not anticipate and move without market confirmation — being a little late in your trade is your insurance that you are right."',
            "id": '"Jangan antisipasi dan bergerak tanpa konfirmasi market — terlambat sedikit dalam entry adalah asuransimu bahwa kamu benar."',
            "konteks": "Jangan entry sebelum candle close, sebelum ATS confirm, sebelum broker summary dibaca.",
            "source": "How to Trade in Stocks"
        },
        {
            "cat": "volume", "cat_label": "Volume & Tape",
            "en": '"Big operators always tip their hand. Watch the volume — they cannot hide their footprints."',
            "id": '"Operator besar selalu meninggalkan jejak. Perhatikan volume — mereka tidak bisa menyembunyikan sidik jari mereka."',
            "konteks": "Fondasi Bandar Hunter. Vol spike tidak proporsional = jejak institusi. TINS naik 6% dengan Vol 134B.",
            "source": "Reminiscences of a Stock Operator"
        },
        {
            "cat": "volume", "cat_label": "Volume & Tape",
            "en": '"When price falls on heavy volume, that is distribution, not accumulation. The smart money is selling to the eager public."',
            "id": '"Ketika harga turun dengan volume besar, itu distribusi, bukan akumulasi. Uang pintar sedang menjual ke publik yang bersemangat."',
            "konteks": "Persis yang terjadi pada ADRO — volume 428K lot tapi harga turun. Bid ratio 24%. Bandar sedang distribusi.",
            "source": "Reminiscences of a Stock Operator"
        },
        {
            "cat": "volume", "cat_label": "Volume & Tape",
            "en": '"Volume is the ammunition of the market. Without volume, price moves are meaningless."',
            "id": '"Volume adalah amunisi market. Tanpa volume, pergerakan harga tidak berarti apa-apa."',
            "konteks": "Kenapa BH pakai threshold vol 2-4x. Markup tanpa volume = false signal. Selalu cek vol sebelum entry.",
            "source": "How to Trade in Stocks"
        },
        {
            "cat": "loss", "cat_label": "Loss & Cut",
            "en": '"A loss never bothers me after I take it. I forget it overnight. But being wrong and not taking the loss — that is what does the damage."',
            "id": '"Loss tidak pernah menggangguku setelah aku ambil. Aku lupakan dalam semalam. Tapi salah arah dan tidak mau cut loss — itulah yang merusak segalanya."',
            "konteks": "RALS cut loss bersih di 450 — sistem bekerja benar. Yang merusak bukan loss-nya, tapi keengganan mengakui salah.",
            "source": "Reminiscences of a Stock Operator"
        },
        {
            "cat": "loss", "cat_label": "Loss & Cut",
            "en": '"The only time I really ever lost money was when I broke my own rules."',
            "id": '"Satu-satunya saat aku benar-benar kehilangan uang adalah ketika aku melanggar rules-ku sendiri."',
            "konteks": "ADRO — SL tidak dipasang langsung setelah fill. Bukan sistem yang salah. Rules yang dilanggar.",
            "source": "Reminiscences of a Stock Operator"
        },
        {
            "cat": "loss", "cat_label": "Loss & Cut",
            "en": '"Successful trading is always an emotional battle for the speculator, not an intellectual one."',
            "id": '"Trading yang sukses selalu merupakan pertarungan emosional bagi trader, bukan pertarungan intelektual."',
            "konteks": "Sistem dan analisis bisa benar 100%. Tapi kalau emosi yang pegang kendali saat eksekusi — hasilnya berbeda.",
            "source": "How to Trade in Stocks"
        },
        {
            "cat": "market", "cat_label": "Market & Harga",
            "en": '"Markets are never wrong — opinions often are."',
            "id": '"Pasar tidak pernah salah — opini yang sering salah."',
            "konteks": "Kalau data IPOT bilang distribusi tapi kamu pikir harusnya naik — data yang benar. Bukan opinimu.",
            "source": "Reminiscences of a Stock Operator"
        },
        {
            "cat": "market", "cat_label": "Market & Harga",
            "en": '"There is nothing new in Wall Street. Whatever happens today has happened before and will happen again."',
            "id": '"Tidak ada yang baru di Wall Street. Apapun yang terjadi hari ini sudah pernah terjadi sebelumnya dan akan terjadi lagi."',
            "konteks": "Pump & dump ADRO, wash trading KBLI, distribusi BRPT — semua sudah terjadi ribuan kali di market manapun.",
            "source": "Reminiscences of a Stock Operator"
        },
        {
            "cat": "market", "cat_label": "Market & Harga",
            "en": '"The line of least resistance — when a stock breaks out on volume, it is telling you where it wants to go."',
            "id": '"Jalur hambatan terkecil — ketika saham breakout dengan volume, ia sedang memberitahumu ke mana ia ingin pergi."',
            "konteks": "TINS breakout dari 2.970 ke 3.180 dengan Vol 134B. Market sedang bicara. Tugas kita mendengarkan.",
            "source": "How to Trade in Stocks"
        },
        {
            "cat": "market", "cat_label": "Market & Harga",
            "en": '"A stock is never too high to buy and never too low to sell."',
            "id": '"Saham tidak pernah terlalu tinggi untuk dibeli dan tidak pernah terlalu rendah untuk dijual."',
            "konteks": "Yang menentukan bukan level harga — tapi arah trend dan konfirmasi sistem. Entry setelah momentum terkonfirmasi.",
            "source": "Reminiscences of a Stock Operator"
        },
        {
            "cat": "psikologi", "cat_label": "Psikologi",
            "en": '"The game of speculation is the most fascinating game in the world. But it is not a game for the stupid, the mentally lazy, or the get-rich-quick adventurer."',
            "id": '"Trading adalah permainan paling menarik di dunia. Tapi ini bukan untuk yang malas berpikir atau yang ingin cepat kaya."',
            "konteks": "Kamu sedang membangun yang tepat — sistem, disiplin, dan pemahaman yang dalam. Bukan get-rich-quick.",
            "source": "Reminiscences of a Stock Operator"
        },
        {
            "cat": "psikologi", "cat_label": "Psikologi",
            "en": '"The human side of every person is the greatest enemy of the average investor or speculator."',
            "id": '"Sisi manusiawi setiap orang adalah musuh terbesar dari trader rata-rata."',
            "konteks": "FOMO, revenge trading, tidak mau cut loss, averaging down — semua lahir dari sisi manusiawi, bukan dari analisis.",
            "source": "Reminiscences of a Stock Operator"
        },
        {
            "cat": "psikologi", "cat_label": "Psikologi",
            "en": '"The market does not beat them. They beat themselves, because though they have brains they cannot sit tight."',
            "id": '"Pasar tidak mengalahkan mereka. Mereka mengalahkan diri sendiri, karena meski punya otak mereka tidak bisa diam dan sabar."',
            "konteks": "Sistem kita sudah cukup baik. Musuh terbesar sekarang adalah diri sendiri saat eksekusi.",
            "source": "Reminiscences of a Stock Operator"
        },
        {
            "cat": "psikologi", "cat_label": "Psikologi",
            "en": '"Hope and fear are the two greatest enemies of the speculator."',
            "id": '"Harapan dan ketakutan adalah dua musuh terbesar dari trader."',
            "konteks": "Hope: tidak mau cut loss karena berharap harga balik. Fear: tidak entry padahal setup valid karena takut loss.",
            "source": "How to Trade in Stocks"
        },
        {
            "cat": "posisi", "cat_label": "Posisi & Sizing",
            "en": '"Don\'t try to buy at the bottom and sell at the top. It can\'t be done, except by liars."',
            "id": '"Jangan coba beli di bottom dan jual di top. Itu tidak bisa dilakukan, kecuali oleh pembohong."',
            "konteks": "Yang kita kejar bukan bottom atau top — tapi momentum yang sudah terkonfirmasi. Entry setelah breakout.",
            "source": "Reminiscences of a Stock Operator"
        },
        {
            "cat": "posisi", "cat_label": "Posisi & Sizing",
            "en": '"It is not good to be too curious about all the reasons behind price movements. You risk clouding your mind with non-essentials."',
            "id": '"Tidak baik terlalu penasaran dengan semua alasan di balik pergerakan harga. Kamu berisiko mengaburkan pikiran dengan hal tidak penting."',
            "konteks": "Terlalu banyak analisis = analysis paralysis. Setup ada, konfirmasi ada, SL ada — eksekusi.",
            "source": "How to Trade in Stocks"
        },
    ]

    # Filter bar
    CATS = {
        "all":      "Semua",
        "sabar":    "Sabar & Timing",
        "volume":   "Volume & Tape",
        "loss":     "Loss & Cut",
        "market":   "Market & Harga",
        "psikologi":"Psikologi",
        "posisi":   "Posisi & Sizing",
    }

    CAT_COLORS = {
        "sabar":    {"bg": "#E1F5EE", "txt": "#0F6E56", "border": "#1D9E75"},
        "volume":   {"bg": "#E6F1FB", "txt": "#185FA5", "border": "#378ADD"},
        "loss":     {"bg": "#FCEBEB", "txt": "#A32D2D", "border": "#E24B4A"},
        "market":   {"bg": "#FAEEDA", "txt": "#854F0B", "border": "#BA7517"},
        "psikologi":{"bg": "#EEEDFE", "txt": "#534AB7", "border": "#7F77DD"},
        "posisi":   {"bg": "#FAECE7", "txt": "#993C1D", "border": "#D85A30"},
    }

    if "wisdom_cat" not in st.session_state:
        st.session_state["wisdom_cat"] = "all"

    # Filter buttons
    btn_cols = st.columns(len(CATS))
    for i, (k, v) in enumerate(CATS.items()):
        with btn_cols[i]:
            if st.button(
                v,
                key=f"wcat_{k}",
                type="primary" if st.session_state["wisdom_cat"] == k else "secondary",
                use_container_width=True
            ):
                st.session_state["wisdom_cat"] = k
                st.rerun()

    st.markdown("---")

    # Filter quotes
    sel = st.session_state["wisdom_cat"]
    filtered = QUOTES if sel == "all" else [q for q in QUOTES if q["cat"] == sel]
    st.caption(f"Menampilkan {len(filtered)} kutipan")

    # Render cards
    for q in filtered:
        c = CAT_COLORS[q["cat"]]
        with st.container(border=True):
            # Badge
            st.markdown(
                f'<span style="background:{c["bg"]};color:{c["txt"]};'
                f'padding:3px 12px;border-radius:12px;font-size:12px;font-weight:500;">'
                f'{q["cat_label"]}</span>',
                unsafe_allow_html=True
            )
            st.markdown(f"*{q['en']}*")
            st.info(q["id"])
            st.caption(f"**Konteks:** {q['konteks']}")
            st.caption(f"*— {q['source']}*")

    st.markdown("---")
    st.markdown(
        "> 📚 **Sumber:** *Reminiscences of a Stock Operator* — Edwin Lefèvre (1923) "
        "& *How to Trade in Stocks* — Jesse Livermore (1940). "
        "Dua buku wajib setiap trader serius."
    )
st.caption(
    f"ATS SuperEngine {APP_VERSION}  |  Update terakhir: {APP_UPDATED}  |  "
    "ISSI Syariah Scanner  |  Bukan rekomendasi investasi"
)