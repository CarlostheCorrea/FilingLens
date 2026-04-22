"""
Human-in-the-Loop helpers: scope approval, claim verification, and question history.
"""

import json
import os
from datetime import datetime, timezone
from models import ScopeProposal, ApprovedScope
from config import DATA_DIR

_STATE_DIR = os.path.join(DATA_DIR, "scope_state")
os.makedirs(_STATE_DIR, exist_ok=True)


def save_proposal(proposal: ScopeProposal) -> None:
    path = os.path.join(_STATE_DIR, f"{proposal.proposal_id}.json")
    with open(path, "w") as f:
        json.dump(proposal.model_dump(), f)


def load_proposal(proposal_id: str) -> ScopeProposal | None:
    path = os.path.join(_STATE_DIR, f"{proposal_id}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return ScopeProposal(**json.load(f))


def save_approved_scope(approved: ApprovedScope) -> None:
    path = os.path.join(_STATE_DIR, f"{approved.proposal_id}_approved.json")
    with open(path, "w") as f:
        json.dump(approved.model_dump(), f)


def load_approved_scope(proposal_id: str) -> ApprovedScope | None:
    path = os.path.join(_STATE_DIR, f"{proposal_id}_approved.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return ApprovedScope(**json.load(f))


def save_answer(proposal_id: str, answer: dict, answer_key: str = "latest") -> None:
    path = os.path.join(_STATE_DIR, f"{proposal_id}_answer_{answer_key}.json")
    with open(path, "w") as f:
        json.dump(answer, f)


def load_answer(proposal_id: str, answer_key: str = "latest") -> dict | None:
    path = os.path.join(_STATE_DIR, f"{proposal_id}_answer_{answer_key}.json")
    if not os.path.exists(path):
        # Fallback to old single-answer format
        path = os.path.join(_STATE_DIR, f"{proposal_id}_answer.json")
        if not os.path.exists(path):
            return None
    with open(path) as f:
        return json.load(f)


def save_question(proposal_id: str, query: str, answer_key: str) -> None:
    history = load_history(proposal_id)
    # Don't duplicate
    existing_queries = [h["query"].strip().lower() for h in history]
    if query.strip().lower() in existing_queries:
        return
    history.append({
        "query": query,
        "answer_key": answer_key,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    path = os.path.join(_STATE_DIR, f"{proposal_id}_history.json")
    with open(path, "w") as f:
        json.dump(history, f)


def load_history(proposal_id: str) -> list[dict]:
    path = os.path.join(_STATE_DIR, f"{proposal_id}_history.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)
