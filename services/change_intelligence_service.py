from __future__ import annotations

import hashlib
import json
import os
import uuid
from bisect import bisect_left

from answer_workflow import _create_json_completion
from config import (
    CHANGE_DETECTION_SYSTEM_PROMPT,
    CHANGE_STATE_DIR,
    CHANGE_SYNTHESIS_SYSTEM_PROMPT,
    OPENAI_MODEL,
    OPENAI_WORKER_MODEL,
    VECTOR_SCHEMA_VERSION,
)
from edgar_client import resolve_ticker_to_cik
import logging_utils
from mcp_client import get_mcp_client
from models import (
    ChangeCard,
    ChangeEvidenceItem,
    ChangeIntelligenceRequest,
    ChangeIntelligenceResponse,
    Chunk,
    Company,
    CompareEvidenceItem,
    Filing,
    FilingComparisonWindow,
    FilingEvent,
)
import rag_pipeline
from services.stock_service import fetch_stock_series

os.makedirs(CHANGE_STATE_DIR, exist_ok=True)

_FOREIGN_FORMS = {"20-F", "6-K"}
_ANNUAL_FORMS = {"10-K", "20-F"}
_QUARTERLY_FORMS = {"10-Q"}
_CURRENT_FORMS = {"8-K", "6-K"}
_ALLOWED_CATEGORIES = {
    "new_risk_introduced",
    "risk_removed_or_deemphasized",
    "strategy_emphasis_increased",
    "capital_allocation_change",
    "pricing_or_margin_change",
    "guidance_or_outlook_change",
    "geographic_or_segment_shift",
    "market_positioning_change",
}


def _change_key(req: ChangeIntelligenceRequest) -> str:
    payload = {
        "ticker": req.ticker.strip().upper(),
        "query": req.query.strip().lower(),
        "form_types": sorted({form.upper() for form in req.form_types}),
        "filing_date_range": req.filing_date_range,
        "max_filings": max(2, min(req.max_filings, 5)),
        "price_lookback": (req.price_lookback or "3M").upper(),
        "retrieval_version": VECTOR_SCHEMA_VERSION,
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _cache_path(key: str) -> str:
    return os.path.join(CHANGE_STATE_DIR, f"{key}.json")


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
        rationale="Change intelligence workflow",
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
            f"{company.ticker}: no filings found for {', '.join(form_types)}; used foreign issuer forms 20-F and 6-K instead."
        )
        return fallback_filings, issues

    issues.append(
        f"{company.ticker}: no filings found in the selected date range for {', '.join(form_types)}."
    )
    return [], issues


def _select_change_filings(filings: list[dict], allowed_forms: list[str], max_filings: int) -> list[dict]:
    allowed = set(allowed_forms)
    filtered = [f for f in filings if f.get("form_type", "").upper() in allowed]
    filtered.sort(key=lambda filing: filing.get("filing_date", ""), reverse=True)
    max_filings = max(2, min(max_filings, 5))

    annual = [f for f in filtered if f.get("form_type", "").upper() in _ANNUAL_FORMS]
    quarterly = [f for f in filtered if f.get("form_type", "").upper() in _QUARTERLY_FORMS]
    selected: list[dict]
    if len(annual) >= 2:
        selected = annual[:max_filings]
    elif len(quarterly) >= 2:
        selected = quarterly[:max_filings]
    else:
        selected = filtered[:max_filings]

    if len(selected) < 2:
        for filing in filtered:
            if filing not in selected:
                selected.append(filing)
            if len(selected) >= 2:
                break
    return selected


def _as_filing(company: Company, filing_meta: dict) -> Filing:
    return Filing(
        accession_number=filing_meta.get("accession_number", ""),
        form_type=filing_meta.get("form_type", ""),
        filing_date=filing_meta.get("filing_date", ""),
        company_name=company.name,
        cik=filing_meta.get("cik", company.cik),
        ticker=company.ticker,
    )


def _window_id(after_filing: Filing, before_filing: Filing) -> str:
    return f"{after_filing.accession_number}__{before_filing.accession_number}"


def _build_window(after_filing: Filing, before_filing: Filing) -> FilingComparisonWindow:
    return FilingComparisonWindow(
        window_id=_window_id(after_filing, before_filing),
        label=f"{after_filing.form_type} {after_filing.filing_date} vs {before_filing.form_type} {before_filing.filing_date}",
        before_filing=before_filing,
        after_filing=after_filing,
    )


def _build_context(chunks: list[Chunk]) -> str:
    return "\n---\n".join(
        f"[chunk_id: {chunk.chunk_id}]\n"
        f"Filing: {chunk.metadata.form_type} {chunk.metadata.filing_date}\n"
        f"Section: {chunk.metadata.item_section}\n"
        f"Text:\n{chunk.text}"
        for chunk in chunks
    )


def _build_evidence_items(chunks: list[Chunk], chunk_ids: list[str]) -> list[ChangeEvidenceItem]:
    chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
    items: list[ChangeEvidenceItem] = []
    for chunk_id in chunk_ids:
        chunk = chunk_map.get(chunk_id)
        if not chunk:
            continue
        items.append(
            ChangeEvidenceItem(
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


def _stock_lookup(stock_series) -> tuple[list[str], list[dict]]:
    if not stock_series:
        return [], []
    series = stock_series[0]
    return [point.date for point in series.points], [point.model_dump() for point in series.points]


def _build_filing_events(
    company: Company,
    filings: list[Filing],
    change_cards: list[ChangeCard],
    stock_series,
) -> list[FilingEvent]:
    _, points = _stock_lookup(stock_series)
    excerpts_by_accession: dict[str, list[ChangeEvidenceItem]] = {}
    for card in change_cards:
        for item in card.before_evidence + card.after_evidence:
            existing = excerpts_by_accession.setdefault(item.accession_number, [])
            if item.chunk_id not in {e.chunk_id for e in existing}:
                existing.append(item)

    events: list[FilingEvent] = []
    for filing in filings:
        trading_date, return_1d, return_5d, return_30d = _compute_returns(points, filing.filing_date)
        excerpts = excerpts_by_accession.get(filing.accession_number, [])
        events.append(
            FilingEvent(
                ticker=company.ticker,
                company_name=company.name,
                accession_number=filing.accession_number,
                cik=filing.cik,
                form_type=filing.form_type,
                filing_date=filing.filing_date,
                trading_date=trading_date,
                sec_url=_sec_url(filing.cik, filing.accession_number),
                return_1d=return_1d,
                return_5d=return_5d,
                return_30d=return_30d,
                supporting_chunk_ids=[item.chunk_id for item in excerpts],
                supporting_excerpts=[
                    CompareEvidenceItem(
                        chunk_id=item.chunk_id,
                        excerpt=item.excerpt,
                        accession_number=item.accession_number,
                        cik=item.cik,
                        form_type=item.form_type,
                        filing_date=item.filing_date,
                        item_section=item.item_section,
                        sec_url=item.sec_url,
                    )
                    for item in excerpts
                ],
            )
        )
    events.sort(key=lambda event: event.filing_date, reverse=True)
    return events


def _retrieve_filing_chunks(query: str, filing_text: dict) -> list[Chunk]:
    focused = rag_pipeline.filter_sections_by_query(filing_text, query)
    chunks = rag_pipeline.chunk_filing(focused)
    store = rag_pipeline.EphemeralStore()
    store.add_chunks(chunks)
    return store.retrieve(query, k=6)


async def _detect_window_changes(
    query: str,
    company: Company,
    window: FilingComparisonWindow,
    before_chunks: list[Chunk],
    after_chunks: list[Chunk],
) -> tuple[str, list[ChangeCard], list[str]]:
    raw = await _create_json_completion(
        model=OPENAI_WORKER_MODEL,
        messages=[
            {"role": "system", "content": CHANGE_DETECTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Analysis question: {query}\n"
                    f"Company: {company.name} ({company.ticker})\n"
                    f"Older filing: {window.before_filing.form_type} {window.before_filing.filing_date}\n"
                    f"Newer filing: {window.after_filing.form_type} {window.after_filing.filing_date}\n\n"
                    f"Older filing excerpts:\n{_build_context(before_chunks)}\n\n"
                    f"Newer filing excerpts:\n{_build_context(after_chunks)}"
                ),
            },
        ],
    )

    valid_before = {chunk.chunk_id for chunk in before_chunks}
    valid_after = {chunk.chunk_id for chunk in after_chunks}
    cards: list[ChangeCard] = []
    for idx, change in enumerate(raw.get("changes", []), start=1):
        category = change.get("category", "")
        if category not in _ALLOWED_CATEGORIES:
            continue
        before_ids = [cid for cid in change.get("before_chunk_ids", []) if cid in valid_before]
        after_ids = [cid for cid in change.get("after_chunk_ids", []) if cid in valid_after]
        if not before_ids or not after_ids:
            continue
        before_evidence = _build_evidence_items(before_chunks, before_ids)
        after_evidence = _build_evidence_items(after_chunks, after_ids)
        sec_urls = []
        for item in before_evidence + after_evidence:
            if item.sec_url and item.sec_url not in sec_urls:
                sec_urls.append(item.sec_url)
        cards.append(
            ChangeCard(
                change_id=f"{window.window_id}_chg_{idx}",
                window_id=window.window_id,
                category=category,
                summary=change.get("summary", ""),
                importance=change.get("importance", "medium"),
                confidence=change.get("confidence", "medium"),
                before_filing=window.before_filing,
                after_filing=window.after_filing,
                before_evidence=before_evidence,
                after_evidence=after_evidence,
                sec_urls=sec_urls,
            )
        )

    return raw.get("window_summary", ""), cards, raw.get("gaps", [])


async def _synthesize_overall_summary(
    query: str,
    company: Company,
    windows: list[FilingComparisonWindow],
    change_cards: list[ChangeCard],
) -> str:
    payload = {
        "company": {"ticker": company.ticker, "name": company.name},
        "windows": [
            {
                "label": window.label,
                "window_id": window.window_id,
                "summary": window.summary,
                "gaps": window.gaps,
            }
            for window in windows
        ],
        "change_cards": [
            {
                "change_id": card.change_id,
                "window_id": card.window_id,
                "category": card.category,
                "summary": card.summary,
                "importance": card.importance,
                "confidence": card.confidence,
            }
            for card in change_cards
        ],
    }
    raw = await _create_json_completion(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": CHANGE_SYNTHESIS_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Analysis question: {query}\n\nStructured change payload:\n{json.dumps(payload, indent=2)}",
            },
        ],
    )
    return raw.get("overall_summary", "")


async def change_intelligence(req: ChangeIntelligenceRequest, force_refresh: bool = False) -> tuple[ChangeIntelligenceResponse, bool]:
    key = _change_key(req)
    cache_path = _cache_path(key)

    if not force_refresh and os.path.exists(cache_path):
        with open(cache_path) as handle:
            data = json.load(handle)
        if data.get("retrieval_version") == VECTOR_SCHEMA_VERSION:
            data["from_cache"] = True
            return ChangeIntelligenceResponse(**data), True

    company = _company_from_ticker(req.ticker)
    if not company.cik:
        raise ValueError(f"Could not resolve a CIK for ticker {req.ticker}.")

    form_types = _normalize_forms(req.form_types)
    listed_filings, issues = await _list_company_filings(company, form_types, req.filing_date_range)
    effective_forms = form_types
    listed_forms = {filing.get("form_type", "").upper() for filing in listed_filings}
    if listed_forms and listed_forms.isdisjoint(set(form_types)):
        effective_forms = sorted(set(form_types).union(_FOREIGN_FORMS))
    selected_meta = _select_change_filings(listed_filings, effective_forms, req.max_filings)
    if len(selected_meta) < 2:
        raise ValueError(f"Not enough filings found for {company.ticker} to compare over time.")

    mcp = get_mcp_client()
    filing_records: list[Filing] = []
    chunks_by_accession: dict[str, list[Chunk]] = {}
    for filing_meta in selected_meta:
        accession = filing_meta.get("accession_number", "")
        if not accession:
            continue
        filing_text = await mcp.fetch_filing(accession, cik=company.cik)
        if filing_text.get("error"):
            continue
        metadata = filing_text.get("metadata", {})
        filing_records.append(
            Filing(
                accession_number=accession,
                form_type=filing_meta.get("form_type", metadata.get("form_type", "")),
                filing_date=filing_meta.get("filing_date", metadata.get("filing_date", "")),
                company_name=company.name,
                cik=company.cik,
                ticker=company.ticker,
            )
        )
        chunks_by_accession[accession] = _retrieve_filing_chunks(req.query, filing_text)

    filing_records.sort(key=lambda filing: filing.filing_date, reverse=True)
    if len(filing_records) < 2:
        raise ValueError(f"Not enough filings could be loaded for {company.ticker} to compare over time.")
    windows: list[FilingComparisonWindow] = []
    change_cards: list[ChangeCard] = []
    for idx in range(len(filing_records) - 1):
        after_filing = filing_records[idx]
        before_filing = filing_records[idx + 1]
        window = _build_window(after_filing, before_filing)
        summary, cards, gaps = await _detect_window_changes(
            req.query,
            company,
            window,
            chunks_by_accession.get(before_filing.accession_number, []),
            chunks_by_accession.get(after_filing.accession_number, []),
        )
        window.summary = summary
        window.gaps = gaps + issues
        windows.append(window)
        change_cards.extend(cards)

    overall_summary = await _synthesize_overall_summary(req.query, company, windows, change_cards)
    stock_series = []
    if (req.price_lookback or "").upper() != "OFF":
        stock_series = fetch_stock_series([company], req.price_lookback)
    filing_events = _build_filing_events(company, filing_records, change_cards, stock_series)

    result = ChangeIntelligenceResponse(
        change_run_id=f"chg_{uuid.uuid4().hex[:10]}",
        from_cache=False,
        retrieval_version=VECTOR_SCHEMA_VERSION,
        company=company,
        overall_summary=overall_summary,
        comparison_windows=windows,
        change_cards=change_cards,
        stock_series=stock_series,
        filing_events=filing_events,
    )

    with open(cache_path, "w") as handle:
        json.dump(result.model_dump(), handle)

    logging_utils.log_change_intelligence(result.change_run_id, company.ticker, len(windows), len(change_cards))
    return result, False
