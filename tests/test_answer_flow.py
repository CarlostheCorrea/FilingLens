import pytest
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
@pytest.mark.asyncio
async def test_generate_answer_no_chunks():
    """With no chunks in the store, answer should return gaps."""
    from agent import generate_answer
    result = await generate_answer("test_proposal_id", "What is the supply chain risk?")
    assert hasattr(result, "claims")
    assert hasattr(result, "gaps")
    # With no chunks: either empty claims or gaps explaining lack of data
    assert isinstance(result.claims, list)
    assert isinstance(result.gaps, list)
