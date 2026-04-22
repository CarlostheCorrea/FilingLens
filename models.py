from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class Company(BaseModel):
    ticker: str
    name: str
    cik: str
    sic: Optional[str] = None
    rationale: Optional[str] = None


class Filing(BaseModel):
    accession_number: str
    form_type: str
    filing_date: str
    company_name: str
    cik: str
    ticker: Optional[str] = None


class ScopeProposal(BaseModel):
    proposal_id: str
    companies: list[Company]
    form_types: list[str]
    date_range: list[str]  # [start, end]
    overall_rationale: str


class ApprovedScope(BaseModel):
    proposal_id: str
    approved_companies: list[Company]
    form_types: list[str]
    date_range: list[str]


class Claim(BaseModel):
    claim_id: str
    text: str
    supporting_chunk_ids: list[str]
    confidence: str  # "high" | "medium" | "low"


class AnswerResponse(BaseModel):
    proposal_id: str
    query: str
    claims: list[Claim]
    gaps: list[str]


class ClaimVerdict(str, Enum):
    confirmed = "confirmed"
    needs_revision = "needs_revision"
    hallucinated = "hallucinated"


class VerifyRequest(BaseModel):
    proposal_id: str
    claim_id: str
    verdict: ClaimVerdict


class ProposeRequest(BaseModel):
    query: str


class ManualScopeRequest(BaseModel):
    tickers: list[str]
    form_types: list[str]
    date_range: list[str]  # [start, end]


class IngestRequest(BaseModel):
    proposal_id: str


class AnswerRequest(BaseModel):
    proposal_id: str
    query: str


class ChunkMetadata(BaseModel):
    chunk_id: str
    company_ticker: str
    company_name: str
    cik: str
    accession_number: str
    form_type: str
    filing_date: str
    item_section: str
    chunk_index: int


class Chunk(BaseModel):
    chunk_id: str
    text: str
    metadata: ChunkMetadata
