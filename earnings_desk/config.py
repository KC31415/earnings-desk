"""Project-wide settings. Everything tunable lives here or in env vars."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

SHOWCASE_TICKERS = ["CRM", "NOW", "ADBE", "SNOW", "DDOG", "NVDA", "MSFT", "PLTR", "AI", "NDAQ"]

# Models (used from Milestone 2 on).
EXTRACTION_MODEL = os.environ.get("EXTRACTION_MODEL", "claude-haiku-5-5")
NOTE_MODEL = os.environ.get("NOTE_MODEL", "claude-sonnet-5-5")

# SEC access
SEC_MIN_INTERVAL_S = 0.12  # ~8 requests/second, under the SEC's 10/s limit
PRESS_RELEASES_PER_TICKER = 6  # last N earnings 8-K Exhibit 99.1s to keep on disk

QUARTERS_SHOWN = 8


def sec_user_agent() -> str | None:
    """SEC_USER_AGENT from env, or from Streamlit secrets when running in Streamlit."""
    ua = os.environ.get("SEC_USER_AGENT")
    if ua:
        return ua
    try:  # pragma: no cover - only inside Streamlit
        import streamlit as st

        return st.secrets.get("SEC_USER_AGENT")
    except Exception:
        return None
