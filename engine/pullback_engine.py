import pandas as pd


def pullback_zone(df):

    close = df["Close"]

    last = close.iloc[-1]

    ma10 = close.tail(10).mean()
    ma20 = close.tail(20).mean()

    zone_top = ma10
    zone_bottom = ma20

    return zone_top, zone_bottom


def pullback_signal(price, zone_top, zone_bottom):

    if zone_bottom <= price <= zone_top:
        return "PULLBACK ENTRY"

    if price < zone_bottom:
        return "DEEP PULLBACK"

    return "WAIT"