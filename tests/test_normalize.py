import pytest

from earnings_desk.analytics import growth_frame, margins_frame
from earnings_desk.normalize import calendar_period, coverage, fiscal_period, normalize
from earnings_desk.sec import find_exhibit_991
from tests.synthetic import JAN, JUN, NOV52, make_company


@pytest.mark.parametrize("cal,expected_fy,expected_cal", [
    (JAN, 2026, ["CY2025 Q1", "CY2025 Q2", "CY2025 Q3", "CY2025 Q4"]),
    (JUN, 2026, ["CY2025 Q3", "CY2025 Q4", "CY2026 Q1", "CY2026 Q2"]),
    (NOV52, 2025, ["CY2025 Q1", "CY2025 Q2", "CY2025 Q3", "CY2025 Q4"]),
])
def test_fiscal_and_calendar_labels(cal, expected_fy, expected_cal):
    fye, periods, fy_start = cal
    c = normalize(*make_company(fye, periods, fy_start), "SYN")
    assert [q["fiscal_label"] for q in c["quarters"]] == [f"FY{expected_fy} Q{i}" for i in (1, 2, 3, 4)]
    assert [q["calendar_label"] for q in c["quarters"]] == expected_cal


@pytest.mark.parametrize("cal", [JAN, JUN, NOV52])
def test_q4_derived_from_full_year_minus_9m(cal):
    c = normalize(*make_company(*cal), "SYN")
    q4 = c["quarters"][3]["metrics"]["revenue"]
    assert q4["value"] == 130 and q4["derived"] and q4["derivation"] == "full year − 9M YTD"
    assert not c["quarters"][0]["metrics"]["revenue"]["derived"]


def test_cash_flow_quarters_from_ytd_and_fcf():
    c = normalize(*make_company(*JUN), "SYN")
    ocf = [q["metrics"]["operating_cash_flow"]["value"] for q in c["quarters"]]
    fcf = [q["metrics"]["free_cash_flow"]["value"] for q in c["quarters"]]
    assert ocf == [10, 15, 20, 25]
    assert fcf == [8, 13, 18, 23]
    assert all(q["metrics"]["free_cash_flow"]["derived"] for q in c["quarters"])


def test_eps_q4_not_derived():
    c = normalize(*make_company(*JAN), "SYN")
    assert "eps_diluted" in c["quarters"][2]["metrics"]
    assert "eps_diluted" not in c["quarters"][3]["metrics"]


def test_restatement_uses_latest_keeps_original():
    c = normalize(*make_company(*JAN, restate_q1=True), "SYN")
    q1 = c["quarters"][0]["metrics"]["revenue"]
    assert q1["value"] == 101 and q1["original_value"] == 100 and q1["restated"]


def test_concept_fallback_across_switch():
    c = normalize(*make_company(*JAN, switch_concept_after=2), "SYN")
    concepts = [q["metrics"]["revenue"]["concept"] for q in c["quarters"]]
    assert concepts[:2] == ["Revenues"] * 2
    assert concepts[2:] == ["RevenueFromContractWithCustomerExcludingAssessedTax"] * 2
    assert c["quarters"][3]["metrics"]["revenue"]["value"] == 130


def test_instant_metric_and_sources():
    c = normalize(*make_company(*JAN), "SYN")
    assert [q["metrics"]["deferred_revenue"]["value"] for q in c["quarters"]] == [50, 40, 30, 80]
    src = c["quarters"][3]["sources"]
    assert src["filing_form"] == "10-K" and src["filing_url"].endswith("/doc.htm")


def test_growth_and_margins():
    c = normalize(*make_company(*JAN), "SYN")
    g = growth_frame(c)
    assert g["revenue_qoq"].iloc[1] == pytest.approx(0.10)
    assert g["revenue_yoy"].isna().all()  # only one fiscal year of data
    m = margins_frame(c)
    assert m["FCF margin"].iloc[0] == pytest.approx(0.08)


@pytest.mark.parametrize("end,fye,expected", [
    ("2025-10-26", "0125", (2026, 3)),   # NVDA-style late-January year end
    ("2025-09-30", "0630", (2026, 1)),   # MSFT
    ("2023-12-01", "1129", (2023, 4)),   # 53-week year ending a few days after the nominal date
    ("2025-07-31", "0430", (2026, 1)),   # C3 AI-style April year end
])
def test_fiscal_period_edge_cases(end, fye, expected):
    assert fiscal_period(end, fye) == expected


def test_calendar_period_uses_midpoint():
    assert calendar_period("2025-11-01", "2026-01-31") == (2025, 4)


def test_coverage_flags():
    sub, cf = make_company(*JAN)
    assert coverage(sub, cf, 4)["level"] == "limited"  # < 5 quarters
    bank = dict(sub, sic="6022", sicDescription="State commercial banks")
    assert coverage(bank, cf, 12)["blocked"]
    foreign = dict(sub, filings={"recent": {"form": ["20-F"], "accessionNumber": ["x"], "filingDate": ["2025-01-01"]}})
    assert coverage(foreign, cf, 0)["blocked"]
    assert coverage(sub, cf, 12) == {"level": "full", "blocked": False, "reasons": []}


def test_find_exhibit_991():
    html = """<table><tr><td>1</td><td><a href="/Archives/edgar/data/1/000/form8k.htm">form8k.htm</a></td><td>8-K</td></tr>
    <tr><td>2</td><td><a href="/Archives/edgar/data/1/000/ex99-2.htm">ex99-2.htm</a></td><td>EX-99.2</td></tr>
    <tr><td>3</td><td><a href="/ix?doc=/Archives/edgar/data/1/000/ex991.htm">ex991.htm</a></td><td>EX-99.1</td></tr></table>"""
    assert find_exhibit_991(html) == "https://www.sec.gov/Archives/edgar/data/1/000/ex991.htm"
