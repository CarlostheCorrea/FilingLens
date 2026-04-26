from fastapi import APIRouter, HTTPException
from models import MarketGapRequest, MarketGapResponse, OpportunityMemoChatRequest, OpportunityMemoChatResponse
from services.market_gap_service import analyze_market_gap, answer_opportunity_memo_chat

router = APIRouter()


@router.post("/api/market-gap", response_model=MarketGapResponse)
async def market_gap(req: MarketGapRequest, refresh: bool = False):
    try:
        result, _from_cache = await analyze_market_gap(req, force_refresh=refresh)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/market-gap/chat", response_model=OpportunityMemoChatResponse)
async def market_gap_chat(req: OpportunityMemoChatRequest):
    try:
        return await answer_opportunity_memo_chat(req)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
