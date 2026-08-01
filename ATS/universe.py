# -*- coding: utf-8 -*-
"""
ISSI UNIVERSE - PREMIUM LIQUID (High Liquidity Only)
Hanya saham syariah dengan likuiditas tinggi, spread tipis, dan aman untuk trading.
Sudah dicoret: DVLA, PYFA, SFGH, SLSA, dan saham illiquid lainnya.
"""

# ═══════════════════════════════════════════════════════════════
# DAFTAR SAHAM ISSI PREMIUM (LIKUID & AMAN)
# ═══════════════════════════════════════════════════════════════

ISSI_TICKERS = [
    # BANKING SYARIAH (1)
    "BRIS.JK",
    
    # CONSUMER & HEALTHCARE (8) - Hanya yang likuid
    "ICBP.JK", "INDF.JK", "UNVR.JK", "MYOR.JK", "KLBF.JK",
    "SIDO.JK", "ULTJ.JK", "JPFA.JK",
    
    # MINING & ENERGY (8)
    "ANTM.JK", "MDKA.JK", "INCO.JK", "TINS.JK", "ITMG.JK",
    "PGAS.JK", "AKRA.JK", "MEDC.JK",
    
    # PROPERTY & REAL ESTATE (5)
    "BSDE.JK", "CTRA.JK", "PWON.JK", "SMRA.JK", "DUTI.JK",
    
    # TELECOM & INFRASTRUCTURE (8)
    "TLKM.JK", "EXCL.JK", "ISAT.JK", "TOWR.JK", "MTEL.JK",
    "LINK.JK", "JSMR.JK", "WIKA.JK",
    
    # INDUSTRIAL & MATERIALS (12)
    "ASII.JK", "UNTR.JK", "SMGR.JK", "INTP.JK", "TPIA.JK",
    "BRPT.JK", "AALI.JK", "LSIP.JK", "DSNG.JK", "SIMP.JK",
    "TBLA.JK", "TKIM.JK",
    
    # RETAIL & SERVICES (5)
    "AMRT.JK", "ERAA.JK", "ACES.JK", "MAPI.JK", "TSPC.JK",
]

# ═══════════════════════════════════════════════════════════════
# SECTOR MAP
# ═══════════════════════════════════════════════════════════════

ISSI_SECTOR_MAP = {
    "BRIS": "Banking Syariah",
    "ICBP": "Consumer", "INDF": "Consumer", "UNVR": "Consumer",
    "MYOR": "Consumer", "KLBF": "Healthcare", "SIDO": "Healthcare",
    "ULTJ": "Consumer", "JPFA": "Consumer",
    "ANTM": "Mining", "MDKA": "Mining", "INCO": "Mining",
    "TINS": "Mining", "ITMG": "Mining", "PGAS": "Energy",
    "AKRA": "Energy", "MEDC": "Energy",
    "BSDE": "Property", "CTRA": "Property", "PWON": "Property",
    "SMRA": "Property", "DUTI": "Property",
    "TLKM": "Telecom", "EXCL": "Telecom", "ISAT": "Telecom",
    "TOWR": "Telecom", "MTEL": "Telecom", "LINK": "Telecom",
    "JSMR": "Infrastructure", "WIKA": "Construction",
    "ASII": "Industrial", "UNTR": "Industrial", "SMGR": "Cement",
    "INTP": "Cement", "TPIA": "Chemical", "BRPT": "Chemical",
    "AALI": "Plantation", "LSIP": "Plantation", "DSNG": "Plantation",
    "SIMP": "Plantation", "TBLA": "Plantation", "TKIM": "Paper",
    "AMRT": "Retail", "ERAA": "Retail", "ACES": "Retail",
    "MAPI": "Retail", "TSPC": "Healthcare",
}

print(f"✅ ISSI Premium Universe loaded: {len(ISSI_TICKERS)} liquid stocks")
print(f"✅ Illicit/Illiquid stocks (DVLA, PYFA, etc.) REMOVED")