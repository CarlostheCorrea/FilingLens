import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.mark.asyncio
async def test_answer_backfills_judge_for_cached_answers(monkeypatch, tmp_path):
    from services import answer_service
    from models import JudgeEvaluation

    cache_path = tmp_path / "scope_test_answer_cache.json"
    cached_payload = {
        "proposal_id": "scope_test",
        "query": "How do these companies grow?",
        "from_cache": False,
        "retrieval_version": "2026-04-window-scout-v1",
        "workflow": {
            "pattern": "supervisor",
            "run_id": "ans_test",
            "status": "completed",
            "stages": [],
        },
        "answer": {
            "overall_answer": {
                "summary": "They discuss pricing and mix.",
                "key_points": [],
            },
            "company_deep_dives": [],
            "claims_audit": {
                "claims": [],
            },
            "coverage_notes": [],
        },
    }
    cache_path.write_text(json.dumps(cached_payload))

    monkeypatch.setattr(answer_service, "_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(answer_service, "_answer_key", lambda query: "cache")
    monkeypatch.setattr(answer_service.hitl, "save_answer", lambda *args, **kwargs: None)

    async def fake_judge(result):
        return JudgeEvaluation(
            helpfulness=4,
            clarity=4,
            grounding=4,
            citation_quality=4,
            overclaiming_risk="low",
            overall_verdict="strong",
            summary="Strong answer.",
            strengths=["Good structure"],
            concerns=[],
        )

    monkeypatch.setattr(answer_service, "judge_answer", fake_judge)

    result, from_cache = await answer_service.answer("scope_test", "How do these companies grow?")

    assert from_cache is True
    assert result.from_cache is True
    assert result.answer.judge_evaluation is not None


@pytest.mark.asyncio
async def test_answer_invalidates_stale_cache_version(monkeypatch, tmp_path):
    from services import answer_service
    from models import WorkflowAnswerResponse, WorkflowMetadata, StructuredAnswerPayload, OverallAnswer, ClaimsAuditPayload, WorkflowStage

    cache_path = tmp_path / "scope_test_answer_cache.json"
    cache_path.write_text(json.dumps({
        "proposal_id": "scope_test",
        "query": "How do these companies grow?",
        "from_cache": False,
        "retrieval_version": "old-version",
        "workflow": {"pattern": "supervisor", "run_id": "ans_old", "status": "completed", "stages": []},
        "answer": {
            "overall_answer": {"summary": "Old", "key_points": []},
            "company_deep_dives": [],
            "claims_audit": {"claims": []},
            "coverage_notes": [],
        },
    }))

    monkeypatch.setattr(answer_service, "_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(answer_service, "_answer_key", lambda query: "cache")
    monkeypatch.setattr(answer_service.hitl, "load_approved_scope", lambda proposal_id: type("Approved", (), {
        "approved_companies": [type("Company", (), {"ticker": "AAA", "name": "AAA Corp", "cik": "1000"})()]
    })())
    monkeypatch.setattr(answer_service.hitl, "save_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(answer_service.hitl, "save_question", lambda *args, **kwargs: None)
    monkeypatch.setattr(answer_service.rag_pipeline, "retrieve", lambda *args, **kwargs: [])
    monkeypatch.setattr(answer_service.logging_utils, "log_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(answer_service.logging_utils, "log_judge", lambda *args, **kwargs: None)

    async def fake_ensure_vectors_current(proposal_id, companies):
        return ["1000-26-000001"]

    monkeypatch.setattr(answer_service, "_ensure_vectors_current", fake_ensure_vectors_current)

    async def fake_run_answer_workflow(proposal_id, query, companies):
        return WorkflowAnswerResponse(
            proposal_id=proposal_id,
            query=query,
            retrieval_version="",
            workflow=WorkflowMetadata(
                run_id="ans_new",
                stages=[WorkflowStage(name="load_context", status="completed", summary="Loaded 1 approved companies (1 with tickers)")],
            ),
            answer=StructuredAnswerPayload(
                overall_answer=OverallAnswer(summary="Fresh answer", key_points=[]),
                company_deep_dives=[],
                claims_audit=ClaimsAuditPayload(claims=[]),
                coverage_notes=[],
            ),
        )

    async def fake_judge(result):
        return None

    monkeypatch.setattr(answer_service, "run_answer_workflow", fake_run_answer_workflow)
    monkeypatch.setattr(answer_service, "judge_answer", fake_judge)

    result, from_cache = await answer_service.answer("scope_test", "How do these companies grow?")

    assert from_cache is False
    assert result.from_cache is False
    assert result.retrieval_version == answer_service.VECTOR_SCHEMA_VERSION
    assert result.workflow.stages[0].name == "refresh_index"
