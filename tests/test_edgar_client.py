"""Tests for edgar_client.py — requires EDGAR_IDENTITY in .env"""

import pytest
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_resolve_ticker_nvda():
    from edgar_client import resolve_ticker_to_cik
    result = resolve_ticker_to_cik("NVDA")
    assert isinstance(result, dict)
    if result:  # may be empty if EDGAR is unavailable in test env
        assert "cik" in result or "error" not in result


def test_search_company_nvidia():
    from edgar_client import search_company_by_name
    results = search_company_by_name("NVIDIA")
    assert isinstance(results, list)


def test_list_filings_returns_list():
    from edgar_client import resolve_ticker_to_cik, list_filings
    company = resolve_ticker_to_cik("AAPL")
    if not company or not company.get("cik"):
        pytest.skip("EDGAR unavailable")
    filings = list_filings(
        cik=company["cik"],
        form_types=["10-K"],
        since_date="2023-01-01",
        until_date="2025-12-31",
    )
    assert isinstance(filings, list)


def test_fetch_filing_text_structure():
    from edgar_client import resolve_ticker_to_cik, list_filings, fetch_filing_text
    company = resolve_ticker_to_cik("MSFT")
    if not company or not company.get("cik"):
        pytest.skip("EDGAR unavailable")
    filings = list_filings(
        cik=company["cik"],
        form_types=["10-K"],
        since_date="2024-01-01",
        until_date="2025-12-31",
    )
    if not filings:
        pytest.skip("No filings found")
    result = fetch_filing_text(filings[0]["accession_number"])
    assert "metadata" in result
    assert "sections" in result
