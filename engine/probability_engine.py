import pandas as pd
# [FIX] Hapus duplikat liquidity_trap() — gunakan single source of truth dari liquidity_engine
# Sebelumnya: dua implementasi berjalan independen (probability_engine vs liquidity_engine)
# Sekarang: satu implementasi, konsisten di seluruh pipeline
from engine.liquidity_engine import liquidity_trap


def runner_probability(df):

    close = df["Close"]
    high = df["High"]
    vol = df["Volume"]

    last = close.iloc[-1]
    prev = close.iloc[-2]

    momentum = (last - prev) / prev

    avg_vol = vol.tail(20).mean()
    last_vol = vol.iloc[-1]

    prob = 20

    # momentum

    if momentum > 0.03:
        prob += 25
    elif momentum > 0.02:
        prob += 18
    elif momentum > 0.01:
        prob += 10

    # momentum acceleration

    m1 = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]
    m2 = (close.iloc[-2] - close.iloc[-3]) / close.iloc[-3]
    m3 = (close.iloc[-3] - close.iloc[-4]) / close.iloc[-4]

    if m1 > m2 and m2 > m3:
        prob += 15

    # volume expansion

    if last_vol > avg_vol * 2:
        prob += 25
    elif last_vol > avg_vol * 1.5:
        prob += 15

    # breakout pressure

    prior_high20 = high.iloc[:-1].tail(20).max()
    if last >= prior_high20:
        prob += 20

    # liquidity trap filter

    if liquidity_trap(df):
        prob -= 25

    return max(min(prob, 100), 0)