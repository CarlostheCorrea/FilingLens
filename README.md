# FilingLens — SEC Filing Intelligence Workspace

FilingLens is a browser-based SEC EDGAR research workspace for public-company analysis. It combines **RAG**, a **custom MCP server**, **human-in-the-loop review**, **multi-agent answer generation**, **XBRL financial data**, **LLM-as-a-judge**, and **RAGAS** to turn SEC filings into production-style research outputs.

The app is built as a single FastAPI + HTML/CSS/JS workspace with five user-facing modes:

- `Market Analyst`
- `Compare Companies`
- `Filing Change Intelligence`
- `Market Gap Discovery`
- `Financial Data`

---

## What It Does

### `Market Analyst`

Ask cross-company research questions directly against SEC filings.

- AI proposes companies, forms, and date range using MCP tools
- user reviews and approves the scope before ingestion
- filings are fetched, section-filtered, chunked, embedded, and stored in Chroma
- answers are generated through a **LangGraph supervisor workflow**
- output includes:
  - overall answer
  - company deep dives
  - claims audit
  - claim-level HITL verification
  - LLM judge
  - RAGAS panel

### `Compare Companies`

Compare two companies side by side using recent filings.

- filing-backed per-company summaries
- synthesized similarities and differences
- indexed stock chart using `yfinance`
- filing-event return windows
- optional XBRL context for quantitative grounding
- LLM judge

### `Filing Change Intelligence`

Track how one company’s disclosure language changes across time.

- compares filings across a selected date range
- classifies material shifts into a fixed taxonomy
- renders structured change cards with before/after evidence
- can add annual XBRL context to the final summary layer
- includes stock context and LLM judge

### `Market Gap Discovery`

Analyze an industry as a market structure problem rather than a company problem.

- extracts pain points from multiple companies’ filings
- clusters shared problems across incumbents
- analyzes hard vs soft structural constraints
- produces ranked **founder-focused opportunity memos**
- each memo now supports **memo-scoped follow-up chat** grounded only in that memo’s filing evidence
- LLM judge scores the output

### `Financial Data`

Pull structured financial data directly from EDGAR.

- machine-readable XBRL metrics
- annual financial facts such as revenue, income, EPS, assets, cash flows
- organized by category: income statement, balance sheet, cash flows, and per-share metrics

---

## Core Capabilities

- SEC filing discovery and fetch through a custom MCP server
- persistent local vector retrieval with ChromaDB
- long-section window scouting for deep filing retrieval
- foreign issuer support (`20-F`, `6-K`)
- XBRL financial fact extraction
- claim-level verification workflow
- automated LLM judging
- reference-free RAGAS scoring for Research Answer

---

## Architecture

```text
Browser UI (HTML / CSS / JS)
    ↓
FastAPI app
    ├── Scope proposal / approval
    ├── Ingestion + vector refresh
    ├── Market Analyst
    │     └── LangGraph supervisor workflow
    ├── Compare Companies
    ├── Filing Change Intelligence
    ├── Market Gap Discovery
    ├── Financial Data (XBRL + tables)
    └── Library / logs / data controls
    ↓
MCP client (stdio subprocess)
    ↓
Custom MCP server
    ├── company discovery tools
    ├── filing listing tools
    ├── filing fetch tools
    ├── filing table extraction
    └── XBRL facts
    ↓
SEC EDGAR / edgartools

OpenAI API
    ├── generation
    ├── worker-model subtasks
    ├── judge scoring
    ├── RAGAS evaluation
    └── embeddings

ChromaDB
    ├── persistent sec_filings collection
    └── ephemeral per-run stores where needed
```

---

## Multi-Agent / HITL / MCP

### Multi-agent

FilingLens uses a real **supervisor-style multi-agent workflow** in `Market Analyst`.

Flow:

1. `load_context`
2. `supervisor`
3. parallel `company_worker` nodes, one per company
4. `merge_answers`
5. `review_answer`
6. `finalize`

This lives in [answer_workflow.py](/Users/carloscorrea/Documents/GitHub/FilingLens/answer_workflow.py).

### Human in the loop

Two explicit HITL checkpoints are built in:

1. scope review/approval before ingestion
2. claim verification after answer generation

### MCP

The repo uses the Python `mcp` package and a custom local MCP server.

Current MCP tools:

- `list_companies_by_sector`
- `search_company`
- `list_filings`
- `fetch_filing`
- `fetch_filing_section`
- `list_recent_filings_for_company`
- `resolve_ticker_to_cik`
- `extract_filing_tables`
- `get_xbrl_facts`

These tools are used mainly for:

- company discovery
- filing discovery
- filing fetch
- XBRL fetch

Later synthesis, judging, and retrieval logic run inside the app after the source data has been fetched.

---

## Evaluation

### LLM-as-a-Judge

All major analysis modes use an automated judge pass:

- `Market Analyst`
- `Compare Companies`
- `Filing Change Intelligence`
- `Market Gap Discovery`

Judge dimensions typically include:

- helpfulness
- clarity
- grounding
- citation quality
- overclaiming risk

### RAGAS

`Market Analyst` also includes a RAGAS panel.

Current RAGAS usage is **reference-free**, so it does not require ground-truth answers. The repo evaluates:

- faithfulness
- answer relevancy
- context utilization

If one metric fails because of prompt/output budget limits, the UI shows a partial status rather than implying full grounding.

---

## Data Flow

### Market Analyst

1. User enters a research question
2. AI proposes a scope through MCP-backed discovery
3. User approves the scope
4. Ingestion fetches filings and refreshes vectors if needed
5. LangGraph workflow generates the answer
6. Judge and RAGAS score the result
7. User can verify claims and save the session to the library

### Compare / Change / Market Gap

These are button-triggered service workflows:

- the user provides the input form
- backend code decides which MCP tools to call
- filing data is fetched automatically
- results are synthesized and judged

So the repo uses both:

- **LLM-directed tool calling** for scope proposal
- **code-directed tool calling** for execution workflows

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Set at minimum:

```text
OPENAI_API_KEY=...
EDGAR_IDENTITY=Your Name your.email@example.com
```

Optional model settings:

```text
OPENAI_MODEL=gpt-4o
OPENAI_WORKER_MODEL=gpt-4o-mini
OPENAI_JUDGE_MODEL=gpt-4o-mini
OPENAI_RAGAS_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

### 3. Run the app

```bash
uvicorn main:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000).

---

## Feature Workflows

### Market Analyst

1. Enter a research question
2. Review/edit the proposed scope
3. Approve and ingest filings
4. Ask a research question against the ingested scope
5. Review overall answer, deep dives, claims audit, judge, and RAGAS
6. Verify claims if needed

### Compare Companies

1. Enter two tickers
2. Enter a comparison question
3. Choose filing types, date range, and stock lookback
4. Review the filing-backed comparison, judge panel, stock chart, and event table

### Filing Change Intelligence

1. Enter one ticker
2. Enter a change lens question
3. Choose forms, time frame, filing count, and stock context
4. Review overall change summary, change cards, and judge panel

### Market Gap Discovery

1. Describe an industry or market problem
2. Review the proposed company set
3. Run market analysis
4. Review industry summary, ranked opportunity memos, underlying gap clusters, and judge panel
5. Ask memo-specific follow-up questions in the grounded follow-up chat

### Financial Data

1. Enter a ticker
2. Pull XBRL metrics directly from SEC EDGAR
3. Review annual income, balance sheet, and cash flow metrics across fiscal years

---

## Important Implementation Notes

### Date ranges

Date-driven UI sections now normalize the end date to the current date in the proposal flows so stale fixed end dates do not linger in the forms.

### Long-section retrieval

The repo no longer relies on only the opening preview of a long filing section. It uses overlapping scouting windows to find relevant deep content inside large sections before chunking and retrieval.

### XBRL

XBRL is now available as an MCP tool and the shared XBRL context service routes through the MCP client rather than calling the EDGAR helper directly.

### Market Gap memo chat

The follow-up chat under each opportunity memo is:

- scoped to a single memo
- grounded only in that memo’s evidence set / cluster evidence
- citation-aware across turns
- designed to prefer new evidence and suppress repeated citations when no new support exists

---

## Repository Structure

| Path | Purpose |
|---|---|
| `main.py` | FastAPI entry point |
| `config.py` | env settings, prompt templates, constants |
| `models.py` | shared Pydantic models |
| `agent.py` | scope proposal logic |
| `answer_workflow.py` | LangGraph supervisor workflow for Market Analyst |
| `mcp_server.py` | custom MCP server |
| `mcp_client.py` | backend MCP client |
| `edgar_client.py` | SEC/edgartools helper layer |
| `rag_pipeline.py` | chunking, embeddings, retrieval, vector refresh |
| `services/` | feature services |
| `routes/` | API routes |
| `templates/index.html` | main app shell |
| `static/app.js` | frontend behavior |
| `static/style.css` | styles |
| `data/` | local app state, logs, caches, vectors |
| `tests/` | test suite |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + uvicorn |
| Frontend | Plain HTML / CSS / JavaScript |
| LLM (primary) | GPT-4o |
| Worker / judge / RAGAS model defaults | GPT-4o-mini |
| Embeddings | `text-embedding-3-small` |
| RAG evaluation | `ragas` |
| Vector store | ChromaDB |
| SEC access | SEC EDGAR + `edgartools` |
| MCP | Python `mcp` package + custom stdio server |
| Stock context | `yfinance` |

---

## Running Tests

Run the full suite:

```bash
pytest tests/ -v
```

Run a focused area:

```bash
pytest -q tests/test_market_gap_service.py
pytest -q tests/test_ragas_service.py
pytest -q tests/test_change_intelligence_service.py
```

---

## Current Positioning

FilingLens is not just a filing viewer and not just an AI wrapper. It is a **market intelligence workspace** built around live SEC evidence, structured review, and decision-support outputs.
