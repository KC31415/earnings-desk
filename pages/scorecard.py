"""Accuracy scorecard: how often the verified note is right, and whether the verifier catches errors."""
from __future__ import annotations

import json

import pandas as pd
import plotly.express as px
import streamlit as st

from earnings_desk.scorecard import HAND_GRADE_CSV, OUT_DIR, hand_grade_summary, read_hand_grades

EXPLAIN = {
    "rounding at the boundary": (
        "The model rounded a number the wrong way at the edge, e.g. writing 21.9% when the true value is 21.846%. "
        "The error is tiny, but the verifier is strict on purpose: a stated number must be a correct rounding of "
        "the filed value. Early notes were partly caused by our own inputs (growth rates sent to the model at 2 "
        "decimals, which it then rounded again); inputs now carry 4 decimals."),
    "quote paraphrased, not quoted": (
        "The model was asked for a direct quote but wrote a paraphrase (\"the CEO described the quarter as strong\"). "
        "Nothing false is said, but a reader can't see management's actual words, so it doesn't count as a verified quote."),
    "quote not in cited paragraph": "The quoted words don't appear in the paragraph the model cited.",
    "sentence and structured value disagree": (
        "The sentence shows a different number from the structured value the model attached to it, so the reader "
        "would see an unchecked number."),
    "number not in cited paragraph": (
        "A number (often in a qualitative sentence, e.g. \"RPO was flat at $4.9 billion\") doesn't appear in the "
        "paragraph the model cited. The number may even be right, but the citation doesn't support it."),
    "wrong value / unit / period": "The number disagrees with the SEC's XBRL data by more than rounding.",
    "sign / direction wrong": "The magnitude is right but the sentence says growth where the filing shows a decline (or vice versa).",
    "nothing checkable (bad metric, period or citation)": "The claim cites something that doesn't exist, so it can't be checked.",
}

st.title("Accuracy scorecard")
st.caption("How often the AI note is right, measured by code against SEC filings, and how often the verifier "
           "catches deliberately injected errors. Computed offline by `scripts/scorecard.py`; no AI is used to grade.")

path = OUT_DIR / "results.json"
if not path.exists():
    st.info("The scorecard hasn't been computed yet. Run `python -m scripts.scorecard`.")
    st.stop()
r = json.loads(path.read_text())

inj = pd.DataFrame(r["injection"])
inj_total = inj.injected.sum()
overall_catch = inj.caught.sum() / inj_total if inj_total else None

c1, c2, c3, c4 = st.columns(4)
c1.metric("Company-quarters", r["company_quarters"], f"{len(r['tickers'])} tickers", delta_color="off")
c2.metric("Numeric claims verified", f"{r['numeric']['verified_pct']}%", f"of {r['numeric']['claims']}", delta_color="off")
c3.metric("Quotes matched verbatim", f"{r['quote']['match_pct']}%", f"of {r['quote']['claims']}", delta_color="off")
c4.metric("Injected errors caught", f"{overall_catch:.1%}" if overall_catch is not None else "—",
          f"of {inj_total}", delta_color="off")
st.caption(f"{r['claims']} claims in total: " + ", ".join(f"{v} {k}" for k, v in r["status_counts"].items()) + ".")

st.subheader("Numeric accuracy")
a, b = st.columns(2)
with a:
    df = pd.DataFrame(r["numeric_by_metric_type"])
    fig = px.bar(df, x="metric_type", y="verified_pct", text="claims", range_y=[0, 100],
                 labels={"metric_type": "", "verified_pct": "% verified"}, title="By metric type (bar label = claims)")
    st.plotly_chart(fig, width="stretch")
with b:
    df = pd.DataFrame(r["numeric_by_calendar"])
    fig = px.bar(df, x="fye_group", y="verified_pct", text="claims", range_y=[0, 100],
                 labels={"fye_group": "", "verified_pct": "% verified"}, title="By fiscal calendar (bar label = claims)")
    st.plotly_chart(fig, width="stretch")
st.dataframe(pd.DataFrame(r["numeric_q4_vs_other"]).rename(columns={"q": "quarter type"}), hide_index=True, width="stretch")

st.subheader("Does the verifier catch errors?")
st.markdown("Each verified claim is corrupted in a known way and re-checked. Injections that happen to produce a "
            "true statement (e.g. the same value in both periods) are skipped.")
st.dataframe(inj.rename(columns={"error_type": "injected error", "catch_rate_pct": "caught %"}), hide_index=True,
             width="stretch")
st.caption("Caveat: injection tests check the verifier's own logic (units, periods, rounding, quote matching) "
           "against the same XBRL data it uses. They cannot reveal a wrong number in the SEC data itself, or a "
           "misleading qualitative claim. That's what the hand-graded sample below is for.")

st.subheader("Hand-graded sample")
hg = hand_grade_summary()
if hg and hg.get("graded"):
    h1, h2, h3 = st.columns(3)
    h1.metric("Claims graded by hand", hg["graded"])
    h2.metric("Correct per human grader", f"{hg['human_correct_pct']}%")
    h3.metric("Verifier agrees with human", f"{hg['agreement_pct']}%")
    st.caption(f"Verifier passed but human found wrong: {hg['verifier_passed_but_human_wrong']} · "
               f"verifier flagged but human found right: {hg['verifier_flagged_but_human_right']}.")
    st.dataframe(read_hand_grades(), hide_index=True, width="stretch")
else:
    st.info("Not graded yet. A random sample of 20 claims is exported to `data/scorecard/hand_grade.csv`. "
            "Fill `my_grade` with correct / incorrect / unsure by checking each claim against the filing, then "
            "commit the file; this page picks the grades up automatically.")
    if HAND_GRADE_CSV.exists():
        st.download_button("Download the sample CSV", HAND_GRADE_CSV.read_bytes(), "hand_grade.csv", "text/csv")

st.subheader("Where it fails and why")
if not r["failures"]:
    st.write("No failures in this run.")
for cat, n in sorted(r["failures"].items(), key=lambda x: -x[1]):
    st.markdown(f"**{cat}** ({n})  \n{EXPLAIN.get(cat, '')}")
    for ex in r["failure_examples"].get(cat, []):
        st.markdown(f"- {ex['ticker']} {ex['quarter']}: “{ex['text']}” · _{ex['reason']}_")
st.markdown("**Verifier bugs found by reading the results.** Reviewing every flagged claim turned up false alarms "
            "that were the verifier's fault, not the model's, and were fixed: sentences split across HTML blocks; "
            "quotes checked against the value field instead of the words in quotation marks; and cash *outflows* "
            "read as a sign error (which alone made April fiscal-year companies look worse). Each fix comes with a "
            "test, and the injected-error catch rate is re-checked after every fix so the verifier isn't loosened.")
st.markdown("**What the verifier can't check.** Whether a qualitative claim (◦) is a fair reading of its paragraph, "
            "and whether the note leaves out something important. Those still need a human reader.")
