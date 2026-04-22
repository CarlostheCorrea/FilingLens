# FilingLens — SEC Filing Intelligence Tool

A browser-based research copilot that uses **RAG + MCP + Human-in-the-Loop** to analyze SEC filings across public companies.

## Architecture

```         
Browser (HTML/CSS/JS)
    ↓
FastAPI Backend
    ├── Scope Proposal (GPT-4o + MCP tools)
    ├── Live Ingestion (EDGAR → ChromaDB)
    ├── RAG Answer Generation (GPT-4o)
    └── Claim Verification (HITL)
    ↓
Custom MCP Server (EDGAR tools)
    ↓
SEC EDGAR via edgartools
    ↓
ChromaDB (vector store)
```

## Quick Start

### 1. Install dependencies

``` bash
pip install -r requirements.txt
```

### 2. Configure environment

``` bash
cp .env.example .env
# Edit .env:
# OPENAI_API_KEY=sk-...
# EDGAR_IDENTITY=Your Name your.email@example.com
```

### 3. Run the server

``` bash
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000 in your browser.

## User Flow

1.  **Ask** — Enter a research question about public companies
2.  **Review scope** — Agent proposes companies + filing types via MCP tools
3.  **Edit scope** — Add/remove companies, adjust form types and date range *(HITL Checkpoint 1)*
4.  **Ingest** — Filings fetched from EDGAR, chunked, embedded, stored in Chroma
5.  **Answer** — GPT-4o generates claim-by-claim answers with chunk citations
6.  **Verify** — Confirm, flag for revision, or mark claims as hallucinated *(HITL Checkpoint 2)*

## Demo Questions

-   "How do NVIDIA, AMD, and Intel describe their dependence on TSMC in their most recent 10-Ks?"
-   "Which semiconductor companies flagged U.S.-China export controls as a material risk?"
-   "How did Micron's description of memory pricing risk change between 2022 and 2024?"

## Project Structure

| File               | Purpose                             |
|--------------------|-------------------------------------|
| `main.py`          | FastAPI entry point                 |
| `config.py`        | Settings, prompts, constants        |
| `models.py`        | Pydantic schemas                    |
| `edgar_client.py`  | EdgarTools wrapper                  |
| `mcp_server.py`    | Standalone MCP server (EDGAR tools) |
| `mcp_client.py`    | Backend MCP client                  |
| `rag_pipeline.py`  | Chunk, embed, retrieve              |
| `agent.py`         | GPT-4o scope proposal + answer      |
| `hitl.py`          | Scope/answer state management       |
| `logging_utils.py` | JSONL structured logging            |
| `routes/`          | FastAPI route handlers              |
| `services/`        | Business logic layer                |
| `static/`          | Frontend JS + CSS                   |
| `templates/`       | Jinja2 HTML template                |

## Running Tests

``` bash
pytest tests/ -v
```

## Tech Stack

-   **LLM**: GPT-4o (all generation tasks)
-   **Embeddings**: text-embedding-3-small
-   **Vector Store**: ChromaDB (persistent)
-   **SEC Data**: edgartools + SEC EDGAR
-   **MCP**: Custom stdio MCP server
-   **Backend**: FastAPI + uvicorn
-   **Frontend**: Plain HTML, CSS, JavaScript