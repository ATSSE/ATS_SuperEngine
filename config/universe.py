# =============================================================
# config/universe.py
# ATS SuperEngine V3.1 — ISSI Universe (Verified Syariah)
#
# Referensi: OJK DES (Daftar Efek Syariah) Periode I 2026
#            KEP-21/D.04/2026, ditetapkan 21 Mei 2026
# Diupdate: 01 Juli 2026
#
# PERUBAHAN dari versi 30 Mei 2026:
#   DITAMBAHKAN — terkonfirmasi ada di DES Periode I 2026 Lampiran I
#   (Sektor A. Energi) tapi kelewat saat compile manual sebelumnya:
#     RAJA  — Rukun Raharja (No. 52 dalam KEP-21/D.04/2026)
#     RATU  — Raharja Energi Cepu (No. 53 dalam KEP-21/D.04/2026)
#
# PERUBAHAN dari versi sebelumnya (DES Periode II 2024):
#   DIHAPUS — tidak ada di DES Periode I 2026:
#     AMMN  — Amman Mineral (keluar dari DES)
#     AMRT  — Sumber Alfaria Trijaya (keluar dari DES)
#     ASII  — Astra International (keluar dari DES)
#     BPAM  — Bareksa Portal Investama (keluar dari DES)
#     EMTK  — Elang Mahkota Teknologi (keluar dari DES)
#     ICHI  — Ichitan Group (keluar dari DES)
#     INCO  — Vale Indonesia (keluar dari DES)
#     NCKL  — Trimegah Bangun Persada (keluar dari DES)
#     PGEO  — Pertamina Geothermal (keluar dari DES)
#     PNLF  — Panin Financial (keluar dari DES)
#     SCMA  — Surya Citra Media (keluar dari DES)
#     SSMS  — Sawit Sumbermas Sarana (keluar dari DES)
#     TBIG  — Tower Bersama (keluar dari DES)
#     TBLA  — Tunas Baru Lampung (keluar dari DES)
#     TOWR  — Sarana Menara Nusantara (keluar dari DES)
#
# CATATAN MAINTENANCE:
#   DES terbit 2x/tahun (Periode I ~Mei, Periode II ~November).
#   Cek ulang list ini tiap rilis DES baru dari OJK — jangan tunggu
#   ticker "hilang" ketauan pas live trading.
# =============================================================

ISSI_UNIVERSE = [

    # ================= ENERGY =================
    "ADRO.JK",   # Adaro (Alamtri Resources) — DES I 2026 ✓
    "ADMR.JK",   # Adaro/Alamtri Minerals — DES I 2026 ✓
    "ITMG.JK",   # Indo Tambangraya Megah — DES I 2026 ✓
    "PTBA.JK",   # Bukit Asam — DES I 2026 ✓
    "HRUM.JK",   # Harum Energy — DES I 2026 ✓
    "PGAS.JK",   # Perusahaan Gas Negara — DES I 2026 ✓
    "MEDC.JK",   # Medco Energi — DES I 2026 ✓
    "ELSA.JK",   # Elnusa — DES I 2026 ✓
    "BUMI.JK",   # Bumi Resources — DES I 2026 ✓
    "INDY.JK",   # Indika Energy — DES I 2026 ✓
    "AKRA.JK",   # AKR Corporindo — DES I 2026 ✓
    "RAJA.JK",   # Rukun Raharja — DES I 2026 ✓ (No. 52, ditambahkan 1 Jul 2026)
    "RATU.JK",   # Raharja Energi Cepu — DES I 2026 ✓ (No. 53, ditambahkan 1 Jul 2026)

    # ================= MINING / BARANG BAKU =================
    "ANTM.JK",   # Aneka Tambang — DES I 2026 ✓
    "MDKA.JK",   # Merdeka Copper Gold — DES I 2026 ✓
    "TINS.JK",   # Timah — DES I 2026 ✓
    "BRMS.JK",   # Bumi Resources Minerals — DES I 2026 ✓
    "HRTA.JK",   # Hartadinata Abadi — DES I 2026 ✓
    "MBMA.JK",   # Merdeka Battery Materials — DES I 2026 ✓
    "INKP.JK",   # Indah Kiat Pulp & Paper — DES I 2026 ✓
    "TKIM.JK",   # Pabrik Kertas Tjiwi Kimia — DES I 2026 ✓
    "INTP.JK",   # Indocement Tunggal Prakarsa — DES I 2026 ✓
    "SMGR.JK",   # Semen Indonesia — DES I 2026 ✓

    # ================= BANK SYARIAH (Emiten Syariah) =================
    "BRIS.JK",   # Bank Syariah Indonesia — DES I 2026 ✓ (Emiten Syariah)
    "BTPS.JK",   # Bank BTPN Syariah — DES I 2026 ✓ (Emiten Syariah)

    # ================= KEUANGAN =================
    "SRTG.JK",   # Saratoga Investama Sedaya — DES I 2026 ✓

    # ================= TELCO =================
    "TLKM.JK",   # Telkom Indonesia — DES I 2026 ✓
    "EXCL.JK",   # XLSMART Telecom (XL Axiata) — DES I 2026 ✓
    "ISAT.JK",   # Indosat Ooredoo — DES I 2026 ✓
    "MTEL.JK",   # Dayamitra Telekomunikasi (Mitratel) — DES I 2026 ✓

    # ================= CONSUMER STAPLES =================
    "ICBP.JK",   # Indofood CBP — DES I 2026 ✓
    "INDF.JK",   # Indofood — DES I 2026 ✓
    "CPIN.JK",   # Charoen Pokphand Indonesia — DES I 2026 ✓
    "JPFA.JK",   # JAPFA Comfeed — DES I 2026 ✓
    "SIDO.JK",   # Industri Jamu Sido Muncul — DES I 2026 ✓
    "ULTJ.JK",   # Ultra Jaya Milk — DES I 2026 ✓
    "MYOR.JK",   # Mayora Indah — DES I 2026 ✓
    "ROTI.JK",   # Nippon Indosari Corpindo — DES I 2026 ✓
    "STTP.JK",   # Siantar Top — DES I 2026 ✓
    "CLEO.JK",   # Sariguna Primatirta — DES I 2026 ✓
    "GOOD.JK",   # Garudafood — DES I 2026 ✓
    "FOOD.JK",   # Sentra Food Indonesia — DES I 2026 ✓
    "DSNG.JK",   # Dharma Satya Nusantara — DES I 2026 ✓
    "SIMP.JK",   # Salim Ivomas Pratama — DES I 2026 ✓
    "AALI.JK",   # Astra Agro Lestari — DES I 2026 ✓
    "LSIP.JK",   # PP London Sumatra — DES I 2026 ✓

    # ================= CONSUMER DISCRETIONARY =================
    "ACES.JK",   # Ace Hardware (Aspirasi Hidup Indonesia) — DES I 2026 ✓
    "ERAA.JK",   # Erajaya Swasembada — DES I 2026 ✓
    "MAPI.JK",   # Mitra Adiperkasa — DES I 2026 ✓
    "RALS.JK",   # Ramayana Lestari — DES I 2026 ✓
    "LPPF.JK",   # MDS Retailing (Matahari) — DES I 2026 ✓
    "MAPB.JK",   # MAP Boga Adiperkasa — DES I 2026 ✓

    # ================= HEALTHCARE =================
    "MIKA.JK",   # Mitra Keluarga Karyasehat — DES I 2026 ✓
    "HEAL.JK",   # Medikaloka Hermina — DES I 2026 ✓
    "KLBF.JK",   # Kalbe Farma — DES I 2026 ✓
    "SILO.JK",   # Siloam International Hospitals — DES I 2026 ✓
    "SAME.JK",   # Sarana Meditama Metropolitan — DES I 2026 ✓
    "MERK.JK",   # Merck Indonesia — DES I 2026 ✓
    "PYFA.JK",   # Pyridam Farma — DES I 2026 ✓
    "TSPC.JK",   # Tempo Scan Pacific — DES I 2026 ✓

    # ================= PROPERTY =================
    "CTRA.JK",   # Ciputra Development — DES I 2026 ✓
    "PWON.JK",   # Pakuwon Jati — DES I 2026 ✓
    "SMRA.JK",   # Summarecon Agung — DES I 2026 ✓
    "DMAS.JK",   # Puradelta Lestari — DES I 2026 ✓
    "BSDE.JK",   # Bumi Serpong Damai — DES I 2026 ✓
    "BEST.JK",   # Bekasi Fajar Industrial — DES I 2026 ✓
    "KIJA.JK",   # Kawasan Industri Jababeka — DES I 2026 ✓

    # ================= CONSTRUCTION / INFRASTRUCTURE =================
    "WSKT.JK",   # Waskita Karya — DES I 2026 ✓
    "WIKA.JK",   # Wijaya Karya — DES I 2026 ✓
    "PTPP.JK",   # PP (Pembangunan Perumahan) — DES I 2026 ✓
    "ADHI.JK",   # Adhi Karya — DES I 2026 ✓
    "WEGE.JK",   # Wijaya Karya Bangunan Gedung — DES I 2026 ✓
    "JSMR.JK",   # Jasa Marga — DES I 2026 ✓

    # ================= INDUSTRIAL =================
    "UNTR.JK",   # United Tractors — DES I 2026 ✓
    "IMPC.JK",   # Impack Pratama Industri — DES I 2026 ✓
    "SMSM.JK",   # Selamat Sempurna — DES I 2026 ✓
    "AUTO.JK",   # Astra Otoparts — DES I 2026 ✓
    "WOOD.JK",   # Integra Indocabinet — DES I 2026 ✓
    "MARK.JK",   # Mark Dynamics — DES I 2026 ✓
    "KBLI.JK",   # KMI Wire and Cable — DES I 2026 ✓
    "SCCO.JK",   # Supreme Cable Manufacturing — DES I 2026 ✓

    # ================= LOGISTICS =================
    "ASSA.JK",   # Adi Sarana Armada — DES I 2026 ✓
    "TMAS.JK",   # Temas (Pelayaran Tempuran Emas) — DES I 2026 ✓
    "SMDR.JK",   # Samudera Indonesia — DES I 2026 ✓
    "BIRD.JK",   # Blue Bird — DES I 2026 ✓
    "TRUK.JK",   # Guna Timur Raya — DES I 2026 ✓

    # ================= TECHNOLOGY =================
    "DCII.JK",   # DCI Indonesia (Data Center) — DES I 2026 ✓
    "MLPT.JK",   # Multipolar Technology — DES I 2026 ✓
    "MTDL.JK",   # Metrodata Electronics — DES I 2026 ✓
    "CYBR.JK",   # ITSEC Asia — DES I 2026 ✓
]

# =============================================================
# SECTOR MAP (sync dengan ISSI_UNIVERSE)
# =============================================================
SECTOR_MAP = {
    # ENERGY
    "ADRO": "Energy", "ADMR": "Energy", "ITMG": "Energy", "PTBA": "Energy",
    "HRUM": "Energy", "PGAS": "Energy", "MEDC": "Energy", "ELSA": "Energy",
    "BUMI": "Energy", "INDY": "Energy", "AKRA": "Energy",
    "RAJA": "Energy", "RATU": "Energy",

    # MINING / MATERIAL
    "ANTM": "Mining", "MDKA": "Mining", "TINS": "Mining",
    "BRMS": "Mining", "HRTA": "Mining", "MBMA": "Mining",
    "INKP": "Chemical", "TKIM": "Chemical",
    "INTP": "Industrial", "SMGR": "Industrial",

    # FINANCE SYARIAH
    "BRIS": "Finance", "BTPS": "Finance", "SRTG": "Finance",

    # TELCO
    "TLKM": "Telco", "EXCL": "Telco", "ISAT": "Telco", "MTEL": "Telco",

    # CONSUMER STAPLES
    "ICBP": "Consumer", "INDF": "Consumer", "CPIN": "Consumer", "JPFA": "Consumer",
    "SIDO": "Consumer", "ULTJ": "Consumer", "MYOR": "Consumer", "ROTI": "Consumer",
    "STTP": "Consumer", "CLEO": "Consumer", "GOOD": "Consumer",
    "FOOD": "Consumer", "DSNG": "Plantation", "SIMP": "Plantation",
    "AALI": "Plantation", "LSIP": "Plantation",

    # CONSUMER DISCRETIONARY
    "ACES": "Consumer", "ERAA": "Consumer", "MAPI": "Consumer",
    "RALS": "Consumer", "LPPF": "Consumer", "MAPB": "Consumer",

    # HEALTHCARE
    "MIKA": "Healthcare", "HEAL": "Healthcare", "KLBF": "Healthcare",
    "SILO": "Healthcare", "SAME": "Healthcare", "MERK": "Healthcare",
    "PYFA": "Healthcare", "TSPC": "Healthcare",

    # PROPERTY
    "CTRA": "Property", "PWON": "Property", "SMRA": "Property",
    "DMAS": "Property", "BSDE": "Property", "BEST": "Property", "KIJA": "Property",

    # CONSTRUCTION
    "WSKT": "Construction", "WIKA": "Construction", "PTPP": "Construction",
    "ADHI": "Construction", "WEGE": "Construction", "JSMR": "Construction",

    # INDUSTRIAL
    "UNTR": "Industrial", "IMPC": "Industrial",
    "SMSM": "Industrial", "AUTO": "Industrial", "WOOD": "Industrial",
    "MARK": "Industrial", "KBLI": "Industrial", "SCCO": "Industrial",

    # LOGISTICS
    "ASSA": "Logistics", "TMAS": "Logistics", "SMDR": "Logistics",
    "BIRD": "Logistics", "TRUK": "Logistics",

    # TECHNOLOGY
    "DCII": "Technology", "MLPT": "Technology", "MTDL": "Technology",
    "CYBR": "Technology",
}

# =============================================================
# HELPER
# =============================================================
def get_sector(ticker: str) -> str:
    symbol = ticker.replace(".JK", "")
    return SECTOR_MAP.get(symbol, "Other")
