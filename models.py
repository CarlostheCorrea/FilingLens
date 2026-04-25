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


class JudgeEvaluation(BaseModel):
    helpfulness: int
    clarity: int
    grounding: int
    citation_quality: int
    overclaiming_risk: str
    overall_verdict: str
    summary: str
    strengths: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)


class StructuredAnswerPayload(BaseModel):
    overall_answer: OverallAnswer
    company_deep_dives: list[CompanyDeepDive] = Field(default_factory=list)
    claims_audit: ClaimsAuditPayload
    coverage_notes: list[str] = Field(default_factory=list)
    judge_evaluation: Optional[JudgeEvaluation] = None


class WorkflowAnswerResponse(BaseModel):
    proposal_id: str
    query: str
    from_cache: bool = False
    retrieval_version: str = ""
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
    section_window_index: Optional[int] = None
    section_focus_hint: Optional[str] = None
    source_char_start: Optional[int] = None
    source_char_end: Optional[int] = None
    section_text_digest: Optional[str] = None
    vector_schema_version: Optional[str] = None


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
    retrieval_version: str = ""
    companies: list[Company]
    overall_summary: str
    company_comparisons: list[CompanyComparison] = Field(default_factory=list)
    similarities: list[str] = Field(default_factory=list)
    differences: list[str] = Field(default_factory=list)
    stock_series: list[StockSeries] = Field(default_factory=list)
    filing_events: list[FilingEvent] = Field(default_factory=list)


class ChangeIntelligenceRequest(BaseModel):
    ticker: str
    query: str
    form_types: list[str]
    filing_date_range: list[str]
    max_filings: int = 3
    price_lookback: str = "3M"


class ChangeEvidenceItem(BaseModel):
    chunk_id: str
    excerpt: str
    accession_number: str
    cik: str
    form_type: str
    filing_date: str
    item_section: str
    sec_url: str


class FilingComparisonWindow(BaseModel):
    window_id: str
    label: str
    before_filing: Filing
    after_filing: Filing
    summary: str = ""
    gaps: list[str] = Field(default_factory=list)


class ChangeCard(BaseModel):
    change_id: str
    window_id: str
    category: str
    summary: str
    importance: str
    confidence: str
    before_filing: Filing
    after_filing: Filing
    before_evidence: list[ChangeEvidenceItem] = Field(default_factory=list)
    after_evidence: list[ChangeEvidenceItem] = Field(default_factory=list)
    sec_urls: list[str] = Field(default_factory=list)


class ChangeIntelligenceResponse(BaseModel):
    change_run_id: str
    from_cache: bool = False
    retrieval_version: str = ""
    company: Company
    overall_summary: str
    comparison_windows: list[FilingComparisonWindow] = Field(default_factory=list)
    change_cards: list[ChangeCard] = Field(default_factory=list)
    stock_series: list[StockSeries] = Field(default_factory=list)
    filing_events: list[FilingEvent] = Field(default_factory=list)


# ── Market Gap Discovery ──────────────────────────────────────────────────────

class PainPoint(BaseModel):
    company_ticker: str
    text: str
    category: str  # operational | regulatory | supply_chain | technology | competitive | financial
    financial_scale: Optional[str] = None
    filing_date: str
    form_type: str
    accession_number: str = ""
    cik: str = ""
    chunk_ids: list[str] = Field(default_factory=list)
    confidence: str  # high | medium | low
    severity: str    # mild | moderate | severe
    buyer_owner_hint: str = ""
    recurrence_hint: str = ""


class GapCluster(BaseModel):
    cluster_id: str
    theme: str
    description: str
    frequency: int
    total_companies: int
    company_tickers: list[str]
    evidence_count: int
    latest_filing_date: str
    financial_scale_estimate: Optional[str] = None
    incumbents_stuck_reason: str = ""
    incumbents_stuck_confidence: str = ""  # high | medium | low | insufficient
    hard_constraints: list[str] = Field(default_factory=list)
    soft_constraints: list[str] = Field(default_factory=list)
    buyer_owners: list[str] = Field(default_factory=list)
    urgency_level: str = ""
    persistence_level: str = ""
    adoption_difficulty: str = ""
    why_now: str = ""
    disconfirming_evidence: list[str] = Field(default_factory=list)
    cluster_score: float = 0.0
    confidence: str = "medium"  # high | medium | low
    pain_points: list[PainPoint] = Field(default_factory=list)


class OpportunityHypothesis(BaseModel):
    hypothesis_id: str
    title: str
    description: str
    target_cluster_id: str
    why_incumbents_cant_copy: str
    failure_modes: list[str] = Field(default_factory=list)
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    opportunity_status: str  # no_clear_opportunity | speculative | plausible | strong
    status_rationale: str


class OpportunityMemo(BaseModel):
    memo_id: str
    title: str
    target_cluster_id: str
    opportunity_type: str
    buyer_owner: str
    problem: str
    thesis: str
    pain_severity: str
    urgency_level: str
    hard_constraint_strength: str
    adoption_difficulty: str
    why_incumbents_are_stuck: str
    why_now: str
    why_this_may_fail: list[str] = Field(default_factory=list)
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    opportunity_status: str  # no_clear_opportunity | speculative | plausible | strong
    status_rationale: str
    opportunity_score: float = 0.0


class MarketGapRequest(BaseModel):
    query: str
    companies: list[Company]
    form_types: list[str]
    filing_date_range: list[str]


class MarketGapResponse(BaseModel):
    run_id: str
    from_cache: bool = False
    retrieval_version: str = ""
    schema_version: str = ""
    industry_summary: str
    market_structure_summary: str = ""
    gap_clusters: list[GapCluster] = Field(default_factory=list)
    opportunity_memos: list[OpportunityMemo] = Field(default_factory=list)
    opportunity_hypotheses: list[OpportunityHypothesis] = Field(default_factory=list)
    coverage_notes: list[str] = Field(default_factory=list)
