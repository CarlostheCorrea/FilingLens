from fastapi import APIRouter, HTTPException
from models import VerifyRequest
from services.verification_service import verify_claim

router = APIRouter(prefix="/api", tags=["verify"])


@router.post("/verify")
async def verify(req: VerifyRequest):
    try:
        return verify_claim(req)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chunk/{chunk_id}")
async def get_chunk(chunk_id: str):
    import rag_pipeline
    chunk = rag_pipeline.get_chunk_by_id(chunk_id)
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return chunk
