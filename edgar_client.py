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
from config import EDGAR_IDENTITY, FILINGS_CACHE_DIR, SECTION_CHAR_LIMIT

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


def _resolve_ticker_via_sec_api(ticker: str) -> dict:
    """Fallback: look up ticker→CIK from SEC's company_tickers.json."""
    try:
        resp = httpx.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": EDGAR_IDENTITY},
            timeout=10,
        )
        resp.raise_for_status()
        ticker_upper = ticker.strip().upper()
        for entry in resp.json().values():
            if str(entry.get("ticker", "")).upper() == ticker_upper:
                cik = str(entry["cik_str"])
                name = entry.get("title", ticker_upper)
                # Fetch SIC from submissions endpoint
                sic = ""
                try:
                    sub = httpx.get(
                        f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json",
                        headers={"User-Agent": EDGAR_IDENTITY},
                        timeout=10,
                    ).json()
                    sic = str(sub.get("sic", ""))
                except Exception:
                    pass
                return {"ticker": ticker_upper, "name": name, "cik": cik, "sic": sic}
    except Exception:
        pass
    return {}


def resolve_ticker_to_cik(ticker: str) -> dict:
    try:
        company = edgar.Company(ticker)
        result = {
            "ticker": ticker.upper(),
            "name": _safe_str(getattr(company, "name", "")),
            "cik": _safe_str(getattr(company, "cik", "")),
            "sic": _safe_str(getattr(company, "sic", "")),
        }
        if result["cik"]:
            return result
    except Exception:
        pass
    return _resolve_ticker_via_sec_api(ticker)


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
                "acceptance_datetime": _filing_acceptance_datetime(f),
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

        form_type_raw = _safe_str(getattr(filing, "form", ""))
        metadata = {
            "ticker": ticker,
            "company_name": _safe_str(getattr(company, "name", "")),
            "cik": _safe_str(getattr(company, "cik", "")),
            "accession_number": accession_number,
            "form_type": form_type_raw,
            "filing_date": _safe_str(getattr(filing, "filing_date", "")),
            "acceptance_datetime": _filing_acceptance_datetime(filing),
        }

        sections = _extract_sections(filing, form_type=form_type_raw)

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


_SECTION_TARGETS = {
    # 10-K annual report
    "10-K": {
        "item_1":  ["item 1.", "item1.", "business"],
        "item_1a": ["item 1a.", "item1a.", "risk factors"],
        "item_7":  ["item 7.", "item7.", "management"],
        "item_7a": ["item 7a.", "item7a.", "quantitative"],
    },
    # 10-Q quarterly report
    "10-Q": {
        "item_1": ["item 1.", "item1.", "financial statements"],
        "item_2": ["item 2.", "item2.", "management"],
        "item_3": ["item 3.", "item3.", "quantitative"],
    },
    # 20-F annual report for foreign private issuers
    # Item 4 = Information on the Company (business description, branding, marketing)
    # Item 5 = MD&A
    # Item 11 = Quantitative/Qualitative Market Risk
    "20-F": {
        "item_4":  ["item 4.", "item4.", "information on the company"],
        "item_4a": ["item 4a.", "item4a.", "unresolved staff comments"],
        "item_5":  ["item 5.", "item5.", "operating and financial"],
        "item_11": ["item 11.", "item11.", "quantitative and qualitative"],
    },
    # 6-K current report for foreign private issuers
    "6-K": {
        "item_1": ["item 1.", "item1.", "report"],
    },
    # 8-K current report
    "8-K": {
        "item_1": ["item 1.", "item1.", "entry into"],
        "item_2": ["item 2.", "item2.", "results of operations"],
        "item_7": ["item 7.", "item7.", "financial statements"],
        "item_9": ["item 9.", "item9.", "regulation fd"],
    },
}


def _target_items_for_form(form_type: str) -> dict:
    form_upper = (form_type or "").upper().strip()
    for key in _SECTION_TARGETS:
        if key in form_upper:
            return _SECTION_TARGETS[key]
    return _SECTION_TARGETS["10-K"]


def _extract_sections(filing, form_type: str = "") -> dict:
    target_items = _target_items_for_form(form_type)
    sections = {}

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
                sections = _parse_sections_from_text(
                    _strip_html(html), target_items
                )
        except Exception:
            pass

    if not any(sections.values()):
        try:
            txt = filing.text() if hasattr(filing, "text") else ""
            sections = _parse_sections_from_text(
                _strip_html(txt), target_items
            )
        except Exception:
            pass

    return sections


def _filing_acceptance_datetime(filing) -> str:
    for attr in ("acceptance_datetime", "accepted", "accepted_datetime", "filing_datetime"):
        value = getattr(filing, attr, None)
        if value:
            return _safe_str(value)
    return ""


def _strip_html(raw: str) -> str:
    """
    Convert HTML to plain text while preserving block-level structure.
    Block-level tags (p, div, br, h1-h6, tr, li) become newlines so that
    section headers — which are their own block — appear on their own line.
    Inline tags become spaces. HTML entities are decoded.
    Non-breaking spaces become regular spaces.
    """
    import re
    import html as html_lib

    # Block tags → newline
    raw = re.sub(
        r"</?(?:p|div|br|h[1-6]|tr|li|section|article|header|footer|table|thead|tbody)[^>]*>",
        "\n", raw, flags=re.IGNORECASE,
    )
    raw = re.sub(r"<[^>]+>", " ", raw)          # remaining tags → space
    raw = html_lib.unescape(raw)                 # &amp; &#160; etc.
    raw = re.sub(r"[\xa0\u00a0\u2009\u200a]+", " ", raw)  # NBSP variants
    raw = re.sub(r"[ \t]+", " ", raw)            # collapse horizontal whitespace
    raw = re.sub(r"\n{3,}", "\n\n", raw)         # max double newline
    return raw.strip()


def _all_occurrences(text: str, text_lower: str, keywords: list[str]) -> list[int]:
    """
    Return all positions where any keyword appears at the START of a line
    (preceded by a newline or the document start, with optional leading spaces).
    Falls back to any occurrence if no line-start match is found.
    """
    import re
    for kw in keywords:
        # Try line-anchored: newline + optional spaces + keyword (case-insensitive)
        pattern = re.compile(r"(?:^|\n) *" + re.escape(kw), re.IGNORECASE)
        positions = [m.start() for m in pattern.finditer(text)]
        # Adjust to the keyword start (skip the newline + spaces)
        adjusted = []
        for p in positions:
            # Find the actual keyword start within the match
            match_text = text[p:p + len(kw) + 5].lower()
            offset = match_text.find(kw.lower())
            if offset != -1:
                adjusted.append(p + offset)
        if adjusted:
            return adjusted
    # Fallback: any occurrence
    for kw in keywords:
        positions = []
        start = 0
        while True:
            idx = text_lower.find(kw, start)
            if idx == -1:
                break
            positions.append(idx)
            start = idx + 1
        if positions:
            return positions
    return []


def _parse_sections_from_text(text: str, target_items: dict) -> dict:
    """
    Extract named sections from a filing's raw text.

    SEC filings have a Table of Contents near the top AND cross-references
    scattered throughout the body — so each item keyword (e.g. "item 4.")
    can appear dozens of times.  A naive .find() picks the TOC entry (tiny
    span).  A "max total span" heuristic picks a cross-reference inside
    another section whose body has no other markers for a while.

    Correct strategy: for each item key, measure the span from each
    candidate occurrence to the nearest occurrence of a *different* item
    key.  Cross-references within a section body say things like
    "see Item 4.A." while still inside Item 5 — those "item 4." hits are
    followed very quickly by the next "item 4." or "item 5." reference.
    The real section header is followed by thousands of chars before any
    OTHER item's header appears.
    """
    sections: dict[str, str] = {}
    text_lower = text.lower()

    # Collect all positions per key (line-anchored preferred)
    all_occ: dict[str, list[int]] = {
        key: _all_occurrences(text, text_lower, kws)
        for key, kws in target_items.items()
    }

    def _next_different_marker(pos: int, current_key: str) -> int:
        """Nearest position of any item OTHER than current_key that comes after pos."""
        nearest = len(text)
        for other_key, occ_list in all_occ.items():
            if other_key == current_key:
                continue
            for p in occ_list:
                if pos < p < nearest:
                    nearest = p
        return nearest

    # For each key, pick the occurrence with the longest span to the
    # nearest OTHER-item marker.  That is the actual section body.
    best_positions: dict[str, int] = {}
    for key, occ_list in all_occ.items():
        if not occ_list:
            continue
        best_pos = occ_list[0]
        best_span = 0
        for pos in occ_list:
            span = _next_different_marker(pos, key) - pos
            if span > best_span:
                best_span = span
                best_pos = pos
        if best_span >= 200:
            best_positions[key] = best_pos

    sorted_keys = sorted(best_positions.keys(), key=lambda k: best_positions[k])

    for i, key in enumerate(sorted_keys):
        start = best_positions[key]
        end = best_positions[sorted_keys[i + 1]] if i + 1 < len(sorted_keys) else len(text)
        sections[key] = text[start:end].strip()[:SECTION_CHAR_LIMIT]

    for key in target_items:
        if key not in sections:
            sections[key] = ""

    return sections
