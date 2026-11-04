"""Scorecard logic on canned notes built from real CRM samples (no API calls)."""
import pytest

from earnings_desk.note import generate_note
from earnings_desk.scorecard import INJECTIONS, claims_frame, compute, hand_grade_summary, inject, write_hand_grade_sample
from tests.test_verify import _fake_llm_factory, crm, numeric  # noqa: F401  (fixture)


@pytest.fixture()
def notes(crm):
    c, ps = crm
    fake, _ = _fake_llm_factory(c, ps)
    i = len(c["quarters"]) - 1
    note = generate_note(c, i, ps, {"earnings_8k_accn": "x"}, llm=fake)
    note["paragraphs"] = ps
    return [(c, i, note)]


def test_compute_and_injection(notes):
    res = compute(notes)
    assert res["claims"] == 3 and res["numeric"]["claims"] == 2
    inj = {r["error_type"]: r for r in res["injection"]}
    assert set(inj) == set(INJECTIONS)
    for kind in ("value +5%", "value −5%", "wrong unit", "wrong period"):
        assert inj[kind]["injected"] == 1 and inj[kind]["caught"] == 1, kind
    assert res["failures"] == {"wrong value / unit / period": 1}


def test_inject_skips_unverified_and_non_applicable(notes):
    c, _, note = notes[0]
    good, bad = note["sections"][0]["claims"]
    assert inject(bad, c, "value +5%") is None  # only correct claims get corrupted
    assert inject(good, c, "QoQ/YoY swapped") is None  # not a growth claim


def test_hand_grade_roundtrip(notes, tmp_path):
    path = tmp_path / "hg.csv"
    assert write_hand_grade_sample(claims_frame(notes), n=3, path=path)
    import pandas as pd
    g = pd.read_csv(path, dtype=str, keep_default_na=False)
    g["my_grade"] = "correct"
    g.to_csv(path, index=False)
    s = hand_grade_summary(path)
    assert s["graded"] == 3 and s["human_correct_pct"] == 100.0
    assert not write_hand_grade_sample(claims_frame(notes), n=3, path=path)  # never overwrites grades
