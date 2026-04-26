import json
import os
from datetime import datetime, timezone
from config import LOGS_DIR

os.makedirs(LOGS_DIR, exist_ok=True)


def _log(event_type: str, data: dict):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        **data,
    }
    log_path = os.path.join(LOGS_DIR, "events.jsonl")
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def log_scope_proposal(proposal_id: str, proposal: dict):
    _log("scope_proposal", {"proposal_id": proposal_id, "proposal": proposal})


def log_scope_approval(proposal_id: str, original: dict, approved: dict):
    _log("scope_approval", {
        "proposal_id": proposal_id,
        "original": original,
        "approved": approved,
    })


def log_ingestion(proposal_id: str, filings_fetched: list, chunks_created: int):
    _log("ingestion", {
        "proposal_id": proposal_id,
        "filings_fetched": filings_fetched,
        "chunks_created": chunks_created,
    })


def log_answer(proposal_id: str, query: str, claims: list, gaps: list, chunks_retrieved: list):
    _log("answer", {
        "proposal_id": proposal_id,
        "query": query,
        "claims_count": len(claims),
        "gaps_count": len(gaps),
        "chunks_retrieved_count": len(chunks_retrieved),
        "claims": claims,
        "gaps": gaps,
    })


def log_judge(proposal_id: str, query: str, judge: dict):
    _log("judge", {
        "proposal_id": proposal_id,
        "query": query,
        "judge": judge,
    })


def log_ragas(proposal_id: str, query: str, ragas: dict):
    _log("ragas", {
        "proposal_id": proposal_id,
        "query": query,
        "ragas": ragas,
    })


def log_verification(proposal_id: str, claim_id: str, verdict: str):
    _log("verification", {
        "proposal_id": proposal_id,
        "claim_id": claim_id,
        "verdict": verdict,
    })


def log_compare(compare_run_id: str, tickers: list[str], filing_events: list, company_count: int):
    _log("compare", {
        "compare_run_id": compare_run_id,
        "tickers": tickers,
        "company_count": company_count,
        "filing_events_count": len(filing_events),
    })


def log_change_intelligence(change_run_id: str, ticker: str, comparison_windows: int, change_cards: int):
    _log("change_intelligence", {
        "change_run_id": change_run_id,
        "ticker": ticker,
        "comparison_windows": comparison_windows,
        "change_cards": change_cards,
    })


def log_section_focus(
    accession_number: str,
    item_section: str,
    windows_kept: list[dict],
    top_scores: list[float],
):
    _log("section_focus", {
        "accession_number": accession_number,
        "item_section": item_section,
        "windows_kept": windows_kept,
        "top_scores": top_scores,
    })


def log_vector_refresh(
    proposal_id: str | None,
    accession_number: str,
    status: str,
    reason: str,
):
    _log("vector_refresh", {
        "proposal_id": proposal_id,
        "accession_number": accession_number,
        "status": status,
        "reason": reason,
    })
