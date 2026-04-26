"""
XBRL Context Service
====================
Detects quantitative queries and formats XBRL financial facts as compact
context strings that can be prepended to LLM synthesis calls.

Used by answer_workflow, compare_service, and change_intelligence_service
to anchor quantitative claims in real SEC-filed numbers.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from mcp_client import get_mcp_client

logger = logging.getLogger(__name__)

# Fetch at most this many companies concurrently to avoid rate limits
_XBRL_SEMAPHORE = asyncio.Semaphore(5)

# Keywords that signal the user wants numbers, not just narrative
_QUANT_RE = re.compile(
    r"\b("
    r"revenue|revenues|sales|income|profit|loss|margins?|earnings|eps|"
    r"cash|assets|liabilities|equity|debt|capex|capital expenditure|"
    r"ebitda|ebit|growth|percent|%|billion|million|thousand|"
    r"costs?|expenses?|return|ratio|rate|"
    r"how much|how big|size of|compared to|versus|vs\.?|"
    r"financial|numbers?|metrics?|figures?|"
    r"quarter|annual|fiscal|year|yoy|cagr|"
    r"increase|decrease|decline|rose|fell|grew|grow|shrink|shrunk"
    r")\b",
    re.IGNORECASE,
)

# Preferred display order — income first, then balance sheet, then cash flows
_PRIORITY = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "GrossProfit",
    "OperatingIncomeLoss",
    "NetIncomeLoss",
    "EarningsPerShareBasic",
    "EarningsPerShareDiluted",
    "ResearchAndDevelopmentExpense",
    "SellingGeneralAndAdministrativeExpense",
    "Assets",
    "AssetsCurrent",
    "CashAndCashEquivalentsAtCarryingValue",
    "Liabilities",
    "LiabilitiesCurrent",
    "LongTermDebt",
    "StockholdersEquity",
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInInvestingActivities",
    "NetCashProvidedByUsedInFinancingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
]


def is_quantitative(query: str) -> bool:
    """Return True if the query appears to ask for financial / numeric data."""
    return bool(_QUANT_RE.search(query))


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt(value: float | None, unit: str) -> str:
    """Format a raw XBRL value for display (e.g. 391_035_000_000 → $391.04B)."""
    if value is None:
        return "—"
    neg = value < 0
    abs_v = abs(value)
    wrap = lambda s: f"({s})" if neg else s

    if unit == "USD":
        if abs_v >= 1e9:
            return wrap(f"${abs_v / 1e9:.2f}B")
        if abs_v >= 1e6:
            return wrap(f"${abs_v / 1e6:.1f}M")
        if abs_v >= 1e3:
            return wrap(f"${abs_v / 1e3:.1f}K")
        return wrap(f"${abs_v:.2f}")
    if unit == "USD/shares":
        return f"${value:.2f}"
    if unit == "shares":
        if abs_v >= 1e9:
            return wrap(f"{abs_v / 1e9:.2f}B")
        if abs_v >= 1e6:
            return wrap(f"{abs_v / 1e6:.1f}M")
        return str(int(value))
    return str(value)


def _table(facts: dict, max_years: int = 4) -> str:
    """Render an ASCII table of metrics × fiscal years for one company."""
    if not facts:
        return "  (no XBRL data available)"

    # Collect up to max_years of period-end dates, most recent first
    all_periods = sorted(
        {f["period_end"] for v in facts.values() for f in v.get("facts", [])},
        reverse=True,
    )[:max_years]

    if not all_periods:
        return "  (no annual XBRL data found)"

    year_labels = [p[:4] for p in all_periods]

    # Sort metrics by priority list
    ordered = sorted(
        facts.keys(),
        key=lambda k: _PRIORITY.index(k) if k in _PRIORITY else 999,
    )

    label_w = max((len(facts[k]["label"]) for k in ordered), default=20)
    label_w = max(label_w, 24)
    val_w = 13

    header = f"  {'Metric':<{label_w}} | " + " | ".join(f"{yr:>{val_w}}" for yr in year_labels)
    sep = f"  {'-' * label_w}-+-" + "-+-".join(["-" * val_w] * len(year_labels))
    lines = [header, sep]

    for key in ordered:
        metric = facts[key]
        label = metric["label"]
        unit = metric["unit"]
        by_period = {f["period_end"]: f["value"] for f in metric.get("facts", [])}
        vals = [_fmt(by_period.get(p), unit) for p in all_periods]
        lines.append(f"  {label:<{label_w}} | " + " | ".join(f"{v:>{val_w}}" for v in vals))

    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

async def build_xbrl_context(
    companies: list[dict],
    categories: Optional[list[str]] = None,
    max_years: int = 4,
) -> str:
    """
    Fetch XBRL facts for a list of companies and return a formatted context
    string ready to prepend to an LLM prompt.

    Args:
        companies: list of {ticker, name, cik} dicts
        categories: optional filter — e.g. ["income_statement", "cash_flow"]
                    None means include all categories
        max_years: how many fiscal years to show per metric (default 4)

    Returns:
        Formatted multi-company XBRL context block, or "" if nothing usable.
    """
    if not companies:
        return ""

    async def _fetch(company: dict) -> tuple[str, str, dict]:
        ticker = company.get("ticker", "")
        name = company.get("name", ticker)
        cik = str(company.get("cik", "")).strip()
        if not cik:
            return ticker, name, {}
        mcp = get_mcp_client()
        async with _XBRL_SEMAPHORE:
            try:
                raw = await mcp.get_xbrl_facts(cik)
                facts = raw.get("facts", {})
                if categories:
                    facts = {k: v for k, v in facts.items() if v.get("category") in categories}
                return ticker, name, facts
            except Exception as exc:
                logger.debug("XBRL fetch skipped for %s (%s): %s", ticker, cik, exc)
                return ticker, name, {}

    results = await asyncio.gather(*[_fetch(c) for c in companies])

    sections: list[str] = []
    for ticker, name, facts in results:
        if not facts:
            continue
        heading = f"{name} ({ticker})"
        sections.append(f"{heading}\n{_table(facts, max_years)}")

    if not sections:
        return ""

    rule = "─" * 64
    body = f"\n\n{rule}\n".join(sections)

    return (
        f"KEY FINANCIAL METRICS — XBRL (SEC EDGAR structured data)\n"
        f"{rule}\n"
        f"Source: 10-K / 20-F XBRL tags. Annual figures only. "
        f"Use these numbers to ground quantitative claims.\n\n"
        f"{body}\n"
        f"{rule}"
    )
