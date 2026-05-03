import os
import sys
import json

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _chunk_for(ticker: str, accession_number: str, filing_date: str, text: str, idx: int):
    from models import Chunk, ChunkMetadata

    return Chunk(
        chunk_id=f"{ticker}_{filing_date[:4]}_item_1a_{idx:02d}",
        text=text,
        metadata=ChunkMetadata(
            chunk_id=f"{ticker}_{filing_date[:4]}_item_1a_{idx:02d}",
            company_ticker=ticker,
            company_name=f"{ticker} Holdings",
            cik="1000000" if ticker == "AAL" else "2000000",
            accession_number=accession_number,
            form_type="10-K",
            filing_date=filing_date,
            item_section="item_1a",
            chunk_index=idx,
        ),
    )


class _FakeStore:
    def __init__(self):
        self._chunks = []

    def add_chunks(self, chunks):
        self._chunks.extend(chunks)

    def retrieve(self, query, k=12):
        return self._chunks[:k]


@pytest.mark.asyncio
async def test_market_gap_generates_ranked_founder_memos(monkeypatch, tmp_path):
    from models import Company, MarketGapRequest
    from services import market_gap_service as svc

    req = MarketGapRequest(
        query="airline operational and technology failures",
        companies=[
            Company(ticker="AAL", name="American Airlines", cik="1000000", rationale=""),
            Company(ticker="DAL", name="Delta Air Lines", cik="2000000", rationale=""),
        ],
        form_types=["10-K"],
        filing_date_range=["2024-01-01", "2026-12-31"],
    )

    aal_chunks = [
        _chunk_for("AAL", "1000000-26-000001", "2026-02-01", "Legacy crew systems cause operational disruptions and cancellations.", 0),
        _chunk_for("AAL", "1000000-26-000001", "2026-02-01", "Cybersecurity oversight and compliance workload continue to rise.", 1),
    ]
    dal_chunks = [
        _chunk_for("DAL", "2000000-26-000001", "2026-02-15", "Aging scheduling systems and vendor dependence create service outages.", 0),
        _chunk_for("DAL", "2000000-26-000001", "2026-02-15", "Regulatory reporting and cyber controls are increasing costs.", 1),
    ]

    monkeypatch.setattr(svc, "_cache_path", lambda key: str(tmp_path / f"{key}.json"))
    monkeypatch.setattr(svc.rag_pipeline, "EphemeralStore", _FakeStore)
    monkeypatch.setattr(svc.rag_pipeline, "filter_sections_by_query", lambda filing_text, query: filing_text)
    async def fake_judge(_result, _query):
        return None

    monkeypatch.setattr(svc, "judge_market_gap", fake_judge)
    monkeypatch.setattr(
        svc.rag_pipeline,
        "chunk_filing",
        lambda filing_text: aal_chunks if filing_text["metadata"]["ticker"] == "AAL" else dal_chunks,
    )

    class FakeMCP:
        async def list_filings(self, cik, form_types, since_date, until_date):
            if cik == "1000000":
                return [{"accession_number": "1000000-26-000001", "form_type": "10-K", "filing_date": "2026-02-01"}]
            return [{"accession_number": "2000000-26-000001", "form_type": "10-K", "filing_date": "2026-02-15"}]

        async def fetch_filing(self, accession_number, cik=None):
            if accession_number.startswith("1000000"):
                return {"metadata": {"ticker": "AAL", "accession_number": accession_number}, "sections": {"item_1a": "text"}}
            return {"metadata": {"ticker": "DAL", "accession_number": accession_number}, "sections": {"item_1a": "text"}}

    monkeypatch.setattr(svc, "get_mcp_client", lambda: FakeMCP())

    async def fake_completion(*, model, messages, max_retries=3):
        system_prompt = messages[0]["content"]
        user_payload = messages[1]["content"]
        if system_prompt == svc.PAIN_EXTRACTION_SYSTEM_PROMPT:
            if "American Airlines" in user_payload:
                return {
                    "pain_points": [
                        {
                            "text": "Legacy crew systems and vendor outages disrupt operations.",
                            "category": "technology",
                            "financial_scale": "$300 million",
                            "severity": "severe",
                            "buyer_owner_hint": "operations",
                            "recurrence_hint": "recurring",
                            "chunk_ids": [aal_chunks[0].chunk_id],
                            "confidence": "high",
                        },
                        {
                            "text": "Cyber and reporting controls increase cost and oversight burden.",
                            "category": "regulatory",
                            "financial_scale": None,
                            "severity": "moderate",
                            "buyer_owner_hint": "compliance",
                            "recurrence_hint": "recent",
                            "chunk_ids": [aal_chunks[1].chunk_id],
                            "confidence": "medium",
                        },
                    ]
                }
            return {
                "pain_points": [
                    {
                        "text": "Aging scheduling systems and vendor dependence create outages.",
                        "category": "technology",
                        "financial_scale": "$180 million",
                        "severity": "severe",
                        "buyer_owner_hint": "operations",
                        "recurrence_hint": "worsening",
                        "chunk_ids": [dal_chunks[0].chunk_id],
                        "confidence": "high",
                    },
                    {
                        "text": "Cyber and regulatory controls raise compliance cost.",
                        "category": "regulatory",
                        "financial_scale": None,
                        "severity": "mild",
                        "buyer_owner_hint": "compliance",
                        "recurrence_hint": "recent",
                        "chunk_ids": [dal_chunks[1].chunk_id],
                        "confidence": "medium",
                    },
                ]
            }
        if system_prompt == svc.GAP_CLUSTER_SYSTEM_PROMPT:
            return {
                "clusters": [
                    {
                        "theme": "Technology System Failures",
                        "description": "Airlines report recurring operational disruption tied to legacy scheduling systems and vendor failures.",
                        "company_tickers": ["AAL", "DAL"],
                        "financial_scale_estimate": "$300 million",
                        "latest_filing_date": "2026-02-15",
                        "severity_summary": "severe",
                        "constituent_pain_point_indices": [0, 2],
                    },
                    {
                        "theme": "Regulatory Oversight Burden",
                        "description": "Carriers report growing compliance and cyber-control burden that increases overhead.",
                        "company_tickers": ["AAL", "DAL"],
                        "financial_scale_estimate": None,
                        "latest_filing_date": "2026-02-15",
                        "severity_summary": "moderate",
                        "constituent_pain_point_indices": [1, 3],
                    },
                ]
            }
        if system_prompt == svc.STRUCTURAL_CONSTRAINT_SYSTEM_PROMPT:
            if "Technology System Failures" in user_payload:
                return {
                    "incumbents_stuck_reason": "Airlines cite entrenched legacy systems, vendor dependence, and regulatory operating requirements that slow replacement.",
                    "incumbents_stuck_confidence": "high",
                    "hard_constraints": ["Legacy scheduling infrastructure is costly to replace", "Vendor dependence creates switching risk"],
                    "soft_constraints": ["Operational change windows are limited"],
                    "disconfirming_evidence": ["Large carriers may still fund internal modernization over time"],
                }
            return {
                "incumbents_stuck_reason": "Compliance burden is real, but carriers can still allocate more internal budget and tooling to address it.",
                "incumbents_stuck_confidence": "medium",
                "hard_constraints": ["Minimum regulatory controls are mandatory"],
                "soft_constraints": ["Compliance modernization competes with other priorities"],
                "disconfirming_evidence": ["Some carriers may solve this with internal teams"],
            }
        if system_prompt == svc.BUYER_OWNERSHIP_SYSTEM_PROMPT:
            if "Technology System Failures" in user_payload:
                return {"buyer_owners": ["operations", "IT"], "primary_buyer_owner": "operations", "ownership_rationale": "Ops and IT bear downtime costs."}
            return {"buyer_owners": ["compliance", "IT"], "primary_buyer_owner": "compliance", "ownership_rationale": "Compliance teams own the burden."}
        if system_prompt == svc.URGENCY_PERSISTENCE_SYSTEM_PROMPT:
            if "Technology System Failures" in user_payload:
                return {
                    "urgency_level": "high",
                    "persistence_level": "recurring",
                    "why_now": "Recent filings from multiple carriers still describe operational outage risk tied to legacy systems.",
                    "disconfirming_evidence": ["Some disruption may reflect cyclical travel peaks rather than permanent failure"],
                }
            return {
                "urgency_level": "medium",
                "persistence_level": "recent",
                "why_now": "Cyber and reporting obligations are growing, but the filings do not show the same repeated outage language.",
                "disconfirming_evidence": ["The burden may normalize as teams mature their controls"],
            }
        if system_prompt == svc.COMMERCIALIZATION_DIFFICULTY_SYSTEM_PROMPT:
            if "Technology System Failures" in user_payload:
                return {"adoption_difficulty": "medium", "difficulty_rationale": "Airlines have long procurement cycles but acute pain."}
            return {"adoption_difficulty": "high", "difficulty_rationale": "Compliance tooling is slow to buy and integrate."}
        if system_prompt == svc.OPPORTUNITY_MEMO_SYSTEM_PROMPT:
            if "Technology System Failures" in user_payload:
                return {
                    "title": "Airline Resilience Layer",
                    "opportunity_type": "infrastructure_tooling",
                    "buyer_owner": "operations",
                    "problem": "Legacy airline operating systems still create expensive disruption.",
                    "thesis": "A resilience and orchestration layer for airline operations could reduce outage spillover without requiring a full core-system replacement.",
                    "why_this_may_fail": ["Airline procurement cycles are slow", "Incumbent vendors may bundle similar capabilities"],
                    "opportunity_status": "strong",
                    "status_rationale": "Broad pain, high urgency, and hard infrastructure constraints create a credible opening.",
                }
            return {
                "title": "Compliance Ops Copilot",
                "opportunity_type": "workflow_software",
                "buyer_owner": "compliance",
                "problem": "Regulatory and cyber-control workload is increasing overhead.",
                "thesis": "A workflow layer could reduce recurring compliance effort, but the opening is less protected and easier for incumbents to address internally.",
                "why_this_may_fail": ["Carriers may keep this in-house", "Budget priority may remain low"],
                "opportunity_status": "speculative",
                "status_rationale": "Pain is real but the entrant case is less protected.",
            }
        if system_prompt == svc.MARKET_SUMMARY_SYSTEM_PROMPT:
            return {
                "industry_summary": "Airline filings describe repeated operational technology pain and a secondary compliance burden.",
                "market_structure_summary": "Operational resilience looks more founder-relevant than compliance overhead because the structural barriers are stronger.",
            }
        raise AssertionError("Unexpected prompt")

    monkeypatch.setattr(svc, "_create_json_completion", fake_completion)

    result, from_cache = await svc.analyze_market_gap(req)

    assert from_cache is False
    assert result.schema_version == svc.MARKET_GAP_SCHEMA_VERSION
    assert len(result.gap_clusters) == 2
    assert len(result.opportunity_memos) == 2
    assert result.opportunity_memos[0].title == "Airline Resilience Layer"
    assert result.opportunity_memos[0].opportunity_score > result.opportunity_memos[1].opportunity_score
    assert result.gap_clusters[0].hard_constraints
    assert result.gap_clusters[0].buyer_owners == ["operations", "IT"]


@pytest.mark.asyncio
async def test_market_gap_invalidates_stale_cache_and_allows_no_clear_opportunity(monkeypatch, tmp_path):
    from models import Company, MarketGapRequest
    from services import market_gap_service as svc

    req = MarketGapRequest(
        query="regional bank cybersecurity burden",
        companies=[
            Company(ticker="ABC", name="ABC Bank", cik="3000000", rationale=""),
            Company(ticker="XYZ", name="XYZ Bank", cik="4000000", rationale=""),
        ],
        form_types=["10-K"],
        filing_date_range=["2024-01-01", "2026-12-31"],
    )

    abc_chunk = _chunk_for("ABC", "3000000-26-000001", "2026-02-01", "Cybersecurity spending and controls continue to grow.", 0)
    xyz_chunk = _chunk_for("XYZ", "4000000-26-000001", "2026-02-10", "Cyber and compliance obligations increase operating costs.", 0)

    monkeypatch.setattr(svc, "_cache_path", lambda key: str(tmp_path / f"{key}.json"))
    monkeypatch.setattr(svc.rag_pipeline, "EphemeralStore", _FakeStore)
    monkeypatch.setattr(svc.rag_pipeline, "filter_sections_by_query", lambda filing_text, query: filing_text)
    async def fake_judge(_result, _query):
        return None

    monkeypatch.setattr(svc, "judge_market_gap", fake_judge)
    monkeypatch.setattr(
        svc.rag_pipeline,
        "chunk_filing",
        lambda filing_text: [abc_chunk] if filing_text["metadata"]["ticker"] == "ABC" else [xyz_chunk],
    )

    class FakeMCP:
        async def list_filings(self, cik, form_types, since_date, until_date):
            if cik == "3000000":
                return [{"accession_number": "3000000-26-000001", "form_type": "10-K", "filing_date": "2026-02-01"}]
            return [{"accession_number": "4000000-26-000001", "form_type": "10-K", "filing_date": "2026-02-10"}]

        async def fetch_filing(self, accession_number, cik=None):
            ticker = "ABC" if accession_number.startswith("3000000") else "XYZ"
            return {"metadata": {"ticker": ticker, "accession_number": accession_number}, "sections": {"item_1a": "text"}}

    monkeypatch.setattr(svc, "get_mcp_client", lambda: FakeMCP())

    stale_key = svc._gap_key(req)
    stale_cache = tmp_path / f"{stale_key}.json"
    stale_cache.write_text(json.dumps({
        "run_id": "old_gap",
        "from_cache": False,
        "retrieval_version": svc.VECTOR_SCHEMA_VERSION,
        "schema_version": "old-market-gap-shape",
        "industry_summary": "stale",
        "market_structure_summary": "stale",
        "gap_clusters": [],
        "opportunity_memos": [],
        "coverage_notes": [],
    }))

    call_count = {"value": 0}

    async def fake_completion(*, model, messages, max_retries=3):
        call_count["value"] += 1
        system_prompt = messages[0]["content"]
        if system_prompt == svc.PAIN_EXTRACTION_SYSTEM_PROMPT:
            chunk_id = abc_chunk.chunk_id if "ABC Bank" in messages[1]["content"] else xyz_chunk.chunk_id
            return {
                "pain_points": [
                    {
                        "text": "Cybersecurity controls increase operating cost.",
                        "category": "regulatory",
                        "financial_scale": None,
                        "severity": "moderate",
                        "buyer_owner_hint": "compliance",
                        "recurrence_hint": "recent",
                        "chunk_ids": [chunk_id],
                        "confidence": "medium",
                    }
                ]
            }
        if system_prompt == svc.GAP_CLUSTER_SYSTEM_PROMPT:
            return {
                "clusters": [
                    {
                        "theme": "Cybersecurity Oversight Burden",
                        "description": "Regional banks disclose rising cyber and control overhead.",
                        "company_tickers": ["ABC", "XYZ"],
                        "financial_scale_estimate": None,
                        "latest_filing_date": "2026-02-10",
                        "severity_summary": "moderate",
                        "constituent_pain_point_indices": [0, 1],
                    }
                ]
            }
        if system_prompt == svc.STRUCTURAL_CONSTRAINT_SYSTEM_PROMPT:
            return {
                "incumbents_stuck_reason": "The burden is real, but the filings do not show a hard structural barrier preventing banks from investing internally.",
                "incumbents_stuck_confidence": "insufficient",
                "hard_constraints": [],
                "soft_constraints": ["Competing priorities may delay action"],
                "disconfirming_evidence": ["Banks appear free to allocate more internal resources if they choose"],
            }
        if system_prompt == svc.BUYER_OWNERSHIP_SYSTEM_PROMPT:
            return {"buyer_owners": ["compliance"], "primary_buyer_owner": "compliance", "ownership_rationale": "Compliance owns the burden."}
        if system_prompt == svc.URGENCY_PERSISTENCE_SYSTEM_PROMPT:
            return {
                "urgency_level": "medium",
                "persistence_level": "recent",
                "why_now": "Banks are describing increased oversight now, but the filings do not show a worsening multi-year pattern.",
                "disconfirming_evidence": ["The burden may shrink if teams build internally"],
            }
        if system_prompt == svc.COMMERCIALIZATION_DIFFICULTY_SYSTEM_PROMPT:
            return {"adoption_difficulty": "high", "difficulty_rationale": "Banks have slow procurement and high integration requirements."}
        if system_prompt == svc.OPPORTUNITY_MEMO_SYSTEM_PROMPT:
            return {
                "title": "Cyber Control Workflow Layer",
                "opportunity_type": "workflow_software",
                "buyer_owner": "compliance",
                "problem": "Banks face higher cyber-control overhead.",
                "thesis": "There may be workflow pain here, but the filings do not show a protected entrant opening.",
                "why_this_may_fail": ["Banks may solve the issue internally", "The sales cycle is slow and expensive"],
                "opportunity_status": "no_clear_opportunity",
                "status_rationale": "Pain is real, but incumbents appear free to address it themselves.",
            }
        if system_prompt == svc.MARKET_SUMMARY_SYSTEM_PROMPT:
            return {
                "industry_summary": "Regional bank filings show rising cyber-control overhead.",
                "market_structure_summary": "The problem is real, but the entrant case looks weak because the filings do not show hard structural lock-in.",
            }
        raise AssertionError("Unexpected prompt")

    monkeypatch.setattr(svc, "_create_json_completion", fake_completion)

    result, from_cache = await svc.analyze_market_gap(req)
    assert from_cache is False
    assert result.opportunity_memos[0].opportunity_status == "no_clear_opportunity"
    assert call_count["value"] > 0

    cached_result, cached = await svc.analyze_market_gap(req)
    assert cached is True
    assert cached_result.from_cache is True


def test_market_gap_endpoint_returns_memos(monkeypatch):
    from main import app
    from models import Company, MarketGapResponse, OpportunityMemo
    from routes import market_gap as market_gap_route

    async def fake_market_gap(req, force_refresh=False):
        return MarketGapResponse(
            run_id="gap_test",
            from_cache=False,
            retrieval_version="test-version",
            schema_version="memo-v1",
            industry_summary="Industry summary",
            market_structure_summary="Market structure summary",
            gap_clusters=[],
            opportunity_memos=[
                OpportunityMemo(
                    memo_id="memo_1",
                    title="Ops Resilience Layer",
                    target_cluster_id="cluster_1",
                    opportunity_type="infrastructure_tooling",
                    buyer_owner="operations",
                    problem="Ops systems still fail.",
                    thesis="A resilience layer could help operators reduce outage spillover.",
                    pain_severity="severe",
                    urgency_level="high",
                    hard_constraint_strength="high",
                    adoption_difficulty="medium",
                    why_incumbents_are_stuck="Legacy systems are hard to replace.",
                    why_now="The issue continues to appear in recent filings.",
                    why_this_may_fail=["Slow procurement"],
                    evidence_chunk_ids=["chunk_1"],
                    opportunity_status="strong",
                    status_rationale="Strong signal.",
                    opportunity_score=0.82,
                )
            ],
            coverage_notes=[],
        ), False

    monkeypatch.setattr(market_gap_route, "analyze_market_gap", fake_market_gap)
    client = TestClient(app)

    res = client.post("/api/market-gap", json={
        "query": "airline technology failures",
        "companies": [{"ticker": "AAL", "name": "American Airlines", "cik": "1000000", "rationale": ""}],
        "form_types": ["10-K"],
        "filing_date_range": ["2024-01-01", "2026-12-31"],
    })

    assert res.status_code == 200
    data = res.json()
    assert data["run_id"] == "gap_test"
    assert "opportunity_memos" in data
    assert data["opportunity_memos"][0]["title"] == "Ops Resilience Layer"


@pytest.mark.asyncio
async def test_market_gap_memo_chat_uses_cached_memo_evidence(monkeypatch, tmp_path):
    from models import MarketGapResponse, GapCluster, PainPoint, OpportunityMemo, OpportunityMemoChatRequest
    from services import market_gap_service as svc

    monkeypatch.setattr(svc, "MARKET_GAP_STATE_DIR", str(tmp_path))

    cached = MarketGapResponse(
        run_id="gap_chat_1",
        from_cache=False,
        retrieval_version="test-version",
        schema_version="memo-v1",
        industry_summary="Industry summary",
        market_structure_summary="Market structure",
        gap_clusters=[
            GapCluster(
                cluster_id="cluster_1",
                theme="Technology System Failures",
                description="Legacy airline systems still fail.",
                frequency=2,
                total_companies=2,
                company_tickers=["AAL", "DAL"],
                evidence_count=2,
                latest_filing_date="2026-02-15",
                financial_scale_estimate="$300 million",
                incumbents_stuck_reason="Legacy infrastructure and vendor lock-in slow replacement.",
                incumbents_stuck_confidence="high",
                hard_constraints=["Legacy operating systems are difficult to replace"],
                soft_constraints=["Operational change windows are limited"],
                buyer_owners=["operations", "IT"],
                urgency_level="high",
                persistence_level="recurring",
                adoption_difficulty="medium",
                why_now="Recent filings continue to describe outage risk.",
                disconfirming_evidence=["Vendors may add similar capabilities"],
                cluster_score=0.82,
                confidence="high",
                pain_points=[
                    PainPoint(
                        company_ticker="AAL",
                        text="Legacy crew systems and vendor outages disrupt operations.",
                        category="technology",
                        financial_scale="$300 million",
                        filing_date="2026-02-01",
                        form_type="10-K",
                        accession_number="1000000-26-000001",
                        cik="1000000",
                        chunk_ids=["AAL_2026_item_1a_00"],
                        confidence="high",
                        severity="severe",
                        buyer_owner_hint="operations",
                        recurrence_hint="recurring",
                    ),
                    PainPoint(
                        company_ticker="DAL",
                        text="Aging scheduling systems and vendor dependence create service outages.",
                        category="technology",
                        financial_scale="$180 million",
                        filing_date="2026-02-15",
                        form_type="10-K",
                        accession_number="2000000-26-000001",
                        cik="2000000",
                        chunk_ids=["DAL_2026_item_1a_00"],
                        confidence="high",
                        severity="severe",
                        buyer_owner_hint="IT",
                        recurrence_hint="worsening",
                    ),
                ],
            )
        ],
        opportunity_memos=[
            OpportunityMemo(
                memo_id="memo_1",
                title="Airline Resilience Layer",
                target_cluster_id="cluster_1",
                opportunity_type="infrastructure_tooling",
                buyer_owner="operations",
                problem="Legacy airline operating systems still create expensive disruption.",
                thesis="A resilience layer could reduce outage spillover.",
                pain_severity="severe",
                urgency_level="high",
                hard_constraint_strength="high",
                adoption_difficulty="medium",
                why_incumbents_are_stuck="Legacy systems are hard to replace quickly.",
                why_now="Recent filings still describe outage risk.",
                why_this_may_fail=["Airline procurement cycles are slow"],
                evidence_chunk_ids=["AAL_2026_item_1a_00", "DAL_2026_item_1a_00"],
                opportunity_status="strong",
                status_rationale="Broad pain and hard constraints create a credible opening.",
                opportunity_score=0.84,
            )
        ],
        coverage_notes=[],
    )
    (tmp_path / "cached.json").write_text(cached.model_dump_json())

    async def fake_completion(*, model, messages, max_retries=3):
        payload = json.loads(messages[1]["content"])
        assert payload["memo"]["title"] == "Airline Resilience Layer"
        assert payload["question"] == "Who exactly has this problem?"
        assert len(payload["evidence"]) == 2
        return {
            "answer": "Operations and IT teams at AAL and DAL appear to own this pain because the filings describe recurring outages tied to legacy operating systems and vendor dependencies.",
            "support_level": "supported",
            "citation_chunk_ids": ["AAL_2026_item_1a_00", "DAL_2026_item_1a_00"],
            "note": "Grounded only in the memo's cited filing evidence.",
        }

    monkeypatch.setattr(svc, "_create_json_completion", fake_completion)

    result = await svc.answer_opportunity_memo_chat(OpportunityMemoChatRequest(
        run_id="gap_chat_1",
        memo_id="memo_1",
        question="Who exactly has this problem?",
    ))

    assert result.support_level == "supported"
    assert len(result.citations) == 2
    assert result.citations[0].chunk_id == "AAL_2026_item_1a_00"


@pytest.mark.asyncio
async def test_market_gap_memo_chat_hides_repeated_only_citations(monkeypatch, tmp_path):
    from models import (
        MarketGapResponse,
        GapCluster,
        PainPoint,
        OpportunityMemo,
        OpportunityMemoChatRequest,
        OpportunityMemoChatTurn,
    )
    from services import market_gap_service as svc

    monkeypatch.setattr(svc, "MARKET_GAP_STATE_DIR", str(tmp_path))

    cached = MarketGapResponse(
        run_id="gap_chat_repeat",
        from_cache=False,
        retrieval_version="test-version",
        schema_version="memo-v1",
        industry_summary="Industry summary",
        market_structure_summary="Market structure",
        gap_clusters=[
            GapCluster(
                cluster_id="cluster_1",
                theme="Technology System Failures",
                description="Legacy airline systems still fail.",
                frequency=2,
                total_companies=2,
                company_tickers=["AAL", "DAL"],
                evidence_count=2,
                latest_filing_date="2026-02-15",
                incumbents_stuck_reason="Legacy infrastructure and vendor lock-in slow replacement.",
                incumbents_stuck_confidence="high",
                hard_constraints=["Legacy operating systems are difficult to replace"],
                soft_constraints=[],
                buyer_owners=["operations"],
                urgency_level="high",
                persistence_level="recurring",
                adoption_difficulty="medium",
                why_now="Recent filings continue to describe outage risk.",
                disconfirming_evidence=[],
                cluster_score=0.82,
                confidence="high",
                pain_points=[
                    PainPoint(
                        company_ticker="AAL",
                        text="Legacy crew systems and vendor outages disrupt operations.",
                        category="technology",
                        filing_date="2026-02-01",
                        form_type="10-K",
                        accession_number="1000000-26-000001",
                        cik="1000000",
                        chunk_ids=["AAL_2026_item_1a_00"],
                        confidence="high",
                        severity="severe",
                        buyer_owner_hint="operations",
                        recurrence_hint="recurring",
                    ),
                    PainPoint(
                        company_ticker="DAL",
                        text="Aging scheduling systems and vendor dependence create service outages.",
                        category="technology",
                        filing_date="2026-02-15",
                        form_type="10-K",
                        accession_number="2000000-26-000001",
                        cik="2000000",
                        chunk_ids=["DAL_2026_item_1a_00"],
                        confidence="high",
                        severity="severe",
                        buyer_owner_hint="operations",
                        recurrence_hint="worsening",
                    ),
                ],
            )
        ],
        opportunity_memos=[
            OpportunityMemo(
                memo_id="memo_1",
                title="Airline Resilience Layer",
                target_cluster_id="cluster_1",
                opportunity_type="infrastructure_tooling",
                buyer_owner="operations",
                problem="Legacy airline operating systems still create expensive disruption.",
                thesis="A resilience layer could reduce outage spillover.",
                pain_severity="severe",
                urgency_level="high",
                hard_constraint_strength="high",
                adoption_difficulty="medium",
                why_incumbents_are_stuck="Legacy systems are hard to replace quickly.",
                why_now="Recent filings still describe outage risk.",
                why_this_may_fail=["Airline procurement cycles are slow"],
                evidence_chunk_ids=["AAL_2026_item_1a_00", "DAL_2026_item_1a_00"],
                opportunity_status="strong",
                status_rationale="Broad pain and hard constraints create a credible opening.",
                opportunity_score=0.84,
            )
        ],
        coverage_notes=[],
    )
    (tmp_path / "cached.json").write_text(cached.model_dump_json())

    async def fake_completion(*, model, messages, max_retries=3):
        payload = json.loads(messages[1]["content"])
        assert any(item["previously_cited"] is True for item in payload["evidence"])
        return {
            "answer": "The filings still support operational ownership of the problem, but they do not add new memo evidence beyond what was already cited earlier in this chat.",
            "support_level": "supported",
            "citation_chunk_ids": ["AAL_2026_item_1a_00"],
            "note": "The answer reuses earlier evidence only.",
        }

    monkeypatch.setattr(svc, "_create_json_completion", fake_completion)

    result = await svc.answer_opportunity_memo_chat(OpportunityMemoChatRequest(
        run_id="gap_chat_repeat",
        memo_id="memo_1",
        question="Who exactly owns this problem?",
        history=[
            OpportunityMemoChatTurn(role="user", content="Who has this problem?"),
            OpportunityMemoChatTurn(
                role="assistant",
                content="Operations teams appear to own it.",
                citation_chunk_ids=["AAL_2026_item_1a_00", "DAL_2026_item_1a_00"],
            ),
        ],
    ))

    assert result.citations == []
    assert "No additional memo evidence" in result.note


@pytest.mark.asyncio
async def test_market_gap_memo_chat_names_companies_when_supported(monkeypatch, tmp_path):
    from models import (
        MarketGapResponse,
        GapCluster,
        PainPoint,
        OpportunityMemo,
        OpportunityMemoChatRequest,
    )
    from services import market_gap_service as svc

    monkeypatch.setattr(svc, "MARKET_GAP_STATE_DIR", str(tmp_path))

    cached = MarketGapResponse(
        run_id="gap_chat_companies",
        from_cache=False,
        retrieval_version="test-version",
        schema_version="memo-v1",
        industry_summary="Industry summary",
        market_structure_summary="Market structure",
        gap_clusters=[
            GapCluster(
                cluster_id="cluster_1",
                theme="Technology System Failures",
                description="Legacy airline systems still fail.",
                frequency=2,
                total_companies=2,
                company_tickers=["AAL", "DAL"],
                evidence_count=2,
                latest_filing_date="2026-02-15",
                incumbents_stuck_reason="Legacy infrastructure and vendor lock-in slow replacement.",
                incumbents_stuck_confidence="high",
                hard_constraints=["Legacy operating systems are difficult to replace"],
                soft_constraints=[],
                buyer_owners=["operations", "IT"],
                urgency_level="high",
                persistence_level="recurring",
                adoption_difficulty="medium",
                why_now="Recent filings continue to describe outage risk.",
                disconfirming_evidence=[],
                cluster_score=0.82,
                confidence="high",
                pain_points=[
                    PainPoint(
                        company_ticker="AAL",
                        text="Legacy crew systems and vendor outages disrupt operations.",
                        category="technology",
                        filing_date="2026-02-01",
                        form_type="10-K",
                        accession_number="1000000-26-000001",
                        cik="1000000",
                        chunk_ids=["AAL_2026_item_1a_00"],
                        confidence="high",
                        severity="severe",
                        buyer_owner_hint="operations",
                        recurrence_hint="recurring",
                    ),
                    PainPoint(
                        company_ticker="DAL",
                        text="Aging scheduling systems and vendor dependence create service outages.",
                        category="technology",
                        filing_date="2026-02-15",
                        form_type="10-K",
                        accession_number="2000000-26-000001",
                        cik="2000000",
                        chunk_ids=["DAL_2026_item_1a_00"],
                        confidence="high",
                        severity="severe",
                        buyer_owner_hint="IT",
                        recurrence_hint="worsening",
                    ),
                ],
            )
        ],
        opportunity_memos=[
            OpportunityMemo(
                memo_id="memo_1",
                title="Airline Resilience Layer",
                target_cluster_id="cluster_1",
                opportunity_type="infrastructure_tooling",
                buyer_owner="operations",
                problem="Legacy airline operating systems still create expensive disruption.",
                thesis="A resilience layer could reduce outage spillover.",
                pain_severity="severe",
                urgency_level="high",
                hard_constraint_strength="high",
                adoption_difficulty="medium",
                why_incumbents_are_stuck="Legacy systems are hard to replace quickly.",
                why_now="Recent filings still describe outage risk.",
                why_this_may_fail=["Airline procurement cycles are slow"],
                evidence_chunk_ids=["AAL_2026_item_1a_00", "DAL_2026_item_1a_00"],
                opportunity_status="strong",
                status_rationale="Broad pain and hard constraints create a credible opening.",
                opportunity_score=0.84,
            )
        ],
        coverage_notes=[],
    )
    (tmp_path / "cached.json").write_text(cached.model_dump_json())

    async def fake_completion(*, model, messages, max_retries=3):
        payload = json.loads(messages[1]["content"])
        assert payload["gap_cluster"]["company_tickers"] == ["AAL", "DAL"]
        assert len(payload["gap_cluster"]["pain_points"]) == 2
        assert payload["question"] == "What airlines are having these problems?"
        return {
            "answer": "AAL and DAL are the airlines explicitly tied to these operational technology problems in the current filings.",
            "support_level": "supported",
            "citation_chunk_ids": ["AAL_2026_item_1a_00", "DAL_2026_item_1a_00"],
            "note": "Grounded in the memo's cited filing evidence.",
        }

    monkeypatch.setattr(svc, "_create_json_completion", fake_completion)

    result = await svc.answer_opportunity_memo_chat(OpportunityMemoChatRequest(
        run_id="gap_chat_companies",
        memo_id="memo_1",
        question="What airlines are having these problems?",
    ))

    assert result.support_level == "supported"
    assert len(result.citations) == 2
    assert {item.company_ticker for item in result.citations} == {"AAL", "DAL"}
    assert "AAL and DAL" in result.answer


def test_market_gap_chat_endpoint(monkeypatch):
    from main import app
    from models import OpportunityMemoChatResponse, OpportunityMemoCitation
    from routes import market_gap as market_gap_route

    async def fake_memo_chat(req):
        return OpportunityMemoChatResponse(
            run_id=req.run_id,
            memo_id=req.memo_id,
            answer="The current filings point to operations and IT owning the outage problem.",
            support_level="supported",
            citations=[
                OpportunityMemoCitation(
                    chunk_id="AAL_2026_item_1a_00",
                    company_ticker="AAL",
                    form_type="10-K",
                    filing_date="2026-02-01",
                    accession_number="1000000-26-000001",
                    cik="1000000",
                    excerpt="Legacy crew systems and vendor outages disrupt operations.",
                )
            ],
            note="Grounded in memo evidence.",
        )

    monkeypatch.setattr(market_gap_route, "answer_opportunity_memo_chat", fake_memo_chat)
    client = TestClient(app)

    res = client.post("/api/market-gap/chat", json={
        "run_id": "gap_chat_1",
        "memo_id": "memo_1",
        "question": "Who exactly has this problem?",
        "history": [],
    })

    assert res.status_code == 200
    data = res.json()
    assert data["memo_id"] == "memo_1"
    assert data["support_level"] == "supported"
    assert data["citations"][0]["chunk_id"] == "AAL_2026_item_1a_00"
