"""Checks on real SEC samples (tests/samples/, made by scripts/make_test_samples.py).

Skipped until the samples exist. These assert internal consistency and calendar
labeling, not hand-typed financial figures.
"""
import json
from pathlib import Path

import pytest

from earnings_desk.normalize import normalize

SAMPLES = Path(__file__).parent / "samples"
# ticker -> fiscal year-end month
CASES = {"MSFT": 6, "CRM": 1, "ADBE": 11}


def load(t):
    cf, sub = SAMPLES / f"{t}_companyfacts.json", SAMPLES / f"{t}_submissions.json"
    if not cf.exists():
        pytest.skip(f"no real sample for {t} yet")
    return normalize(json.loads(sub.read_text()), json.loads(cf.read_text()), t)


@pytest.mark.parametrize("ticker", list(CASES))
def test_has_eight_contiguous_quarters(ticker):
    c = load(ticker)
    assert c["coverage"]["level"] == "full"
    labels = [(q["fiscal_year"], q["fiscal_quarter"]) for q in c["quarters"][-8:]]
    assert len(labels) == 8
    for (y1, q1), (y2, q2) in zip(labels, labels[1:]):
        assert (y2, q2) == ((y1, q1 + 1) if q1 < 4 else (y1 + 1, 1))


@pytest.mark.parametrize("ticker,fye_month", CASES.items())
def test_q4_ends_at_fiscal_year_end(ticker, fye_month):
    c = load(ticker)
    for q in c["quarters"][-8:]:
        month = int(q["period_end"][5:7])
        if q["fiscal_quarter"] == 4:
            assert month in (fye_month, fye_month % 12 + 1)  # 52/53-week years can spill into the next month


@pytest.mark.parametrize("ticker", list(CASES))
def test_q4_revenue_is_derived_and_positive(ticker):
    c = load(ticker)
    q4s = [q for q in c["quarters"][-8:] if q["fiscal_quarter"] == 4]
    assert q4s
    for q in q4s:
        rev = q["metrics"]["revenue"]
        assert rev["value"] > 0
        assert rev["derived"] or rev["form"].startswith("10-K")


@pytest.mark.parametrize("ticker", list(CASES))
def test_core_metrics_present(ticker):
    latest = load(ticker)["quarters"][-1]["metrics"]
    for k in ("revenue", "operating_income", "net_income", "operating_cash_flow", "free_cash_flow"):
        assert k in latest, k
