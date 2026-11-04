"""Minimal SEC EDGAR client and fetchers.

- Sends the User-Agent the SEC requires (env var SEC_USER_AGENT, "Name email").
- Throttles to stay under the SEC's 10 requests/second limit.
- Retries politely on 429 / 5xx.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

from . import config
from .concepts import all_concepts

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}"

# Forms we keep from the filing list (the rest is noise for this project).
KEEP_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "8-K", "8-K/A", "20-F", "20-F/A", "40-F", "6-K"}


class SECError(RuntimeError):
    pass


class SECClient:
    def __init__(self, user_agent: str | None = None, min_interval: float = config.SEC_MIN_INTERVAL_S,
                 session: requests.Session | None = None):
        ua = user_agent or config.sec_user_agent()
        if not ua or "@" not in ua:
            raise SECError("Set SEC_USER_AGENT to 'Your Name your@email.com' (the SEC requires it).")
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": ua, "Accept-Encoding": "gzip, deflate"})
        self.min_interval = min_interval
        self._last = 0.0

    def _get(self, url: str) -> requests.Response:
        for attempt in range(5):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.session.get(url, timeout=30)
            except requests.RequestException:
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                raise SECError(f"Not found: {url}")
            if r.status_code in (403, 429) or r.status_code >= 500:
                time.sleep(2 ** (attempt + 1))
                continue
            r.raise_for_status()
        raise SECError(f"Gave up after retries: {url}")

    def get_json(self, url: str) -> dict:
        return self._get(url).json()

    def get_text(self, url: str) -> str:
        return self._get(url).text


# ---------------------------------------------------------------- helpers

def cik10(cik: int | str) -> str:
    return str(int(cik)).zfill(10)


def normalize_ticker(t: str) -> str:
    return t.strip().upper().replace(".", "-")


def filing_url(cik: int | str, accn: str, primary_doc: str = "") -> str:
    base = ARCHIVE_BASE.format(cik=int(cik), accn_nodash=accn.replace("-", ""))
    return f"{base}/{primary_doc}" if primary_doc else f"{base}/{accn}-index.htm"


def ticker_map(client: SECClient) -> dict[str, dict]:
    raw = client.get_json(TICKERS_URL)
    return {normalize_ticker(v["ticker"]): {"cik": int(v["cik_str"]), "name": v["title"]} for v in raw.values()}


def filings_table(submissions: dict) -> list[dict]:
    """Columnar `filings.recent` -> list of row dicts."""
    recent = submissions.get("filings", {}).get("recent", {})
    keys = list(recent.keys())
    n = len(recent.get("accessionNumber", []))
    return [{k: recent[k][i] for k in keys} for i in range(n)]


def trim_submissions(sub: dict) -> dict:
    rows = [r for r in filings_table(sub) if r.get("form") in KEEP_FORMS]
    keys = ["accessionNumber", "filingDate", "reportDate", "form", "items", "primaryDocument", "primaryDocDescription"]
    out = {k: v for k, v in sub.items() if k != "filings"}
    out["filings"] = {"recent": {k: [r.get(k, "") for r in rows] for k in keys}}
    return out


def trim_companyfacts(cf: dict) -> dict:
    """Keep only the concepts this project maps (values untouched) plus `dei`."""
    keep = all_concepts()
    facts = cf.get("facts", {})
    out = {k: v for k, v in cf.items() if k != "facts"}
    out["_taxonomies"] = sorted(facts.keys())
    out["facts"] = {
        "dei": facts.get("dei", {}),
        "us-gaap": {c: v for c, v in facts.get("us-gaap", {}).items() if c in keep},
    }
    return out


_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_HREF_RE = re.compile(r'href="([^"]+)"', re.I)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def find_exhibit_991(index_html: str) -> str | None:
    """Return the href of Exhibit 99.1 from an EDGAR filing index page (falls back to any EX-99.x)."""
    candidates: list[tuple[int, str]] = []
    for row in _ROW_RE.findall(index_html):
        cells = [_TAG_RE.sub("", c).strip().upper() for c in _TD_RE.findall(row)]
        href = _HREF_RE.search(row)
        if not href:
            continue
        for c in cells:
            if re.fullmatch(r"EX-99(\.0?1)?", c):
                candidates.append((0, href.group(1)))
            elif c.startswith("EX-99"):
                candidates.append((1, href.group(1)))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    href = candidates[0][1]
    if href.startswith("/ix?doc="):
        href = href[len("/ix?doc="):]
    return href if href.startswith("http") else "https://www.sec.gov" + href


def earnings_8ks(submissions: dict) -> list[dict]:
    """8-K filings that carry Item 2.02 (Results of Operations), newest first."""
    rows = [r for r in filings_table(submissions)
            if r.get("form") in ("8-K", "8-K/A") and "2.02" in str(r.get("items", ""))]
    return sorted(rows, key=lambda r: r["filingDate"], reverse=True)


# ---------------------------------------------------------------- fetch & save

def fetch_company(ticker: str, client: SECClient, out_root: Path = config.RAW_DIR,
                  tmap: dict | None = None, n_press: int = config.PRESS_RELEASES_PER_TICKER) -> Path:
    ticker = normalize_ticker(ticker)
    tmap = tmap or ticker_map(client)
    if ticker not in tmap:
        raise SECError(f"Unknown ticker: {ticker}")
    cik = tmap[ticker]["cik"]
    out = out_root / ticker
    (out / "press").mkdir(parents=True, exist_ok=True)

    sub = trim_submissions(client.get_json(SUBMISSIONS_URL.format(cik10=cik10(cik))))
    _write_json(out / "submissions.json", sub)
    try:
        cf = trim_companyfacts(client.get_json(COMPANYFACTS_URL.format(cik10=cik10(cik))))
    except SECError:
        cf = {"cik": cik, "entityName": tmap[ticker]["name"], "facts": {}, "_taxonomies": []}
    _write_json(out / "companyfacts.json", cf)

    index_path = out / "press" / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    for row in earnings_8ks(sub)[:n_press]:
        accn = row["accessionNumber"]
        if accn in index and (out / "press" / f"{accn}.htm").exists():
            continue
        idx_html = client.get_text(filing_url(cik, accn))
        url = find_exhibit_991(idx_html)
        entry = {"accn": accn, "filing_date": row["filingDate"], "report_date": row.get("reportDate", ""),
                 "items": row.get("items", ""), "filing_index": filing_url(cik, accn), "exhibit_url": url}
        if url:
            (out / "press" / f"{accn}.htm").write_text(client.get_text(url), encoding="utf-8")
        index[accn] = entry
    _write_json(index_path, dict(sorted(index.items(), reverse=True)))
    return out


def fetch_live(ticker: str, client: SECClient, tmap: dict | None = None) -> tuple[dict, dict]:
    """Fetch submissions + companyfacts into memory (for on-demand tickers in the app)."""
    ticker = normalize_ticker(ticker)
    tmap = tmap or ticker_map(client)
    if ticker not in tmap:
        raise SECError(f"Unknown ticker: {ticker}")
    cik = tmap[ticker]["cik"]
    sub = trim_submissions(client.get_json(SUBMISSIONS_URL.format(cik10=cik10(cik))))
    try:
        cf = trim_companyfacts(client.get_json(COMPANYFACTS_URL.format(cik10=cik10(cik))))
    except SECError:
        cf = {"cik": cik, "entityName": tmap[ticker]["name"], "facts": {}, "_taxonomies": []}
    return sub, cf


def load_raw(ticker: str, root: Path = config.RAW_DIR) -> tuple[dict, dict] | None:
    d = root / normalize_ticker(ticker)
    if not (d / "submissions.json").exists() or not (d / "companyfacts.json").exists():
        return None
    return json.loads((d / "submissions.json").read_text()), json.loads((d / "companyfacts.json").read_text())


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1, sort_keys=False) + "\n", encoding="utf-8")
