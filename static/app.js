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

function addCompany() {
  const ticker = $('add-ticker-input').value.trim().toUpperCase();
  if (!ticker) return;
  if (state.companies.find(c => c.ticker === ticker)) { alert(`${ticker} already in scope.`); return; }
  state.companies.push({ ticker, name: ticker, cik: '', rationale: 'Manually added.' });
  $('add-ticker-input').value = '';
  renderCompanyGrid();
  log(`Added ${ticker} to scope`, 'success');
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

function buildCompanyDeepDiveCard(dive) {
  const statusClass = dive.status === 'supported' ? 'deep-dive-supported' : 'deep-dive-insufficient';
  const statusLabel = dive.status === 'supported' ? 'Supported' : 'Insufficient Evidence';
  const evidence = (dive.evidence || []).map(item => {
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
  }).join('');

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
      ${evidence ? `<div class="deep-dive-evidence-list">${evidence}</div>` : ''}
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
        hide('ingestion-panel');
        hide('answer-panel');
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
})();

/* ── Keyboard shortcuts ── */
document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') proposeScope();
});
