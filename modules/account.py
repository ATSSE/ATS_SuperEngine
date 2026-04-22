# =========================
# ACCOUNT MANAGEMENT
# =========================

def risk_per_trade(balance, risk_percent=2):
    """
    Menghitung jumlah uang yang siap dirisikokan per trade
    """

    risk_value = balance * (risk_percent / 100)

    return risk_value


# =========================
# LOT CALCULATOR
# =========================

def lot_size(entry, stop_loss, balance, risk_percent=2):
    """
    Menghitung jumlah lot berdasarkan risk management
    """

    risk_value = risk_per_trade(balance, risk_percent)

    risk_per_share = abs(entry - stop_loss)

    if risk_per_share == 0:
        return 1

    lot = risk_value / (risk_per_share * 100)

    return max(1, int(lot))


# =========================
# POSITION VALUE
# =========================

def position_value(entry, lot):
    """
    Menghitung nilai transaksi
    """

    return entry * lot * 100


# =========================
# RISK REWARD
# =========================

def risk_reward(entry, stop_loss, target):

    risk = entry - stop_loss

    reward = target - entry

    if risk == 0:
        return 0

    rr = reward / risk

    return round(rr, 2)