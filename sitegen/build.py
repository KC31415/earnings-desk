"""Build the static site (GitHub Pages) from the JSON data in the repo. No network, no AI.

    python -m sitegen.build [--out _site] [--sp500-limit N]
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import markdown as md
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from plotly.offline import get_plotlyjs

from earnings_desk import config
from earnings_desk.analytics import GROWTH_METRICS, MARGINS, fmt_money, fmt_pct, growth_frame, margins_frame, values_frame
from earnings_desk.concepts import METRIC_ORDER, METRICS, label
from earnings_desk.landing import GROUP_ORDER, cq_order, findings, latest_common_cq, panel, snapshot
from earnings_desk.normalize import normalize
from earnings_desk.notes_service import get_note, note_quarters
from earnings_desk.scorecard import FAILURE_EXPLAIN, HAND_GRADE_CSV, hand_grade_summary
from earnings_desk.sec import filing_url, load_raw
from earnings_desk.takes import load_take

HERE = Path(__file__).parent
PORTFOLIO = "https://kechunwu.github.io"
REPO = "https://github.com/kechunwu/earnings-desk"
COLORS = dict(zip(GROUP_ORDER, ["#E45756", "#F58518", "#4C78A8", "#B279A2", "#9D9D9D"]))
ICON = {"verified": "✓", "mismatch": "⚠", "sourced": "◦", "unsupported": "✗"}
STATUS_LABEL = {"verified": "verified", "mismatch": "mismatch", "sourced": "sourced, not machine-verifiable",
                "unsupported": "unsupported: nothing checkable"}
_chart_id = 0


def chart(fig: go.Figure, height: int = 380) -> Markup:
    global _chart_id
    _chart_id += 1
    fig.update_layout(height=height, margin=dict(l=50, r=20, t=50, b=40), template="plotly_white",
                      font=dict(family="Inter, system-ui, sans-serif", size=13),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    return Markup(fig.to_html(full_html=False, include_plotlyjs=False, div_id=f"c{_chart_id}",
                              config={"displayModeBar": False, "responsive": True}))


def money(v, key="revenue"):
    unit = METRICS[key].unit if key in METRICS else "USD"
    return fmt_money(v, unit)


def pct1(v):
    return "—" if v is None or pd.isna(v) else f"{v * 100:.1f}%"


# ---------------------------------------------------------------- company pages

def company_context(c: dict) -> dict:
    quarters = c["quarters"][-config.QUARTERS_SHOWN:]
    ctx = {"c": {**c, "fye": f"{c['fiscal_year_end'][:2]}/{c['fiscal_year_end'][2:]}"}, "coverage": c["coverage"]}
    if c["coverage"]["blocked"] or not quarters:
        ctx["coverage"] = dict(c["coverage"], blocked=True, reasons=c["coverage"]["reasons"] or ["No quarterly XBRL data found."])
        return ctx
    ctx["cols"] = [(q["fiscal_label"], q["period_end"]) for q in quarters]
    rows = []
    for k in METRIC_ORDER:
        cells, any_val = [], False
        for q in quarters:
            m = q["metrics"].get(k)
            if not m:
                cells.append({"text": "—", "flags": "", "title": ""})
                continue
            any_val = True
            flags = ("ᵈ" if m["derived"] else "") + ("ʳ" if m.get("restated") else "") + ("ᵖ" if m.get("partial") else "")
            title = m["concept"] + (f" · {m['derivation']}" if m.get("derivation") else "")
            cells.append({"text": money(m["value"], k), "flags": flags, "title": title})
        if any_val:
            rows.append({"label": label(k), "cells": cells})
    ctx["metric_rows"] = rows

    n = len(quarters)
    g, m = growth_frame(c).tail(n), margins_frame(c).tail(n)
    ctx["growth_rows"] = [(f"{label(k)} {kind.upper()}", [fmt_pct(v) for v in g[f"{k}_{kind}"]])
                          for k in GROWTH_METRICS for kind in ("yoy", "qoq") if g[f"{k}_{kind}"].notna().any()]
    ctx["margin_rows"] = [(name, [pct1(v) for v in m[name]]) for name in MARGINS if m[name].notna().any()]

    vf = values_frame(c).tail(n)
    x = [f"{a}<br>{b}" for a, b in zip(vf.fiscal_label, vf.calendar_label)]
    fig = go.Figure()
    fig.add_bar(x=x, y=vf.revenue / 1e9, name="Revenue ($B)", marker_color="#4C78A8")
    if g.revenue_yoy.notna().any():
        fig.add_scatter(x=x, y=g.revenue_yoy * 100, name="YoY growth (%)", yaxis="y2", mode="lines+markers", line_color="#E45756")
    fig.update_layout(title="Revenue and YoY growth", yaxis=dict(title="$B"),
                      yaxis2=dict(title="%", overlaying="y", side="right", showgrid=False), legend=dict(orientation="h", y=-0.25))
    charts = {"revenue": chart(fig)}
    fig = go.Figure()
    for name in ["Gross margin", "Operating margin", "FCF margin"]:
        if m[name].notna().any():
            fig.add_scatter(x=x, y=m[name] * 100, name=name, mode="lines+markers")
    fig.update_layout(title="Margins (%)", legend=dict(orientation="h", y=-0.25))
    charts["margins"] = chart(fig)
    if vf.rpo.notna().any() or vf.deferred_revenue.notna().any():
        fig = go.Figure()
        if vf.rpo.notna().any():
            fig.add_bar(x=x, y=vf.rpo / 1e9, name="RPO ($B)")
        if vf.deferred_revenue.notna().any():
            fig.add_bar(x=x, y=vf.deferred_revenue / 1e9, name="Deferred revenue ($B)")
        fig.update_layout(title="Backlog: RPO and deferred revenue", barmode="group", legend=dict(orientation="h", y=-0.25))
        charts["backlog"] = chart(fig, 320)
    ctx["charts"] = charts
    ctx["sources"] = [{"quarter": q["fiscal_label"], "calendar": q["calendar_label"], "end": q["period_end"],
                       "form": q["sources"].get("filing_form", "—"), "filed": q["sources"].get("filing_date", "—"),
                       "url": q["sources"].get("filing_url"), "k8": q["sources"].get("earnings_8k_url")}
                      for q in reversed(quarters)]
    return ctx


# ---------------------------------------------------------------- notes

def _fmt_actual(v: dict) -> str:
    a = v.get("actual")
    if a is None:
        return ""
    return f"{a:.2f}%" if v.get("actual_unit") == "percent" else (
        f"${a:,.2f}" if v.get("actual_unit") == "usd_per_share" else fmt_money(a))


def note_context(note: dict, cik: int) -> dict:
    paras = {p["id"].upper(): p["text"] for p in note.get("paragraphs", [])}
    from earnings_desk.note import SECTION_TITLES

    order = {t: i for i, t in enumerate(SECTION_TITLES)}
    sections = []
    for sec in sorted(note["sections"], key=lambda s: order.get(s["title"], 99)):
        claims = []
        for c in sec["claims"]:
            v = c["verification"]
            ev = v.get("evidence") or {}
            pid = (ev.get("paragraph") or (c.get("source") or {}).get("ref") or "").upper()
            claims.append({
                "text": c["text"], "status": v["status"], "icon": ICON[v["status"]], "status_label": STATUS_LABEL[v["status"]],
                "real": _fmt_actual(v) if v["status"] == "mismatch" and v.get("actual") is not None else "",
                "reason": v.get("reason") or v.get("note") or "",
                "inputs": [{"concept": e["concept"], "period": e["period"], "end": e["period_end"],
                            "value": f"${e['value']:,.2f}" if e["metric"] == "eps_diluted" else fmt_money(e["value"]),
                            "derivation": e.get("derivation") if e.get("derived") else "", "accn": e["accn"],
                            "url": filing_url(cik, e["accn"]) if e.get("accn") else ""} for e in ev.get("inputs", [])],
                "formula": ev.get("formula", ""), "actual": _fmt_actual(v),
                "para_id": pid if pid in paras else "", "para": paras.get(pid, "")[:1500],
                "metric": c.get("metric"), "period": c.get("period"), "value": c.get("value"), "unit": c.get("unit") or "",
                "cites": f"{c['source']['type']}:{c['source']['ref']}",
            })
        sections.append({"title": sec["title"], "claims": claims})
    src = note.get("source", {})
    return {"fiscal_label": note["fiscal_label"], "calendar_label": note["calendar_label"], "period_end": note["period_end"],
            "summary": note["summary"], "sections": sections, "accn": src.get("earnings_8k_accn"),
            "press_url": src.get("exhibit_url") or src.get("filing_index"), "filing_url": src.get("periodic_filing"),
            "models": f"{note['models']['note']} (writing), {note['models']['extraction']} (paragraph selection)",
            "generated": note["generated_at"][:10]}


# ---------------------------------------------------------------- landing / scorecard / research charts

def landing_context(p: pd.DataFrame) -> dict:
    end = latest_common_cq(p)
    y, q = int(end[2:6]), int(end[-1])
    quarters = [c for c in cq_order(p.calendar_label) if (int(c[2:6]), int(c[-1])) <= (y, q)][-8:]
    d = p[p.calendar_label.isin(quarters)].copy()
    d["cq_idx"] = d.calendar_label.map({c: i for i, c in enumerate(quarters)})
    d = d.sort_values(["ticker", "cq_idx"])
    co = {"calendar_label": quarters, "group": GROUP_ORDER}
    lg = dict(orientation="h", y=-0.3)
    charts = {}
    fig = px.line(d, x="calendar_label", y=d.revenue_yoy * 100, color="group", line_group="ticker", markers=True,
                  hover_name="ticker", color_discrete_map=COLORS, category_orders=co,
                  labels={"y": "Revenue growth YoY (%)", "calendar_label": "", "group": ""}, title="Revenue growth, year over year")
    fig.update_layout(legend=lg)
    charts["growth"] = chart(fig, 440)
    fig = px.line(d, x="calendar_label", y=d["Operating margin"] * 100, color="group", line_group="ticker", markers=True,
                  hover_name="ticker", color_discrete_map=COLORS, category_orders=co,
                  labels={"y": "GAAP operating margin (%)", "calendar_label": "", "group": ""}, title="GAAP operating margin")
    fig.update_layout(legend=lg, yaxis_range=[-60, 80])
    off = d[d["Operating margin"] < -0.6].ticker.unique()
    if len(off):
        fig.add_annotation(text=f"{', '.join(off)} below −60%, off the chart", showarrow=False, xref="paper", yref="paper",
                           x=0, y=0.02, xanchor="left", font=dict(size=11, color="gray"))
    charts["margin"] = chart(fig, 440)
    snap = snapshot(p)
    s = snap.dropna(subset=["revenue_yoy", "FCF margin"])
    fig = px.scatter(s, x=s.revenue_yoy * 100, y=s["FCF margin"] * 100, color="group", text="ticker", hover_name="name",
                     color_discrete_map=COLORS, category_orders={"group": GROUP_ORDER},
                     labels={"x": "Revenue growth YoY (%)", "y": "Free cash flow margin (%)", "group": ""},
                     title="Growth vs cash generation, latest quarter")
    fig.update_traces(textposition="top center", marker_size=11)
    fig.update_layout(legend=lg)
    charts["scatter"] = chart(fig, 440)
    s = snap.dropna(subset=["rpo_yoy"]).sort_values("rpo_yoy")
    fig = px.bar(s, x=s.rpo_yoy * 100, y="ticker", color="group", orientation="h", color_discrete_map=COLORS,
                 category_orders={"group": GROUP_ORDER}, labels={"x": "RPO growth YoY (%)", "ticker": "", "group": ""},
                 title="Backlog: remaining performance obligations, YoY")
    fig.update_layout(showlegend=False)
    charts["rpo"] = chart(fig, 440)
    head = ["Company", "Group", "Quarter", "Revenue", "Revenue YoY", "Op. margin", "FCF margin", "RPO YoY", "Deferred rev. YoY"]
    rows = [[r.ticker, r.group, f"{r.fiscal_label} · {r.calendar_label}", fmt_money(r.revenue), fmt_pct(r.revenue_yoy),
             pct1(r["Operating margin"]), pct1(r["FCF margin"]), fmt_pct(r.rpo_yoy),
             fmt_pct(r.deferred_revenue_yoy) + ("ᵖ" if r.dr_partial and pd.notna(r.deferred_revenue_yoy) else "")]
            for _, r in snap.iterrows()]
    import re

    fs = [re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(f, quote=False)) for f in findings(p)]
    return {"charts": charts, "quarters": quarters, "snap_head": head, "snap_rows": rows, "findings": fs}


def scorecard_charts(r: dict) -> dict:
    out = {}
    df = pd.DataFrame(r["numeric_by_metric_type"])
    out["metric"] = chart(px.bar(df, x="metric_type", y="verified_pct", text="claims", range_y=[0, 100],
                                 labels={"metric_type": "", "verified_pct": "% verified"},
                                 title="By metric type (bar label = claims)", color_discrete_sequence=["#4C78A8"]))
    df = pd.DataFrame(r["numeric_by_calendar"])
    out["calendar"] = chart(px.bar(df, x="fye_group", y="verified_pct", text="claims", range_y=[0, 100],
                                   labels={"fye_group": "", "verified_pct": "% verified"},
                                   title="By fiscal calendar (bar label = claims)", color_discrete_sequence=["#4C78A8"]))
    df = pd.DataFrame(r["all_by_claim_type"])
    df["sourced_pct"] = [100 - a - b - c if t == "qualitative" else 0 for t, a, b, c in
                         zip(df.claim_type, df.verified_pct, df.mismatch_pct, df.unsupported_pct)]
    fig = go.Figure()
    for col, name, color in [("verified_pct", "✓ verified", "#1b7f3b"), ("sourced_pct", "◦ sourced", "#9aa3b2"),
                             ("mismatch_pct", "⚠ mismatch", "#c0362c"), ("unsupported_pct", "✗ unsupported", "#b26a00")]:
        fig.add_bar(x=df[col], y=[f"{t} ({n})" for t, n in zip(df.claim_type, df.claims)], name=name, orientation="h",
                    marker_color=color)
    fig.update_layout(barmode="stack", title="Outcome by claim type (% of claims)", legend=dict(orientation="h", y=-0.25),
                      xaxis_range=[0, 100])
    out["types"] = chart(fig, 300)
    f = pd.Series(r["failures"]).sort_values()
    fig = px.bar(x=f.values, y=f.index, orientation="h", labels={"x": "claims", "y": ""}, title="Why claims failed")
    fig.update_traces(marker_color="#c0362c")
    out["failures"] = chart(fig, 300)
    df = pd.DataFrame(r["injection"])
    fig = px.bar(df, x="error_type", y="catch_rate_pct", text="injected", range_y=[0, 100],
                 labels={"error_type": "", "catch_rate_pct": "% caught"}, title="Injected errors caught (bar label = injected)",
                 color_discrete_sequence=["#1b7f3b"])
    out["injection"] = chart(fig, 340)
    return out


# ---------------------------------------------------------------- main

def load_companies(sp500_limit: int = 0) -> tuple[dict[str, dict], set[str]]:
    companies = {}
    for t in config.SHOWCASE_TICKERS:
        raw = load_raw(t)
        if raw:
            companies[t] = normalize(*raw, t)
    showcase = set(companies)
    files = sorted((config.DATA_DIR / "sp500").glob("*.json"))
    for f in files[: sp500_limit or None]:
        t = f.stem
        if t not in companies:
            companies[t] = json.loads(f.read_text())
    return companies, showcase


def build(out: Path, sp500_limit: int = 0) -> dict:
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]))
    built = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = {"portfolio": PORTFOLIO, "repo": REPO, "built": built}

    if out.exists():
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)
    (out / "company").mkdir()
    (out / "notes").mkdir()
    (out / "data").mkdir()
    for f in (HERE / "static").iterdir():
        shutil.copy(f, out / "assets" / f.name)
    (out / "assets" / "plotly.min.js").write_text(get_plotlyjs(), encoding="utf-8")
    (out / ".nojekyll").write_text("")

    def render(tpl: str, path: Path, root: str, **ctx):
        path.write_text(env.get_template(tpl).render(**base, root=root, **ctx), encoding="utf-8")

    def take_html(t: str):
        tk = load_take(t)
        return Markup(md.markdown(tk)) if tk else None

    companies, showcase = load_companies(sp500_limit)
    sc_path = config.DATA_DIR / "scorecard" / "results.json"
    sc = json.loads(sc_path.read_text()) if sc_path.exists() else None
    hg = hand_grade_summary()

    # Notes
    cards, note_rows = [], []
    for t in config.SHOWCASE_TICKERS:
        c = companies.get(t)
        if not c:
            continue
        notes = [n for i in note_quarters(c, 5) if (n := get_note(c, i))]
        if not notes:
            continue
        ctxs = [note_context(n, c["cik"]) for n in notes]
        render("note.html", out / "notes" / f"{t}.html", "../", title=f"{t} verified earnings note", active="notes",
               description=f"AI earnings note for {c['name']} with every claim checked against SEC filings.",
               ticker=t, name=c["name"], notes=ctxs, take=take_html(t), charts=False)
        claims = [cl for n in notes for s in n["sections"] for cl in s["claims"]]
        num = [cl for cl in claims if cl["claim_type"] == "numeric" and not cl["verification"].get("note")]
        quo = [cl for cl in claims if cl["claim_type"] == "quote"]
        ok = lambda xs: sum(x["verification"]["status"] == "verified" for x in xs)
        note_rows.append({"ticker": t, "name": c["name"], "latest": f"{notes[0]['fiscal_label']} ({notes[0]['calendar_label']})",
                          "n": len(notes), "numeric": f"{ok(num)}/{len(num)}", "quotes": f"{ok(quo)}/{len(quo)}",
                          "mismatch": sum(x["verification"]["status"] == "mismatch" for x in claims)})
        s0 = notes[0]["summary"]
        cards.append({"ticker": t, "name": c["name"], "quarter": notes[0]["fiscal_label"],
                      "verified": s0["numeric_verified"], "numeric": s0["numeric"]})
    render("notes_index.html", out / "notes" / "index.html", "../", title="Verified notes", active="notes",
           description="Verified AI earnings notes for 10 companies.", rows=note_rows, charts=False)

    # Company pages
    has_note = {r["ticker"] for r in note_rows}
    search = []
    for t, c in sorted(companies.items()):
        ctx = company_context(c)
        render("company.html", out / "company" / f"{t}.html", "../", title=f"{c['name']} ({t})", active="",
               description=f"Quarterly financials for {c['name']} from SEC filings.", note_link=t in has_note,
               take=take_html(t) if t in showcase else None, **ctx)
        search.append({"t": t, "n": c.get("name") or c.get("index_name") or "", "note": t in has_note})
    (out / "tickers.json").write_text(json.dumps(search, separators=(",", ":")))

    # Landing
    p = panel()
    render("index.html", out / "index.html", "", title="Is AI eating software? What the filings say", active="home",
           description="Ten software and AI companies' SEC filings, aligned by calendar quarter, plus AI earnings notes verified by code.",
           n_companies=len(companies), cards=cards, sc=sc or {"company_quarters": 0, "numeric": {"verified_pct": "—"}},
           **landing_context(p))

    # Scorecard + research
    if sc:
        injected = sum(i["injected"] for i in sc["injection"])
        caught = sum(i["caught"] for i in sc["injection"])
        catch_pct = f"{caught / injected:.1%}" if injected else "—"
        sch = scorecard_charts(sc)
        failures = [{"cat": k, "n": v, "explain": FAILURE_EXPLAIN.get(k, ""), "examples": sc["failure_examples"].get(k, [])}
                    for k, v in sorted(sc["failures"].items(), key=lambda x: -x[1])]
        render("scorecard.html", out / "scorecard.html", "", title="Accuracy scorecard", active="scorecard",
               description="How often AI earnings notes are right, measured against SEC filings.", r=sc, hg=hg,
               injected=injected, catch_pct=catch_pct, failures=failures, charts=sch)
        q4 = next((q for q in sc["numeric_q4_vs_other"] if q["q"].startswith("Q4")), {"verified_pct": "—", "claims": 0})
        qual = next((x for x in sc["all_by_claim_type"] if x["claim_type"] == "qualitative"), {"claims": 0})
        render("research.html", out / "research.html", "", title="Research note: can you trust an AI earnings note?",
               active="research", description="What a code verifier caught in 1,000 AI-written earnings claims.", r=sc, hg=hg,
               injected=injected, catch_pct=catch_pct, q4=q4, qual_share=round(qual["claims"] / sc["claims"] * 100),
               note_model=config.NOTE_MODEL, charts=sch)
    if HAND_GRADE_CSV.exists():
        shutil.copy(HAND_GRADE_CSV, out / "data" / "hand_grade.csv")
    return {"companies": len(companies), "notes": len(note_rows)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_site")
    ap.add_argument("--sp500-limit", type=int, default=0)
    a = ap.parse_args()
    res = build(Path(a.out), a.sp500_limit)
    print(f"Built {res['companies']} company pages and {res['notes']} note pages into {a.out}/")


if __name__ == "__main__":
    main()
