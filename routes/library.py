from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import LIBRARY_DIR
import hitl
import rag_pipeline

os.makedirs(LIBRARY_DIR, exist_ok=True)

router = APIRouter()


class SaveLibraryRequest(BaseModel):
    proposal_id: str
    name: str


def _all_entries() -> list[dict]:
    entries = []
    for fname in os.listdir(LIBRARY_DIR):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(LIBRARY_DIR, fname)) as f:
                entries.append(json.load(f))
        except Exception:
            pass
    entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    return entries


def _entry_path(analyst_id: str) -> str:
    return os.path.join(LIBRARY_DIR, f"{analyst_id}.json")


def _vectors_present(filings: list[dict]) -> bool:
    for filing in filings:
        accession = filing.get("accession_number", "")
        if not accession:
            continue
        state = rag_pipeline.get_filing_chunk_state(accession)
        if state.get("count", 0) > 0:
            return True
    return False


@router.post("/api/library/save")
async def save_analyst(req: SaveLibraryRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Analyst name cannot be empty.")

    approved = hitl.load_approved_scope(req.proposal_id)
    if not approved:
        raise HTTPException(status_code=404, detail="Approved scope not found for this session.")

    manifest = hitl.load_ingestion_manifest(req.proposal_id) or {}
    filings = manifest.get("filings", [])

    analyst_id = f"lib_{uuid.uuid4().hex[:10]}"
    entry = {
        "id": analyst_id,
        "name": name,
        "proposal_id": req.proposal_id,
        "companies": [c.model_dump() for c in approved.approved_companies],
        "form_types": approved.form_types,
        "date_range": approved.date_range,
        "filings": filings,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    with open(_entry_path(analyst_id), "w") as f:
        json.dump(entry, f)

    return entry


@router.get("/api/library")
async def list_analysts():
    return _all_entries()


@router.post("/api/library/load/{analyst_id}")
async def load_analyst(analyst_id: str):
    path = _entry_path(analyst_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Analyst not found.")
    with open(path) as f:
        entry = json.load(f)
    entry["vectors_present"] = _vectors_present(entry.get("filings", []))
    return entry


@router.delete("/api/library/{analyst_id}")
async def delete_analyst(analyst_id: str):
    path = _entry_path(analyst_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Analyst not found.")
    os.remove(path)
    return {"deleted": analyst_id}
