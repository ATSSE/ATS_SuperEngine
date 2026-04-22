import pandas as pd
from datetime import datetime


# =========================
# CREATE JOURNAL
# =========================

def create_journal():

    df = pd.DataFrame(
        columns=["Tanggal","Ticker","Entry","Lot","Value"]
    )

    return df


# =========================
# ADD TRADE
# =========================

def add_trade(journal, ticker, entry, lot):

    exists = (
        (journal["Ticker"] == ticker) &
        (journal["Entry"] == entry)
    )

    if exists.any():
        return journal

    new_trade = pd.DataFrame([{

        "Tanggal": datetime.now().strftime("%Y-%m-%d"),
        "Ticker": ticker,
        "Entry": entry,
        "Lot": lot,
        "Value": None

    }])

    journal = pd.concat([journal, new_trade], ignore_index=True)

    return journal


# =========================
# DELETE TRADE
# =========================

def delete_trade(journal, ticker, entry):

    journal = journal[
        ~(
            (journal["Ticker"] == ticker) &
            (journal["Entry"] == entry)
        )
    ]

    return journal


# =========================
# CLEAR JOURNAL
# =========================

def clear_journal():

    return create_journal()


# =========================
# JOURNAL STATS
# =========================

def journal_stats(journal):

    total_trade = len(journal)

    return {

        "total_trade": total_trade

    }