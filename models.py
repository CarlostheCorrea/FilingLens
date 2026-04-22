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


class WorkflowStage(BaseModel):
    name: str
    status: str
    summary: str


class WorkflowMetadata(BaseModel):
    pattern: str = "supervisor"
    run_id: str
    status: str = "completed"
    stages: list[WorkflowStage]


class OverallKeyPoint(BaseModel):
    text: str
    supporting_tickers: list[str] = Field(default_factory=list)


class OverallAnswer(BaseModel):
    summary: str
    key_points: list[OverallKeyPoint] = Field(default_factory=list)


class CompanyEvidenceItem(BaseModel):
    chunk_id: str
    excerpt: str
    company_ticker: str
    company_name: str
    cik: str
    accession_number: str
    form_type: str
    filing_date: str
    item_section: str


class CompanyDeepDive(BaseModel):
    ticker: str
    company_name: str
    status: str
    summary: str
    evidence: list[CompanyEvidenceItem] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class ClaimsAuditPayload(BaseModel):
    claims: list[Claim] = Field(default_factory=list)


class StructuredAnswerPayload(BaseModel):
    overall_answer: OverallAnswer
    company_deep_dives: list[CompanyDeepDive] = Field(default_factory=list)
    claims_audit: ClaimsAuditPayload
    coverage_notes: list[str] = Field(default_factory=list)


class WorkflowAnswerResponse(BaseModel):
    proposal_id: str
    query: str
    from_cache: bool = False
    workflow: WorkflowMetadata
    answer: StructuredAnswerPayload


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


class CompareRequest(BaseModel):
    ticker_a: str
    ticker_b: str
    query: str
    form_types: list[str]
    filing_date_range: list[str]
    price_lookback: str = "3M"


class StockPricePoint(BaseModel):
    date: str
    close: float
    indexed_close: float


class StockSeries(BaseModel):
    ticker: str
    company_name: str
    points: list[StockPricePoint] = Field(default_factory=list)


class CompareEvidenceItem(BaseModel):
    chunk_id: str
    excerpt: str
    accession_number: str
    cik: str
    form_type: str
    filing_date: str
    item_section: str
    sec_url: str


class CompanyComparison(BaseModel):
    ticker: str
    company_name: str
    status: str
    summary: str
    evidence: list[CompareEvidenceItem] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class FilingEvent(BaseModel):
    ticker: str
    company_name: str
    accession_number: str
    cik: str
    form_type: str
    filing_date: str
    acceptance_datetime: Optional[str] = None
    trading_date: Optional[str] = None
    sec_url: str
    return_1d: Optional[float] = None
    return_5d: Optional[float] = None
    return_30d: Optional[float] = None
    supporting_chunk_ids: list[str] = Field(default_factory=list)
    supporting_excerpts: list[CompareEvidenceItem] = Field(default_factory=list)


class CompareResponse(BaseModel):
    compare_run_id: str
    from_cache: bool = False
    companies: list[Company]
    overall_summary: str
    company_comparisons: list[CompanyComparison] = Field(default_factory=list)
    similarities: list[str] = Field(default_factory=list)
    differences: list[str] = Field(default_factory=list)
    stock_series: list[StockSeries] = Field(default_factory=list)
    filing_events: list[FilingEvent] = Field(default_factory=list)
