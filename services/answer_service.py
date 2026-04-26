import hashlib
import json
import os

from answer_workflow import run_answer_workflow
from models import CostSummary, WorkflowAnswerResponse, WorkflowStage
from pydantic import ValidationError
import cost_tracker
import hitl
from services.judge_service import judge_answer
from services.ragas_service import evaluate_answer_ragas
import logging_utils
import rag_pipeline
from config import DATA_DIR, VECTOR_SCHEMA_VERSION
from mcp_client import get_mcp_client

_STATE_DIR = os.path.join(DATA_DIR, "scope_state")


def _answer_key(query: str) -> str:
    return hashlib.md5(query.strip().lower().encode()).hexdigest()[:12]


async def _ensure_vectors_current(proposal_id: str, companies: list[dict]) -> list[str]:
    manifest = hitl.load_ingestion_manifest(proposal_id) or {}
    filings = manifest.get("filings", [])
    if not filings and companies:
        filings = rag_pipeline.list_indexed_filings([c["ticker"] for c in companies if c.get("ticker")])
    if not filings:
        return []

    mcp = get_mcp_client()
    refreshed: list[str] = []
    seen: set[tuple[str, str | None]] = set()
    for filing in filings:
        accession = filing.get("accession_number", "")
        cik = filing.get("cik")
        if not accession:
            continue
        key = (accession, cik)
        if key in seen:
            continue
        seen.add(key)
        filing_text = await mcp.fetch_filing(accession, cik=cik)
        if filing_text.get("error"):
            continue
        refresh = rag_pipeline.ensure_filing_embeddings_current(filing_text)
        if refresh.get("status") == "refreshed":
            refreshed.append(accession)
            logging_utils.log_vector_refresh(
                proposal_id,
                accession,
                refresh.get("status", ""),
                refresh.get("reason", ""),
            )
    return refreshed


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
    approved = hitl.load_approved_scope(proposal_id)
    companies = (
        [
            {"ticker": c.ticker, "name": c.name, "cik": c.cik}
            for c in approved.approved_companies
            if c.ticker
        ]
        if approved else []
    )

    # ── Cache hit ─────────────────────────────────────────────────────────────
    if not force_refresh and os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                data = json.load(f)
            if data.get("retrieval_version") != VECTOR_SCHEMA_VERSION:
                raise ValueError("stale retrieval version")
            data["from_cache"] = True
            cached = WorkflowAnswerResponse(**data)
            cache_updated = False
            if cached.answer.judge_evaluation is None:
                cached.answer.judge_evaluation = await judge_answer(cached)
                cache_updated = True
            if cached.answer.ragas_evaluation is None:
                cached.answer.ragas_evaluation = await evaluate_answer_ragas(cached)
                cache_updated = True
            if cache_updated:
                with open(cache_path, "w") as f:
                    json.dump({**cached.model_dump(), "from_cache": False}, f)
                hitl.save_answer(proposal_id, cached.model_dump(), answer_key="latest")
            cached.from_cache = True
            return cached, True
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
            pass

    cost_tracker.start_tracking()
    refreshed_accessions = await _ensure_vectors_current(proposal_id, companies)

    # ── Run supervisor workflow ────────────────────────────────────────────────
    result = await run_answer_workflow(proposal_id, query, companies)
    result.retrieval_version = VECTOR_SCHEMA_VERSION
    result.answer.judge_evaluation = await judge_answer(result)
    result.answer.ragas_evaluation = await evaluate_answer_ragas(result)
    result.answer.cost_summary = CostSummary(**cost_tracker.get_summary())
    if refreshed_accessions:
        result.workflow.stages.insert(0, WorkflowStage(
            name="refresh_index",
            status="completed",
            summary=f"Re-vectorized {len(refreshed_accessions)} filings due to retrieval version drift.",
        ))

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
    logging_utils.log_ragas(
        proposal_id,
        query,
        result.answer.ragas_evaluation.model_dump() if result.answer.ragas_evaluation else {},
    )

    return result, False
