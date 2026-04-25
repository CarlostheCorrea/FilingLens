from fastapi import APIRouter, HTTPException, Query

from models import ChangeIntelligenceRequest, ChangeIntelligenceResponse
from services.change_intelligence_service import change_intelligence

router = APIRouter(prefix="/api", tags=["change-intelligence"])


@router.post("/change-intelligence", response_model=ChangeIntelligenceResponse)
async def run_change_intelligence(
    req: ChangeIntelligenceRequest,
    refresh: bool = Query(default=False, description="Force re-run even if cached"),
):
    try:
        result, _from_cache = await change_intelligence(req, force_refresh=refresh)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
