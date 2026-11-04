"""Note storage and the daily cap on new on-demand notes.

Lookup order for a note keyed by (ticker, accession):
  1. data/notes/<TICKER>/<accn>.json   committed to the repo (showcase notes from GitHub Actions)
  2. Upstash Redis                     if UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN are set
  3. cache/notes/<TICKER>/<accn>.json  local file cache otherwise
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import config

REPO_NOTES = config.DATA_DIR / "notes"
LOCAL_CACHE = config.ROOT / "cache"


def _secret(name: str) -> str | None:
    v = os.environ.get(name)
    if v:
        return v
    try:  # pragma: no cover
        import streamlit as st

        return st.secrets.get(name)
    except Exception:
        return None


def daily_cap() -> int:
    try:
        return int(_secret("NOTE_DAILY_CAP") or 20)
    except ValueError:
        return 20


class _Upstash:
    def __init__(self, url: str, token: str):
        self.url, self.headers = url.rstrip("/"), {"Authorization": f"Bearer {token}"}

    def cmd(self, *args):
        r = requests.post(self.url, headers=self.headers, json=list(args), timeout=10)
        r.raise_for_status()
        return r.json().get("result")


class NoteStore:
    def __init__(self, repo_dir: Path = REPO_NOTES, local_dir: Path = LOCAL_CACHE, use_redis: bool = True):
        self.repo_dir, self.local_dir = repo_dir, local_dir
        url, token = _secret("UPSTASH_REDIS_REST_URL"), _secret("UPSTASH_REDIS_REST_TOKEN")
        self.redis = _Upstash(url, token) if (use_redis and url and token) else None

    @property
    def backend(self) -> str:
        return "upstash" if self.redis else "local file"

    @staticmethod
    def _key(ticker: str, accn: str) -> str:
        return f"earnings-desk:note:{ticker.upper()}:{accn}"

    def get(self, ticker: str, accn: str) -> dict | None:
        p = self.repo_dir / ticker.upper() / f"{accn}.json"
        if p.exists():
            return json.loads(p.read_text())
        if self.redis:
            v = self.redis.cmd("GET", self._key(ticker, accn))
            return json.loads(v) if v else None
        p = self.local_dir / "notes" / ticker.upper() / f"{accn}.json"
        return json.loads(p.read_text()) if p.exists() else None

    def put(self, ticker: str, accn: str, note: dict, repo: bool = False) -> None:
        blob = json.dumps(note, indent=1)
        if repo:
            p = self.repo_dir / ticker.upper() / f"{accn}.json"
        elif self.redis:
            self.redis.cmd("SET", self._key(ticker, accn), blob)
            return
        else:
            p = self.local_dir / "notes" / ticker.upper() / f"{accn}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(blob + "\n")

    # ---- daily cap (UTC days)
    def _day_key(self) -> str:
        return "earnings-desk:count:" + datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def used_today(self) -> int:
        if self.redis:
            return int(self.redis.cmd("GET", self._day_key()) or 0)
        p = self.local_dir / "daily_count.json"
        d = json.loads(p.read_text()) if p.exists() else {}
        return int(d.get(self._day_key(), 0))

    def take_slot(self) -> bool:
        """Reserve one on-demand generation. False when today's cap is reached."""
        cap = daily_cap()
        if self.redis:
            n = int(self.redis.cmd("INCR", self._day_key()))
            if n == 1:
                self.redis.cmd("EXPIRE", self._day_key(), 172800)
            return n <= cap
        p = self.local_dir / "daily_count.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        d = json.loads(p.read_text()) if p.exists() else {}
        n = int(d.get(self._day_key(), 0)) + 1
        if n > cap:
            return False
        p.write_text(json.dumps({self._day_key(): n}))
        return True
