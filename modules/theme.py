import streamlit as st


def apply_bmw_theme():

    st.markdown("""
    <style>

    /* ===== BACKGROUND ===== */
    .stApp {
        background: linear-gradient(180deg,#0b0f17 0%,#05070c 100%);
        color:#e6e6e6;
    }

    /* ===== FONT ===== */
    html, body, [class*="css"] {
        font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
    }

    /* ===== HEADER TITLE ===== */
    h1 {
        font-weight:700;
        letter-spacing:0.5px;
        color:#f2f2f2;
    }

    /* ===== TABS ===== */
    button[data-baseweb="tab"]{
        font-size:14px;
        color:#9aa4b2;
        font-weight:500;
        letter-spacing:0.4px;
    }

    button[data-baseweb="tab"][aria-selected="true"]{
        color:#ff3b3b;
        border-bottom:2px solid #ff3b3b;
    }

    /* ===== BUTTON ===== */
    .stButton>button{
        background:linear-gradient(90deg,#007cf0,#00dfd8);
        color:white;
        border-radius:6px;
        border:none;
        padding:8px 18px;
        font-weight:600;
    }

    .stButton>button:hover{
        background:linear-gradient(90deg,#0060c7,#00b7b0);
    }

    /* ===== DATAFRAME ===== */
    .stDataFrame{
        border:1px solid #20242f;
        border-radius:8px;
    }

    /* ===== INPUT ===== */
    .stSelectbox div[data-baseweb="select"]{
        background-color:#121722;
        border-radius:6px;
    }

    /* ===== SEPARATOR ===== */
    hr{
        border:1px solid #1c2130;
    }

    </style>
    """, unsafe_allow_html=True)