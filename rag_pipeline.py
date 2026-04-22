"""
Chunking, embedding, and retrieval pipeline using ChromaDB.
"""

import os
import uuid
import tiktoken
import chromadb
from openai import OpenAI
from models import Chunk, ChunkMetadata
from config import (
    OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL,
    CHUNK_SIZE_TOKENS, CHUNK_OVERLAP_TOKENS,
    RETRIEVAL_K, CHROMA_DIR,
)

os.makedirs(CHROMA_DIR, exist_ok=True)

_enc = tiktoken.get_encoding("cl100k_base")
_openai = OpenAI(api_key=OPENAI_API_KEY)
_chroma = chromadb.PersistentClient(path=CHROMA_DIR)


def _get_collection(collection_name: str = "sec_filings"):
    return _chroma.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )


def _tokenize(text: str) -> list[int]:
    return _enc.encode(text)


def _detokenize(tokens: list[int]) -> str:
    return _enc.decode(tokens)


def chunk_filing(filing_text_dict: dict) -> list[Chunk]:
    meta = filing_text_dict.get("metadata", {})
    sections = filing_text_dict.get("sections", {})

    chunks: list[Chunk] = []

    ticker = meta.get("ticker", "UNK")
    form_type = meta.get("form_type", "")
    filing_date = meta.get("filing_date", "")
    year = filing_date[:4] if filing_date else "0000"

    for section_name, text in sections.items():
        if not text or not text.strip():
            continue

        tokens = _tokenize(text)
        chunk_index = 0
        start = 0

        while start < len(tokens):
            end = min(start + CHUNK_SIZE_TOKENS, len(tokens))
            chunk_tokens = tokens[start:end]
            chunk_text = _detokenize(chunk_tokens).strip()

            if chunk_text:
                chunk_id = f"{ticker}_{form_type.replace('-','')}_{year}_{section_name}_{chunk_index:02d}"
                chunk = Chunk(
                    chunk_id=chunk_id,
                    text=chunk_text,
                    metadata=ChunkMetadata(
                        chunk_id=chunk_id,
                        company_ticker=ticker,
                        company_name=meta.get("company_name", ""),
                        cik=meta.get("cik", ""),
                        accession_number=meta.get("accession_number", ""),
                        form_type=form_type,
                        filing_date=filing_date,
                        item_section=section_name,
                        chunk_index=chunk_index,
                    ),
                )
                chunks.append(chunk)
                chunk_index += 1

            if end >= len(tokens):
                break
            start = end - CHUNK_OVERLAP_TOKENS

    return chunks


def embed_chunks(chunks: list[Chunk], collection_name: str = "sec_filings") -> None:
    if not chunks:
        return

    collection = _get_collection(collection_name)

    batch_size = 100
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        texts = [c.text for c in batch]

        response = _openai.embeddings.create(
            model=OPENAI_EMBEDDING_MODEL,
            input=texts,
        )
        vectors = [item.embedding for item in response.data]

        collection.upsert(
            ids=[c.chunk_id for c in batch],
            embeddings=vectors,
            documents=texts,
            metadatas=[c.metadata.model_dump() for c in batch],
        )


def _build_where(filters: dict | None, tickers: list[str] | None) -> dict | None:
    conditions = []

    if tickers:
        if len(tickers) == 1:
            conditions.append({"company_ticker": {"$eq": tickers[0]}})
        elif len(tickers) > 1:
            conditions.append({"company_ticker": {"$in": tickers}})

    if filters:
        if "form_type" in filters:
            conditions.append({"form_type": {"$eq": filters["form_type"]}})
        if "item_section" in filters:
            conditions.append({"item_section": {"$eq": filters["item_section"]}})

    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def _collection_count(collection_name: str = "sec_filings") -> int:
    try:
        return _get_collection(collection_name).count()
    except Exception:
        return 0


def retrieve(
    query: str,
    k: int = RETRIEVAL_K,
    filters: dict | None = None,
    tickers: list[str] | None = None,
    collection_name: str = "sec_filings",
) -> list[Chunk]:
    total = _collection_count(collection_name)
    if total == 0:
        return []

    collection = _get_collection(collection_name)

    response = _openai.embeddings.create(
        model=OPENAI_EMBEDDING_MODEL,
        input=[query],
    )
    query_vec = response.data[0].embedding

    where = _build_where(filters, tickers)
    safe_k = min(k, total)

    try:
        results = collection.query(
            query_embeddings=[query_vec],
            n_results=safe_k,
            where=where,
            include=["documents", "metadatas"],
        )
    except Exception:
        # Fallback: drop the filter and try unscoped
        results = collection.query(
            query_embeddings=[query_vec],
            n_results=safe_k,
            include=["documents", "metadatas"],
        )

    chunks = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    ids = results.get("ids", [[]])[0]

    for doc, meta, cid in zip(docs, metas, ids):
        chunk = Chunk(
            chunk_id=cid,
            text=doc,
            metadata=ChunkMetadata(**meta),
        )
        chunks.append(chunk)

    return chunks


def get_chunk_by_id(chunk_id: str, collection_name: str = "sec_filings") -> Chunk | None:
    try:
        result = _get_collection(collection_name).get(ids=[chunk_id], include=["documents", "metadatas"])
        docs = result.get("documents", [])
        metas = result.get("metadatas", [])
        if docs and metas:
            return Chunk(
                chunk_id=chunk_id,
                text=docs[0],
                metadata=ChunkMetadata(**metas[0]),
            )
    except Exception:
        pass
    return None
