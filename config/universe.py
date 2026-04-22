# =========================
# ISSI STOCK UNIVERSE (CLEAN SYARIAH)
# =========================

ISSI_UNIVERSE = [

# ================= ENERGY =================
"ADRO.JK","ADMR.JK","ITMG.JK","PTBA.JK","HRUM.JK","PGAS.JK","MEDC.JK","ELSA.JK","PGEO.JK",
"BUMI.JK","INDY.JK","AKRA.JK",

# ================= MINING =================
"ANTM.JK","INCO.JK","MDKA.JK","TINS.JK","NCKL.JK","BRMS.JK","HRTA.JK",

# ================= BANK (SYARIAH ONLY) =================
"BRIS.JK","BJBR.JK","BJTM.JK","BBTN.JK",

# ================= TELCO =================
"TLKM.JK","EXCL.JK","ISAT.JK","MTEL.JK","TOWR.JK","TBIG.JK",

# ================= CONSUMER =================
"ICBP.JK","INDF.JK","CPIN.JK","JPFA.JK","SIDO.JK","ULTJ.JK","UNVR.JK",
"HMSP.JK","AMRT.JK","ACES.JK","ERAA.JK","MAPI.JK",
"MYOR.JK","ROTI.JK","STTP.JK","CLEO.JK",
"RALS.JK","LPPF.JK",
"ICHI.JK","GOOD.JK","FOOD.JK",

# ================= HEALTHCARE =================
"MIKA.JK","HEAL.JK","KLBF.JK","SILO.JK","SAME.JK",

# ================= PROPERTY =================
"CTRA.JK","PWON.JK","SMRA.JK","DMAS.JK","BSDE.JK",
"BEST.JK","KIJA.JK",

# ================= INDUSTRIAL =================
"ASII.JK","UNTR.JK","SMGR.JK","INTP.JK","WSKT.JK","WIKA.JK","PTPP.JK",
"IMPC.JK","SMSM.JK","AUTO.JK",
"ADHI.JK","WEGE.JK",

# ================= PLANTATION =================
"LSIP.JK","DSNG.JK","SIMP.JK",

# ================= MEDIA =================
"SCMA.JK","EMTK.JK",

# ================= CHEMICAL =================
"TKIM.JK","INKP.JK",

# ================= LOGISTIC =================
"ASSA.JK","TMAS.JK","SMDR.JK",

# ================= TECHNO =================
"BUKA.JK","GOTO.JK","DCII.JK",

# ================= SMALL MOMENTUM =================
"WOOD.JK","MARK.JK"

]

# =========================
# SECTOR MAP (SYNC)
# =========================

SECTOR_MAP = {

# ENERGY
"ADRO":"Energy","ADMR":"Energy","ITMG":"Energy","PTBA":"Energy","HRUM":"Energy",
"PGAS":"Energy","MEDC":"Energy","ELSA":"Energy","PGEO":"Energy",
"BUMI":"Energy","INDY":"Energy","AKRA":"Energy",

# MINING
"ANTM":"Mining","INCO":"Mining","MDKA":"Mining","TINS":"Mining",
"NCKL":"Mining","BRMS":"Mining","HRTA":"Mining",

# FINANCE (SYARIAH)
"BRIS":"Finance","BJBR":"Finance","BJTM":"Finance","BBTN":"Finance",

# TELCO
"TLKM":"Telco","EXCL":"Telco","ISAT":"Telco","MTEL":"Telco","TOWR":"Telco","TBIG":"Telco",

# CONSUMER
"ICBP":"Consumer","INDF":"Consumer","CPIN":"Consumer","JPFA":"Consumer",
"SIDO":"Consumer","ULTJ":"Consumer","UNVR":"Consumer","HMSP":"Consumer",
"AMRT":"Consumer","ACES":"Consumer","ERAA":"Consumer","MAPI":"Consumer",
"MYOR":"Consumer","ROTI":"Consumer","STTP":"Consumer","CLEO":"Consumer",
"RALS":"Consumer","LPPF":"Consumer",
"ICHI":"Consumer","GOOD":"Consumer","FOOD":"Consumer",

# HEALTHCARE
"MIKA":"Healthcare","HEAL":"Healthcare","KLBF":"Healthcare","SILO":"Healthcare","SAME":"Healthcare",

# PROPERTY
"CTRA":"Property","PWON":"Property","SMRA":"Property","DMAS":"Property","BSDE":"Property",
"BEST":"Property","KIJA":"Property",

# INDUSTRIAL
"ASII":"Industrial","UNTR":"Industrial","SMGR":"Industrial","INTP":"Industrial",
"WSKT":"Industrial","WIKA":"Industrial","PTPP":"Industrial",
"IMPC":"Industrial","SMSM":"Industrial","AUTO":"Industrial",
"ADHI":"Industrial","WEGE":"Industrial",

# PLANTATION
"LSIP":"Plantation","DSNG":"Plantation","SIMP":"Plantation",

# MEDIA
"SCMA":"Media","EMTK":"Media",

# CHEMICAL
"TKIM":"Chemical","INKP":"Chemical",

# LOGISTIC
"ASSA":"Logistics","TMAS":"Logistics","SMDR":"Logistics",

# TECHNO
"BUKA":"Technology","GOTO":"Technology","DCII":"Technology",

# SMALL
"WOOD":"Industrial","MARK":"Industrial"

}

# =========================
# HELPER
# =========================

def get_sector(ticker):
    symbol = ticker.replace(".JK","")
    return SECTOR_MAP.get(symbol, "Other")