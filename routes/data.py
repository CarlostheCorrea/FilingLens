import os
import shutil
import chromadb
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import CHROMA_DIR, FILINGS_CACHE_DIR, LOGS_DIR, DATA_DIR

router = APIRouter(prefix="/api/data", tags=["data"])

_STATE_DIR = os.path.join(DATA_DIR, "scope_state")


class ClearRequest(BaseModel):
    targets: list[str]  # any combo of: "vectors", "cache", "sessions", "logs"


def _dir_size_mb(path: str) -> float:
    total = 0
    if os.path.exists(path):
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
    return round(total / (1024 * 1024), 2)


def _file_count(path: str) -> int:
    if not os.path.exists(path):
        return 0
    return sum(len(files) for _, _, files in os.walk(path))


@router.get("/status")
async def data_status():
    """Return sizes and file counts for each data store."""
    return {
        "vectors": {
            "label": "ChromaDB vectors",
            "size_mb": _dir_size_mb(CHROMA_DIR),
            "files": _file_count(CHROMA_DIR),
        },
        "cache": {
            "label": "Filing text cache",
            "size_mb": _dir_size_mb(FILINGS_CACHE_DIR),
            "files": _file_count(FILINGS_CACHE_DIR),
        },
        "sessions": {
            "label": "Scope & answer sessions",
            "size_mb": _dir_size_mb(_STATE_DIR),
            "files": _file_count(_STATE_DIR),
        },
        "logs": {
            "label": "Event logs",
            "size_mb": _dir_size_mb(LOGS_DIR),
            "files": _file_count(LOGS_DIR),
        },
    }


@router.post("/clear")
async def clear_data(req: ClearRequest):
    cleared = []
    errors = []

    for target in req.targets:
        try:
            if target == "vectors":
                # Delete and recreate Chroma collection cleanly
                client = chromadb.PersistentClient(path=CHROMA_DIR)
                try:
                    client.delete_collection("sec_filings")
                except Exception:
                    pass
                # Also wipe the directory so the in-process client resets on next request
                shutil.rmtree(CHROMA_DIR, ignore_errors=True)
                os.makedirs(CHROMA_DIR, exist_ok=True)
                cleared.append("vectors")

            elif target == "cache":
                shutil.rmtree(FILINGS_CACHE_DIR, ignore_errors=True)
                os.makedirs(FILINGS_CACHE_DIR, exist_ok=True)
                cleared.append("cache")

            elif target == "sessions":
                shutil.rmtree(_STATE_DIR, ignore_errors=True)
                os.makedirs(_STATE_DIR, exist_ok=True)
                cleared.append("sessions")

            elif target == "logs":
                shutil.rmtree(LOGS_DIR, ignore_errors=True)
                os.makedirs(LOGS_DIR, exist_ok=True)
                cleared.append("logs")

        except Exception as e:
            errors.append(f"{target}: {e}")

    return {"cleared": cleared, "errors": errors}
