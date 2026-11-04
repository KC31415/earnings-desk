"""Compute the accuracy scorecard from stored notes (no API calls) and write the hand-grade sample.

    python -m scripts.scorecard
"""
from __future__ import annotations

import json

from earnings_desk.scorecard import OUT_DIR, claims_frame, compute, load_notes, write_hand_grade_sample


def main() -> None:
    notes = load_notes()
    res = compute(notes)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "results.json").write_text(json.dumps(res, indent=1, default=str) + "\n")
    claims_frame(notes).to_csv(OUT_DIR / "claims.csv", index=False)
    wrote = write_hand_grade_sample(claims_frame(notes))
    print(f"{res['company_quarters']} company-quarters, {res['claims']} claims; numeric verified "
          f"{res['numeric']['verified_pct']}%, quotes matched {res['quote']['match_pct']}%")
    for r in res["injection"]:
        print(f"  injected {r['error_type']}: {r['caught']}/{r['injected']} caught")
    print("hand-grade CSV", "written" if wrote else "kept (already has grades)")


if __name__ == "__main__":
    main()
