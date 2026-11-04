"""Landing page: Is AI eating software? What the filings say."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from earnings_desk.analytics import fmt_money
from earnings_desk.landing import GROUP_ORDER, cq_order, findings, latest_common_cq, panel, snapshot

COLORS = dict(zip(GROUP_ORDER, ["#E45756", "#F58518", "#4C78A8", "#B279A2", "#9D9D9D"]))


@st.cache_data(ttl=6 * 3600, show_spinner="Reading SEC filings…")
def get_panel():
    return panel()


st.title("Is AI eating software? What the filings say.")
st.markdown(
    "In 2026 software stocks sold off on fears that AI would replace SaaS (the \"SaaSpocalypse\"), even as earnings "
    "estimates rose. This page lines up ten companies' own SEC filings by calendar quarter so you can check the story "
    "against reported numbers. Every figure comes from XBRL data in 10-Qs and 10-Ks. Charts only: no investment "
    "advice, ratings or price targets.")

p = get_panel()
if p.empty:
    st.warning("No data yet. Run the fetch workflow.")
    st.stop()

end = latest_common_cq(p)
y, q = int(end[2:6]), int(end[-1])
quarters = [c for c in cq_order(p.calendar_label) if (int(c[2:6]), int(c[-1])) <= (y, q)][-8:]
st.caption(f"Calendar quarters {quarters[0]} to {end} (the latest quarter at least 8 of 10 companies have reported). "
           "Quarters are mapped to calendar time by their midpoint, so a February-to-April fiscal quarter counts as "
           "calendar Q1. Groups follow how the selloff framed each company, not this app's view.")

st.subheader("What the filings say")
for f in findings(p):
    st.markdown(f"- {f}")

d = p[p.calendar_label.isin(quarters)].copy()
d["cq_idx"] = d.calendar_label.map({c: i for i, c in enumerate(quarters)})
d = d.sort_values(["ticker", "cq_idx"])

c1, c2 = st.columns(2)
with c1:
    fig = px.line(d, x="calendar_label", y=d.revenue_yoy * 100, color="group", line_group="ticker", markers=True,
                  hover_name="ticker", color_discrete_map=COLORS, category_orders={"calendar_label": quarters, "group": GROUP_ORDER},
                  labels={"y": "Revenue growth YoY (%)", "calendar_label": "", "group": ""},
                  title="Revenue growth, year over year")
    fig.update_layout(height=420, legend=dict(orientation="h", y=-0.2))
    st.plotly_chart(fig, width="stretch")
with c2:
    fig = px.line(d, x="calendar_label", y=d["Operating margin"] * 100, color="group", line_group="ticker", markers=True,
                  hover_name="ticker", color_discrete_map=COLORS, category_orders={"calendar_label": quarters, "group": GROUP_ORDER},
                  labels={"y": "GAAP operating margin (%)", "calendar_label": "", "group": ""},
                  title="GAAP operating margin")
    fig.update_layout(height=420, legend=dict(orientation="h", y=-0.2), yaxis_range=[-60, 80])
    off = d[d["Operating margin"] < -0.6].ticker.unique()
    if len(off):
        fig.add_annotation(text=f"{', '.join(off)} below −60%, off the chart (see its company page)", showarrow=False,
                           xref="paper", yref="paper", x=0, y=0.02, xanchor="left", font=dict(size=11, color="gray"))
    st.plotly_chart(fig, width="stretch")

snap = snapshot(p)
c3, c4 = st.columns(2)
with c3:
    s = snap.dropna(subset=["revenue_yoy", "FCF margin"])
    fig = px.scatter(s, x=s.revenue_yoy * 100, y=s["FCF margin"] * 100, color="group", text="ticker",
                     color_discrete_map=COLORS, category_orders={"group": GROUP_ORDER},
                     labels={"x": "Revenue growth YoY (%)", "y": "Free cash flow margin (%)", "group": ""},
                     title="Growth vs cash generation, latest quarter")
    fig.update_traces(textposition="top center", marker_size=11)
    fig.update_layout(height=420, legend=dict(orientation="h", y=-0.2))
    st.plotly_chart(fig, width="stretch")
with c4:
    s = snap.dropna(subset=["rpo_yoy"]).sort_values("rpo_yoy")
    fig = px.bar(s, x=s.rpo_yoy * 100, y="ticker", color="group", orientation="h", color_discrete_map=COLORS,
                 category_orders={"group": GROUP_ORDER},
                 labels={"x": "RPO growth YoY (%)", "ticker": "", "group": ""},
                 title="Backlog: remaining performance obligations, YoY")
    fig.update_layout(height=420, legend=dict(orientation="h", y=-0.2), showlegend=False)
    st.plotly_chart(fig, width="stretch")

st.subheader("Latest quarter, side by side")
tbl = pd.DataFrame({
    "Company": snap.ticker, "Group": snap.group, "Quarter": snap.fiscal_label + " · " + snap.calendar_label,
    "Revenue": [fmt_money(v) for v in snap.revenue],
    "Revenue YoY": [f"{v:+.1%}" if pd.notna(v) else "—" for v in snap.revenue_yoy],
    "Op. margin": [f"{v:.1%}" if pd.notna(v) else "—" for v in snap["Operating margin"]],
    "FCF margin": [f"{v:.1%}" if pd.notna(v) else "—" for v in snap["FCF margin"]],
    "RPO YoY": [f"{v:+.1%}" if pd.notna(v) else "—" for v in snap.rpo_yoy],
    "Deferred rev. YoY": [(f"{v:+.1%}" + ("ᵖ" if part else "")) if pd.notna(v) else "—"
                          for v, part in zip(snap.deferred_revenue_yoy, snap.dr_partial)],
})
st.dataframe(tbl, hide_index=True, width="stretch")
st.caption("ᵖ current portion of deferred revenue only. NDAQ revenue is total revenue as tagged, before "
           "transaction-based expenses, so its margins aren't comparable to the software companies. "
           "See each company's page for sources, and the Verified note page for an AI-written note where every "
           "number is checked against these filings.")
