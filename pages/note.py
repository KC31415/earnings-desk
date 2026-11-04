"""Verified note: AI-written earnings note where every claim is checked by code."""
from __future__ import annotations

import html

import streamlit as st

from earnings_desk import config
from earnings_desk.analytics import fmt_money
from earnings_desk.data import load_company
from earnings_desk.note import SECTION_TITLES
from earnings_desk.notes_service import CapReached, create_note, get_note, note_quarters
from earnings_desk.sec import SECError
from earnings_desk.sec import filing_url
from earnings_desk.takes import load_take
from earnings_desk.ui import ticker_picker
from earnings_desk.store import NoteStore, daily_cap

ICON = {"verified": "✓", "mismatch": "⚠", "sourced": "◦", "unsupported": "✗"}
LABEL = {"verified": "verified", "mismatch": "mismatch", "sourced": "sourced, not machine-verifiable",
         "unsupported": "unsupported: nothing checkable"}
COLOR = {"verified": "green", "mismatch": "red", "sourced": "gray", "unsupported": "orange"}


@st.cache_data(ttl=6 * 3600, show_spinner="Reading SEC filings…")
def get_company(ticker: str):
    return load_company(ticker)


def fmt_actual(v: dict) -> str:
    a = v.get("actual")
    if a is None:
        return ""
    return f"{a:.2f}%" if v.get("actual_unit") == "percent" else (
        f"${a:,.2f}" if v.get("actual_unit") == "usd_per_share" else fmt_money(a))


def render_claim(c: dict, note: dict, cik: int, paras: dict):
    v = c["verification"]
    s = v["status"]
    line = f":{COLOR[s]}[{ICON[s]}] {c['text']}"
    if s == "mismatch" and v.get("actual") is not None:
        line += f"  \n:red[**Source says {fmt_actual(v)}**]"
    st.markdown(line)
    with st.expander(f"{ICON[s]} {LABEL[s]} · source", expanded=False):
        if v.get("reason"):
            st.markdown(f"**Why:** {v['reason']}")
        ev = v.get("evidence") or {}
        for inp in ev.get("inputs", []):
            link = filing_url(cik, inp["accn"]) if inp.get("accn") else ""
            der = f" · derived: {inp['derivation']}" if inp.get("derived") else ""
            val = f"${inp['value']:,.2f}" if inp["metric"] == "eps_diluted" else fmt_money(inp["value"])
            st.markdown(f"XBRL `{inp['concept']}` · {inp['period']} (ended {inp['period_end']}) = **{val}**{der} · "
                        f"[filing {inp['accn']}]({link})")
        if ev.get("formula"):
            st.caption(f"Recomputed as {ev['formula']} = {fmt_actual(v)}")
        pid = ev.get("paragraph") or (c.get("source") or {}).get("ref", "")
        if pid and pid.upper() in paras:
            url = note["source"].get("exhibit_url") or note["source"].get("filing_index")
            st.markdown(f"Press release [{pid}]({url}):")
            st.markdown(f"> {html.escape(paras[pid.upper()][:1500])}".replace("\n", "  \n> "))
        st.caption(f"Model's structured claim: metric `{c.get('metric') or '—'}`, period `{c.get('period') or '—'}`, "
                   f"value `{c.get('value') or '—'}` {c.get('unit') or ''}, cites {c['source']['type']}:{c['source']['ref']}")


st.title("Verified earnings note")
st.caption("Written by an AI model from SEC filings, then every claim is checked by plain code: numbers against "
           "XBRL data, quotes against the press release text. Failed claims are shown, never hidden or fixed.")

ticker = ticker_picker("note")
if not ticker:
    st.stop()

try:
    company, _ = get_company(ticker)
except SECError as e:
    st.error(str(e))
    st.stop()
if company["coverage"]["blocked"]:
    st.error("**Limited coverage for this filer type.** " + " ".join(company["coverage"]["reasons"]))
    st.stop()

qidx = note_quarters(company, 5)
if not qidx:
    st.warning("No earnings press release (8-K Item 2.02) matched to a quarter with XBRL data yet.")
    st.stop()
choice = st.selectbox("Quarter", qidx, format_func=lambda i: f"{company['quarters'][i]['fiscal_label']} "
                      f"({company['quarters'][i]['calendar_label']}, ended {company['quarters'][i]['period_end']})")

store = NoteStore()
note = get_note(company, choice, store)
if not note:
    st.info("No note for this quarter yet.")
    left = max(0, daily_cap() - store.used_today())
    if st.button(f"Generate verified note ({left} of {daily_cap()} left today)", disabled=left == 0, type="primary"):
        with st.spinner("Writing and verifying (about a minute)…"):
            try:
                note = create_note(company, choice, store)
            except CapReached:
                st.warning("Today's limit on new notes has been reached. Please try again tomorrow.")
            except Exception as e:  # show the error; nothing is cached on failure
                st.error(f"Could not generate the note: {e}")
    if left == 0:
        st.warning("Today's limit on new notes has been reached. Please try again tomorrow.")
    if not note:
        st.stop()

take = load_take(company["ticker"])
if take:
    with st.container(border=True):
        st.markdown("**Analyst's take** (Kechun Wu's own view, not AI-generated, not investment advice)")
        st.markdown(take)

s = note["summary"]
bs = s["by_status"]
m1, m2, m3, m4 = st.columns(4)
pct = s["numeric_verified_pct"]
m1.metric("Numeric claims verified", f"{s['numeric_verified']}/{s['numeric']}" + (f" ({pct:.0%})" if pct is not None else ""))
m2.metric("⚠ Mismatches", bs.get("mismatch", 0))
m3.metric("◦ Sourced (qualitative)", bs.get("sourced", 0))
m4.metric("✗ Unsupported", bs.get("unsupported", 0))
st.caption(f"{note['name']} · {note['fiscal_label']} ({note['calendar_label']}, ended {note['period_end']}) · "
           f"press release: [8-K {note['source']['earnings_8k_accn']}]({note['source'].get('filing_index')}) · "
           f"models: {note['models']['note']} (writing), {note['models']['extraction']} (paragraph selection) · "
           f"generated {note['generated_at'][:10]}")

paras = {p["id"].upper(): p["text"] for p in note.get("paragraphs", [])}
order = {t: k for k, t in enumerate(SECTION_TITLES)}
for sec in sorted(note["sections"], key=lambda x: order.get(x["title"], 99)):
    st.subheader(sec["title"])
    for c in sec["claims"]:
        render_claim(c, note, company["cik"], paras)

st.divider()
st.caption("✓ verified against XBRL (within rounding) or exact text match · ⚠ disagrees with the source (real value "
           "shown) · ◦ sourced to a cited paragraph, not machine-verifiable · ✗ nothing checkable. "
           "Earnings-call transcripts are not used. Not investment advice.")
