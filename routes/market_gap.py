from fastapi import APIRouter, HTTPException
from models import MarketGapRequest, MarketGapResponse
from services.market_gap_service import analyze_market_gap

router = APIRouter()


@router.post("/api/market-gap", response_model=MarketGapResponse)
async def market_gap(req: MarketGapRequest, refresh: bool = False):
    try:
        result, _from_cache = await analyze_market_gap(req, force_refresh=refresh)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
