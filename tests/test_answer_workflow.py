import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.mark.asyncio
async def test_run_answer_workflow_returns_structured_answer(monkeypatch):
    import answer_workflow

    class FakeGraph:
        async def ainvoke(self, initial_state):
            return {
                **initial_state,
                "final_claims": [{
                    "claim_id": "merged_claim_1",
                    "text": "Consumer preference shifts are a common risk.",
                    "supporting_chunk_ids": ["TAP_10K_2026_item_1a_00"],
                    "confidence": "high",
                }],
                "final_overall_answer": {
                    "summary": "Across the sampled companies, consumer shifts are a recurring theme.",
                    "key_points": [
                        {
                            "text": "Premiumization and non-alcoholic substitution appear repeatedly.",
                            "supporting_tickers": ["TAP", "BUD"],
                        }
                    ],
                },
                "final_company_deep_dives": [{
                    "ticker": "TAP",
                    "company_name": "Molson Coors Beverage Company",
                    "status": "supported",
                    "summary": "TAP discusses shifting consumer preferences and premium mix pressure.",
                    "evidence": [{
                        "chunk_id": "TAP_10K_2026_item_1a_00",
                        "excerpt": "Consumer preferences are evolving rapidly.",
                        "company_ticker": "TAP",
                        "company_name": "Molson Coors Beverage Company",
                        "cik": "24545",
                        "accession_number": "24545-26-000001",
                        "form_type": "10-K",
                        "filing_date": "2026-02-18",
                        "item_section": "item_1a",
                    }],
                    "gaps": [],
                }, {
                    "ticker": "ABEV",
                    "company_name": "Ambev S.A.",
                    "status": "insufficient_evidence",
                    "summary": "No relevant indexed filing evidence was found for Ambev S.A.",
                    "evidence": [],
                    "gaps": ["No indexed filing data found for ABEV."],
                }],
                "final_coverage_notes": ["The available excerpts do not rank risks quantitatively."],
                "stages": [
                    {"name": "load_context", "status": "completed", "summary": "Loaded 2 approved companies"},
                    {"name": "finalize", "status": "completed", "summary": "Workflow complete"},
                ],
            }

    monkeypatch.setattr(answer_workflow, "get_graph", lambda: FakeGraph())

    result = await answer_workflow.run_answer_workflow(
        "scope_test",
        "What are the biggest risks to the beer industry?",
        [
            {"ticker": "TAP", "name": "Molson Coors Beverage Company", "cik": "24545"},
            {"ticker": "ABEV", "name": "Ambev S.A.", "cik": "1029800"},
        ],
    )

    assert result.answer.overall_answer.summary
    assert len(result.answer.overall_answer.key_points) == 1
    assert len(result.answer.company_deep_dives) == 2
    assert result.answer.company_deep_dives[1].status == "insufficient_evidence"
    assert result.answer.claims_audit.claims[0].claim_id == "merged_claim_1"
    assert result.answer.coverage_notes == ["The available excerpts do not rank risks quantitatively."]


@pytest.mark.asyncio
async def test_create_json_completion_retries_rate_limit(monkeypatch):
    import answer_workflow
    from openai import RateLimitError

    calls = {"count": 0}
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    class FakeResponse:
        def __init__(self, content):
            self.choices = [type("Choice", (), {"message": type("Msg", (), {"content": content})()})]

    async def fake_create(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
            response = httpx.Response(429, request=request, headers={"retry-after-ms": "750"})
            raise RateLimitError("rate limit", response=response, body={})
        return FakeResponse('{"ok": true}')

    monkeypatch.setattr(answer_workflow.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(answer_workflow._openai.chat.completions, "create", fake_create)

    result = await answer_workflow._create_json_completion(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hello"}],
    )

    assert result == {"ok": True}
    assert calls["count"] == 2
    assert sleeps == [0.75]


@pytest.mark.asyncio
async def test_company_worker_uses_worker_model(monkeypatch):
    import answer_workflow

    captured = {}

    monkeypatch.setattr(answer_workflow.rag_pipeline, "retrieve", lambda query, k, tickers: [
        type("Chunk", (), {
            "chunk_id": "TAP_10K_2026_item_1a_00",
            "text": "Consumer preferences are evolving rapidly.",
            "metadata": type("Meta", (), {
                "form_type": "10-K",
                "filing_date": "2026-02-18",
                "item_section": "item_1a",
            })(),
        })()
    ])

    async def fake_completion(*, model, messages, max_retries=3):
        captured["model"] = model
        return {
            "summary": "TAP highlights consumer preference shifts.",
            "claims": [],
            "evidence_chunk_ids": ["TAP_10K_2026_item_1a_00"],
            "gaps": [],
        }

    monkeypatch.setattr(answer_workflow, "_create_json_completion", fake_completion)

    result = await answer_workflow.company_worker({
        "ticker": "TAP",
        "query": "How will these companies grow profit?",
        "company_name": "Molson Coors Beverage Company",
        "proposal_id": "scope_test",
    })

    assert captured["model"] == answer_workflow.OPENAI_WORKER_MODEL
    assert result["worker_results"][0]["status"] == "ok"
