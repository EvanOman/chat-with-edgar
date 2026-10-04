const API = '/api/edgar';
const $ = (id) => document.getElementById(id);
const state = { status: null, snapshot: null, company: null, filing: null, filings: [], conversation: null, busy: false, controller: null, completedRuns: [], selection: 0 };
const adapterNotes = {
  direct: 'A small application-owned tool loop over the Responses API.',
  'pydantic-ai': 'Pydantic AI orchestration with an explicit adapter for the plan-usage transport.',
  'openai-agents': 'OpenAI Agents SDK orchestration with an adapted model transport; not the hosted Agents API.',
};

function node(tag, attrs = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'class') element.className = value;
    else if (key === 'text') element.textContent = value;
    else element.setAttribute(key, String(value));
  }
  for (const child of children.flat()) if (child != null) element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return element;
}
function notice(message) { $('global-notice').textContent = message; $('global-notice').hidden = !message; }
function safeSource(url) {
  try { const parsed = new URL(url); return parsed.protocol === 'https:' && ['www.sec.gov', 'sec.gov', 'data.sec.gov'].includes(parsed.hostname) && !parsed.username && !parsed.password ? parsed.href : null; } catch { return null; }
}
function sourceLink(label, url, className = '') {
  const safe = safeSource(url);
  return safe ? node('a', { href: safe, target: '_blank', rel: 'noopener noreferrer', class: className }, label) : node('span', { class: className }, label);
}
function errorMessage(payload, fallback) { return payload?.error?.message || payload?.detail?.message || (typeof payload?.detail === 'string' ? payload.detail : null) || payload?.message || fallback; }
async function api(path, body, options = {}) {
  const response = await fetch(`${API}${path}`, { method: body === undefined ? 'GET' : 'POST', headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) }, credentials: 'same-origin', cache: 'no-store', ...(body === undefined ? {} : { body: JSON.stringify(body) }), ...options });
  const payload = await response.json().catch(() => null);
  if (!response.ok || payload?.ok === false) throw new Error(errorMessage(payload, `Request failed (${response.status}).`));
  return payload;
}
async function tool(name, args) { return api('/tools', { name, arguments: args }); }
function tab(id) {
  document.querySelectorAll('[role="tab"]').forEach((button) => { const active = button.dataset.tab === id; button.setAttribute('aria-selected', String(active)); button.tabIndex = active ? 0 : -1; });
  document.querySelectorAll('[role="tabpanel"]').forEach((panel) => { panel.hidden = panel.id !== id; });
}
document.querySelectorAll('[role="tab"]').forEach((button) => {
  button.addEventListener('click', () => tab(button.dataset.tab));
  button.addEventListener('keydown', (event) => {
    const tabs = [...document.querySelectorAll('[role="tab"]')];
    const index = tabs.indexOf(button);
    const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
    if (next !== null) { event.preventDefault(); tabs[next].click(); tabs[next].focus(); }
  });
});
$('compare-link').addEventListener('click', () => tab('comparison'));

function renderStatus() {
  const status = state.status;
  const authenticated = Boolean(status?.authenticated);
  const enabled = authenticated && Boolean(status?.inference_enabled);
  const hosted = status?.deployment !== 'local';
  $('intro').replaceChildren('Start with the filing. Follow the evidence.', node('br'), hosted ? 'Explore excerpts while hosted chat awaits approval.' : 'Ask questions with your own eligible ChatGPT plan.');
  const strip = document.querySelector('.access-strip');
  strip.classList.toggle('connected', enabled);
  $('sign-in').hidden = !status?.auth_available || authenticated;
  $('sign-out').hidden = !authenticated;
  $('account-controls').hidden = !status?.auth_available || !(status?.registrations?.length);
  $('enable-plan').hidden = !authenticated || enabled;
  $('chat-gate').hidden = enabled;
  $('question').disabled = !enabled || state.busy;
  $('send').disabled = !enabled || state.busy || !$('model').value || $('model').disabled;
  if (enabled) {
    $('access-title').textContent = `Connected as ${status.account?.email || status.account?.label || 'your ChatGPT account'}`;
    $('access-description').textContent = `${status.account?.plan ? `${status.account.plan} · ` : ''}Requests use this account’s eligible allowance. Remaining balance is not reported. Tokens stay on the local server.`;
  } else if (authenticated) {
    $('access-title').textContent = 'Signed in · plan usage is not enabled';
    $('access-description').textContent = 'Identity sign-in does not authorize inference. Enable plan usage with app-specific consent to continue.';
  } else if (status?.auth_available) {
    $('access-title').textContent = 'Local app · connect your own ChatGPT account';
    $('access-description').textContent = 'Sign in with app-specific consent. Inference uses your eligible plan allowance; no operator-funded fallback.';
  } else {
    $('access-title').textContent = status?.gate?.title || 'Hosted ChatGPT access awaiting approval';
    $('access-description').textContent = status?.gate?.message || 'Explore real SEC filings now. Hosted sign-in and plan-funded answers require separate OpenAI access approval.';
  }
  const gateTitle = authenticated ? 'Your account has not authorized plan usage.' : hosted ? 'Hosted ChatGPT access is awaiting approval.' : 'Connect your ChatGPT account to ask a question.';
  $('chat-gate').replaceChildren(node('strong', {}, gateTitle), node('p', {}, hosted ? 'The public filing explorer is available. This site cannot run inference until its hosted integration is approved. The documented local app is a separate deployment and requires your consent.' : 'This local application uses your own eligible plan allowance. It will stop when authorization or allowance is unavailable.'), node('a', { href: 'https://github.com/EvanOman/chat-with-edgar#run-locally' }, 'Local setup and current access details ↗'));
  $('source-mode').textContent = hosted ? 'PUBLIC SNAPSHOT' : 'SEC EDGAR';
  document.getElementById('usage-link')?.remove();
  if (authenticated && status.usage_url === 'https://chatgpt.com/settings/usage') {
    $('access-description').append(' ', node('a', { id: 'usage-link', href: status.usage_url, target: '_blank', rel: 'noopener noreferrer' }, 'View ChatGPT usage ↗'));
  }
  const registrations = status?.registrations || [];
  $('registration').replaceChildren(...registrations.map((r) => node('option', { value: r.registration_id }, r.email || r.label || 'App registration')));
  if (status?.active_registration) $('registration').value = status.active_registration;
  if (Array.isArray(status?.adapters)) {
    for (const entry of status.adapters) { const option = [...$('adapter').options].find((o) => o.value === entry.id); if (option) { option.disabled = entry.available === false; if (entry.label) option.textContent = entry.label; } }
  }
  $('comparison-evidence').textContent = state.completedRuns.length ? `${state.completedRuns.length} completed inference run${state.completedRuns.length === 1 ? '' : 's'} in this browser session. These measurements are from your requests, not a controlled benchmark.` : 'No inference has run in this browser session. Model behavior and latency are unverified until a real authorized request completes.';
}
async function refreshStatus() {
  try {
    state.status = await api('/status');
    renderStatus();
    if (state.status.authenticated && state.status.inference_enabled) {
      const result = await api('/models');
      const models = result.models || result.data || [];
      $('model').replaceChildren(...models.map((m) => { const id = typeof m === 'string' ? m : m.id || m.slug; return node('option', { value: id }, typeof m === 'string' ? m : m.label || m.display_name || id); }));
      $('model').disabled = !models.length;
      if (!models.length) notice('This account did not return any eligible models. No model request will be sent.');
    } else {
      $('model').replaceChildren(node('option', { value: '' }, 'Sign in to load eligible models')); $('model').disabled = true;
    }
    renderStatus();
  } catch (error) {
    state.status = { deployment: 'unknown', authenticated: false, auth_available: false, gate: { title: 'Account service unavailable', message: 'The access check failed. Chat is disabled; public source files remain available.' } };
    renderStatus(); notice(error.message);
  }
}
async function signIn(options = {}) {
  try {
    notice('');
    const result = await api('/auth/start', options);
    const url = new URL(result.url);
    if (url.protocol !== 'https:' || url.hostname !== 'auth.openai.com' || url.username || url.password) throw new Error('The server returned an unexpected authorization URL. Sign-in stopped.');
    window.location.assign(url.href);
  } catch (error) { notice(error.message); }
}
$('sign-in').addEventListener('click', () => signIn());
$('add-account').addEventListener('click', () => signIn());
$('reconnect').addEventListener('click', () => signIn({ registration_id: $('registration').value }));
$('enable-plan').addEventListener('click', () => signIn({ registration_id: $('registration').value, reconsent: true }));
$('sign-out').addEventListener('click', async () => { try { state.controller?.abort(); await api('/auth/logout', {}); resetConversation(); await refreshStatus(); } catch (error) { notice(error.message); } });
$('registration').addEventListener('change', async () => { try { state.controller?.abort(); await api('/auth/select', { registration_id: $('registration').value }); resetConversation(); await refreshStatus(); } catch (error) { notice(error.message); } });

function renderCompanies(companies) {
  $('companies').replaceChildren(...companies.map((company) => { const button = node('button', { class: 'company-button', type: 'button', 'aria-pressed': company.cik === state.company?.cik, title: company.name }, company.ticker || company.name); button.addEventListener('click', () => selectCompany(company)); return button; }));
  if (!companies.length) $('companies').append(node('p', { class: 'muted' }, 'No companies matched this source collection.'));
}
function snapshotCompanies(query) { const text = query.trim().toLowerCase(); return (state.snapshot?.companies || []).filter((c) => [c.name, c.ticker, c.cik].some((value) => String(value).toLowerCase().includes(text))); }
async function loadSnapshot() {
  try {
    const response = await fetch(new URL('../data/snapshot.json', import.meta.url), { cache: 'no-cache' });
    if (!response.ok) throw new Error('Public filing snapshot is unavailable.');
    state.snapshot = await response.json();
    renderCompanies(state.snapshot.companies || []);
    $('coverage-note').textContent = state.status?.deployment === 'local' ? 'Search recent SEC filings and retrieve source passages on demand. The starter companies come from the public snapshot; company search uses SEC data.' : state.snapshot.description || 'A bounded snapshot of SEC filings. Search covers only the included documents.';
    if (state.snapshot.companies?.length) await selectCompany(state.snapshot.companies[0]);
  } catch (error) { $('companies').replaceChildren(node('p', { class: 'muted' }, 'Source collection unavailable.')); notice(error.message); }
}
$('company-search').addEventListener('submit', async (event) => {
  event.preventDefault();
  const query = $('company-query').value.trim();
  try {
    notice('');
    if (state.status?.deployment === 'local') {
      const result = await tool('search_companies', { query, limit: 10 }); renderCompanies(result.data || []);
    } else renderCompanies(snapshotCompanies(query));
  } catch (error) { notice(error.message); }
});
async function selectCompany(company) {
  const selection = ++state.selection;
  state.company = company; state.filing = null;
  document.querySelectorAll('.company-button').forEach((button) => button.setAttribute('aria-pressed', String(button.title === company.name)));
  $('filings').replaceChildren(node('p', { class: 'muted' }, 'Loading filings…'));
  try {
    let filings;
    if (state.status?.deployment === 'local') { const selectedForm = $('form-filter').value; const forms = selectedForm === 'S-1' ? ['S-1', 'S-1/A'] : selectedForm ? [selectedForm] : ['10-K', '10-Q', 'S-1', '8-K']; const result = await tool('list_filings', { cik: String(company.cik), forms, limit: 12 }); filings = result.data || []; }
    else filings = company.filings || state.snapshot?.companies.find((c) => c.cik === company.cik)?.filings || [];
    if (selection !== state.selection) return;
    state.filings = filings;
    renderFilings();
  } catch (error) { if (selection === state.selection) { $('filings').replaceChildren(node('p', { class: 'muted' }, error.message)); } }
}
function renderFilings() {
  const filter = $('form-filter').value;
  const filings = state.filings.filter((f) => !filter || f.form === filter || (filter === 'S-1' && f.form === 'S-1/A'));
  $('filings').replaceChildren(...filings.map((filing) => {
    const button = node('button', { type: 'button', class: 'filing-button', 'aria-pressed': 'false', 'data-accession': filing.accession }, node('span', { class: 'filing-kind' }, filing.form, node('time', {}, filing.filed)), node('small', {}, filing.report_date ? `Period ending ${filing.report_date}` : 'Registration statement'));
    button.addEventListener('click', () => selectFiling(filing)); return button;
  }));
  if (filings.length) selectFiling(filings[0]);
  else { state.filing = null; $('filings').append(node('p', { class: 'muted' }, 'No matching filings in this collection.')); $('reader-heading').replaceChildren(node('h2', {}, 'No matching filings.')); $('passages').replaceChildren(); $('passage-search').hidden = true; }
}
$('form-filter').addEventListener('change', () => { if (state.status?.deployment === 'local' && state.company) selectCompany(state.company); else renderFilings(); });
async function selectFiling(filing) {
  state.filing = filing;
  document.querySelectorAll('.filing-button').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.accession === filing.accession)));
  $('filing-date').textContent = `FILED ${filing.filed}`;
  $('reader-heading').replaceChildren(node('h2', {}, state.company.name), node('div', { class: 'reader-meta' }, node('span', {}, `${filing.form} / ${filing.form === '10-K' ? 'Annual report' : filing.form === '10-Q' ? 'Quarterly report' : filing.form.startsWith('S-1') ? 'Registration statement' : 'Current report'}`), node('span', {}, filing.report_date ? `Period ending ${filing.report_date}` : filing.accession)), sourceLink('Read the original SEC filing ↗', filing.url, 'filing-source'), node('p', { class: 'provenance-line' }, `Accession ${filing.accession} · ${state.status?.deployment === 'local' ? 'Application-side SEC retrieval' : `Snapshot captured ${state.snapshot?.generated_at?.slice(0, 10) || 'at build time'}. Excerpts are partial; use the original filing for full context.`}`));
  $('passage-search').hidden = false; $('passage-query').value = '';
  await loadPassages('');
}
async function loadPassages(query) {
  const filing = state.filing;
  if (!filing) return;
  $('passages').replaceChildren(node('p', { class: 'muted' }, 'Retrieving source passages…'));
  try {
    let passages;
    if (state.status?.deployment === 'local') { const result = await tool('filing_passages', { cik: String(state.company.cik), accession: filing.accession, document: filing.document, query: query || 'business revenue risks', limit: 5 }); passages = result.data || []; }
    else {
      const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
      passages = (filing.passages || []).map((p) => ({ ...p, matchScore: terms.reduce((n, term) => n + (p.text.toLowerCase().includes(term) ? 1 : 0), 0) })).filter((p) => !terms.length || p.matchScore > 0).sort((a, b) => b.matchScore - a.matchScore).slice(0, 6);
    }
    if (state.filing !== filing) return;
    $('passages').replaceChildren(...passages.map((passage, index) => node('article', { class: 'passage' }, node('div', { class: 'passage-label' }, node('span', {}, `EXCERPT ${String(index + 1).padStart(2, '0')}`), node('span', {}, passage.id || passage.citation?.id || filing.form)), node('blockquote', {}, passage.text), sourceLink(passage.citation?.label || `${state.company.name} · ${filing.form} · SEC EDGAR ↗`, passage.citation?.url || filing.url))));
    if (!passages.length) $('passages').append(node('p', { class: 'empty-state' }, 'No matching passages in the retrieved excerpts. Try a broader term or open the original filing. This is not evidence that the disclosure is absent.'));
  } catch (error) { if (state.filing === filing) $('passages').replaceChildren(node('p', { class: 'empty-state' }, error.message)); }
}
$('passage-search').addEventListener('submit', (event) => { event.preventDefault(); loadPassages($('passage-query').value.trim()); });

function resetConversation() { state.conversation = null; $('thread').replaceChildren(); $('conversation-note').textContent = 'Your own plan · No operator-funded fallback'; }
$('new-chat').addEventListener('click', () => { state.controller?.abort(); resetConversation(); });
$('adapter').addEventListener('change', () => { $('adapter-note').textContent = adapterNotes[$('adapter').value] || ''; if (state.conversation) $('conversation-note').textContent = 'Continuing this conversation with the selected implementation'; });
$('model').addEventListener('change', () => { resetConversation(); $('conversation-note').textContent = 'Model changed · Starting a new conversation'; });
$('stop-chat').addEventListener('click', () => state.controller?.abort());
function setBusy(busy) {
  state.busy = busy; $('stop-chat').hidden = !busy; $('adapter').disabled = busy; $('model').disabled = busy || !state.status?.inference_enabled; $('new-chat').disabled = busy;
  $('registration').disabled = busy; $('add-account').disabled = busy; $('reconnect').disabled = busy; renderStatus();
}
async function consumeSSE(response, onEvent) {
  if (!response.body) throw new Error('The response did not contain a stream.');
  if (!(response.headers.get('content-type') || '').includes('text/event-stream')) throw new Error('The server did not return an event stream.');
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
  function parseFrame(frame) {
    let event = 'message'; const lines = [];
    for (const line of frame.split('\n')) { if (line.startsWith('event:')) event = line.slice(6).trim(); else if (line.startsWith('data:')) lines.push(line.slice(5).trimStart()); }
    if (!lines.length) return;
    const raw = lines.join('\n');
    if (raw === '[DONE]') return; // Transport EOF is never proof of a completed answer.
    const data = JSON.parse(raw); onEvent(event === 'message' && data.type ? data.type : event, data);
  }
  try {
    while (true) { const { value, done } = await reader.read(); buffer = (buffer + decoder.decode(value, { stream: !done })).replace(/\r\n/g, '\n'); if (buffer.length > 2_000_000) throw new Error('The event stream exceeded the permitted frame size.'); let boundary; while ((boundary = buffer.indexOf('\n\n')) !== -1) { parseFrame(buffer.slice(0, boundary)); buffer = buffer.slice(boundary + 2); } if (done) { if (buffer.trim()) parseFrame(buffer); break; } }
  } finally { reader.releaseLock(); }
}
function displayMetrics(result, fallbackMs, toolCount) {
  let usage = result.usage || {};
  if (Array.isArray(usage.responses)) {
    const allReported = usage.responses.length > 0 && usage.responses.every((entry) => entry && Number.isFinite(entry.input_tokens) && Number.isFinite(entry.output_tokens));
    usage = allReported ? usage.responses.reduce((sum, entry) => ({ input_tokens: sum.input_tokens + entry.input_tokens, output_tokens: sum.output_tokens + entry.output_tokens }), { input_tokens: 0, output_tokens: 0 }) : {};
  }
  const reported = Number.isFinite(usage.total_tokens) ? `${usage.total_tokens.toLocaleString()} tokens` : Number.isFinite(usage.input_tokens) && Number.isFinite(usage.output_tokens) ? `${usage.input_tokens.toLocaleString()} in / ${usage.output_tokens.toLocaleString()} out` : 'Token usage not reported';
  return `${((result.latency_ms ?? fallbackMs) / 1000).toFixed(1)}s elapsed · ${result.telemetry?.tool_calls ?? toolCount} tool call${(result.telemetry?.tool_calls ?? toolCount) === 1 ? '' : 's'} · ${reported}`;
}
$('chat-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const question = $('question').value.trim();
  if (!question || state.busy || !state.status?.authenticated || !state.status?.inference_enabled || $('model').disabled) return;
  notice(''); setBusy(true);
  const controller = new AbortController(); state.controller = controller;
  const user = node('article', { class: 'message user' }, node('h3', { class: 'message-heading' }, 'YOU'), node('p', { class: 'message-body' }, question));
  const status = node('span', { class: 'message-state' }, 'Connecting…');
  const body = node('p', { class: 'message-body' }); const events = node('ol', { class: 'tool-events' });
  const answer = node('article', { class: 'message assistant' }, node('h3', { class: 'message-heading' }, 'EDGAR', status), events, body);
  $('thread').append(user, answer); $('question').value = '';
  const adapter = $('adapter').value; const model = $('model').value; const started = performance.now(); let completed = false; let completion = null; let toolCount = 0;
  const filingContext = state.filing && state.company ? `User-selected research context: company ${state.company.name}; CIK ${state.company.cik}; form ${state.filing.form}; accession ${state.filing.accession}; document ${state.filing.document}.\n\n` : '';
  try {
    const response = await fetch(`${API}/chat`, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' }, body: JSON.stringify({ adapter, model, message: filingContext + question, ...(state.conversation ? { conversation_id: state.conversation } : {}) }), signal: controller.signal });
    if (!response.ok) { const payload = await response.json().catch(() => null); if (response.status === 401 || response.status === 403) await refreshStatus(); throw new Error(errorMessage(payload, `Chat request failed (${response.status}).`)); }
    status.textContent = 'Streaming · incomplete';
    await consumeSSE(response, (eventName, payload) => {
      if (completed) throw new Error('Received events after the completed answer.');
      if (eventName === 'token') body.append(document.createTextNode(payload.delta || ''));
      else if (eventName === 'tool') {
        if (payload.status === 'started' || payload.status === 'running' || payload.status === 'start' || payload.status === 'tool_start' || !payload.status) toolCount += 1;
        events.append(node('li', {}, `${payload.name || 'Retrieval tool'} · ${payload.status === 'tool_start' ? 'running' : payload.status === 'tool_end' ? payload.ok === false ? 'failed' : 'finished' : payload.status || 'requested'}`));
      } else if (eventName === 'completed') {
        completed = true; completion = payload;
      } else if (eventName === 'error') throw new Error(errorMessage(payload, 'The model request failed.'));
    });
    if (!completed) throw new Error('The stream ended before completion. This answer is incomplete.');
    if ((completion.text || completion.answer) && !body.textContent) body.textContent = completion.text || completion.answer;
    state.conversation = completion.conversation_id || null;
    status.textContent = 'Complete'; status.classList.add('complete');
    const citations = completion.citations || [];
    if (citations.length) answer.append(node('div', { class: 'answer-citations' }, ...citations.filter((c) => safeSource(c.url)).map((c) => sourceLink(c.label || c.id || 'SEC filing', c.url, 'citation'))));
    const metrics = displayMetrics(completion, performance.now() - started, toolCount);
    answer.append(node('p', { class: 'run-metrics' }, metrics));
    state.completedRuns.push({ adapter, model, metrics });
    $('run-results').append(node('div', { class: 'run-record' }, node('strong', {}, `${$('adapter').selectedOptions[0].textContent} · ${model}`), node('div', { class: 'run-metrics' }, metrics)));
    renderStatus();
  } catch (error) {
    status.textContent = controller.signal.aborted ? 'Stopped · incomplete' : 'Failed · incomplete'; status.classList.remove('complete'); status.classList.add('failed');
    answer.append(node('p', { class: 'run-metrics' }, controller.signal.aborted ? 'Request stopped. Partial text is not a completed answer.' : error.message));
    state.conversation = null;
  } finally { state.controller = null; setBusy(false); }
});

await refreshStatus();
await loadSnapshot();
if (window.location.hash === '#comparison') tab('comparison');
