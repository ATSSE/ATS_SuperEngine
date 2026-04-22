import pandas as pd

def pullback_quality(df):

    close = df["Close"]
    vol = df["Volume"]

    last = close.iloc[-1]

    ma20 = close.tail(20).mean()

    # trend check
    if last < ma20:
        return "WEAK"

    # volume check
    up_vol = vol.iloc[-5:].mean()
    down_vol = vol.iloc[-10:-5].mean()

    # momentum check
    recent_high = close.tail(10).max()

    pullback_pct = (recent_high - last) / recent_high

    if pullback_pct < 0.02:
        return "STRONG"

    if pullback_pct < 0.05 and up_vol >= down_vol:
        return "HEALTHY"

    return "WEAK"