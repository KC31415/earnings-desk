"""Deterministic claim verifier. Plain code, no LLM.

Statuses:
  verified     numeric value recomputed from XBRL (or found verbatim in the cited press-release
               paragraph when the metric has no XBRL equivalent), or quote found verbatim
  mismatch     the claim disagrees with the source; `actual` shows the real value
  sourced      qualitative claim citing a real paragraph: sourced, not machine-verifiable
  unsupported  nothing checkable: unknown metric/period, missing or bad citation, or numbers
               inside a qualitative claim that its cited paragraph does not contain

A failing claim is never dropped or rewritten; its status and reason are attached to it.
"""
from __future__ import annotations

import re

from .analytics import pct_change
from .concepts import METRICS
from .press import norm_text

UNIT_SCALE = {
    "usd": 1.0, "usd_thousands": 1e3, "usd_millions": 1e6, "usd_billions": 1e9,
    "usd_per_share": 1.0, "percent": 1.0, "count": 1.0, "": 1.0,
}
MARGIN_METRICS = {
    "gross_margin": "gross_profit", "operating_margin": "operating_income",
    "net_margin": "net_income", "fcf_margin": "free_cash_flow",
}
BASE_METRICS = list(METRICS) + ["free_cash_flow"]
GROWTH_METRICS = [f"{k}_{g}" for k in BASE_METRICS for g in ("yoy", "qoq")]
METRIC_VOCAB = BASE_METRICS + GROWTH_METRICS + list(MARGIN_METRICS) + ["other"]

NEG_WORDS = re.compile(r"\b(down|declin\w*|decreas\w*|fell|fall\w*|lower|drop\w*|contract\w*|negative|loss\w*|shrank|shrink\w*)\b", re.I)
_NUM_RE = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")
# Numbers that are labels, not values: FY2026, Q3, 2026, 10-Q, 8-K, 99.1
_LABEL_RE = re.compile(r"\b(?:FY|CY|Q)\s?\d{1,4}\b|\b(?:19|20)\d{2}\b|\b\d{1,2}-[A-Z]\b|\b99\.\d\b", re.I)


def numbers_in(text: str) -> list[float]:
    """Absolute values of the numbers in a text, ignoring labels like FY2026, Q3, 2026, 8-K."""
    text = _LABEL_RE.sub(" ", norm_text(text))
    out = []
    for m in _NUM_RE.findall(text):
        try:
            out.append(abs(float(m.replace(",", ""))))
        except ValueError:
            pass
    return out


def number_strings(text: str) -> list[str]:
    """Numbers as written (unsigned, commas removed), ignoring labels like FY2026, Q3, 2026, 8-K."""
    return [m.replace(",", "").lstrip("-") for m in _NUM_RE.findall(_LABEL_RE.sub(" ", norm_text(text)))]


_QUOTED_RE = re.compile(r'"([^"]{8,}?)"')


def _decimals(s: str) -> int:
    s = s.strip().replace(",", "")
    return len(s.split(".")[1]) if "." in s else 0


def _parse_value(s: str) -> float | None:
    try:
        return float(str(s).strip().replace(",", "").replace("$", "").replace("%", ""))
    except ValueError:
        return None


def _find_quarter(company: dict, label: str) -> int | None:
    """Match 'FY2026 Q3', 'Q3 FY26', 'CY2025 Q4' or a period-end date to a quarter."""
    label = (label or "").strip().upper()
    for i, q in enumerate(company["quarters"]):
        if q["fiscal_label"].upper() == label or q["calendar_label"].upper() == label or q["period_end"] == label:
            return i
    fy, qn = re.search(r"\b(FY|CY)\s?'?(\d{2}|\d{4})\b", label), re.search(r"\bQ([1-4])\b", label)
    if fy and qn:
        year = int(fy.group(2)) + (2000 if len(fy.group(2)) == 2 else 0)
        want = f"{fy.group(1)}{year} Q{qn.group(1)}"
        for i, q in enumerate(company["quarters"]):
            if want in (q["fiscal_label"], q["calendar_label"]):
                return i
    return None


def _year_ago(company: dict, i: int) -> int | None:
    q = company["quarters"][i]
    for j, p in enumerate(company["quarters"]):
        if p["fiscal_year"] == q["fiscal_year"] - 1 and p["fiscal_quarter"] == q["fiscal_quarter"]:
            return j
    return None


def _val(company: dict, i: int | None, key: str) -> dict | None:
    if i is None or i < 0:
        return None
    return company["quarters"][i]["metrics"].get(key)


def actual_value(company: dict, metric: str, period: str) -> tuple[float | None, str, dict]:
    """Recompute a metric from XBRL. Returns (value in base units or percent, unit, evidence)."""
    i = _find_quarter(company, period)
    if i is None:
        return None, "", {"error": f"period '{period}' not found in XBRL data"}
    q = company["quarters"][i]
    if metric in BASE_METRICS:
        v = _val(company, i, metric)
        if not v:
            return None, "", {"error": f"{metric} not reported for {q['fiscal_label']}"}
        unit = "usd_per_share" if METRICS.get(metric) and METRICS[metric].unit == "USD/shares" else "usd"
        return v["value"], unit, {"inputs": [_evidence(q, metric, v)]}
    if metric in GROWTH_METRICS:
        base, kind = metric.rsplit("_", 1)
        j = _year_ago(company, i) if kind == "yoy" else (i - 1 if i > 0 else None)
        cur, old = _val(company, i, base), _val(company, j, base)
        if not cur or not old:
            return None, "", {"error": f"need {base} for both periods to compute {kind.upper()}"}
        pc = pct_change(cur["value"], old["value"])
        if pc is None:
            return None, "", {"error": "base value is zero"}
        prev = company["quarters"][j]
        return pc * 100, "percent", {"inputs": [_evidence(q, base, cur), _evidence(prev, base, old)],
                                     "formula": f"({q['fiscal_label']} / {prev['fiscal_label']} − 1) × 100"}
    if metric in MARGIN_METRICS:
        num, rev = _val(company, i, MARGIN_METRICS[metric]), _val(company, i, "revenue")
        if not num or not rev or not rev["value"]:
            return None, "", {"error": f"need {MARGIN_METRICS[metric]} and revenue for {q['fiscal_label']}"}
        return num["value"] / rev["value"] * 100, "percent", {
            "inputs": [_evidence(q, MARGIN_METRICS[metric], num), _evidence(q, "revenue", rev)],
            "formula": f"{MARGIN_METRICS[metric]} / revenue × 100"}
    return None, "", {"error": f"metric '{metric}' has no XBRL equivalent"}


def _evidence(q: dict, key: str, v: dict) -> dict:
    return {"period": q["fiscal_label"], "period_end": q["period_end"], "metric": key, "value": v["value"],
            "concept": v["concept"], "accn": v["accn"], "derived": v["derived"], "derivation": v["derivation"]}


def _tolerance(value_str: str, scale: float) -> float:
    return 0.5 * 10 ** (-_decimals(value_str)) * scale + 1e-9


def verify_claim(claim: dict, company: dict, paras: dict[str, str]) -> dict:
    """Return the claim with a `verification` dict attached (input is not modified)."""
    c = dict(claim)
    ctype = c.get("claim_type")
    src = c.get("source") or {}
    ref = (src.get("ref") or "").strip()
    para = paras.get(ref.upper()) if ref else None
    value_str = str(c.get("value") or "").strip()
    res: dict = {"status": "unsupported", "reason": "", "actual": None}

    if ctype == "numeric" and not number_strings(c.get("text", "")):
        # The sentence shows no number, so the reader sees a qualitative statement: check it as one.
        ctype = "qualitative"
        res["note"] = "numeric claim whose sentence contains no number; checked as qualitative"

    if ctype == "numeric":
        stated = _parse_value(value_str)
        unit = c.get("unit") or ""
        shown = None
        if stated is not None:
            # Verify the number the reader actually sees: the structured value itself, or the sentence's
            # rounding of it (e.g. value 105.85 written as "106%").
            for t in number_strings(c.get("text", "")):
                if abs(float(t) - abs(stated)) <= 0.5 * 10 ** (-_decimals(t)) + 1e-9:
                    shown = t
                    break
        if stated is None or unit not in UNIT_SCALE:
            res["reason"] = f"value '{value_str}' / unit '{unit}' is not a number with a known unit"
        elif shown is None:
            res.update(status="mismatch", reason=f"sentence does not contain the structured value {value_str}")
        else:
            if shown != value_str.replace(",", "").lstrip("-"):
                res["checked_as_written"] = shown
                stated = float(shown) * (-1 if stated < 0 else 1)
                value_str = shown
            metric = c.get("metric") or ""
            actual, a_unit, ev = actual_value(company, metric, c.get("period", "")) if metric != "other" else (None, "", {})
            if actual is not None:
                if a_unit == "percent" and unit != "percent":
                    res.update(status="mismatch", reason=f"{metric} is a percentage but the claim's unit is '{unit}'", actual=actual)
                elif a_unit != "percent" and unit == "percent":
                    res.update(status="mismatch", reason=f"{metric} is an amount but the claim's unit is 'percent'", actual=actual)
                else:
                    scale = UNIT_SCALE[unit]
                    stated_base = stated * scale
                    tol = _tolerance(value_str, scale)
                    same_mag = abs(abs(stated_base) - abs(actual)) <= tol
                    sign_ok = (actual < 0) == (stated_base < 0) or (actual < 0 and bool(NEG_WORDS.search(c.get("text", ""))))
                    ok = same_mag and sign_ok
                    res.update(status="verified" if ok else "mismatch", actual=actual, actual_unit=a_unit,
                               method="xbrl", evidence=ev,
                               reason="" if ok else ("sign/direction is wrong" if same_mag else
                                                     f"stated {value_str} {unit}, XBRL gives {_fmt(actual, a_unit, unit)}"))
            elif para is not None:
                scale = UNIT_SCALE[unit]
                tol = _tolerance(value_str, scale)
                # Same amount at any scale counts ("$2.58 billion" vs "2,580" in a table in millions).
                scales = (1.0, 1e3, 1e6, 1e9) if unit.startswith("usd") and unit != "usd_per_share" else (scale,)
                found = any(abs(n * s - abs(stated) * scale) <= tol for n in numbers_in(para) for s in scales)
                res.update(status="verified" if found else "mismatch", method="press_release_text",
                           reason="" if found else f"{value_str} does not appear in {ref}",
                           evidence={"paragraph": ref, **({"xbrl_note": ev.get("error")} if ev.get("error") else {})})
            else:
                res["reason"] = ev.get("error") or "no XBRL equivalent and no valid press-release paragraph cited"
    elif ctype == "quote":
        text = norm_text(c.get("text", ""))
        # What the reader sees is the quoted span(s) in the sentence; fall back to the value field.
        spans = [m.strip().rstrip(".,;:").strip() for m in _QUOTED_RE.findall(text)]
        q = norm_text(value_str).strip('"').strip()
        if not spans and q and q in text:
            spans = [q]
        if para is None:
            res["reason"] = f"cited paragraph '{ref}' does not exist"
        elif not spans:
            res.update(status="mismatch", method="exact_substring",
                       reason="the sentence paraphrases instead of containing the quoted words")
        else:
            bad = [sp for sp in spans if sp not in norm_text(para)]
            if not bad:
                res.update(status="verified", method="exact_substring", evidence={"paragraph": ref, "spans": spans})
            else:
                elsewhere = sorted({pid for pid, t in paras.items() for sp in bad if sp in norm_text(t)})
                res.update(status="mismatch", method="exact_substring", evidence={"paragraph": ref},
                           reason=f"quoted words not found in {ref}" + (f" (found in {', '.join(elsewhere)})" if elsewhere else ""))
    if ctype == "qualitative":
        if para is None:
            res["reason"] = f"cited paragraph '{ref}' does not exist" if ref else "no paragraph cited"
        else:
            missing = [n for n in numbers_in(c.get("text", "")) if not any(abs(n - p) < 1e-9 for p in numbers_in(para))]
            if missing:
                res["reason"] = f"numbers {missing} in a qualitative claim are not in {ref}"
            else:
                res.update(status="sourced", evidence={"paragraph": ref})
    elif ctype not in ("numeric", "quote"):
        res["reason"] = f"unknown claim_type '{ctype}'"
    c["verification"] = res
    return c


def _fmt(actual: float, a_unit: str, unit: str) -> str:
    if a_unit == "percent":
        return f"{actual:.3f}%"
    scale = UNIT_SCALE.get(unit, 1.0)
    return f"{actual / scale:,.4g} {unit} ({actual:,.0f})"


def _with_continuations(paragraphs: list[dict]) -> dict[str, str]:
    """Paragraph id -> text, extended with following blocks that continue a broken sentence.

    Notes stored before the splitter re-joined sentences can cite the first half of a sentence that the
    HTML split in two; this lets them be checked against the whole sentence. New notes are unaffected.
    """
    out = {}
    for i, p in enumerate(paragraphs):
        text = p["text"]
        j = i + 1
        while (p["kind"] == "text" and j < len(paragraphs) and paragraphs[j]["kind"] == "text"
               and not re.search(r"[.!?:;\"')\]]$", text) and re.match(r"[a-z$\d(]", paragraphs[j]["text"])):
            text += " " + paragraphs[j]["text"]
            j += 1
        out[p["id"].upper()] = text
    return out


def verify_note(note: dict, company: dict, paragraphs: list[dict]) -> dict:
    paras = _with_continuations(paragraphs)
    out = dict(note)
    out["sections"] = [dict(s, claims=[verify_claim(c, company, paras) for c in s.get("claims", [])])
                       for s in note.get("sections", [])]
    out["summary"] = summarize([c for s in out["sections"] for c in s["claims"]])
    return out


def summarize(claims: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for c in claims:
        k = c["verification"]["status"]
        counts[k] = counts.get(k, 0) + 1
    numeric = [c for c in claims if c.get("claim_type") == "numeric" and not c["verification"].get("note")]
    nv = sum(c["verification"]["status"] == "verified" for c in numeric)
    return {"claims": len(claims), "by_status": counts, "numeric": len(numeric), "numeric_verified": nv,
            "numeric_verified_pct": (nv / len(numeric)) if numeric else None}
