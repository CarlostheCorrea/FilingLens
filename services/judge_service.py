from __future__ import annotations

import json

from answer_workflow import _create_json_completion
from config import (
    CHANGE_JUDGE_SYSTEM_PROMPT,
    COMPARE_JUDGE_SYSTEM_PROMPT,
    JUDGE_SYSTEM_PROMPT,
    MARKET_GAP_JUDGE_SYSTEM_PROMPT,
    OPENAI_JUDGE_MODEL,
)
from models import (
    ChangeIntelligenceResponse,
    CompareResponse,
    JudgeEvaluation,
    MarketGapResponse,
    WorkflowAnswerResponse,
)
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


def _build_judge_evaluation(raw: dict) -> JudgeEvaluation:
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


# ── Compare Two Companies Judge ───────────────────────────────────────────────

def _compare_answer_text(result: CompareResponse) -> str:
    parts: list[str] = []
    if result.overall_summary:
        parts.append(f"Overall summary:\n{result.overall_summary}")
    if result.similarities:
        parts.append("Similarities:\n" + "\n".join(f"- {s}" for s in result.similarities))
    if result.differences:
        parts.append("Differences:\n" + "\n".join(f"- {d}" for d in result.differences))
    for comp in result.company_comparisons:
        if comp.summary:
            parts.append(f"{comp.ticker} ({comp.company_name}):\n{comp.summary}")
        if comp.gaps:
            parts.append(f"{comp.ticker} gaps:\n" + "\n".join(f"- {g}" for g in comp.gaps))
    return "\n\n".join(parts).strip()


def _compare_evidence_payload(result: CompareResponse) -> list[dict]:
    seen: set[str] = set()
    evidence: list[dict] = []
    for comp in result.company_comparisons:
        for item in comp.evidence:
            if item.chunk_id in seen:
                continue
            seen.add(item.chunk_id)
            evidence.append({
                "chunk_id": item.chunk_id,
                "company_ticker": comp.ticker,
                "form_type": item.form_type,
                "filing_date": item.filing_date,
                "item_section": item.item_section,
                "excerpt": item.excerpt[:500],
            })
    return evidence


async def judge_compare(result: CompareResponse, query: str) -> JudgeEvaluation:
    answer_text = _compare_answer_text(result)
    if not answer_text:
        return JudgeEvaluation(
            helpfulness=1, clarity=1, grounding=1, citation_quality=1,
            overclaiming_risk="medium", overall_verdict="weak",
            summary="The comparison did not contain enough content for meaningful judge evaluation.",
            strengths=[], concerns=["No comparison text was available to evaluate."],
        )
    evidence = _compare_evidence_payload(result)
    companies_payload = [
        {"ticker": comp.ticker, "status": comp.status, "summary": comp.summary}
        for comp in result.company_comparisons
    ]
    raw = await _create_json_completion(
        model=OPENAI_JUDGE_MODEL,
        messages=[
            {"role": "system", "content": COMPARE_JUDGE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Comparison question:\n{query}\n\n"
                    f"Comparison output:\n{answer_text}\n\n"
                    f"Company-level analysis:\n{json.dumps(companies_payload, indent=2)}\n\n"
                    f"Evidence excerpts:\n{json.dumps(evidence, indent=2)}"
                ),
            },
        ],
    )
    return _build_judge_evaluation(raw)


# ── Filing Change Intelligence Judge ─────────────────────────────────────────

def _change_answer_text(result: ChangeIntelligenceResponse) -> str:
    parts: list[str] = []
    if result.overall_summary:
        parts.append(f"Overall summary:\n{result.overall_summary}")
    for window in result.comparison_windows:
        if window.summary:
            parts.append(f"Window [{window.label}]:\n{window.summary}")
    for card in result.change_cards:
        parts.append(
            f"Change [{card.category}] importance={card.importance} confidence={card.confidence}:\n{card.summary}"
        )
    return "\n\n".join(parts).strip()


def _change_evidence_payload(result: ChangeIntelligenceResponse) -> list[dict]:
    seen: set[str] = set()
    evidence: list[dict] = []
    for card in result.change_cards:
        for item in card.before_evidence + card.after_evidence:
            if item.chunk_id in seen:
                continue
            seen.add(item.chunk_id)
            evidence.append({
                "chunk_id": item.chunk_id,
                "form_type": item.form_type,
                "filing_date": item.filing_date,
                "item_section": item.item_section,
                "excerpt": item.excerpt[:500],
            })
    return evidence


async def judge_change(result: ChangeIntelligenceResponse, query: str) -> JudgeEvaluation:
    answer_text = _change_answer_text(result)
    if not answer_text:
        return JudgeEvaluation(
            helpfulness=1, clarity=1, grounding=1, citation_quality=1,
            overclaiming_risk="medium", overall_verdict="weak",
            summary="The change intelligence output did not contain enough content for evaluation.",
            strengths=[], concerns=["No change analysis text was available to evaluate."],
        )
    evidence = _change_evidence_payload(result)
    cards_payload = [
        {
            "change_id": card.change_id,
            "category": card.category,
            "importance": card.importance,
            "confidence": card.confidence,
            "summary": card.summary,
        }
        for card in result.change_cards
    ]
    raw = await _create_json_completion(
        model=OPENAI_JUDGE_MODEL,
        messages=[
            {"role": "system", "content": CHANGE_JUDGE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Analysis lens:\n{query}\n\n"
                    f"Change analysis output:\n{answer_text}\n\n"
                    f"Structured change cards:\n{json.dumps(cards_payload, indent=2)}\n\n"
                    f"Evidence excerpts:\n{json.dumps(evidence, indent=2)}"
                ),
            },
        ],
    )
    return _build_judge_evaluation(raw)


# ── Market Gap Discovery Judge ────────────────────────────────────────────────

def _gap_answer_text(result: MarketGapResponse) -> str:
    parts: list[str] = []
    if result.industry_summary:
        parts.append(f"Industry summary:\n{result.industry_summary}")
    if result.market_structure_summary:
        parts.append(f"Market structure summary:\n{result.market_structure_summary}")
    for cluster in result.gap_clusters:
        parts.append(
            f"Cluster [{cluster.confidence} confidence | score={cluster.cluster_score}]:\n"
            f"Theme: {cluster.theme}\n"
            f"Description: {cluster.description}\n"
            f"Companies: {', '.join(cluster.company_tickers)}\n"
            f"Structural constraint ({cluster.incumbents_stuck_confidence}): {cluster.incumbents_stuck_reason}"
        )
    for memo in result.opportunity_memos:
        parts.append(
            f"Opportunity Memo [{memo.opportunity_status}]:\n"
            f"Title: {memo.title}\n"
            f"Problem: {memo.problem}\n"
            f"Thesis: {memo.thesis}\n"
            f"Why this may fail: {'; '.join(memo.why_this_may_fail)}"
        )
    return "\n\n".join(parts).strip()


async def judge_market_gap(result: MarketGapResponse, query: str) -> JudgeEvaluation:
    answer_text = _gap_answer_text(result)
    if not answer_text:
        return JudgeEvaluation(
            helpfulness=1, clarity=1, grounding=1, citation_quality=1,
            overclaiming_risk="medium", overall_verdict="weak",
            summary="The market gap analysis did not contain enough content for meaningful evaluation.",
            strengths=[], concerns=["No analysis content was available to evaluate."],
        )
    clusters_payload = [
        {
            "theme": c.theme,
            "description": c.description,
            "company_tickers": c.company_tickers,
            "frequency": f"{c.frequency}/{c.total_companies}",
            "incumbents_stuck_confidence": c.incumbents_stuck_confidence,
            "incumbents_stuck_reason": c.incumbents_stuck_reason,
            "hard_constraints": c.hard_constraints,
            "cluster_score": c.cluster_score,
        }
        for c in result.gap_clusters
    ]
    memos_payload = [
        {
            "title": m.title,
            "opportunity_status": m.opportunity_status,
            "status_rationale": m.status_rationale,
            "opportunity_type": m.opportunity_type,
            "thesis": m.thesis,
            "why_this_may_fail": m.why_this_may_fail,
        }
        for m in result.opportunity_memos
    ]
    raw = await _create_json_completion(
        model=OPENAI_JUDGE_MODEL,
        messages=[
            {"role": "system", "content": MARKET_GAP_JUDGE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Sector query:\n{query}\n\n"
                    f"Analysis output:\n{answer_text}\n\n"
                    f"Gap clusters:\n{json.dumps(clusters_payload, indent=2)}\n\n"
                    f"Opportunity memos:\n{json.dumps(memos_payload, indent=2)}"
                ),
            },
        ],
    )
    return _build_judge_evaluation(raw)
