"""Turn a normalized company dict into tidy pandas tables for display."""
from __future__ import annotations

from datetime import date

import pandas as pd

from .concepts import METRIC_ORDER

MARGINS = {
    "Gross margin": "gross_profit",
    "Operating margin": "operating_income",
    "Net margin": "net_income",
    "FCF margin": "free_cash_flow",
    "R&D % of revenue": "rnd",
    "S&M % of revenue": "sm",
    "SBC % of revenue": "sbc",
}
GROWTH_METRICS = ["revenue", "gross_profit", "operating_income", "net_income", "eps_diluted",
                  "operating_cash_flow", "free_cash_flow", "deferred_revenue", "rpo"]


def values_frame(company: dict) -> pd.DataFrame:
    """One row per quarter (oldest first), one column per metric (float or NaN)."""
    rows = []
    for q in company["quarters"]:
        row = {k: q[k] for k in ("fiscal_label", "calendar_label", "period_end", "fiscal_year", "fiscal_quarter")}
        for k in METRIC_ORDER:
            row[k] = q["metrics"].get(k, {}).get("value")
        rows.append(row)
    return pd.DataFrame(rows)


def _prior_quarter_idx(df: pd.DataFrame, i: int) -> int | None:
    if i == 0:
        return None
    gap = (date.fromisoformat(df.period_end.iloc[i]) - date.fromisoformat(df.period_end.iloc[i - 1])).days
    return i - 1 if 75 <= gap <= 105 else None


def _year_ago_idx(df: pd.DataFrame, i: int) -> int | None:
    fy, fq = df.fiscal_year.iloc[i], df.fiscal_quarter.iloc[i]
    m = df.index[(df.fiscal_year == fy - 1) & (df.fiscal_quarter == fq)]
    return int(m[0]) if len(m) else None


def pct_change(new, old) -> float | None:
    if new is None or old is None or pd.isna(new) or pd.isna(old) or old == 0:
        return None
    return (new - old) / abs(old)


def growth_frame(company: dict) -> pd.DataFrame:
    df = values_frame(company)
    out = []
    for i in range(len(df)):
        p, y = _prior_quarter_idx(df, i), _year_ago_idx(df, i)
        row = {"fiscal_label": df.fiscal_label.iloc[i], "calendar_label": df.calendar_label.iloc[i],
               "period_end": df.period_end.iloc[i]}
        for k in GROWTH_METRICS:
            cur = df[k].iloc[i]
            row[f"{k}_qoq"] = pct_change(cur, df[k].iloc[p]) if p is not None else None
            row[f"{k}_yoy"] = pct_change(cur, df[k].iloc[y]) if y is not None else None
        out.append(row)
    return pd.DataFrame(out)


def margins_frame(company: dict) -> pd.DataFrame:
    df = values_frame(company)
    out = df[["fiscal_label", "calendar_label", "period_end"]].copy()
    for name, k in MARGINS.items():
        out[name] = [v / r if pd.notna(v) and pd.notna(r) and r else None for v, r in zip(df[k], df["revenue"])]
    return out


def fmt_money(v, unit: str = "USD") -> str:
    if v is None or pd.isna(v):
        return "—"
    if unit == "USD/shares":
        return f"${v:,.2f}"
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e9:
        return f"{sign}${a / 1e9:,.2f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:,.1f}M"
    return f"{sign}${a:,.0f}"


def fmt_pct(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v * 100:+.1f}%"
