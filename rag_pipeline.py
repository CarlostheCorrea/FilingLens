"""
Chunking, embedding, and retrieval pipeline using ChromaDB.
"""

import os
import logging
import warnings
import uuid
import hashlib

import tiktoken
import chromadb
from chromadb.config import Settings as ChromaSettings

# ChromaDB's posthog telemetry client has a version mismatch with the
# installed posthog library — capture() signature changed. Silence the
# resulting ERROR logs; they're cosmetic and don't affect functionality.
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)

# edgartools ships a bundled company_tickers.parquet built against an older
# pyarrow. The load fails but edgartools falls back gracefully. Suppress it.
warnings.filterwarnings("ignore", message=".*company_tickers.parquet.*")
from openai import OpenAI
from models import Chunk, ChunkMetadata
from config import (
    OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL,
    CHUNK_SIZE_TOKENS, CHUNK_OVERLAP_TOKENS,
    RETRIEVAL_K, CHROMA_DIR,
    SECTION_SCOUT_WINDOW_CHARS,
    SECTION_SCOUT_OVERLAP_CHARS,
    SECTION_SCOUT_TOP_WINDOWS,
    SECTION_SCOUT_SCORE_THRESHOLD,
    VECTOR_SCHEMA_VERSION,
)
import logging_utils

import cost_tracker

os.makedirs(CHROMA_DIR, exist_ok=True)

_enc = tiktoken.get_encoding("cl100k_base")
_openai = OpenAI(api_key=OPENAI_API_KEY)


def _embed(inputs: list[str]) -> list[list[float]]:
    """Embed a list of strings, record token usage, return vectors."""
    response = _openai.embeddings.create(model=OPENAI_EMBEDDING_MODEL, input=inputs)
    usage = getattr(response, "usage", None)
    if usage:
        cost_tracker.record_embedding(OPENAI_EMBEDDING_MODEL, usage.total_tokens)
    return [item.embedding for item in response.data]
_chroma = chromadb.PersistentClient(
    path=CHROMA_DIR,
    settings=ChromaSettings(anonymized_telemetry=False),
)


def _get_collection(collection_name: str = "sec_filings"):
    return _chroma.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )


def _tokenize(text: str) -> list[int]:
    return _enc.encode(text)


def _detokenize(tokens: list[int]) -> str:
    return _enc.decode(tokens)


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _section_digest(filing_text_dict: dict) -> str:
    meta = filing_text_dict.get("metadata", {})
    sections = filing_text_dict.get("sections", {})
    payload = {
        "accession_number": meta.get("accession_number", ""),
        "sections": {k: sections[k] for k in sorted(sections)},
    }
    return hashlib.md5(str(payload).encode()).hexdigest()


def _section_windows(text: str) -> list[tuple[int, int]]:
    if len(text) <= SECTION_SCOUT_WINDOW_CHARS:
        return [(0, len(text))]

    step = max(SECTION_SCOUT_WINDOW_CHARS - SECTION_SCOUT_OVERLAP_CHARS, 1)
    spans: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        end = min(start + SECTION_SCOUT_WINDOW_CHARS, len(text))
        spans.append((start, end))
        if end >= len(text):
            break
        start += step
    return spans


def _expand_span(start: int, end: int, text_len: int) -> tuple[int, int]:
    pad = max(SECTION_SCOUT_OVERLAP_CHARS, 250)
    return max(0, start - pad), min(text_len, end + pad)


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not spans:
        return []
    merged = [spans[0]]
    for start, end in sorted(spans):
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _focus_hint(text: str, start: int, end: int) -> str | None:
    window = text[max(0, start - 300):min(len(text), end + 300)]
    for line in window.splitlines():
        candidate = " ".join(line.split()).strip(" .:-")
        if len(candidate) < 6 or len(candidate) > 120:
            continue
        if any(ch.isdigit() for ch in candidate[:5]) or candidate.isupper():
            return candidate
    return None


def _paragraph_chunks(text: str) -> list[str]:
    """
    Split text into chunks that respect paragraph boundaries.

    Paragraphs (double-newline separated) are grouped greedily up to
    CHUNK_SIZE_TOKENS. A paragraph that exceeds the limit on its own is
    split by tokens as a fallback. Overlap is carried at the token level
    from the tail of each outgoing chunk into the next.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        # No paragraph structure — fall back to single-newline split
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    if not paragraphs:
        return []

    result: list[str] = []
    buf: list[int] = []          # token buffer for the current chunk
    sep_tokens = _tokenize("\n\n")

    def _flush(buf: list[int]) -> list[int]:
        if buf:
            result.append(_detokenize(buf).strip())
        return buf[-CHUNK_OVERLAP_TOKENS:] if CHUNK_OVERLAP_TOKENS else []

    for para in paragraphs:
        ptoks = _tokenize(para)

        if len(ptoks) > CHUNK_SIZE_TOKENS:
            # Oversized paragraph: flush buffer first, then split by tokens
            buf = _flush(buf)
            start = 0
            while start < len(ptoks):
                end = min(start + CHUNK_SIZE_TOKENS, len(ptoks))
                result.append(_detokenize(ptoks[start:end]).strip())
                if end >= len(ptoks):
                    break
                start = end - CHUNK_OVERLAP_TOKENS
            buf = ptoks[-CHUNK_OVERLAP_TOKENS:] if CHUNK_OVERLAP_TOKENS else []
            continue

        needed = len(sep_tokens) + len(ptoks) if buf else len(ptoks)
        if buf and len(buf) + needed > CHUNK_SIZE_TOKENS:
            buf = _flush(buf)

        buf = buf + sep_tokens + ptoks if buf else list(ptoks)

    _flush(buf)
    return [c for c in result if c]


def chunk_filing(filing_text_dict: dict) -> list[Chunk]:
    meta = filing_text_dict.get("metadata", {})
    sections = filing_text_dict.get("sections", {})
    section_focuses = filing_text_dict.get("section_focuses") or []

    chunks: list[Chunk] = []
    ticker = meta.get("ticker", "UNK")
    form_type = meta.get("form_type", "")
    filing_date = meta.get("filing_date", "")
    year = filing_date[:4] if filing_date else "0000"
    section_text_digest = _section_digest(filing_text_dict)

    items: list[tuple[str, str, dict]] = []
    if section_focuses:
        for focus in section_focuses:
            items.append((focus["section_name"], focus.get("text", ""), focus))
    else:
        for section_name, text in sections.items():
            items.append((section_name, text, {}))

    for section_name, text, focus in items:
        if not text or not text.strip():
            continue

        window_index = focus.get("section_window_index")
        chunk_prefix = f"{section_name}_w{window_index:02d}" if window_index is not None else section_name

        for chunk_index, chunk_text in enumerate(_paragraph_chunks(text)):
            chunk_id = f"{ticker}_{form_type.replace('-','')}_{year}_{chunk_prefix}_{chunk_index:02d}"
            chunks.append(Chunk(
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
                    section_window_index=window_index,
                    section_focus_hint=focus.get("section_focus_hint"),
                    source_char_start=focus.get("source_char_start"),
                    source_char_end=focus.get("source_char_end"),
                    section_text_digest=section_text_digest,
                    vector_schema_version=VECTOR_SCHEMA_VERSION,
                ),
            ))

    return chunks


def delete_filing_chunks(accession_number: str, collection_name: str = "sec_filings") -> None:
    """Remove all chunks for a given accession number so stale data doesn't linger."""
    try:
        collection = _get_collection(collection_name)
        results = collection.get(
            where={"accession_number": {"$eq": accession_number}},
            include=[],
        )
        ids = results.get("ids", [])
        if ids:
            collection.delete(ids=ids)
    except Exception:
        pass


def get_filing_chunk_state(accession_number: str, collection_name: str = "sec_filings") -> dict:
    try:
        collection = _get_collection(collection_name)
        results = collection.get(
            where={"accession_number": {"$eq": accession_number}},
            include=["metadatas"],
        )
        metadatas = results.get("metadatas", [])
        if not metadatas:
            return {"count": 0, "vector_schema_version": None, "section_text_digest": None}
        first = metadatas[0] or {}
        return {
            "count": len(metadatas),
            "vector_schema_version": first.get("vector_schema_version"),
            "section_text_digest": first.get("section_text_digest"),
        }
    except Exception:
        return {"count": 0, "vector_schema_version": None, "section_text_digest": None}


def list_indexed_filings(tickers: list[str] | None = None, collection_name: str = "sec_filings") -> list[dict]:
    try:
        collection = _get_collection(collection_name)
        where = _build_where(None, tickers)
        if where:
            results = collection.get(where=where, include=["metadatas"])
        else:
            results = collection.get(include=["metadatas"])
    except Exception:
        return []

    seen: set[tuple[str, str]] = set()
    filings: list[dict] = []
    for meta in results.get("metadatas", []):
        if not meta:
            continue
        key = (meta.get("accession_number", ""), meta.get("cik", ""))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        filings.append({
            "accession_number": meta.get("accession_number", ""),
            "cik": meta.get("cik", ""),
            "ticker": meta.get("company_ticker", ""),
        })
    return filings


def ensure_filing_embeddings_current(
    filing_text_dict: dict,
    collection_name: str = "sec_filings",
) -> dict:
    accession_number = filing_text_dict.get("metadata", {}).get("accession_number", "")
    if not accession_number:
        return {"status": "skipped", "reason": "missing_accession", "chunks": 0}

    digest = _section_digest(filing_text_dict)
    current = get_filing_chunk_state(accession_number, collection_name=collection_name)
    is_stale = (
        current["count"] == 0
        or current["vector_schema_version"] != VECTOR_SCHEMA_VERSION
        or current["section_text_digest"] != digest
    )
    if not is_stale:
        return {"status": "current", "reason": "up_to_date", "chunks": current["count"]}

    delete_filing_chunks(accession_number, collection_name=collection_name)
    chunks = chunk_filing(filing_text_dict)
    embed_chunks(chunks, collection_name=collection_name)

    reason = "missing_vectors"
    if current["count"] > 0 and current["vector_schema_version"] != VECTOR_SCHEMA_VERSION:
        reason = "vector_schema_version"
    elif current["count"] > 0 and current["section_text_digest"] != digest:
        reason = "section_text_digest"

    return {"status": "refreshed", "reason": reason, "chunks": len(chunks)}


class EphemeralStore:
    """
    In-memory vector store for a single compare run.

    Uses chromadb.EphemeralClient so nothing is written to disk and there
    is no SQLite file to lock.  Compare results are cached separately in
    JSON, so the vector store doesn't need to survive beyond the request.
    """

    def __init__(self) -> None:
        client = chromadb.EphemeralClient(
            settings=ChromaSettings(anonymized_telemetry=False)
        )
        self._col = client.get_or_create_collection(
            "compare", metadata={"hnsw:space": "cosine"}
        )

    def add_chunks(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        batch_size = 100
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            texts_to_embed = [_context_prefix(c) + c.text for c in batch]
            texts_to_store = [c.text for c in batch]
            vectors = _embed(texts_to_embed)
            self._col.upsert(
                ids=[c.chunk_id for c in batch],
                embeddings=vectors,
                documents=texts_to_store,
                metadatas=[_storage_metadata(c) for c in batch],
            )

    def retrieve(
        self,
        query: str,
        tickers: list[str] | None = None,
        k: int = RETRIEVAL_K,
    ) -> list[Chunk]:
        total = self._col.count()
        if total == 0:
            return []
        query_vec = _embed([query])[0]
        where = _build_where(None, tickers)
        safe_k = min(k, total)
        try:
            results = self._col.query(
                query_embeddings=[query_vec],
                n_results=safe_k,
                where=where,
                include=["documents", "metadatas"],
            )
        except Exception:
            results = self._col.query(
                query_embeddings=[query_vec],
                n_results=safe_k,
                include=["documents", "metadatas"],
            )
        chunks = []
        for doc, meta, cid in zip(
            results.get("documents", [[]])[0],
            results.get("metadatas", [[]])[0],
            results.get("ids", [[]])[0],
        ):
            chunks.append(Chunk(chunk_id=cid, text=doc, metadata=ChunkMetadata(**meta)))
        return chunks


def filter_sections_by_query(
    filing_text_dict: dict,
    query: str,
    threshold: float = SECTION_SCOUT_SCORE_THRESHOLD,
) -> dict:
    sections: dict[str, str] = filing_text_dict.get("sections", {})
    non_empty = {k: v for k, v in sections.items() if v and v.strip()}

    if not non_empty or not query.strip():
        return filing_text_dict

    q_vec = _embed([query])[0]

    windows: list[dict] = []
    preview_inputs: list[str] = []
    for section_name, text in non_empty.items():
        label = _SECTION_LABELS.get(section_name, section_name)
        for window_index, (start, end) in enumerate(_section_windows(text)):
            windows.append({
                "section_name": section_name,
                "window_index": window_index,
                "start": start,
                "end": end,
                "text": text,
            })
            preview_inputs.append(f"{label}\n{text[start:end]}")

    if not preview_inputs:
        return filing_text_dict

    s_vecs = _embed(preview_inputs)
    for idx, window in enumerate(windows):
        window["score"] = _dot(q_vec, s_vecs[idx])
        window["section_focus_hint"] = _focus_hint(window["text"], window["start"], window["end"])

    by_section: dict[str, list[dict]] = {}
    for window in windows:
        by_section.setdefault(window["section_name"], []).append(window)

    best_window = max(windows, key=lambda window: window["score"])
    section_focuses: list[dict] = []
    kept_sections: dict[str, str] = {}
    accession_number = filing_text_dict.get("metadata", {}).get("accession_number", "")

    for section_name, section_windows in by_section.items():
        ranked = sorted(section_windows, key=lambda window: window["score"], reverse=True)
        selected = ranked[:SECTION_SCOUT_TOP_WINDOWS]
        selected = [window for window in selected if window["score"] >= threshold]
        if not selected and best_window["section_name"] == section_name:
            selected = [ranked[0]]
        if not selected:
            continue

        spans = []
        for window in selected:
            spans.append(_expand_span(window["start"], window["end"], len(window["text"])))
        merged_spans = _merge_spans(spans)
        selected_by_span = []
        for span_start, span_end in merged_spans:
            matching = [
                window for window in selected
                if window["start"] < span_end and window["end"] > span_start
            ]
            primary = max(matching, key=lambda window: window["score"])
            focused_text = primary["text"][span_start:span_end].strip()
            if not focused_text:
                continue
            section_focuses.append({
                "section_name": section_name,
                "text": focused_text,
                "section_window_index": primary["window_index"],
                "section_focus_hint": primary.get("section_focus_hint"),
                "source_char_start": span_start,
                "source_char_end": span_end,
                "similarity_score": round(primary["score"], 4),
            })
            selected_by_span.append({
                "window_index": primary["window_index"],
                "start": span_start,
                "end": span_end,
                "score": round(primary["score"], 4),
            })

        kept_sections[section_name] = non_empty[section_name]
        logging_utils.log_section_focus(
            accession_number=accession_number,
            item_section=section_name,
            windows_kept=selected_by_span,
            top_scores=[round(window["score"], 4) for window in ranked[:3]],
        )

    if not section_focuses:
        section_focuses.append({
            "section_name": best_window["section_name"],
            "text": best_window["text"][best_window["start"]:best_window["end"]].strip(),
            "section_window_index": best_window["window_index"],
            "section_focus_hint": best_window.get("section_focus_hint"),
            "source_char_start": best_window["start"],
            "source_char_end": best_window["end"],
            "similarity_score": round(best_window["score"], 4),
        })
        kept_sections[best_window["section_name"]] = non_empty[best_window["section_name"]]

    return {
        **filing_text_dict,
        "sections": kept_sections,
        "section_focuses": section_focuses,
    }


_SECTION_LABELS: dict[str, str] = {
    "item_1":   "Item 1 – Business Overview",
    "item_1a":  "Item 1A – Risk Factors",
    "item_2":   "Item 2 – Management Discussion and Analysis",
    "item_3":   "Item 3 – Quantitative Market Risk",
    "item_4":   "Item 4 – Information on the Company (Business Overview, Branding, Products)",
    "item_4a":  "Item 4A – Unresolved Staff Comments",
    "item_5":   "Item 5 – Operating and Financial Review (MD&A)",
    "item_7":   "Item 7 – Management Discussion and Analysis",
    "item_7a":  "Item 7A – Quantitative Disclosures About Market Risk",
    "item_9":   "Item 9 – Regulation FD",
    "item_11":  "Item 11 – Quantitative and Qualitative Disclosures About Market Risk",
}


def _context_prefix(chunk: Chunk) -> str:
    """
    One-line header prepended to a chunk before embedding.
    Makes the vector space aware of who wrote the text, when, and from which
    section — so semantically similar text from different companies or sections
    doesn't collapse to the same embedding region.
    """
    m = chunk.metadata
    label = _SECTION_LABELS.get(m.item_section, m.item_section.replace("_", " ").title())
    focus = f" | Focus: {m.section_focus_hint}" if m.section_focus_hint else ""
    return f"[{m.company_ticker} | {m.form_type} | {m.filing_date} | {label}{focus}]\n"


def _storage_metadata(chunk: Chunk) -> dict:
    return {
        key: value
        for key, value in chunk.metadata.model_dump().items()
        if value is not None
    }


def embed_chunks(chunks: list[Chunk], collection_name: str = "sec_filings") -> None:
    if not chunks:
        return

    collection = _get_collection(collection_name)

    batch_size = 100
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        # Embed contextually-prefixed text for richer vector representation,
        # but store the original clean text for display.
        texts_to_embed = [_context_prefix(c) + c.text for c in batch]
        texts_to_store = [c.text for c in batch]

        vectors = _embed(texts_to_embed)

        collection.upsert(
            ids=[c.chunk_id for c in batch],
            embeddings=vectors,
            documents=texts_to_store,
            metadatas=[_storage_metadata(c) for c in batch],
        )


def _build_where(
    filters: dict | None,
    tickers: list[str] | None,
    accession_numbers: list[str] | None = None,
) -> dict | None:
    conditions = []

    if tickers:
        if len(tickers) == 1:
            conditions.append({"company_ticker": {"$eq": tickers[0]}})
        elif len(tickers) > 1:
            conditions.append({"company_ticker": {"$in": tickers}})

    if accession_numbers:
        if len(accession_numbers) == 1:
            conditions.append({"accession_number": {"$eq": accession_numbers[0]}})
        elif len(accession_numbers) > 1:
            conditions.append({"accession_number": {"$in": accession_numbers}})

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
    accession_numbers: list[str] | None = None,
    collection_name: str = "sec_filings",
) -> list[Chunk]:
    total = _collection_count(collection_name)
    if total == 0:
        return []

    collection = _get_collection(collection_name)

    query_vec = _embed([query])[0]

    where = _build_where(filters, tickers, accession_numbers)
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
