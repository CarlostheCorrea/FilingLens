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
    """Smoke test — skipped if OPENAI_API_KEY not set."""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    from rag_pipeline import chunk_filing, embed_chunks, retrieve
    chunks = chunk_filing(SAMPLE_FILING)
    embed_chunks(chunks[:2])
    results = retrieve("supply chain risk", k=2)
    assert isinstance(results, list)
