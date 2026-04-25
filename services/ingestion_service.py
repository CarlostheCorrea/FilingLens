"""
Fetches approved filings via MCP, chunks, embeds, and stores in Chroma.
"""

import asyncio
import logging
from mcp_client import get_mcp_client
import rag_pipeline
import hitl
import logging_utils

logger = logging.getLogger(__name__)

FOREIGN_ISSUER_FORM_TYPES = ["20-F", "6-K"]


async def _list_filings_with_fallback(
    mcp,
    company,
    form_types: list[str],
    since_date: str,
    until_date: str,
) -> tuple[list[dict], list[str]]:
    filings = await mcp.list_filings(
        cik=company.cik,
        form_types=form_types,
        since_date=since_date,
        until_date=until_date,
    )
    issues: list[str] = []

    if filings or any(form in form_types for form in FOREIGN_ISSUER_FORM_TYPES):
        return filings, issues

    fallback_filings = await mcp.list_filings(
        cik=company.cik,
        form_types=FOREIGN_ISSUER_FORM_TYPES,
        since_date=since_date,
        until_date=until_date,
    )
    if fallback_filings:
        issues.append(
            f"{company.ticker}: no filings found for {', '.join(form_types)}; "
            "used foreign issuer forms 20-F and 6-K instead."
        )
        return fallback_filings, issues

    issues.append(
        f"{company.ticker}: no filings found in the selected date range for "
        f"{', '.join(form_types)}."
    )
    return [], issues


async def ingest(proposal_id: str) -> dict:
    approved = hitl.load_approved_scope(proposal_id)
    if not approved:
        return {"error": "No approved scope found for proposal_id", "proposal_id": proposal_id}

    mcp = get_mcp_client()
    filings_fetched = []
    total_chunks = 0
    errors = []
    issues = []

    for company in approved.approved_companies:
        cik = company.cik
        if not cik:
            logger.warning(f"Company {company.ticker} has no CIK, skipping.")
            continue

        try:
            filings, company_issues = await _list_filings_with_fallback(
                mcp,
                company,
                approved.form_types,
                approved.date_range[0],
                approved.date_range[1],
            )
            issues.extend(company_issues)
        except Exception as e:
            errors.append(f"{company.ticker}: failed to list filings: {e}")
            continue

        # Limit to 3 most recent filings per company to control costs
        for filing_meta in filings[:3]:
            accession = filing_meta.get("accession_number", "")
            if not accession:
                continue

            try:
                filing_text = await mcp.fetch_filing(accession, cik=cik)

                if filing_text.get("error"):
                    errors.append(f"{accession}: {filing_text['error']}")
                    continue

                refresh = rag_pipeline.ensure_filing_embeddings_current(filing_text)
                total_chunks += refresh.get("chunks", 0)
                if refresh.get("status") == "refreshed" and refresh.get("reason") != "missing_vectors":
                    issues.append(
                        f"{company.ticker}: re-vectorized {accession} due to {refresh['reason']}."
                    )

                filings_fetched.append({
                    "accession_number": accession,
                    "cik": cik,
                    "company": company.ticker,
                    "form_type": filing_meta.get("form_type", ""),
                    "filing_date": filing_meta.get("filing_date", ""),
                    "chunks": refresh.get("chunks", 0),
                })

            except Exception as e:
                errors.append(f"{accession}: {e}")
                continue

    logging_utils.log_ingestion(proposal_id, filings_fetched, total_chunks)
    hitl.save_ingestion_manifest(
        proposal_id,
        {
            "proposal_id": proposal_id,
            "filings": filings_fetched,
        },
    )

    return {
        "proposal_id": proposal_id,
        "filings_ingested": len(filings_fetched),
        "chunks_created": total_chunks,
        "filings": filings_fetched,
        "errors": errors,
        "issues": issues + errors,
    }
