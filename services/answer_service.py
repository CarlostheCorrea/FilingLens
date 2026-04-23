import hashlib
import json
import os

from answer_workflow import run_answer_workflow
from models import WorkflowAnswerResponse
from pydantic import ValidationError
import hitl
from services.judge_service import judge_answer
import logging_utils
import rag_pipeline
from config import DATA_DIR

_STATE_DIR = os.path.join(DATA_DIR, "scope_state")


def _answer_key(query: str) -> str:
    return hashlib.md5(query.strip().lower().encode()).hexdigest()[:12]


async def answer(
    proposal_id: str,
    query: str,
    force_refresh: bool = False,
) -> tuple[WorkflowAnswerResponse, bool]:
    """
    Returns (WorkflowAnswerResponse, from_cache).
    Checks disk cache first; runs the LangGraph supervisor workflow on a miss.
    """
    key = _answer_key(query)
    cache_path = os.path.join(_STATE_DIR, f"{proposal_id}_answer_{key}.json")

    # ── Cache hit ─────────────────────────────────────────────────────────────
    if not force_refresh and os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                data = json.load(f)
            data["from_cache"] = True
            cached = WorkflowAnswerResponse(**data)
            if cached.answer.judge_evaluation is None:
                cached.answer.judge_evaluation = await judge_answer(cached)
                with open(cache_path, "w") as f:
                    json.dump({**cached.model_dump(), "from_cache": False}, f)
                hitl.save_answer(proposal_id, cached.model_dump(), answer_key="latest")
            cached.from_cache = True
            return cached, True
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
            pass

    # ── Resolve approved companies for this proposal ───────────────────────────
    approved = hitl.load_approved_scope(proposal_id)
    companies = (
        [
            {"ticker": c.ticker, "name": c.name, "cik": c.cik}
            for c in approved.approved_companies
            if c.ticker
        ]
        if approved else []
    )

    # ── Run supervisor workflow ────────────────────────────────────────────────
    result = await run_answer_workflow(proposal_id, query, companies)
    result.answer.judge_evaluation = await judge_answer(result)

    # ── Persist cache + history ───────────────────────────────────────────────
    with open(cache_path, "w") as f:
        json.dump(result.model_dump(), f)

    hitl.save_answer(proposal_id, result.model_dump(), answer_key="latest")
    hitl.save_question(proposal_id, query, key)

    # ── Analytics log ─────────────────────────────────────────────────────────
    tickers = [c["ticker"] for c in companies] or None
    chunks = rag_pipeline.retrieve(query, k=8, tickers=tickers)
    logging_utils.log_answer(
        proposal_id,
        query,
        [c.model_dump() for c in result.answer.claims_audit.claims],
        result.answer.coverage_notes,
        [c.chunk_id for c in chunks],
    )
    logging_utils.log_judge(
        proposal_id,
        query,
        result.answer.judge_evaluation.model_dump() if result.answer.judge_evaluation else {},
    )

    return result, False
