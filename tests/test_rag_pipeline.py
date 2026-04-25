import pytest
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


SAMPLE_FILING = {
    "metadata": {
        "ticker": "TEST",
        "company_name": "Test Corp",
        "cik": "0000000001",
        "accession_number": "0000000001-24-000001",
        "form_type": "10-K",
        "filing_date": "2024-01-15",
    },
    "sections": {
        "item_1": "Test Corp designs and manufactures test widgets. " * 200,
        "item_1a": "Risk factors: market competition, supply chain disruption. " * 200,
        "item_7": "Revenue increased 20% year over year. " * 200,
        "item_7a": "Quantitative risk disclosure. " * 100,
    },
}


def test_chunk_filing_returns_chunks():
    from rag_pipeline import chunk_filing
    chunks = chunk_filing(SAMPLE_FILING)
    assert isinstance(chunks, list)
    assert len(chunks) > 0


def test_chunk_metadata_fields():
    from rag_pipeline import chunk_filing
    chunks = chunk_filing(SAMPLE_FILING)
    c = chunks[0]
    assert c.metadata.company_ticker == "TEST"
    assert c.metadata.form_type == "10-K"
    assert c.metadata.filing_date == "2024-01-15"
    assert c.chunk_id.startswith("TEST_")


def test_chunk_size_reasonable():
    from rag_pipeline import chunk_filing
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    chunks = chunk_filing(SAMPLE_FILING)
    for chunk in chunks:
        tokens = len(enc.encode(chunk.text))
        assert tokens <= 900, f"Chunk too large: {tokens} tokens"


def test_embed_and_retrieve(monkeypatch):
    from rag_pipeline import chunk_filing, embed_chunks, retrieve

    class _Resp:
        def __init__(self, vectors):
            self.data = [type("Embed", (), {"embedding": vector})() for vector in vectors]

    def fake_embed(*, model, input):
        vectors = []
        for text in input:
            lowered = text.lower()
            if "supply chain" in lowered or "risk" in lowered:
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return _Resp(vectors)

    monkeypatch.setattr(__import__("rag_pipeline")._openai.embeddings, "create", fake_embed)
    chunks = chunk_filing(SAMPLE_FILING)
    embed_chunks(chunks[:2], collection_name="test_embed_and_retrieve")
    results = retrieve("supply chain risk", k=2, collection_name="test_embed_and_retrieve")
    assert isinstance(results, list)
    assert results


def test_filter_sections_by_query_uses_deep_windows(monkeypatch):
    import rag_pipeline

    filing = {
        "metadata": {
            "ticker": "BUD",
            "company_name": "Anheuser-Busch InBev SA/NV",
            "cik": "1668717",
            "accession_number": "1668717-26-000001",
            "form_type": "20-F",
            "filing_date": "2026-03-03",
        },
        "sections": {
            "item_4": ("History and development of the company. " * 300)
            + ("Branding and marketing strategy focuses on brand investment and media. " * 120),
            "item_5": "Operating review and finance. " * 200,
        },
    }

    class _Resp:
        def __init__(self, vectors):
            self.data = [type("Embed", (), {"embedding": vector})() for vector in vectors]

    def fake_embed(*, model, input):
        vectors = []
        for text in input:
            lowered = text.lower().split("\n", 1)[-1]
            if "branding" in lowered or "marketing" in lowered:
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return _Resp(vectors)

    monkeypatch.setattr(rag_pipeline._openai.embeddings, "create", fake_embed)
    monkeypatch.setattr(rag_pipeline.logging_utils, "log_section_focus", lambda *args, **kwargs: None)

    focused = rag_pipeline.filter_sections_by_query(filing, "How is BUD approaching branding and marketing?")

    assert focused["section_focuses"]
    first_focus = focused["section_focuses"][0]
    assert first_focus["section_name"] == "item_4"
    assert first_focus["source_char_start"] > 4000
    chunks = rag_pipeline.chunk_filing(focused)
    assert chunks
    assert chunks[0].metadata.section_window_index is not None


def test_ensure_filing_embeddings_current_refreshes_on_schema_version(monkeypatch):
    import rag_pipeline

    filing = {
        "metadata": {
            "ticker": "TEST",
            "company_name": "Test Corp",
            "cik": "1",
            "accession_number": "1-26-000001",
            "form_type": "10-K",
            "filing_date": "2026-01-01",
        },
        "sections": {"item_1": "business overview"},
    }
    calls = {"deleted": 0, "embedded": 0}

    monkeypatch.setattr(
        rag_pipeline,
        "get_filing_chunk_state",
        lambda accession_number, collection_name="sec_filings": {
            "count": 3,
            "vector_schema_version": "old-version",
            "section_text_digest": "old-digest",
        },
    )
    monkeypatch.setattr(rag_pipeline, "delete_filing_chunks", lambda accession_number, collection_name="sec_filings": calls.__setitem__("deleted", calls["deleted"] + 1))
    monkeypatch.setattr(rag_pipeline, "embed_chunks", lambda chunks, collection_name="sec_filings": calls.__setitem__("embedded", len(chunks)))

    result = rag_pipeline.ensure_filing_embeddings_current(filing)

    assert result["status"] == "refreshed"
    assert result["reason"] == "vector_schema_version"
    assert calls["deleted"] == 1
    assert calls["embedded"] > 0
