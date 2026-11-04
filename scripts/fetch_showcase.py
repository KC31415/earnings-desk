"""Fetch raw SEC data for the showcase tickers and write normalized tables.

Usage:
    SEC_USER_AGENT="Name email@example.com" python -m scripts.fetch_showcase [TICKER ...]
"""
from __future__ import annotations

import json
import sys

from earnings_desk import config
from earnings_desk.normalize import normalize
from earnings_desk.sec import SECClient, fetch_company, load_raw, ticker_map


def main(tickers: list[str]) -> int:
    client = SECClient()
    tmap = ticker_map(client)
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0
    for t in tickers:
        try:
            fetch_company(t, client, tmap=tmap)
            sub, cf = load_raw(t)
            company = normalize(sub, cf, t)
            (config.PROCESSED_DIR / f"{t}.json").write_text(json.dumps(company, indent=1) + "\n")
            q = company["quarters"]
            print(f"{t}: {len(q)} quarters, latest {q[-1]['fiscal_label'] if q else '-'}, coverage={company['coverage']['level']}")
        except Exception as e:  # keep going; report at the end
            failures += 1
            print(f"{t}: FAILED {type(e).__name__}: {e}", file=sys.stderr)
    return 1 if failures == len(tickers) else 0


if __name__ == "__main__":
    sys.exit(main([a.upper() for a in sys.argv[1:]] or config.SHOWCASE_TICKERS))
