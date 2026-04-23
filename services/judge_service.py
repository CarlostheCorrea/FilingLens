from __future__ import annotations

import json

from answer_workflow import _create_json_completion
from config import JUDGE_SYSTEM_PROMPT, OPENAI_JUDGE_MODEL
from models import JudgeEvaluation, WorkflowAnswerResponse
import rag_pipeline


def _answer_text(result: WorkflowAnswerResponse) -> str:
    answer = result.answer
    parts: list[str] = []

    summary = (answer.overall_answer.summary or "").strip()
    if summary:
        parts.append(f"Overall summary:\n{summary}")

    if answer.overall_answer.key_points:
        parts.append(
            "Key points:\n" + "\n".join(
                f"- {point.text}" for point in answer.overall_answer.key_points if point.text
            )
        )

    if answer.company_deep_dives:
        parts.append(
            "Company deep dives:\n" + "\n".join(
                f"- {dive.ticker}: {dive.summary}" for dive in answer.company_deep_dives if dive.summary
            )
        )

    if answer.coverage_notes:
        parts.append(
            "Coverage notes:\n" + "\n".join(f"- {note}" for note in answer.coverage_notes)
        )

    return "\n\n".join(parts).strip()


def _evidence_payload(result: WorkflowAnswerResponse) -> list[dict]:
    seen: set[str] = set()
    evidence: list[dict] = []

    for claim in result.answer.claims_audit.claims:
        for chunk_id in claim.supporting_chunk_ids:
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            chunk = rag_pipeline.get_chunk_by_id(chunk_id)
            if not chunk:
                continue
            evidence.append({
                "chunk_id": chunk_id,
                "company_ticker": chunk.metadata.company_ticker,
                "form_type": chunk.metadata.form_type,
                "filing_date": chunk.metadata.filing_date,
                "item_section": chunk.metadata.item_section,
                "excerpt": chunk.text[:600],
            })

    return evidence


def _coerce_score(value, default: int = 3) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(score, 5))


def _coerce_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


async def judge_answer(result: WorkflowAnswerResponse) -> JudgeEvaluation:
    answer_text = _answer_text(result)
    evidence = _evidence_payload(result)
    claims = [claim.model_dump() for claim in result.answer.claims_audit.claims]

    if not answer_text:
        return JudgeEvaluation(
            helpfulness=1,
            clarity=1,
            grounding=1,
            citation_quality=1,
            overclaiming_risk="medium",
            overall_verdict="weak",
            summary="The answer did not contain enough content for meaningful judge evaluation.",
            strengths=[],
            concerns=["No answer text was available to evaluate."],
        )

    raw = await _create_json_completion(
        model=OPENAI_JUDGE_MODEL,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Question:\n{result.query}\n\n"
                    f"Generated answer:\n{answer_text}\n\n"
                    f"Audit claims:\n{json.dumps(claims, indent=2)}\n\n"
                    f"Supporting evidence excerpts:\n{json.dumps(evidence, indent=2)}"
                ),
            },
        ],
    )

    return JudgeEvaluation(
        helpfulness=_coerce_score(raw.get("helpfulness")),
        clarity=_coerce_score(raw.get("clarity")),
        grounding=_coerce_score(raw.get("grounding")),
        citation_quality=_coerce_score(raw.get("citation_quality")),
        overclaiming_risk=str(raw.get("overclaiming_risk", "medium")).strip().lower() or "medium",
        overall_verdict=str(raw.get("overall_verdict", "mixed")).strip().lower() or "mixed",
        summary=raw.get("summary", ""),
        strengths=_coerce_list(raw.get("strengths")),
        concerns=_coerce_list(raw.get("concerns")),
    )
