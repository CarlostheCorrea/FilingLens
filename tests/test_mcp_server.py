import pytest
import asyncio
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.mark.asyncio
async def test_mcp_client_search_company():
    from mcp_client import MCPClient
    client = MCPClient()
    result = await client.search_company("NVIDIA")
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_mcp_client_resolve_ticker():
    from mcp_client import MCPClient
    client = MCPClient()
    result = await client.resolve_ticker_to_cik("NVDA")
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_mcp_client_list_companies_by_sector():
    from mcp_client import MCPClient
    client = MCPClient()
    result = await client.list_companies_by_sector("3674")
    assert isinstance(result, list)
