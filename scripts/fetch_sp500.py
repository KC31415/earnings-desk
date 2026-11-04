"""Weekly: numbers-only data for every S&P 500 company (no AI).

Writes data/sp500/<TICKER>.json: the normalized quarterly table (last 12 quarters), same
structure as earnings_desk.normalize output, so the site renders it with the same code.
Incremental: a company's XBRL facts are re-downloaded only when it has filed a new 10-Q/10-K.

The constituent list comes from the open "datasets/s-and-p-500-companies" CSV on GitHub
(public domain); all financial data comes from the SEC.

    SEC_USER_AGENT="Name email" python -m scripts.fetch_sp500 [--limit N]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys

import requests

from earnings_desk import config
from earnings_desk.normalize import normalize
from earnings_desk.sec import (COMPANYFACTS_URL, SUBMISSIONS_URL, SECClient, SECError, cik10, filings_table,
                               normalize_ticker, ticker_map, trim_companyfacts, trim_submissions)

CONSTITUENTS_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
OUT = config.DATA_DIR / "sp500"
KEEP_QUARTERS = 12


def constituents() -> list[dict]:
    path = OUT / "constituents.csv"
    try:
        r = requests.get(CONSTITUENTS_URL, timeout=30)
        r.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(r.text)))
        if len(rows) > 400:
            OUT.mkdir(parents=True, exist_ok=True)
            path.write_text(r.text)
    except requests.RequestException as e:
        print(f"constituents download failed ({e}); using the saved list", file=sys.stderr)
    with path.open() as f:
        return [{"ticker": normalize_ticker(r["Symbol"]), "name": r.get("Security", ""),
                 "sector": r.get("GICS Sector", "")} for r in csv.DictReader(f)]


def compact(company: dict) -> dict:
    keep = ("value", "concept", "accn", "derived", "derivation", "restated", "original_value", "partial")
    out = {k: v for k, v in company.items() if k != "quarters"}
    out["quarters"] = [dict({k: q[k] for k in q if k != "metrics"},
                            metrics={m: {k: v[k] for k in keep} for m, v in q["metrics"].items()})
                       for q in company["quarters"][-KEEP_QUARTERS:]]
    return out


def latest_periodic(sub: dict) -> str:
    return max((r["filingDate"] for r in filings_table(sub) if r["form"].startswith(("10-Q", "10-K"))), default="")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    client = SECClient()
    tmap = ticker_map(client)
    members = constituents()[: args.limit or None]
    OUT.mkdir(parents=True, exist_ok=True)
    updated = skipped = failed = 0
    for m in members:
        t = m["ticker"]
        path = OUT / f"{t}.json"
        try:
            if t not in tmap:
                raise SECError("ticker not in SEC map")
            cik = tmap[t]["cik"]
            sub = trim_submissions(client.get_json(SUBMISSIONS_URL.format(cik10=cik10(cik))))
            stamp = latest_periodic(sub)
            if path.exists() and json.loads(path.read_text()).get("latest_periodic_filing") == stamp:
                skipped += 1
                continue
            try:
                cf = trim_companyfacts(client.get_json(COMPANYFACTS_URL.format(cik10=cik10(cik))))
            except SECError:
                cf = {"cik": cik, "entityName": tmap[t]["name"], "facts": {}, "_taxonomies": []}
            c = compact(normalize(sub, cf, t))
            c.update(latest_periodic_filing=stamp, sector=m["sector"], index_name=m["name"])
            path.write_text(json.dumps(c, separators=(",", ":")))
            updated += 1
        except Exception as e:
            failed += 1
            print(f"{t}: FAILED {type(e).__name__}: {e}", file=sys.stderr)
    print(f"S&P 500: {updated} updated, {skipped} unchanged, {failed} failed, of {len(members)}")
    return 1 if failed > len(members) // 2 else 0


if __name__ == "__main__":
    sys.exit(main())
