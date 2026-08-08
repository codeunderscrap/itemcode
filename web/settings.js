/* Item Code Studio — Settings screen (Agent B).
 * Admin only. This is where Anuraag pastes the LLM API key himself; nothing
 * here is hardcoded and nothing is committed (agents/CONTRACTS.md decision
 * 11). The key is a password field and is never sent back to the browser
 * once saved - the server always answers with a masked placeholder or an
 * empty string (routes/auth.py MASK / SECRET_KEYS).
 */
const $ = s => document.querySelector(s);
const MASK = '••••••••';

const PROVIDER_LABELS = { none: 'None (fuzzy only)', anthropic: 'Anthropic', gemini: 'Google Gemini', openai: 'OpenAI', ollama: 'Ollama (local)', grok: 'Grok (xAI)', groq: 'Groq' };
const PROVIDER_MODEL_HINT = { none: '', anthropic: 'claude-haiku-4-5-20251001', gemini: 'gemini-2.0-flash', openai: 'gpt-4o-mini', ollama: 'llama3.1', grok: 'grok-4', groq: 'llama-3.3-70b-versatile' };

async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
  });
  const d = await r.json().catch(() => ({ ok: false, error: { message: 'bad response from server' } }));
  return { status: r.status, ...d };
}

function locked(html) {
  $('#main').innerHTML = `<div class="locked">${html}</div>`;
}

function render(settings) {
  const keySet = !!settings.llm_key_set;
  $('#main').innerHTML = `
    <h1>Settings</h1>
    <p class="lead">Admin only. Changes take effect immediately for every desktop client.</p>

    <div class="banner ${keySet && settings['match.mode'] === 'llm' ? 'ready show' : (keySet ? '' : 'fuzzy show')}" id="modeBanner">
      ${keySet
        ? (settings['match.mode'] === 'llm'
            ? 'LLM-assisted matching is on.'
            : 'A key is set, but fuzzy matching only is currently selected below.')
        : 'Fuzzy matching only — no API key set. The app runs perfectly well like this.'}
    </div>

    <div class="card">
      <h2>Matching</h2>
      <p class="hint">Rules always build the shortlist and hold the veto. The LLM only selects from that shortlist, or answers "none".</p>
      <div class="row">
        <div class="field">
          <label for="matchMode">Matching mode</label>
          <select id="matchMode">
            <option value="fuzzy">Fuzzy only</option>
            <option value="llm">LLM-first, fuzzy fallback</option>
          </select>
        </div>
        <div class="field">
          <label for="threshold">Fuzzy confidence threshold</label>
          <input id="threshold" type="number" min="0" max="100" step="1">
        </div>
      </div>
    </div>

    <div class="card">
      <h2>LLM provider</h2>
      <p class="hint">The key is stored on this server only, never in config.json, never in git, never sent back to a browser once saved.</p>
      <div class="row">
        <div class="field">
          <label for="provider">Provider</label>
          <select id="provider">
            ${Object.entries(PROVIDER_LABELS).map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}
          </select>
        </div>
        <div class="field">
          <label for="model">Model</label>
          <input id="model" placeholder="sensible default per provider">
        </div>
      </div>
      <div class="row">
        <div class="field">
          <label for="apiKey">API key</label>
          <input id="apiKey" type="password" placeholder="${keySet ? MASK : 'paste the key here'}" autocomplete="off">
        </div>
      </div>
      <div class="btnrow">
        <button class="ghost" id="testBtn" type="button">Test key</button>
        <span class="testresult" id="testResult"></span>
      </div>
    </div>

    <div class="card">
      <h2>ERPNext</h2>
      <p class="hint">Item access only — no delete, no cancel, no transactions.</p>
      <div class="toggle"><input id="erpEnabled" type="checkbox"><label for="erpEnabled" style="margin:0">Enabled</label></div>
      <div class="toggle"><input id="erpDryRun" type="checkbox"><label for="erpDryRun" style="margin:0">Dry-run (no real writes)</label></div>
      <div class="row">
        <div class="field">
          <label for="erpUrl">Base URL</label>
          <input id="erpUrl" placeholder="https://your-site.erpnext.com">
        </div>
      </div>
    </div>

    <div class="card">
      <h2>Sync</h2>
      <div class="row">
        <div class="field">
          <label for="syncTimes">Daily sync times (24h, comma-separated)</label>
          <input id="syncTimes" placeholder="09:00,17:00">
        </div>
      </div>
    </div>

    <div class="btnrow">
      <button class="primary" id="saveBtn" type="button">Save changes</button>
      <span class="savemsg" id="saveMsg"></span>
    </div>
  `;

  $('#matchMode').value = settings['match.mode'];
  $('#threshold').value = settings['match.threshold'];
  $('#provider').value = settings['llm.provider'];
  $('#model').value = settings['llm.model'] || '';
  $('#erpEnabled').checked = !!settings['erp.enabled'];
  $('#erpDryRun').checked = !!settings['erp.dry_run'];
  $('#erpUrl').value = settings['erp.base_url'] || '';
  $('#syncTimes').value = settings['sync.times'] || '09:00,17:00';

  $('#provider').addEventListener('change', () => {
    if (!$('#model').value) $('#model').placeholder = PROVIDER_MODEL_HINT[$('#provider').value] || '';
  });

  $('#testBtn').addEventListener('click', async () => {
    const out = $('#testResult');
    out.className = 'testresult';
    out.textContent = 'testing…';
    const body = { 'llm.provider': $('#provider').value, 'llm.model': $('#model').value };
    const typed = $('#apiKey').value.trim();
    if (typed) body['llm.api_key'] = typed;
    const d = await api('/api/v1/settings/test-llm', { method: 'POST', body: JSON.stringify(body) });
    if (!d.ok) { out.className = 'testresult bad'; out.textContent = (d.error && d.error.message) || 'request failed'; return; }
    if (d.success) { out.className = 'testresult ok'; out.textContent = 'key works'; }
    else { out.className = 'testresult bad'; out.textContent = d.detail || 'failed'; }
  });

  $('#saveBtn').addEventListener('click', async () => {
    const btn = $('#saveBtn');
    btn.disabled = true;
    $('#saveMsg').textContent = '';
    const body = {
      'match.mode': $('#matchMode').value,
      'match.threshold': Number($('#threshold').value),
      'llm.provider': $('#provider').value,
      'llm.model': $('#model').value,
      'erp.enabled': $('#erpEnabled').checked,
      'erp.dry_run': $('#erpDryRun').checked,
      'erp.base_url': $('#erpUrl').value,
      'sync.times': $('#syncTimes').value,
    };
    const typedKey = $('#apiKey').value; // '' means "leave unchanged" unless explicitly cleared below
    if (typedKey.trim()) body['llm.api_key'] = typedKey.trim();
    const d = await api('/api/v1/settings', { method: 'POST', body: JSON.stringify(body) });
    btn.disabled = false;
    if (!d.ok) {
      $('#saveMsg').style.color = 'var(--mm-bad)';
      $('#saveMsg').textContent = (d.error && d.error.message) || 'could not save';
      return;
    }
    $('#saveMsg').style.color = 'var(--mm-good)';
    $('#saveMsg').textContent = 'saved';
    $('#apiKey').value = '';
    render(d.settings);
  });
}

async function boot() {
  const me = await api('/api/v1/auth/me');
  if (!me.ok || !me.user) { location.href = '/login.html'; return; }
  if (!me.user.is_admin) { locked('Settings is admin only. Ask an administrator for access.'); return; }

  const s = await api('/api/v1/settings');
  if (!s.ok) { locked((s.error && s.error.message) || 'could not load settings'); return; }
  render(s.settings);
}

boot();
