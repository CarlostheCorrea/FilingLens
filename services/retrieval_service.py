import rag_pipeline
from models import Chunk


def retrieve_chunks(query: str, k: int = 8, filters: dict | None = None) -> list[Chunk]:
    return rag_pipeline.retrieve(query, k=k, filters=filters)


def get_chunk(chunk_id: str) -> Chunk | None:
    return rag_pipeline.get_chunk_by_id(chunk_id)
