import pandas as pd

def sector_leader(sector_power):

    df = pd.DataFrame(
        sector_power.items(),
        columns=["Sector", "Strength"]
    )

    df = df.sort_values("Strength", ascending=False)

    leader = df.iloc[0]["Sector"]
    weakest = df.iloc[-1]["Sector"]

    return leader, weakest, df