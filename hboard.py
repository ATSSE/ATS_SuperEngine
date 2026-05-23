warning: in the working copy of 'dashboard.py', LF will be replaced by CRLF the next time Git touches it
[1mdiff --git a/dashboard.py b/dashboard.py[m
[1mindex 8967baf..d225dd6 100644[m
[1m--- a/dashboard.py[m
[1m+++ b/dashboard.py[m
[36m@@ -113,10 +113,28 @@[m [m_telegram_lock = threading.Lock()[m
 # ============================================================[m
 # VERSION HISTORY[m
 # ============================================================[m
[31m-APP_VERSION  = "V5.6.3"[m
[31m-APP_UPDATED  = "06 Mei 2026"[m
[32m+[m[32mAPP_VERSION  = "V5.6.4"[m
[32m+[m[32mAPP_UPDATED  = "12 Mei 2026"[m
 [m
 VERSION_HISTORY = [[m
[32m+[m[32m    {[m
[32m+[m[32m        "versi":   "V5.6.4",[m
[32m+[m[32m        "tanggal": "12 Mei 2026",[m
[32m+[m[32m        "tipe":    "Feature — Falcon Macro Check SOP A.1",[m
[32m+[m[32m        "ringkasan": "Tombol Cek Konteks Makro otomatis di tab Falcon Hunter — fetch data global real-time sesuai SOP A.1",[m
[32m+[m[32m        "detail": [[m
[32m+[m[32m            "[FEAT #1] get_falcon_macro_context(): fetch Dow, S&P500, Nikkei, KOSPI, IHSG, USD/IDR via yfinance",[m
[32m+[m[32m            "  Data diambil real-time saat tombol diklik — tidak perlu buka tab lain",[m
[32m+[m[32m            "  Status setiap indeks: naik/turun/flat dengan delta persen",[m
[32m+[m[32m            "[FEAT #2] Verdict makro otomatis (BULLISH/NEUTRAL/BEARISH) untuk guidance Falcon sizing",[m
[32m+[m[32m            "  Logic: ≥3 dari 4 indeks global naik → BULLISH; ≥3 turun → BEARISH; sisanya NEUTRAL",[m
[32m+[m[32m            "  Override: jika IHSG BEARISH dari scan → verdict BEARISH otomatis",[m
[32m+[m[32m            "[FEAT #3] Tombol '🌍 Cek Makro (SOP A.1)' di row tombol scan Falcon",[m
[32m+[m[32m            "  Tampil di expander 'Konteks Makro SOP A.1' dengan tabel terstruktur",[m
[32m+[m[32m            "  Checklist sesuai SOP A.1: Dow/S&P semalam, Asia pagi, Rupiah, guidance besok",[m
[32m+[m[32m            "[FEAT #4] Checklist berita besok manual — reminder no-trade day kalau ada FOMC/BI Rate",[m
[32m+[m[32m        ][m
[32m+[m[32m    },[m
     {[m
         "versi":   "V5.6.3",[m
         "tanggal": "06 Mei 2026",[m
[36m@@ -2755,6 +2773,84 @@[m [mdef start_scheduler():[m
 [m
 _scheduler = start_scheduler()[m
 [m
[32m+[m[32m# ============================================================[m
[32m+[m[32m# 🌍 FALCON MACRO CONTEXT — SOP A.1[m
[32m+[m[32m# Fetch data makro global real-time untuk checklist Falcon[m
[32m+[m[32m# ============================================================[m
[32m+[m
[32m+[m[32mdef get_falcon_macro_context() -> dict:[m
[32m+[m[32m    """[m
[32m+[m[32m    Fetch data makro sesuai SOP Falcon A.1:[m
[32m+[m[32m    - Wall Street semalam (Dow, S&P500)[m
[32m+[m[32m    - Asia pagi (Nikkei, KOSPI)[m
[32m+[m[32m    - IHSG hari ini[m
[32m+[m[32m    - USD/IDR Rupiah[m
[32m+[m[32m    Return dict lengkap dengan verdict makro Falcon.[m
[32m+[m[32m    """[m
[32m+[m[32m    result = {[m
[32m+[m[32m        "dow":    {"value": None, "change": 0.0, "status": "flat"},[m
[32m+[m[32m        "sp500":  {"value": None, "change": 0.0, "status": "flat"},[m
[32m+[m[32m        "nikkei": {"value": None, "change": 0.0, "status": "flat"},[m
[32m+[m[32m        "kospi":  {"value": None, "change": 0.0, "status": "flat"},[m
[32m+[m[32m        "ihsg":   {"value": None, "change": 0.0, "status": "flat"},[m
[32m+[m[32m        "usdidr": {"value": None, "change": 0.0, "status": "flat"},[m
[32m+[m[32m        "verdict": "NEUTRAL",[m
[32m+[m[32m        "verdict_reason": "Data belum lengkap",[m
[32m+[m[32m        "fetch_time": datetime.now(WIB).strftime("%H:%M WIB"),[m
[32m+[m[32m    }[m
[32m+[m
[32m+[m[32m    ticker_map = {[m
[32m+[m[32m        "dow":    "^DJI",[m
[32m+[m[32m        "sp500":  "^GSPC",[m
[32m+[m[32m        "nikkei": "^N225",[m
[32m+[m[32m        "kospi":  "^KS11",[m
[32m+[m[32m        "ihsg":   "^JKSE",[m
[32m+[m[32m        "usdidr": "USDIDR=X",[m
[32m+[m[32m    }[m
[32m+[m
[32m+[m[32m    for key, tkr in ticker_map.items():[m
[32m+[m[32m        try:[m
[32m+[m[32m            df = yf.download(tkr, period="5d", interval="1d",[m
[32m+[m[32m                             progress=False, auto_adjust=True)[m
[32m+[m[32m            if df is None or len(df) < 2:[m
[32m+[m[32m                continue[m
[32m+[m[32m            cl = df["Close"].squeeze()[m
[32m+[m[32m            last = float(cl.iloc[-1])[m
[32m+[m[32m            prev = float(cl.iloc[-2])[m
[32m+[m[32m            chg  = (last - prev) / prev * 100 if prev > 0 else 0.0[m
[32m+[m[32m            status = "up" if chg > 0.3 else ("down" if chg < -0.3 else "flat")[m
[32m+[m[32m            result[key] = {"value": last, "change": chg, "status": status}[m
[32m+[m[32m        except Exception as e:[m
[32m+[m[32m            LOG.warning(f"macro_fetch {tkr}: {e}")[m
[32m+[m
[32m+[m[32m    # ── Verdict logic ────────────────────────────────────────[m
[32m+[m[32m    global_keys    = ["dow", "sp500", "nikkei", "kospi"][m
[32m+[m[32m    n_up   = sum(1 for k in global_keys if result[k]["status"] == "up")[m
[32m+[m[32m    n_down = sum(1 for k in global_keys if result[k]["status"] == "down")[m
[32m+[m
[32m+[m[32m    if n_down >= 3:[m
[32m+[m[32m        verdict = "BEARISH"[m
[32m+[m[32m        reason  = f"{n_down}/4 indeks global turun — sentimen negatif, Falcon waspada"[m
[32m+[m[32m    elif n_up >= 3:[m
[32m+[m[32m        verdict = "BULLISH"[m
[32m+[m[32m        reason  = f"{n_up}/4 indeks global naik — sentimen positif, Falcon aktif"[m
[32m+[m[32m    else:[m
[32m+[m[32m        verdict = "NEUTRAL"[m
[32m+[m[32m        reason  = f"Sentimen global mixed ({n_up} naik, {n_down} turun) — Falcon selektif"[m
[32m+[m
[32m+[m[32m    # IHSG override — paling menentukan untuk IDX[m
[32m+[m[32m    ihsg_st = st.session_state.get("falcon_ihsg_status", "")[m
[32m+[m[32m    if ihsg_st == "BEARISH":[m
[32m+[m[32m        verdict = "BEARISH"[m
[32m+[m[32m        reason  = "IHSG BEARISH (di bawah MA20 & MA50) — Falcon istirahat, paper trade only"[m
[32m+[m[32m    elif ihsg_st == "BULLISH" and verdict == "BULLISH":[m
[32m+[m[32m        reason = f"IHSG BULLISH + {n_up}/4 global naik — kondisi ideal, full size"[m
[32m+[m
[32m+[m[32m    result["verdict"]        = verdict[m
[32m+[m[32m    result["verdict_reason"] = reason[m
[32m+[m[32m    return result[m
[32m+[m
[32m+[m
 # ============================================================[m
 # UI[m
 # ============================================================[m
[36m@@ -4566,14 +4662,92 @@[m [mwith tabs[6]:[m
         watchlist_input = [t.strip().upper() for t in wl_raw.split(",") if t.strip()][m
 [m
     # ── Scan button ───────────────────────────────────────────[m
[31m-    col_btn1, col_btn2, _ = st.columns([1, 1, 3])[m
[32m+[m[32m    col_btn1, col_btn2, col_btn3 = st.columns([1.2, 1, 1.2])[m
     with col_btn1:[m
         do_scan = st.button("🦅 Jalankan Falcon Scan", type="primary",[m
                             use_container_width=True)[m
     with col_btn2:[m
         only_setup = st.checkbox("Tampilkan setup saja", value=True)[m
[32m+[m[32m    with col_btn3:[m
[32m+[m[32m        do_macro = st.button("🌍 Cek Makro (SOP A.1)",[m
[32m+[m[32m                             use_container_width=True,[m
[32m+[m[32m                             help="Fetch data Dow, Nikkei, KOSPI, IHSG, Rupiah — sesuai checklist SOP A.1")[m
[32m+[m
[32m+[m[32m    # ── Macro Check SOP A.1 ───────────────────────────────────[m
[32m+[m[32m    if do_macro:[m
[32m+[m[32m        with st.spinner("🌍 Mengambil data makro global..."):[m
[32m+[m[32m            macro = get_falcon_macro_context()[m
[32m+[m[32m        st.session_state["falcon_macro"] = macro[m
[32m+[m
[32m+[m[32m    if "falcon_macro" in st.session_state:[m
[32m+[m[32m        macro = st.session_state["falcon_macro"][m
[32m+[m[32m        verdict    = macro["verdict"][m
[32m+[m[32m        v_color    = {"BULLISH": "success", "NEUTRAL": "warning", "BEARISH": "error"}[m
[32m+[m[32m        v_emoji    = {"BULLISH": "🟢", "NEUTRAL": "🟡", "BEARISH": "🔴"}[m
[32m+[m[32m        v_action   = {[m
[32m+[m[32m            "BULLISH": "Full size, scan agresif, target normal.",[m
[32m+[m[32m            "NEUTRAL": "Size ½ — hanya ambil Falcon Score tertinggi.",[m
[32m+[m[32m            "BEARISH": "🛑 Falcon istirahat — paper trade only. Jangan entry real.",[m
[32m+[m[32m        }[m
 [m
[31m-    # ── Run scan ──────────────────────────────────────────────[m
[32m+[m[32m        with st.expander([m
[32m+[m[32m            f"🌍 Konteks Makro SOP A.1 — {v_emoji.get(verdict,'')} {verdict} "[m
[32m+[m[32m            f"| {macro['fetch_time']}",[m
[32m+[m[32m            expanded=True,[m
[32m+[m[32m        ):[m
[32m+[m[32m            # Verdict banner[m
[32m+[m[32m            getattr(st, v_color.get(verdict, "info"))([m
[32m+[m[32m                f"**{v_emoji.get(verdict,'')} Verdict Makro Falcon: {verdict}** — "[m
[32m+[m[32m                f"{macro['verdict_reason']}\n\n"[m
[32m+[m[32m                f"📌 Panduan: {v_action.get(verdict,'')}"[m
[32m+[m[32m            )[m
[32m+[m
[32m+[m[32m            # Metrics row 1: Wall Street[m
[32m+[m[32m            st.markdown("**📊 Wall Street semalam**")[m
[32m+[m[32m            mw1, mw2 = st.columns(2)[m
[32m+[m
[32m+[m[32m            def _fmt_val(d: dict, is_currency: bool = False) -> str:[m
[32m+[m[32m                if d["value"] is None: return "N/A"[m
[32m+[m[32m                v = d["value"][m
[32m+[m[32m                return f"{v:,.0f}" if not is_currency else f"Rp {v:,.0f}"[m
[32m+[m
[32m+[m[32m            def _delta_str(d: dict) -> str:[m
[32m+[m[32m                if d["value"] is None: return ""[m
[32m+[m[32m                sign = "▲" if d["status"] == "up" else ("▼" if d["status"] == "down" else "→")[m
[32m+[m[32m                return f"{sign} {d['change']:+.2f}%"[m
[32m+[m
[32m+[m[32m            mw1.metric("Dow Jones",  _fmt_val(macro["dow"]),   _delta_str(macro["dow"]))[m
[32m+[m[32m            mw2.metric("S&P 500",    _fmt_val(macro["sp500"]), _delta_str(macro["sp500"]))[m
[32m+[m
[32m+[m[32m            st.markdown("**🌏 Asia pagi ini**")[m
[32m+[m[32m            ma1, ma2 = st.columns(2)[m
[32m+[m[32m            ma1.metric("Nikkei 225", _fmt_val(macro["nikkei"]), _delta_str(macro["nikkei"]))[m
[32m+[m[32m            ma2.metric("KOSPI",      _fmt_val(macro["kospi"]),  _delta_str(macro["kospi"]))[m
[32m+[m
[32m+[m[32m            st.markdown("**💱 Rupiah & IHSG**")[m
[32m+[m[32m            mr1, mr2 = st.columns(2)[m
[32m+[m[32m            mr1.metric("USD/IDR",[m
[32m+[m[32m                       f"Rp {macro['usdidr']['value']:,.0f}" if macro["usdidr"]["value"] else "N/A",[m
[32m+[m[32m                       _delta_str(macro["usdidr"]),[m
[32m+[m[32m                       delta_color="inverse")   # rupiah melemah = negatif[m
[32m+[m[32m            mr2.metric("IHSG",[m
[32m+[m[32m                       _fmt_val(macro["ihsg"]),[m
[32m+[m[32m                       _delta_str(macro["ihsg"]))[m
[32m+[m
[32m+[m[32m            # Checklist manual[m
[32m+[m[32m            st.markdown("---")[m
[32m+[m[32m            st.markdown("**📋 Checklist SOP A.1 — Manual**")[m
[32m+[m[32m            st.markdown([m
[32m+[m[32m                "- [ ] **Komoditas** (CPO, batu bara, nikel) → cek Investing.com\n"[m
[32m+[m[32m                "- [ ] **Yield SBN 10Y** → cek DJPPR / Bloomberg Indonesia\n"[m
[32m+[m[32m                "- [ ] **Berita besok** (FOMC? BI Rate? Inflasi?) → kalau ada: *no-trade day*"[m
[32m+[m[32m            )[m
[32m+[m[32m            st.caption([m
[32m+[m[32m                f"Data dari yfinance — diambil {macro['fetch_time']}. "[m
[32m+[m[32m                "Klik tombol lagi untuk refresh."[m
[32m+[m[32m            )[m
[32m+[m
[32m+[m[32m    st.markdown("---")[m
     if do_scan:[m
         falcon_params = FalconParams([m
             vol_breakout_mult = vol_brk,[m
