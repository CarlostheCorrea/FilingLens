from __future__ import annotations

from openai import AsyncOpenAI

from config import OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL, OPENAI_RAGAS_MODEL
from models import RagasEvaluation, WorkflowAnswerResponse
import rag_pipeline

_openai = AsyncOpenAI(api_key=OPENAI_API_KEY)
_MAX_ANSWER_CHARS = 2_200
_MAX_GROUNDING_RESPONSE_CHARS = 1_400
_MAX_DEEP_DIVES = 3
_MAX_CONTEXTS = 4
_MAX_CONTEXT_CHARS = 900


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
                f"- {dive.ticker}: {dive.summary}"
                for dive in answer.company_deep_dives[:_MAX_DEEP_DIVES]
                if dive.summary
            )
        )

    if answer.coverage_notes:
        parts.append(
            "Coverage notes:\n" + "\n".join(f"- {note}" for note in answer.coverage_notes)
        )

    text = "\n\n".join(parts).strip()
    if len(text) > _MAX_ANSWER_CHARS:
        text = text[:_MAX_ANSWER_CHARS].rsplit(" ", 1)[0].rstrip() + "…"
    return text


def _grounding_response_text(result: WorkflowAnswerResponse) -> str:
    claims = [
        claim.text.strip()
        for claim in result.answer.claims_audit.claims
        if claim.text and claim.text.strip()
    ]
    if claims:
        text = "Audit claims:\n" + "\n".join(f"- {claim}" for claim in claims)
    else:
        text = _answer_text(result)
    if len(text) > _MAX_GROUNDING_RESPONSE_CHARS:
        text = text[:_MAX_GROUNDING_RESPONSE_CHARS].rsplit(" ", 1)[0].rstrip() + "…"
    return text


def _retrieved_contexts(result: WorkflowAnswerResponse) -> list[str]:
    seen: set[str] = set()
    contexts: list[str] = []

    for claim in result.answer.claims_audit.claims:
        for chunk_id in claim.supporting_chunk_ids:
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            chunk = rag_pipeline.get_chunk_by_id(chunk_id)
            if not chunk or not chunk.text:
                continue
            text = chunk.text.strip()
            if len(text) > _MAX_CONTEXT_CHARS:
                text = text[:_MAX_CONTEXT_CHARS].rsplit(" ", 1)[0].rstrip() + "…"
            contexts.append(text)
            if len(contexts) >= _MAX_CONTEXTS:
                return contexts

    return contexts


def _load_ragas_runtime():
    from ragas.embeddings.base import embedding_factory
    from ragas.llms import llm_factory
    from ragas.metrics.collections import AnswerRelevancy, ContextUtilization, Faithfulness

    llm = llm_factory(OPENAI_RAGAS_MODEL, client=_openai)
    embeddings = embedding_factory(
        "openai",
        model=OPENAI_EMBEDDING_MODEL,
        client=_openai,
    )
    return {
        "Faithfulness": Faithfulness,
        "AnswerRelevancy": AnswerRelevancy,
        "ContextUtilization": ContextUtilization,
        "llm": llm,
        "embeddings": embeddings,
    }


def _metric_value(result) -> float | None:
    value = getattr(result, "value", result)
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return None


def _rounded(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 3)


def _build_summary(scores: dict[str, float | None]) -> tuple[str, list[str]]:
    concerns: list[str] = []

    faithfulness = scores.get("faithfulness")
    answer_relevancy = scores.get("answer_relevancy")
    context_utilization = scores.get("context_utilization")

    if faithfulness is not None and faithfulness < 0.75:
        concerns.append("Some answer claims may not be fully supported by the cited filing excerpts.")
    if answer_relevancy is not None and answer_relevancy < 0.75:
        concerns.append("The answer may not directly address the research question tightly enough.")
    if context_utilization is not None and context_utilization < 0.75:
        concerns.append("The retrieved filing chunks may not be used or ranked effectively for the final answer.")

    available = [score for score in scores.values() if score is not None]
    if not available:
        return (
            "RAGAS could not produce any metric scores for this answer.",
            concerns or ["No RAGAS metrics were available to score this answer."],
        )

    if faithfulness is None and (answer_relevancy is not None or context_utilization is not None):
        return (
            "RAGAS partially evaluated this answer. Relevancy and context-use signals are available, but faithfulness could not be confirmed.",
            concerns,
        )

    overall = sum(available) / len(available)
    if overall >= 0.88:
        summary = "RAGAS indicates the answer is highly relevant and well-grounded in retrieved filing evidence."
    elif overall >= 0.75:
        summary = "RAGAS indicates the answer is generally solid, with moderate room to improve grounding or retrieval use."
    else:
        summary = "RAGAS indicates meaningful weakness in answer grounding, retrieval use, or question alignment."

    return summary, concerns


def _ragas_failure_note(metric_name: str, exc: Exception) -> str:
    text = str(exc)
    lower = text.lower()
    if "finish_reason='length'" in text or "finish_reason=\"length\"" in text or "max_tokens" in lower:
        return f"{metric_name} could not complete because the evaluation prompt exceeded the model output budget."
    return f"{metric_name} could not be scored: {text}"


async def _safe_metric(metric_name: str, scorer, **kwargs) -> tuple[float | None, str | None]:
    try:
        result = await scorer.ascore(**kwargs)
        return _metric_value(result), None
    except Exception as exc:
        return None, _ragas_failure_note(metric_name, exc)


async def evaluate_answer_ragas(result: WorkflowAnswerResponse) -> RagasEvaluation:
    answer_text = _answer_text(result)
    if not answer_text:
        return RagasEvaluation(
            status="unavailable",
            summary="RAGAS could not run because there was no answer text to evaluate.",
            concerns=["No generated answer text was available for RAGAS scoring."],
        )

    contexts = _retrieved_contexts(result)
    grounding_text = _grounding_response_text(result)

    try:
        runtime = _load_ragas_runtime()
    except ImportError:
        return RagasEvaluation(
            status="unavailable",
            summary="RAGAS is configured in the repo, but the package is not installed in this environment.",
            concerns=["Install the `ragas` dependency to enable automated RAG metric scoring."],
        )
    except Exception as exc:
        return RagasEvaluation(
            status="error",
            summary="RAGAS setup failed before metrics could run.",
            concerns=[str(exc)],
        )

    faithfulness_scorer = runtime["Faithfulness"](llm=runtime["llm"])
    answer_relevancy_scorer = runtime["AnswerRelevancy"](
        llm=runtime["llm"],
        embeddings=runtime["embeddings"],
    )
    context_utilization_scorer = runtime["ContextUtilization"](llm=runtime["llm"])
    metrics_run: list[str] = []
    concerns: list[str] = []

    has_contexts = bool(contexts)

    answer_relevancy, answer_relevancy_error = await _safe_metric(
        "Answer relevancy",
        answer_relevancy_scorer,
        user_input=result.query,
        response=answer_text,
    )
    if answer_relevancy is not None:
        metrics_run.append("answer_relevancy")
    elif answer_relevancy_error:
        concerns.append(answer_relevancy_error)

    faithfulness = None
    context_utilization = None
    if has_contexts:
        faithfulness, faithfulness_error = await _safe_metric(
            "Faithfulness",
            faithfulness_scorer,
            user_input=result.query,
            response=grounding_text,
            retrieved_contexts=contexts,
        )
        if faithfulness is not None:
            metrics_run.append("faithfulness")
        elif faithfulness_error:
            concerns.append(faithfulness_error)

        context_utilization, context_utilization_error = await _safe_metric(
            "Context utilization",
            context_utilization_scorer,
            user_input=result.query,
            response=grounding_text,
            retrieved_contexts=contexts,
        )
        if context_utilization is not None:
            metrics_run.append("context_utilization")
        elif context_utilization_error:
            concerns.append(context_utilization_error)

    scores = {
        "faithfulness": faithfulness,
        "answer_relevancy": answer_relevancy,
        "context_utilization": context_utilization,
    }
    summary, score_concerns = _build_summary(scores)
    concerns.extend(score_concerns)
    if not has_contexts:
        concerns.append("No cited retrieved contexts were available, so faithfulness and context utilization were skipped.")

    available = [score for score in scores.values() if score is not None]
    overall = round(sum(available) / len(available), 3) if available else None
    status = "available" if available else "error"
    if available and len(metrics_run) < 3:
        status = "partial"
        overall = None

    return RagasEvaluation(
        status=status,
        summary=summary,
        overall_score=overall,
        faithfulness=_rounded(faithfulness),
        answer_relevancy=_rounded(answer_relevancy),
        context_utilization=_rounded(context_utilization),
        concerns=concerns,
        metrics_run=metrics_run,
    )
