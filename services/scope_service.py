import uuid
from models import ScopeProposal, ApprovedScope, Company, ManualScopeRequest
from agent import propose_scope
from edgar_client import resolve_ticker_to_cik
import hitl
import logging_utils


async def create_proposal(query: str) -> ScopeProposal:
    proposal = await propose_scope(query)
    hitl.save_proposal(proposal)
    logging_utils.log_scope_proposal(proposal.proposal_id, proposal.model_dump())
    return proposal


def approve_scope(approved: ApprovedScope) -> ApprovedScope:
    original = hitl.load_proposal(approved.proposal_id)
    original_dict = original.model_dump() if original else {}
    hitl.save_approved_scope(approved)
    logging_utils.log_scope_approval(
        approved.proposal_id,
        original_dict,
        approved.model_dump(),
    )
    return approved


def create_manual_scope(req: ManualScopeRequest) -> ApprovedScope:
    proposal_id = f"manual_{uuid.uuid4().hex[:8]}"
    companies = []

    for ticker in req.tickers:
        ticker = ticker.strip().upper()
        if not ticker:
            continue
        try:
            info = resolve_ticker_to_cik(ticker)
        except Exception:
            info = {}

        companies.append(Company(
            ticker=ticker,
            name=info.get("name", ticker) if info else ticker,
            cik=info.get("cik", "") if info else "",
            rationale="Manually selected",
        ))

    approved = ApprovedScope(
        proposal_id=proposal_id,
        approved_companies=companies,
        form_types=req.form_types,
        date_range=req.date_range,
    )
    hitl.save_approved_scope(approved)
    logging_utils.log_scope_approval(proposal_id, {}, approved.model_dump())
    return approved
