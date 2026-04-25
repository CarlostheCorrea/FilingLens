from fastapi import APIRouter, HTTPException
from models import ProposeRequest, ApprovedScope, ScopeProposal, ManualScopeRequest
from services.scope_service import create_proposal, approve_scope, create_manual_scope
from edgar_client import resolve_ticker_to_cik
from agent import propose_gap_scope

router = APIRouter(prefix="/api/scope", tags=["scope"])


@router.post("/propose", response_model=ScopeProposal)
async def propose(req: ProposeRequest):
    try:
        return await create_proposal(req.query)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/approve", response_model=ApprovedScope)
async def approve(approved: ApprovedScope):
    try:
        return approve_scope(approved)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/manual", response_model=ApprovedScope)
async def manual(req: ManualScopeRequest):
    """Create a scope directly from tickers without AI proposal."""
    try:
        return create_manual_scope(req)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/propose-gap", response_model=ScopeProposal)
async def propose_gap(req: ProposeRequest):
    """Scope proposal tuned for market gap discovery — broader coverage, annual filings."""
    try:
        proposal = await propose_gap_scope(req.query)
        import hitl, logging_utils
        hitl.save_proposal(proposal)
        logging_utils.log_scope_proposal(proposal.proposal_id, proposal.model_dump())
        return proposal
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/resolve/{ticker}")
async def resolve_ticker(ticker: str):
    """Resolve a ticker symbol to company name and CIK."""
    info = resolve_ticker_to_cik(ticker.strip().upper()) or {}
    return {
        "ticker": ticker.strip().upper(),
        "name": info.get("name", ticker.strip().upper()),
        "cik": info.get("cik", ""),
        "sic": info.get("sic", ""),
        "found": bool(info.get("cik", "")),
    }
