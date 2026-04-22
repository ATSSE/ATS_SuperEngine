import pandas as pd


def market_breadth(market):

    total_up = 0
    total_down = 0

    for ticker, df in market.items():

        close = df["Close"]

        last = close.iloc[-1]
        prev = close.iloc[-2]

        if last > prev:
            total_up += 1
        else:
            total_down += 1

    return total_up, total_down


def detect_market_regime(market):

    total_up, total_down = market_breadth(market)

    if total_up > total_down * 1.5:
        return "BULLISH"

    if total_down > total_up * 1.5:
        return "DISTRIBUTION"

    return "SIDEWAYS"


def regime_score_adjustment(regime):

    if regime == "BULLISH":
        return 10

    if regime == "SIDEWAYS":
        return 0

    if regime == "DISTRIBUTION":
        return -15

    return 0