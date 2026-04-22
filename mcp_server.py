#!/usr/bin/env python3
"""
Standalone MCP server exposing SEC EDGAR tools.
Run with: python mcp_server.py
"""

import asyncio
import json
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

import edgar_client

app = Server("edgar-mcp-server")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_companies_by_sector",
            description="List public companies in a sector by SIC code (up to 50).",
            inputSchema={
                "type": "object",
                "properties": {
                    "sic_code": {"type": "string", "description": "4-digit SIC code, e.g. '3674' for semiconductors"},
                },
                "required": ["sic_code"],
            },
        ),
        types.Tool(
            name="search_company",
            description="Search for companies by name or keyword.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Company name or keyword"},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="list_filings",
            description="List SEC filings for a company by CIK within a date range.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cik": {"type": "string"},
                    "form_types": {"type": "array", "items": {"type": "string"}},
                    "since_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "until_date": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["cik", "form_types", "since_date", "until_date"],
            },
        ),
        types.Tool(
            name="fetch_filing",
            description="Fetch and parse a filing by accession number. Returns metadata and key sections.",
            inputSchema={
                "type": "object",
                "properties": {
                    "accession_number": {"type": "string"},
                    "cik": {"type": "string", "description": "Company CIK (optional but improves reliability when filing agent submitted)"},
                },
                "required": ["accession_number"],
            },
        ),
        types.Tool(
            name="fetch_filing_section",
            description="Fetch a specific section (item_1, item_1a, item_7, item_7a) of a filing.",
            inputSchema={
                "type": "object",
                "properties": {
                    "accession_number": {"type": "string"},
                    "section_name": {"type": "string", "description": "e.g. 'item_1a'"},
                },
                "required": ["accession_number", "section_name"],
            },
        ),
        types.Tool(
            name="list_recent_filings_for_company",
            description="List the most recent filings for a company by CIK.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cik": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["cik"],
            },
        ),
        types.Tool(
            name="resolve_ticker_to_cik",
            description="Resolve a stock ticker to a company CIK and metadata.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                },
                "required": ["ticker"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "list_companies_by_sector":
            result = edgar_client.search_companies_by_sic(arguments["sic_code"])
        elif name == "search_company":
            result = edgar_client.search_company_by_name(arguments["query"])
        elif name == "list_filings":
            result = edgar_client.list_filings(
                cik=arguments["cik"],
                form_types=arguments["form_types"],
                since_date=arguments["since_date"],
                until_date=arguments["until_date"],
            )
        elif name == "fetch_filing":
            result = edgar_client.fetch_filing_text(
                arguments["accession_number"],
                cik=arguments.get("cik"),
            )
        elif name == "fetch_filing_section":
            filing = edgar_client.fetch_filing_text(arguments["accession_number"])
            section = arguments["section_name"]
            result = {
                "metadata": filing.get("metadata", {}),
                "section_name": section,
                "text": filing.get("sections", {}).get(section, ""),
            }
        elif name == "list_recent_filings_for_company":
            limit = arguments.get("limit", 10)
            result = edgar_client.list_filings(
                cik=arguments["cik"],
                form_types=["10-K", "10-Q", "8-K", "20-F", "6-K"],
                since_date="2022-01-01",
                until_date="2026-12-31",
            )[:limit]
        elif name == "resolve_ticker_to_cik":
            result = edgar_client.resolve_ticker_to_cik(arguments["ticker"])
        else:
            result = {"error": f"Unknown tool: {name}"}
    except Exception as e:
        result = {"error": str(e)}

    return [types.TextContent(type="text", text=json.dumps(result))]


async def main():
    async with stdio_server() as streams:
        await app.run(streams[0], streams[1], app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
