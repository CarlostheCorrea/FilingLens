# FilingLens — SEC Filing Intelligence Tool

A browser-based research copilot that uses **RAG + MCP + LLM-as-a-Judge** to analyze SEC filings across public companies. Every feature produces filing-grounded outputs with automated quality scoring.

------------------------------------------------------------------------

## Features

### 📊 Market Analyst

Ask cross-company research questions answered directly from SEC filings. An AI supervisor proposes which companies and filings to include, you review and approve the scope (HITL), and GPT-4o generates claim-by-claim answers with full chunk citations. Every answer is automatically scored by an LLM judge.

### ⚖️ Compare Two Companies

Side-by-side filing-backed comparison of any two public companies on a question of your choice. Fetches relevant filings, runs per-company analysis, synthesizes similarities and differences, and overlays indexed stock price performance around filing dates. LLM judge scores the comparison output.

### 🔄 Filing Change Intelligence

Track how a single company's filing language shifted across time. Compares consecutive filings (10-K, 10-Q, or 8-K), detects material wording changes, classifies each by a fixed taxonomy (new risk introduced, strategy shift, capital allocation change, etc.), and overlays stock market context. LLM judge scores the detected changes.

### 🔎 Market Gap Discovery

Discover structural market opportunities from SEC filings across an entire industry. The pipeline extracts specific pain points from each company, clusters shared problems across incumbents, analyzes why incumbents appear structurally unable to fix them (regulatory lock-in, legacy systems, business model conflicts), and synthesizes ranked founder-oriented opportunity memos. LLM judge scores the analysis for grounding and honesty.

### 📚 Analyst Library

Save any completed Market Analyst session to a named library entry and reload it later without re-ingesting. Preserves the full RAG scope so you can return to the same analyst context instantly.

------------------------------------------------------------------------

## Architecture

```         
Browser (HTML / CSS / JS)
    ↓
FastAPI Backend
    ├── Scope Proposal          GPT-4o + MCP tools (EDGAR discovery)
    ├── Live Ingestion          EDGAR → chunk → embed → ChromaDB
    ├── Market Analyst          Supervisor workflow → claims → LLM judge
    ├── Compare                 Per-company analysis → synthesis → LLM judge
    ├── Change Intelligence     Window diff → change cards → LLM judge
    ├── Market Gap Discovery    Pain extraction → clustering → opportunity memos → LLM judge
    └── Analyst Library         Named save/load of RAG sessions
    ↓
Custom MCP Server (EDGAR tools via edgartools)
    ↓
SEC EDGAR (live, free, no account required)
    ↓
ChromaDB (persistent vector store — local disk)
OpenAI API (embeddings + generation + judge)
```

------------------------------------------------------------------------

## Data Flow

1.  **Scope** — AI proposes companies + form types via EDGAR tool calls; you review, edit, and approve.
2.  **Ingest** — Filings fetched live from EDGAR, section-focused, chunked (\~800 tokens), embedded (`text-embedding-3-small`), stored in ChromaDB. Up to 3 most recent filings per company. Cache-aware: re-embeds only on content change.
3.  **RAG** — Queries embedded at runtime, top-k chunks retrieved, fed to GPT-4o for generation.
4.  **Judge** — Every final output (answer, comparison, change summary, gap analysis) is independently scored by a second LLM pass on helpfulness, clarity, grounding, citation quality, and overclaiming risk.

------------------------------------------------------------------------

## Quick Start

### 1. Install dependencies

``` bash
pip install -r requirements.txt
```

### 2. Configure environment

``` bash
cp .env.example .env
```

Edit `.env`:

```         
OPENAI_API_KEY=...
EDGAR_IDENTITY=Your Name your.email@example.com
```

### 3. Run the server

``` bash
uvicorn main:app --reload --port 8000
```

Open <http://localhost:8000> in your browser.

------------------------------------------------------------------------

## User Flows

### Market Analyst

1.  Enter a research question
2.  Review and edit the AI's proposed scope (companies, form types, date range)
3.  Run Live Ingestion — filings fetched from EDGAR and vectorized
4.  Ask questions — answers generated with claim citations
5.  Verify claims — confirm, flag, or mark as hallucinated
6.  Optionally save the session to the Analyst Library

### Compare Two Companies

1.  Enter two tickers and a comparison question
2.  Select form types and date range
3.  Results include: overall summary, per-company analysis, similarities/differences, stock chart, filing event table, LLM judge score

### Filing Change Intelligence

1.  Enter a ticker and an analysis lens (e.g. "what changed in pricing and margin language?")
2.  Select form types, time window, and number of filings
3.  Results include: change cards classified by category, timeline selector, stock context, LLM judge score

### Market Gap Discovery

1.  Describe an industry or sector
2.  AI discovers a representative set of companies (8–12) — review and edit
3.  Pipeline runs: pain extraction → clustering → structural constraint analysis → opportunity memos
4.  Results include: ranked opportunity memos, gap clusters with evidence, coverage notes, LLM judge score

------------------------------------------------------------------------

## Example Questions

**Market Analyst** - "How do NVIDIA, AMD, and Intel describe their dependence on TSMC?" - "Which semiconductor companies flagged U.S.-China export controls as a material risk?"

**Compare** - "How do Coca-Cola and PepsiCo compare on pricing power and volume strategy?" - "How do JPMorgan and Bank of America describe credit loss exposure?"

**Change Intelligence** - "What changed in Apple's supply chain risk language between 2022 and 2024?" - "How did Meta's description of regulatory risk shift after the EU Digital Markets Act?"

**Market Gap Discovery** - "US beverage distribution" - "Regional banking technology infrastructure" - "Industrial automation and robotics"

------------------------------------------------------------------------

## LLM-as-a-Judge

Every feature runs an automated quality review after generation. The judge is a separate LLM call that scores the output independently — it does not share context with the generation step.

| Feature | What the judge evaluates |
|------------------------------------|------------------------------------|
| Market Analyst | Helpfulness, clarity, grounding, citation quality, overclaiming risk |
| Compare | Whether similarities/differences are specific and filing-backed; flags causal stock claims |
| Change Intelligence | Whether change cards have real before/after evidence; flags inferred management intent |
| Market Gap | Whether clusters are specific (not platitudes); flags `strong` status without proportionate constraint evidence |

Scores: 1–5 per dimension. Verdict: `strong` / `mixed` / `weak`. Overclaiming risk: `low` / `medium` / `high`.

------------------------------------------------------------------------

## Project Structure

| Path | Purpose |
|------------------------------------|------------------------------------|
| `main.py` | FastAPI entry point, router registration |
| `config.py` | Settings, all system prompts, constants |
| `models.py` | Pydantic schemas for all features |
| `agent.py` | GPT-4o scope proposal (standard + market gap) |
| `edgar_client.py` | EdgarTools wrapper, ticker-to-CIK resolution |
| `mcp_server.py` | Standalone MCP server (EDGAR tools) |
| `mcp_client.py` | Backend MCP client (stdio subprocess) |
| `rag_pipeline.py` | Chunk, embed, retrieve, EphemeralStore, section scout |
| `answer_workflow.py` | LangGraph supervisor workflow for Market Analyst |
| `hitl.py` | HITL state persistence (scope, answers, manifests) |
| `logging_utils.py` | Structured JSONL logging |
| `routes/` | FastAPI route handlers (scope, ingest, answer, compare, change, gap, library, data, verify) |
| `services/` | Business logic (ingestion, answer, compare, change intelligence, market gap, judge, library) |
| `static/app.js` | All frontend logic |
| `static/style.css` | All styles |
| `templates/index.html` | Single-page app shell |

------------------------------------------------------------------------

## Tech Stack

| Component          | Technology                                 |
|--------------------|--------------------------------------------|
| LLM (generation)   | GPT-4o                                     |
| LLM (worker tasks) | GPT-4o-mini                                |
| LLM (judge)        | GPT-4o-mini                                |
| Embeddings         | text-embedding-3-small                     |
| Vector store       | ChromaDB (persistent local)                |
| SEC data           | edgartools + SEC EDGAR (free, live)        |
| MCP                | Custom stdio MCP server                    |
| Backend            | FastAPI + uvicorn                          |
| Frontend           | Plain HTML, CSS, JavaScript (no framework) |

------------------------------------------------------------------------

## Running Tests

``` bash
pytest tests/ -v
```