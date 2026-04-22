import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
EDGAR_IDENTITY = os.getenv("EDGAR_IDENTITY", "FilingLens user filinglens@example.com")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
FILINGS_CACHE_DIR = os.path.join(DATA_DIR, "filings_cache")
CHROMA_DIR = os.path.join(DATA_DIR, "chroma")
LOGS_DIR = os.path.join(DATA_DIR, "logs")

CHUNK_SIZE_TOKENS = 800
CHUNK_OVERLAP_TOKENS = 100
RETRIEVAL_K = 8

SCOPE_PROPOSAL_SYSTEM_PROMPT = """You are a SEC filings research assistant. When the user asks a research question,
propose a SCOPE for analysis — the set of companies and filings that should be
retrieved to answer the question well.

Use the tools available to you to discover companies. Do not fetch full
filing text yet — only propose the scope.

Return a JSON object with:
- companies: list of {ticker, name, cik, rationale}
- form_types: list of SEC form types (e.g., "10-K", "10-Q", "8-K")
- date_range: [start_date, end_date] in YYYY-MM-DD
- overall_rationale: 2-3 sentence explanation of your scoping decisions

The system must be able to handle broad analyses involving 20–50 companies and
up to 3 years of filings, but you should still propose a scope that is justified
by the user's question."""

ANSWERING_SYSTEM_PROMPT = """You are answering a research question using retrieved excerpts from SEC filings.

RULES:
1. Every factual claim MUST cite at least one retrieved chunk by its chunk_id.
2. If the retrieved chunks do not support a claim, say so explicitly.
3. Structure your answer as a list of discrete claims, each with citations.
4. Surface differences in company language when they exist.
5. Do not fabricate, generalize beyond the retrieved evidence, or imply certainty when support is weak.

Return JSON:
{
  "claims": [
    {
      "claim_id": "...",
      "text": "...",
      "supporting_chunk_ids": ["...", "..."],
      "confidence": "high" | "medium" | "low"
    }
  ],
  "gaps": ["..."]
}"""
