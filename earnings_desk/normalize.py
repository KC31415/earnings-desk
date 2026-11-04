"""SEC companyfacts -> clean quarterly table for one company.

Key rules (see README "Data rules"):
- Periods come from the XBRL facts themselves (start/end dates), never from the
  `fy`/`fp` fields, which describe the *filing*, not the fact.
- Discrete quarters are taken directly when a ~3-month fact exists. Otherwise they
  are derived by subtracting consecutive year-to-date totals that share a start date
  (e.g. Q4 = full year - 9M YTD; Q2 cash flow = 6M YTD - 3M). Derived values are flagged.
- When several filings report the same period (restatements, comparatives), the most
  recently filed value is used and the earliest-filed value is kept as `original_value`.
- Fiscal labels use the company's fiscal year end; calendar labels use the quarter's midpoint.
"""
from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from .concepts import COST_OF_REVENUE_CONCEPTS, METRIC_ORDER, METRICS
from .sec import filing_url, filings_table

PERIODIC_FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A", "10-KT", "10-KT/A"}
QUARTER_DAYS = (75, 105)  # covers 13- and 14-week quarters
YEAR_DAYS = (350, 380)


@dataclass
class Value:
    value: float
    end: str
    start: str | None
    concept: str
    accn: str
    filed: str
    form: str
    derived: bool = False
    derivation: str | None = None
    original_value: float | None = None
    restated: bool = False
    partial: bool = False
    components: list[dict] = field(default_factory=list)


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _dur(start: str, end: str) -> int:
    return (_d(end) - _d(start)).days + 1


def _span_name(start: str, end: str) -> str:
    months = round(_dur(start, end) / 30.44)
    return "full year" if months >= 12 else f"{months}M YTD"


# ---------------------------------------------------------------- per-concept series

def _facts(cf: dict, concept: str, unit: str) -> list[dict]:
    node = cf.get("facts", {}).get("us-gaap", {}).get(concept)
    if not node:
        return []
    return [f for f in node.get("units", {}).get(unit, []) if f.get("form") in PERIODIC_FORMS and "end" in f]


def _best_by_period(facts: list[dict], concept: str) -> dict[tuple, Value]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for f in facts:
        groups[(f.get("start"), f["end"])].append(f)
    out = {}
    for (start, end), fs in groups.items():
        fs.sort(key=lambda f: (f.get("filed", ""), f.get("accn", "")))
        first, last = fs[0], fs[-1]
        out[(start, end)] = Value(
            value=float(last["val"]), end=end, start=start, concept=concept, accn=last.get("accn", ""),
            filed=last.get("filed", ""), form=last.get("form", ""), original_value=float(first["val"]),
            restated=float(first["val"]) != float(last["val"]),
        )
    return out


def _quarterly_flow(best: dict[tuple, Value], subtractable: bool) -> dict[str, Value]:
    q: dict[str, Value] = {}
    for (start, end), v in best.items():
        if start and QUARTER_DAYS[0] <= _dur(start, end) <= QUARTER_DAYS[1]:
            if end not in q or v.filed > q[end].filed:
                q[end] = v
    if not subtractable:
        return q
    by_start: dict[str, list[Value]] = defaultdict(list)
    for (start, end), v in best.items():
        if start and QUARTER_DAYS[0] <= _dur(start, end) <= YEAR_DAYS[1]:
            by_start[start].append(v)
    for start, items in by_start.items():
        items.sort(key=lambda v: v.end)
        for a, b in zip(items, items[1:]):
            gap = (_d(b.end) - _d(a.end)).days
            if not (QUARTER_DAYS[0] <= gap <= QUARTER_DAYS[1]) or b.end in q:
                continue
            q[b.end] = Value(
                value=b.value - a.value, end=b.end, start=(_d(a.end) + timedelta(days=1)).isoformat(),
                concept=b.concept, accn=b.accn, filed=b.filed, form=b.form, derived=True,
                derivation=f"{_span_name(start, b.end)} − {_span_name(start, a.end)}",
                original_value=(b.original_value or 0) - (a.original_value or 0),
                restated=a.restated or b.restated,
                components=[
                    {"start": start, "end": b.end, "value": b.value, "accn": b.accn, "sign": "+"},
                    {"start": start, "end": a.end, "value": a.value, "accn": a.accn, "sign": "-"},
                ],
            )
    return q


def _instant(best: dict[tuple, Value]) -> dict[str, Value]:
    out: dict[str, Value] = {}
    for (start, end), v in best.items():
        if start is None and (end not in out or v.filed > out[end].filed):
            out[end] = v
    return out


def metric_series(cf: dict, key: str) -> dict[str, Value]:
    """Quarter-end date -> Value, merging fallback concepts in priority order."""
    m = METRICS[key]
    merged: dict[str, Value] = {}
    for concept in m.concepts:
        best = _best_by_period(_facts(cf, concept, m.unit), concept)
        series = _instant(best) if m.kind == "instant" else _quarterly_flow(best, m.subtractable)
        for end, v in series.items():
            if end not in merged:
                v.partial = concept in m.partial_concepts
                merged[end] = v
    return merged


def _first_series(cf: dict, concepts: tuple[str, ...]) -> dict[str, Value]:
    merged: dict[str, Value] = {}
    for c in concepts:
        for end, v in _quarterly_flow(_best_by_period(_facts(cf, c, "USD"), c), True).items():
            merged.setdefault(end, v)
    return merged


def _combine(a: Value, b: Value, op: str, derivation: str) -> Value:
    val = a.value - b.value if op == "-" else a.value + b.value
    return Value(
        value=val, end=a.end, start=a.start, concept="(derived)", accn=a.accn, filed=max(a.filed, b.filed),
        form=a.form, derived=True, derivation=derivation, restated=a.restated or b.restated,
        components=[{"concept": a.concept, "value": a.value, "accn": a.accn, "sign": "+"},
                    {"concept": b.concept, "value": b.value, "accn": b.accn, "sign": op}],
    )


# ---------------------------------------------------------------- calendars

def fiscal_period(end: str, fye_mmdd: str) -> tuple[int, int]:
    """(fiscal_year, fiscal_quarter) for a quarter ending on `end`.

    Fiscal year = calendar year in which the fiscal year ends (CRM FY2026 ends Jan 2026,
    MSFT FY2026 ends Jun 2026). A 7-day tolerance handles 52/53-week years (ADBE).
    """
    e = _d(end)
    mm, dd = int(fye_mmdd[:2]), int(fye_mmdd[2:])

    def fye(y: int) -> date:
        return date(y, mm, min(dd, calendar.monthrange(y, mm)[1]))

    year = next(y for y in (e.year - 1, e.year, e.year + 1, e.year + 2) if fye(y) >= e - timedelta(days=7))
    quarter = min(4, max(1, 4 - round((fye(year) - e).days / 91.31)))
    if mm == 1 and dd <= 7:  # years ending in the first days of January are named for the prior year
        year -= 1
    return year, quarter


def calendar_period(start: str | None, end: str) -> tuple[int, int]:
    e = _d(end)
    s = _d(start) if start else e - timedelta(days=90)
    mid = s + (e - s) / 2
    return mid.year, (mid.month - 1) // 3 + 1


def infer_fye(cf: dict) -> str:
    ends = []
    for c in METRICS["revenue"].concepts + METRICS["net_income"].concepts:
        for f in _facts(cf, c, "USD"):
            if f.get("start") and f.get("form", "").startswith("10-K") and YEAR_DAYS[0] <= _dur(f["start"], f["end"]) <= YEAR_DAYS[1]:
                ends.append(f["end"][5:7] + f["end"][8:10])
    return Counter(ends).most_common(1)[0][0] if ends else "1231"


# ---------------------------------------------------------------- coverage

def coverage(sub: dict, cf: dict, n_quarters: int) -> dict:
    forms = {r["form"] for r in filings_table(sub)}
    sic = int(sub.get("sic") or 0)
    reasons, block = [], False
    if forms & {"20-F", "40-F", "20-F/A"} and not forms & {"10-Q", "10-K"}:
        reasons.append("Foreign filer (20-F/40-F): no quarterly 10-Q data in SEC XBRL, and statements may be in IFRS.")
        block = True
    elif "ifrs-full" in cf.get("_taxonomies", []) and not cf.get("facts", {}).get("us-gaap"):
        reasons.append("Financials are tagged in IFRS, which this app does not map.")
        block = True
    if 6000 <= sic <= 6199 or 6300 <= sic <= 6411:
        reasons.append(f"Banks, lenders and insurers (SIC {sic}: {sub.get('sicDescription', '')}) report revenue as "
                       "interest income or premiums, so these software-style metrics would be misleading.")
        block = True
    if n_quarters == 0 and not block:
        reasons.append("No quarterly structured (XBRL) financials found for this filer.")
        block = True
    elif 0 < n_quarters < 5:
        reasons.append(f"Only {n_quarters} quarters of structured data (recent IPO or sparse filings); "
                       "growth rates may be missing.")
    return {"level": "full" if not reasons else "limited", "blocked": block, "reasons": reasons}


# ---------------------------------------------------------------- sources

def quarter_sources(sub: dict, cik: int | str, end: str) -> dict:
    rows = filings_table(sub)
    periodic = [r for r in rows if r["form"] in ("10-Q", "10-K", "10-KT") and r.get("reportDate") == end]
    if not periodic:
        periodic = [r for r in rows if r["form"].startswith(("10-Q", "10-K")) and r.get("reportDate") == end]
    e = _d(end)
    eightk = sorted(
        (r for r in rows if r["form"] == "8-K" and "2.02" in str(r.get("items", ""))
         and e <= _d(r["filingDate"]) <= e + timedelta(days=75)),
        key=lambda r: r["filingDate"],
    )
    out = {}
    if periodic:
        p = sorted(periodic, key=lambda r: r["filingDate"])[0]
        out.update(filing_form=p["form"], filing_accn=p["accessionNumber"], filing_date=p["filingDate"],
                   filing_url=filing_url(cik, p["accessionNumber"], p.get("primaryDocument", "")))
    if eightk:
        k = eightk[0]
        out.update(earnings_8k_accn=k["accessionNumber"], earnings_8k_date=k["filingDate"],
                   earnings_8k_url=filing_url(cik, k["accessionNumber"]))
    return out


# ---------------------------------------------------------------- main entry

def normalize(sub: dict, cf: dict, ticker: str) -> dict:
    cik = int(cf.get("cik") or sub.get("cik") or 0)
    fye = sub.get("fiscalYearEnd") or infer_fye(cf)
    series = {k: metric_series(cf, k) for k in METRICS}

    # Gross profit fallback: revenue - cost of revenue.
    cost = _first_series(cf, COST_OF_REVENUE_CONCEPTS)
    for end, rev in series["revenue"].items():
        if end not in series["gross_profit"] and end in cost:
            series["gross_profit"][end] = _combine(rev, cost[end], "-", "revenue − cost of revenue")
    # Free cash flow = OCF - capex.
    series["free_cash_flow"] = {
        end: _combine(ocf, series["capex"][end], "-", "operating cash flow − capex")
        for end, ocf in series["operating_cash_flow"].items() if end in series["capex"]
    }

    spine = series["revenue"] or series["net_income"]
    quarters = []
    for end in sorted(spine):
        start = spine[end].start
        fy, fq = fiscal_period(end, fye)
        cy, cq = calendar_period(start, end)
        metrics = {k: asdict(series[k][end]) for k in METRIC_ORDER if end in series[k]}
        quarters.append({
            "period_end": end, "period_start": start, "fiscal_year": fy, "fiscal_quarter": fq,
            "fiscal_label": f"FY{fy} Q{fq}", "calendar_label": f"CY{cy} Q{cq}",
            "sources": quarter_sources(sub, cik, end), "metrics": metrics,
        })

    return {
        "ticker": ticker.upper(), "cik": cik, "name": sub.get("name") or cf.get("entityName", ""),
        "fiscal_year_end": fye, "sic": sub.get("sic"), "sic_description": sub.get("sicDescription"),
        "coverage": coverage(sub, cf, len(quarters)), "quarters": quarters,
    }
