import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class _Company:
    def __init__(self, ticker: str, cik: str):
        self.ticker = ticker
        self.cik = cik


class _ApprovedScope:
    def __init__(self, companies, form_types, date_range):
        self.approved_companies = companies
        self.form_types = form_types
        self.date_range = date_range


@pytest.mark.asyncio
async def test_ingest_falls_back_to_foreign_issuer_forms(monkeypatch):
    from services import ingestion_service

    approved = _ApprovedScope(
        companies=[_Company("ABEV", "1029800")],
        form_types=["10-K", "10-Q", "8-K"],
        date_range=["2024-01-01", "2025-12-31"],
    )

    class FakeMCP:
        async def list_filings(self, cik, form_types, since_date, until_date):
            if form_types == ["10-K", "10-Q", "8-K"]:
                return []
            if form_types == ["20-F", "6-K"]:
                return [{
                    "accession_number": "1029800-25-000001",
                    "form_type": "20-F",
                    "filing_date": "2025-03-01",
                }]
            return []

        async def fetch_filing(self, accession_number, cik=None):
            return {
                "metadata": {
                    "ticker": "ABEV",
                    "company_name": "Ambev S.A.",
                    "cik": "1029800",
                    "accession_number": accession_number,
                    "form_type": "20-F",
                    "filing_date": "2025-03-01",
                },
                "sections": {
                    "item_1": "Annual report text",
                },
            }

    monkeypatch.setattr(ingestion_service.hitl, "load_approved_scope", lambda proposal_id: approved)
    monkeypatch.setattr(ingestion_service, "get_mcp_client", lambda: FakeMCP())
    monkeypatch.setattr(
        ingestion_service.rag_pipeline,
        "ensure_filing_embeddings_current",
        lambda filing_text: {"status": "refreshed", "reason": "missing_vectors", "chunks": 2},
    )
    monkeypatch.setattr(ingestion_service.logging_utils, "log_ingestion", lambda *args, **kwargs: None)
    monkeypatch.setattr(ingestion_service.hitl, "save_ingestion_manifest", lambda *args, **kwargs: None)

    result = await ingestion_service.ingest("scope_test")

    assert result["filings_ingested"] == 1
    assert result["chunks_created"] == 2
    assert result["filings"][0]["form_type"] == "20-F"
    assert any("used foreign issuer forms 20-F and 6-K instead" in issue for issue in result["issues"])


@pytest.mark.asyncio
async def test_ingest_reports_missing_filings_when_none_exist(monkeypatch):
    from services import ingestion_service

    approved = _ApprovedScope(
        companies=[_Company("BUD", "1668717")],
        form_types=["10-K", "10-Q", "8-K"],
        date_range=["2024-01-01", "2025-12-31"],
    )

    class FakeMCP:
        async def list_filings(self, cik, form_types, since_date, until_date):
            return []

    monkeypatch.setattr(ingestion_service.hitl, "load_approved_scope", lambda proposal_id: approved)
    monkeypatch.setattr(ingestion_service, "get_mcp_client", lambda: FakeMCP())
    monkeypatch.setattr(ingestion_service.logging_utils, "log_ingestion", lambda *args, **kwargs: None)
    monkeypatch.setattr(ingestion_service.hitl, "save_ingestion_manifest", lambda *args, **kwargs: None)

    result = await ingestion_service.ingest("scope_test")

    assert result["filings_ingested"] == 0
    assert any("BUD: no filings found" in issue for issue in result["issues"])
