"""SYNTHETIC test fixtures: made-up round numbers shaped like SEC companyfacts JSON.

These exercise the normalizer's logic (fiscal calendars, Q4 derivation, YTD
subtraction, restatements, concept fallbacks). They are NOT real company data.
Real trimmed SEC samples live in tests/samples/ once the fetch workflow has run.
"""
from __future__ import annotations


def _fact(start, end, val, accn, form, filed):
    f = {"end": end, "val": val, "accn": accn, "form": form, "filed": filed, "fy": 0, "fp": "X"}
    if start:
        f["start"] = start
    return f


def make_company(fye: str, periods: list[tuple[str, str]], fy_start: str, *, restate_q1=False,
                 switch_concept_after: int | None = None) -> tuple[dict, dict]:
    """periods: four (start, end) quarter tuples making up one fiscal year.

    Revenue = 100, 110, 120, 130 (Q4 only reported as full-year 460 in the 10-K).
    OCF is reported YTD only: 10, 25, 45, 70 (so quarters are 10, 15, 20, 25).
    Capex YTD: 2, 4, 6, 8. EPS: 1.00, 1.10, 1.20, FY 4.60 (Q4 not derivable).
    Deferred revenue (instant): 50, 40, 30, 80.
    """
    rev = [100, 110, 120, 130]
    ocf_ytd = [10, 25, 45, 70]
    capex_ytd = [2, 4, 6, 8]
    eps = [1.00, 1.10, 1.20]
    dr = [50, 40, 30, 80]
    revs_a, revs_b, ocf, capex, eps_f, drf = [], [], [], [], [], []
    for i, (s, e) in enumerate(periods):
        form = "10-K" if i == 3 else "10-Q"
        accn = f"0000000000-00-00000{i + 1}"
        filed = e[:8] + "28"  # plausible filing date; not used for logic except ordering
        target = revs_b if switch_concept_after is not None and i >= switch_concept_after else revs_a
        if i < 3:
            target.append(_fact(s, e, rev[i], accn, form, filed))
            eps_f.append(_fact(s, e, eps[i], accn, form, filed))
        else:
            target.append(_fact(fy_start, e, sum(rev), accn, form, filed))
            eps_f.append(_fact(fy_start, e, 4.60, accn, form, filed))
        ocf.append(_fact(fy_start, e, ocf_ytd[i], accn, form, filed))
        capex.append(_fact(fy_start, e, capex_ytd[i], accn, form, filed))
        drf.append(_fact(None, e, dr[i], accn, form, filed))
    # The Q3 10-Q also reports 9M year-to-date revenue; Q4 is derived as FY - 9M YTD.
    s3, e3 = periods[2]
    target3 = revs_b if switch_concept_after is not None and 2 >= switch_concept_after else revs_a
    target3.append(_fact(fy_start, e3, 330, "0000000000-00-000003", "10-Q", e3[:8] + "28"))
    if restate_q1:
        s1, e1 = periods[0]
        # Q1 revenue later restated to 101 in a 10-Q/A filed after the original.
        revs_a.append(_fact(s1, e1, 101, "0000000000-00-000009", "10-Q/A", "2099-01-01"))

    us = {
        "Revenues": {"units": {"USD": revs_a}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": ocf}},
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": capex}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": eps_f}},
        "ContractWithCustomerLiability": {"units": {"USD": drf}},
    }
    if revs_b:
        us["RevenueFromContractWithCustomerExcludingAssessedTax"] = {"units": {"USD": revs_b}}
    cf = {"cik": 1, "entityName": "Synthetic Co", "facts": {"us-gaap": us, "dei": {}}, "_taxonomies": ["dei", "us-gaap"]}

    rows = []
    for i, (s, e) in enumerate(periods):
        rows.append({"accessionNumber": f"0000000000-00-00000{i + 1}", "filingDate": e[:8] + "28",
                     "reportDate": e, "form": "10-K" if i == 3 else "10-Q", "items": "",
                     "primaryDocument": "doc.htm", "primaryDocDescription": ""})
    sub = {"cik": "1", "name": "Synthetic Co", "sic": "7372", "sicDescription": "Prepackaged software",
           "fiscalYearEnd": fye, "filings": {"recent": {k: [r[k] for r in rows] for k in rows[0]}}}
    return sub, cf


# Three fiscal calendars, mirroring CRM (January), MSFT (June), ADBE (52/53-week, ~end of November).
JAN = ("0131", [("2025-02-01", "2025-04-30"), ("2025-05-01", "2025-07-31"),
                ("2025-08-01", "2025-10-31"), ("2025-11-01", "2026-01-31")], "2025-02-01")
JUN = ("0630", [("2025-07-01", "2025-09-30"), ("2025-10-01", "2025-12-31"),
                ("2026-01-01", "2026-03-31"), ("2026-04-01", "2026-06-30")], "2025-07-01")
NOV52 = ("1128", [("2024-11-30", "2025-02-28"), ("2025-03-01", "2025-05-30"),
                  ("2025-05-31", "2025-08-29"), ("2025-08-30", "2025-11-28")], "2024-11-30")
