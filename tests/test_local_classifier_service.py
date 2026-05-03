import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://localhost:11434/api/chat")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self):
        return self._body


@pytest.mark.asyncio
async def test_local_table_classifier_accepts_valid_json(monkeypatch):
    from services import local_classifier_service as svc

    async def fake_post(self, url, json):
        return _FakeResponse({
            "message": {
                "content": '{"tables":[{"table_id":"table_0","title":"Statements of Operations","category":"income_statement"}]}'
            }
        })

    monkeypatch.setattr(svc.httpx.AsyncClient, "post", fake_post)

    result = await svc.classify_tables([
        {"table_id": "table_0", "headers": ["Revenue", "Net income"], "sample_rows": [["10", "2"]]}
    ])

    assert result["table_0"]["category"] == "income_statement"
    assert result["table_0"]["title"] == "Statements of Operations"


@pytest.mark.asyncio
async def test_local_table_classifier_rejects_invalid_label(monkeypatch):
    from services import local_classifier_service as svc

    async def fake_post(self, url, json):
        return _FakeResponse({
            "message": {
                "content": '{"tables":[{"table_id":"table_0","title":"Bad","category":"made_up"}]}'
            }
        })

    monkeypatch.setattr(svc.httpx.AsyncClient, "post", fake_post)

    with pytest.raises(svc.LocalClassifierError):
        await svc.classify_tables([
            {"table_id": "table_0", "headers": ["Revenue"], "sample_rows": [["10"]]}
        ])


@pytest.mark.asyncio
async def test_local_classifier_rejects_invalid_json(monkeypatch):
    from services import local_classifier_service as svc

    async def fake_post(self, url, json):
        return _FakeResponse({"message": {"content": "not json"}})

    monkeypatch.setattr(svc.httpx.AsyncClient, "post", fake_post)

    with pytest.raises(svc.LocalClassifierError):
        await svc.classify_claim_confidence([
            {"claim_id": "claim_1", "text": "Revenue increased.", "supporting_excerpts": ["Revenue increased."]}
        ])


@pytest.mark.asyncio
async def test_secondary_judge_returns_none_when_disabled(monkeypatch):
    from services import local_classifier_service as svc

    monkeypatch.setattr(svc, "LOCAL_SECONDARY_JUDGE_ENABLED", False)

    assert await svc.secondary_judge({"answer_text": "test"}) is None
