"""Cross-company data for the landing page: the 10 showcase tickers aligned by calendar quarter."""
from __future__ import annotations

import pandas as pd

from . import config
from .analytics import growth_frame, margins_frame, values_frame
from .normalize import normalize
from .sec import load_raw

# How the 2026 software selloff framed each company (a market narrative, not this app's view).
GROUPS = {
    "CRM": "Seen as AI-at-risk (SaaS apps)", "NOW": "Seen as AI-at-risk (SaaS apps)",
    "ADBE": "Seen as AI-at-risk (SaaS apps)",
    "SNOW": "Data & infrastructure software", "DDOG": "Data & infrastructure software",
    "NVDA": "Seen as AI winners", "MSFT": "Seen as AI winners", "PLTR": "Seen as AI winners",
    "AI": "AI-native app vendor", "NDAQ": "Exchange + fintech (comparison)",
}
GROUP_ORDER = ["Seen as AI-at-risk (SaaS apps)", "Data & infrastructure software", "Seen as AI winners",
               "AI-native app vendor", "Exchange + fintech (comparison)"]


def panel() -> pd.DataFrame:
    """One row per (ticker, quarter) with values, growth and margins."""
    rows = []
    for t in config.SHOWCASE_TICKERS:
        raw = load_raw(t)
        if not raw:
            continue
        c = normalize(*raw, t)
        v, g, m = values_frame(c), growth_frame(c), margins_frame(c)
        df = pd.concat([v, g.drop(columns=["fiscal_label", "calendar_label", "period_end"]),
                        m.drop(columns=["fiscal_label", "calendar_label", "period_end"])], axis=1)
        df["ticker"], df["name"], df["group"] = t, c["name"], GROUPS.get(t, "Other")
        df["dr_partial"] = [bool(q["metrics"].get("deferred_revenue", {}).get("partial")) for q in c["quarters"]]
        rows.append(df)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def cq_order(labels) -> list[str]:
    return sorted(set(labels), key=lambda s: (int(s[2:6]), int(s[-1])))


def latest_common_cq(p: pd.DataFrame, min_companies: int = 8) -> str:
    counts = p.groupby("calendar_label").ticker.nunique()
    ok = [cq for cq in cq_order(counts.index) if counts[cq] >= min_companies]
    return ok[-1]


def snapshot(p: pd.DataFrame) -> pd.DataFrame:
    """Each company's latest reported quarter."""
    return p.sort_values("period_end").groupby("ticker").tail(1).set_index("ticker").loc[
        [t for t in config.SHOWCASE_TICKERS if t in set(p.ticker)]].reset_index()


def group_medians(p: pd.DataFrame, col: str, quarters: list[str]) -> pd.DataFrame:
    d = p[p.calendar_label.isin(quarters)]
    return d.groupby(["group", "calendar_label"])[col].median().reset_index()


def findings(p: pd.DataFrame) -> list[str]:
    """Plain, code-generated observations (no LLM): group medians, then vs a year earlier."""
    out = []
    end = latest_common_cq(p)
    y, q = int(end[2:6]), int(end[-1])
    start = f"CY{y - 1} Q{q}"
    for grp in GROUP_ORDER[:3]:
        d = p[p.group == grp]
        a = d[d.calendar_label == start].revenue_yoy.median()
        b = d[d.calendar_label == end].revenue_yoy.median()
        if pd.notna(a) and pd.notna(b):
            verb = "accelerated" if b > a + 0.005 else "slowed" if b < a - 0.005 else "held steady"
            out.append(f"**{grp}**: median revenue growth {verb}, from {a:.1%} YoY in {start} to {b:.1%} in {end} "
                       f"({', '.join(sorted(d.ticker.unique()))}).")
    d = p[p.calendar_label == end]
    om = d.groupby("group")["Operating margin"].median()
    if GROUP_ORDER[0] in om and GROUP_ORDER[2] in om:
        out.append(f"Median GAAP operating margin in {end}: {om[GROUP_ORDER[0]]:.1%} for the AI-at-risk SaaS group vs "
                   f"{om[GROUP_ORDER[2]]:.1%} for the AI-winners group.")
    snap = snapshot(p)
    rpo = snap.dropna(subset=["rpo_yoy"])
    if len(rpo):
        up = rpo[rpo.rpo_yoy > 0].ticker.tolist()
        out.append(f"Remaining performance obligations (contracted future revenue) grew year over year at "
                   f"{len(up)} of {len(rpo)} companies that report it, in each one's latest quarter.")
    return out
