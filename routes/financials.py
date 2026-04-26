"""
Financials API Routes
=====================
Endpoints for table extraction and XBRL structured financial data.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import edgar_client
from models import FinancialsRequest, XBRLRequest
from services.table_extraction_service import extract_tables

router = APIRouter(prefix="/api/financials")


@router.post("/tables")
async def get_filing_tables(req: FinancialsRequest):
    """
    Extract and classify financial tables from a specific SEC filing.

    Fetches the filing HTML, parses <table> elements, filters out layout/nav
    tables, then uses an LLM to classify each table (income statement, balance
    sheet, etc.) and assign a readable title.
    """
    try:
        result = await extract_tables(
            req.accession_number,
            cik=req.cik,
            classify=req.classify_tables,
        )
        return result.model_dump()
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.post("/xbrl")
async def get_xbrl_facts(req: XBRLRequest):
    """
    Fetch machine-readable XBRL financial facts for a company from SEC EDGAR.

    Accepts either a CIK or a ticker. If only a ticker is provided the endpoint
    resolves it to a CIK automatically before fetching XBRL data.
    Returns key metrics (revenue, net income, assets, cash flows, etc.) across
    multiple fiscal years.
    """
    try:
        cik = req.cik
        if not cik:
            ticker = (req.ticker or "").strip().upper()
            if not ticker:
                return JSONResponse(status_code=422, content={"error": "Provide either cik or ticker"})
            resolved = edgar_client.resolve_ticker_to_cik(ticker)
            cik = resolved.get("cik")
            if not cik:
                return JSONResponse(status_code=404, content={"error": f"Could not resolve ticker '{ticker}' to a CIK"})

        result = edgar_client.fetch_xbrl_facts(cik)
        if "error" in result and not result.get("facts"):
            return JSONResponse(status_code=422, content=result)
        return result
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.get("/xbrl/{cik}")
async def get_xbrl_facts_by_cik(cik: str):
    """GET variant — convenience endpoint for browser testing."""
    try:
        result = edgar_client.fetch_xbrl_facts(cik)
        return result
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
