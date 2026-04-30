import pandas as pd


def liquidity_trap(df):

    close = df["Close"]
    low = df["Low"]
    vol = df["Volume"]

    last_close = close.iloc[-1]
    prev_close = close.iloc[-2]

    last_low = low.iloc[-1]
    low5 = low.iloc[:-1].tail(5).min()

    avg_vol = vol.tail(20).mean()
    last_vol = vol.iloc[-1]

    # kondisi stop hunt
    if last_low < low5 and last_close > prev_close and last_vol > avg_vol * 1.5:
        return True

    return False


def fake_breakout(df):

    close = df["Close"]
    high = df["High"]

    last_close = close.iloc[-1]
    prev_close = close.iloc[-2]

    high5 = high.iloc[-6:-1].max()

    # breakout gagal
    if last_close < high5 and prev_close >= high5:
        return True

    return False


def liquidity_score_adjustment(df):

    score = 0

    if liquidity_trap(df):
        score -= 25

    if fake_breakout(df):
        score -= 15

    return score
