from fastapi import APIRouter, HTTPException, Query
from models import AnswerRequest, AnswerResponse
from services.answer_service import answer
import hitl

router = APIRouter(prefix="/api", tags=["answer"])


@router.post("/answer")
async def generate_answer(
    req: AnswerRequest,
    refresh: bool = Query(default=False, description="Force re-run even if cached"),
):
    try:
        result, from_cache = await answer(req.proposal_id, req.query, force_refresh=refresh)
        data = result.model_dump()
        data["from_cache"] = from_cache
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/{proposal_id}")
async def get_history(proposal_id: str):
    try:
        return hitl.load_history(proposal_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
