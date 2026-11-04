"""Verified notes are stored as JSON in the repo: data/notes/<TICKER>/<earnings 8-K accession>.json.

Notes are generated only by GitHub Actions for the showcase tickers, then published with the static site.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config

REPO_NOTES = config.DATA_DIR / "notes"


class NoteStore:
    def __init__(self, repo_dir: Path = REPO_NOTES):
        self.repo_dir = repo_dir

    def _path(self, ticker: str, accn: str) -> Path:
        return self.repo_dir / ticker.upper() / f"{accn}.json"

    def get(self, ticker: str, accn: str) -> dict | None:
        p = self._path(ticker, accn)
        return json.loads(p.read_text()) if p.exists() else None

    def put(self, ticker: str, accn: str, note: dict) -> None:
        p = self._path(ticker, accn)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(note, indent=1) + "\n")
