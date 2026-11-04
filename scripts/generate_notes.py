"""Generate verified notes for showcase tickers whose latest earnings release has no note yet.

Calls the Anthropic API once or twice per missing note (never in a loop over retries).
    ANTHROPIC_API_KEY=... python -m scripts.generate_notes [--max 10] [TICKER ...]
"""
from __future__ import annotations

import argparse
import sys

from earnings_desk import config
from earnings_desk.normalize import normalize
from earnings_desk.notes_service import create_note, get_note, note_quarters
from earnings_desk.sec import load_raw
from earnings_desk.store import NoteStore

# Approximate list prices, $ per million tokens (input, output), for the cost printout only.
PRICES = {"claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--max", type=int, default=10, help="max notes to generate in this run")
    args = ap.parse_args()
    store = NoteStore()
    made, cost, failures = 0, 0.0, 0
    for t in [x.upper() for x in args.tickers] or config.SHOWCASE_TICKERS:
        raw = load_raw(t)
        if not raw:
            print(f"{t}: no raw data, skipped")
            continue
        company = normalize(*raw, t)
        if company["coverage"]["blocked"] or not note_quarters(company, 1):
            print(f"{t}: not eligible")
            continue
        i = note_quarters(company, 1)[0]
        if get_note(company, i, store):
            print(f"{t}: note exists for {company['quarters'][i]['fiscal_label']}")
            continue
        if made >= args.max:
            print(f"{t}: skipped (--max {args.max} reached)")
            continue
        try:
            note = create_note(company, i, store=store)
        except Exception as e:
            failures += 1
            print(f"{t}: FAILED {type(e).__name__}: {e}", file=sys.stderr)
            continue
        made += 1
        for u in note["usage"]:
            pin, pout = PRICES.get(u["model"], PRICES["claude-sonnet-5-5"])
            cost += u["input_tokens"] / 1e6 * pin + u["output_tokens"] / 1e6 * pout
        s = note["summary"]
        print(f"{t} {note['fiscal_label']}: {s['claims']} claims, {s['by_status']}")
    print(f"Generated {made} notes, approx cost ${cost:.2f}")
    return 1 if failures and not made else 0


if __name__ == "__main__":
    sys.exit(main())
