import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _sample_result():
    from models import (
        Claim,
        ClaimsAuditPayload,
        CompanyDeepDive,
        OverallAnswer,
        StructuredAnswerPayload,
        WorkflowAnswerResponse,
        WorkflowMetadata,
    )

    return WorkflowAnswerResponse(
        proposal_id="scope_test",
        query="How are these companies trying to grow profit?",
        workflow=WorkflowMetadata(run_id="ans_test", stages=[]),
        answer=StructuredAnswerPayload(
            overall_answer=OverallAnswer(
                summary="Both companies emphasize pricing and premium mix."
            ),
            company_deep_dives=[
                CompanyDeepDive(
                    ticker="TAP",
                    company_name="Molson Coors Beverage Company",
                    status="supported",
                    summary="TAP emphasizes pricing and premiumization.",
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


@pytest.mark.asyncio
async def test_ragas_answer_scores_reference_free_metrics(monkeypatch):
    from services import ragas_service

    class FakeChunk:
        text = "The company cites premiumization and pricing as growth levers."

    monkeypatch.setattr(ragas_service.rag_pipeline, "get_chunk_by_id", lambda chunk_id: FakeChunk())

    class FakeMetricResult:
        def __init__(self, value):
            self.value = value

    class FakeFaithfulness:
        def __init__(self, llm=None):
            self.llm = llm

        async def ascore(self, **kwargs):
            assert kwargs["retrieved_contexts"]
            return FakeMetricResult(0.91)

    class FakeAnswerRelevancy:
        def __init__(self, llm=None, embeddings=None):
            self.llm = llm
            self.embeddings = embeddings

        async def ascore(self, **kwargs):
            assert kwargs["response"]
            return FakeMetricResult(0.87)

    class FakeContextUtilization:
        def __init__(self, llm=None):
            self.llm = llm

        async def ascore(self, **kwargs):
            assert kwargs["retrieved_contexts"]
            return FakeMetricResult(0.79)

    monkeypatch.setattr(
        ragas_service,
        "_load_ragas_runtime",
        lambda: {
            "Faithfulness": FakeFaithfulness,
            "AnswerRelevancy": FakeAnswerRelevancy,
            "ContextUtilization": FakeContextUtilization,
            "llm": object(),
            "embeddings": object(),
        },
    )

    evaluation = await ragas_service.evaluate_answer_ragas(_sample_result())

    assert evaluation.status == "available"
    assert evaluation.faithfulness == 0.91
    assert evaluation.answer_relevancy == 0.87
    assert evaluation.context_utilization == 0.79
    assert evaluation.overall_score == 0.857
    assert evaluation.metrics_run == ["answer_relevancy", "faithfulness", "context_utilization"]


@pytest.mark.asyncio
async def test_ragas_answer_handles_missing_package(monkeypatch):
    from services import ragas_service

    class FakeChunk:
        text = "The company cites premiumization and pricing as growth levers."

    monkeypatch.setattr(ragas_service.rag_pipeline, "get_chunk_by_id", lambda chunk_id: FakeChunk())
    monkeypatch.setattr(
        ragas_service,
        "_load_ragas_runtime",
        lambda: (_ for _ in ()).throw(ImportError("ragas missing")),
    )

    evaluation = await ragas_service.evaluate_answer_ragas(_sample_result())

    assert evaluation.status == "unavailable"
    assert "package is not installed" in evaluation.summary.lower()


@pytest.mark.asyncio
async def test_ragas_answer_handles_length_limited_metric_and_returns_partial(monkeypatch):
    from services import ragas_service

    class FakeChunk:
        text = "The company cites premiumization and pricing as growth levers." * 100

    monkeypatch.setattr(ragas_service.rag_pipeline, "get_chunk_by_id", lambda chunk_id: FakeChunk())

    class FakeMetricResult:
        def __init__(self, value):
            self.value = value

    class FakeFaithfulness:
        def __init__(self, llm=None):
            self.llm = llm

        async def ascore(self, **kwargs):
            assert kwargs["response"].startswith("Audit claims:")
            assert len(kwargs["response"]) <= ragas_service._MAX_GROUNDING_RESPONSE_CHARS + 1
            raise RuntimeError("The output is incomplete due to a max_tokens length limit. finish_reason='length'")

    class FakeAnswerRelevancy:
        def __init__(self, llm=None, embeddings=None):
            self.llm = llm
            self.embeddings = embeddings

        async def ascore(self, **kwargs):
            assert len(kwargs["response"]) <= ragas_service._MAX_ANSWER_CHARS + 1
            return FakeMetricResult(0.84)

    class FakeContextUtilization:
        def __init__(self, llm=None):
            self.llm = llm

        async def ascore(self, **kwargs):
            assert len(kwargs["retrieved_contexts"]) <= ragas_service._MAX_CONTEXTS
            assert all(len(text) <= ragas_service._MAX_CONTEXT_CHARS + 1 for text in kwargs["retrieved_contexts"])
            return FakeMetricResult(0.81)

    monkeypatch.setattr(
        ragas_service,
        "_load_ragas_runtime",
        lambda: {
            "Faithfulness": FakeFaithfulness,
            "AnswerRelevancy": FakeAnswerRelevancy,
            "ContextUtilization": FakeContextUtilization,
            "llm": object(),
            "embeddings": object(),
        },
    )

    evaluation = await ragas_service.evaluate_answer_ragas(_sample_result())

    assert evaluation.status == "partial"
    assert evaluation.answer_relevancy == 0.84
    assert evaluation.context_utilization == 0.81
    assert evaluation.faithfulness is None
    assert evaluation.overall_score is None
    assert "faithfulness could not be confirmed" in evaluation.summary.lower()
    assert any("output budget" in concern.lower() for concern in evaluation.concerns)
