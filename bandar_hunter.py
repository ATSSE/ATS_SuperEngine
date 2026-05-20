"""
🎯 BANDAR HUNTER ENGINE
Deteksi pergerakan institusional / bandar via anomali volume + price
pada data intraday 5 menit.

METODOLOGI: Jesse Livermore — Modernised
─────────────────────────────────────────
"The market does not beat them. They beat themselves, because though they
have brains they cannot sit tight." — Jesse Livermore

Livermore membaca "tape" untuk mendeteksi pergerakan Big Operators.
Engine ini melakukan hal yang sama — bukan dengan mata dan intuisi,
tapi dengan data 5 menit dan kalkulasi volume anomali.

4 Prinsip Livermore yang diimplementasikan:
  1. Volume spike    = Big Operators masuk (sidik jari tidak bisa disembunyikan)
  2. Akumulasi senyap = Bandar kumpul saham sebelum markup (beli bertahap)
  3. Initial Markup  = Bandar mulai push harga — entry window terbaik
  4. Distribusi      = Bandar jual ke retail — jauhi atau exit

"There is nothing new in Wall Street. Whatever happens today has
happened before and will happen again." — Jesse Livermore

FILOSOFI:
Bandar tidak bisa membeli jutaan lembar sekaligus tanpa meninggalkan
jejak di volume dan price. Tugas kita: baca jejak itu sebelum retail
menyadarinya.

DATA SOURCE: yfinance 5m (tersedia, gratis, cukup akurat untuk IDX)
INPUT     : kandidat dari hasil scan ATS (3-5 ticker) + watchlist custom
OUTPUT    : alert Telegram + data untuk tab UI

KETERBATASAN YANG HARUS DIPAHAMI:
- Ini adalah PROXY, bukan deteksi bandar sesungguhnya
- Broker flow data (RTI/Stockbit) tidak tersedia
- False positive mungkin terjadi, terutama di saham tidak likuid
- Selalu konfirmasi dengan D1 chart sebelum eksekusi

Author: ATS SuperEngine team
"""

from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import numpy as np
import pandas as pd
import yfinance as yf
import pytz

LOG = logging.getLogger(__name__)
WIB = pytz.timezone("Asia/Jakarta")

# ─────────────────────────────────────────────────────────────
# SINYAL TYPE — untuk edukasi
# ─────────────────────────────────────────────────────────────
SIGNAL_EDUCATION = {
    "MARKUP_AWAL": {
        "label"  : "⚡ Initial Markup",
        "icon"   : "⚡",
        "arti"   : "Bandar mulai mendorong harga naik secara agresif.",
        "pola"   : "Volume meledak + harga loncat > 1% dalam 15 menit tanpa pullback.",
        "aksi"   : "Monitor entry di H1. Konfirmasi dengan D1 trend.",
        "risiko" : "Bisa jadi pump sesaat jika volume tidak berlanjut.",
    },
    "AKUMULASI_SENYAP": {
        "label"  : "🤫 Akumulasi Senyap",
        "icon"   : "🤫",
        "arti"   : "Bandar mengumpulkan saham diam-diam — harga tidak banyak bergerak tapi volume naik konsisten.",
        "pola"   : "Volume di atas rata-rata 3-5 candle berturut-turut, harga sideways atau naik tipis.",
        "aksi"   : "Sabar. Ini fase sebelum markup. Entry di support terdekat.",
        "risiko" : "Akumulasi bisa berlangsung berminggu-minggu.",
    },
    "VOLUME_ANOMALI": {
        "label"  : "🔊 Volume Anomali",
        "icon"   : "🔊",
        "arti"   : "Ada pihak besar yang masuk — tapi arahnya belum jelas.",
        "pola"   : "Volume melonjak ekstrem (>5×) tapi harga tidak banyak bergerak.",
        "aksi"   : "Tunggu konfirmasi arah. Bisa jadi distribusi atau akumulasi.",
        "risiko" : "Tanpa konfirmasi harga, berbahaya langsung entry.",
    },
    "DISTRIBUSI": {
        "label"  : "🔴 Distribusi",
        "icon"   : "🔴",
        "arti"   : "Bandar mulai jual ke retail — harga naik tapi volume mulai turun.",
        "pola"   : "Harga masih naik atau flat, volume trend turun 3+ candle.",
        "aksi"   : "HINDARI entry baru. Kalau sudah pegang, pertimbangkan profit taking.",
        "risiko" : "Harga bisa runtuh tiba-tiba ketika supply habis.",
    },
    "NONE": {
        "label"  : "😴 Normal",
        "icon"   : "😴",
        "arti"   : "Tidak ada anomali terdeteksi.",
        "pola"   : "Volume dan harga dalam range normal.",
        "aksi"   : "Tidak ada aksi. Monitor saja.",
        "risiko" : "-",
    },
}

# ─────────────────────────────────────────────────────────────
# RESULT
# ─────────────────────────────────────────────────────────────
@dataclass
class BandarSignal:
    ticker       : str
    signal_type  : str = "NONE"       # MARKUP_AWAL | AKUMULASI_SENYAP | VOLUME_ANOMALI | DISTRIBUSI | NONE
    confidence   : str = "LOW"        # HIGH | MEDIUM | LOW
    vol_ratio    : float = 0.0        # vol sekarang vs avg20 candle
    price_chg_3c : float = 0.0        # % change 3 candle terakhir
    price_chg_1c : float = 0.0        # % change candle terakhir
    pullback     : float = 0.0        # pullback ratio (0=tidak ada, 1=full pullback)
    last_price   : float = 0.0
    vol_trend    : str = "-"          # "NAIK" | "TURUN" | "FLAT"
    consec_above : int = 0            # candle berturut-turut di atas avg vol
    fvg          : bool = False       # ada Fair Value Gap?
    timestamp    : str = ""
    scan_time    : str = ""
    error        : str = ""

    @property
    def education(self) -> dict:
        return SIGNAL_EDUCATION.get(self.signal_type, SIGNAL_EDUCATION["NONE"])

    @property
    def is_actionable(self) -> bool:
        return self.signal_type in ("MARKUP_AWAL", "AKUMULASI_SENYAP")

# ─────────────────────────────────────────────────────────────
# CORE DETECTION ENGINE
# ─────────────────────────────────────────────────────────────
def _detect_bandar(ticker: str) -> BandarSignal:
    """
    Analisis satu ticker menggunakan data 5m.
    Mengembalikan BandarSignal dengan tipe dan konteks edukasi.
    """
    tkr_jk = ticker if ticker.endswith(".JK") else f"{ticker}.JK"
    sig    = BandarSignal(
        ticker    = ticker,
        timestamp = datetime.now(WIB).strftime("%H:%M WIB"),
        scan_time = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB"),
    )

    try:
        df = yf.download(
            tkr_jk, period="5d", interval="5m",
            progress=False, auto_adjust=True
        )
        if df is None or len(df) < 25:
            sig.error = "data 5m tidak cukup"
            return sig

        # Ambil hanya hari ini
        df.index = pd.to_datetime(df.index)
        today    = datetime.now(WIB).date()
        df_today = df[df.index.tz_convert(WIB).date == today]

        # Kalau hari ini kurang dari 10 candle (pre-market / data belum cukup)
        # pakai semua data
        if len(df_today) < 10:
            df_today = df.tail(30)

        if len(df_today) < 5:
            sig.error = "candle hari ini terlalu sedikit"
            return sig

        close  = df_today["Close"].squeeze().astype(float)
        volume = df_today["Volume"].squeeze().astype(float)
        high   = df_today["High"].squeeze().astype(float)
        low    = df_today["Low"].squeeze().astype(float)

        # ── Kalkulasi baseline ───────────────────────────────
        avg_vol_20 = float(volume.iloc[:-1].tail(20).mean()) if len(volume) > 20 else float(volume.mean())
        last_vol   = float(volume.iloc[-1])
        vol_ratio  = last_vol / avg_vol_20 if avg_vol_20 > 0 else 0.0

        last_price = float(close.iloc[-1])
        sig.last_price = round(last_price, 0)

        # Price change
        chg_1c = ((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100
                  if len(close) >= 2 else 0.0)
        chg_3c = ((close.iloc[-1] - close.iloc[-4]) / close.iloc[-4] * 100
                  if len(close) >= 4 else chg_1c)

        # Pullback ratio — seberapa besar candle 3 terakhir sudah "ditarik balik"
        move_high     = float(high.iloc[-3:].max())
        move_low      = float(low.iloc[-3:].min())
        move_range    = move_high - move_low
        pullback_ratio = ((move_high - last_price) / move_range
                         if move_range > 0 else 0.5)

        # Volume trend — apakah vol 5 candle terakhir naik atau turun?
        vol_ma5_prev = float(volume.iloc[-6:-1].mean()) if len(volume) >= 6 else avg_vol_20
        vol_ma5_now  = float(volume.iloc[-5:].mean())
        if vol_ma5_now > vol_ma5_prev * 1.1:
            vol_trend = "NAIK"
        elif vol_ma5_now < vol_ma5_prev * 0.9:
            vol_trend = "TURUN"
        else:
            vol_trend = "FLAT"

        # Candle berturut-turut di atas avg vol
        consec = 0
        for v in reversed(volume.values):
            if v > avg_vol_20:
                consec += 1
            else:
                break

        # FVG: low candle -2 > high candle -4 (gap tidak terisi)
        fvg = (float(low.iloc[-2]) > float(high.iloc[-4])
               if len(df_today) >= 5 else False)

        # ── Simpan ke signal ─────────────────────────────────
        sig.vol_ratio    = round(vol_ratio, 2)
        sig.price_chg_3c = round(float(chg_3c), 2)
        sig.price_chg_1c = round(float(chg_1c), 2)
        sig.pullback     = round(pullback_ratio, 2)
        sig.vol_trend    = vol_trend
        sig.consec_above = consec
        sig.fvg          = fvg

        # ── Klasifikasi sinyal ───────────────────────────────

        # 1. MARKUP AWAL: volume spike + price impulsif + no pullback
        if (vol_ratio >= 4.0 and
            chg_3c >= 1.0 and
            pullback_ratio <= 0.35):
            sig.signal_type = "MARKUP_AWAL"
            sig.confidence  = "HIGH" if (vol_ratio >= 5.0 and fvg) else "MEDIUM"

        # 2. AKUMULASI SENYAP: volume konsisten di atas avg, harga naik tipis
        elif (consec >= 3 and
              vol_ratio >= 1.5 and
              vol_trend == "NAIK" and
              0 < chg_3c < 1.5):
            sig.signal_type = "AKUMULASI_SENYAP"
            sig.confidence  = "HIGH" if consec >= 5 else "MEDIUM"

        # 3. VOLUME ANOMALI: vol ekstrem tapi harga tidak bergerak
        elif (vol_ratio >= 5.0 and abs(chg_3c) < 0.5):
            sig.signal_type = "VOLUME_ANOMALI"
            sig.confidence  = "MEDIUM"

        # 4. DISTRIBUSI: harga naik tapi vol turun
        elif (chg_3c > 0.5 and
              vol_trend == "TURUN" and
              vol_ratio < 0.8):
            sig.signal_type = "DISTRIBUSI"
            sig.confidence  = "MEDIUM"

        else:
            sig.signal_type = "NONE"
            sig.confidence  = "LOW"

    except Exception as e:
        sig.error = str(e)[:80]
        LOG.warning(f"BandarHunter {ticker} error: {e}")

    return sig


# ─────────────────────────────────────────────────────────────
# BATCH SCAN
# ─────────────────────────────────────────────────────────────
def run_bandar_scan(
    tickers     : list[str],
    min_signal  : str = "VOLUME_ANOMALI",  # filter minimum
    progress_cb = None,
) -> list[BandarSignal]:
    """
    Scan batch ticker, return list BandarSignal sorted by priority.
    min_signal: kalau "NONE" → return semua, kalau "MARKUP_AWAL" → hanya yang actionable
    """
    # Filter jam bursa — scan hanya 09:30–15:00
    now = datetime.now(WIB)
    if now.weekday() >= 5:
        LOG.info("Bandar Hunter: weekend, skip")
        return []

    priority = {"MARKUP_AWAL": 4, "AKUMULASI_SENYAP": 3,
                "VOLUME_ANOMALI": 2, "DISTRIBUSI": 1, "NONE": 0}
    min_prio = priority.get(min_signal, 0)

    results: list[BandarSignal] = []
    n = len(tickers)

    for i, ticker in enumerate(tickers):
        if progress_cb:
            progress_cb(i, n, ticker)
        sig = _detect_bandar(ticker)
        if priority.get(sig.signal_type, 0) >= min_prio:
            results.append(sig)
        time.sleep(0.1)   # polite delay — hindari rate limit

    if progress_cb:
        progress_cb(n, n, "selesai")

    results.sort(
        key=lambda s: (priority.get(s.signal_type, 0),
                       s.vol_ratio),
        reverse=True
    )
    return results


# ─────────────────────────────────────────────────────────────
# BACKGROUND JOB — dipanggil dari scheduler
# ─────────────────────────────────────────────────────────────
# Watchlist tetap Bandar Hunter — FULL 98 ISSI Universe
# Sync dengan config/universe.py ATS SuperEngine
# Radar independen, tidak tergantung hasil scan ATS
BANDAR_BASE_WATCHLIST: list[str] = [
    # ENERGY
    "ADRO","ADMR","ITMG","PTBA","HRUM","PGAS","MEDC","ELSA","PGEO","BUMI","INDY","AKRA",
    # MINING
    "ANTM","INCO","MDKA","TINS","NCKL","BRMS","HRTA","AMMN","MBMA",
    # BANK SYARIAH
    "BRIS","BPAM","PNLF",
    # TELCO
    "TLKM","EXCL","ISAT","MTEL","TOWR","TBIG",
    # CONSUMER STAPLES
    "ICBP","INDF","CPIN","JPFA","SIDO","ULTJ","MYOR","ROTI","STTP","CLEO","GOOD","FOOD","ICHI",
    # CONSUMER DISCRETIONARY
    "AMRT","ACES","ERAA","MAPI","RALS","LPPF","MAPB",
    # HEALTHCARE
    "MIKA","HEAL","KLBF","SILO","SAME","MERK","PYFA",
    # PROPERTY
    "CTRA","PWON","SMRA","DMAS","BSDE","BEST","KIJA",
    # CONSTRUCTION
    "WSKT","WIKA","PTPP","ADHI","WEGE",
    # INDUSTRIAL
    "ASII","UNTR","SMGR","INTP","IMPC","SMSM","AUTO","WOOD","MARK","KBLI","SCCO",
    # PLANTATION
    "LSIP","DSNG","SIMP","AALI","TBLA","SSMS",
    # MEDIA
    "SCMA","EMTK",
    # CHEMICAL
    "TKIM","INKP",
    # LOGISTICS
    "ASSA","TMAS","SMDR","BIRD","TRUK",
    # TECHNOLOGY
    "DCII","MLPT","MTDL",
]


def build_scan_universe(ats_tickers: list[str]) -> list[str]:
    """
    Gabungkan kandidat ATS + base watchlist.
    ATS tickers di depan (prioritas lebih tinggi karena sudah pre-filter).
    Hapus duplikat, max 35 ticker untuk jaga rate limit.
    """
    combined = list(dict.fromkeys(ats_tickers + BANDAR_BASE_WATCHLIST))
    return combined[:35]


def bandar_hunter_job(ats_tickers: list[str], send_telegram_fn) -> None:
    """
    Background job untuk scheduler.
    Input: kandidat ATS (bisa kosong) + base watchlist tetap.
    Selalu punya ticker untuk dipantau — tidak tergantung ATS output.
    """
    now = datetime.now(WIB)
    # Hanya jam 09:30 – 15:00
    if not (9 <= now.hour <= 14 or (now.hour == 15 and now.minute == 0)):
        return
    if now.hour == 9 and now.minute < 30:
        return

    # Merge ATS candidates + base watchlist
    universe = build_scan_universe(ats_tickers)
    LOG.info(
        f"BandarHunter job: {len(universe)} tickers "
        f"({len(ats_tickers)} ATS + base watchlist)"
    )

    results = run_bandar_scan(universe, min_signal="AKUMULASI_SENYAP")
    actionable = [r for r in results if r.is_actionable and not r.error]
    if not actionable:
        return

    # Tag mana yang dari ATS vs base watchlist
    ats_set = set(ats_tickers)
    for r in actionable:
        r._source = "🔥 ATS+BH" if r.ticker in ats_set else "🎯 BH"

    msg = format_bandar_telegram(actionable)
    if msg:
        send_telegram_fn(msg)


# ─────────────────────────────────────────────────────────────
# TELEGRAM FORMAT
# ─────────────────────────────────────────────────────────────
def format_bandar_telegram(signals: list[BandarSignal]) -> str:
    """Format Telegram untuk sinyal bandar."""
    if not signals:
        return ""

    ts    = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    lines = [
        f"🎯 *BANDAR HUNTER ALERT*",
        f"{'─' * 28}",
        f"⏰ {ts}",
        f"{'─' * 28}",
    ]

    confidence_em = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "⚪"}

    for s in signals:
        edu  = s.education
        c_em = confidence_em.get(s.confidence, "⚪")
        lines.append(
            f"{edu['icon']} *{s.ticker}* — {edu['label']}\n"
            f"   {c_em} Confidence: {s.confidence} | Harga: {int(s.last_price):,}\n"
            f"   📊 Vol: {s.vol_ratio:.1f}× | Chg: {s.price_chg_3c:+.2f}% (3 candle)\n"
            f"   📌 {edu['aksi']}"
        )
        if s.fvg:
            lines.append(f"   ⚡ FVG terdeteksi — gap harga belum terisi")
        lines.append("")

    lines.append("─" * 28)
    lines.append("_Selalu konfirmasi di D1 sebelum eksekusi_ 🎯")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# UNDERVALUED + ACCUMULATION SCANNER
# ─────────────────────────────────────────────────────────────
# Livermore: "The line of least resistance — find where smart
# money is quietly accumulating at a discount."
# ─────────────────────────────────────────────────────────────

def scan_undervalued(
    tickers     : list[str],
    discount_min: float = 25.0,   # min diskon dari 52W high (%)
    rsi_max     : float = 45.0,   # RSI D1 maksimum
    vol_days    : int   = 3,       # min hari berturut vol di atas avg
    progress_cb = None,
) -> list[dict]:
    """
    Scan saham yang undervalued secara teknikal DAN sedang diakumulasi.

    Grading:
        A = semua 3 kriteria terpenuhi (discount + RSI + vol akumulasi)
        B = 2 dari 3 kriteria
        C = 1 kriteria
        - = tidak ada kriteria (tidak ditampilkan)

    Return list of dict — bukan dataclass agar fleksibel untuk UI.
    """
    results = []
    n = len(tickers)

    for i, ticker in enumerate(tickers):
        if progress_cb:
            progress_cb(i, n, ticker)

        tkr_jk = ticker if ticker.endswith(".JK") else f"{ticker}.JK"
        try:
            df = yf.download(
                tkr_jk, period="260d", interval="1d",
                progress=False, auto_adjust=True
            )
            if df is None or len(df) < 60:
                time.sleep(0.05)
                continue

            cl = df["Close"].squeeze().astype(float)
            vl = df["Volume"].squeeze().astype(float)

            last_price = float(cl.iloc[-1])
            high_52w   = float(cl.tail(252).max())
            low_52w    = float(cl.tail(252).min())

            # ── Diskon dari 52W high ──────────────────────────
            discount_52w = ((high_52w - last_price) / high_52w * 100
                            if high_52w > 0 else 0.0)

            # ── RSI 14 D1 ─────────────────────────────────────
            delta  = cl.diff()
            gain   = delta.clip(lower=0).rolling(14).mean()
            loss   = (-delta.clip(upper=0)).rolling(14).mean()
            rs     = gain / loss.replace(0, np.nan)
            rsi_d1 = float((100 - 100 / (1 + rs)).iloc[-1])

            # ── Volume akumulasi D1 ───────────────────────────
            vol_avg20 = float(vl.rolling(20).mean().iloc[-1])
            consec_vol = 0
            for v in reversed(vl.values[-10:]):
                if v > vol_avg20:
                    consec_vol += 1
                else:
                    break

            # Price trend saat volume naik (sideways atau naik = akumulasi)
            price_chg_5d = ((last_price - float(cl.iloc[-6])) / float(cl.iloc[-6]) * 100
                            if len(cl) >= 6 else 0.0)

            vol_accumulating = (consec_vol >= vol_days and
                                price_chg_5d >= -2.0)   # harga tidak collapse

            # ── Vol trend label ───────────────────────────────
            vol_ma5_now  = float(vl.iloc[-5:].mean())
            vol_ma5_prev = float(vl.iloc[-10:-5].mean()) if len(vl) >= 10 else vol_ma5_now
            if vol_ma5_now > vol_ma5_prev * 1.1:
                vol_trend = "NAIK"
            elif vol_ma5_now < vol_ma5_prev * 0.9:
                vol_trend = "TURUN"
            else:
                vol_trend = "FLAT"

            # ── Bandar score (reuse existing logic) ──────────
            bh_sig = _detect_bandar(ticker)
            bandar_score_proxy = (
                2 if bh_sig.signal_type in ("MARKUP_AWAL", "AKUMULASI_SENYAP") else
                1 if bh_sig.signal_type == "VOLUME_ANOMALI" else 0
            )

            # ── PBV dari yfinance (kalau tersedia) ────────────
            pbv = None
            try:
                info = yf.Ticker(tkr_jk).info
                pbv_raw = info.get("priceToBook")
                if pbv_raw and pbv_raw > 0:
                    pbv = round(float(pbv_raw), 2)
            except Exception:
                pass

            # ── Scoring dan grading ───────────────────────────
            crit_discount = discount_52w >= discount_min
            crit_rsi      = rsi_d1 <= rsi_max
            crit_vol      = vol_accumulating

            score = sum([crit_discount, crit_rsi, crit_vol])
            if score == 0:
                time.sleep(0.05)
                continue

            grade = "A" if score == 3 else "B" if score == 2 else "C"

            # ── Fase label ────────────────────────────────────
            if grade == "A" and vol_accumulating:
                phase = "🤫 Akumulasi Diam-Diam"
            elif crit_rsi and crit_discount:
                phase = "🔍 Potensi Reversal"
            elif crit_discount and crit_vol:
                phase = "📦 Akumulasi di Diskon"
            else:
                phase = "👀 Monitor"

            # ── Rationale ─────────────────────────────────────
            reasons = []
            if crit_discount:
                reasons.append(f"diskon {discount_52w:.0f}% dari 52W high")
            if crit_rsi:
                reasons.append(f"RSI {rsi_d1:.0f} oversold")
            if crit_vol:
                reasons.append(f"vol naik {consec_vol} hari berturut")
            rationale = " + ".join(reasons)

            # ── Sektor ───────────────────────────────────────
            from config.universe import get_sector
            sector = get_sector(ticker)

            results.append({
                "ticker"       : ticker,
                "grade"        : grade,
                "sector"       : sector,
                "price"        : round(last_price, 0),
                "high_52w"     : round(high_52w, 0),
                "low_52w"      : round(low_52w, 0),
                "discount_52w" : round(discount_52w, 1),
                "rsi"          : round(rsi_d1, 1),
                "vol_trend"    : vol_trend,
                "consec_vol"   : consec_vol,
                "bandar_score" : bandar_score_proxy,
                "pbv"          : pbv,
                "phase"        : phase,
                "rationale"    : rationale,
                "score"        : score,
            })

        except Exception as e:
            LOG.warning(f"UV scan {ticker}: {e}")

        time.sleep(0.08)

    if progress_cb:
        progress_cb(n, n, "selesai")

    # Sort: grade A dulu, lalu score, lalu discount
    grade_order = {"A": 3, "B": 2, "C": 1}
    results.sort(
        key=lambda r: (grade_order.get(r["grade"], 0),
                       r["score"], r["discount_52w"]),
        reverse=True
    )
    return results


def format_uv_telegram(results: list[dict]) -> str:
    """Format Telegram untuk hasil Undervalued scanner."""
    if not results:
        return ""

    ts    = datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")
    grade_em = {"A": "🔴", "B": "🟡", "C": "⚪"}

    lines = [
        f"🔍 *UNDERVALUED + AKUMULASI*",
        f"{'─' * 28}",
        f"⏰ {ts}",
        f"{'─' * 28}",
    ]

    for r in results:
        em = grade_em.get(r["grade"], "⚪")
        lines.append(
            f"{em} *{r['ticker']}* [Grade {r['grade']}] — {r['sector']}\n"
            f"   💰 Rp {int(r['price']):,} | Diskon {r['discount_52w']:.0f}% dari 52W high\n"
            f"   📊 RSI: {r['rsi']:.0f} | Vol: {r['vol_trend']} | Bandar: {r['bandar_score']}/2\n"
            f"   📌 {r['phase']} — {r['rationale']}"
        )
        lines.append("")

    lines.append("─" * 28)
    lines.append(
        "_Proxy teknikal — konfirmasi dengan ATS sebelum eksekusi_ 🔍"
    )
    return "\n".join(lines)
