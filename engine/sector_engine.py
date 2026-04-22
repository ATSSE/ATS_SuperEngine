import pandas as pd


def sector_momentum(market, sector_map):

    sector_data = {}

    for ticker, df in market.items():

        symbol = ticker.replace(".JK","")

        sector = sector_map.get(symbol)

        if not sector:
            continue

        close = df["Close"]

        momentum = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5]

        sector_data.setdefault(sector, []).append(momentum)

    sector_strength = {}

    for sector, values in sector_data.items():

        sector_strength[sector] = sum(values) / len(values)

    return sector_strength


def strongest_sector(sector_strength):

    if not sector_strength:
        return None

    return max(sector_strength, key=sector_strength.get)


def sector_score_adjustment(sector_strength, sector):

    if sector not in sector_strength:
        return 0

    strength = sector_strength[sector]

    if strength > 0.03:
        return 15

    if strength > 0.015:
        return 10

    if strength > 0:
        return 5

    return 0