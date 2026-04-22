import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _chunk_for(ticker: str, accession_number: str, text: str):
    from models import Chunk, ChunkMetadata

    return Chunk(
        chunk_id=f"{ticker}_10K_2026_item_1_00",
        text=text,
        metadata=ChunkMetadata(
            chunk_id=f"{ticker}_10K_2026_item_1_00",
            company_ticker=ticker,
            company_name=f"{ticker} Corp",
            cik="1000" if ticker == "AAA" else "2000",
            accession_number=accession_number,
            form_type="10-K",
            filing_date="2026-02-15" if ticker == "AAA" else "2026-02-20",
            item_section="item_1",
            chunk_index=0,
        ),
    )


@pytest.mark.asyncio
async def test_compare_companies_caches_and_uses_compare_collection(monkeypatch, tmp_path):
    from models import CompareRequest, Company, StockPricePoint, StockSeries
    from services import compare_service

    companies = {
        "AAA": Company(ticker="AAA", name="AAA Corp", cik="1000", rationale="Compare workflow"),
        "BBB": Company(ticker="BBB", name="BBB Corp", cik="2000", rationale="Compare workflow"),
    }
    chunk_map = {
        "AAA": [_chunk_for("AAA", "1000-26-000001", "AAA discusses premium mix expansion.")],
        "BBB": [_chunk_for("BBB", "2000-26-000001", "BBB focuses on price discipline and distribution.")],
    }
    embed_collections = []
    retrieve_collections = []
    completion_calls = {"count": 0}

    monkeypatch.setattr(compare_service, "_cache_path", lambda key: str(tmp_path / f"{key}.json"))
    monkeypatch.setattr(compare_service, "_company_from_ticker", lambda ticker: companies[ticker])

    class FakeMCP:
        async def list_filings(self, cik, form_types, since_date, until_date):
            return [{
                "accession_number": "1000-26-000001" if cik == "1000" else "2000-26-000001",
                "form_type": "10-K",
                "filing_date": "2026-02-15" if cik == "1000" else "2026-02-20",
                "acceptance_datetime": "2026-02-15T16:10:00Z" if cik == "1000" else "2026-02-20T16:20:00Z",
            }]

        async def fetch_filing(self, accession_number, cik=None):
            ticker = "AAA" if cik == "1000" else "BBB"
            return {
                "metadata": {
                    "ticker": ticker,
                    "company_name": f"{ticker} Corp",
                    "cik": cik,
                    "accession_number": accession_number,
                    "form_type": "10-K",
                    "filing_date": "2026-02-15" if ticker == "AAA" else "2026-02-20",
                    "acceptance_datetime": "2026-02-15T16:10:00Z" if ticker == "AAA" else "2026-02-20T16:20:00Z",
                },
                "sections": {"item_1": f"{ticker} filing text"},
            }

    monkeypatch.setattr(compare_service, "get_mcp_client", lambda: FakeMCP())
    monkeypatch.setattr(compare_service.rag_pipeline, "chunk_filing", lambda filing_text: chunk_map[filing_text["metadata"]["ticker"]])

    def fake_embed_chunks(chunks, collection_name="sec_filings"):
        embed_collections.append(collection_name)

    monkeypatch.setattr(compare_service.rag_pipeline, "embed_chunks", fake_embed_chunks)

    def fake_retrieve(query, k=8, filters=None, tickers=None, collection_name="sec_filings"):
        retrieve_collections.append(collection_name)
        return chunk_map[tickers[0]]

    monkeypatch.setattr(compare_service.rag_pipeline, "retrieve", fake_retrieve)
    monkeypatch.setattr(compare_service.logging_utils, "log_compare", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        compare_service,
        "fetch_stock_series",
        lambda companies, lookback: [
            StockSeries(
                ticker="AAA",
                company_name="AAA Corp",
                points=[
                    StockPricePoint(date="2026-02-13", close=100.0, indexed_close=100.0),
                    StockPricePoint(date="2026-02-16", close=102.0, indexed_close=102.0),
                    StockPricePoint(date="2026-02-17", close=104.0, indexed_close=104.0),
                    StockPricePoint(date="2026-02-23", close=108.0, indexed_close=108.0),
                ],
            ),
            StockSeries(
                ticker="BBB",
                company_name="BBB Corp",
                points=[
                    StockPricePoint(date="2026-02-20", close=50.0, indexed_close=100.0),
                    StockPricePoint(date="2026-02-23", close=49.0, indexed_close=98.0),
                    StockPricePoint(date="2026-02-24", close=50.5, indexed_close=101.0),
                    StockPricePoint(date="2026-03-27", close=55.0, indexed_close=110.0),
                ],
            ),
        ],
    )

    async def fake_completion(*, model, messages, max_retries=3):
        completion_calls["count"] += 1
        system_prompt = messages[0]["content"]
        if "one company inside a two-company comparison workflow" in system_prompt:
            ticker = "AAA" if "(AAA)" in messages[1]["content"] else "BBB"
            return {
                "summary": f"{ticker} strategy summary",
                "evidence_chunk_ids": [chunk_map[ticker][0].chunk_id],
                "gaps": [],
            }
        return {
            "overall_summary": "Both companies focus on pricing and portfolio mix, but execution differs.",
            "similarities": ["Both emphasize pricing discipline."],
            "differences": ["AAA stresses premiumization while BBB stresses distribution."],
        }

    monkeypatch.setattr(compare_service, "_create_json_completion", fake_completion)

    req = CompareRequest(
        ticker_a="AAA",
        ticker_b="BBB",
        query="How do these companies plan to grow profit?",
        form_types=["10-K", "10-Q", "8-K"],
        filing_date_range=["2025-01-01", "2026-12-31"],
        price_lookback="3M",
    )

    result, from_cache = await compare_service.compare_companies(req)

    assert not from_cache
    assert result.overall_summary
    assert len(result.company_comparisons) == 2
    assert len(result.filing_events) == 2
    assert all(name.startswith("compare_cmp_") for name in embed_collections)
    assert retrieve_collections and all(name == embed_collections[0] for name in retrieve_collections)

    cached_result, cached = await compare_service.compare_companies(req)
    assert cached is True
    assert cached_result.from_cache is True
    assert completion_calls["count"] == 3


def test_compute_returns_uses_next_trading_day_for_weekend():
    from services.compare_service import _compute_returns

    points = [
        {"date": "2026-04-17", "close": 100.0},
        {"date": "2026-04-20", "close": 102.0},
        {"date": "2026-04-21", "close": 103.0},
        {"date": "2026-04-27", "close": 105.0},
    ]

    trading_date, return_1d, return_5d, return_30d = _compute_returns(points, "2026-04-19")

    assert trading_date == "2026-04-20"
    assert return_1d == pytest.approx(((103.0 - 102.0) / 102.0) * 100.0, rel=1e-4)
    assert return_5d is None
    assert return_30d is None


def test_compare_endpoint_returns_schema(monkeypatch):
    from main import app
    from models import CompareResponse, Company
    from routes import compare as compare_route

    async def fake_compare(req, force_refresh=False):
        return CompareResponse(
            compare_run_id="cmp_test",
            from_cache=False,
            companies=[
                Company(ticker="AAA", name="AAA Corp", cik="1000", rationale="Compare workflow"),
                Company(ticker="BBB", name="BBB Corp", cik="2000", rationale="Compare workflow"),
            ],
            overall_summary="AAA and BBB both discuss margin expansion.",
            company_comparisons=[],
            similarities=["Both emphasize mix."],
            differences=["AAA leans premium; BBB leans scale."],
            stock_series=[],
            filing_events=[],
        ), False

    monkeypatch.setattr(compare_route, "compare_companies", fake_compare)
    client = TestClient(app)

    res = client.post("/api/compare", json={
        "ticker_a": "AAA",
        "ticker_b": "BBB",
        "query": "How do they grow?",
        "form_types": ["10-K"],
        "filing_date_range": ["2025-01-01", "2026-12-31"],
        "price_lookback": "3M",
    })

    assert res.status_code == 200
    data = res.json()
    assert data["compare_run_id"] == "cmp_test"
    assert "overall_summary" in data
    assert "company_comparisons" in data
    assert "stock_series" in data
