# FilingLens — SEC Filing Intelligence Workspace

FilingLens is a browser-based SEC EDGAR research workspace for public-company analysis. It combines **RAG**, a **custom MCP server**, **human-in-the-loop review**, **multi-agent answer generation**, **XBRL financial data**, **LLM-as-a-judge**, **RAGAS**, and optional **local Ollama classifiers** to turn SEC filings into production-style research outputs.

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
- prompt injection protection via input sanitization and filing content isolation

---

## Architecture

```text
Browser UI (HTML / CSS / JS)
    ↓
Input Sanitizer (services/sanitizer.py)
    ├── query injection detection + unicode normalization
    ├── ticker format validation
    └── filing content XML wrapping (indirect injection defence)
    ↓
FastAPI app
    ├── Scope proposal / approval
    ├── Ingestion + vector refresh
    ├── Market Analyst
    │     └── LangGraph supervisor workflow
    ├── Compare Companies
    ├── Filing Change Intelligence
    ├── Market Gap Discovery
    ├── Financial Data (XBRL)
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

Ollama (optional, local)
    └── lightweight classification tasks

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

### Clone Repo
```bash
git clone https://github.com/CarlostheCorrea/FilingLens
cd FilingLens

```

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

Optional local Ollama classifier settings:

```text
OLLAMA_MODEL=llama3.1:8b
OLLAMA_BASE_URL=http://localhost:11434
LOCAL_CLASSIFIER_ENABLED=true
LOCAL_CLASSIFIER_TIMEOUT_SECONDS=30
LOCAL_CLASSIFIER_FALLBACK_TO_OPENAI=true
LOCAL_SECONDARY_JUDGE_ENABLED=false
```

Ollama is used only for constrained local classification tasks such as table category, pain-point category/severity, buyer owner, urgency, adoption difficulty, change-card labels, and claim confidence. It is not used for embeddings, final synthesis, RAGAS, or the primary LLM judge. Embeddings use `OPENAI_EMBEDDING_MODEL`, which defaults to `text-embedding-3-small`.

### 3. Run the app

```bash
uvicorn main:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000).

---

## Feature Workflows

### Market Analyst

1. Enter a research question

<img width="1512" height="825" alt="Screenshot 2026-04-27 at 8 44 28 AM" src="https://github.com/user-attachments/assets/03ef41e0-7721-43ff-86b3-37ac71a41ef6" />

3. Review/edit the proposed scope
4. Approve and ingest filings

<img width="1512" height="823" alt="Screenshot 2026-04-27 at 8 45 29 AM" src="https://github.com/user-attachments/assets/753314b1-c53e-4a02-8b2e-a81aee1ff6b7" />

5. Ask a research question against the ingested scope

<img width="1042" height="641" alt="Screenshot 2026-04-27 at 8 52 32 AM" src="https://github.com/user-attachments/assets/14a4731f-8b1d-40d1-9961-3e325be6d1ed" />

6. Review overall answer, deep dives, claims audit, judge, and RAGAS
7. Verify claims if needed

<img width="777" height="621" alt="Screenshot 2026-04-27 at 8 56 35 AM" src="https://github.com/user-attachments/assets/0d0db84a-c180-4ffd-bc04-9cffd1ef0065" />

<img width="749" height="551" alt="Screenshot 2026-04-27 at 8 57 07 AM" src="https://github.com/user-attachments/assets/bdc0b524-c612-4b67-a645-3e1ca1e084fc" />


### Compare Companies

1. Enter two tickers
2. Enter a comparison question
3. Choose filing types, date range, and stock lookback

<img width="786" height="615" alt="Screenshot 2026-04-27 at 8 59 03 AM" src="https://github.com/user-attachments/assets/61eb0c3c-8050-444d-a9ca-5d782a526202" />

<img width="718" height="554" alt="Screenshot 2026-04-27 at 8 59 33 AM" src="https://github.com/user-attachments/assets/9fc427c9-31d8-4687-bf86-019a45edd16c" />

5. Review the filing-backed comparison, judge panel, stock chart, and event table

<img width="718" height="435" alt="Screenshot 2026-04-27 at 8 59 59 AM" src="https://github.com/user-attachments/assets/f827aba7-6eb9-4db6-bf57-1a4f2c9d8f4b" />


### Filing Change Intelligence

1. Enter one ticker
2. Enter a change lens question

<img width="750" height="353" alt="Screenshot 2026-04-27 at 9 01 59 AM" src="https://github.com/user-attachments/assets/492d7b8f-7683-4538-acff-42effa2e5d28" />


4. Choose forms, time frame, filing count, and stock context
5. Review overall change summary, change cards, and judge panel

<img width="733" height="614" alt="Screenshot 2026-04-27 at 9 03 36 AM" src="https://github.com/user-attachments/assets/302faa72-4ae1-4d97-8eef-357a77710e80" />

<img width="714" height="641" alt="Screenshot 2026-04-27 at 9 03 55 AM" src="https://github.com/user-attachments/assets/c0511017-fb82-40aa-bf62-68c351553072" />


### Market Gap Discovery

1. Describe an industry or market problem

<img width="711" height="637" alt="Screenshot 2026-04-27 at 9 05 38 AM" src="https://github.com/user-attachments/assets/e557a05d-593f-4bbb-bb77-e6876220d576" />

3. Review the proposed company set
4. Run market analysis
5. Review industry summary, ranked opportunity memos, underlying gap clusters, and judge panel

<img width="749" height="564" alt="Screenshot 2026-04-27 at 9 09 08 AM" src="https://github.com/user-attachments/assets/bbe177f9-528a-4c44-90bf-cdc15458b392" />

<img width="711" height="601" alt="Screenshot 2026-04-27 at 9 09 30 AM" src="https://github.com/user-attachments/assets/fb122dd8-a85d-4567-a54a-0f863f4c2cd9" />

7. Ask memo-specific follow-up questions in the grounded follow-up chat

<img width="720" height="347" alt="Screenshot 2026-04-27 at 9 10 28 AM" src="https://github.com/user-attachments/assets/e51bd952-c31b-4f2e-9ea4-640cc50ac86c" />


### Financial Data

1. Enter a ticker
2. Pull XBRL metrics directly from SEC EDGAR
3. Review annual income, balance sheet, and cash flow metrics across fiscal years

---

## Important Implementation Notes

### Prompt injection protection

All user-supplied inputs are sanitized by `services/sanitizer.py` before reaching any LLM call. Three layers:

1. **Query sanitization** — unicode normalization (NFKC), control character removal, 2,000-character limit, and detection of 15+ injection phrase families. Raises a clear error on detection rather than silently stripping.
2. **Ticker validation** — strict regex enforcing 1–5 uppercase letters with optional dot/dash suffix (covers BRK.A, BF.B). Rejects anything that does not match.
3. **Filing content wrapping** — SEC filing chunks inserted into LLM prompts are wrapped in `<filing_content>` XML tags, signalling to the model that the enclosed text is external data to analyse, not instructions to follow. Defends against indirect injection from adversarial text in filings.

Sanitization is enforced automatically via Pydantic `@field_validator` on all request models — it cannot be bypassed at the route level.

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
| `models.py` | shared Pydantic models with input validators |
| `agent.py` | scope proposal logic |
| `answer_workflow.py` | LangGraph supervisor workflow for Market Analyst |
| `mcp_server.py` | custom MCP server |
| `mcp_client.py` | backend MCP client |
| `edgar_client.py` | SEC/edgartools helper layer |
| `rag_pipeline.py` | chunking, embeddings, retrieval, vector refresh |
| `services/sanitizer.py` | prompt injection protection — query sanitization, ticker validation, filing content wrapping |
| `services/xbrl_context_service.py` | XBRL fetch and quantitative query detection |
| `services/` | feature services (compare, change intelligence, market gap, judge, RAGAS, etc.) |
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
| Local classifiers | Ollama `llama3.1:8b` with OpenAI fallback |
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
