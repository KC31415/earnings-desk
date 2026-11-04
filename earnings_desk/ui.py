"""Shared Streamlit widgets."""
from __future__ import annotations

import streamlit as st

from . import config


def ticker_picker(prefix: str, default: str = "CRM") -> str:
    """Ticker text box + showcase buttons that stay in sync.

    Clicking a showcase button writes its ticker into the text box; typing in the box clears the button.
    The choice is mirrored in the URL (?ticker=...) so links are shareable.
    """
    box, pill = f"{prefix}_ticker", f"{prefix}_pill"
    if box not in st.session_state:
        st.session_state[box] = (st.query_params.get("ticker") or default).strip().upper()

    def on_pill():
        if st.session_state.get(pill):
            st.session_state[box] = st.session_state[pill]

    def on_box():
        st.session_state[box] = st.session_state[box].strip().upper()
        if st.session_state.get(pill) != st.session_state[box]:
            st.session_state[pill] = None

    c1, c2 = st.columns([1, 3])
    c1.text_input("Ticker", key=box, on_change=on_box)
    c2.pills("Showcase", config.SHOWCASE_TICKERS, selection_mode="single", key=pill, on_change=on_pill)
    ticker = st.session_state[box].strip().upper()
    if ticker:
        st.query_params["ticker"] = ticker
    return ticker
