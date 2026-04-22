from fastapi import APIRouter, HTTPException, Query

from models import CompareRequest, CompareResponse
from services.compare_service import compare_companies

router = APIRouter(prefix="/api", tags=["compare"])


@router.post("/compare", response_model=CompareResponse)
async def compare(
    req: CompareRequest,
    refresh: bool = Query(default=False, description="Force compare refresh even if cached"),
):
    try:
        result, _from_cache = await compare_companies(req, force_refresh=refresh)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
