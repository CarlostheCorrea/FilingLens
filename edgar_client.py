"""
Wraps edgartools to provide stable, JSON-serializable helpers.
The agent never calls edgartools directly — it goes through MCP tools
which call these functions internally.
"""

import os
import json
import re
from typing import Optional
import edgar
from config import EDGAR_IDENTITY, FILINGS_CACHE_DIR

os.makedirs(FILINGS_CACHE_DIR, exist_ok=True)

# Set EDGAR identity once on import
edgar.set_identity(EDGAR_IDENTITY)


def _safe_str(val) -> str:
    return str(val) if val is not None else ""


def search_companies_by_sic(sic_code: str) -> list[dict]:
    try:
        companies = edgar.get_entity_submissions(sic=sic_code)
        results = []
        for c in companies[:50]:
            results.append({
                "ticker": _safe_str(getattr(c, "tickers", [""])[0] if getattr(c, "tickers", []) else ""),
                "name": _safe_str(getattr(c, "name", "")),
                "cik": _safe_str(getattr(c, "cik", "")),
                "sic": sic_code,
            })
        return results
    except Exception:
        return _search_companies_by_sic_fallback(sic_code)


def _search_companies_by_sic_fallback(sic_code: str) -> list[dict]:
    try:
        company_search = edgar.CompanySearchIndex()
        results = []
        for entry in company_search:
            if _safe_str(getattr(entry, "sic", "")) == sic_code:
                tickers = getattr(entry, "tickers", []) or []
                results.append({
                    "ticker": tickers[0] if tickers else "",
                    "name": _safe_str(getattr(entry, "name", "")),
                    "cik": _safe_str(getattr(entry, "cik", "")),
                    "sic": sic_code,
                })
                if len(results) >= 50:
                    break
        return results
    except Exception:
        return []


def search_company_by_name(query: str) -> list[dict]:
    try:
        results_raw = edgar.find_company(query)
        results = []
        if results_raw is None:
            return []
        items = results_raw if isinstance(results_raw, list) else [results_raw]
        for c in items[:20]:
            tickers = getattr(c, "tickers", []) or []
            results.append({
                "ticker": tickers[0] if tickers else "",
                "name": _safe_str(getattr(c, "name", "")),
                "cik": _safe_str(getattr(c, "cik", "")),
                "sic": _safe_str(getattr(c, "sic", "")),
            })
        return results
    except Exception:
        return []


def resolve_ticker_to_cik(ticker: str) -> dict:
    try:
        company = edgar.Company(ticker)
        tickers = getattr(company, "tickers", []) or []
        return {
            "ticker": ticker.upper(),
            "name": _safe_str(getattr(company, "name", "")),
            "cik": _safe_str(getattr(company, "cik", "")),
            "sic": _safe_str(getattr(company, "sic", "")),
        }
    except Exception:
        return {}


def list_filings(
    cik: str,
    form_types: list[str],
    since_date: str,
    until_date: str,
) -> list[dict]:
    try:
        company = edgar.Company(cik)
        filings = company.get_filings(form=form_types)
        results = []
        for f in filings:
            date_str = _safe_str(getattr(f, "filing_date", ""))
            if since_date and date_str < since_date:
                continue
            if until_date and date_str > until_date:
                continue
            tickers = getattr(company, "tickers", []) or []
            results.append({
                "accession_number": _safe_str(getattr(f, "accession_no", "")),
                "form_type": _safe_str(getattr(f, "form", "")),
                "filing_date": date_str,
                "company_name": _safe_str(getattr(company, "name", "")),
                "cik": cik,
                "ticker": tickers[0] if tickers else "",
            })
        return results[:30]
    except Exception:
        return []


def fetch_filing_text(accession_number: str) -> dict:
    cache_path = os.path.join(FILINGS_CACHE_DIR, f"{accession_number.replace('/', '_')}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    try:
        filing, company = _get_filing_by_accession(accession_number)
        if filing is None:
            return {"error": f"Filing not found: {accession_number}", "accession_number": accession_number, "metadata": {}, "sections": {}}

        tickers = getattr(company, "tickers", []) or []
        ticker = tickers[0] if tickers else ""

        metadata = {
            "ticker": ticker,
            "company_name": _safe_str(getattr(company, "name", "")),
            "cik": _safe_str(getattr(company, "cik", "")),
            "accession_number": accession_number,
            "form_type": _safe_str(getattr(filing, "form", "")),
            "filing_date": _safe_str(getattr(filing, "filing_date", "")),
        }

        sections = _extract_sections(filing)

        result = {"metadata": metadata, "sections": sections}
        with open(cache_path, "w") as f:
            json.dump(result, f)
        return result
    except Exception as e:
        return {"error": str(e), "accession_number": accession_number, "metadata": {}, "sections": {}}


def _get_filing_by_accession(accession_number: str):
    """
    Resolve a filing object by accession number.
    edgartools has no get_filing() function — we parse the CIK from the
    accession number (first 10 digits) and search the company's filings.
    Accession number format: XXXXXXXXXX-YY-ZZZZZZ
    """
    # Try direct Filing class first (some edgartools versions expose it)
    try:
        from edgar import Filing
        filing = Filing(accession_no=accession_number)
        if filing is not None:
            company = getattr(filing, "company", None)
            if company:
                return filing, company
    except Exception:
        pass

    # Parse CIK from the accession number prefix
    try:
        parts = accession_number.replace("/", "-").split("-")
        cik_raw = parts[0]  # e.g. "0001045810"
        cik = str(int(cik_raw))  # strip leading zeros → "1045810"

        company = edgar.Company(cik)
        filings = company.get_filings()

        normalized_target = accession_number.replace("-", "").replace("/", "").lower()

        for f in filings:
            acc = _safe_str(getattr(f, "accession_no", ""))
            if acc.replace("-", "").lower() == normalized_target:
                return f, company
    except Exception:
        pass

    return None, None


def _extract_sections(filing) -> dict:
    sections = {}
    target_items = {
        "item_1": ["item 1.", "item1.", "business"],
        "item_1a": ["item 1a.", "item1a.", "risk factors"],
        "item_7": ["item 7.", "item7.", "management"],
        "item_7a": ["item 7a.", "item7a.", "quantitative"],
    }

    try:
        # Try structured document access first
        doc = filing.primary_documents
        if doc:
            full_text = ""
            for d in doc:
                try:
                    text = d.text if hasattr(d, "text") else ""
                    full_text += text + "\n"
                except Exception:
                    pass

            if not full_text.strip():
                raise ValueError("empty doc text")

            sections = _parse_sections_from_text(full_text, target_items)
    except Exception:
        pass

    if not any(sections.values()):
        try:
            html = filing.html() if hasattr(filing, "html") else ""
            if html:
                import re
                text = re.sub(r"<[^>]+>", " ", html)
                text = re.sub(r"\s+", " ", text)
                sections = _parse_sections_from_text(text, target_items)
        except Exception:
            pass

    if not any(sections.values()):
        try:
            txt = filing.text() if hasattr(filing, "text") else ""
            sections = _parse_sections_from_text(txt, target_items)
        except Exception:
            pass

    return sections


def _parse_sections_from_text(text: str, target_items: dict) -> dict:
    sections: dict[str, str] = {}
    text_lower = text.lower()

    item_keys = list(target_items.keys())
    markers = [(key, kws) for key, kws in target_items.items()]

    # Find positions of each section marker
    positions: dict[str, int] = {}
    for key, keywords in markers:
        for kw in keywords:
            idx = text_lower.find(kw)
            if idx != -1:
                positions[key] = idx
                break

    sorted_keys = sorted(positions.keys(), key=lambda k: positions[k])

    for i, key in enumerate(sorted_keys):
        start = positions[key]
        end = positions[sorted_keys[i + 1]] if i + 1 < len(sorted_keys) else len(text)
        section_text = text[start:end].strip()
        # Cap at ~50k chars to avoid blowing up memory
        sections[key] = section_text[:50000]

    # Fill missing with empty
    for key in target_items:
        if key not in sections:
            sections[key] = ""

    return sections
