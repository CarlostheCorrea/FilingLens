from fastapi import APIRouter, HTTPException
from models import IngestRequest
from services.ingestion_service import ingest

router = APIRouter(prefix="/api", tags=["ingest"])


@router.post("/ingest")
async def ingest_filings(req: IngestRequest):
    try:
        return await ingest(req.proposal_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
