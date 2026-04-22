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
    """
    Two-step EDGAR lookup:
    1. Atom feed → CIKs for the given SIC code
    2. data.sec.gov submissions JSON → company name + ticker per CIK
    """
    import httpx
    import xml.etree.ElementTree as ET

    headers = {"User-Agent": EDGAR_IDENTITY}

    # Step 1: Collect CIKs from EDGAR atom feed
    try:
        resp = httpx.get(
            "https://www.sec.gov/cgi-bin/browse-edgar",
            params={
                "action": "getcompany",
                "SIC": sic_code,
                "type": "10-K",
                "dateb": "",
                "owner": "include",
                "count": "40",
                "search_text": "",
                "output": "atom",
            },
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)

        ciks = []
        for entry in root.iter():
            tag = entry.tag.split("}")[-1] if "}" in entry.tag else entry.tag
            if tag != "entry":
                continue
            for child in entry.iter():
                ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if ctag == "cik" and child.text:
                    cik = child.text.strip().lstrip("0") or "0"
                    if cik not in ciks:
                        ciks.append(cik)
                    break
    except Exception:
        return []

    if not ciks:
        return []

    # Step 2: Resolve each CIK → name + ticker via data.sec.gov
    results = []
    with httpx.Client(headers=headers, timeout=10) as client:
        for cik in ciks[:25]:
            try:
                data = client.get(
                    f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
                ).json()
                name = data.get("name", "")
                tickers = data.get("tickers", [])
                if name:
                    results.append({
                        "ticker": tickers[0] if tickers else "",
                        "name": name,
                        "cik": cik,
                        "sic": sic_code,
                    })
            except Exception:
                pass

    return results


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


def fetch_filing_text(accession_number: str, cik: str | None = None) -> dict:
    cache_path = os.path.join(FILINGS_CACHE_DIR, f"{accession_number.replace('/', '_')}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    try:
        filing, company = _get_filing_by_accession(accession_number, cik=cik)
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


def _get_filing_by_accession(accession_number: str, cik: str | None = None):
    """
    Resolve a filing object by accession number.
    Prefer the supplied CIK (from the company that owns the filing) over
    parsing it from the accession prefix — filing agents submit on behalf of
    companies, so the prefix CIK is often the agent's, not the company's.
    """
    normalized_target = accession_number.replace("-", "").replace("/", "").lower()

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

    def _search_company_filings(cik_str: str):
        try:
            cik_clean = str(int(cik_str))
            company = edgar.Company(cik_clean)
            filings = company.get_filings()
            for f in filings:
                acc = _safe_str(getattr(f, "accession_no", ""))
                if acc.replace("-", "").lower() == normalized_target:
                    return f, company
        except Exception:
            pass
        return None, None

    # 1. Use the provided company CIK (most reliable)
    if cik:
        filing, company = _search_company_filings(cik)
        if filing is not None:
            return filing, company

    # 2. Fall back to parsing the CIK from the accession number prefix
    try:
        prefix = accession_number.replace("/", "-").split("-")[0]
        if prefix != (cik or "").lstrip("0"):
            filing, company = _search_company_filings(prefix)
            if filing is not None:
                return filing, company
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
