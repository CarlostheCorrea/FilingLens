import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.mark.asyncio
async def test_judge_answer_builds_scores_from_response(monkeypatch):
    from models import (
        WorkflowAnswerResponse,
        WorkflowMetadata,
        WorkflowStage,
        StructuredAnswerPayload,
        OverallAnswer,
        OverallKeyPoint,
        CompanyDeepDive,
        CompanyEvidenceItem,
        ClaimsAuditPayload,
        Claim,
    )
    from services import judge_service

    class FakeChunk:
        text = "The company cites premiumization and pricing as growth levers."
        metadata = type("Meta", (), {
            "company_ticker": "TAP",
            "form_type": "10-K",
            "filing_date": "2026-02-18",
            "item_section": "item_1",
        })()

    monkeypatch.setattr(judge_service.rag_pipeline, "get_chunk_by_id", lambda chunk_id: FakeChunk())

    async def fake_completion(*, model, messages, max_retries=3):
        return {
            "helpfulness": 4,
            "clarity": "5",
            "grounding": 4,
            "citation_quality": 3,
            "overclaiming_risk": "LOW",
            "overall_verdict": "STRONG",
            "summary": "The answer is strong overall and mostly grounded.",
            "strengths": ["Direct summary", "Reasonable evidence use"],
            "concerns": ["Could cite more evidence for some synthesis"],
        }

    monkeypatch.setattr(judge_service, "_create_json_completion", fake_completion)

    result = WorkflowAnswerResponse(
        proposal_id="scope_test",
        query="How are these companies trying to grow profit?",
        workflow=WorkflowMetadata(
            run_id="ans_test",
            stages=[WorkflowStage(name="finalize", status="completed", summary="done")],
        ),
        answer=StructuredAnswerPayload(
            overall_answer=OverallAnswer(
                summary="Both companies emphasize pricing and premium mix.",
                key_points=[OverallKeyPoint(text="Pricing power is a recurring theme.", supporting_tickers=["TAP", "STZ"])],
            ),
            company_deep_dives=[
                CompanyDeepDive(
                    ticker="TAP",
                    company_name="Molson Coors Beverage Company",
                    status="supported",
                    summary="TAP emphasizes pricing and premiumization.",
                    evidence=[
                        CompanyEvidenceItem(
                            chunk_id="TAP_10K_2026_item_1_00",
                            excerpt="Pricing and premiumization remain important.",
                            company_ticker="TAP",
                            company_name="Molson Coors Beverage Company",
                            cik="24545",
                            accession_number="24545-26-000001",
                            form_type="10-K",
                            filing_date="2026-02-18",
                            item_section="item_1",
                        )
                    ],
                    gaps=[],
                )
            ],
            claims_audit=ClaimsAuditPayload(
                claims=[
                    Claim(
                        claim_id="merged_claim_1",
                        text="Pricing is a common growth lever.",
                        supporting_chunk_ids=["TAP_10K_2026_item_1_00"],
                        confidence="high",
                    )
                ]
            ),
            coverage_notes=[],
        ),
    )

    judge = await judge_service.judge_answer(result)

    assert judge.helpfulness == 4
    assert judge.clarity == 5
    assert judge.overall_verdict == "strong"
    assert judge.overclaiming_risk == "low"
    assert judge.strengths


@pytest.mark.asyncio
async def test_judge_answer_handles_empty_answer():
    from models import (
        WorkflowAnswerResponse,
        WorkflowMetadata,
        StructuredAnswerPayload,
        OverallAnswer,
        ClaimsAuditPayload,
    )
    from services import judge_service

    result = WorkflowAnswerResponse(
        proposal_id="scope_test",
        query="What is the answer?",
        workflow=WorkflowMetadata(run_id="ans_test", stages=[]),
        answer=StructuredAnswerPayload(
            overall_answer=OverallAnswer(summary=""),
            company_deep_dives=[],
            claims_audit=ClaimsAuditPayload(claims=[]),
            coverage_notes=[],
        ),
    )

    judge = await judge_service.judge_answer(result)

    assert judge.overall_verdict == "weak"
    assert judge.helpfulness == 1
