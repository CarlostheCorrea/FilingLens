import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.mark.asyncio
async def test_build_xbrl_context_uses_mcp_client(monkeypatch):
    from services import xbrl_context_service as svc

    calls = []

    class FakeMCP:
        async def get_xbrl_facts(self, cik):
            calls.append(cik)
            return {
                "facts": {
                    "Revenues": {
                        "label": "Revenue",
                        "unit": "USD",
                        "category": "income_statement",
                        "facts": [
                            {"period_end": "2025-12-31", "value": 1200000000},
                            {"period_end": "2024-12-31", "value": 1000000000},
                        ],
                    }
                }
            }

    monkeypatch.setattr(svc, "get_mcp_client", lambda: FakeMCP())

    result = await svc.build_xbrl_context(
        [{"ticker": "STZ", "name": "Constellation Brands", "cik": "0016940"}]
    )

    assert calls == ["0016940"]
    assert "KEY FINANCIAL METRICS" in result
    assert "Constellation Brands (STZ)" in result
    assert "Revenue" in result

