import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_verify_claim_reads_nested_claims_audit(monkeypatch):
    from models import VerifyRequest
    from services import verification_service

    monkeypatch.setattr(verification_service.hitl, "load_answer", lambda proposal_id: {
        "answer": {
            "claims_audit": {
                "claims": [{
                    "claim_id": "merged_claim_1",
                    "supporting_chunk_ids": ["chunk_1"],
                }]
            }
        }
    })

    class FakeChunk:
        class metadata:
            company_name = "Molson Coors Beverage Company"
            form_type = "10-K"
            filing_date = "2026-02-18"
            item_section = "item_1a"

        text = "Consumer preferences are evolving rapidly."

    monkeypatch.setattr(verification_service.rag_pipeline, "get_chunk_by_id", lambda chunk_id: FakeChunk())
    monkeypatch.setattr(verification_service.logging_utils, "log_verification", lambda *args, **kwargs: None)

    result = verification_service.verify_claim(
        VerifyRequest(
            proposal_id="scope_test",
            claim_id="merged_claim_1",
            verdict="confirmed",
        )
    )

    assert result["claim_id"] == "merged_claim_1"
    assert len(result["evidence"]) == 1
    assert result["evidence"][0]["chunk_id"] == "chunk_1"
