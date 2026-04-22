from models import VerifyRequest
import logging_utils
import hitl
import rag_pipeline


def verify_claim(req: VerifyRequest) -> dict:
    logging_utils.log_verification(req.proposal_id, req.claim_id, req.verdict.value)

    # Fetch evidence chunks for the claim
    answer = hitl.load_answer(req.proposal_id)
    evidence = []
    if answer:
        # Support current nested audit schema plus older cached formats.
        claims_list = (
            answer.get("answer", {}).get("claims_audit", {}).get("claims")
            or answer.get("answer", {}).get("claims")
            or answer.get("claims", [])
        )
        for claim in claims_list:
            if claim.get("claim_id") == req.claim_id:
                for cid in claim.get("supporting_chunk_ids", []):
                    chunk = rag_pipeline.get_chunk_by_id(cid)
                    if chunk:
                        evidence.append({
                            "chunk_id": cid,
                            "text": chunk.text[:500],
                            "company": chunk.metadata.company_name,
                            "form_type": chunk.metadata.form_type,
                            "filing_date": chunk.metadata.filing_date,
                            "section": chunk.metadata.item_section,
                        })

    return {
        "proposal_id": req.proposal_id,
        "claim_id": req.claim_id,
        "verdict": req.verdict.value,
        "evidence": evidence,
        "status": "logged",
    }
