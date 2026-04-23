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
