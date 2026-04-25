import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _chunk_for(ticker: str, accession_number: str, form_type: str, filing_date: str, text: str):
    from models import Chunk, ChunkMetadata

    return Chunk(
        chunk_id=f"{ticker}_{form_type.replace('-', '')}_{filing_date[:4]}_item_1_00",
        text=text,
        metadata=ChunkMetadata(
            chunk_id=f"{ticker}_{form_type.replace('-', '')}_{filing_date[:4]}_item_1_00",
            company_ticker=ticker,
            company_name=f"{ticker} Corp",
            cik="1668717",
            accession_number=accession_number,
            form_type=form_type,
            filing_date=filing_date,
            item_section="item_1",
            chunk_index=0,
        ),
    )


@pytest.mark.asyncio
async def test_change_intelligence_caches_and_validates_categories(monkeypatch, tmp_path):
    from models import ChangeIntelligenceRequest, Company, StockPricePoint, StockSeries
    from services import change_intelligence_service as svc

    company = Company(ticker="BUD", name="Anheuser-Busch InBev SA/NV", cik="1668717", rationale="Change intelligence workflow")
    before_chunk = _chunk_for("BUD", "1668717-25-000001", "20-F", "2025-03-01", "Older filing discusses portfolio and broad competition.")
    after_chunk = _chunk_for("BUD", "1668717-26-000001", "20-F", "2026-03-03", "New filing emphasizes premiumization, marketing investment, and margin discipline.")
    completion_calls = {"count": 0}

    monkeypatch.setattr(svc, "_cache_path", lambda key: str(tmp_path / f"{key}.json"))
    monkeypatch.setattr(svc, "_company_from_ticker", lambda ticker: company)
    monkeypatch.setattr(svc, "_retrieve_filing_chunks", lambda query, filing_text: [after_chunk] if filing_text["metadata"]["accession_number"].endswith("000001") and filing_text["metadata"]["filing_date"] == "2026-03-03" else [before_chunk])
    monkeypatch.setattr(svc.logging_utils, "log_change_intelligence", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        svc,
        "fetch_stock_series",
        lambda companies, lookback: [
            StockSeries(
                ticker="BUD",
                company_name="Anheuser-Busch InBev SA/NV",
                points=[
                    StockPricePoint(date="2025-03-03", close=60.0, indexed_close=100.0),
                    StockPricePoint(date="2025-03-04", close=61.0, indexed_close=101.67),
                    StockPricePoint(date="2025-03-10", close=62.0, indexed_close=103.33),
                    StockPricePoint(date="2026-03-03", close=72.0, indexed_close=120.0),
                    StockPricePoint(date="2026-03-04", close=73.0, indexed_close=121.67),
                ],
            )
        ],
    )

    class FakeMCP:
        async def list_filings(self, cik, form_types, since_date, until_date):
            return [
                {"accession_number": "1668717-26-000001", "form_type": "20-F", "filing_date": "2026-03-03"},
                {"accession_number": "1668717-25-000001", "form_type": "20-F", "filing_date": "2025-03-01"},
            ]

        async def fetch_filing(self, accession_number, cik=None):
            filing_date = "2026-03-03" if accession_number.endswith("000001") and accession_number.startswith("1668717-26") else "2025-03-01"
            return {
                "metadata": {
                    "ticker": "BUD",
                    "company_name": "Anheuser-Busch InBev SA/NV",
                    "cik": "1668717",
                    "accession_number": accession_number,
                    "form_type": "20-F",
                    "filing_date": filing_date,
                },
                "sections": {"item_1": "filing text"},
            }

    monkeypatch.setattr(svc, "get_mcp_client", lambda: FakeMCP())

    async def fake_completion(*, model, messages, max_retries=3):
        completion_calls["count"] += 1
        system_prompt = messages[0]["content"]
        if "single company's filing language changed across time" in system_prompt:
            return {
                "window_summary": "The newer filing puts more emphasis on premiumization and marketing investment.",
                "changes": [
                    {
                        "change_id": "chg_1",
                        "category": "strategy_emphasis_increased",
                        "summary": "The newer filing places more explicit emphasis on premiumization and marketing investment.",
                        "importance": "high",
                        "confidence": "high",
                        "before_chunk_ids": [before_chunk.chunk_id],
                        "after_chunk_ids": [after_chunk.chunk_id],
                    },
                    {
                        "change_id": "chg_2",
                        "category": "unsupported_custom_label",
                        "summary": "Should be filtered out.",
                        "importance": "medium",
                        "confidence": "medium",
                        "before_chunk_ids": [before_chunk.chunk_id],
                        "after_chunk_ids": [after_chunk.chunk_id],
                    },
                ],
                "gaps": [],
            }
        return {"overall_summary": "Across annual filings, BUD increased emphasis on premiumization and marketing-led margin expansion."}

    monkeypatch.setattr(svc, "_create_json_completion", fake_completion)

    req = ChangeIntelligenceRequest(
        ticker="BUD",
        query="What changed in strategy, pricing, and market positioning?",
        form_types=["20-F", "6-K"],
        filing_date_range=["2024-01-01", "2026-12-31"],
        max_filings=3,
        price_lookback="3M",
    )

    stale_key = svc._change_key(req)
    stale_cache = tmp_path / f"{stale_key}.json"
    stale_cache.write_text('{"change_run_id":"chg_old","from_cache":false,"retrieval_version":"old-version","company":{"ticker":"BUD","name":"BUD","cik":"1668717"},"overall_summary":"stale","comparison_windows":[],"change_cards":[],"stock_series":[],"filing_events":[]}')

    result, from_cache = await svc.change_intelligence(req)

    assert from_cache is False
    assert result.retrieval_version == svc.VECTOR_SCHEMA_VERSION
    assert len(result.comparison_windows) == 1
    assert len(result.change_cards) == 1
    assert result.change_cards[0].category == "strategy_emphasis_increased"
    assert result.change_cards[0].before_evidence
    assert result.change_cards[0].after_evidence

    cached_result, cached = await svc.change_intelligence(req)
    assert cached is True
    assert cached_result.from_cache is True
    assert completion_calls["count"] == 2


def test_change_intelligence_endpoint_returns_schema(monkeypatch):
    from main import app
    from models import ChangeIntelligenceResponse, Company
    from routes import change_intelligence as change_route

    async def fake_change(req, force_refresh=False):
        return ChangeIntelligenceResponse(
            change_run_id="chg_test",
            from_cache=False,
            company=Company(ticker="BUD", name="Anheuser-Busch InBev SA/NV", cik="1668717", rationale="Change intelligence workflow"),
            retrieval_version="test-version",
            overall_summary="The company increased emphasis on premiumization and pricing discipline.",
            comparison_windows=[],
            change_cards=[],
            stock_series=[],
            filing_events=[],
        ), False

    monkeypatch.setattr(change_route, "change_intelligence", fake_change)
    client = TestClient(app)

    res = client.post("/api/change-intelligence", json={
        "ticker": "BUD",
        "query": "What changed in strategy?",
        "form_types": ["20-F"],
        "filing_date_range": ["2024-01-01", "2026-12-31"],
        "max_filings": 3,
        "price_lookback": "3M",
    })

    assert res.status_code == 200
    data = res.json()
    assert data["change_run_id"] == "chg_test"
    assert "overall_summary" in data
    assert "comparison_windows" in data
    assert "change_cards" in data
