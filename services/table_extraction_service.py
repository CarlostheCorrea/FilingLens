"""
Table Extraction Service
========================
Fetches financial HTML tables from SEC filings and uses an LLM to
classify and name them (income statement, balance sheet, etc.).
"""

import json
import logging
from typing import Optional

from openai import AsyncOpenAI

import cost_tracker
import edgar_client
from config import (
    OPENAI_API_KEY,
    OPENAI_WORKER_MODEL,
    TABLE_CLASSIFIER_SYSTEM_PROMPT,
)
from models import FilingFinancials, FinancialTable

logger = logging.getLogger(__name__)

_openai = AsyncOpenAI(api_key=OPENAI_API_KEY)


async def extract_tables(
    accession_number: str,
    cik: Optional[str] = None,
    classify: bool = True,
) -> FilingFinancials:
    """
    Fetch financial tables from a filing and optionally classify them.

    Args:
        accession_number: SEC accession number (e.g. "0000320193-23-000077")
        cik: Company CIK — improves resolution reliability when provided
        classify: If True, run LLM classification to name each table

    Returns:
        FilingFinancials with tables list and metadata
    """
    cost_tracker.start_tracking()

    raw = edgar_client.fetch_filing_tables(accession_number, cik=cik)

    if raw.get("error") and not raw.get("tables"):
        return FilingFinancials(
            accession_number=accession_number,
            extraction_notes=[raw.get("error", "Unknown extraction error")],
        )

    meta = raw.get("metadata", {})
    raw_tables = raw.get("tables", [])

    # Build table objects from raw extraction
    tables: list[FinancialTable] = [
        FinancialTable(
            table_id=t["table_id"],
            headers=t.get("headers", []),
            rows=t.get("rows", []),
            row_count=t.get("row_count", 0),
            col_count=t.get("col_count", 0),
        )
        for t in raw_tables
    ]

    notes: list[str] = [f"Extracted {len(tables)} financial table(s) from filing HTML"]

    # LLM classification
    if classify and tables:
        tables, classification_note = await _classify_tables(tables)
        notes.append(classification_note)

    return FilingFinancials(
        accession_number=accession_number,
        ticker=meta.get("ticker", ""),
        company_name=meta.get("company_name", ""),
        cik=meta.get("cik", ""),
        form_type=meta.get("form_type", ""),
        filing_date=meta.get("filing_date", ""),
        tables=tables,
        extraction_notes=notes,
    )


async def _classify_tables(
    tables: list[FinancialTable],
) -> tuple[list[FinancialTable], str]:
    """
    Call LLM to assign a title and category to each table.
    Sends headers + first 3 rows as a compact preview to minimise token usage.
    """
    # Build compact preview payload
    previews = [
        {
            "table_id": t.table_id,
            "headers": t.headers[:12],
            "sample_rows": [row[:12] for row in t.rows[:3]],
        }
        for t in tables
    ]

    try:
        response = await _openai.chat.completions.create(
            model=OPENAI_WORKER_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": TABLE_CLASSIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"tables": previews})},
            ],
        )

        if response.usage:
            cost_tracker.record_llm(
                OPENAI_WORKER_MODEL,
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
            )

        raw = json.loads(response.choices[0].message.content or "{}")
        classifications: dict[str, dict] = {
            item["table_id"]: item
            for item in raw.get("tables", [])
            if "table_id" in item
        }

        for table in tables:
            cls = classifications.get(table.table_id, {})
            table.title = cls.get("title", "")
            table.category = cls.get("category", "other")

        classified_count = sum(1 for t in tables if t.title)
        note = f"LLM classified {classified_count}/{len(tables)} tables"

    except Exception as exc:
        logger.warning("Table classification failed: %s", exc)
        note = f"Table classification skipped ({exc})"

    return tables, note
