import pandas as pd


def runner_prediction(df):

    close = df["Close"]
    vol = df["Volume"]

    last = close.iloc[-1]

    ma20 = close.tail(20).mean()
    ma50 = close.tail(50).mean()

    avg_vol = vol.tail(20).mean()
    last_vol = vol.iloc[-1]

    score = 0

    # trend strength
    if last > ma20 > ma50:
        score += 30

    # momentum
    momentum = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5]

    if momentum > 0.03:
        score += 30
    elif momentum > 0.02:
        score += 20

    # volume expansion
    if last_vol > avg_vol * 1.5:
        score += 20

    # compression (runner preparation)
    range5 = close.tail(5).max() - close.tail(5).min()

    if range5 / last < 0.02:
        score += 20

    return min(score,100)