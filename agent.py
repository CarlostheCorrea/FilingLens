"""
Scope proposal and answer generation using GPT-4o with tool calling.
The agent uses MCP tools via mcp_client.py — never calls edgar_client directly.
"""

import json
import uuid
from openai import AsyncOpenAI
from config import OPENAI_API_KEY, OPENAI_MODEL, SCOPE_PROPOSAL_SYSTEM_PROMPT, ANSWERING_SYSTEM_PROMPT, MARKET_GAP_SCOPE_SYSTEM_PROMPT
from mcp_client import get_mcp_client
from models import ScopeProposal, Company, AnswerResponse, Claim
import rag_pipeline

_openai = AsyncOpenAI(api_key=OPENAI_API_KEY)

SCOPE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_companies_by_sector",
            "description": "List public companies in a sector by SIC code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sic_code": {"type": "string", "description": "4-digit SIC code"},
                },
                "required": ["sic_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_company",
            "description": "Search for a company by name or keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_recent_filings_for_company",
            "description": "List recent filing metadata for a company to determine which SEC form types it actually uses.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cik": {"type": "string"},
                    "limit": {"type": "integer", "default": 6},
                },
                "required": ["cik"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_ticker_to_cik",
            "description": "Resolve a stock ticker to company metadata including CIK.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                },
                "required": ["ticker"],
            },
        },
    },
]


async def _run_tool(name: str, args: dict) -> str:
    mcp = get_mcp_client()
    if name == "list_companies_by_sector":
        result = await mcp.list_companies_by_sector(args["sic_code"])
    elif name == "search_company":
        result = await mcp.search_company(args["query"])
    elif name == "list_recent_filings_for_company":
        result = await mcp.list_recent_filings_for_company(
            args["cik"],
            limit=args.get("limit", 6),
        )
    elif name == "resolve_ticker_to_cik":
        result = await mcp.resolve_ticker_to_cik(args["ticker"])
    else:
        result = {"error": f"Unknown tool: {name}"}
    return json.dumps(result)


async def _normalize_form_types(
    companies: list[Company],
    proposed_form_types: list[str],
) -> list[str]:
    mcp = get_mcp_client()
    normalized: list[str] = []

    def add_form(form_type: str) -> None:
        form = str(form_type or "").strip().upper()
        if form and form not in normalized:
            normalized.append(form)

    for form_type in proposed_form_types or ["10-K"]:
        add_form(form_type)

    supported_forms = {"10-K", "10-Q", "8-K", "20-F", "6-K"}
    discovered_forms: set[str] = set()

    for company in companies:
        if not company.cik:
            continue
        recent_filings = await mcp.list_recent_filings_for_company(company.cik, limit=6)
        for filing in recent_filings:
            form_type = str(filing.get("form_type", "")).strip().upper()
            if form_type in supported_forms:
                discovered_forms.add(form_type)

    if {"20-F", "6-K"} & discovered_forms:
        add_form("20-F")
        add_form("6-K")

    return normalized


async def propose_scope(query: str) -> ScopeProposal:
    messages = [
        {"role": "system", "content": SCOPE_PROPOSAL_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"{query}\n\n"
                "Use the tools to discover relevant companies, then return a JSON scope proposal "
                "with fields: companies (list of {ticker, name, cik, rationale}), "
                "form_types, date_range ([start, end] as YYYY-MM-DD), overall_rationale."
            ),
        },
    ]

    max_rounds = 6
    for _ in range(max_rounds):
        response = await _openai.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            tools=SCOPE_TOOLS,
            tool_choice="auto",
            response_format={"type": "json_object"},
        )

        msg = response.choices[0].message
        messages.append(msg)

        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_result = await _run_tool(tc.function.name, json.loads(tc.function.arguments))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })
        else:
            raw = json.loads(msg.content or "{}")
            companies = [
                Company(
                    ticker=c.get("ticker", ""),
                    name=c.get("name", ""),
                    cik=str(c.get("cik", "")),
                    rationale=c.get("rationale", ""),
                )
                for c in raw.get("companies", [])
            ]
            proposal_id = f"scope_{uuid.uuid4().hex[:8]}"
            return ScopeProposal(
                proposal_id=proposal_id,
                companies=companies,
                form_types=await _normalize_form_types(
                    companies,
                    raw.get("form_types", ["10-K"]),
                ),
                date_range=raw.get("date_range", ["2022-01-01", "2025-12-31"]),
                overall_rationale=raw.get("overall_rationale", ""),
            )

    # Fallback if loop exhausted
    return ScopeProposal(
        proposal_id=f"scope_{uuid.uuid4().hex[:8]}",
        companies=[],
        form_types=["10-K"],
        date_range=["2022-01-01", "2025-12-31"],
        overall_rationale="Scope proposal could not be completed after tool calls.",
    )


async def propose_gap_scope(query: str) -> ScopeProposal:
    """Scope proposal optimised for market gap discovery: broader coverage, annual filings."""
    messages = [
        {"role": "system", "content": MARKET_GAP_SCOPE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"{query}\n\n"
                "Use the tools to discover a representative cross-section of companies in this industry, "
                "then return a JSON scope proposal with fields: companies (list of {ticker, name, cik, rationale}), "
                "form_types, date_range ([start, end] as YYYY-MM-DD), overall_rationale."
            ),
        },
    ]

    max_rounds = 6
    for _ in range(max_rounds):
        response = await _openai.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            tools=SCOPE_TOOLS,
            tool_choice="auto",
            response_format={"type": "json_object"},
        )

        msg = response.choices[0].message
        messages.append(msg)

        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_result = await _run_tool(tc.function.name, json.loads(tc.function.arguments))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })
        else:
            raw = json.loads(msg.content or "{}")
            companies = [
                Company(
                    ticker=c.get("ticker", ""),
                    name=c.get("name", ""),
                    cik=str(c.get("cik", "")),
                    rationale=c.get("rationale", ""),
                )
                for c in raw.get("companies", [])
            ]
            proposal_id = f"gap_scope_{uuid.uuid4().hex[:8]}"
            return ScopeProposal(
                proposal_id=proposal_id,
                companies=companies,
                form_types=raw.get("form_types", ["10-K", "20-F"]),
                date_range=raw.get("date_range", ["2022-01-01", "2025-12-31"]),
                overall_rationale=raw.get("overall_rationale", ""),
            )

    return ScopeProposal(
        proposal_id=f"gap_scope_{uuid.uuid4().hex[:8]}",
        companies=[],
        form_types=["10-K", "20-F"],
        date_range=["2022-01-01", "2025-12-31"],
        overall_rationale="Scope proposal could not be completed after tool calls.",
    )


async def generate_answer(
    proposal_id: str,
    query: str,
    tickers: list[str] | None = None,
) -> AnswerResponse:
    chunks = rag_pipeline.retrieve(query, k=16, tickers=tickers)

    if not chunks:
        return AnswerResponse(
            proposal_id=proposal_id,
            query=query,
            claims=[],
            gaps=["No relevant chunks were found in the ingested filings for this query."],
        )

    context_parts = []
    for c in chunks:
        context_parts.append(
            f"[chunk_id: {c.chunk_id}]\n"
            f"Company: {c.metadata.company_name} ({c.metadata.company_ticker})\n"
            f"Filing: {c.metadata.form_type} {c.metadata.filing_date}\n"
            f"Section: {c.metadata.item_section}\n"
            f"Text:\n{c.text}\n"
        )

    context = "\n---\n".join(context_parts)

    messages = [
        {"role": "system", "content": ANSWERING_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Research question: {query}\n\n"
                f"Retrieved filing excerpts:\n\n{context}"
            ),
        },
    ]

    response = await _openai.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages,
        response_format={"type": "json_object"},
    )

    raw = json.loads(response.choices[0].message.content or "{}")
    claims = [
        Claim(
            claim_id=c.get("claim_id", f"claim_{i:03d}"),
            text=c.get("text", ""),
            supporting_chunk_ids=c.get("supporting_chunk_ids", []),
            confidence=c.get("confidence", "medium"),
        )
        for i, c in enumerate(raw.get("claims", []))
    ]

    return AnswerResponse(
        proposal_id=proposal_id,
        query=query,
        claims=claims,
        gaps=raw.get("gaps", []),
    )
