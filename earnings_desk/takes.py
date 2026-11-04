"""Optional "Analyst's take" boxes, written by hand in takes/<TICKER>.md."""
from __future__ import annotations

import re

from . import config

TAKES_DIR = config.ROOT / "takes"


def load_take(ticker: str) -> str | None:
    p = TAKES_DIR / f"{ticker.upper()}.md"
    if not p.exists():
        return None
    text = re.sub(r"<!--.*?-->", "", p.read_text(encoding="utf-8"), flags=re.S).strip()
    return text or None
