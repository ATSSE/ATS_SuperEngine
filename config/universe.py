# =============================================================
# config/universe.py
# ATS SuperEngine V3.0 — ISSI Universe (Verified Syariah)
#
# Referensi: IDX ISSI Periode II 2024 (Nov 2024 - Apr 2025)
# Diupdate: April 2025
#
# DIHAPUS dari versi sebelumnya:
#   BJBR  — Bank Jabar Banten (bank konvensional, bukan syariah)
#   BJTM  — Bank Jatim (bank konvensional, bukan syariah)
#   BBTN  — Bank BTN (bank konvensional, unit syariah belum spinoff)
#   HMSP  — HM Sampoerna (rokok, haram)
#   UNVR  — Unilever (tidak konsisten masuk ISSI, produk bermasalah)
#   GOTO  — GoTo (tidak masuk ISSI karena ekuitas negatif)
#   BUKA  — Bukalapak (tidak konsisten masuk ISSI)
# =============================================================

ISSI_UNIVERSE = [

    # ================= ENERGY =================
    "ADRO.JK",   # Adaro Energy — ISSI ✓
    "ADMR.JK",   # Adaro Minerals — ISSI ✓
    "ITMG.JK",   # Indo Tambangraya Megah — ISSI ✓
    "PTBA.JK",   # Bukit Asam — ISSI ✓
    "HRUM.JK",   # Harum Energy — ISSI ✓
    "PGAS.JK",   # Perusahaan Gas Negara — ISSI ✓
    "MEDC.JK",   # Medco Energi — ISSI ✓
    "ELSA.JK",   # Elnusa — ISSI ✓
    "PGEO.JK",   # Pertamina Geothermal — ISSI ✓
    "BUMI.JK",   # Bumi Resources — ISSI ✓
    "INDY.JK",   # Indika Energy — ISSI ✓
    "AKRA.JK",   # AKR Corporindo — ISSI ✓

    # ================= MINING =================
    "ANTM.JK",   # Aneka Tambang — ISSI ✓
    "INCO.JK",   # Vale Indonesia — ISSI ✓
    "MDKA.JK",   # Merdeka Copper Gold — ISSI ✓
    "TINS.JK",   # Timah — ISSI ✓
    "NCKL.JK",   # Trimegah Bangun Persada (Nickel) — ISSI ✓
    "BRMS.JK",   # Bumi Resources Minerals — ISSI ✓
    "HRTA.JK",   # Hartadinata Abadi — ISSI ✓
    "AMMN.JK",   # Amman Mineral — ISSI ✓
    "MBMA.JK",   # Merdeka Battery Materials — ISSI ✓

    # ================= BANK SYARIAH =================
    # Hanya bank yang terdaftar di OJK sebagai Bank Umum Syariah
    "BRIS.JK",   # Bank Syariah Indonesia — ISSI ✓ (satu-satunya bank syariah besar)

    # ================= MULTIFINANCE SYARIAH =================
    "BPAM.JK",   # Bareksa Portal Investama — ISSI ✓
    "PNLF.JK",   # Panin Financial — ISSI ✓ (screened syariah)

    # ================= TELCO =================
    "TLKM.JK",   # Telkom Indonesia — ISSI ✓
    "EXCL.JK",   # XL Axiata — ISSI ✓
    "ISAT.JK",   # Indosat Ooredoo — ISSI ✓
    "MTEL.JK",   # Dayamitra Telekomunikasi (Mitratel) — ISSI ✓
    "TOWR.JK",   # Sarana Menara Nusantara — ISSI ✓
    "TBIG.JK",   # Tower Bersama — ISSI ✓

    # ================= CONSUMER STAPLES =================
    "ICBP.JK",   # Indofood CBP — ISSI ✓
    "INDF.JK",   # Indofood — ISSI ✓
    "CPIN.JK",   # Charoen Pokphand Indonesia — ISSI ✓
    "JPFA.JK",   # JAPFA Comfeed — ISSI ✓
    "SIDO.JK",   # Industri Jamu Sido Muncul — ISSI ✓
    "ULTJ.JK",   # Ultra Jaya Milk — ISSI ✓
    "MYOR.JK",   # Mayora Indah — ISSI ✓
    "ROTI.JK",   # Nippon Indosari Corpindo — ISSI ✓
    "STTP.JK",   # Siantar Top — ISSI ✓
    "CLEO.JK",   # Sariguna Primatirta — ISSI ✓
    "GOOD.JK",   # Garudafood — ISSI ✓
    "FOOD.JK",   # Sentra Food Indonesia — ISSI ✓
    "ICHI.JK",   # Ichitan Group — ISSI ✓

    # ================= CONSUMER DISCRETIONARY =================
    "AMRT.JK",   # Sumber Alfaria Trijaya (Alfamart) — ISSI ✓
    "ACES.JK",   # Ace Hardware Indonesia — ISSI ✓
    "ERAA.JK",   # Erajaya Swasembada — ISSI ✓
    "MAPI.JK",   # Mitra Adiperkasa — ISSI ✓
    "RALS.JK",   # Ramayana Lestari — ISSI ✓
    "LPPF.JK",   # Matahari Department Store — ISSI ✓
    "MAPB.JK",   # MAP Boga Adiperkasa — ISSI ✓

    # ================= HEALTHCARE =================
    "MIKA.JK",   # Mitra Keluarga Karyasehat — ISSI ✓
    "HEAL.JK",   # Medikaloka Hermina — ISSI ✓
    "KLBF.JK",   # Kalbe Farma — ISSI ✓
    "SILO.JK",   # Siloam International Hospitals — ISSI ✓
    "SAME.JK",   # Sarana Meditama Metropolitan — ISSI ✓
    "MERK.JK",   # Merck Indonesia — ISSI ✓
    "PYFA.JK",   # Pyridam Farma — ISSI ✓

    # ================= PROPERTY =================
    "CTRA.JK",   # Ciputra Development — ISSI ✓
    "PWON.JK",   # Pakuwon Jati — ISSI ✓
    "SMRA.JK",   # Summarecon Agung — ISSI ✓
    "DMAS.JK",   # Puradelta Lestari — ISSI ✓
    "BSDE.JK",   # Bumi Serpong Damai — ISSI ✓
    "BEST.JK",   # Bekasi Fajar Industrial — ISSI ✓
    "KIJA.JK",   # Kawasan Industri Jababeka — ISSI ✓
    "WSKT.JK",   # Waskita Karya — ISSI ✓ (konstruksi/properti)
    "WIKA.JK",   # Wijaya Karya — ISSI ✓
    "PTPP.JK",   # PP (Pembangunan Perumahan) — ISSI ✓
    "ADHI.JK",   # Adhi Karya — ISSI ✓
    "WEGE.JK",   # Wijaya Karya Bangunan Gedung — ISSI ✓

    # ================= INDUSTRIAL =================
    "ASII.JK",   # Astra International — ISSI ✓
    "UNTR.JK",   # United Tractors — ISSI ✓
    "SMGR.JK",   # Semen Indonesia — ISSI ✓
    "INTP.JK",   # Indocement — ISSI ✓
    "IMPC.JK",   # Impack Pratama Industri — ISSI ✓
    "SMSM.JK",   # Selamat Sempurna — ISSI ✓
    "AUTO.JK",   # Astra Otoparts — ISSI ✓
    "WOOD.JK",   # Integra Indocabinet — ISSI ✓
    "MARK.JK",   # Mark Dynamics — ISSI ✓
    "KBLI.JK",   # KMI Wire and Cable — ISSI ✓
    "SCCO.JK",   # Supreme Cable Manufacturing — ISSI ✓

    # ================= PLANTATION =================
    "LSIP.JK",   # PP London Sumatra — ISSI ✓
    "DSNG.JK",   # Dharma Satya Nusantara — ISSI ✓
    "SIMP.JK",   # Salim Ivomas Pratama — ISSI ✓
    "AALI.JK",   # Astra Agro Lestari — ISSI ✓
    "TBLA.JK",   # Tunas Baru Lampung — ISSI ✓
    "SSMS.JK",   # Sawit Sumbermas Sarana — ISSI ✓

    # ================= MEDIA =================
    "SCMA.JK",   # Surya Citra Media — ISSI ✓
    "EMTK.JK",   # Elang Mahkota Teknologi — ISSI ✓

    # ================= CHEMICAL / PAPER =================
    "TKIM.JK",   # Pabrik Kertas Tjiwi Kimia — ISSI ✓
    "INKP.JK",   # Indah Kiat Pulp & Paper — ISSI ✓

    # ================= LOGISTICS =================
    "ASSA.JK",   # Adi Sarana Armada — ISSI ✓
    "TMAS.JK",   # Pelayaran Tempuran Emas — ISSI ✓
    "SMDR.JK",   # Samudera Indonesia — ISSI ✓
    "BIRD.JK",   # Blue Bird — ISSI ✓
    "TRUK.JK",   # Guna Timur Raya — ISSI ✓

    # ================= TECHNOLOGY =================
    "DCII.JK",   # DCI Indonesia (Data Center) — ISSI ✓
    "MLPT.JK",   # Multipolar Technology — ISSI ✓
    "MTDL.JK",   # Metrodata Electronics — ISSI ✓
]

# =============================================================
# SECTOR MAP (sync dengan ISSI_UNIVERSE)
# =============================================================
SECTOR_MAP = {
    # ENERGY
    "ADRO": "Energy", "ADMR": "Energy", "ITMG": "Energy", "PTBA": "Energy",
    "HRUM": "Energy", "PGAS": "Energy", "MEDC": "Energy", "ELSA": "Energy",
    "PGEO": "Energy", "BUMI": "Energy", "INDY": "Energy", "AKRA": "Energy",

    # MINING
    "ANTM": "Mining", "INCO": "Mining", "MDKA": "Mining", "TINS": "Mining",
    "NCKL": "Mining", "BRMS": "Mining", "HRTA": "Mining",
    "AMMN": "Mining", "MBMA": "Mining",

    # FINANCE SYARIAH
    "BRIS": "Finance", "BPAM": "Finance", "PNLF": "Finance",

    # TELCO
    "TLKM": "Telco", "EXCL": "Telco", "ISAT": "Telco",
    "MTEL": "Telco", "TOWR": "Telco", "TBIG": "Telco",

    # CONSUMER STAPLES
    "ICBP": "Consumer", "INDF": "Consumer", "CPIN": "Consumer", "JPFA": "Consumer",
    "SIDO": "Consumer", "ULTJ": "Consumer", "MYOR": "Consumer", "ROTI": "Consumer",
    "STTP": "Consumer", "CLEO": "Consumer", "GOOD": "Consumer",
    "FOOD": "Consumer", "ICHI": "Consumer",

    # CONSUMER DISCRETIONARY
    "AMRT": "Consumer", "ACES": "Consumer", "ERAA": "Consumer", "MAPI": "Consumer",
    "RALS": "Consumer", "LPPF": "Consumer", "MAPB": "Consumer",

    # HEALTHCARE
    "MIKA": "Healthcare", "HEAL": "Healthcare", "KLBF": "Healthcare",
    "SILO": "Healthcare", "SAME": "Healthcare", "MERK": "Healthcare", "PYFA": "Healthcare",

    # PROPERTY
    "CTRA": "Property", "PWON": "Property", "SMRA": "Property",
    "DMAS": "Property", "BSDE": "Property", "BEST": "Property", "KIJA": "Property",

    # CONSTRUCTION
    "WSKT": "Construction", "WIKA": "Construction", "PTPP": "Construction",
    "ADHI": "Construction", "WEGE": "Construction",

    # INDUSTRIAL
    "ASII": "Industrial", "UNTR": "Industrial", "SMGR": "Industrial",
    "INTP": "Industrial", "IMPC": "Industrial", "SMSM": "Industrial",
    "AUTO": "Industrial", "WOOD": "Industrial", "MARK": "Industrial",
    "KBLI": "Industrial", "SCCO": "Industrial",

    # PLANTATION
    "LSIP": "Plantation", "DSNG": "Plantation", "SIMP": "Plantation",
    "AALI": "Plantation", "TBLA": "Plantation", "SSMS": "Plantation",

    # MEDIA
    "SCMA": "Media", "EMTK": "Media",

    # CHEMICAL
    "TKIM": "Chemical", "INKP": "Chemical",

    # LOGISTICS
    "ASSA": "Logistics", "TMAS": "Logistics", "SMDR": "Logistics",
    "BIRD": "Logistics", "TRUK": "Logistics",

    # TECHNOLOGY
    "DCII": "Technology", "MLPT": "Technology", "MTDL": "Technology",
}

# =============================================================
# HELPER
# =============================================================
def get_sector(ticker: str) -> str:
    symbol = ticker.replace(".JK", "")
    return SECTOR_MAP.get(symbol, "Other")