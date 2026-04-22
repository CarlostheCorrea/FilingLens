"""
Backend client for calling MCP server tools.
Runs the MCP server as a subprocess and communicates via stdio.
"""

import json
import asyncio
import subprocess
import sys
import os
from typing import Any


class MCPClient:
    def __init__(self):
        self._server_path = os.path.join(os.path.dirname(__file__), "mcp_server.py")

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Call an MCP tool via the standalone server process."""
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, self._server_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Build JSON-RPC initialize + call sequence
            initialize_msg = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "filing-lens-client", "version": "1.0"},
                },
            }
            call_msg = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }
            initialized_notification = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }

            input_data = (
                json.dumps(initialize_msg) + "\n"
                + json.dumps(initialized_notification) + "\n"
                + json.dumps(call_msg) + "\n"
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input_data.encode()),
                timeout=60,
            )

            lines = stdout.decode().strip().split("\n")
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if msg.get("id") == 2:
                        result = msg.get("result", {})
                        content = result.get("content", [])
                        if content:
                            return json.loads(content[0]["text"])
                        return result
                except json.JSONDecodeError:
                    continue

            return {"error": "No valid response from MCP server"}
        except asyncio.TimeoutError:
            return {"error": "MCP server timed out"}
        except Exception as e:
            return {"error": str(e)}

    # Convenience wrappers matching the MCP tool names

    async def list_companies_by_sector(self, sic_code: str) -> list[dict]:
        result = await self.call_tool("list_companies_by_sector", {"sic_code": sic_code})
        return result if isinstance(result, list) else []

    async def search_company(self, query: str) -> list[dict]:
        result = await self.call_tool("search_company", {"query": query})
        return result if isinstance(result, list) else []

    async def list_filings(
        self, cik: str, form_types: list[str], since_date: str, until_date: str
    ) -> list[dict]:
        result = await self.call_tool("list_filings", {
            "cik": cik,
            "form_types": form_types,
            "since_date": since_date,
            "until_date": until_date,
        })
        return result if isinstance(result, list) else []

    async def fetch_filing(self, accession_number: str, cik: str | None = None) -> dict:
        args = {"accession_number": accession_number}
        if cik:
            args["cik"] = cik
        return await self.call_tool("fetch_filing", args)

    async def resolve_ticker_to_cik(self, ticker: str) -> dict:
        result = await self.call_tool("resolve_ticker_to_cik", {"ticker": ticker})
        return result if isinstance(result, dict) else {}


_client: MCPClient | None = None


def get_mcp_client() -> MCPClient:
    global _client
    if _client is None:
        _client = MCPClient()
    return _client
