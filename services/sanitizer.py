"""
Input sanitization for prompt injection protection.

All user-supplied text passes through here before reaching any LLM call.
Three layers of defence:

1. sanitize_query   — research questions, change lenses, comparison questions
2. validate_ticker  — strict format check on all ticker symbols
3. wrap_filing_content — structural XML delimiters around SEC filing text so
                         the LLM treats it as external data, not instructions
"""

from __future__ import annotations

import re
import unicodedata

# ── Constants ─────────────────────────────────────────────────────────────────

QUERY_MAX_LENGTH = 2_000
TICKER_MAX_LENGTH = 10

# ── Injection pattern detection ────────────────────────────────────────────────
#
# These are unambiguous prompt-injection phrases. We raise rather than silently
# strip so the user gets a clear error and can rephrase legitimately.

_INJECTION_PATTERNS = re.compile(
    r"("
    r"ignore\s+(all\s+)?previous\s+(instructions?|prompts?|context|directives?)|"
    r"disregard\s+(all\s+)?previous\s+(instructions?|prompts?|context)|"
    r"forget\s+(everything|all\s+previous|prior\s+instructions?)|"
    r"you\s+are\s+now\s+(?:a\s+)?(?:an?\s+)?\w+|"
    r"act\s+as\s+(?:if\s+you\s+(?:are|were)\s+)?(?:a\s+)?|"
    r"pretend\s+(?:you\s+are|to\s+be)\s+|"
    r"your\s+new\s+(?:role|instructions?|system\s+prompt)\s+is|"
    r"new\s+instructions?:|"
    r"revised?\s+instructions?:|"
    r"system\s*prompt\s*:|"
    r"###\s*(?:system|instruction|override)|"
    r"\[INST\]|"
    r"\[\/INST\]|"
    r"<\|system\|>|"
    r"<\|user\|>|"
    r"<\|assistant\|>|"
    r"<\|im_start\|>|"
    r"<\|im_end\|>"
    r")",
    re.IGNORECASE | re.DOTALL,
)

# Valid ticker: 1-5 uppercase letters, optionally followed by . or - and 1-2 more letters
# Covers: AAPL, BRK.A, BF.B, GOOGL, 2222 (excluded intentionally — numeric-only tickers
# are not supported by the EDGAR lookup tools in this project)
_TICKER_RE = re.compile(r"^[A-Z]{1,5}([.\-][A-Z]{1,2})?$")


# ── Public API ─────────────────────────────────────────────────────────────────

def _normalize_for_matching(text: str) -> tuple[str, str]:
    """
    Produce two flattened forms of the text used only for injection detection.

    Returns (spaced, joined) where:
    - spaced: non-alphabetic chars → space  (catches inter-word insertion: IGNORE\\x00previous)
    - joined: non-alphabetic chars → removed (catches intra-word insertion: ign.ore previous)

    Running the injection pattern against both forms closes the gap where
    attackers insert characters either between keywords or within a keyword.
    """
    # Replace literal typed escape sequences first (\x00, ​, \n etc.)
    cleaned = re.sub(r"\\[xuU][0-9a-fA-F]{1,8}", " ", text)
    cleaned = re.sub(r"\\[nrtbf0]", " ", cleaned)

    # spaced: non-alpha → space (preserves word boundaries for inter-word evasion)
    spaced = re.sub(r"[^a-zA-Z\s]", " ", cleaned)
    spaced = re.sub(r"\s+", " ", spaced).strip()

    # joined: non-alpha → removed (collapses intra-word punctuation like ign.ore → ignore)
    joined = re.sub(r"[^a-zA-Z\s]", "", cleaned)
    joined = re.sub(r"\s+", " ", joined).strip()

    return spaced, joined


def sanitize_query(query: str, max_length: int = QUERY_MAX_LENGTH) -> str:
    """
    Sanitize a user research query before it reaches any LLM prompt.

    Steps applied in order:
    - Reject empty input
    - Normalize unicode to NFKC (neutralises zero-width chars and homoglyphs)
    - Strip null bytes and non-printable control characters
    - Truncate to max_length (before injection check to avoid regex DoS)
    - Run injection detection against a flattened version of the text so that
      character-insertion evasion techniques (\\x00, zero-width spaces, etc.)
      cannot bypass the pattern check
    - Raise ValueError on detection; return cleaned original text otherwise

    Returns the cleaned query string.
    Raises ValueError for empty input or detected injection.
    """
    if not query or not query.strip():
        raise ValueError("Query cannot be empty.")

    # Normalize unicode — catches zero-width space, homoglyph evasion, etc.
    text = unicodedata.normalize("NFKC", query)

    # Replace actual null bytes and control characters with a space (keep \t, \n, \r).
    # Using space rather than empty string prevents words from joining when a null
    # byte is inserted between them as an evasion technique (IGNORE\x00previous →
    # "IGNORE previous" rather than "IGNOREprevious").
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)

    # Enforce length before injection regex to avoid catastrophic backtracking
    text = text[:max_length]

    # Run injection check against both flattened forms:
    # - spaced catches inter-word evasion (IGNORE\x00previous → "IGNORE previous")
    # - joined catches intra-word evasion (ign.ore previous → "ignore previous")
    flat_spaced, flat_joined = _normalize_for_matching(text)
    for flat in (flat_spaced, flat_joined):
        match = _INJECTION_PATTERNS.search(flat)
        if match:
            snippet = match.group(0)[:50].replace("\n", " ")
            raise ValueError(
                f"Query contains a disallowed pattern: '{snippet}'. "
                "Please rephrase your research question."
            )

    return text.strip()


def validate_ticker(ticker: str) -> str:
    """
    Validate and normalise a ticker symbol.

    Returns the uppercased ticker.
    Raises ValueError if the format is invalid.
    """
    if not ticker or not ticker.strip():
        raise ValueError("Ticker cannot be empty.")

    normalised = ticker.strip().upper()

    if len(normalised) > TICKER_MAX_LENGTH:
        raise ValueError(
            f"'{normalised}' exceeds the maximum ticker length of {TICKER_MAX_LENGTH} characters."
        )

    if not _TICKER_RE.match(normalised):
        raise ValueError(
            f"'{normalised}' is not a valid ticker symbol. "
            "Expected 1–5 letters, optionally followed by a dot or dash and 1–2 letters "
            "(e.g. AAPL, BRK.A, BF.B)."
        )

    return normalised


def wrap_filing_content(text: str) -> str:
    """
    Wrap SEC filing text in structural XML delimiters.

    This signals to the LLM that the enclosed text is external source material
    to be analysed, not instructions to follow. Mitigates indirect prompt
    injection from adversarial content embedded in SEC filings.
    """
    return f"<filing_content>\n{text}\n</filing_content>"


def sanitize_short_text(text: str, max_length: int = 500, field_name: str = "field") -> str:
    """
    Light sanitizer for short free-text fields (chat questions, company descriptions).
    Applies unicode normalization and control-character removal but does NOT
    run injection detection — suitable for fields that legitimately contain
    partial sentences or informal language.
    """
    if not text or not text.strip():
        raise ValueError(f"{field_name} cannot be empty.")

    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text[:max_length].strip()
