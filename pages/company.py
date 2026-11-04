"""Company numbers: last 8 quarters from SEC XBRL, with sources."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from earnings_desk import config
from earnings_desk.analytics import (GROWTH_METRICS, MARGINS, fmt_money, fmt_pct, growth_frame, margins_frame,
                                     values_frame)
from earnings_desk.concepts import METRIC_ORDER, METRICS, label
from earnings_desk.data import load_company
from earnings_desk.sec import SECError
from earnings_desk.takes import load_take
from earnings_desk.ui import ticker_picker

N = config.QUARTERS_SHOWN


@st.cache_data(ttl=6 * 3600, show_spinner="Reading SEC filings…")
def get_company(ticker: str):
    return load_company(ticker)


def unit_of(key: str) -> str:
    return METRICS[key].unit if key in METRICS else "USD"


def col_name(q: dict) -> str:
    return f"{q['fiscal_label']} · {q['period_end']}"


st.title("Company numbers")
st.caption("Quarterly figures straight from the SEC's structured XBRL data. No AI on this page.")

ticker = ticker_picker("company")
if not ticker:
    st.stop()

try:
    company, source = get_company(ticker)
except SECError as e:
    st.error(str(e))
    st.stop()

st.subheader(f"{company['name']} ({company['ticker']})")
fye = company["fiscal_year_end"]
st.caption(f"CIK {company['cik']} · fiscal year ends {fye[:2]}/{fye[2:]} · SIC {company.get('sic') or '—'} "
           f"{company.get('sic_description') or ''} · data: {'saved in repo' if source == 'repo' else 'live from SEC'}")

cov = company["coverage"]
if cov["reasons"]:
    head = "Limited coverage for this filer type." if cov["blocked"] else "Limited data."
    msg = f"**{head}** " + " ".join(cov["reasons"])
    (st.error if cov["blocked"] else st.warning)(msg)
if cov["blocked"]:
    st.stop()

take = load_take(company["ticker"])
if take:
    with st.container(border=True):
        st.markdown("**Analyst's take** (Kechun Wu's own view, not AI-generated, not investment advice)")
        st.markdown(take)

quarters = company["quarters"][-N:]
cols = [col_name(q) for q in quarters]

# ---- Key metrics table
st.markdown("#### Key metrics")
rows = {}
for k in METRIC_ORDER:
    cells = []
    for q in quarters:
        m = q["metrics"].get(k)
        if not m:
            cells.append("—")
            continue
        flags = ("ᵈ" if m["derived"] else "") + ("ʳ" if m["restated"] else "") + ("ᵖ" if m["partial"] else "")
        cells.append(fmt_money(m["value"], unit_of(k)) + flags)
    if any(c != "—" for c in cells):
        rows[label(k)] = cells
st.dataframe(pd.DataFrame(rows, index=cols).T, width="stretch")
st.caption("ᵈ derived (e.g. Q4 = full year − 9M YTD; cash-flow quarters from year-to-date totals) · "
           "ʳ restated: latest filed value shown, original kept · ᵖ current portion only. "
           "Columns: fiscal quarter · period end date.")

# ---- Growth and margins
g = growth_frame(company).tail(N)
m = margins_frame(company).tail(N)
t1, t2 = st.tabs(["Growth (QoQ / YoY)", "Margins"])
with t1:
    gt = {}
    for k in GROWTH_METRICS:
        for kind in ("yoy", "qoq"):
            s = g[f"{k}_{kind}"]
            if s.notna().any():
                gt[f"{label(k)} {kind.upper()}"] = [fmt_pct(v) for v in s]
    st.dataframe(pd.DataFrame(gt, index=cols).T, width="stretch")
with t2:
    mt = {name: [f"{v * 100:.1f}%" if pd.notna(v) else "—" for v in m[name]] for name in MARGINS if m[name].notna().any()}
    st.dataframe(pd.DataFrame(mt, index=cols).T, width="stretch")

# ---- Charts
vf = values_frame(company).tail(N)
x = [f"{a}<br>{b}" for a, b in zip(vf.fiscal_label, vf.calendar_label)]
ch1, ch2 = st.columns(2)
with ch1:
    fig = go.Figure()
    fig.add_bar(x=x, y=vf.revenue / 1e9, name="Revenue ($B)", marker_color="#4C78A8")
    if g.revenue_yoy.notna().any():
        fig.add_scatter(x=x, y=g.revenue_yoy * 100, name="YoY growth (%)", yaxis="y2",
                        mode="lines+markers", line_color="#E45756")
    fig.update_layout(title="Revenue and YoY growth", height=360, margin=dict(t=40, b=10),
                      yaxis=dict(title="$B"), yaxis2=dict(title="%", overlaying="y", side="right", showgrid=False),
                      legend=dict(orientation="h", y=-0.25))
    st.plotly_chart(fig, width="stretch")
with ch2:
    fig = go.Figure()
    for name in ["Gross margin", "Operating margin", "FCF margin"]:
        if m[name].notna().any():
            fig.add_scatter(x=x, y=m[name] * 100, name=name, mode="lines+markers")
    fig.update_layout(title="Margins (%)", height=360, margin=dict(t=40, b=10), legend=dict(orientation="h", y=-0.25))
    st.plotly_chart(fig, width="stretch")

if vf.rpo.notna().any() or vf.deferred_revenue.notna().any():
    fig = go.Figure()
    if vf.rpo.notna().any():
        fig.add_bar(x=x, y=vf.rpo / 1e9, name="RPO ($B)")
    if vf.deferred_revenue.notna().any():
        fig.add_bar(x=x, y=vf.deferred_revenue / 1e9, name="Deferred revenue ($B)")
    fig.update_layout(title="Backlog: RPO and deferred revenue", height=320, barmode="group",
                      margin=dict(t=40, b=10), legend=dict(orientation="h", y=-0.25))
    st.plotly_chart(fig, width="stretch")

# ---- Sources
st.markdown("#### Source filings")
src = pd.DataFrame([{
    "Quarter": q["fiscal_label"], "Calendar": q["calendar_label"], "Period end": q["period_end"],
    "Form": q["sources"].get("filing_form", "—"), "Filed": q["sources"].get("filing_date", "—"),
    "Filing": q["sources"].get("filing_url"), "Earnings 8-K": q["sources"].get("earnings_8k_url"),
} for q in reversed(quarters)])
st.dataframe(src, hide_index=True, width="stretch", column_config={
    "Filing": st.column_config.LinkColumn(display_text="open"),
    "Earnings 8-K": st.column_config.LinkColumn(display_text="open"),
})

with st.expander("Where each number comes from (XBRL concept, filing, derivation)"):
    detail = []
    for q in reversed(quarters):
        for k in METRIC_ORDER:
            v = q["metrics"].get(k)
            if v:
                detail.append({
                    "Quarter": q["fiscal_label"], "Metric": label(k), "Value": fmt_money(v["value"], unit_of(k)),
                    "Concept": v["concept"], "Accession": v["accn"], "Form": v["form"], "Filed": v["filed"],
                    "Derivation": v["derivation"] or "", "Original value": fmt_money(v["original_value"], unit_of(k)) if v["restated"] else "",
                })
    st.dataframe(pd.DataFrame(detail), hide_index=True, width="stretch")
