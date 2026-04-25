/* ═══════════════════════════════════════════
   FilingLens — app.js
   ═══════════════════════════════════════════ */

/* ── State ── */
const state = {
  activeTab: 'ai',
  proposalId: null,
  companies: [],       // AI tab scope companies
  formTypes: [],
  dateRange: ['', ''],
  answer: null,
  currentQuery: '',
  compare: null,
  compareChart: null,
  compareFormTypes: ['10-K', '10-Q', '8-K', '20-F', '6-K'],
  compareDateRange: ['', ''],
  compareLookback: '3M',
  change: null,
  changeChart: null,
  changeFormTypes: ['10-K', '10-Q'],
  changeDateRange: ['', ''],
  changeLookback: '3M',
  changeWindowId: '',
  manualTickers: [],   // Manual tab tickers
  manualFormTypes: ['10-K', '10-Q'],
  manualDateRange: ['', ''],
};

/* ── Preset company groups ── */
const PRESETS = {
  semis:   ['NVDA', 'AMD', 'INTC', 'QCOM', 'AVGO', 'MU', 'AMAT', 'LRCX'],
  bigtech: ['AAPL', 'MSFT', 'GOOGL', 'META', 'AMZN', 'TSLA', 'NFLX'],
  ev:      ['TSLA', 'RIVN', 'LCID', 'NIO', 'F', 'GM'],
  banks:   ['JPM', 'BAC', 'WFC', 'GS', 'MS', 'C'],
};

/* ── Utilities ── */
const $ = id => document.getElementById(id);
const show = id => $(id) && $(id).classList.remove('hidden');
const hide = id => $(id) && $(id).classList.add('hidden');

function switchMode(mode) {
  const modes = ['research', 'compare', 'change', 'gap'];
  modes.forEach(m => {
    const el = $(`${m}-mode`);
    const btn = $(`mode-btn-${m}`);
    if (el) el.classList.toggle('hidden', m !== mode);
    if (btn) btn.classList.toggle('active', m === mode);
  });
  const tagline = $('mode-tagline');
  if (tagline) {
    tagline.textContent = {
      research: 'Ask questions across SEC filings for a set of companies',
      compare:  'Side-by-side analysis of two companies using their filings',
      change:   'Track how one company’s filing language changes across time',
      gap:      'Find structural pain points across an industry and identify where incumbents are stuck',
    }[mode] || '';
  }
}

function normalizeCik(cikOrAccession) {
  const raw = String(cikOrAccession || '').split('-')[0].trim();
  if (!raw) return '';
  const numeric = Number.parseInt(raw, 10);
  return Number.isNaN(numeric) ? raw : String(numeric);
}

function edgarUrl(cik, accessionNumber) {
  const normalizedCik = normalizeCik(cik || accessionNumber);
  const normalizedAccession = String(accessionNumber || '').trim();
  if (!normalizedCik || !normalizedAccession) return '';
  const accessionNoDashes = normalizedAccession.replace(/-/g, '');
  return `https://www.sec.gov/Archives/edgar/data/${normalizedCik}/${accessionNoDashes}/${normalizedAccession}-index.htm`;
}

function log(msg, type = 'info') {
  const el = $('activity-log');
  const now = new Date().toLocaleTimeString();
  const div = document.createElement('div');
  div.className = `log-entry log-${type}`;
  div.innerHTML = `<span class="log-time">${now}</span><span class="log-msg">${msg}</span>`;
  el.prepend(div);
}
function clearLog() { $('activity-log').innerHTML = ''; }

function today() {
  return new Date().toISOString().slice(0, 10);
}
function yearsAgo(n) {
  const d = new Date();
  d.setFullYear(d.getFullYear() - n);
  return d.toISOString().slice(0, 10);
}

/* ── Tab switching ── */
function switchTab(tab) {
  state.activeTab = tab;
  ['ai', 'manual'].forEach(t => {
    $(`tab-${t}`).classList.toggle('hidden', t !== tab);
    $(`tab-btn-${t}`).classList.toggle('active', t === tab);
  });
  // Hide downstream panels when switching tabs
  hide('scope-panel');
  hide('ingestion-panel');
  hide('answer-panel');
}

function getAnswerPayload(data) {
  return data.answer || data || {};
}

function getAuditClaims(data) {
  const answer = getAnswerPayload(data);
  return answer.claims_audit?.claims || answer.claims || [];
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const sign = Number(value) > 0 ? '+' : '';
  return `${sign}${Number(value).toFixed(2)}%`;
}

function compareEventId(event) {
  return `${event.ticker}-${event.accession_number}`;
}

/* ═══════════════════════════════════════════
   ANALYST LIBRARY
   ═══════════════════════════════════════════ */
function toggleLibraryPanel() {
  const body = $('library-body');
  const chevron = $('library-chevron');
  const isHidden = body.classList.contains('hidden');
  body.classList.toggle('hidden', !isHidden);
  chevron.textContent = isHidden ? '▲' : '▼';
  if (isHidden) loadLibrary();
}

async function loadLibrary() {
  try {
    const res = await fetch('/api/library');
    if (!res.ok) return;
    const entries = await res.json();
    renderLibrary(entries);
  } catch (_) {}
}

function renderLibrary(entries) {
  const badge = $('library-count-badge');
  if (entries.length) {
    badge.textContent = entries.length;
    badge.classList.remove('hidden');
  } else {
    badge.classList.add('hidden');
  }

  const list = $('library-list');
  if (!entries.length) {
    list.innerHTML = '<div class="empty-state">No saved analysts yet. Run an ingestion, then save it below.</div>';
    return;
  }
  list.innerHTML = entries.map(entry => {
    const date = entry.created_at ? new Date(entry.created_at).toLocaleDateString() : '';
    const tickers = (entry.companies || []).map(c => c.ticker).join(', ');
    const filingCount = (entry.filings || []).length;
    return `
      <div class="library-entry" id="lib-entry-${entry.id}">
        <div class="library-entry-main">
          <div class="library-entry-name">${entry.name}</div>
          <div class="library-entry-meta">${tickers} &bull; ${entry.form_types?.join(', ') || ''} &bull; ${filingCount} filing${filingCount !== 1 ? 's' : ''} &bull; saved ${date}</div>
        </div>
        <div class="library-entry-actions">
          <button class="btn btn-sm btn-primary" onclick="loadAnalyst('${entry.id}')">&#9654; Load</button>
          <button class="btn btn-sm btn-ghost" onclick="deleteAnalyst('${entry.id}', '${entry.name.replace(/'/g, "\\'")}')">&#x2715;</button>
        </div>
      </div>
    `;
  }).join('');
}

async function saveAnalyst() {
  const name = $('library-name-input').value.trim();
  if (!name) { alert('Enter a name for this analyst.'); return; }
  if (!state.proposalId) { alert('No active ingestion session to save.'); return; }

  const statusEl = $('library-save-status');
  statusEl.textContent = 'Saving…';
  statusEl.className = 'library-save-status';
  statusEl.classList.remove('hidden');

  try {
    const res = await fetch('/api/library/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proposal_id: state.proposalId, name }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Server error');
    $('library-name-input').value = '';
    statusEl.textContent = `✓ Saved as "${name}"`;
    statusEl.className = 'library-save-status success';
    log(`Analyst saved: ${name}`, 'success');
    // Refresh the library panel count badge quietly
    loadLibrary();
  } catch (err) {
    statusEl.textContent = 'Error: ' + err.message;
    statusEl.className = 'library-save-status error';
  }
}

async function loadAnalyst(analystId) {
  try {
    const res = await fetch(`/api/library/load/${analystId}`, { method: 'POST' });
    if (!res.ok) throw new Error((await res.json()).detail || 'Server error');
    const entry = await res.json();

    // Restore scope state
    state.proposalId = entry.proposal_id;
    state.companies  = entry.companies || [];
    state.formTypes  = entry.form_types || [];
    state.dateRange  = entry.date_range || ['', ''];

    log(`Loaded analyst: ${entry.name}`, 'success');

    if (entry.vectors_present) {
      // Vectors still on disk — skip re-ingestion, go straight to questions
      renderIngestionResults({
        filings_ingested: (entry.filings || []).length,
        chunks_created: 0,
        filings: entry.filings || [],
        issues: [],
        errors: [],
      });
      show('ingestion-panel');
      log(`Vectors present — skipping re-ingestion for "${entry.name}"`, 'info');
    } else {
      // Vectors gone — show panel with re-ingest prompt
      show('ingestion-panel');
      hide('ingestion-results');
      show('ingestion-progress');
      hide('ask-question-area');
      $('ingestion-badge').textContent = 'Re-ingest Needed';
      $('ingestion-badge').className = 'badge badge-yellow';
      $('ingestion-status-text').innerHTML =
        `Vectors for <strong>${entry.name}</strong> were cleared. ` +
        `<button class="btn btn-sm btn-primary" style="margin-left:8px" onclick="runIngestion()">Re-ingest Now</button>`;
      log(`Vectors cleared — re-ingest needed for "${entry.name}"`, 'warn');
    }

    $('ingestion-panel').scrollIntoView({ behavior: 'smooth' });
  } catch (err) {
    log('Load analyst error: ' + err.message, 'error');
  }
}

async function deleteAnalyst(analystId, name) {
  if (!confirm(`Remove analyst "${name}" from library? This does not delete the indexed vectors.`)) return;
  try {
    const res = await fetch(`/api/library/${analystId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error((await res.json()).detail || 'Server error');
    log(`Analyst removed: ${name}`, 'warn');
    loadLibrary();
  } catch (err) {
    log('Delete analyst error: ' + err.message, 'error');
  }
}

/* ═══════════════════════════════════════════
   TIME FRAME PRESETS
   ═══════════════════════════════════════════ */
function setTimePreset(years, context) {
  const startId = context === 'manual' ? 'manual-date-start' : 'date-start';
  const endId   = context === 'manual' ? 'manual-date-end'   : 'date-end';

  if (years === 'custom') {
    // Just focus the start date input — user fills manually
    $(startId).focus();
  } else {
    $(startId).value = yearsAgo(years);
    $(endId).value   = today();
    if (context === 'manual') {
      state.manualDateRange = [$(startId).value, $(endId).value];
    } else {
      state.dateRange = [$(startId).value, $(endId).value];
    }
  }

  // Highlight active preset button
  const row = context === 'manual'
    ? $('tab-manual').querySelectorAll('.time-preset-btn')
    : document.querySelector('#scope-panel .time-preset-row').querySelectorAll('.time-preset-btn');
  row.forEach(btn => {
    btn.classList.toggle('active',
      (years === 'custom' && btn.textContent === 'Custom') ||
      (years !== 'custom' && btn.textContent === `${years}Y`)
    );
  });
}

/* ═══════════════════════════════════════════
   MANUAL TAB
   ═══════════════════════════════════════════ */
function addManualTickers() {
  const raw = $('manual-ticker-input').value;
  const tickers = raw.toUpperCase().split(/[\s,;]+/).map(t => t.trim()).filter(Boolean);
  tickers.forEach(t => {
    if (!state.manualTickers.includes(t)) state.manualTickers.push(t);
  });
  $('manual-ticker-input').value = '';
  renderManualChips();
}

function removeManualTicker(ticker) {
  state.manualTickers = state.manualTickers.filter(t => t !== ticker);
  renderManualChips();
}

function clearManualTickers() {
  state.manualTickers = [];
  renderManualChips();
}

function addPreset(key) {
  const tickers = PRESETS[key] || [];
  tickers.forEach(t => {
    if (!state.manualTickers.includes(t)) state.manualTickers.push(t);
  });
  renderManualChips();
  log(`Added ${tickers.length} ${key} tickers`, 'info');
}

function renderManualChips() {
  const container = $('manual-ticker-chips');
  if (!state.manualTickers.length) {
    hide('manual-ticker-chips');
    return;
  }
  show('manual-ticker-chips');
  container.innerHTML = state.manualTickers.map(t => `
    <span class="ticker-chip">
      ${t}
      <span class="x" onclick="removeManualTicker('${t}')" title="Remove">&#x2715;</span>
    </span>
  `).join('');
}

function toggleManualForm(chip) {
  const type = chip.dataset.type;
  chip.classList.toggle('active');
  if (chip.classList.contains('active')) {
    if (!state.manualFormTypes.includes(type)) state.manualFormTypes.push(type);
  } else {
    state.manualFormTypes = state.manualFormTypes.filter(t => t !== type);
  }
}

async function startManualIngest() {
  if (!state.manualTickers.length) {
    alert('Add at least one ticker first.');
    return;
  }
  if (!state.manualFormTypes.length) {
    alert('Select at least one filing type.');
    return;
  }

  // Read current date range from inputs
  const ds = $('manual-date-start').value;
  const de = $('manual-date-end').value;
  if (!ds || !de) {
    alert('Please set a date range or pick a time frame preset.');
    return;
  }

  $('manual-ingest-btn').disabled = true;
  $('manual-status').textContent = 'Resolving tickers…';
  log(`Manual scope: ${state.manualTickers.join(', ')}`, 'info');

  try {
    // Create scope on backend (resolves CIKs)
    const scopeRes = await fetch('/api/scope/manual', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tickers: state.manualTickers,
        form_types: state.manualFormTypes,
        date_range: [ds, de],
      }),
    });
    if (!scopeRes.ok) throw new Error((await scopeRes.json()).detail || 'Server error');
    const scope = await scopeRes.json();

    state.proposalId = scope.proposal_id;
    state.companies  = scope.approved_companies;
    $('manual-status').textContent = '';

    log(`Scope created: ${scope.approved_companies.length} companies`, 'success');
    await runIngestion();
  } catch (err) {
    log('Manual ingest error: ' + err.message, 'error');
    $('manual-status').textContent = 'Error: ' + err.message;
  } finally {
    $('manual-ingest-btn').disabled = false;
  }
}

/* ═══════════════════════════════════════════
   AI TAB — SCOPE PROPOSAL
   ═══════════════════════════════════════════ */
async function proposeScope() {
  const query = $('query-input').value.trim();
  if (!query) { alert('Please enter a research question.'); return; }

  const btn = $('propose-btn');
  btn.disabled = true;
  btn.textContent = 'Proposing scope…';
  log('Proposing scope for: ' + query.slice(0, 60) + (query.length > 60 ? '…' : ''), 'info');

  try {
    const res = await fetch('/api/scope/propose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Server error');
    const data = await res.json();
    renderScopeProposal(data);
    log(`Scope proposed: ${data.companies.length} companies, ${data.form_types.join(', ')}`, 'success');
  } catch (err) {
    log('Error: ' + err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Propose Scope';
  }
}

function renderScopeProposal(data) {
  state.proposalId = data.proposal_id;
  state.companies  = data.companies.map(c => ({ ...c }));
  state.formTypes  = [...data.form_types];
  state.dateRange  = [...data.date_range];

  $('scope-rationale').textContent = data.overall_rationale;
  $('date-start').value = data.date_range[0] || '';
  $('date-end').value   = data.date_range[1] || '';

  renderFormTypeChips();
  renderCompanyGrid();
  show('scope-panel');
  $('scope-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderFormTypeChips() {
  const ALL = ['10-K', '10-Q', '8-K', '20-F', '6-K'];
  const container = $('form-types-editor');
  container.innerHTML = '';
  const row = document.createElement('div');
  row.className = 'form-type-chips';
  ALL.forEach(ft => {
    const chip = document.createElement('span');
    chip.className = 'chip' + (state.formTypes.includes(ft) ? ' active' : '');
    chip.textContent = ft;
    chip.onclick = () => {
      chip.classList.toggle('active');
      if (chip.classList.contains('active')) state.formTypes.push(ft);
      else state.formTypes = state.formTypes.filter(t => t !== ft);
    };
    row.appendChild(chip);
  });
  container.appendChild(row);
}

function renderCompanyGrid() {
  const grid = $('company-list');
  grid.innerHTML = '';
  $('company-count').textContent = state.companies.length;

  if (!state.companies.length) {
    grid.innerHTML = '<p class="empty-state">No companies in scope. Add tickers above.</p>';
    return;
  }
  state.companies.forEach((c, i) => {
    const card = document.createElement('div');
    card.className = 'company-card';
    card.innerHTML = `
      <button class="remove-btn" onclick="removeCompany(${i})">&#x2715;</button>
      <div class="ticker">${c.ticker || '—'}</div>
      <div class="company-name">${c.name || ''}</div>
      ${c.rationale ? `<div class="rationale">${c.rationale}</div>` : ''}
    `;
    grid.appendChild(card);
  });
}

function removeCompany(idx) {
  const r = state.companies.splice(idx, 1)[0];
  renderCompanyGrid();
  log(`Removed ${r.ticker} from scope`, 'warn');
}

async function addCompany() {
  const ticker = $('add-ticker-input').value.trim().toUpperCase();
  if (!ticker) return;
  if (state.companies.find(c => c.ticker === ticker)) { alert(`${ticker} already in scope.`); return; }

  $('add-ticker-input').value = '';
  // Optimistically add with empty CIK while resolving
  state.companies.push({ ticker, name: ticker, cik: '', rationale: 'Resolving…' });
  renderCompanyGrid();

  try {
    const res = await fetch(`/api/scope/resolve/${encodeURIComponent(ticker)}`);
    const info = res.ok ? await res.json() : {};
    const idx = state.companies.findIndex(c => c.ticker === ticker);
    if (idx !== -1) {
      state.companies[idx] = {
        ticker,
        name: info.name || ticker,
        cik: info.cik || '',
        rationale: info.found ? 'Manually added.' : 'CIK not found — will be skipped during ingestion.',
      };
      renderCompanyGrid();
      if (info.found) {
        log(`Added ${ticker} (${info.name}) — CIK resolved`, 'success');
      } else {
        log(`Added ${ticker} — WARNING: CIK not found, will be skipped during ingestion`, 'warn');
      }
    }
  } catch (_) {
    const idx = state.companies.findIndex(c => c.ticker === ticker);
    if (idx !== -1) state.companies[idx].rationale = 'Manually added (CIK unresolved).';
    renderCompanyGrid();
    log(`Added ${ticker} — could not resolve CIK`, 'warn');
  }
}

function rejectScope() {
  hide('scope-panel');
  state.proposalId = null;
  state.companies = [];
  log('Scope rejected', 'warn');
}

async function approveScope() {
  state.dateRange = [$('date-start').value, $('date-end').value];
  const payload = {
    proposal_id: state.proposalId,
    approved_companies: state.companies,
    form_types: state.formTypes,
    date_range: state.dateRange,
  };
  log(`Scope approved: ${state.companies.length} companies`, 'success');
  try {
    const res = await fetch('/api/scope/approve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Server error');
    await runIngestion();
  } catch (err) {
    log('Approval error: ' + err.message, 'error');
  }
}

/* ═══════════════════════════════════════════
   INGESTION (shared by both tabs)
   ═══════════════════════════════════════════ */
async function runIngestion() {
  show('ingestion-panel');
  hide('ingestion-results');
  show('ingestion-progress');
  hide('ask-question-area');
  $('ingestion-badge').textContent = 'Running';
  $('ingestion-badge').className = 'badge badge-yellow';
  $('ingestion-status-text').textContent = 'Fetching filings from SEC EDGAR…';
  $('ingestion-panel').scrollIntoView({ behavior: 'smooth' });
  log('Starting live ingestion…', 'info');

  try {
    const res = await fetch('/api/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proposal_id: state.proposalId }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Server error');
    const data = await res.json();
    renderIngestionResults(data);
  } catch (err) {
    $('ingestion-badge').textContent = 'Error';
    $('ingestion-badge').className = 'badge badge-red';
    $('ingestion-status-text').textContent = 'Ingestion failed: ' + err.message;
    log('Ingestion error: ' + err.message, 'error');
  }
}

function renderIngestionResults(data) {
  hide('ingestion-progress');
  show('ingestion-results');
  $('ingestion-badge').textContent = 'Complete';
  $('ingestion-badge').className = 'badge badge-green';

  const issues = data.issues || data.errors || [];
  $('ingestion-stats').innerHTML = `
    <div class="stat-card"><div class="stat-value">${data.filings_ingested ?? 0}</div><div class="stat-label">Filings</div></div>
    <div class="stat-card"><div class="stat-value">${data.chunks_created ?? 0}</div><div class="stat-label">Chunks</div></div>
    <div class="stat-card"><div class="stat-value">${issues.length}</div><div class="stat-label">Issues</div></div>
  `;

  $('filing-list').innerHTML = (data.filings || []).map(f => {
    const url = edgarUrl(f.cik, f.accession_number);
    const link = url
      ? `<a class="sec-link" href="${url}" target="_blank" rel="noopener noreferrer">SEC ↗</a>`
      : '';

    return `
      <div class="filing-row">
        <span class="fticker">${f.company||''}</span>
        <span class="ftype">${f.form_type||''}</span>
        <span class="fdate">${f.filing_date||''}</span>
        <span class="fchunks">${f.chunks||0} chunks</span>
        ${link}
      </div>
    `;
  }).join('');

  if (issues.length) {
    show('ingestion-errors');
    $('ingestion-errors').innerHTML = issues.map(e => `<div class="error-item">${e}</div>`).join('');
  } else {
    hide('ingestion-errors');
  }

  log(`Ingestion complete: ${data.filings_ingested} filings, ${data.chunks_created} chunks`, 'success');

  // Pre-fill question with AI query (if AI tab) or leave blank for manual
  if (state.activeTab === 'ai') {
    $('answer-query-input').value = $('query-input').value.trim();
  }

  // Show company scope chips
  const tickers = state.companies.map(c => c.ticker).filter(Boolean);
  $('scope-ticker-chips').innerHTML = tickers.map(t => `<span class="scope-chip">${t}</span>`).join('');

  show('ask-question-area');
  loadHistory();
}

/* ═══════════════════════════════════════════
   QUESTION HISTORY
   ═══════════════════════════════════════════ */
async function loadHistory() {
  if (!state.proposalId) return;
  try {
    const res = await fetch(`/api/history/${state.proposalId}`);
    if (!res.ok) return;
    renderHistory(await res.json());
  } catch (_) {}
}

function renderHistory(history) {
  if (!history?.length) { hide('history-section'); return; }
  show('history-section');
  $('history-list').innerHTML = '';
  history.forEach(item => {
    const btn = document.createElement('button');
    btn.className = 'history-item';
    btn.innerHTML = `
      <span class="h-icon">&#128203;</span>
      <span class="h-text">${item.query}</span>
      <span class="h-cached">&#9889; cached</span>
    `;
    btn.onclick = () => {
      $('answer-query-input').value = item.query;
      generateAnswer(false);
    };
    $('history-list').appendChild(btn);
  });
}

/* ═══════════════════════════════════════════
   ANSWER GENERATION
   ═══════════════════════════════════════════ */
async function generateAnswer(forceRefresh = false) {
  const query = $('answer-query-input').value.trim();
  if (!query) { alert('Please enter a research question.'); return; }
  state.currentQuery = query;

  show('answer-panel');
  show('answer-loading');
  hide('overall-answer-section');
  hide('judge-section');
  hide('company-deep-dives-section');
  hide('coverage-section');
  hide('claims-section');
  hide('cached-badge');
  $('answer-panel').scrollIntoView({ behavior: 'smooth' });
  log(`Generating answer${forceRefresh ? ' (refresh)' : ''}…`, 'info');

  try {
    const res = await fetch(`/api/answer${forceRefresh ? '?refresh=true' : ''}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proposal_id: state.proposalId, query }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Server error');
    const data = await res.json();
    state.answer = data;

    if (data.from_cache) {
      show('cached-badge');
      log('Answer served from cache ⚡', 'success');
      // Log cached workflow stages so the activity log still shows them
      if (data.workflow?.stages) {
        data.workflow.stages.forEach(s => log(`[${s.name}] ${s.summary}`, 'info'));
      }
    } else {
      hide('cached-badge');
      const ans = getAnswerPayload(data);
      const auditClaims = getAuditClaims(data);
      const deepDives = ans.company_deep_dives || [];
      log(`Answer: ${auditClaims.length} audit claims, ${deepDives.length} company deep dives`, 'success');
      if (ans.judge_evaluation) {
        log(`Judge: ${ans.judge_evaluation.overall_verdict}, grounding ${ans.judge_evaluation.grounding}/5`, 'info');
      }
      // Log each workflow stage to the activity log
      if (data.workflow?.stages) {
        data.workflow.stages.forEach(s => log(`[${s.name}] ${s.summary}`, 'info'));
      }
      loadHistory();
    }
    renderAnswer(data);
  } catch (err) {
    hide('answer-loading');
    log('Answer error: ' + err.message, 'error');
  }
}

function renderAnswer(data) {
  hide('answer-loading');

  const ans = getAnswerPayload(data);
  const overall = ans.overall_answer || {};
  const keyPoints = overall.key_points || [];
  const deepDives = ans.company_deep_dives || [];
  const coverageNotes = ans.coverage_notes || [];
  const judge = ans.judge_evaluation || null;
  const claims = getAuditClaims(data);

  if (overall.summary || keyPoints.length) {
    show('overall-answer-section');
    $('overall-answer-summary').textContent = overall.summary || '';
    $('overall-answer-points').innerHTML = keyPoints.map(point => {
      const tickers = (point.supporting_tickers || []).length
        ? `<span class="overall-point-tickers">${point.supporting_tickers.join(', ')}</span>`
        : '';
      return `<li><span>${point.text}</span>${tickers}</li>`;
    }).join('');
  } else {
    hide('overall-answer-section');
  }

  if (judge) {
    const verdictClass = `judge-verdict-${judge.overall_verdict || 'mixed'}`;
    const riskClass = `judge-risk-${judge.overclaiming_risk || 'medium'}`;
    $('judge-verdict').className = `judge-verdict ${verdictClass}`;
    $('judge-verdict').textContent = `Verdict: ${(judge.overall_verdict || 'mixed').replace('_', ' ')}`;
    $('judge-summary').textContent = judge.summary || '';
    $('judge-overclaiming').className = `judge-risk ${riskClass}`;
    $('judge-overclaiming').textContent = `Overclaiming risk: ${judge.overclaiming_risk || 'medium'}`;
    $('judge-score-grid').innerHTML = [
      ['Helpfulness', judge.helpfulness],
      ['Clarity', judge.clarity],
      ['Grounding', judge.grounding],
      ['Citation quality', judge.citation_quality],
    ].map(([label, score]) => `
      <div class="judge-score-card">
        <span class="judge-score-label">${label}</span>
        <span class="judge-score-value">${score}/5</span>
      </div>
    `).join('');
    $('judge-strengths').innerHTML = (judge.strengths || []).map(item => `<li>${item}</li>`).join('') || '<li>No strengths noted.</li>';
    $('judge-concerns').innerHTML = (judge.concerns || []).map(item => `<li>${item}</li>`).join('') || '<li>No major concerns noted.</li>';
    show('judge-section');
  } else {
    hide('judge-section');
  }

  if (deepDives.length) {
    show('company-deep-dives-section');
    $('company-deep-dives-list').innerHTML = deepDives.map(buildCompanyDeepDiveCard).join('');
  } else {
    hide('company-deep-dives-section');
  }

  if (coverageNotes.length) {
    show('coverage-section');
    $('coverage-list').innerHTML = coverageNotes.map(g => `<li>${g}</li>`).join('');
  } else {
    hide('coverage-section');
  }

  const list = $('claims-list');
  list.innerHTML = '';
  if (!claims.length) {
    hide('claims-section');
  } else {
    claims.forEach(c => list.appendChild(buildClaimCard(c)));
    $('claims-audit-details').open = false;
    show('claims-section');
  }
}

function buildEvidenceDisclosure(items, label, emptyLabel = '') {
  if (!items || !items.length) {
    return emptyLabel ? `<p class="evidence-empty-note">${emptyLabel}</p>` : '';
  }

  return `
    <details class="evidence-disclosure">
      <summary class="evidence-disclosure-summary">
        <span>&#128269; ${label}</span>
        <span class="evidence-disclosure-count">${items.length}</span>
      </summary>
      <div class="evidence-disclosure-body">
        ${items.join('')}
      </div>
    </details>
  `;
}

function buildCompanyDeepDiveCard(dive) {
  const statusClass = dive.status === 'supported' ? 'deep-dive-supported' : 'deep-dive-insufficient';
  const statusLabel = dive.status === 'supported' ? 'Supported' : 'Insufficient Evidence';
  const evidenceItems = (dive.evidence || []).map(item => {
    const url = edgarUrl(item.cik, item.accession_number);
    const link = url
      ? `<a class="sec-link" href="${url}" target="_blank" rel="noopener noreferrer">SEC ↗</a>`
      : '';
    return `
      <div class="deep-dive-evidence-item">
        <div class="deep-dive-evidence-meta">
          ${item.company_ticker} &bull; ${item.form_type} &bull; ${item.filing_date} &bull; ${item.item_section}
          ${link}
        </div>
        <div class="deep-dive-evidence-text">${item.excerpt}</div>
      </div>
    `;
  });
  const evidence = buildEvidenceDisclosure(evidenceItems, 'Show evidence excerpts');

  const gaps = (dive.gaps || []).map(gap => `<li>${gap}</li>`).join('');

  return `
    <article class="deep-dive-card ${statusClass}">
      <div class="deep-dive-header">
        <div>
          <div class="deep-dive-ticker">${dive.ticker || '—'}</div>
          <div class="deep-dive-name">${dive.company_name || ''}</div>
        </div>
        <span class="deep-dive-status">${statusLabel}</span>
      </div>
      <p class="deep-dive-summary">${dive.summary || ''}</p>
      ${evidence}
      ${gaps ? `<ul class="deep-dive-gaps">${gaps}</ul>` : ''}
    </article>
  `;
}

function buildClaimCard(claim) {
  const card = document.createElement('div');
  card.className = 'claim-card';
  card.id = `claim-${claim.claim_id}`;

  const confClass = { high: 'conf-high', medium: 'conf-medium', low: 'conf-low' }[claim.confidence] || 'conf-medium';
  const pills = (claim.supporting_chunk_ids || []).map(cid =>
    `<span class="chunk-pill" onclick="toggleEvidence('${claim.claim_id}')" title="Show evidence">${cid}</span>`
  ).join('');

  card.innerHTML = `
    <div class="claim-header">
      <span class="claim-id">${claim.claim_id}</span>
      <span class="claim-text">${claim.text}</span>
      <span class="confidence-badge ${confClass}">${claim.confidence}</span>
    </div>
    ${pills ? `<div class="claim-chunks">${pills}</div>` : ''}
    <div class="evidence-drawer" id="evidence-${claim.claim_id}">
      <div class="evidence-loading"><div class="spinner" style="width:20px;height:20px;border-width:2px;margin:8px auto"></div></div>
    </div>
    <div class="verdict-controls">
      <button class="toggle-evidence-btn" onclick="toggleEvidence('${claim.claim_id}')">&#128269; Show evidence</button>
      <button class="verdict-btn v-confirmed"      onclick="submitVerdict('${claim.claim_id}','confirmed',this)">&#10003; Confirmed</button>
      <button class="verdict-btn v-needs_revision" onclick="submitVerdict('${claim.claim_id}','needs_revision',this)">&#9998; Needs Revision</button>
      <button class="verdict-btn v-hallucinated"   onclick="submitVerdict('${claim.claim_id}','hallucinated',this)">&#9747; Hallucinated</button>
    </div>
  `;
  return card;
}

async function toggleEvidence(claimId) {
  const drawer = $(`evidence-${claimId}`);
  drawer.classList.toggle('open');
  if (drawer.classList.contains('open') && drawer.querySelector('.evidence-loading')) {
    await loadEvidence(claimId);
  }
}

async function loadEvidence(claimId) {
  const claims = getAuditClaims(state.answer);
  const claim = claims.find(c => c.claim_id === claimId);
  if (!claim) return;
  const drawer = $(`evidence-${claimId}`);
  const items = await Promise.all((claim.supporting_chunk_ids || []).map(async cid => {
    try {
      const r = await fetch(`/api/chunk/${encodeURIComponent(cid)}`);
      return r.ok ? await r.json() : null;
    } catch { return null; }
  }));
  const valid = items.filter(Boolean);
  drawer.innerHTML = valid.length ? valid.map(c => {
    const url = edgarUrl(c.metadata.cik, c.metadata.accession_number);
    const link = url
      ? `<a class="sec-link" href="${url}" target="_blank" rel="noopener noreferrer">View full filing on SEC.gov ↗</a>`
      : '';

    return `
      <div class="evidence-item">
        <div class="evidence-meta">
          ${c.metadata.company_ticker} &bull; ${c.metadata.form_type} &bull; ${c.metadata.filing_date} &bull; ${c.metadata.item_section}
          ${link}
        </div>
        <div class="evidence-text">${c.text.slice(0,600)}${c.text.length>600?'…':''}</div>
      </div>
    `;
  }).join('') : '<p style="font-size:12px;color:var(--text-muted)">No evidence chunks found.</p>';
}

/* ═══════════════════════════════════════════
   COMPARE COMPANIES
   ═══════════════════════════════════════════ */
function toggleCompareForm(chip) {
  const type = chip.dataset.type;
  chip.classList.toggle('active');
  if (chip.classList.contains('active')) {
    if (!state.compareFormTypes.includes(type)) state.compareFormTypes.push(type);
  } else {
    state.compareFormTypes = state.compareFormTypes.filter(t => t !== type);
  }
}

function setCompareTimePreset(years) {
  const startId = 'compare-date-start';
  const endId = 'compare-date-end';
  if (years === 'custom') {
    $(startId).focus();
  } else {
    $(startId).value = yearsAgo(years);
    $(endId).value = today();
    state.compareDateRange = [$(startId).value, $(endId).value];
  }
  $('compare-time-presets').querySelectorAll('.time-preset-btn').forEach(btn => {
    btn.classList.toggle('active',
      (years === 'custom' && btn.textContent === 'Custom') ||
      (years !== 'custom' && btn.textContent === `${years}Y`)
    );
  });
}

function setCompareLookback(lookback) {
  state.compareLookback = lookback;
  $('compare-lookback-presets').querySelectorAll('.time-preset-btn').forEach(btn => {
    btn.classList.toggle('active', btn.textContent === lookback);
  });
}

async function runCompare(forceRefresh = false) {
  const tickerA = $('compare-ticker-a').value.trim().toUpperCase();
  const tickerB = $('compare-ticker-b').value.trim().toUpperCase();
  const query = $('compare-query-input').value.trim();
  const filingDateRange = [
    $('compare-date-start').value,
    $('compare-date-end').value,
  ];

  if (!tickerA || !tickerB) {
    alert('Enter two ticker symbols.');
    return;
  }
  if (tickerA === tickerB) {
    alert('Use two different ticker symbols.');
    return;
  }
  if (!query) {
    alert('Enter a comparison question.');
    return;
  }
  if (!state.compareFormTypes.length) {
    alert('Select at least one filing type.');
    return;
  }
  if (!filingDateRange[0] || !filingDateRange[1]) {
    alert('Choose a filing date range.');
    return;
  }

  show('compare-results-panel');
  show('compare-loading');
  hide('compare-summary-section');
  hide('compare-company-section');
  hide('compare-chart-section');
  hide('compare-events-section');
  hide('compare-cached-badge');
  $('compare-status').textContent = 'Running compare workflow…';
  $('compare-results-panel').scrollIntoView({ behavior: 'smooth' });
  log(`Comparing ${tickerA} vs ${tickerB}${forceRefresh ? ' (refresh)' : ''}…`, 'info');

  try {
    const res = await fetch(`/api/compare${forceRefresh ? '?refresh=true' : ''}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ticker_a: tickerA,
        ticker_b: tickerB,
        query,
        form_types: state.compareFormTypes,
        filing_date_range: filingDateRange,
        price_lookback: state.compareLookback,
      }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Server error');
    const data = await res.json();
    state.compare = data;
    $('compare-status').textContent = '';

    if (data.from_cache) {
      show('compare-cached-badge');
      log(`Compare served from cache for ${tickerA} vs ${tickerB}`, 'success');
    } else {
      hide('compare-cached-badge');
      log(`Compare complete: ${data.company_comparisons.length} company summaries, ${data.filing_events.length} filing events`, 'success');
    }
    renderCompare(data);
  } catch (err) {
    hide('compare-loading');
    $('compare-status').textContent = '';
    log('Compare error: ' + err.message, 'error');
  }
}

function renderCompare(data) {
  hide('compare-loading');

  $('compare-overall-summary').textContent = data.overall_summary || '';
  $('compare-similarities').innerHTML = (data.similarities || []).map(item => `<li>${item}</li>`).join('');
  $('compare-differences').innerHTML = (data.differences || []).map(item => `<li>${item}</li>`).join('');
  show('compare-summary-section');

  const comparisons = data.company_comparisons || [];
  $('compare-company-list').innerHTML = comparisons.map(buildCompareCompanyCard).join('');
  show('compare-company-section');

  renderCompareChart(data.stock_series || [], data.filing_events || []);
  show('compare-chart-section');

  $('compare-events-body').innerHTML = (data.filing_events || []).map(event => `
    <tr onclick="showCompareEventDetail('${compareEventId(event)}')" class="compare-event-row">
      <td>${event.ticker}</td>
      <td>${event.form_type || '—'}</td>
      <td>${event.filing_date || '—'}</td>
      <td>${event.trading_date || '—'}</td>
      <td class="${Number(event.return_1d) > 0 ? 'positive' : Number(event.return_1d) < 0 ? 'negative' : ''}">${formatPercent(event.return_1d)}</td>
      <td class="${Number(event.return_5d) > 0 ? 'positive' : Number(event.return_5d) < 0 ? 'negative' : ''}">${formatPercent(event.return_5d)}</td>
      <td class="${Number(event.return_30d) > 0 ? 'positive' : Number(event.return_30d) < 0 ? 'negative' : ''}">${formatPercent(event.return_30d)}</td>
    </tr>
  `).join('');
  if ((data.filing_events || []).length) {
    show('compare-events-section');
    showCompareEventDetail(compareEventId(data.filing_events[0]));
  } else {
    hide('compare-events-section');
  }
}

function buildCompareCompanyCard(comparison) {
  const statusClass = comparison.status === 'supported' ? 'deep-dive-supported' : 'deep-dive-insufficient';
  const statusLabel = comparison.status === 'supported' ? 'Supported' : 'Insufficient Evidence';
  const evidenceItems = (comparison.evidence || []).map(item => `
    <div class="deep-dive-evidence-item">
      <div class="deep-dive-evidence-meta">
        ${comparison.ticker} &bull; ${item.form_type} &bull; ${item.filing_date} &bull; ${item.item_section}
        <a class="sec-link" href="${item.sec_url}" target="_blank" rel="noopener noreferrer">SEC ↗</a>
      </div>
      <div class="deep-dive-evidence-text">${item.excerpt}</div>
    </div>
  `);
  const evidence = buildEvidenceDisclosure(evidenceItems, 'Show evidence excerpts');
  const gaps = (comparison.gaps || []).map(gap => `<li>${gap}</li>`).join('');

  return `
    <article class="deep-dive-card ${statusClass}">
      <div class="deep-dive-header">
        <div>
          <div class="deep-dive-ticker">${comparison.ticker}</div>
          <div class="deep-dive-name">${comparison.company_name}</div>
        </div>
        <span class="deep-dive-status">${statusLabel}</span>
      </div>
      <p class="deep-dive-summary">${comparison.summary || ''}</p>
      ${evidence}
      ${gaps ? `<ul class="deep-dive-gaps">${gaps}</ul>` : ''}
    </article>
  `;
}

function renderCompareChart(stockSeries, filingEvents) {
  if (state.compareChart) {
    state.compareChart.destroy();
    state.compareChart = null;
  }
  const canvas = $('compare-stock-chart');
  if (!canvas || typeof Chart === 'undefined') return;

  const colors = ['#2563eb', '#d97706'];
  const lineDatasets = stockSeries.map((series, idx) => ({
    type: 'line',
    label: `${series.ticker} indexed`,
    borderColor: colors[idx % colors.length],
    backgroundColor: colors[idx % colors.length],
    borderWidth: 2,
    tension: 0.15,
    pointRadius: 0,
    data: (series.points || []).map(point => ({ x: point.date, y: point.indexed_close })),
  }));

  const markerDatasets = stockSeries.map((series, idx) => {
    const pointMap = new Map((series.points || []).map(point => [point.date, point.indexed_close]));
    const points = (filingEvents || [])
      .map((event, eventIndex) => ({ event, eventIndex }))
      .filter(({ event }) => event.ticker === series.ticker && event.trading_date && pointMap.has(event.trading_date))
      .map(({ event, eventIndex }) => ({
        x: event.trading_date,
        y: pointMap.get(event.trading_date),
        eventIndex,
        label: `${event.ticker} ${event.form_type}`,
      }));

    return {
      type: 'scatter',
      label: `${series.ticker} filings`,
      borderColor: colors[idx % colors.length],
      backgroundColor: colors[idx % colors.length],
      pointStyle: 'triangle',
      pointRadius: 6,
      pointHoverRadius: 7,
      data: points,
      compareEventDataset: true,
    };
  });

  state.compareChart = new Chart(canvas.getContext('2d'), {
    data: {
      datasets: [...lineDatasets, ...markerDatasets],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'nearest', intersect: false },
      scales: {
        x: {
          type: 'category',
        },
        y: {
          title: {
            display: true,
            text: 'Indexed Close (Start = 100)',
          },
        },
      },
      plugins: {
        legend: { position: 'bottom' },
        tooltip: {
          callbacks: {
            label(context) {
              const raw = context.raw || {};
              if (raw.label) return `${raw.label}: ${context.formattedValue}`;
              return `${context.dataset.label}: ${context.formattedValue}`;
            },
          },
        },
      },
      onClick(_event, elements, chart) {
        if (!elements.length) return;
        const element = elements[0];
        const dataset = chart.data.datasets[element.datasetIndex];
        if (!dataset.compareEventDataset) return;
        const point = dataset.data[element.index];
        if (point && Number.isInteger(point.eventIndex)) {
          const eventRecord = (state.compare?.filing_events || [])[point.eventIndex];
          if (eventRecord) showCompareEventDetail(compareEventId(eventRecord));
        }
      },
    },
  });
}

function showCompareEventDetail(eventId) {
  const events = state.compare?.filing_events || [];
  const event = events.find(item => compareEventId(item) === eventId);
  if (!event) return;

  const detail = $('compare-event-detail');
  const excerptItems = (event.supporting_excerpts || []).map(item => `
    <div class="deep-dive-evidence-item">
      <div class="deep-dive-evidence-meta">
        ${event.ticker} &bull; ${item.form_type} &bull; ${item.filing_date} &bull; ${item.item_section}
        <a class="sec-link" href="${item.sec_url}" target="_blank" rel="noopener noreferrer">SEC ↗</a>
      </div>
      <div class="deep-dive-evidence-text">${item.excerpt}</div>
    </div>
  `);
  const excerpts = buildEvidenceDisclosure(
    excerptItems,
    'Show event evidence',
    'No compare excerpts from this filing were selected for the strategy summary.'
  );

  detail.innerHTML = `
    <div class="compare-event-detail-header">
      <div>
        <div class="deep-dive-ticker">${event.ticker}</div>
        <div class="compare-event-meta">${event.form_type} filed ${event.filing_date}${event.trading_date ? ` • trading day ${event.trading_date}` : ''}</div>
      </div>
      <a class="sec-link" href="${event.sec_url}" target="_blank" rel="noopener noreferrer">View filing</a>
    </div>
    <div class="compare-return-strip">
      <span>+1D ${formatPercent(event.return_1d)}</span>
      <span>+5D ${formatPercent(event.return_5d)}</span>
      <span>+30D ${formatPercent(event.return_30d)}</span>
    </div>
    ${event.acceptance_datetime ? `<div class="compare-acceptance-time">Accepted: ${event.acceptance_datetime}</div>` : ''}
    ${excerpts}
  `;
  show('compare-event-detail');
}

/* ═══════════════════════════════════════════
   CHANGE INTELLIGENCE
   ═══════════════════════════════════════════ */
function toggleChangeForm(chip) {
  const type = chip.dataset.type;
  chip.classList.toggle('active');
  if (chip.classList.contains('active')) {
    if (!state.changeFormTypes.includes(type)) state.changeFormTypes.push(type);
  } else {
    state.changeFormTypes = state.changeFormTypes.filter(t => t !== type);
  }
}

function setChangeTimePreset(years) {
  const startId = 'change-date-start';
  const endId = 'change-date-end';
  if (years === 'custom') {
    $(startId).focus();
  } else {
    $(startId).value = yearsAgo(years);
    $(endId).value = today();
    state.changeDateRange = [$(startId).value, $(endId).value];
  }
  $('change-time-presets').querySelectorAll('.time-preset-btn').forEach(btn => {
    btn.classList.toggle('active',
      (years === 'custom' && btn.textContent === 'Custom') ||
      (years !== 'custom' && btn.textContent === `${years}Y`)
    );
  });
}

function setChangeLookback(lookback) {
  state.changeLookback = lookback;
  $('change-lookback-presets').querySelectorAll('.time-preset-btn').forEach(btn => {
    btn.classList.toggle('active', btn.textContent.toUpperCase() === lookback.toUpperCase());
  });
}

async function runChangeIntelligence(forceRefresh = false) {
  const ticker = $('change-ticker').value.trim().toUpperCase();
  const query = $('change-query-input').value.trim();
  const filingDateRange = [
    $('change-date-start').value,
    $('change-date-end').value,
  ];
  const maxFilings = Number.parseInt($('change-max-filings').value, 10) || 3;

  if (!ticker) {
    alert('Enter a ticker symbol.');
    return;
  }
  if (!query) {
    alert('Enter a change lens or analysis question.');
    return;
  }
  if (!state.changeFormTypes.length) {
    alert('Select at least one filing type.');
    return;
  }
  if (!filingDateRange[0] || !filingDateRange[1]) {
    alert('Choose a filing date range.');
    return;
  }

  show('change-results-panel');
  show('change-loading');
  hide('change-summary-section');
  hide('change-timeline-section');
  hide('change-cards-section');
  hide('change-chart-section');
  hide('change-events-section');
  hide('change-cached-badge');
  $('change-status').textContent = 'Running change intelligence…';
  $('change-results-panel').scrollIntoView({ behavior: 'smooth' });
  log(`Running change intelligence for ${ticker}${forceRefresh ? ' (refresh)' : ''}…`, 'info');

  try {
    const res = await fetch(`/api/change-intelligence${forceRefresh ? '?refresh=true' : ''}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ticker,
        query,
        form_types: state.changeFormTypes,
        filing_date_range: filingDateRange,
        max_filings: maxFilings,
        price_lookback: state.changeLookback,
      }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Server error');
    const data = await res.json();
    state.change = data;
    state.changeWindowId = (data.comparison_windows || [])[0]?.window_id || '';
    $('change-status').textContent = '';

    if (data.from_cache) {
      show('change-cached-badge');
      log(`Change intelligence served from cache for ${ticker}`, 'success');
    } else {
      hide('change-cached-badge');
      log(`Change intelligence complete: ${data.change_cards.length} change cards across ${data.comparison_windows.length} windows`, 'success');
    }
    renderChangeIntelligence(data);
  } catch (err) {
    hide('change-loading');
    $('change-status').textContent = '';
    log('Change intelligence error: ' + err.message, 'error');
  }
}

function renderChangeIntelligence(data) {
  hide('change-loading');

  $('change-overall-summary').textContent = data.overall_summary || '';
  show('change-summary-section');

  const filingEvents = data.filing_events || [];
  if (filingEvents.length) {
    $('change-filings-list').innerHTML = filingEvents
      .slice()
      .sort((a, b) => (a.filing_date < b.filing_date ? 1 : -1))
      .map(event => {
        const url = event.sec_url || edgarUrl(event.cik, event.accession_number);
        const link = url
          ? `<a class="sec-link" href="${url}" target="_blank" rel="noopener noreferrer">SEC ↗</a>`
          : '';
        return `
          <div class="change-filing-row">
            <span class="change-filing-type">${event.form_type || '—'}</span>
            <span class="change-filing-date">${event.filing_date || '—'}</span>
            <span class="change-filing-accession">${event.accession_number || ''}</span>
            ${link}
          </div>
        `;
      }).join('');
    show('change-filings-section');
  } else {
    hide('change-filings-section');
  }

  const windows = data.comparison_windows || [];
  $('change-window-selector').innerHTML = windows.map(window => `
    <button class="change-window-chip ${state.changeWindowId === window.window_id ? 'active' : ''}" onclick="selectChangeWindow('${window.window_id}')">
      ${window.label}
    </button>
  `).join('');
  if (windows.length) show('change-timeline-section');
  else hide('change-timeline-section');

  renderChangeCards();
  renderChangeChart(data.stock_series || [], data.filing_events || []);
  if ((data.stock_series || []).length) show('change-chart-section');
  else hide('change-chart-section');

  renderChangeEvents();
}

function selectChangeWindow(windowId) {
  state.changeWindowId = windowId;
  renderChangeCards();
  renderChangeEvents();
  const windows = state.change?.comparison_windows || [];
  $('change-window-selector').innerHTML = windows.map(window => `
    <button class="change-window-chip ${state.changeWindowId === window.window_id ? 'active' : ''}" onclick="selectChangeWindow('${window.window_id}')">
      ${window.label}
    </button>
  `).join('');
}

function renderChangeCards() {
  const data = state.change;
  if (!data) return;
  const cards = (data.change_cards || []).filter(card => !state.changeWindowId || card.window_id === state.changeWindowId);
  if (!cards.length) {
    $('change-cards-list').innerHTML = '<div class="empty-state">No filing-backed changes were detected for the selected window.</div>';
  } else {
    $('change-cards-list').innerHTML = cards.map(buildChangeCard).join('');
  }
  show('change-cards-section');
}

function buildChangeCard(card) {
  const categoryLabel = card.category.replace(/_/g, ' ');
  const beforeEvidence = buildEvidenceDisclosure(
    (card.before_evidence || []).map(item => `
      <div class="deep-dive-evidence-item">
        <div class="deep-dive-evidence-meta">
          Before &bull; ${item.form_type} &bull; ${item.filing_date} &bull; ${item.item_section}
          <a class="sec-link" href="${item.sec_url}" target="_blank" rel="noopener noreferrer">SEC ↗</a>
        </div>
        <div class="deep-dive-evidence-text">${item.excerpt}</div>
      </div>
    `),
    'Show before evidence'
  );
  const afterEvidence = buildEvidenceDisclosure(
    (card.after_evidence || []).map(item => `
      <div class="deep-dive-evidence-item">
        <div class="deep-dive-evidence-meta">
          After &bull; ${item.form_type} &bull; ${item.filing_date} &bull; ${item.item_section}
          <a class="sec-link" href="${item.sec_url}" target="_blank" rel="noopener noreferrer">SEC ↗</a>
        </div>
        <div class="deep-dive-evidence-text">${item.excerpt}</div>
      </div>
    `),
    'Show after evidence'
  );

  return `
    <article class="change-card">
      <div class="change-card-header">
        <div class="change-card-badges">
          <span class="change-category-badge">${categoryLabel}</span>
          <span class="change-importance-badge importance-${card.importance}">${card.importance}</span>
          <span class="change-confidence-badge confidence-${card.confidence}">${card.confidence}</span>
        </div>
        <div class="change-card-range">${card.after_filing.form_type} ${card.after_filing.filing_date} vs ${card.before_filing.form_type} ${card.before_filing.filing_date}</div>
      </div>
      <p class="change-card-summary">${card.summary || ''}</p>
      <div class="change-evidence-grid">
        <div class="change-evidence-column">
          <div class="compare-subtitle">Before</div>
          ${beforeEvidence}
        </div>
        <div class="change-evidence-column">
          <div class="compare-subtitle">After</div>
          ${afterEvidence}
        </div>
      </div>
    </article>
  `;
}

function renderChangeChart(stockSeries, filingEvents) {
  if (state.changeChart) {
    state.changeChart.destroy();
    state.changeChart = null;
  }
  const canvas = $('change-stock-chart');
  if (!canvas || typeof Chart === 'undefined' || !stockSeries.length) return;

  const series = stockSeries[0];
  const pointMap = new Map((series.points || []).map(point => [point.date, point.indexed_close]));
  const lineDataset = {
    type: 'line',
    label: `${series.ticker} indexed`,
    borderColor: '#2563eb',
    backgroundColor: '#2563eb',
    borderWidth: 2,
    tension: 0.15,
    pointRadius: 0,
    data: (series.points || []).map(point => ({ x: point.date, y: point.indexed_close })),
  };
  const markerDataset = {
    type: 'scatter',
    label: `${series.ticker} filings`,
    borderColor: '#d97706',
    backgroundColor: '#d97706',
    pointStyle: 'triangle',
    pointRadius: 6,
    pointHoverRadius: 7,
    data: (filingEvents || [])
      .map((event, eventIndex) => ({ event, eventIndex }))
      .filter(({ event }) => event.trading_date && pointMap.has(event.trading_date))
      .map(({ event, eventIndex }) => ({
        x: event.trading_date,
        y: pointMap.get(event.trading_date),
        eventIndex,
        label: `${event.form_type} ${event.filing_date}`,
      })),
    compareEventDataset: true,
  };

  state.changeChart = new Chart(canvas.getContext('2d'), {
    data: { datasets: [lineDataset, markerDataset] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'nearest', intersect: false },
      scales: {
        x: { type: 'category' },
        y: { title: { display: true, text: 'Indexed Close (Start = 100)' } },
      },
      plugins: { legend: { position: 'bottom' } },
      onClick(_event, elements, chart) {
        if (!elements.length) return;
        const element = elements[0];
        const dataset = chart.data.datasets[element.datasetIndex];
        if (!dataset.compareEventDataset) return;
        const point = dataset.data[element.index];
        if (point && Number.isInteger(point.eventIndex)) {
          const eventRecord = (state.change?.filing_events || [])[point.eventIndex];
          if (eventRecord) showChangeEventDetail(compareEventId(eventRecord));
        }
      },
    },
  });
}

function renderChangeEvents() {
  const data = state.change;
  if (!data) return;
  const activeWindow = (data.comparison_windows || []).find(window => window.window_id === state.changeWindowId);
  const allowedAccessions = new Set(
    activeWindow ? [activeWindow.before_filing.accession_number, activeWindow.after_filing.accession_number] : []
  );
  const events = (data.filing_events || []).filter(event =>
    !allowedAccessions.size || allowedAccessions.has(event.accession_number)
  );
  $('change-events-body').innerHTML = events.map(event => `
    <tr onclick="showChangeEventDetail('${compareEventId(event)}')" class="compare-event-row">
      <td>${event.form_type || '—'}</td>
      <td>${event.filing_date || '—'}</td>
      <td>${event.trading_date || '—'}</td>
      <td class="${Number(event.return_1d) > 0 ? 'positive' : Number(event.return_1d) < 0 ? 'negative' : ''}">${formatPercent(event.return_1d)}</td>
      <td class="${Number(event.return_5d) > 0 ? 'positive' : Number(event.return_5d) < 0 ? 'negative' : ''}">${formatPercent(event.return_5d)}</td>
      <td class="${Number(event.return_30d) > 0 ? 'positive' : Number(event.return_30d) < 0 ? 'negative' : ''}">${formatPercent(event.return_30d)}</td>
    </tr>
  `).join('');
  if (events.length) {
    show('change-events-section');
    showChangeEventDetail(compareEventId(events[0]));
  } else {
    hide('change-events-section');
  }
}

function showChangeEventDetail(eventId) {
  const events = state.change?.filing_events || [];
  const event = events.find(item => compareEventId(item) === eventId);
  if (!event) return;

  const detail = $('change-event-detail');
  const excerpts = buildEvidenceDisclosure(
    (event.supporting_excerpts || []).map(item => `
      <div class="deep-dive-evidence-item">
        <div class="deep-dive-evidence-meta">
          ${item.form_type} &bull; ${item.filing_date} &bull; ${item.item_section}
          <a class="sec-link" href="${item.sec_url}" target="_blank" rel="noopener noreferrer">SEC ↗</a>
        </div>
        <div class="deep-dive-evidence-text">${item.excerpt}</div>
      </div>
    `),
    'Show event evidence',
    'No change-evidence excerpts were attached to this filing.'
  );

  detail.innerHTML = `
    <div class="compare-event-detail-header">
      <div>
        <div class="deep-dive-ticker">${state.change?.company?.ticker || ''}</div>
        <div class="compare-event-meta">${event.form_type} filed ${event.filing_date}${event.trading_date ? ` • trading day ${event.trading_date}` : ''}</div>
      </div>
      <a class="sec-link" href="${event.sec_url}" target="_blank" rel="noopener noreferrer">View filing</a>
    </div>
    <div class="compare-return-strip">
      <span>+1D ${formatPercent(event.return_1d)}</span>
      <span>+5D ${formatPercent(event.return_5d)}</span>
      <span>+30D ${formatPercent(event.return_30d)}</span>
    </div>
    ${excerpts}
  `;
  show('change-event-detail');
}

/* ═══════════════════════════════════════════
   CLAIM VERIFICATION
   ═══════════════════════════════════════════ */
async function submitVerdict(claimId, verdict, btn) {
  log(`Verdict — ${claimId}: ${verdict}`, verdict === 'confirmed' ? 'success' : verdict === 'hallucinated' ? 'error' : 'warn');
  try {
    await fetch('/api/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proposal_id: state.proposalId, claim_id: claimId, verdict }),
    });
  } catch (err) { log('Verify error: ' + err.message, 'error'); return; }

  const card = $(`claim-${claimId}`);
  card.className = `claim-card verdict-${verdict}`;
  card.querySelectorAll('.verdict-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}

/* ═══════════════════════════════════════════
   DATA MANAGEMENT
   ═══════════════════════════════════════════ */
function toggleDataPanel() {
  const body = $('data-mgmt-body');
  const chevron = $('data-mgmt-chevron');
  const isHidden = body.classList.contains('hidden');
  body.classList.toggle('hidden', !isHidden);
  chevron.textContent = isHidden ? '▲' : '▼';
  if (isHidden) loadDataStatus();
}

async function loadDataStatus() {
  try {
    const res = await fetch('/api/data/status');
    if (!res.ok) return;
    const data = await res.json();
    const container = $('data-status-rows');
    container.innerHTML = Object.values(data).map(d => {
      const hasData = d.size_mb > 0;
      return `
        <div class="data-status-row">
          <span class="data-status-label">${d.label}</span>
          <span class="data-status-size ${hasData ? 'has-data' : ''}">
            ${d.size_mb > 0 ? d.size_mb + ' MB' : 'empty'}
          </span>
        </div>
      `;
    }).join('');
  } catch (_) {}
}

async function clearData() {
  const targets = [];
  if ($('clear-vectors').checked)  targets.push('vectors');
  if ($('clear-cache').checked)    targets.push('cache');
  if ($('clear-sessions').checked) targets.push('sessions');
  if ($('clear-logs').checked)     targets.push('logs');

  if (!targets.length) {
    alert('Select at least one item to clear.');
    return;
  }

  const label = targets.join(', ');
  if (!confirm(`This will permanently delete: ${label}.\n\nYou will need to re-ingest filings before asking questions again. Continue?`)) return;

  const resultEl = $('clear-result');
  resultEl.textContent = 'Clearing…';
  resultEl.className = 'clear-result';

  try {
    const res = await fetch('/api/data/clear', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ targets }),
    });
    const data = await res.json();

    if (data.cleared.length) {
      resultEl.textContent = `✓ Cleared: ${data.cleared.join(', ')}`;
      resultEl.className = 'clear-result success';
      log(`Data cleared: ${data.cleared.join(', ')}`, 'warn');

      // Reset UI state if vectors or sessions were wiped
      if (data.cleared.includes('vectors') || data.cleared.includes('sessions')) {
        state.proposalId = null;
        state.answer = null;
        state.compare = null;
        state.change = null;
        if (state.compareChart) {
          state.compareChart.destroy();
          state.compareChart = null;
        }
        if (state.changeChart) {
          state.changeChart.destroy();
          state.changeChart = null;
        }
        hide('ingestion-panel');
        hide('answer-panel');
        hide('compare-results-panel');
        hide('change-results-panel');
        hide('scope-panel');
      }
    }
    if (data.errors.length) {
      resultEl.textContent += ` Errors: ${data.errors.join(', ')}`;
      resultEl.className = 'clear-result error';
    }

    // Refresh the size display
    await loadDataStatus();
  } catch (err) {
    resultEl.textContent = 'Error: ' + err.message;
    resultEl.className = 'clear-result error';
  }
}

/* ── Init: set default 3Y time frame on manual tab ── */
(function init() {
  const end   = today();
  const start = yearsAgo(3);
  $('manual-date-start').value = start;
  $('manual-date-end').value   = end;
  state.manualDateRange = [start, end];

  const compareEnd = today();
  const compareStart = yearsAgo(2);
  $('compare-date-start').value = compareStart;
  $('compare-date-end').value = compareEnd;
  state.compareDateRange = [compareStart, compareEnd];

  const changeEnd = today();
  const changeStart = yearsAgo(2);
  $('change-date-start').value = changeStart;
  $('change-date-end').value = changeEnd;
  state.changeDateRange = [changeStart, changeEnd];
})();

/* ═══════════════════════════════════════════
   MARKET GAP DISCOVERY
   ═══════════════════════════════════════════ */
const gapState = {
  proposalId: null,
  companies: [],
  formTypes: ['10-K', '20-F'],
  dateRange: [yearsAgo(3), today()],
  result: null,
};

function setGapTimePreset(years) {
  if (years === 'custom') {
    $('gap-date-start').focus();
  } else {
    $('gap-date-start').value = yearsAgo(years);
    $('gap-date-end').value   = today();
    gapState.dateRange = [$('gap-date-start').value, $('gap-date-end').value];
  }
  document.querySelectorAll('#gap-scope-panel .time-preset-btn').forEach(btn => {
    btn.classList.toggle('active',
      (years === 'custom' && btn.textContent === 'Custom') ||
      (years !== 'custom' && btn.textContent === `${years}Y`)
    );
  });
}

function renderGapFormTypeChips() {
  const ALL = ['10-K', '10-Q', '8-K', '20-F', '6-K'];
  const container = $('gap-form-types-editor');
  container.innerHTML = '';
  const row = document.createElement('div');
  row.className = 'form-type-chips';
  ALL.forEach(ft => {
    const chip = document.createElement('span');
    chip.className = 'chip' + (gapState.formTypes.includes(ft) ? ' active' : '');
    chip.textContent = ft;
    chip.onclick = () => {
      chip.classList.toggle('active');
      if (chip.classList.contains('active')) gapState.formTypes.push(ft);
      else gapState.formTypes = gapState.formTypes.filter(t => t !== ft);
    };
    row.appendChild(chip);
  });
  container.appendChild(row);
}

function renderGapCompanyGrid() {
  const grid = $('gap-company-list');
  grid.innerHTML = '';
  $('gap-company-count').textContent = gapState.companies.length;
  if (!gapState.companies.length) {
    grid.innerHTML = '<p class="empty-state">No companies in scope.</p>';
    return;
  }
  gapState.companies.forEach((c, i) => {
    const card = document.createElement('div');
    card.className = 'company-card';
    card.innerHTML = `
      <button class="remove-btn" onclick="removeGapCompany(${i})">&#x2715;</button>
      <div class="ticker">${c.ticker || '—'}</div>
      <div class="company-name">${c.name || ''}</div>
      ${c.rationale ? `<div class="rationale">${c.rationale}</div>` : ''}
    `;
    grid.appendChild(card);
  });
}

function removeGapCompany(idx) {
  const r = gapState.companies.splice(idx, 1)[0];
  renderGapCompanyGrid();
  log(`Removed ${r.ticker} from gap scope`, 'warn');
}

async function addGapCompany() {
  const ticker = $('gap-add-ticker-input').value.trim().toUpperCase();
  if (!ticker) return;
  if (gapState.companies.find(c => c.ticker === ticker)) { alert(`${ticker} already in scope.`); return; }
  $('gap-add-ticker-input').value = '';
  gapState.companies.push({ ticker, name: ticker, cik: '', rationale: 'Resolving…' });
  renderGapCompanyGrid();
  try {
    const res = await fetch(`/api/scope/resolve/${encodeURIComponent(ticker)}`);
    const info = res.ok ? await res.json() : {};
    const idx = gapState.companies.findIndex(c => c.ticker === ticker);
    if (idx !== -1) {
      gapState.companies[idx] = {
        ticker,
        name: info.name || ticker,
        cik: info.cik || '',
        rationale: info.found ? 'Manually added.' : 'CIK not found — will be skipped.',
      };
      renderGapCompanyGrid();
      log(`Added ${ticker} (${info.name || '?'}) to gap scope`, info.found ? 'success' : 'warn');
    }
  } catch (_) {
    const idx = gapState.companies.findIndex(c => c.ticker === ticker);
    if (idx !== -1) gapState.companies[idx].rationale = 'Manually added (CIK unresolved).';
    renderGapCompanyGrid();
  }
}

function rejectGapScope() {
  hide('gap-scope-panel');
  gapState.proposalId = null;
  gapState.companies = [];
  log('Gap scope rejected', 'warn');
}

async function proposeGapScope() {
  const query = $('gap-query-input').value.trim();
  if (!query) { alert('Describe an industry or sector first.'); return; }

  const btn = $('gap-propose-btn');
  btn.disabled = true;
  btn.textContent = 'Discovering companies…';
  log('Proposing gap scope for: ' + query.slice(0, 60), 'info');

  try {
    const res = await fetch('/api/scope/propose-gap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Server error');
    const data = await res.json();

    gapState.proposalId = data.proposal_id;
    gapState.companies  = data.companies.map(c => ({ ...c }));
    gapState.formTypes  = [...data.form_types];
    gapState.dateRange  = [...data.date_range];

    $('gap-scope-rationale').textContent = data.overall_rationale;
    $('gap-date-start').value = data.date_range[0] || '';
    $('gap-date-end').value   = data.date_range[1] || '';

    renderGapFormTypeChips();
    renderGapCompanyGrid();
    show('gap-scope-panel');
    $('gap-scope-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
    log(`Gap scope: ${data.companies.length} companies proposed`, 'success');
  } catch (err) {
    log('Gap scope error: ' + err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '🔍 Discover Companies';
  }
}

async function runMarketGap(forceRefresh = false) {
  const query = $('gap-query-input').value.trim();
  if (!query) { alert('Enter an industry description.'); return; }
  if (!gapState.companies.length) { alert('No companies in scope.'); return; }
  if (!gapState.formTypes.length) { alert('Select at least one filing type.'); return; }
  const dr = [$('gap-date-start').value, $('gap-date-end').value];
  if (!dr[0] || !dr[1]) { alert('Set a date range.'); return; }

  show('gap-results-panel');
  show('gap-loading');
  hide('gap-summary-section');
  hide('gap-memos-section');
  hide('gap-clusters-section');
  hide('gap-coverage-section');
  hide('gap-cached-badge');
  $('gap-status').textContent = 'Analyzing market gaps…';
  $('gap-results-panel').scrollIntoView({ behavior: 'smooth' });
  log(`Running market gap analysis${forceRefresh ? ' (refresh)' : ''}…`, 'info');

  try {
    const res = await fetch(`/api/market-gap${forceRefresh ? '?refresh=true' : ''}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query,
        companies: gapState.companies,
        form_types: gapState.formTypes,
        filing_date_range: dr,
      }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Server error');
    const data = await res.json();
    gapState.result = data;
    $('gap-status').textContent = '';

    if (data.from_cache) {
      show('gap-cached-badge');
      log('Market gap served from cache ⚡', 'success');
    } else {
      hide('gap-cached-badge');
      log(`Market gap complete: ${data.gap_clusters.length} clusters, ${(data.opportunity_memos || []).length} memos`, 'success');
    }
    renderMarketGap(data);
  } catch (err) {
    hide('gap-loading');
    $('gap-status').textContent = '';
    log('Market gap error: ' + err.message, 'error');
  }
}

function renderMarketGap(data) {
  hide('gap-loading');

  $('gap-industry-summary').textContent   = data.industry_summary || '';
  $('gap-market-structure').textContent   = data.market_structure_summary || '';
  show('gap-summary-section');

  const memos = data.opportunity_memos || [];
  if (memos.length) {
    $('gap-memos-list').innerHTML = memos.map(memo => buildOpportunityMemoCard(memo, data.gap_clusters || [])).join('');
    show('gap-memos-section');
  } else {
    hide('gap-memos-section');
  }

  const clusters = data.gap_clusters || [];
  if (clusters.length) {
    $('gap-clusters-list').innerHTML = clusters.map(buildGapClusterCard).join('');
    show('gap-clusters-section');
  } else {
    hide('gap-clusters-section');
  }

  const notes = data.coverage_notes || [];
  if (notes.length) {
    $('gap-coverage-list').innerHTML = notes.map(n => `<li>${n}</li>`).join('');
    show('gap-coverage-section');
  } else {
    hide('gap-coverage-section');
  }
}

function buildGapClusterCard(cluster) {
  const freqPct = Math.round((cluster.frequency / Math.max(cluster.total_companies, 1)) * 100);
  const confClass = { high: 'conf-high', medium: 'conf-medium', low: 'conf-low' }[cluster.confidence] || 'conf-medium';
  const stuckConf = cluster.incumbents_stuck_confidence || 'low';
  const stuckLabel = {
    high: '🔒 Strong lock-in',
    medium: '⚠️ Partial constraint',
    low: '❓ Weak constraint',
    insufficient: '✗ No clear structural barrier',
  }[stuckConf] || stuckConf;
  const stuckClass = {
    high: 'stuck-high',
    medium: 'stuck-medium',
    low: 'stuck-low',
    insufficient: 'stuck-none',
  }[stuckConf] || 'stuck-low';

  const tickers = (cluster.company_tickers || []).map(t => `<span class="scope-chip">${t}</span>`).join('');
  const financial = cluster.financial_scale_estimate
    ? `<div class="gap-meta-item"><span class="gap-meta-label">Financial scale</span><span class="gap-meta-value">${cluster.financial_scale_estimate}</span></div>`
    : '';
  const buyerOwners = (cluster.buyer_owners || []).length
    ? `<div class="gap-meta-item"><span class="gap-meta-label">Buyer owner</span><span class="gap-meta-value">${cluster.buyer_owners.join(', ')}</span></div>`
    : '';
  const timing = cluster.urgency_level || cluster.persistence_level
    ? `<div class="gap-meta-item"><span class="gap-meta-label">Urgency / persistence</span><span class="gap-meta-value">${cluster.urgency_level || '—'} / ${cluster.persistence_level || '—'}</span></div>`
    : '';

  const painEvidence = buildEvidenceDisclosure(
    (cluster.pain_points || []).map(pp => `
      <div class="deep-dive-evidence-item">
        <div class="deep-dive-evidence-meta">
          ${pp.company_ticker} &bull; ${pp.form_type} &bull; ${pp.filing_date} &bull; ${pp.category} &bull; severity: ${pp.severity}
          ${edgarUrl(pp.cik, pp.accession_number) ? `<a class="sec-link" href="${edgarUrl(pp.cik, pp.accession_number)}" target="_blank" rel="noopener noreferrer">SEC ↗</a>` : ''}
        </div>
        <div class="deep-dive-evidence-text">${pp.text}</div>
        <div class="gap-point-meta">Owner: ${pp.buyer_owner_hint || 'unknown'} &bull; Recurrence: ${pp.recurrence_hint || 'unclear'} &bull; Chunks: ${(pp.chunk_ids || []).join(', ')}</div>
      </div>
    `),
    `Show ${cluster.evidence_count} pain point${cluster.evidence_count !== 1 ? 's' : ''}`
  );
  const constraints = buildEvidenceDisclosure([
    ...(cluster.hard_constraints || []).map(item => `<div class="gap-constraint-item"><span class="gap-constraint-kind">Hard</span><span>${item}</span></div>`),
    ...(cluster.soft_constraints || []).map(item => `<div class="gap-constraint-item"><span class="gap-constraint-kind soft">Soft</span><span>${item}</span></div>`),
  ], 'Show structural constraints');
  const caveats = (cluster.disconfirming_evidence || []).length
    ? `<ul class="failure-modes-list">${cluster.disconfirming_evidence.map(item => `<li>${item}</li>`).join('')}</ul>`
    : '';

  return `
    <article class="gap-cluster-card gap-conf-${cluster.confidence}">
      <div class="gap-cluster-header">
        <div class="gap-cluster-title-row">
          <span class="gap-cluster-theme">${cluster.theme}</span>
          <span class="confidence-badge ${confClass}">${cluster.confidence}</span>
        </div>
        <div class="gap-freq-row">
          <div class="gap-freq-bar-wrap">
            <div class="gap-freq-bar" style="width:${freqPct}%"></div>
          </div>
          <span class="gap-freq-label">${cluster.frequency}/${cluster.total_companies} companies &bull; ${freqPct}%</span>
        </div>
      </div>
      <p class="gap-cluster-desc">${cluster.description}</p>
      <div class="gap-meta-row">
        <div class="gap-meta-item"><span class="gap-meta-label">Latest filing</span><span class="gap-meta-value">${cluster.latest_filing_date || '—'}</span></div>
        ${financial}
        ${buyerOwners}
        ${timing}
        <div class="gap-meta-item"><span class="gap-meta-label">Score</span><span class="gap-meta-value">${cluster.cluster_score.toFixed(2)}</span></div>
      </div>
      <div class="gap-companies-row">${tickers}</div>
      ${painEvidence}
      <div class="gap-stuck-block ${stuckClass}">
        <div class="gap-stuck-label">${stuckLabel}</div>
        <div class="gap-stuck-reason">${cluster.incumbents_stuck_reason || 'No analysis available.'}</div>
      </div>
      ${constraints}
      ${cluster.why_now ? `<div class="opp-section"><div class="opp-section-label">Why now</div><p>${cluster.why_now}</p></div>` : ''}
      ${caveats ? `<div class="opp-section"><div class="opp-section-label">Caveats</div>${caveats}</div>` : ''}
    </article>
  `;
}

function buildOpportunityMemoCard(memo, clusters) {
  const cluster = (clusters || []).find(item => item.cluster_id === memo.target_cluster_id) || null;
  const statusMeta = {
    strong:               { label: '● Strong',               cls: 'opp-strong' },
    plausible:            { label: '● Plausible',            cls: 'opp-plausible' },
    speculative:          { label: '◐ Speculative',          cls: 'opp-speculative' },
    no_clear_opportunity: { label: '○ No Clear Entrant Case', cls: 'opp-none' },
  }[memo.opportunity_status] || { label: memo.opportunity_status, cls: 'opp-speculative' };

  const failureModes = (memo.why_this_may_fail || []).map(f => `<li>${f}</li>`).join('');
  const scoreItems = [
    ['Score', memo.opportunity_score?.toFixed ? memo.opportunity_score.toFixed(2) : memo.opportunity_score],
    ['Type', memo.opportunity_type?.replace(/_/g, ' ') || 'other'],
    ['Buyer', memo.buyer_owner || 'unknown'],
    ['Severity', memo.pain_severity || 'moderate'],
    ['Urgency', memo.urgency_level || 'medium'],
    ['Adoption', memo.adoption_difficulty || 'medium'],
  ].map(([label, value]) => `
    <div class="opp-score-item">
      <span class="opp-score-label">${label}</span>
      <span class="opp-score-value">${value}</span>
    </div>
  `).join('');
  const evidenceItems = (cluster?.pain_points || [])
    .filter(pp => (memo.evidence_chunk_ids || []).some(cid => (pp.chunk_ids || []).includes(cid)))
    .map(pp => `
      <div class="deep-dive-evidence-item">
        <div class="deep-dive-evidence-meta">
          ${pp.company_ticker} &bull; ${pp.form_type} &bull; ${pp.filing_date}
          ${edgarUrl(pp.cik, pp.accession_number) ? `<a class="sec-link" href="${edgarUrl(pp.cik, pp.accession_number)}" target="_blank" rel="noopener noreferrer">SEC ↗</a>` : ''}
        </div>
        <div class="deep-dive-evidence-text">${pp.text}</div>
        <div class="gap-point-meta">Chunks: ${(pp.chunk_ids || []).join(', ')}</div>
      </div>
    `);
  const evidence = buildEvidenceDisclosure(evidenceItems, 'Show memo evidence', 'No memo evidence captured.');

  return `
    <article class="gap-opportunity-card ${statusMeta.cls}">
      <div class="opp-header">
        <span class="opp-status-badge ${statusMeta.cls}">${statusMeta.label}</span>
        <span class="opp-title">${memo.title}</span>
      </div>
      <p class="opp-rationale">Filing-grounded opportunity memo &bull; hypothesis, not validation</p>
      <p class="opp-status-note">${memo.status_rationale || ''}</p>
      <div class="opp-score-grid">${scoreItems}</div>
      <div class="opp-section">
        <div class="opp-section-label">Problem</div>
        <p>${memo.problem || ''}</p>
      </div>
      <p class="opp-description">${memo.thesis || ''}</p>
      <div class="opp-section">
        <div class="opp-section-label">Why incumbents are stuck</div>
        <p>${memo.why_incumbents_are_stuck || 'No clear structural barrier identified.'}</p>
      </div>
      ${memo.why_now ? `
        <div class="opp-section">
          <div class="opp-section-label">Why now</div>
          <p>${memo.why_now}</p>
        </div>
      ` : ''}
      <div class="opp-section">
        <div class="opp-section-label">Why this may fail</div>
        <ul class="failure-modes-list">${failureModes}</ul>
      </div>
      ${evidence}
    </article>
  `;
}

/* ── Keyboard shortcuts ── */
document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') proposeScope();
});
