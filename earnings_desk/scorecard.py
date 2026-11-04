"""Accuracy scorecard: how often the verified note is right, and how often the verifier catches errors.

Everything here is offline: it reads stored notes and re-runs the deterministic verifier.
"""
from __future__ import annotations

import csv
import random
import re
from pathlib import Path

import pandas as pd

from . import config
from .normalize import normalize
from .notes_service import get_note, note_quarters
from .sec import load_raw
from .store import NoteStore
from .verify import (GROWTH_METRICS, MARGIN_METRICS, UNIT_SCALE, _tolerance, _with_continuations, actual_value,
                     number_strings, verify_claim)

OUT_DIR = config.DATA_DIR / "scorecard"
HAND_GRADE_CSV = OUT_DIR / "hand_grade.csv"
QUARTERS = 5
HAND_GRADE_COLUMNS = ["sample_id", "ticker", "quarter", "section", "claim_type", "text", "cited_source",
                      "verifier_status", "verifier_reason", "my_grade", "my_notes"]


def fye_group(company: dict) -> str:
    mm = company["fiscal_year_end"][:2]
    return "Calendar year (Dec)" if mm == "12" else f"Non-calendar ({ {'01': 'Jan', '04': 'Apr', '06': 'Jun', '11': 'Nov'}.get(mm, mm)} FYE)"


def metric_type(claim: dict) -> str:
    if claim.get("claim_type") != "numeric" or claim["verification"].get("note"):
        return claim["verification"].get("note") and "qualitative" or claim.get("claim_type", "")
    m = claim.get("metric", "")
    if m in GROWTH_METRICS:
        return "growth (QoQ/YoY)"
    if m in MARGIN_METRICS:
        return "margin"
    if claim["verification"].get("method") == "xbrl" or m not in ("other", ""):
        return "amount (XBRL)"
    return "press-release number"


def failure_category(claim: dict) -> str:
    v = claim["verification"]
    r = v.get("reason", "")
    if v["status"] in ("verified", "sourced"):
        return ""
    if "paraphrases" in r:
        return "quote paraphrased, not quoted"
    if "quoted words not found" in r:
        return "quote not in cited paragraph"
    if "sentence does not contain" in r:
        return "sentence and structured value disagree"
    if "does not appear in" in r or "qualitative claim are not in" in r:
        return "number not in cited paragraph"
    if v.get("method") == "xbrl" and v.get("actual") is not None:
        shown = v.get("checked_as_written") or str(claim.get("value", ""))
        try:
            stated = float(shown.replace(",", "")) * UNIT_SCALE.get(claim.get("unit", ""), 1.0)
            if abs(abs(stated) - abs(v["actual"])) <= 2 * _tolerance(shown, UNIT_SCALE.get(claim.get("unit", ""), 1.0)):
                return "rounding at the boundary"
        except ValueError:
            pass
        return "wrong value / unit / period"
    if v["status"] == "unsupported":
        return "nothing checkable (bad metric, period or citation)"
    return "other"


def load_notes() -> list[tuple[dict, int, dict]]:
    store = NoteStore(use_redis=False)
    out = []
    for t in config.SHOWCASE_TICKERS:
        raw = load_raw(t)
        if not raw:
            continue
        company = normalize(*raw, t)
        for i in note_quarters(company, QUARTERS):
            note = get_note(company, i, store)
            if note:
                out.append((company, i, note))
    return out


def claims_frame(notes) -> pd.DataFrame:
    rows = []
    for company, i, note in notes:
        q = company["quarters"][i]
        for s in note["sections"]:
            for c in s["claims"]:
                v = c["verification"]
                eff_type = "qualitative" if v.get("note") else c.get("claim_type")
                rows.append({
                    "ticker": company["ticker"], "quarter": q["fiscal_label"], "period_end": q["period_end"],
                    "fiscal_quarter": q["fiscal_quarter"], "fye_group": fye_group(company), "section": s["title"],
                    "claim_type": eff_type, "metric": c.get("metric", ""), "metric_type": metric_type(c),
                    "status": v["status"], "reason": v.get("reason", ""), "failure": failure_category(c),
                    "text": c.get("text", ""), "source": f"{c['source']['type']}:{c['source']['ref']}",
                    "derived_q4": q["fiscal_quarter"] == 4,
                })
    return pd.DataFrame(rows)


def rate_table(df: pd.DataFrame, by: str) -> list[dict]:
    out = []
    for key, g in df.groupby(by):
        n = len(g)
        out.append({by: key, "claims": n, "verified_pct": round((g.status == "verified").mean() * 100, 1),
                    "mismatch_pct": round((g.status == "mismatch").mean() * 100, 1),
                    "unsupported_pct": round((g.status == "unsupported").mean() * 100, 1)})
    return out


# ---------------------------------------------------------------- error injection

INJECTIONS = ["value +5%", "value −5%", "wrong unit", "wrong period", "QoQ/YoY swapped", "quote word changed"]


def _replace_number(text: str, old: str, new: str) -> str:
    pat = re.compile(r"(?<![\d.])" + re.escape(old).replace(r"\.", r"\.") + r"(?![\d])")
    variants = [old, f"{float(old):,.{len(old.split('.')[1]) if '.' in old else 0}f}"]
    for o in variants:
        pat = re.compile(r"(?<![\d.,])" + re.escape(o) + r"(?![\d])")
        if pat.search(text):
            return pat.sub(new, text, count=1)
    return text


def _is_true(claim: dict, company: dict) -> bool:
    """Is an injected numeric claim accidentally still correct? (Then it is not an error; skip it.)"""
    actual, a_unit, _ = actual_value(company, claim.get("metric", ""), claim.get("period", ""))
    if actual is None:
        return False
    try:
        shown = number_strings(claim["text"])
        stated = float(claim["value"]) * UNIT_SCALE[claim["unit"]]
    except (ValueError, KeyError):
        return False
    tol = _tolerance(claim["value"], UNIT_SCALE[claim["unit"]])
    same_unit_kind = (a_unit == "percent") == (claim["unit"] == "percent")
    return same_unit_kind and abs(abs(stated) - abs(actual)) <= tol and claim["value"] in shown


def inject(claim: dict, company: dict, kind: str) -> dict | None:
    """Return a corrupted copy of a verified claim, or None if this error type does not apply."""
    v = claim["verification"]
    c = {k: val for k, val in claim.items() if k != "verification"}
    if kind == "quote word changed":
        if claim.get("claim_type") != "quote" or v["status"] != "verified":
            return None
        spans = v.get("evidence", {}).get("spans") or []
        words = [w for w in (spans[0].split() if spans else []) if len(w) > 3 and w.isalpha()]
        if not words:
            return None
        w = words[len(words) // 2]
        bad = w[:-1] + ("s" if w[-1] != "s" else "x")
        bad_span = spans[0].replace(w, bad, 1)
        if spans[0] not in c["text"]:
            return None
        c["text"] = c["text"].replace(spans[0], bad_span, 1)  # corrupt the words inside the quotation marks
        c["value"] = str(c.get("value", "")).replace(w, bad, 1)
        return c
    if claim.get("claim_type") != "numeric" or v["status"] != "verified" or v.get("method") != "xbrl":
        return None
    shown = v.get("checked_as_written") or str(c["value"]).replace(",", "").lstrip("-")
    c["value"] = shown
    if kind in ("value +5%", "value −5%"):
        dec = len(shown.split(".")[1]) if "." in shown else 0
        new = f"{float(shown) * (1.05 if kind == 'value +5%' else 0.95):.{dec}f}"
        if new == shown:
            return None
        c["text"], c["value"] = _replace_number(c["text"], shown, new), new
    elif kind == "wrong unit":
        swap = {"usd_billions": ("usd_millions", "billion", "million"), "usd_millions": ("usd_billions", "million", "billion")}
        if c.get("unit") not in swap:
            return None
        c["unit"], old_w, new_w = swap[c["unit"]]
        c["text"] = re.sub(old_w, new_w, c["text"], count=1)
    elif kind == "wrong period":
        idx = next((k for k, q in enumerate(company["quarters"]) if q["fiscal_label"] == c.get("period")), None)
        if not idx:
            return None
        c["period"] = company["quarters"][idx - 1]["fiscal_label"]
    elif kind == "QoQ/YoY swapped":
        m = c.get("metric", "")
        if not m.endswith(("_qoq", "_yoy")):
            return None
        c["metric"] = m[:-4] + ("_yoy" if m.endswith("_qoq") else "_qoq")
    if _is_true(c, company):
        return None  # the "error" happens to be correct (e.g. same value in both periods): not an error
    return c


def injection_results(notes) -> list[dict]:
    counts = {k: {"injected": 0, "caught": 0} for k in INJECTIONS}
    for company, i, note in notes:
        paras = _with_continuations(note.get("paragraphs", []))
        for s in note["sections"]:
            for claim in s["claims"]:
                for kind in INJECTIONS:
                    bad = inject(claim, company, kind)
                    if bad is None:
                        continue
                    counts[kind]["injected"] += 1
                    if verify_claim(bad, company, paras)["verification"]["status"] != "verified":
                        counts[kind]["caught"] += 1
    return [{"error_type": k, **v, "catch_rate_pct": round(v["caught"] / v["injected"] * 100, 1) if v["injected"] else None}
            for k, v in counts.items()]


# ---------------------------------------------------------------- hand grading

def write_hand_grade_sample(df: pd.DataFrame, n: int = 20, seed: int = 7, path: Path = HAND_GRADE_CSV) -> bool:
    """Write a random sample to CSV with blank grade columns. Never overwrites a file that has grades."""
    if path.exists() and read_hand_grades(path) is not None and read_hand_grades(path)["my_grade"].astype(str).str.strip().ne("").any():
        return False
    sample = df.sample(n=min(n, len(df)), random_state=seed).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HAND_GRADE_COLUMNS)
        w.writeheader()
        for k, r in sample.iterrows():
            w.writerow({"sample_id": k + 1, "ticker": r.ticker, "quarter": r.quarter, "section": r.section,
                        "claim_type": r.claim_type, "text": r.text, "cited_source": r.source,
                        "verifier_status": r.status, "verifier_reason": r.reason, "my_grade": "", "my_notes": ""})
    return True


def read_hand_grades(path: Path = HAND_GRADE_CSV) -> pd.DataFrame | None:
    if not path.exists():
        return None
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def hand_grade_summary(path: Path = HAND_GRADE_CSV) -> dict | None:
    """Agreement between the verifier and the human grade ('correct' / 'incorrect' / 'unsure')."""
    g = read_hand_grades(path)
    if g is None:
        return None
    graded = g[g.my_grade.str.strip().str.lower().isin(["correct", "incorrect"])]
    if graded.empty:
        return {"graded": 0}
    human_ok = graded.my_grade.str.strip().str.lower() == "correct"
    verifier_ok = graded.verifier_status.isin(["verified", "sourced"])
    return {
        "graded": int(len(graded)),
        "human_correct_pct": round(human_ok.mean() * 100, 1),
        "agreement_pct": round((human_ok == verifier_ok).mean() * 100, 1),
        "verifier_passed_but_human_wrong": int((verifier_ok & ~human_ok).sum()),
        "verifier_flagged_but_human_right": int((~verifier_ok & human_ok).sum()),
    }


# ---------------------------------------------------------------- all results

def compute(notes=None) -> dict:
    notes = notes if notes is not None else load_notes()
    df = claims_frame(notes)
    num = df[df.claim_type == "numeric"]
    quotes = df[df.claim_type == "quote"]
    fails = df[df.failure != ""]
    examples = {}
    for cat, g in fails.groupby("failure"):
        examples[cat] = g.head(3)[["ticker", "quarter", "text", "reason"]].to_dict("records")
    usage = [u for _, _, n in notes for u in n.get("usage", [])]
    return {
        "company_quarters": len(notes), "tickers": sorted({c["ticker"] for c, _, _ in notes}),
        "claims": int(len(df)),
        "status_counts": df.status.value_counts().to_dict(),
        "numeric": {"claims": int(len(num)), "verified_pct": round((num.status == "verified").mean() * 100, 1) if len(num) else None,
                    "mismatch_pct": round((num.status == "mismatch").mean() * 100, 1) if len(num) else None},
        "quote": {"claims": int(len(quotes)), "match_pct": round((quotes.status == "verified").mean() * 100, 1) if len(quotes) else None},
        "numeric_by_metric_type": rate_table(num, "metric_type"),
        "numeric_by_calendar": rate_table(num, "fye_group"),
        "numeric_q4_vs_other": rate_table(num.assign(q=num.derived_q4.map({True: "Q4 (derived from 10-K)", False: "Q1-Q3"})), "q"),
        "all_by_claim_type": rate_table(df, "claim_type"),
        "failures": fails.failure.value_counts().to_dict(),
        "failure_examples": examples,
        "injection": injection_results(notes),
        "tokens": {"input": sum(u.get("input_tokens", 0) for u in usage), "output": sum(u.get("output_tokens", 0) for u in usage)},
    }
