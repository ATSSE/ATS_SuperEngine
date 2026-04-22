import pandas as pd


# =========================
# SECTOR HEATMAP
# =========================

def sector_heatmap(market, sector_map):

    sector_perf = {}

    for ticker, df in market.items():

        symbol = ticker.replace(".JK","")

        sector = sector_map.get(symbol)

        if not sector:
            continue

        close = df["Close"]

        change = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2]

        sector_perf.setdefault(sector, []).append(change)

    heatmap = {}

    for sector, values in sector_perf.items():

        heatmap[sector] = sum(values) / len(values)

    return heatmap


# =========================
# MARKET BREADTH
# =========================

def market_breadth(market):

    up = 0
    down = 0

    for ticker, df in market.items():

        close = df["Close"]

        if close.iloc[-1] > close.iloc[-2]:
            up += 1
        else:
            down += 1

    return up, down


# =========================
# MARKET SENTIMENT
# =========================

def market_sentiment(up, down):

    if up > down * 1.5:
        return "RISK ON"

    if down > up * 1.5:
        return "RISK OFF"

    return "NEUTRAL"