import hashlib
import json
import os

from agent import generate_answer
from models import AnswerResponse
import hitl
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
) -> tuple[AnswerResponse, bool]:
    """
    Returns (AnswerResponse, from_cache).
    Checks cache first; generates fresh answer on miss or force_refresh.
    Retrieval is scoped to the approved companies for this proposal.
    """
    key = _answer_key(query)
    cache_path = os.path.join(_STATE_DIR, f"{proposal_id}_answer_{key}.json")

    # Return cached answer if available and not forcing refresh
    if not force_refresh and os.path.exists(cache_path):
        with open(cache_path) as f:
            data = json.load(f)
        return AnswerResponse(**data), True

    # Scope retrieval to approved companies
    approved = hitl.load_approved_scope(proposal_id)
    tickers = (
        [c.ticker for c in approved.approved_companies if c.ticker]
        if approved else None
    )

    result = await generate_answer(proposal_id, query, tickers=tickers)

    # Persist to cache
    with open(cache_path, "w") as f:
        json.dump(result.model_dump(), f)

    # Also save as "latest" for verification lookup
    hitl.save_answer(proposal_id, result.model_dump(), answer_key="latest")

    # Persist to question history
    hitl.save_question(proposal_id, query, key)

    retrieved_chunks = rag_pipeline.retrieve(query, k=12, tickers=tickers)
    logging_utils.log_answer(
        proposal_id,
        query,
        [c.model_dump() for c in result.claims],
        result.gaps,
        [c.chunk_id for c in retrieved_chunks],
    )

    return result, False
