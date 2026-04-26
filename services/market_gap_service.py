from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime

from answer_workflow import _create_json_completion
from config import (
    BUYER_OWNERSHIP_SYSTEM_PROMPT,
    COMMERCIALIZATION_DIFFICULTY_SYSTEM_PROMPT,
    GAP_CLUSTER_SYSTEM_PROMPT,
    MARKET_GAP_SCHEMA_VERSION,
    MARKET_GAP_STATE_DIR,
    MARKET_SUMMARY_SYSTEM_PROMPT,
    OPPORTUNITY_MEMO_SYSTEM_PROMPT,
    OPENAI_MODEL,
    OPENAI_WORKER_MODEL,
    PAIN_EXTRACTION_SYSTEM_PROMPT,
    STRUCTURAL_CONSTRAINT_SYSTEM_PROMPT,
    URGENCY_PERSISTENCE_SYSTEM_PROMPT,
    VECTOR_SCHEMA_VERSION,
)
from mcp_client import get_mcp_client
import cost_tracker
from services.judge_service import judge_market_gap
from models import (
    Chunk,
    Company,
    CostSummary,
    GapCluster,
    MarketGapRequest,
    MarketGapResponse,
    OpportunityMemo,
    PainPoint,
)
import rag_pipeline

os.makedirs(MARKET_GAP_STATE_DIR, exist_ok=True)

PAIN_RETRIEVAL_QUERY = (
    "risks challenges operational inefficiencies regulatory constraints "
    "supply chain problems technology legacy costs failures bottlenecks "
    "unable to solve persistent problems financial penalties"
)

_ANNUAL_FORMS = {"10-K", "20-F"}
_BUYER_OWNERS = {
    "operations",
    "IT",
    "finance",
    "compliance",
    "procurement",
    "distribution",
    "customer_success",
    "management",
    "unknown",
}
_PERSISTENCE_VALUES = {"worsening", "recurring", "recent", "episodic", "unclear"}
_OPPORTUNITY_TYPES = {
    "workflow_software",
    "compliance_automation",
    "infrastructure_tooling",
    "logistics_service_layer",
    "data_analytics",
    "marketplace_network",
    "embedded_finance",
    "other",
}


def _gap_key(req: MarketGapRequest) -> str:
    payload = {
        "query": req.query.strip().lower(),
        "tickers": sorted(c.ticker.upper() for c in req.companies if c.cik),
        "form_types": sorted(f.upper() for f in req.form_types),
        "filing_date_range": req.filing_date_range,
        "schema_version": MARKET_GAP_SCHEMA_VERSION,
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _cache_path(key: str) -> str:
    return os.path.join(MARKET_GAP_STATE_DIR, f"{key}.json")


def _severity_to_num(severity: str) -> float:
    return {"mild": 0.25, "moderate": 0.6, "severe": 1.0}.get(severity, 0.4)


def _avg_severity(pain_points: list[PainPoint]) -> float:
    if not pain_points:
        return 0.4
    return sum(_severity_to_num(p.severity) for p in pain_points) / len(pain_points)


def _problem_severity_label(pain_points: list[PainPoint]) -> str:
    score = _avg_severity(pain_points)
    if score >= 0.8:
        return "severe"
    if score >= 0.5:
        return "moderate"
    return "mild"


def _recency_score(latest_filing_date: str) -> float:
    try:
        year = int(latest_filing_date[:4])
        years_ago = max(0, datetime.now().year - year)
        return max(0.0, 1.0 - years_ago * 0.3)
    except Exception:
        return 0.5


def _cluster_score(
    frequency: int,
    total_companies: int,
    latest_filing_date: str,
    financial_scale: str | None,
    pain_points: list[PainPoint],
) -> float:
    freq_score = frequency / max(total_companies, 1)
    recency = _recency_score(latest_filing_date)
    financial = 1.0 if financial_scale else 0.0
    severity = _avg_severity(pain_points)
    return round(freq_score * 0.4 + recency * 0.2 + financial * 0.2 + severity * 0.2, 4)


def _confidence_from_score(score: float) -> str:
    if score >= 0.65:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


def _select_gap_filings(filings: list[dict], allowed_forms: list[str]) -> list[dict]:
    allowed = {f.upper() for f in allowed_forms}
    filtered = [f for f in filings if f.get("form_type", "").upper() in allowed]
    filtered.sort(key=lambda f: f.get("filing_date", ""), reverse=True)
    annual = [f for f in filtered if f.get("form_type", "").upper() in _ANNUAL_FORMS]
    return (annual or filtered)[:2]


def _build_context(chunks: list[Chunk]) -> str:
    return "\n---\n".join(
        f"[chunk_id: {chunk.chunk_id}]\n"
        f"Filing: {chunk.metadata.form_type} {chunk.metadata.filing_date}\n"
        f"Section: {chunk.metadata.item_section}\n"
        f"Text:\n{chunk.text}"
        for chunk in chunks
    )


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        ordered.append(clean)
    return ordered


def _normalize_buyer_owner(value: str) -> str:
    clean = str(value or "").strip()
    return clean if clean in _BUYER_OWNERS else "unknown"


def _normalize_persistence(value: str) -> str:
    clean = str(value or "").strip()
    return clean if clean in _PERSISTENCE_VALUES else "unclear"


def _normalize_difficulty(value: str) -> str:
    clean = str(value or "").strip()
    return clean if clean in {"low", "medium", "high"} else "medium"


def _normalize_status(value: str) -> str:
    clean = str(value or "").strip()
    return clean if clean in {"no_clear_opportunity", "speculative", "plausible", "strong"} else "speculative"


def _normalize_opportunity_type(value: str) -> str:
    clean = str(value or "").strip()
    return clean if clean in _OPPORTUNITY_TYPES else "other"


def _persistence_score(value: str) -> float:
    return {
        "worsening": 1.0,
        "recurring": 0.8,
        "recent": 0.6,
        "episodic": 0.35,
        "unclear": 0.45,
    }.get(value, 0.45)


def _urgency_score(value: str) -> float:
    return {"high": 1.0, "medium": 0.6, "low": 0.3}.get(value, 0.45)


def _hard_constraint_score(value: str) -> float:
    return {"high": 1.0, "medium": 0.65, "low": 0.35, "insufficient": 0.0}.get(value, 0.25)


def _adoption_penalty(value: str) -> float:
    return {"low": 0.1, "medium": 0.22, "high": 0.35}.get(value, 0.22)


def _compute_opportunity_score(cluster: GapCluster) -> float:
    breadth = cluster.frequency / max(cluster.total_companies, 1)
    severity = _avg_severity(cluster.pain_points)
    persistence = _persistence_score(cluster.persistence_level)
    hard = _hard_constraint_score(cluster.incumbents_stuck_confidence)
    urgency = _urgency_score(cluster.urgency_level)
    penalty = _adoption_penalty(cluster.adoption_difficulty)
    score = breadth * 0.2 + severity * 0.2 + persistence * 0.15 + hard * 0.25 + urgency * 0.2 - penalty
    return round(min(max(score, 0.0), 1.0), 4)


def _collect_cluster_chunks(cluster: GapCluster, company_chunks: dict[str, list[Chunk]], limit: int = 10) -> list[Chunk]:
    relevant: list[Chunk] = []
    chunk_ids = {cid for pp in cluster.pain_points for cid in pp.chunk_ids}
    for ticker in cluster.company_tickers:
        for chunk in company_chunks.get(ticker, []):
            if not chunk_ids or chunk.chunk_id in chunk_ids:
                relevant.append(chunk)
    if not relevant:
        for ticker in cluster.company_tickers:
            relevant.extend(company_chunks.get(ticker, []))
    return relevant[:limit]


async def _extract_pain_points(company: Company, chunks: list[Chunk]) -> list[PainPoint]:
    if not chunks:
        return []
    raw = await _create_json_completion(
        model=OPENAI_WORKER_MODEL,
        messages=[
            {"role": "system", "content": PAIN_EXTRACTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Company: {company.name} ({company.ticker})\n\n"
                    f"Filing excerpts:\n{_build_context(chunks)}"
                ),
            },
        ],
    )
    valid_chunk_ids = {chunk.chunk_id for chunk in chunks}
    chunk_meta_map = {chunk.chunk_id: chunk.metadata for chunk in chunks}

    pain_points: list[PainPoint] = []
    for pp in raw.get("pain_points", []):
        valid_ids = [cid for cid in pp.get("chunk_ids", []) if cid in valid_chunk_ids]
        if not valid_ids:
            continue
        meta = chunk_meta_map.get(valid_ids[0])
        pain_points.append(PainPoint(
            company_ticker=company.ticker,
            text=pp.get("text", ""),
            category=pp.get("category", "operational"),
            financial_scale=pp.get("financial_scale") or None,
            filing_date=meta.filing_date if meta else "",
            form_type=meta.form_type if meta else "",
            accession_number=meta.accession_number if meta else "",
            cik=meta.cik if meta else "",
            chunk_ids=valid_ids,
            confidence=pp.get("confidence", "medium"),
            severity=pp.get("severity", "moderate"),
            buyer_owner_hint=_normalize_buyer_owner(pp.get("buyer_owner_hint", "unknown")),
            recurrence_hint=_normalize_persistence(pp.get("recurrence_hint", "unclear")),
        ))
    return pain_points


async def _cluster_pain_points(
    all_pain_points: list[PainPoint],
    total_companies: int,
    query: str,
) -> list[dict]:
    if not all_pain_points:
        return []
    payload = [
        {
            "index": i,
            "company_ticker": p.company_ticker,
            "text": p.text,
            "category": p.category,
            "financial_scale": p.financial_scale,
            "severity": p.severity,
            "filing_date": p.filing_date,
            "buyer_owner_hint": p.buyer_owner_hint,
            "recurrence_hint": p.recurrence_hint,
        }
        for i, p in enumerate(all_pain_points)
    ]
    raw = await _create_json_completion(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": GAP_CLUSTER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Sector query: {query}\n"
                    f"Total companies analyzed: {total_companies}\n\n"
                    f"Pain points:\n{json.dumps(payload, indent=2)}"
                ),
            },
        ],
    )
    return raw.get("clusters", [])


async def _analyze_structural_constraint(cluster: GapCluster, company_chunks: dict[str, list[Chunk]]) -> dict:
    relevant = _collect_cluster_chunks(cluster, company_chunks)
    if not relevant:
        return {
            "incumbents_stuck_reason": "Insufficient filing evidence to assess structural constraints.",
            "incumbents_stuck_confidence": "insufficient",
            "hard_constraints": [],
            "soft_constraints": [],
            "disconfirming_evidence": [],
            "notes": "",
        }
    return await _create_json_completion(
        model=OPENAI_WORKER_MODEL,
        messages=[
            {"role": "system", "content": STRUCTURAL_CONSTRAINT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Gap cluster: {cluster.theme}\n"
                    f"Description: {cluster.description}\n"
                    f"Affected companies: {', '.join(cluster.company_tickers)}\n\n"
                    f"Relevant filing excerpts:\n{_build_context(relevant)}"
                ),
            },
        ],
    )


async def _analyze_buyer_ownership(cluster: GapCluster, company_chunks: dict[str, list[Chunk]]) -> dict:
    relevant = _collect_cluster_chunks(cluster, company_chunks, limit=8)
    payload = [
        {
            "company_ticker": pp.company_ticker,
            "text": pp.text,
            "buyer_owner_hint": pp.buyer_owner_hint,
            "severity": pp.severity,
            "filing_date": pp.filing_date,
        }
        for pp in cluster.pain_points
    ]
    return await _create_json_completion(
        model=OPENAI_WORKER_MODEL,
        messages=[
            {"role": "system", "content": BUYER_OWNERSHIP_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Gap cluster: {cluster.theme}\n"
                    f"Description: {cluster.description}\n"
                    f"Pain points:\n{json.dumps(payload, indent=2)}\n\n"
                    f"Relevant excerpts:\n{_build_context(relevant)}"
                ),
            },
        ],
    )


async def _analyze_urgency_persistence(cluster: GapCluster) -> dict:
    payload = [
        {
            "company_ticker": pp.company_ticker,
            "filing_date": pp.filing_date,
            "severity": pp.severity,
            "text": pp.text,
            "recurrence_hint": pp.recurrence_hint,
            "financial_scale": pp.financial_scale,
        }
        for pp in cluster.pain_points
    ]
    return await _create_json_completion(
        model=OPENAI_WORKER_MODEL,
        messages=[
            {"role": "system", "content": URGENCY_PERSISTENCE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Gap cluster: {cluster.theme}\n"
                    f"Description: {cluster.description}\n"
                    f"Pain points with filing dates:\n{json.dumps(payload, indent=2)}"
                ),
            },
        ],
    )


async def _analyze_commercialization_difficulty(cluster: GapCluster) -> dict:
    return await _create_json_completion(
        model=OPENAI_WORKER_MODEL,
        messages=[
            {"role": "system", "content": COMMERCIALIZATION_DIFFICULTY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Gap cluster: {cluster.theme}\n"
                    f"Description: {cluster.description}\n"
                    f"Likely buyer owners: {', '.join(cluster.buyer_owners) or 'unknown'}\n"
                    f"Structural constraint confidence: {cluster.incumbents_stuck_confidence}\n"
                    f"Hard constraints: {json.dumps(cluster.hard_constraints)}\n"
                    f"Soft constraints: {json.dumps(cluster.soft_constraints)}"
                ),
            },
        ],
    )


async def _synthesize_opportunity_memo(cluster: GapCluster) -> dict:
    return await _create_json_completion(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": OPPORTUNITY_MEMO_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Gap cluster: {cluster.theme}\n"
                    f"Description: {cluster.description}\n"
                    f"Frequency: {cluster.frequency}/{cluster.total_companies} companies\n"
                    f"Pain severity: {_problem_severity_label(cluster.pain_points)}\n"
                    f"Likely buyer owners: {', '.join(cluster.buyer_owners) or 'unknown'}\n"
                    f"Urgency: {cluster.urgency_level}\n"
                    f"Persistence: {cluster.persistence_level}\n"
                    f"Why now: {cluster.why_now}\n"
                    f"Hard constraint strength: {cluster.incumbents_stuck_confidence}\n"
                    f"Hard constraints: {json.dumps(cluster.hard_constraints)}\n"
                    f"Soft constraints: {json.dumps(cluster.soft_constraints)}\n"
                    f"Why incumbents are stuck: {cluster.incumbents_stuck_reason}\n"
                    f"Commercialization difficulty: {cluster.adoption_difficulty}\n"
                    f"Disconfirming evidence: {json.dumps(cluster.disconfirming_evidence)}\n"
                    f"Opportunity score: {_compute_opportunity_score(cluster)}"
                ),
            },
        ],
    )


async def _generate_summaries(
    query: str,
    gap_clusters: list[GapCluster],
    opportunity_memos: list[OpportunityMemo],
) -> dict:
    payload = {
        "sector_query": query,
        "clusters": [
            {
                "theme": c.theme,
                "frequency": f"{c.frequency}/{c.total_companies}",
                "urgency_level": c.urgency_level,
                "persistence_level": c.persistence_level,
                "incumbents_stuck_confidence": c.incumbents_stuck_confidence,
                "cluster_score": c.cluster_score,
            }
            for c in gap_clusters
        ],
        "opportunity_memos": [
            {
                "title": memo.title,
                "opportunity_status": memo.opportunity_status,
                "opportunity_type": memo.opportunity_type,
                "buyer_owner": memo.buyer_owner,
                "opportunity_score": memo.opportunity_score,
            }
            for memo in opportunity_memos
        ],
    }
    return await _create_json_completion(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": MARKET_SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, indent=2)},
        ],
    )


async def analyze_market_gap(
    req: MarketGapRequest,
    force_refresh: bool = False,
) -> tuple[MarketGapResponse, bool]:
    key = _gap_key(req)
    cache_path = _cache_path(key)

    if not force_refresh and os.path.exists(cache_path):
        with open(cache_path) as f:
            data = json.load(f)
        if (
            data.get("retrieval_version") == VECTOR_SCHEMA_VERSION
            and data.get("schema_version") == MARKET_GAP_SCHEMA_VERSION
        ):
            data["from_cache"] = True
            cached = MarketGapResponse(**data)
            if cached.judge_evaluation is None:
                cached.judge_evaluation = await judge_market_gap(cached, req.query)
                with open(cache_path, "w") as f:
                    json.dump({**cached.model_dump(), "from_cache": False}, f)
            cached.from_cache = True
            return cached, True

    cost_tracker.start_tracking()
    run_id = f"gap_{uuid.uuid4().hex[:10]}"
    mcp = get_mcp_client()
    coverage_notes: list[str] = []
    company_chunks: dict[str, list[Chunk]] = {}

    for company in req.companies:
        if not company.cik:
            coverage_notes.append(f"{company.ticker}: no CIK, skipped.")
            continue

        try:
            filings = await mcp.list_filings(
                cik=company.cik,
                form_types=req.form_types,
                since_date=req.filing_date_range[0],
                until_date=req.filing_date_range[1],
            )
        except Exception as exc:
            coverage_notes.append(f"{company.ticker}: failed to list filings: {exc}")
            continue

        selected = _select_gap_filings(filings, req.form_types)
        if not selected:
            coverage_notes.append(f"{company.ticker}: no qualifying filings in date range.")
            continue

        company_store = rag_pipeline.EphemeralStore()
        fetched_any = False
        for filing_meta in selected:
            accession = filing_meta.get("accession_number", "")
            if not accession:
                continue
            filing_text = await mcp.fetch_filing(accession, cik=company.cik)
            if filing_text.get("error"):
                continue
            focused = rag_pipeline.filter_sections_by_query(filing_text, PAIN_RETRIEVAL_QUERY)
            chunks = rag_pipeline.chunk_filing(focused)
            company_store.add_chunks(chunks)
            fetched_any = True

        if fetched_any:
            company_chunks[company.ticker] = company_store.retrieve(PAIN_RETRIEVAL_QUERY, k=12)
        else:
            coverage_notes.append(f"{company.ticker}: filing text could not be loaded.")

    if not company_chunks:
        result = MarketGapResponse(
            run_id=run_id,
            from_cache=False,
            retrieval_version=VECTOR_SCHEMA_VERSION,
            schema_version=MARKET_GAP_SCHEMA_VERSION,
            industry_summary="No filing data could be loaded for the selected companies.",
            market_structure_summary="",
            coverage_notes=coverage_notes,
        )
        with open(cache_path, "w") as f:
            json.dump(result.model_dump(), f)
        return result, False

    companies_with_chunks = [c for c in req.companies if c.ticker in company_chunks]
    pain_results = await asyncio.gather(*[
        _extract_pain_points(company, company_chunks[company.ticker])
        for company in companies_with_chunks
    ])

    all_pain_points: list[PainPoint] = []
    for pain_list in pain_results:
        all_pain_points.extend(pain_list)

    if not all_pain_points:
        result = MarketGapResponse(
            run_id=run_id,
            from_cache=False,
            retrieval_version=VECTOR_SCHEMA_VERSION,
            schema_version=MARKET_GAP_SCHEMA_VERSION,
            industry_summary="Filing excerpts were retrieved but no discrete pain points were extracted.",
            market_structure_summary="Insufficient evidence to identify market gaps.",
            coverage_notes=coverage_notes,
        )
        with open(cache_path, "w") as f:
            json.dump(result.model_dump(), f)
        return result, False

    raw_clusters = await _cluster_pain_points(all_pain_points, len(req.companies), req.query)

    gap_clusters: list[GapCluster] = []
    for idx, raw_cluster in enumerate(raw_clusters):
        tickers_in_cluster = _dedupe_keep_order(raw_cluster.get("company_tickers", []))
        if len(tickers_in_cluster) < 2:
            continue
        indices = raw_cluster.get("constituent_pain_point_indices", [])
        constituent_pain_points = [all_pain_points[i] for i in indices if 0 <= i < len(all_pain_points)]
        latest_date = raw_cluster.get("latest_filing_date", "")
        financial = raw_cluster.get("financial_scale_estimate") or None
        frequency = len(tickers_in_cluster)
        total = len(req.companies)
        score = _cluster_score(frequency, total, latest_date, financial, constituent_pain_points)
        gap_clusters.append(GapCluster(
            cluster_id=f"cluster_{idx + 1}",
            theme=raw_cluster.get("theme", ""),
            description=raw_cluster.get("description", ""),
            frequency=frequency,
            total_companies=total,
            company_tickers=tickers_in_cluster,
            evidence_count=len(constituent_pain_points),
            latest_filing_date=latest_date,
            financial_scale_estimate=financial,
            cluster_score=score,
            confidence=_confidence_from_score(score),
            pain_points=constituent_pain_points,
        ))

    gap_clusters.sort(key=lambda cluster: cluster.cluster_score, reverse=True)

    if not gap_clusters:
        result = MarketGapResponse(
            run_id=run_id,
            from_cache=False,
            retrieval_version=VECTOR_SCHEMA_VERSION,
            schema_version=MARKET_GAP_SCHEMA_VERSION,
            industry_summary="Pain points were extracted but no shared theme appeared across 2+ companies.",
            market_structure_summary="No shared market gaps were identified at this coverage threshold.",
            coverage_notes=coverage_notes,
        )
        with open(cache_path, "w") as f:
            json.dump(result.model_dump(), f)
        return result, False

    structural_results = await asyncio.gather(*[
        _analyze_structural_constraint(cluster, company_chunks)
        for cluster in gap_clusters
    ])
    for cluster, structural in zip(gap_clusters, structural_results):
        cluster.incumbents_stuck_reason = structural.get("incumbents_stuck_reason", "")
        cluster.incumbents_stuck_confidence = structural.get("incumbents_stuck_confidence", "low")
        cluster.hard_constraints = _dedupe_keep_order(structural.get("hard_constraints", []))
        cluster.soft_constraints = _dedupe_keep_order(structural.get("soft_constraints", []))
        cluster.disconfirming_evidence = _dedupe_keep_order(structural.get("disconfirming_evidence", []))

    buyer_results = await asyncio.gather(*[
        _analyze_buyer_ownership(cluster, company_chunks)
        for cluster in gap_clusters
    ])
    for cluster, buyer in zip(gap_clusters, buyer_results):
        owners = [_normalize_buyer_owner(value) for value in buyer.get("buyer_owners", [])]
        hinted = [_normalize_buyer_owner(point.buyer_owner_hint) for point in cluster.pain_points]
        cluster.buyer_owners = [owner for owner in _dedupe_keep_order(owners + hinted) if owner != "unknown"][:3]
        if not cluster.buyer_owners:
            cluster.buyer_owners = ["unknown"]

    urgency_results = await asyncio.gather(*[
        _analyze_urgency_persistence(cluster)
        for cluster in gap_clusters
    ])
    for cluster, urgency in zip(gap_clusters, urgency_results):
        cluster.urgency_level = urgency.get("urgency_level", "medium")
        cluster.persistence_level = _normalize_persistence(urgency.get("persistence_level", "unclear"))
        cluster.why_now = urgency.get("why_now", "")
        cluster.disconfirming_evidence = _dedupe_keep_order(
            cluster.disconfirming_evidence + urgency.get("disconfirming_evidence", [])
        )

    commercialization_results = await asyncio.gather(*[
        _analyze_commercialization_difficulty(cluster)
        for cluster in gap_clusters
    ])
    for cluster, commercialization in zip(gap_clusters, commercialization_results):
        cluster.adoption_difficulty = _normalize_difficulty(commercialization.get("adoption_difficulty", "medium"))

    memo_results = await asyncio.gather(*[
        _synthesize_opportunity_memo(cluster)
        for cluster in gap_clusters
    ])

    opportunity_memos: list[OpportunityMemo] = []
    for idx, (cluster, raw_memo) in enumerate(zip(gap_clusters, memo_results)):
        evidence_ids = _dedupe_keep_order([cid for point in cluster.pain_points for cid in point.chunk_ids])[:8]
        memo = OpportunityMemo(
            memo_id=f"memo_{idx + 1}",
            title=raw_memo.get("title", cluster.theme),
            target_cluster_id=cluster.cluster_id,
            opportunity_type=_normalize_opportunity_type(raw_memo.get("opportunity_type", "other")),
            buyer_owner=_normalize_buyer_owner(raw_memo.get("buyer_owner", cluster.buyer_owners[0] if cluster.buyer_owners else "unknown")),
            problem=raw_memo.get("problem", cluster.description),
            thesis=raw_memo.get("thesis", raw_memo.get("description", cluster.description)),
            pain_severity=_problem_severity_label(cluster.pain_points),
            urgency_level=cluster.urgency_level or "medium",
            hard_constraint_strength=cluster.incumbents_stuck_confidence or "low",
            adoption_difficulty=cluster.adoption_difficulty or "medium",
            why_incumbents_are_stuck=cluster.incumbents_stuck_reason,
            why_now=cluster.why_now,
            why_this_may_fail=_dedupe_keep_order(raw_memo.get("why_this_may_fail", []) + cluster.disconfirming_evidence)[:4],
            evidence_chunk_ids=evidence_ids,
            opportunity_status=_normalize_status(raw_memo.get("opportunity_status", "speculative")),
            status_rationale=raw_memo.get("status_rationale", ""),
            opportunity_score=_compute_opportunity_score(cluster),
        )
        opportunity_memos.append(memo)

    opportunity_memos.sort(key=lambda memo: memo.opportunity_score, reverse=True)

    summaries = await _generate_summaries(req.query, gap_clusters, opportunity_memos)

    result = MarketGapResponse(
        run_id=run_id,
        from_cache=False,
        retrieval_version=VECTOR_SCHEMA_VERSION,
        schema_version=MARKET_GAP_SCHEMA_VERSION,
        industry_summary=summaries.get("industry_summary", ""),
        market_structure_summary=summaries.get("market_structure_summary", ""),
        gap_clusters=gap_clusters,
        opportunity_memos=opportunity_memos,
        opportunity_hypotheses=[],
        coverage_notes=coverage_notes,
    )

    result.judge_evaluation = await judge_market_gap(result, req.query)
    result.cost_summary = CostSummary(**cost_tracker.get_summary())

    with open(cache_path, "w") as f:
        json.dump(result.model_dump(), f)

    return result, False
