"""
LangGraph Supervisor workflow for multi-company answer generation.

Graph topology:
  START
    └─ load_context
         └─ supervisor ──── fan-out (Send × N) ──► company_worker[0..N]
              ↑                                            │ (fan-in)
              │                                           ▼
              │                                    merge_answers
              │                                           │
              │                                    review_answer
              │                                           │
              └──────────── (if needs_revision) ──────────┘
                            (if approved) ──► finalize ──► END

The supervisor node is called twice:
  1. After load_context  → fans out to parallel company workers
  2. After review_answer → routes to finalize OR back to merge for one revision
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Annotated, TypedDict
import operator

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from openai import AsyncOpenAI, RateLimitError

import cost_tracker
import rag_pipeline
from services.xbrl_context_service import build_xbrl_context, is_quantitative
from config import (
    OPENAI_API_KEY,
    OPENAI_MODEL,
    OPENAI_WORKER_MODEL,
    COMPANY_WORKER_SYSTEM_PROMPT,
    MERGE_SYSTEM_PROMPT,
    REVIEW_SYSTEM_PROMPT,
    FINAL_SYNTHESIS_SYSTEM_PROMPT,
)
from models import (
    WorkflowAnswerResponse,
    WorkflowStage,
    WorkflowMetadata,
    StructuredAnswerPayload,
    OverallAnswer,
    OverallKeyPoint,
    CompanyDeepDive,
    CompanyEvidenceItem,
    ClaimsAuditPayload,
    Claim,
)

_openai = AsyncOpenAI(api_key=OPENAI_API_KEY)
_RATE_LIMIT_RETRY_MESSAGE = re.compile(r"try again in\s+([0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE)


def _retry_delay_from_error(error: RateLimitError, attempt: int) -> float:
    response = getattr(error, "response", None)
    if response is not None:
        retry_after_ms = response.headers.get("retry-after-ms")
        if retry_after_ms:
            try:
                return max(float(retry_after_ms) / 1000.0, 0.5)
            except ValueError:
                pass
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return max(float(retry_after), 0.5)
            except ValueError:
                pass

    match = _RATE_LIMIT_RETRY_MESSAGE.search(str(error))
    if match:
        return max(float(match.group(1)), 0.5)

    return min(1.5 * (2 ** attempt), 8.0)


async def _create_json_completion(*, model: str, messages: list[dict], max_retries: int = 3) -> dict:
    last_error: RateLimitError | None = None

    for attempt in range(max_retries + 1):
        try:
            response = await _openai.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
            )
            if response.usage:
                cost_tracker.record_llm(
                    model,
                    response.usage.prompt_tokens,
                    response.usage.completion_tokens,
                )
            return json.loads(response.choices[0].message.content or "{}")
        except RateLimitError as error:
            last_error = error
            if attempt >= max_retries:
                raise
            await asyncio.sleep(_retry_delay_from_error(error, attempt))

    if last_error is not None:
        raise last_error
    return {}


# ─────────────────────────── Shared State ────────────────────────────────────

class WorkflowState(TypedDict):
    # Inputs (set once at graph entry)
    proposal_id: str
    query: str
    run_id: str
    companies: list[dict]          # [{ticker, name, cik}]

    # Worker results — accumulated in parallel via operator.add reducer
    worker_results: Annotated[list[dict], operator.add]

    # Post-merge state (plain fields, last-write wins)
    merged_claims: list[dict]
    merged_gaps: list[str]
    review_verdict: str            # "approved" | "needs_revision"
    review_feedback: str
    revision_count: int

    # Final answer fields set by finalize node
    final_claims: list[dict]
    final_gaps: list[str]
    final_overall_answer: dict
    final_company_deep_dives: list[dict]
    final_coverage_notes: list[str]

    # Workflow activity trace — accumulated across all nodes
    stages: Annotated[list[dict], operator.add]


# ─────────────────────────── Nodes ───────────────────────────────────────────

async def load_context(state: WorkflowState) -> dict:
    companies = state.get("companies", [])
    tickered = [c for c in companies if c.get("ticker")]
    return {
        "revision_count": 0,
        "merged_claims": [],
        "merged_gaps": [],
        "review_verdict": "",
        "review_feedback": "",
        "final_claims": [],
        "final_gaps": [],
        "final_overall_answer": {"summary": "", "key_points": []},
        "final_company_deep_dives": [],
        "final_coverage_notes": [],
        "stages": [{
            "name": "load_context",
            "status": "completed",
            "summary": (
                f"Loaded {len(companies)} approved companies "
                f"({len(tickered)} with tickers)"
            ),
        }],
    }


async def supervisor(state: WorkflowState) -> dict:
    """
    Lightweight routing node — logs the current decision.
    Actual routing is driven by route_supervisor() conditional edge.
    """
    worker_results = state.get("worker_results", [])

    if not worker_results:
        # First call: about to fan out
        tickered = [c for c in state.get("companies", []) if c.get("ticker")]
        summary = f"Dispatching {len(tickered)} parallel company workers"
    else:
        # Second call: post-review routing decision
        verdict = state.get("review_verdict", "approved")
        revision_count = state.get("revision_count", 0)
        if verdict == "needs_revision" and revision_count < 1:
            summary = f"Reviewer requested revision — routing to merge (pass {revision_count + 1})"
        else:
            summary = "Answer approved — routing to finalize"

    return {
        "stages": [{
            "name": "supervisor",
            "status": "routing",
            "summary": summary,
        }]
    }


async def company_worker(state: dict) -> dict:
    """
    Per-company worker dispatched via Send.
    Receives sub-state: {query, ticker, company_name, proposal_id}
    Returns {worker_results: [result]} — merged into main state via operator.add.
    """
    ticker = state["ticker"]
    query = state["query"]
    company_name = state.get("company_name", ticker)

    chunks = rag_pipeline.retrieve(query, k=8, tickers=[ticker])

    if not chunks:
        return {
            "worker_results": [{
                "ticker": ticker,
                "company_name": company_name,
                "summary": f"No relevant indexed filing evidence was found for {company_name}.",
                "claims": [],
                "gaps": [f"No indexed filing data found for {ticker}."],
                "evidence_chunk_ids": [],
                "retrieved_chunk_ids": [],
                "status": "empty",
            }]
        }

    context = "\n---\n".join(
        f"[chunk_id: {c.chunk_id}]\n"
        f"Filing: {c.metadata.form_type} {c.metadata.filing_date}\n"
        f"Section: {c.metadata.item_section}\n"
        f"Text:\n{c.text}"
        for c in chunks
    )

    raw = await _create_json_completion(
        model=OPENAI_WORKER_MODEL,
        messages=[
            {"role": "system", "content": COMPANY_WORKER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Company: {company_name} ({ticker})\n"
                    f"Research question: {query}\n\n"
                    f"Filing excerpts:\n{context}"
                ),
            },
        ],
    )
    evidence_chunk_ids = [
        cid for cid in raw.get("evidence_chunk_ids", [])
        if cid in {c.chunk_id for c in chunks}
    ]
    if not evidence_chunk_ids:
        evidence_chunk_ids = [c.chunk_id for c in chunks[:3]]

    return {
        "worker_results": [{
            "ticker": ticker,
            "company_name": company_name,
            "summary": raw.get("summary", ""),
            "claims": raw.get("claims", []),
            "gaps": raw.get("gaps", []),
            "evidence_chunk_ids": evidence_chunk_ids,
            "retrieved_chunk_ids": [c.chunk_id for c in chunks],
            "status": "ok",
        }]
    }


async def merge_answers(state: WorkflowState) -> dict:
    """
    Fan-in point: merges all worker claims into one cross-company answer.
    On revision pass, incorporates reviewer feedback.
    """
    worker_results = state.get("worker_results", [])
    feedback = state.get("review_feedback", "")
    revision_count = state.get("revision_count", 0)

    all_claims: list[dict] = []
    all_gaps: list[str] = []
    for r in worker_results:
        all_claims.extend(r.get("claims", []))
        all_gaps.extend(r.get("gaps", []))

    draft_count = len(all_claims)
    stages_to_add: list[dict] = []

    # First merge pass — add the company_workers stage summary here (post fan-in)
    if not feedback:
        ok_count = sum(1 for r in worker_results if r.get("status") == "ok")
        stages_to_add.append({
            "name": "company_workers",
            "status": "completed",
            "summary": (
                f"{len(worker_results)} workers ran in parallel "
                f"({ok_count} with data)"
            ),
        })

    user_content = (
        f"Research question: {state['query']}\n\n"
        f"Company-level claims:\n{json.dumps(all_claims, indent=2)}\n\n"
        f"Gaps reported by workers:\n{json.dumps(all_gaps, indent=2)}"
    )
    if feedback:
        user_content += f"\n\nReviewer feedback to address:\n{feedback}"

    raw = await _create_json_completion(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": MERGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )
    merged_claims = raw.get("claims", [])
    revision_label = f" (revision {revision_count + 1})" if feedback else ""

    stages_to_add.append({
        "name": "merge_answers",
        "status": "completed",
        "summary": (
            f"Merged {draft_count} draft claims into "
            f"{len(merged_claims)} claims{revision_label}"
        ),
    })

    return {
        "merged_claims": merged_claims,
        "merged_gaps": raw.get("gaps", []),
        # Increment revision_count only when processing reviewer feedback
        "revision_count": revision_count + (1 if feedback else 0),
        "review_feedback": "",   # clear after consuming
        "stages": stages_to_add,
    }


async def review_answer(state: WorkflowState) -> dict:
    """
    Checks merged claims for citation accuracy.
    Skips re-review after one revision to prevent infinite loops.
    """
    revision_count = state.get("revision_count", 0)

    # After one revision pass, finalize without further review
    if revision_count >= 1:
        return {
            "review_verdict": "approved",
            "review_feedback": "",
            "stages": [{
                "name": "review_answer",
                "status": "completed",
                "summary": "Skipped re-review after revision pass — finalizing",
            }],
        }

    merged_claims = state.get("merged_claims", [])
    worker_results = state.get("worker_results", [])

    all_chunk_ids: list[str] = []
    for r in worker_results:
        all_chunk_ids.extend(r.get("retrieved_chunk_ids", []))

    raw = await _create_json_completion(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Research question: {state['query']}\n\n"
                    f"Merged claims:\n{json.dumps(merged_claims, indent=2)}\n\n"
                    f"All retrieved chunk IDs (valid citations):\n"
                    f"{json.dumps(all_chunk_ids)}"
                ),
            },
        ],
    )
    verdict = raw.get("verdict", "approved")
    feedback = raw.get("feedback", "")

    summary = (
        "Reviewer approved — claims are well-supported"
        if verdict == "approved"
        else f"Reviewer requested revision: {feedback[:120]}"
    )

    return {
        "review_verdict": verdict,
        "review_feedback": feedback,
        "stages": [{
            "name": "review_answer",
            "status": "completed",
            "summary": summary,
        }],
    }


async def finalize(state: WorkflowState) -> dict:
    merged_claims = state.get("merged_claims", [])
    merged_gaps = state.get("merged_gaps", [])
    worker_results = state.get("worker_results", [])
    ok_count = sum(1 for r in worker_results if r.get("status") == "ok")
    final_claims = state.get("final_claims", [])
    if not final_claims:
        final_claims = merged_claims

    # Inject XBRL key metrics for quantitative questions so the synthesis
    # LLM can anchor claims to real filed numbers rather than prose alone.
    query = state["query"]
    xbrl_block = ""
    if is_quantitative(query):
        companies = state.get("companies", [])
        xbrl_block = await build_xbrl_context(companies)

    xbrl_section = f"\n\n{xbrl_block}" if xbrl_block else ""

    synthesis_raw = await _create_json_completion(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": FINAL_SYNTHESIS_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Research question: {query}\n\n"
                    f"Merged claims:\n{json.dumps(final_claims, indent=2)}\n\n"
                    f"Company worker results:\n{json.dumps(worker_results, indent=2)}\n\n"
                    f"Merged gaps:\n{json.dumps(merged_gaps, indent=2)}"
                    f"{xbrl_section}"
                ),
            },
        ],
    )

    company_deep_dives = []
    for result in worker_results:
        evidence_items = []
        for chunk_id in result.get("evidence_chunk_ids", [])[:3]:
            chunk = rag_pipeline.get_chunk_by_id(chunk_id)
            if not chunk:
                continue
            evidence_items.append({
                "chunk_id": chunk_id,
                "excerpt": chunk.text[:350],
                "company_ticker": chunk.metadata.company_ticker,
                "company_name": chunk.metadata.company_name,
                "cik": chunk.metadata.cik,
                "accession_number": chunk.metadata.accession_number,
                "form_type": chunk.metadata.form_type,
                "filing_date": chunk.metadata.filing_date,
                "item_section": chunk.metadata.item_section,
            })

        summary = (result.get("summary") or "").strip()
        if not summary:
            if result.get("status") == "ok":
                summary = f"{result.get('company_name', result.get('ticker', 'This company'))} discusses the question topic in its filings, but the retrieved evidence is summarized only in the audit claims below."
            else:
                summary = f"No relevant indexed filing evidence was found for {result.get('company_name', result.get('ticker', 'this company'))}."

        company_deep_dives.append({
            "ticker": result.get("ticker", ""),
            "company_name": result.get("company_name", result.get("ticker", "")),
            "status": "supported" if result.get("status") == "ok" else "insufficient_evidence",
            "summary": summary,
            "evidence": evidence_items,
            "gaps": result.get("gaps", []),
        })

    overall_answer = synthesis_raw.get("overall_answer", {})
    if not overall_answer.get("summary"):
        overall_answer = {
            "summary": "The available filings provide a mixed level of support across the selected companies. Review the company deep dives and audit claims for the strongest evidence.",
            "key_points": [],
        }

    return {
        "final_claims": final_claims,
        "final_gaps": merged_gaps,
        "final_overall_answer": overall_answer,
        "final_company_deep_dives": company_deep_dives,
        "final_coverage_notes": synthesis_raw.get("coverage_notes", []),
        "stages": [{
            "name": "finalize",
            "status": "completed",
            "summary": (
                f"Workflow complete: {len(final_claims)} audit claims, "
                f"{len(company_deep_dives)} company deep dives "
                f"({ok_count}/{len(worker_results)} companies contributed data)"
            ),
        }],
    }


# ─────────────────────────── Routing ─────────────────────────────────────────

def route_supervisor(state: WorkflowState) -> list[Send] | str:
    """
    Conditional edge from the supervisor node.

    Phase 1 — no workers have run yet (worker_results is empty):
      Fan out a Send per ticker so all company_workers run in parallel.

    Phase 2 — workers are done and review has run:
      Route to merge_answers for one revision OR to finalize.
    """
    worker_results = state.get("worker_results", [])

    # ── Phase 1: fan out ──────────────────────────────────────────────────────
    if not worker_results:
        companies = [c for c in state.get("companies", []) if c.get("ticker")]
        if not companies:
            # No tickers → skip straight to finalize with an explanatory gap
            return "finalize"
        return [
            Send("company_worker", {
                "query": state["query"],
                "ticker": c["ticker"],
                "company_name": c.get("name", c["ticker"]),
                "proposal_id": state["proposal_id"],
            })
            for c in companies
        ]

    # ── Phase 2: post-review routing ──────────────────────────────────────────
    verdict = state.get("review_verdict", "approved")
    revision_count = state.get("revision_count", 0)
    if verdict == "needs_revision" and revision_count < 1:
        return "merge_answers"
    return "finalize"


# ─────────────────────────── Graph construction ───────────────────────────────

_GRAPH = None


def _build_graph():
    builder = StateGraph(WorkflowState)

    builder.add_node("load_context", load_context)
    builder.add_node("supervisor", supervisor)
    builder.add_node("company_worker", company_worker)
    builder.add_node("merge_answers", merge_answers)
    builder.add_node("review_answer", review_answer)
    builder.add_node("finalize", finalize)

    # Entry
    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "supervisor")

    # Supervisor routes: fan-out via Send  OR  merge (revision)  OR  finalize
    builder.add_conditional_edges(
        "supervisor",
        route_supervisor,
        ["company_worker", "merge_answers", "finalize"],
    )

    # Fan-in: after ALL parallel company_workers complete → merge
    builder.add_edge("company_worker", "merge_answers")

    # Merge → Review
    builder.add_edge("merge_answers", "review_answer")

    # Review → Supervisor for post-review routing (finalize or one revision)
    builder.add_edge("review_answer", "supervisor")

    # Terminal
    builder.add_edge("finalize", END)

    return builder.compile()


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = _build_graph()
    return _GRAPH


# ─────────────────────────── Public API ──────────────────────────────────────

async def run_answer_workflow(
    proposal_id: str,
    query: str,
    companies: list[dict],
) -> WorkflowAnswerResponse:
    """
    Execute the full supervisor workflow.

    Args:
        proposal_id: the approved scope ID
        query: the user's research question
        companies: list of {ticker, name, cik} dicts from the approved scope

    Returns:
        WorkflowAnswerResponse with nested answer payload and workflow trace
    """
    run_id = f"ans_{uuid.uuid4().hex[:10]}"

    initial_state: WorkflowState = {
        "proposal_id": proposal_id,
        "query": query,
        "run_id": run_id,
        "companies": companies,
        "worker_results": [],
        "merged_claims": [],
        "merged_gaps": [],
        "review_verdict": "",
        "review_feedback": "",
        "revision_count": 0,
        "final_claims": [],
        "final_gaps": [],
        "final_overall_answer": {"summary": "", "key_points": []},
        "final_company_deep_dives": [],
        "final_coverage_notes": [],
        "stages": [],
    }

    graph = get_graph()
    final_state: WorkflowState = await graph.ainvoke(initial_state)

    # Build typed Claim objects from the finalized state
    claims = [
        Claim(
            claim_id=c.get("claim_id", f"claim_{i:03d}"),
            text=c.get("text", ""),
            supporting_chunk_ids=c.get("supporting_chunk_ids", []),
            confidence=c.get("confidence", "medium"),
        )
        for i, c in enumerate(final_state.get("final_claims", []))
    ]

    company_deep_dives = [
        CompanyDeepDive(
            ticker=d.get("ticker", ""),
            company_name=d.get("company_name", d.get("ticker", "")),
            status=d.get("status", "supported"),
            summary=d.get("summary", ""),
            evidence=[
                CompanyEvidenceItem(
                    chunk_id=e.get("chunk_id", ""),
                    excerpt=e.get("excerpt", ""),
                    company_ticker=e.get("company_ticker", d.get("ticker", "")),
                    company_name=e.get("company_name", d.get("company_name", d.get("ticker", ""))),
                    cik=e.get("cik", ""),
                    accession_number=e.get("accession_number", ""),
                    form_type=e.get("form_type", ""),
                    filing_date=e.get("filing_date", ""),
                    item_section=e.get("item_section", ""),
                )
                for e in d.get("evidence", [])
            ],
            gaps=d.get("gaps", []),
        )
        for d in final_state.get("final_company_deep_dives", [])
    ]

    overall_answer_raw = final_state.get("final_overall_answer", {})
    overall_answer = OverallAnswer(
        summary=overall_answer_raw.get("summary", "No overall summary was generated."),
        key_points=[
            OverallKeyPoint(
                text=kp.get("text", ""),
                supporting_tickers=kp.get("supporting_tickers", []),
            )
            for kp in overall_answer_raw.get("key_points", [])
            if kp.get("text")
        ],
    )

    coverage_notes = final_state.get("final_coverage_notes", [])
    if not claims and not company_deep_dives and not coverage_notes:
        coverage_notes = ["No relevant filing data found for the approved companies."]

    stages = [
        WorkflowStage(
            name=s["name"],
            status=s["status"],
            summary=s["summary"],
        )
        for s in final_state.get("stages", [])
    ]

    return WorkflowAnswerResponse(
        proposal_id=proposal_id,
        query=query,
        from_cache=False,
        workflow=WorkflowMetadata(
            pattern="supervisor",
            run_id=run_id,
            status="completed",
            stages=stages,
        ),
        answer=StructuredAnswerPayload(
            overall_answer=overall_answer,
            company_deep_dives=company_deep_dives,
            claims_audit=ClaimsAuditPayload(claims=claims),
            coverage_notes=coverage_notes,
        ),
    )
