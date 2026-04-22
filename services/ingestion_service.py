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


async def ingest(proposal_id: str) -> dict:
    approved = hitl.load_approved_scope(proposal_id)
    if not approved:
        return {"error": "No approved scope found for proposal_id", "proposal_id": proposal_id}

    mcp = get_mcp_client()
    filings_fetched = []
    total_chunks = 0
    errors = []

    for company in approved.approved_companies:
        cik = company.cik
        if not cik:
            logger.warning(f"Company {company.ticker} has no CIK, skipping.")
            continue

        try:
            filings = await mcp.list_filings(
                cik=cik,
                form_types=approved.form_types,
                since_date=approved.date_range[0],
                until_date=approved.date_range[1],
            )
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

                chunks = rag_pipeline.chunk_filing(filing_text)
                if chunks:
                    rag_pipeline.embed_chunks(chunks)
                    total_chunks += len(chunks)

                filings_fetched.append({
                    "accession_number": accession,
                    "company": company.ticker,
                    "form_type": filing_meta.get("form_type", ""),
                    "filing_date": filing_meta.get("filing_date", ""),
                    "chunks": len(chunks),
                })

            except Exception as e:
                errors.append(f"{accession}: {e}")
                continue

    logging_utils.log_ingestion(proposal_id, filings_fetched, total_chunks)

    return {
        "proposal_id": proposal_id,
        "filings_ingested": len(filings_fetched),
        "chunks_created": total_chunks,
        "filings": filings_fetched,
        "errors": errors,
    }
