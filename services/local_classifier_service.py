from __future__ import annotations

import json
import logging
from typing import Any

import httpx

import cost_tracker
import logging_utils
from config import (
    LOCAL_BUYER_OWNERSHIP_PROMPT,
    LOCAL_CHANGE_CARD_CLASSIFIER_PROMPT,
    LOCAL_CLAIM_CONFIDENCE_PROMPT,
    LOCAL_CLASSIFIER_ENABLED,
    LOCAL_CLASSIFIER_TIMEOUT_SECONDS,
    LOCAL_COMMERCIALIZATION_DIFFICULTY_PROMPT,
    LOCAL_PAIN_POINT_CLASSIFIER_PROMPT,
    LOCAL_SECONDARY_JUDGE_ENABLED,
    LOCAL_SECONDARY_JUDGE_PROMPT,
    LOCAL_TABLE_CLASSIFIER_PROMPT,
    LOCAL_URGENCY_PERSISTENCE_PROMPT,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
)

logger = logging.getLogger(__name__)


class LocalClassifierError(Exception):
    """Raised when a local classifier cannot produce validated JSON."""


TABLE_CATEGORIES = {
    "income_statement",
    "balance_sheet",
    "cash_flow",
    "segment",
    "equity_rollforward",
    "debt_schedule",
    "quarterly_summary",
    "other",
}
PAIN_CATEGORIES = {"operational", "regulatory", "supply_chain", "technology", "competitive", "financial"}
SEVERITY_VALUES = {"mild", "moderate", "severe"}
BUYER_OWNERS = {
    "operations",
    "IT",
    "finance",
    "compliance",
    "procurement",
    "distribution",
    "customer_success",
    "management",
    "unknown",
}
PERSISTENCE_VALUES = {"recurring", "worsening", "recent", "episodic", "shrinking", "unclear"}
CONFIDENCE_VALUES = {"high", "medium", "low"}
URGENCY_VALUES = {"high", "medium", "low"}
DIFFICULTY_VALUES = {"low", "medium", "high"}
CHANGE_CATEGORIES = {
    "new_risk_introduced",
    "risk_removed_or_deemphasized",
    "strategy_emphasis_increased",
    "capital_allocation_change",
    "pricing_or_margin_change",
    "guidance_or_outlook_change",
    "geographic_or_segment_shift",
    "market_positioning_change",
}
IMPORTANCE_VALUES = {"high", "medium", "low"}
OVERCLAIMING_VALUES = {"low", "medium", "high"}
VERDICT_VALUES = {"strong", "mixed", "weak"}


def _as_label(value: Any) -> str:
    return str(value or "").strip()


def _validate_choice(value: Any, allowed: set[str], field: str) -> str:
    label = _as_label(value)
    if label not in allowed:
        raise LocalClassifierError(f"Invalid {field}: {label!r}")
    return label


def _validate_int_score(value: Any, field: str) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError) as exc:
        raise LocalClassifierError(f"Invalid {field}: {value!r}") from exc
    if score < 1 or score > 5:
        raise LocalClassifierError(f"Invalid {field}: {value!r}")
    return score


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


async def _ollama_json(task: str, system_prompt: str, payload: dict) -> dict:
    if not LOCAL_CLASSIFIER_ENABLED:
        raise LocalClassifierError("Local classifiers are disabled")

    url = f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    request = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0},
    }

    try:
        async with httpx.AsyncClient(timeout=LOCAL_CLASSIFIER_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=request)
            response.raise_for_status()
            body = response.json()
    except Exception as exc:
        logging_utils.log_local_classifier(task, "error", OLLAMA_MODEL, str(exc))
        raise LocalClassifierError(f"Ollama request failed for {task}: {exc}") from exc

    content = ""
    if isinstance(body.get("message"), dict):
        content = body["message"].get("content") or ""
    if not content:
        content = body.get("response") or ""
    try:
        raw = json.loads(content or "{}")
    except json.JSONDecodeError as exc:
        logging_utils.log_local_classifier(task, "invalid_json", OLLAMA_MODEL, content[:500])
        raise LocalClassifierError(f"Ollama returned invalid JSON for {task}") from exc

    # Record token usage at $0 cost so the cost summary reflects Ollama's share
    # of work accurately. Ollama returns prompt_eval_count and eval_count in the
    # response body when stream=False.
    prompt_tokens     = body.get("prompt_eval_count", 0) or 0
    completion_tokens = body.get("eval_count", 0) or 0
    cost_tracker.record_ollama(prompt_tokens, completion_tokens)

    logging_utils.log_local_classifier(task, "ok", OLLAMA_MODEL, "")
    return raw


async def classify_tables(previews: list[dict]) -> dict[str, dict]:
    raw = await _ollama_json("table_classifier", LOCAL_TABLE_CLASSIFIER_PROMPT, {"tables": previews})
    tables: dict[str, dict] = {}
    for item in raw.get("tables", []):
        table_id = _as_label(item.get("table_id"))
        if not table_id:
            continue
        tables[table_id] = {
            "title": _as_label(item.get("title")),
            "category": _validate_choice(item.get("category", "other"), TABLE_CATEGORIES, "table category"),
        }
    if not tables and previews:
        raise LocalClassifierError("No table classifications returned")
    return tables


async def classify_pain_points(points: list[dict]) -> dict[int, dict]:
    raw = await _ollama_json("pain_point_classifier", LOCAL_PAIN_POINT_CLASSIFIER_PROMPT, {"pain_points": points})
    classified: dict[int, dict] = {}
    for item in raw.get("pain_points", []):
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError) as exc:
            raise LocalClassifierError(f"Invalid pain point index: {item.get('index')!r}") from exc
        classified[index] = {
            "category": _validate_choice(item.get("category"), PAIN_CATEGORIES, "pain category"),
            "severity": _validate_choice(item.get("severity"), SEVERITY_VALUES, "severity"),
            "buyer_owner_hint": _validate_choice(item.get("buyer_owner_hint"), BUYER_OWNERS, "buyer owner"),
            "recurrence_hint": _validate_choice(item.get("recurrence_hint"), PERSISTENCE_VALUES, "recurrence"),
            "confidence": _validate_choice(item.get("confidence"), CONFIDENCE_VALUES, "confidence"),
        }
    if len(classified) != len(points):
        raise LocalClassifierError("Pain point classifier did not classify every item")
    return classified


async def classify_buyer_ownership(payload: dict) -> dict:
    raw = await _ollama_json("buyer_ownership_classifier", LOCAL_BUYER_OWNERSHIP_PROMPT, payload)
    owners = [_validate_choice(owner, BUYER_OWNERS, "buyer owner") for owner in raw.get("buyer_owners", [])]
    primary = _validate_choice(raw.get("primary_buyer_owner", owners[0] if owners else "unknown"), BUYER_OWNERS, "primary buyer owner")
    if primary not in owners:
        owners = [primary] + owners
    return {
        "buyer_owners": owners[:3],
        "primary_buyer_owner": primary,
        "ownership_rationale": _as_label(raw.get("ownership_rationale")),
    }


async def classify_urgency_persistence(payload: dict) -> dict:
    raw = await _ollama_json("urgency_persistence_classifier", LOCAL_URGENCY_PERSISTENCE_PROMPT, payload)
    return {
        "urgency_level": _validate_choice(raw.get("urgency_level"), URGENCY_VALUES, "urgency"),
        "persistence_level": _validate_choice(raw.get("persistence_level"), PERSISTENCE_VALUES, "persistence"),
        "why_now": _as_label(raw.get("why_now")),
        "disconfirming_evidence": _list_of_strings(raw.get("disconfirming_evidence")),
    }


async def classify_commercialization_difficulty(payload: dict) -> dict:
    raw = await _ollama_json("commercialization_difficulty_classifier", LOCAL_COMMERCIALIZATION_DIFFICULTY_PROMPT, payload)
    return {
        "adoption_difficulty": _validate_choice(raw.get("adoption_difficulty"), DIFFICULTY_VALUES, "adoption difficulty"),
        "difficulty_rationale": _as_label(raw.get("difficulty_rationale")),
    }


async def classify_change_cards(changes: list[dict]) -> dict[str, dict]:
    raw = await _ollama_json("change_card_classifier", LOCAL_CHANGE_CARD_CLASSIFIER_PROMPT, {"changes": changes})
    classified: dict[str, dict] = {}
    for item in raw.get("changes", []):
        change_id = _as_label(item.get("change_id"))
        if not change_id:
            continue
        classified[change_id] = {
            "category": _validate_choice(item.get("category"), CHANGE_CATEGORIES, "change category"),
            "importance": _validate_choice(item.get("importance"), IMPORTANCE_VALUES, "importance"),
            "confidence": _validate_choice(item.get("confidence"), CONFIDENCE_VALUES, "confidence"),
        }
    if len(classified) != len(changes):
        raise LocalClassifierError("Change classifier did not classify every item")
    return classified


async def classify_claim_confidence(claims: list[dict]) -> dict[str, str]:
    raw = await _ollama_json("claim_confidence_classifier", LOCAL_CLAIM_CONFIDENCE_PROMPT, {"claims": claims})
    classified: dict[str, str] = {}
    for item in raw.get("claims", []):
        claim_id = _as_label(item.get("claim_id"))
        if not claim_id:
            continue
        classified[claim_id] = _validate_choice(item.get("confidence"), CONFIDENCE_VALUES, "claim confidence")
    if len(classified) != len(claims):
        raise LocalClassifierError("Claim confidence classifier did not classify every item")
    return classified


async def secondary_judge(payload: dict) -> dict | None:
    if not LOCAL_SECONDARY_JUDGE_ENABLED:
        return None
    raw = await _ollama_json("secondary_judge", LOCAL_SECONDARY_JUDGE_PROMPT, payload)
    return {
        "helpfulness": _validate_int_score(raw.get("helpfulness"), "helpfulness"),
        "clarity": _validate_int_score(raw.get("clarity"), "clarity"),
        "grounding": _validate_int_score(raw.get("grounding"), "grounding"),
        "citation_quality": _validate_int_score(raw.get("citation_quality"), "citation_quality"),
        "overclaiming_risk": _validate_choice(raw.get("overclaiming_risk"), OVERCLAIMING_VALUES, "overclaiming risk"),
        "overall_verdict": _validate_choice(raw.get("overall_verdict"), VERDICT_VALUES, "overall verdict"),
        "summary": _as_label(raw.get("summary")),
        "strengths": _list_of_strings(raw.get("strengths")),
        "concerns": _list_of_strings(raw.get("concerns")),
    }
