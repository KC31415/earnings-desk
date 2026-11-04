"""Metric -> XBRL concept mapping with fallbacks.

For each period the first concept in the list that has a value wins, and the
concept actually used is recorded next to the value so a reader can see it.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    concepts: tuple[str, ...]
    kind: str = "duration"  # "duration" (flow over a period) or "instant" (balance at a date)
    unit: str = "USD"
    # Can a quarter be derived by subtracting year-to-date totals? Not for per-share values.
    subtractable: bool = True
    note: str = ""
    # Concepts that only cover part of the metric (e.g. the current portion of a liability).
    partial_concepts: tuple[str, ...] = field(default_factory=tuple)


METRICS: dict[str, Metric] = {
    m.key: m
    for m in [
        Metric(
            "revenue",
            "Revenue",
            (
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues",
                "RevenueFromContractWithCustomerIncludingAssessedTax",
                "SalesRevenueNet",
            ),
        ),
        Metric("gross_profit", "Gross profit", ("GrossProfit",),
               note="If not tagged, derived as revenue minus cost of revenue."),
        Metric("operating_income", "Operating income", ("OperatingIncomeLoss",)),
        Metric("net_income", "Net income", ("NetIncomeLoss", "ProfitLoss")),
        Metric(
            "eps_diluted",
            "Diluted EPS",
            ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"),
            unit="USD/shares",
            subtractable=False,
            note="Not derivable for Q4 (share counts differ across periods); shown blank when the 10-K has no quarterly figure.",
        ),
        Metric(
            "operating_cash_flow",
            "Operating cash flow",
            (
                "NetCashProvidedByUsedInOperatingActivities",
                "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            ),
        ),
        Metric(
            "capex",
            "Capital expenditures",
            ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
        ),
        Metric(
            "rnd",
            "R&D expense",
            ("ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"),
        ),
        Metric("sm", "Sales & marketing expense", ("SellingAndMarketingExpense",)),
        Metric("sbc", "Stock-based compensation", ("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense")),
        Metric(
            "deferred_revenue",
            "Deferred revenue",
            (
                "ContractWithCustomerLiability",
                "DeferredRevenue",
                "ContractWithCustomerLiabilityCurrent",
                "DeferredRevenueCurrent",
            ),
            kind="instant",
            partial_concepts=("ContractWithCustomerLiabilityCurrent", "DeferredRevenueCurrent"),
        ),
        Metric("rpo", "Remaining performance obligations", ("RevenueRemainingPerformanceObligation",), kind="instant"),
    ]
}

# Used only to derive gross profit when GrossProfit is not tagged.
COST_OF_REVENUE_CONCEPTS = (
    "CostOfRevenue",
    "CostOfGoodsAndServicesSold",
    "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization",
)

# Derived metrics (computed in normalize.py), listed here so the UI can label them.
DERIVED_LABELS = {"free_cash_flow": "Free cash flow (OCF − capex)"}

METRIC_ORDER = [
    "revenue", "gross_profit", "operating_income", "net_income", "eps_diluted",
    "operating_cash_flow", "capex", "free_cash_flow", "rnd", "sm", "sbc",
    "deferred_revenue", "rpo",
]


def label(key: str) -> str:
    if key in METRICS:
        return METRICS[key].label
    return DERIVED_LABELS.get(key, key)


def all_concepts() -> set[str]:
    out = set(COST_OF_REVENUE_CONCEPTS)
    for m in METRICS.values():
        out.update(m.concepts)
    return out
