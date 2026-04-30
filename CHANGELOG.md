# ATS SuperEngine — Tabel Perkembangan Sistem
# V2.1 → V3.0 → V4.0

| # | Komponen | V2.1 (Awal) | V3.0 | V4.0 (Terbaru) | Dampak |
|---|---|---|---|---|---|
| 1 | **VWAP** | Kumulatif 6 bulan (salah) | Rolling 20 hari ✅ | Rolling 20 hari ✅ | Sinyal momentum lebih akurat |
| 2 | **RSI** | Simple rolling mean | Wilder's smoothing EWM ✅ | Wilder's smoothing EWM ✅ | Nilai RSI lebih presisi |
| 3 | **ATR** | Simple rolling | Wilder's EWM ✅ | Wilder's EWM ✅ | Stop loss lebih akurat |
| 4 | **Pivot Point / Target** | Swing high 20/50 hari saja | PP mean 5 hari + Fib (rumus salah) | PP (H+L+C)/3 satu candle + Fib ✅ | Target resistance lebih valid |
| 5 | **Stop Loss** | Flat % (5%) | ATR × 1.5 + floor 7% ✅ | ATR × 1.5 + floor 7% ✅ | SL menyesuaikan volatilitas |
| 6 | **Position Sizing** | Flat formula | ATR-adjusted (kurangi lot saat volatile) ✅ | ATR-adjusted ✅ | Risk lebih terkontrol |
| 7 | **Bandar Detection** | Distribusi terlalu sensitif (vol < 80%) | Diperketat (vol < 60%, gain > 1.5%) ✅ | Sama ✅ | Lebih sedikit false signal |
| 8 | **Persistensi State** | Tidak ada (hilang saat restart) | JSON (cybernetic + signal_lock) ✅ | JSON + balance ✅ | Parameter tidak reset |
| 9 | **Balance Persistensi** | Tidak ada | Tidak ada | Tersimpan di JSON ✅ | Balance tidak reset saat restart |
| 10 | **Bug BUY Logic** | Tambah yang sudah ada (bug) | Diperbaiki: tambah yang belum ada ✅ | Diperbaiki ✅ | Active trades tidak duplikat |
| 11 | **Validasi Data Ticker** | Tidak ada | Skip: <60 bar, harga/vol = 0 ✅ | Sama ✅ | Tidak crash saat data rusak |
| 12 | **Filter Sektor** | Tidak ada | Hanya sektor momentum positif ✅ | Sama ✅ | Fokus pada sektor kuat |
| 13 | **ISSI Universe** | Ada saham non-syariah (BJBR, BJTM, BBTN, HMSP, UNVR, GOTO, BUKA) | Sama (belum difix) | Dibersihkan 7 non-syariah, +15 ticker valid ✅ | Universe 100% syariah |
| 14 | **Duplikasi Kode Scanner** | Ada | Duplikasi run_scanner & auto_scan | Refactor: satu fungsi scan_core() ✅ | Tidak ada risk inkonsistensi |
| 15 | **Balance di Auto Scan** | Hardcode 800rb | Hardcode 800rb | Baca dari ats_state.json ✅ | Lot kalkulasi sesuai modal asli |
| 16 | **Dead Code** | Banyak | finnhub_quote, pullback_zone, lot_size, acct_rr tidak dipakai | Semua dihapus ✅ | Kode lebih bersih |
| 17 | **Signal Lock Expire** | Tidak ada (JSON terus membesar) | Tidak ada | Auto-expire 7 hari ✅ | File JSON tidak membengkak |
| 18 | **Cybernetic Min Trades** | 8 trade | 8 trade | 20 trade ✅ | Lebih statistically valid |
| 19 | **Hari Libur IDX** | Tidak ada | Tidak ada | 20 hari libur 2025 ditambahkan ✅ | Tidak scan saat bursa libur |
| 20 | **Top N Hasil Scan** | Hardcode 5 | Hardcode 5 | Configurable 3–15 ✅ | User bisa pilih berapa kandidat |
| 21 | **Server Health Check** | Tidak ada | Tidak ada | Telegram notif saat app start/restart ✅ | Tahu kalau server mati/hidup |
| 22 | **Change% Harian** | Tidak ditampilkan | Tidak ditampilkan | Ditampilkan di tabel & Telegram ✅ | Konteks momentum harga hari ini |
| 23 | **Telegram Summary** | Hanya kirim kalau ada EXECUTE | Hanya kirim kalau ada EXECUTE | Kirim summary meski tidak ada EXECUTE ✅ | Konfirmasi scan sudah berjalan |
| 24 | **Active Trades Kolom** | Entry, SL, Target saja | Sama | + ExitPrice, ExitDate, PnL ✅ | Tracking trade lebih lengkap |
| 25 | **Journal Kolom Wajib** | Tidak ada validasi | Tidak ada validasi | Kolom wajib: Date, Ticker, Entry, Exit, Lot, PnL, Notes ✅ | Journal lebih terstruktur |
| 26 | **Balance Sync** | Tidak sinkron antar tab | Tidak sinkron | st.rerun() + save_state() ✅ | Balance langsung update semua tab |
| 27 | **Dynamic Threshold** | Static (hardcode 85/75/65) | Percentile P88/P70/P45 ✅ | Sama ✅ | Threshold adaptif per kondisi market |
| 28 | **Scan Debug** | Tidak ada | Ada expander alasan gugur ✅ | Sama + filter sektor/status ✅ | Transparansi kenapa saham tidak lolos |
| 29 | **Equity Curve** | Tidak ada | Ada (Plotly) ✅ | Sama ✅ | Visualisasi performa trading |
| 30 | **Drawdown Chart** | Tidak ada | Ada ✅ | Sama ✅ | Monitor risiko drawdown |
| 31 | **Sector Leader Radar** | Ada (basic) | Bar chart dengan warna ✅ | Sama ✅ | Visual kekuatan sektor |
| 32 | **Tombol Warna** | Merah (primary default) | Hijau via CSS ✅ | Sama ✅ | Eye friendly |
| 33 | **Auto Scheduler** | Tidak ada (manual saja) | APScheduler 4x sehari ✅ | Sama + holiday filter ✅ | Tidak perlu online 24 jam |
| 34 | **Jam Bursa WIB** | Tidak ditampilkan | Ditampilkan ✅ | + status libur nasional ✅ | Selalu tahu status market |
| 35 | **Intraday Data** | 5d interval | 5d interval | 2d interval (lebih ringan) ✅ | Request lebih cepat |
| 36 | **Server Deploy** | Lokal saja | Lokal saja | Railway + GitHub CI/CD ✅ | 24/7 tanpa buka komputer |

---

## Ringkasan Statistik Perbaikan

| Versi | Jumlah Perbaikan | Critical Fix | Bug Fix | New Feature |
|---|---|---|---|---|
| V2.1 → V3.0 | 15 item | 5 | 4 | 6 |
| V3.0 → V4.0 | 21 item | 7 | 5 | 9 |
| **Total V2.1 → V4.0** | **36 item** | **12** | **9** | **15** |
