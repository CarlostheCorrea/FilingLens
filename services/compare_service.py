from __future__ import annotations

import hashlib
import json
import os
import uuid  # still used for compare_run_id
from bisect import bisect_left

from answer_workflow import _create_json_completion
from services.xbrl_context_service import build_xbrl_context
from config import (
    COMPARE_COMPANY_SYSTEM_PROMPT,
    COMPARE_STATE_DIR,
    COMPARE_SYNTHESIS_SYSTEM_PROMPT,
    OPENAI_MODEL,
    OPENAI_WORKER_MODEL,
    VECTOR_SCHEMA_VERSION,
)
import cost_tracker
from models import CostSummary
from services.judge_service import judge_compare
from edgar_client import resolve_ticker_to_cik
import logging_utils
from mcp_client import get_mcp_client
from models import (
    Chunk,
    Company,
    CompanyComparison,
    CompareEvidenceItem,
    CompareRequest,
    CompareResponse,
    FilingEvent,
)
import rag_pipeline
from services.stock_service import fetch_stock_series

os.makedirs(COMPARE_STATE_DIR, exist_ok=True)

_FOREIGN_FORMS = {"20-F", "6-K"}
_ANNUAL_FORMS = {"10-K", "20-F"}
_QUARTERLY_FORMS = {"10-Q"}
_CURRENT_FORMS = {"8-K", "6-K"}


def _compare_key(req: CompareRequest) -> str:
    payload = {
        "ticker_a": req.ticker_a.strip().upper(),
        "ticker_b": req.ticker_b.strip().upper(),
        "query": req.query.strip().lower(),
        "form_types": sorted({form.upper() for form in req.form_types}),
        "filing_date_range": req.filing_date_range,
        "price_lookback": req.price_lookback.upper(),
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _cache_path(key: str) -> str:
    return os.path.join(COMPARE_STATE_DIR, f"{key}.json")



def _normalize_forms(form_types: list[str]) -> list[str]:
    return [form.strip().upper() for form in form_types if form.strip()]


def _sec_url(cik: str, accession_number: str) -> str:
    if not cik or not accession_number:
        return ""
    normalized_cik = str(int(str(cik).split("-")[0]))
    accession = accession_number.strip()
    return (
        f"https://www.sec.gov/Archives/edgar/data/{normalized_cik}/"
        f"{accession.replace('-', '')}/{accession}-index.htm"
    )


def _company_from_ticker(ticker: str) -> Company:
    info = resolve_ticker_to_cik(ticker) or {}
    return Company(
        ticker=ticker.strip().upper(),
        name=info.get("name", ticker.strip().upper()),
        cik=info.get("cik", ""),
        sic=info.get("sic"),
        rationale="Compare workflow",
    )


async def _list_company_filings(company: Company, form_types: list[str], date_range: list[str]) -> tuple[list[dict], list[str]]:
    mcp = get_mcp_client()
    issues: list[str] = []
    filings = await mcp.list_filings(
        cik=company.cik,
        form_types=form_types,
        since_date=date_range[0],
        until_date=date_range[1],
    )
    if filings or _FOREIGN_FORMS.intersection(form_types):
        return filings, issues

    fallback_forms = sorted(_FOREIGN_FORMS)
    fallback_filings = await mcp.list_filings(
        cik=company.cik,
        form_types=fallback_forms,
        since_date=date_range[0],
        until_date=date_range[1],
    )
    if fallback_filings:
        issues.append(
            f"{company.ticker}: no filings found for {', '.join(form_types)}; "
            "used foreign issuer forms 20-F and 6-K instead."
        )
        return fallback_filings, issues

    issues.append(
        f"{company.ticker}: no filings found in the selected date range for "
        f"{', '.join(form_types)}."
    )
    return [], issues


def _select_compare_filings(filings: list[dict], allowed_forms: list[str]) -> list[dict]:
    allowed = set(allowed_forms)
    filtered = [f for f in filings if f.get("form_type", "").upper() in allowed]
    filtered.sort(key=lambda filing: filing.get("filing_date", ""), reverse=True)

    selected: list[dict] = []
    selected_accessions: set[str] = set()

    def _take(forms: set[str], limit: int):
        count = 0
        for filing in filtered:
            accession = filing.get("accession_number", "")
            if accession in selected_accessions:
                continue
            if filing.get("form_type", "").upper() not in forms:
                continue
            selected.append(filing)
            selected_accessions.add(accession)
            count += 1
            if count >= limit:
                break

    _take(_ANNUAL_FORMS.intersection(allowed), 1)
    _take(_QUARTERLY_FORMS.intersection(allowed), 2)
    _take(_CURRENT_FORMS.intersection(allowed), 5)

    uncategorized = allowed - _ANNUAL_FORMS - _QUARTERLY_FORMS - _CURRENT_FORMS
    if uncategorized:
        _take(uncategorized, 2)

    selected.sort(key=lambda filing: filing.get("filing_date", ""), reverse=True)
    return selected


def _build_company_context(chunks: list[Chunk]) -> str:
    return "\n---\n".join(
        f"[chunk_id: {chunk.chunk_id}]\n"
        f"Filing: {chunk.metadata.form_type} {chunk.metadata.filing_date}\n"
        f"Section: {chunk.metadata.item_section}\n"
        f"Text:\n{chunk.text}"
        for chunk in chunks
    )


def _build_evidence_items(chunks: list[Chunk], evidence_chunk_ids: list[str]) -> list[CompareEvidenceItem]:
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
    items: list[CompareEvidenceItem] = []
    for chunk_id in evidence_chunk_ids:
        chunk = chunk_map.get(chunk_id)
        if not chunk:
            continue
        items.append(
            CompareEvidenceItem(
                chunk_id=chunk.chunk_id,
                excerpt=chunk.text[:500] + ("…" if len(chunk.text) > 500 else ""),
                accession_number=chunk.metadata.accession_number,
                cik=chunk.metadata.cik,
                form_type=chunk.metadata.form_type,
                filing_date=chunk.metadata.filing_date,
                item_section=chunk.metadata.item_section,
                sec_url=_sec_url(chunk.metadata.cik, chunk.metadata.accession_number),
            )
        )
    return items


def _stock_lookup(stock_series) -> dict[str, tuple[list[str], list[dict]]]:
    lookup: dict[str, tuple[list[str], list[dict]]] = {}
    for series in stock_series:
        lookup[series.ticker] = ([point.date for point in series.points], [point.model_dump() for point in series.points])
    return lookup


def _compute_returns(points: list[dict], filing_date: str) -> tuple[str | None, float | None, float | None, float | None]:
    if not points:
        return None, None, None, None

    dates = [point["date"] for point in points]
    idx = bisect_left(dates, filing_date)
    if idx >= len(points):
        return None, None, None, None

    base = points[idx]["close"]
    trading_date = points[idx]["date"]

    def _window_return(offset: int) -> float | None:
        target = idx + offset
        if target >= len(points) or not base:
            return None
        return round(((points[target]["close"] - base) / base) * 100.0, 4)

    return trading_date, _window_return(1), _window_return(5), _window_return(30)


def _build_filing_events(companies: list[Company], filings_by_ticker: dict[str, list[dict]], comparisons: list[CompanyComparison], stock_series) -> list[FilingEvent]:
    comparison_map = {comparison.ticker: comparison for comparison in comparisons}
    stock_lookup = _stock_lookup(stock_series)
    company_map = {company.ticker: company for company in companies}

    events: list[FilingEvent] = []
    for ticker, filings in filings_by_ticker.items():
        dates, points = stock_lookup.get(ticker, ([], []))
        _ = dates
        comparison = comparison_map.get(ticker)
        company = company_map.get(ticker)
        evidence = comparison.evidence if comparison else []

        for filing in filings:
            accession_number = filing.get("accession_number", "")
            if not accession_number:
                continue
            supporting_excerpts = [
                item for item in evidence
                if item.accession_number == accession_number
            ]
            trading_date, return_1d, return_5d, return_30d = _compute_returns(points, filing.get("filing_date", ""))
            events.append(
                FilingEvent(
                    ticker=ticker,
                    company_name=company.name if company else filing.get("company_name", ticker),
                    accession_number=accession_number,
                    cik=filing.get("cik", company.cik if company else ""),
                    form_type=filing.get("form_type", ""),
                    filing_date=filing.get("filing_date", ""),
                    acceptance_datetime=filing.get("acceptance_datetime") or None,
                    trading_date=trading_date,
                    sec_url=_sec_url(filing.get("cik", company.cik if company else ""), accession_number),
                    return_1d=return_1d,
                    return_5d=return_5d,
                    return_30d=return_30d,
                    supporting_chunk_ids=[item.chunk_id for item in supporting_excerpts],
                    supporting_excerpts=supporting_excerpts,
                )
            )

    events.sort(key=lambda event: (event.filing_date, event.ticker), reverse=True)
    return events


async def _compare_company(query: str, company: Company, store: rag_pipeline.EphemeralStore) -> CompanyComparison:
    chunks = store.retrieve(query, tickers=[company.ticker])

    if not chunks:
        return CompanyComparison(
            ticker=company.ticker,
            company_name=company.name,
            status="insufficient_evidence",
            summary=f"No relevant indexed filing evidence was found for {company.name}.",
            evidence=[],
            gaps=[f"No indexed filing data found for {company.ticker}."],
        )

    raw = await _create_json_completion(
        model=OPENAI_WORKER_MODEL,
        messages=[
            {"role": "system", "content": COMPARE_COMPANY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Comparison question: {query}\n"
                    f"Company: {company.name} ({company.ticker})\n\n"
                    f"Filing excerpts:\n{_build_company_context(chunks)}"
                ),
            },
        ],
    )

    valid_chunk_ids = {chunk.chunk_id for chunk in chunks}
    evidence_chunk_ids = [
        chunk_id for chunk_id in raw.get("evidence_chunk_ids", [])
        if chunk_id in valid_chunk_ids
    ] or [chunk.chunk_id for chunk in chunks[:3]]

    return CompanyComparison(
        ticker=company.ticker,
        company_name=company.name,
        status="supported",
        summary=raw.get("summary", ""),
        evidence=_build_evidence_items(chunks, evidence_chunk_ids),
        gaps=raw.get("gaps", []),
    )


async def _synthesize_compare(
    query: str,
    comparisons: list[CompanyComparison],
    companies: list[Company],
) -> tuple[str, list[str], list[str]]:
    payload = [
        {
            "ticker": comparison.ticker,
            "company_name": comparison.company_name,
            "summary": comparison.summary,
            "status": comparison.status,
            "gaps": comparison.gaps,
        }
        for comparison in comparisons
    ]

    # Always attach XBRL metrics to comparisons — financial numbers almost
    # always improve comparison quality regardless of query phrasing.
    xbrl_block = await build_xbrl_context(
        [{"ticker": c.ticker, "name": c.name, "cik": c.cik} for c in companies]
    )
    xbrl_section = f"\n\n{xbrl_block}" if xbrl_block else ""

    raw = await _create_json_completion(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": COMPARE_SYNTHESIS_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Comparison question: {query}\n\n"
                    f"Company summaries:\n{json.dumps(payload, indent=2)}"
                    f"{xbrl_section}"
                ),
            },
        ],
    )
    return (
        raw.get("overall_summary", ""),
        raw.get("similarities", []),
        raw.get("differences", []),
    )


async def compare_companies(req: CompareRequest, force_refresh: bool = False) -> tuple[CompareResponse, bool]:
    key = _compare_key(req)
    cache_path = _cache_path(key)

    if not force_refresh and os.path.exists(cache_path):
        with open(cache_path) as handle:
            data = json.load(handle)
        if data.get("retrieval_version") == VECTOR_SCHEMA_VERSION:
            data["from_cache"] = True
            cached = CompareResponse(**data)
            if cached.judge_evaluation is None:
                cached.judge_evaluation = await judge_compare(cached, req.query)
                with open(cache_path, "w") as handle:
                    json.dump({**cached.model_dump(), "from_cache": False}, handle)
            cached.from_cache = True
            return cached, True

    cost_tracker.start_tracking()
    companies = [
        _company_from_ticker(req.ticker_a),
        _company_from_ticker(req.ticker_b),
    ]
    compare_run_id = f"cmp_{uuid.uuid4().hex[:10]}"

    filings_by_ticker: dict[str, list[dict]] = {}
    issues_by_ticker: dict[str, list[str]] = {}
    form_types = _normalize_forms(req.form_types)
    mcp = get_mcp_client()

    # One in-memory store per compare run — no disk writes, no SQLite locks.
    store = rag_pipeline.EphemeralStore()

    for company in companies:
        issues_by_ticker[company.ticker] = []
        filings_by_ticker[company.ticker] = []

        if not company.cik:
            issues_by_ticker[company.ticker].append(f"{company.ticker}: could not resolve a CIK for this ticker.")
            continue

        listed_filings, issues = await _list_company_filings(company, form_types, req.filing_date_range)
        effective_forms = form_types
        listed_forms = {filing.get("form_type", "").upper() for filing in listed_filings}
        if listed_forms and listed_forms.isdisjoint(set(form_types)):
            effective_forms = sorted(set(form_types).union(_FOREIGN_FORMS))
        selected_filings = _select_compare_filings(listed_filings, effective_forms)
        issues_by_ticker[company.ticker].extend(issues)

        for filing_meta in selected_filings:
            accession = filing_meta.get("accession_number", "")
            if not accession:
                continue

            filing_text = await mcp.fetch_filing(accession, cik=company.cik)
            if filing_text.get("error"):
                continue

            metadata = filing_text.get("metadata", {})
            filing_record = {
                "accession_number": accession,
                "cik": company.cik,
                "form_type": filing_meta.get("form_type", metadata.get("form_type", "")),
                "filing_date": filing_meta.get("filing_date", metadata.get("filing_date", "")),
                "acceptance_datetime": (
                    filing_meta.get("acceptance_datetime")
                    or metadata.get("acceptance_datetime", "")
                ),
                "company_name": company.name,
                "ticker": company.ticker,
            }
            filings_by_ticker[company.ticker].append(filing_record)

            focused = rag_pipeline.filter_sections_by_query(filing_text, req.query)
            chunks = rag_pipeline.chunk_filing(focused)
            store.add_chunks(chunks)

    comparisons = [
        await _compare_company(req.query, company, store)
        for company in companies
    ]
    for comparison in comparisons:
        comparison.gaps.extend(issues_by_ticker.get(comparison.ticker, []))
    overall_summary, similarities, differences = await _synthesize_compare(req.query, comparisons, companies)
    stock_series = fetch_stock_series(companies, req.price_lookback)
    filing_events = _build_filing_events(companies, filings_by_ticker, comparisons, stock_series)

    result = CompareResponse(
        compare_run_id=compare_run_id,
        from_cache=False,
        retrieval_version=VECTOR_SCHEMA_VERSION,
        companies=companies,
        overall_summary=overall_summary,
        company_comparisons=comparisons,
        similarities=similarities,
        differences=differences,
        stock_series=stock_series,
        filing_events=filing_events,
    )

    result.judge_evaluation = await judge_compare(result, req.query)
    result.cost_summary = CostSummary(**cost_tracker.get_summary())

    with open(cache_path, "w") as handle:
        json.dump(result.model_dump(), handle)

    logging_utils.log_compare(compare_run_id, [company.ticker for company in companies], filing_events, len(companies))
    return result, False
