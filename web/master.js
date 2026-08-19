/* Item Code Studio — item master, versions, revert & activity (Agent F).
 *
 * Loaded after app.js (index.html), and relies on its globals: $, $$, esc,
 * api, post, toast, modal, closeModal, BOOT, state. `loadMaster` and
 * `loadActivity` are what the nav dispatcher in app.js calls by name.
 *
 * Everything here is behind login already (index.html only renders once
 * app.js's loadMe() confirms a session), but every /api/v1 call the server
 * receives is re-checked with require_session regardless — hiding this
 * script is not the security boundary, routes/master.py's guard is.
 */

/* ─────────────────────────────────────────────────────── cascade state */
const mstate = {
  page: 1, head: '', sub: '', group: '', groupOptions: [],
  acUser: '', acCode: '', acFrom: '', acTo: '',
};

function fillSelect(sel, opts, placeholder) {
  sel.innerHTML = `<option value="">${placeholder}</option>` +
    opts.map(o => `<option value="${o.id}">${esc(o.label)}</option>`).join('');
}

function populateHeadOptions() {
  const sel = $('#mHead');
  fillSelect(sel, (BOOT.heads || []).map(h => ({ id: h.id, label: h.name })), 'any head');
  sel.value = mstate.head;
}

function populateSubOptions() {
  const sel = $('#mSub');
  const subs = (BOOT.subheads || []).filter(s => !mstate.head || String(s.head_id) === String(mstate.head));
  fillSelect(sel, subs.map(s => ({ id: s.id, label: `${s.head_name} / ${s.name}` })), 'any sub-head');
  sel.value = mstate.sub;
}

async function populateGroupOptions() {
  const sel = $('#mGroup');
  if (!mstate.sub) {
    mstate.groupOptions = [];
    fillSelect(sel, [], 'any group');
    return;
  }
  // Group browsing is Agent A's public read endpoint — the same list the
  // Dictionary tab uses — so this cascade never re-derives the taxonomy,
  // only filters it.
  const gs = await api('/api/groups?limit=999&sub_id=' + encodeURIComponent(mstate.sub));
  mstate.groupOptions = gs;
  fillSelect(sel, gs.map(g => ({ id: g.id, label: `${g.name} (${g.prefix})` })), 'any group');
  sel.value = mstate.group;
}

/* ─────────────────────────────────────────────────────────────── list */
// Cache ERP items across page changes (re-fetched when tab first loads)
let _erpItemsCache = null;
let _erpItemsFetching = false;

async function fetchErpItems() {
  if (_erpItemsFetching) return;
  _erpItemsFetching = true;
  try {
    const d = await api('/api/v1/erp-items').catch(() => ({ items: [] }));
    _erpItemsCache = d.items || [];
  } finally {
    _erpItemsFetching = false;
  }
}

function renderErpOnlyRow(item) {
  const hs = (item.head_name && item.subhead_name) 
             ? `<div class="p"><b>${esc(item.head_name)}</b></div><div class="s">${esc(item.subhead_name)}</div>` 
             : `<button class="ghost sm" style="font-size:11px; padding:3px 6px;" onclick="app.showMapErpGroupModal('${esc(item.item_group || '').replace(/'/g, "\\'")}')">+ Set Hierarchy</button>`;
  const specs = (item.specs_list && item.specs_list.length > 0)
             ? item.specs_list.map(esc).join('<br>')
             : `<span class="muted">—</span>`;
  return `<tr style="opacity:.8;background:var(--panel2,#1a1f2e)">
    <td><code>${esc(item.name || '')}</code></td>
    <td>${esc(item.item_name || item.name || '')}</td>
    <td>${hs}</td>
    <td>${esc(item.item_group || '–')}</td>
    <td>${specs}</td>
    <td>—</td><td>${esc(item.stock_uom || '')}</td><td>—</td>
    <td><span class="pill erp">ERP only</span></td>
    <td class="num muted">—</td>
    <td><span class="muted" style="font-size:11px">view in ERP</span></td>
  </tr>`;
}

async function loadMaster() {
  if (!$('#mHead').children.length || $('#mHead').children.length === 1) {
    populateHeadOptions();
    populateSubOptions();
  }
  const params = {
    q: $('#mq').value, status: $('#mstatus').value,
    undecodable: $('#mundec').checked ? '1' : '', page: mstate.page, size: 60,
  };
  if (mstate.head) params.head_id = mstate.head;
  if (mstate.sub) params.subhead_id = mstate.sub;
  if (mstate.group) params.group_id = mstate.group;
  const q = new URLSearchParams(params);

  // Fetch local items + trigger ERP fetch (non-blocking)
  const [d] = await Promise.all([
    api('/api/v1/item?' + q),
    _erpItemsCache === null ? fetchErpItems() : Promise.resolve(),
  ]);

  // Build set of codes already in local DB for deduplication
  const localCodes = new Set(d.rows.map(r => (r.code || '').toUpperCase()));

  // Filter ERP-only items (not in local DB) — apply search filter too
  const qStr = ($('#mq').value || '').toLowerCase();
  const erpOnly = (_erpItemsCache || []).filter(item => {
    const code = (item.name || '').toUpperCase();
    if (localCodes.has(code)) return false;  // already shown in local rows
    if (qStr && !code.toLowerCase().includes(qStr) &&
        !(item.item_name || '').toLowerCase().includes(qStr)) return false;
    return true;
  });

  const totalCount = d.total + erpOnly.length;
  const erpCount = (_erpItemsCache || []).length;
  $('#mTotal').textContent = `${d.total.toLocaleString()} items` +
    (erpCount ? ` · ${erpOnly.length} ERP-only` : '');
  $('#mPage').textContent = `page ${d.page} of ${Math.max(1, Math.ceil(d.total / d.size))}`;

  const localRows = d.rows.map(r => `<tr>
    <td><code>${esc(r.code)}</code></td>
    <td>${esc(r.name)}</td>
    <td>${esc(r.hname || '–')}<br><span class="muted">${esc(r.sname || '')}</span></td>
    <td>${esc(r.gname || '')}${r.group_id ? '' : '<span class="muted">unmapped</span>'}</td>
    <td>${[r.v1, r.v2, r.v3, r.v4].filter(Boolean).map(esc).join(' · ') || '<span class="muted">–</span>'}</td>
    <td>${esc(r.vv || '–')}</td><td>${esc(r.uom || '')}</td><td>${esc(r.hsn || '')}</td>
    <td><span class="pill ${r.status === 'in_erp' ? 'erp' : ''}">${r.status === 'in_erp' ? 'in ERPNext' : esc(r.status)}</span>
      ${r.decodable ? '' : '<span class="pill dr">stale code</span>'}</td>
    <td class="num muted">${r.version_no || 1}</td>
    <td><button class="ghost sm" data-edit="${esc(r.code)}">edit</button>
        ${r.status === 'confirmed' ? `<button class="primary sm" style="margin-left:4px" data-push="${esc(r.code)}">Push to ERP</button>` : ''}</td></tr>`);

  const erpRows = erpOnly.map(renderErpOnlyRow);

  const allRows = [...localRows, ...erpRows];
  $('#mTable tbody').innerHTML = allRows.join('') ||
    `<tr><td colspan="11" class="muted" style="padding:22px;text-align:center">no items match</td></tr>`;
  $$('[data-edit]').forEach(b => b.onclick = () => openItemModal(b.dataset.edit));
  $$('[data-push]').forEach(b => b.onclick = () => openPushReviewModal(b.dataset.push));
}

['mq', 'mstatus', 'mundec'].forEach(id => $('#' + id) && $('#' + id).addEventListener('input', () => {
  mstate.page = 1; loadMaster();
}));
$('#mHead') && ($('#mHead').onchange = () => {
  mstate.head = $('#mHead').value; mstate.sub = ''; mstate.group = '';
  populateSubOptions(); populateGroupOptions(); mstate.page = 1; loadMaster();
});
$('#mSub') && ($('#mSub').onchange = () => {
  mstate.sub = $('#mSub').value; mstate.group = '';
  populateGroupOptions().then(loadMaster);
  mstate.page = 1;
});
$('#mGroup') && ($('#mGroup').onchange = () => {
  mstate.group = $('#mGroup').value; mstate.page = 1; loadMaster();
});
$('#mPrev') && ($('#mPrev').onclick = () => { if (mstate.page > 1) { mstate.page--; loadMaster(); } });
$('#mNext') && ($('#mNext').onclick = () => { mstate.page++; loadMaster(); });

$('#btnExport') && ($('#btnExport').onclick = async () => {
  toast('Building workbook…');
  const d = await api('/api/v1/export');
  toast(`Exported ${d.file}`, 'ok');
  window.location = '/api/v1/download/' + encodeURIComponent(d.file);
});

/* ──────────────────────────────────────────────────── item edit modal */
const EDIT_FIELDS = [
  ['name', 'Item name'], ['description', 'Description'], ['uom', 'UoM'],
  ['alt_uom', 'Alternate UoM'], ['hsn', 'HSN / SAC'], ['tax', 'Tax template'],
  ['status', 'Status'],
];

async function openItemModal(code) {
  const d = await api('/api/v1/item/' + encodeURIComponent(code));
  const it = d.item;
  renderItemModal(it, 'edit');
}

function renderItemModal(it, tab) {
  const frozen = it.frozen_effective;
  const classCascade = `${esc(it.hname || '–')} (<code>${esc(it.hcode || '--')}</code>) → ${esc(it.sname || '–')} (<code>${esc(it.scode || '--')}</code>) → ${esc(it.gname || '–')} (<code>${esc(it.gcode || '---')}</code>)`;
  const specLine = (it.specs || []).filter(s => s.label)
    .map(s => `${esc(s.label)} <b>${esc(s.value || '—')}</b> (<code>${esc(s.code2 || '--')}</code>)`).join(' · ') || 'no specifications on this group';

  modal(`
    <h3><code style="font-size:15px">${esc(it.code)}</code></h3>
    <div class="sub">${classCascade} &nbsp;·&nbsp; ${specLine}</div>
    ${frozen ? `<div class="note" style="margin:0 0 12px">This code is live in ERPNext — it is frozen.
      Field edits still save; the code itself can never change.</div>` : ''}
    <div class="tabs" style="margin:0 0 12px">
      <button class="tab ${tab === 'edit' ? 'on' : ''}" data-mtab="edit">Edit</button>
      <button class="tab ${tab === 'history' ? 'on' : ''}" data-mtab="history">History (v${it.version_count || 1})</button>
    </div>
    <div id="mtBody"></div>
    <div class="row">
      <button class="ghost" id="mClose">Close</button>
      <button class="ghost" id="mEditGroup">Edit Group in Dictionary</button>
    </div>`);

  $('#mClose').onclick = closeModal;
  $('#mEditGroup').onclick = () => {
    closeModal();
    // Use the nav button to switch to dictionary view
    document.querySelector('.nav[data-view="dict"]').click();
    // Open the group in dictionary
    if (typeof openGroup === 'function') {
      openGroup(it.group_id);
    }
  };

  $$('[data-mtab]').forEach(b => b.onclick = () => renderItemModal(it, b.dataset.mtab));

  if (tab === 'edit') renderEditTab(it);
  else renderHistoryTab(it);
}

function renderEditTab(it) {
  $('#mtBody').innerHTML = `
    <div class="slots">
      ${EDIT_FIELDS.map(([k, l]) => k === 'status'
        ? `<div class="slot"><label>${l}</label>
            <select id="e_${k}">
              ${['draft', 'confirmed', 'in_erp'].map(v =>
                `<option value="${v}" ${it[k] === v ? 'selected' : ''}>${v === 'in_erp' ? 'live in ERPNext' : v}</option>`).join('')}
            </select><span class="cc"></span></div>`
        : `<div class="slot"><label>${l}</label><input id="e_${k}" value="${esc(it[k] || '')}"><span class="cc"></span></div>`
      ).join('')}
    </div>
    <div class="sub">The code, its head/sub-head/group and its four spec slots are never editable
      here — that classification is what the code encodes. Use the Dictionary screen's move/merge
      if an item is sitting under the wrong group.</div>
    <div class="row"><button class="primary" id="eSave">Save</button></div>`;

  $('#eSave').onclick = async () => {
    const body = {};
    EDIT_FIELDS.forEach(([k]) => body[k] = $('#e_' + k).value);
    try {
      const d = await post(`/api/v1/item/${encodeURIComponent(it.code)}/update`, body);
      if (!d.changed) { toast('Nothing changed', ''); return; }
      toast(`Saved as version ${d.version} — code unchanged`, 'ok');
      closeModal(); loadMaster();
    } catch (e) { toast(e.message, 'err'); }
  };
}

async function renderHistoryTab(it) {
  $('#mtBody').innerHTML = `<div class="empty" style="padding:24px"><b>Loading history…</b></div>`;
  const d = await api('/api/v1/versions?code=' + encodeURIComponent(it.code));
  $('#mtBody').innerHTML = `
    <div class="verlist">${d.versions.map(v => verRow(v, d.frozen, it.code)).join('')}</div>`;
  $$('[data-revert]', $('#mtBody')).forEach(b => b.onclick = () => doRevert(it.code, +b.dataset.revert, it));
}

function verRow(v, frozen, code) {
  const diffHtml = v.diff.length
    ? `<div class="difflist">${v.diff.map(f =>
        `<div><b>${esc(f.field)}</b>: ${esc(f.before ?? '—')} <span class="ar">→</span> ${esc(f.after ?? '—')}</div>`).join('')}</div>`
    : '<div class="muted" style="padding:6px 0">no fields recorded as different from the version before</div>';
  return `<div class="verrow">
    <div class="verhead">
      <b>v${v.version_no}</b>
      <span class="muted">${esc((v.changed_at || '').replace('T', ' '))} · ${esc(v.changed_by)}</span>
      <span class="grow"></span>
      <button class="ghost sm" data-revert="${v.version_no}">Revert to this version</button>
    </div>
    <div class="muted" style="padding:2px 0 6px">${esc(v.summary || '')}</div>
    ${diffHtml}
  </div>`;
}

async function doRevert(code, versionNo, itemForModal) {
  if (!confirm(`Revert ${code} to version ${versionNo}? This never deletes later versions — it adds a new one.`)) return;
  try {
    const d = await post('/api/v1/revert', { code, version_no: versionNo });
    toast(d.message, d.skipped_frozen && d.skipped_frozen.length ? '' : 'ok');
    loadMaster();
    if (itemForModal) {
      const fresh = await api('/api/v1/item/' + encodeURIComponent(code));
      renderItemModal(fresh.item, 'history');
    }
  } catch (e) { toast(e.message, 'err'); }
}

/* ═══════════════════════════════════════════════════════════ ACTIVITY */
function fmtDiff(diff) {
  if (!diff || !diff.length) return '<span class="muted">–</span>';
  return diff.slice(0, 4).map(f =>
    `<div><b>${esc(f.field)}</b>: ${esc(f.before ?? '—')} <span class="ar">→</span> ${esc(f.after ?? '—')}</div>`
  ).join('') + (diff.length > 4 ? `<div class="muted">+${diff.length - 4} more</div>` : '');
}

function matchedByBadge(mb) {
  if (!mb) return '';
  return `<span class="layer ${esc(mb)}">${esc(mb)}</span>`;
}

async function loadActivity() {
  const params = {};
  mstate.acUser = $('#acUser').value.trim();
  mstate.acCode = $('#acCode').value.trim().toUpperCase();
  mstate.acFrom = $('#acFrom').value;
  mstate.acTo = $('#acTo').value;
  if (mstate.acUser) params.user = mstate.acUser;
  if (mstate.acCode) params.code = mstate.acCode;
  if (mstate.acFrom) params.from = mstate.acFrom + 'T00:00:00';
  if (mstate.acTo) params.to = mstate.acTo + 'T23:59:59';
  params.limit = 150;

  const [act, mp, vac] = await Promise.all([
    api('/api/v1/audit?' + new URLSearchParams(params)),
    api('/api/mappings'),
    api('/api/v1/vacancies'),
  ]);

  $('#acTotal').textContent = `${act.total.toLocaleString()} event${act.total === 1 ? '' : 's'}`;
  $('#acTable tbody').innerHTML = act.events.map(e => `<tr>
      <td class="muted">${esc((e.ts || '').replace('T', ' '))}</td>
      <td>${esc(e.user)}</td>
      <td>${e.item_code ? `<code>${esc(e.item_code)}</code>` : '<span class="muted">–</span>'}
        ${e.item_name ? `<br><span class="muted">${esc(e.item_name)}</span>` : ''}</td>
      <td>${e.kind === 'version' ? fmtDiff(e.diff) : `<span class="muted">${esc(e.action)}${e.item_code ? '' : ''}</span>`}</td>
      <td>${matchedByBadge(e.matched_by)}</td>
      <td>${e.revertable ? `<button class="ghost sm" data-act-revert="${esc(e.item_code)}" data-act-v="${e.version_no}">Revert</button>` : ''}</td>
    </tr>`).join('') || '<tr><td colspan="6" class="muted" style="padding:20px;text-align:center">no activity matches these filters</td></tr>';
  $$('[data-act-revert]').forEach(b => b.onclick = () =>
    doRevert(b.dataset.actRevert, +b.dataset.actV, null).then(loadActivity));

  $('#cmTable tbody').innerHTML = mp.map(r => `<tr><td><code>${esc(r.old_code)}</code></td>
    <td><code>${esc(r.new_code)}</code></td><td>${esc(r.reason)}</td><td>${esc(r.user)}</td></tr>`).join('')
    || '<tr><td colspan="4" class="muted">nothing re-issued yet</td></tr>';

  // core.codes.list_vacancies (Agent D) shape: level, scope, prefix, number,
  // freed_by, freed_at — already identical for group- and item-level, so no
  // per-level branching is needed here. "number" is the next free number the
  // NEXT arrival gets (queue-claim, lowest-first), never "reserved for".
  $('#vTable tbody').innerHTML = (vac.groups || []).map(r => `<tr>
      <td><code>${esc(r.prefix || '')}${esc(r.number)}</code></td>
      <td>${esc(r.scope || '')}</td>
      <td>${esc(r.freed_by || '–')}</td>
      <td class="muted">${esc((r.freed_at || '').replace('T', ' '))}</td></tr>`).join('')
    || '<tr><td colspan="4" class="muted">no free numbers waiting — the next group starts a new one</td></tr>';

  $('#viTable tbody').innerHTML = (vac.items || []).map(r => `<tr>
      <td><code>${esc(r.prefix || '')}${esc(r.number)}</code></td>
      <td>${esc(r.scope || '')}</td>
      <td>${esc(r.freed_by || '–')}</td>
      <td class="muted">${esc((r.freed_at || '').replace('T', ' '))}</td></tr>`).join('')
    || '<tr><td colspan="4" class="muted">no free item positions waiting</td></tr>';
}

['acUser', 'acCode', 'acFrom', 'acTo'].forEach(id => $('#' + id) && $('#' + id).addEventListener('input', loadActivity));
$('#acClear') && ($('#acClear').onclick = () => {
  ['acUser', 'acCode', 'acFrom', 'acTo'].forEach(id => $('#' + id).value = '');
  loadActivity();
});

/* ───────────────────────────────────────────────────────── push review */
let _currentPushItem = null;
async function openPushReviewModal(code) {
  const d = await api('/api/v1/item/' + encodeURIComponent(code));
  if (!d.ok) { toast(d.error, 'err'); return; }
  const it = d.item;
  _currentPushItem = it;
  
  const classCascade = `${esc(it.hname || '–')} → ${esc(it.sname || '–')} → ${esc(it.gname || '–')}`;
  const specLine = (it.specs || []).filter(s => s.label)
    .map(s => `${esc(s.label)} <b>${esc(s.value || '—')}</b>`).join(' · ') || 'no specifications on this group';
    
  const txd = await api('/api/v1/cascade/taxes');
  const taxes = txd.taxes || [];
  const taxPlaceholder = taxes.length === 0
    ? '-- No templates loaded (Check ERPNext settings) --'
    : '-- Select a Tax Template --';
  const taxOptions = `<option value="">${esc(taxPlaceholder)}</option>` + 
    taxes.map(t => `<option value="${esc(t)}" ${t === it.tax ? 'selected' : ''}>${esc(t)}</option>`).join('');

  modal(`
    <h2 style="margin-top: 0; margin-bottom: 16px;">Review & Push to ERP</h2>
    <div style="margin-bottom: 20px; font-size: 13.5px; color: var(--mm-dim);">
      <div style="margin-bottom: 12px"><h3><code style="font-size:15px">${esc(it.code)}</code></h3>
      <div>${esc(it.name || 'Unnamed Item')}</div></div>
      <div class="sub">${classCascade} &nbsp;·&nbsp; ${specLine}</div>
    </div>
    
    <div class="row" style="display: flex; gap: 14px; margin-bottom: 20px;">
      <div class="field" style="flex: 1;">
        <label style="display: block; font-size: 12.5px; color: var(--mm-dim); margin-bottom: 5px;">Tax Template (Required in ERP)</label>
        <select id="pushTaxSel" style="width: 100%; font: inherit; background: var(--mm-navy); color: var(--mm-text); border: 1px solid var(--mm-border); border-radius: 7px; padding: 9px 11px; outline: none;">
          ${taxOptions}
        </select>
      </div>
      <div class="field" style="flex: 1;">
        <label style="display: block; font-size: 12.5px; color: var(--mm-dim); margin-bottom: 5px;">HSN Code</label>
        <input id="pushHsnInp" type="text" placeholder="e.g. 84821011" value="${esc(it.hsn || '')}" style="width: 100%; font: inherit; background: var(--mm-navy); color: var(--mm-text); border: 1px solid var(--mm-border); border-radius: 7px; padding: 9px 11px; outline: none;">
      </div>
    </div>
    <div class="btnrow" style="display: flex; gap: 10px; justify-content: flex-end;">
      <button class="ghost" id="pushReviewCancel" style="font: inherit; cursor: pointer; border-radius: 7px; padding: 9px 16px; background: transparent; border: 1px solid var(--mm-border); color: var(--mm-dim);">Cancel</button>
      <button class="primary" id="pushReviewConfirm" style="font: inherit; cursor: pointer; border-radius: 7px; padding: 9px 16px; background: var(--mm-teal); color: #00202c; font-weight: 600; border: 1px solid transparent;">Confirm Push</button>
    </div>
  `);
  
  document.getElementById('pushReviewCancel').onclick = closeModal;
  document.getElementById('pushReviewConfirm').onclick = async () => {
    const taxSel = document.getElementById('pushTaxSel');
    const tax = taxSel.value;
    const hsn = document.getElementById('pushHsnInp').value.trim();
    if (!tax) {
      toast('Tax Template is mandatory for ERPNext', 'err');
      return;
    }
    
    document.getElementById('pushReviewConfirm').textContent = 'Pushing...';
    document.getElementById('pushReviewConfirm').disabled = true;
    try {
      const res = await post('/api/v1/item/' + encodeURIComponent(it.code) + '/push', { tax, hsn });
      if (!res.ok) throw new Error(res.error || 'Failed to push');
      toast('Successfully pushed to ERPNext', 'ok');
      closeModal();
      loadMaster();
    } catch (e) {
      toast(e.message, 'err');
    } finally {
      const btn = document.getElementById('pushReviewConfirm');
      if (btn) {
        btn.textContent = 'Confirm Push';
        btn.disabled = false;
      }
    }
  };
}

app.showMapErpGroupModal = async function(groupName) {
  modal(`
    <h3 style="margin-top:0">Set Hierarchy for ERP Group</h3>
    <p style="margin-bottom:15px; color:var(--tx2); font-size:13px;">
      Map the ERP group <b>${esc(groupName)}</b> to a local Head and Sub-head. 
      This will register the group locally and update its parent in ERPNext.
    </p>
    
    <label style="display:block; margin-bottom:5px; font-weight:600; font-size:12px;">Head</label>
    <select id="mapErpHead" style="width:100%; margin-bottom:15px;"></select>
    
    <label style="display:block; margin-bottom:5px; font-weight:600; font-size:12px;">Sub-head</label>
    <select id="mapErpSub" style="width:100%; margin-bottom:20px;" disabled>
      <option value="">Select a head first...</option>
    </select>
    
    <div style="display:flex; justify-content:flex-end; gap:8px;">
      <button class="ghost" onclick="closeModal()">Cancel</button>
      <button class="primary" id="mapErpSubmit" disabled>Save Mapping</button>
    </div>
  `);
  
  const heads = await api('/api/v1/cascade/heads');
  const headSel = document.getElementById('mapErpHead');
  headSel.innerHTML = '<option value="">Select a head...</option>' + 
    heads.heads.map(h => `<option value="${h.id}">${esc(h.name)}</option>`).join('');
    
  headSel.onchange = async () => {
    const subSel = document.getElementById('mapErpSub');
    subSel.innerHTML = '<option value="">Loading...</option>';
    subSel.disabled = true;
    document.getElementById('mapErpSubmit').disabled = true;
    
    if (!headSel.value) {
      subSel.innerHTML = '<option value="">Select a head first...</option>';
      return;
    }
    
    const subs = await api('/api/v1/cascade/subheads?head=' + headSel.value);
    subSel.innerHTML = '<option value="">Select a sub-head...</option>' + 
      subs.subheads.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
    subSel.disabled = false;
  };
  
  document.getElementById('mapErpSub').onchange = () => {
    document.getElementById('mapErpSubmit').disabled = !document.getElementById('mapErpSub').value;
  };
  
  document.getElementById('mapErpSubmit').onclick = async () => {
    const subId = document.getElementById('mapErpSub').value;
    const btn = document.getElementById('mapErpSubmit');
    btn.disabled = true;
    btn.textContent = "Saving...";
    try {
      const res = await post('/api/v1/erp-group/map', { group_name: groupName, subhead_id: subId });
      if (!res.ok) throw new Error(res.error || res.message || "Failed to map group");
      toast("Group mapped successfully!", "ok");
      closeModal();
      loadMaster();
    } catch(e) {
      toast(e.message, "err");
      btn.disabled = false;
      btn.textContent = "Save Mapping";
    }
  };
};
