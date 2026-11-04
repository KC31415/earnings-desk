"""Static site builder: page contexts and a small end-to-end build (no network, no AI)."""
import json
from pathlib import Path

import pytest

from earnings_desk.normalize import normalize
from scripts.fetch_sp500 import compact
from sitegen.build import company_context, note_context
from tests.synthetic import JAN, make_company
from tests.test_verify import _fake_llm_factory, crm  # noqa: F401  (fixture)

SAMPLES = Path(__file__).parent / "samples"


def test_company_context_on_compact_sample():
    sub = json.loads((SAMPLES / "MSFT_submissions.json").read_text())
    cf = json.loads((SAMPLES / "MSFT_companyfacts.json").read_text())
    c = compact(normalize(sub, cf, "MSFT"))
    assert len(c["quarters"]) == 12
    ctx = company_context(c)
    assert len(ctx["cols"]) == 8 and ctx["metric_rows"][0]["label"] == "Revenue"
    assert {"revenue", "margins"} <= set(ctx["charts"])
    assert ctx["sources"][0]["url"].startswith("https://www.sec.gov/Archives/")


def test_blocked_filer_gets_message_not_numbers():
    sub, cf = make_company(*JAN)
    c = normalize(dict(sub, sic="6022", sicDescription="State commercial banks"), cf, "BANK")
    ctx = company_context(c)
    assert ctx["coverage"]["blocked"] and "metric_rows" not in ctx


def test_note_context_tags_and_sources(crm):
    from earnings_desk.note import generate_note

    c, ps = crm
    fake, _ = _fake_llm_factory(c, ps)
    note = generate_note(c, len(c["quarters"]) - 1, ps, {"earnings_8k_accn": "x", "filing_index": "https://x"}, llm=fake)
    note["paragraphs"] = ps
    ctx = note_context(note, c["cik"])
    claims = [cl for s in ctx["sections"] for cl in s["claims"]]
    assert [cl["icon"] for cl in claims] == ["✓", "⚠", "◦"]
    assert claims[1]["real"].startswith("$") and claims[0]["inputs"][0]["url"].startswith("https://www.sec.gov/")
    assert claims[2]["para_id"] == "P5" and claims[2]["para"]
