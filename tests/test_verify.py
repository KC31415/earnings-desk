"""Verifier + note pipeline tests on real CRM samples. The LLM is replaced by canned outputs.

Claim values are computed from the sample data at test time, never hand-typed.
"""
import json
from pathlib import Path

import pytest

from earnings_desk.note import SECTION_TITLES, generate_note, metrics_block
from earnings_desk.normalize import normalize
from earnings_desk.store import NoteStore
from earnings_desk.verify import actual_value, numbers_in, verify_claim

SAMPLES = Path(__file__).parent / "samples"


@pytest.fixture(scope="module")
def crm():
    if not (SAMPLES / "CRM_companyfacts.json").exists():
        pytest.skip("no CRM sample")
    c = normalize(json.loads((SAMPLES / "CRM_submissions.json").read_text()),
                  json.loads((SAMPLES / "CRM_companyfacts.json").read_text()), "CRM")
    press = json.loads((SAMPLES / "CRM_press_paragraphs.json").read_text())
    return c, press["paragraphs"]


def paras_of(ps):
    return {p["id"]: p["text"] for p in ps}


def numeric(metric, period, value, unit, text=None, ref="RevenueFromContractWithCustomerExcludingAssessedTax"):
    return {"text": text or f"Metric was {value} in {period}.", "claim_type": "numeric", "metric": metric,
            "period": period, "value": value, "unit": unit, "source": {"type": "xbrl", "ref": ref}}


def status(claim, crm):
    c, ps = crm
    return verify_claim(claim, c, paras_of(ps))["verification"]


def test_revenue_in_billions_verifies(crm):
    c, _ = crm
    q = c["quarters"][-1]
    rev = q["metrics"]["revenue"]["value"]
    v = status(numeric("revenue", q["fiscal_label"], f"{rev / 1e9:.2f}", "usd_billions"), crm)
    assert v["status"] == "verified" and v["method"] == "xbrl"
    v = status(numeric("revenue", q["fiscal_label"], f"{rev / 1e9:.1f}", "usd_billions"), crm)
    assert v["status"] == "verified"


@pytest.mark.parametrize("perturb", ["plus5", "minus5", "wrong_unit", "wrong_period"])
def test_injected_errors_are_caught(crm, perturb):
    c, _ = crm
    q, prev = c["quarters"][-1], c["quarters"][-2]
    rev = q["metrics"]["revenue"]["value"]
    period, value, unit = q["fiscal_label"], f"{rev / 1e9:.2f}", "usd_billions"
    if perturb == "plus5":
        value = f"{rev * 1.05 / 1e9:.2f}"
    elif perturb == "minus5":
        value = f"{rev * 0.95 / 1e9:.2f}"
    elif perturb == "wrong_unit":
        unit = "usd_millions"
    elif perturb == "wrong_period":
        period = prev["fiscal_label"]
    assert status(numeric("revenue", period, value, unit), crm)["status"] == "mismatch"


def test_growth_and_swapped_qoq_yoy(crm):
    c, _ = crm
    label = c["quarters"][-1]["fiscal_label"]
    yoy, _, _ = actual_value(c, "revenue_yoy", label)
    qoq, _, _ = actual_value(c, "revenue_qoq", label)
    assert status(numeric("revenue_yoy", label, f"{yoy:.1f}", "percent"), crm)["status"] == "verified"
    assert abs(yoy - qoq) > 1
    assert status(numeric("revenue_qoq", label, f"{yoy:.1f}", "percent"), crm)["status"] == "mismatch"


def test_sentence_must_contain_value(crm):
    c, _ = crm
    q = c["quarters"][-1]
    rev = q["metrics"]["revenue"]["value"]
    claim = numeric("revenue", q["fiscal_label"], f"{rev / 1e9:.2f}", "usd_billions", text="Revenue was $99.99 billion.")
    v = status(claim, crm)
    assert v["status"] == "mismatch" and "sentence" in v["reason"]


def test_press_release_number_for_non_xbrl_metric(crm):
    _, ps = crm
    p = next(p for p in ps if p["kind"] == "text" and numbers_in(p["text"]))
    n = numbers_in(p["text"])[0]
    s = f"{n:g}"
    claim = {"text": f"A figure of {s} was reported.", "claim_type": "numeric", "metric": "other", "period": "",
             "value": s, "unit": "count", "source": {"type": "press_release", "ref": p["id"]}}
    assert status(claim, crm)["status"] == "verified"
    claim["value"] = claim["text"] = "123456.789"
    assert status(claim, crm)["status"] == "mismatch"


def test_quotes(crm):
    _, ps = crm
    p = max((p for p in ps if p["kind"] == "text"), key=lambda p: len(p["text"]))
    words = p["text"].split()
    quote = " ".join(words[3:15])
    claim = {"text": f'Management said "{quote}".', "claim_type": "quote", "metric": "", "period": "",
             "value": quote, "unit": "", "source": {"type": "press_release", "ref": p["id"]}}
    assert status(claim, crm)["status"] == "verified"
    other = next(x for x in ps if x["id"] != p["id"] and quote not in x["text"])
    v = status(dict(claim, source={"type": "press_release", "ref": other["id"]}), crm)
    assert v["status"] == "mismatch" and p["id"] in v["reason"]
    altered = quote.replace(words[5], words[5] + "x", 1)
    assert status(dict(claim, value=altered), crm)["status"] == "mismatch"


def test_qualitative(crm):
    _, ps = crm
    q = {"text": "Management highlighted AI demand.", "claim_type": "qualitative", "metric": "", "period": "",
         "value": "", "unit": "", "source": {"type": "press_release", "ref": "P5"}}
    assert status(q, crm)["status"] == "sourced"
    assert status(dict(q, source={"type": "press_release", "ref": "P9999"}), crm)["status"] == "unsupported"
    assert status(dict(q, text="Management expects 987654 new customers."), crm)["status"] == "unsupported"


def _fake_llm_factory(c, ps):
    calls = []

    def fake(model, system, user, schema, effort):
        calls.append(model)
        if "paragraph_ids" in schema["properties"]:
            return {"paragraph_ids": [p["id"] for p in ps[:40]]}, {"model": model, "input_tokens": 1, "output_tokens": 1}
        q = c["quarters"][-1]
        rev = q["metrics"]["revenue"]["value"]
        good = numeric("revenue", q["fiscal_label"], f"{rev / 1e9:.2f}", "usd_billions")
        bad = numeric("revenue", q["fiscal_label"], f"{rev * 1.05 / 1e9:.2f}", "usd_billions")
        qual = {"text": "Demand commentary.", "claim_type": "qualitative", "metric": "", "period": "", "value": "",
                "unit": "", "source": {"type": "press_release", "ref": "P5"}}
        return {"sections": [{"title": SECTION_TITLES[0], "claims": [good, bad]},
                             {"title": SECTION_TITLES[2], "claims": [qual]}]}, {"model": model, "input_tokens": 1, "output_tokens": 1}

    return fake, calls


def test_pipeline_end_to_end_keeps_failed_claims(crm):
    c, ps = crm
    fake, calls = _fake_llm_factory(c, ps)
    note = generate_note(c, len(c["quarters"]) - 1, ps, {"earnings_8k_accn": "x"}, llm=fake)
    assert len(calls) == 2  # paragraph selection (>60 paragraphs) + note
    statuses = [cl["verification"]["status"] for s in note["sections"] for cl in s["claims"]]
    assert statuses == ["verified", "mismatch", "sourced"]  # failed claim kept, not dropped or fixed
    assert note["summary"]["numeric"] == 2 and note["summary"]["numeric_verified"] == 1


def test_metrics_block_has_three_periods(crm):
    c, _ = crm
    mb = metrics_block(c, len(c["quarters"]) - 1)
    assert set(mb["periods"]) == {"current", "prior_quarter", "year_ago_quarter"}
    assert "revenue_yoy" in mb["computed_percent_for_current_quarter"]


def test_store_and_daily_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTE_DAILY_CAP", "2")
    s = NoteStore(repo_dir=tmp_path / "repo", local_dir=tmp_path / "cache", use_redis=False)
    assert s.get("ABC", "1") is None
    s.put("ABC", "1", {"x": 1})
    assert s.get("ABC", "1") == {"x": 1}
    assert s.take_slot() and s.take_slot() and not s.take_slot()


def test_period_label_variants(crm):
    c, _ = crm
    q = c["quarters"][-1]
    fy, qn = q["fiscal_year"], q["fiscal_quarter"]
    rev = q["metrics"]["revenue"]["value"]
    for label in (f"Q{qn} FY{fy % 100}", f"FY{fy} Q{qn}", q["period_end"]):
        assert status(numeric("revenue", label, f"{rev / 1e9:.2f}", "usd_billions"), crm)["status"] == "verified"


def test_quote_must_appear_in_sentence(crm):
    _, ps = crm
    p = max((p for p in ps if p["kind"] == "text"), key=lambda p: len(p["text"]))
    quote = " ".join(p["text"].split()[3:15])
    claim = {"text": "Management sounded upbeat.", "claim_type": "quote", "metric": "", "period": "",
             "value": quote, "unit": "", "source": {"type": "press_release", "ref": p["id"]}}
    v = status(claim, crm)
    assert v["status"] == "mismatch" and "paraphrases" in v["reason"]
