"""Copy real SEC data for 3 tickers (different fiscal calendars) into tests/samples/.

Run after the fetch workflow has committed data/raw/. Values are copied verbatim;
only facts ending before CUTOFF are dropped to keep the samples small.

    python -m scripts.make_test_samples
"""
from __future__ import annotations

import json

from earnings_desk import config
from earnings_desk.sec import load_raw

SAMPLE_TICKERS = ["MSFT", "CRM", "ADBE"]  # June, January, 52/53-week November year ends
CUTOFF = "2021-01-01"
OUT = config.ROOT / "tests" / "samples"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for t in SAMPLE_TICKERS:
        raw = load_raw(t)
        if not raw:
            raise SystemExit(f"{t}: no data/raw/{t}; run the fetch workflow first")
        sub, cf = raw
        for concept in cf["facts"].get("us-gaap", {}).values():
            for unit, facts in concept["units"].items():
                concept["units"][unit] = [f for f in facts if f["end"] >= CUTOFF]
        cf["facts"]["dei"] = {}
        (OUT / f"{t}_companyfacts.json").write_text(json.dumps(cf, separators=(",", ":")))
        (OUT / f"{t}_submissions.json").write_text(json.dumps(sub, separators=(",", ":")))
        print(f"{t}: written")


if __name__ == "__main__":
    main()
