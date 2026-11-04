"""Load a company: committed raw files first, live SEC fetch otherwise."""
from __future__ import annotations

from . import config
from .normalize import normalize
from .sec import SECClient, fetch_live, load_raw, normalize_ticker, ticker_map

_TMAP: dict | None = None


def load_company(ticker: str) -> tuple[dict, str]:
    """Return (normalized company, source) where source is 'repo' or 'live'."""
    global _TMAP
    t = normalize_ticker(ticker)
    raw = load_raw(t, config.RAW_DIR)
    if raw:
        return normalize(*raw, t), "repo"
    client = SECClient()
    if _TMAP is None:
        _TMAP = ticker_map(client)
    sub, cf = fetch_live(t, client, _TMAP)
    return normalize(sub, cf, t), "live"
