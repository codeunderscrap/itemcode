/* Item Code Studio - front end */
const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => (s ?? '').toString().replace(/[&<>"]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

let ME = null;   // { username, display_name, is_admin } — set from /api/v1/auth/me, never a client-supplied header
let BOOT = null;
const state = { lines: [], results: [], mpage: 1, group: null };

async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    credentials: 'same-origin',
    headers: { ...(opts.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
               ...(opts.headers || {}) }
  });
  if (r.status === 401) {
    // session missing or expired - land back on the public page, quietly,
    // rather than a blank screen or a raw 401
    location.href = '/login.html?expired=1';
    throw new Error('session ended');
  }
  const d = await r.json().catch(() => ({ error: 'bad response' }));
  if (!r.ok && d.error) throw new Error((d.error && d.error.message) || d.error);
  return d;
}
const post = (p, b) => api(p, { method: 'POST', body: JSON.stringify(b) });

let toastT;
function toast(msg, kind = '') {
  const t = $('#toast'); t.textContent = msg; t.className = 'toast on ' + kind;
  clearTimeout(toastT); toastT = setTimeout(() => t.className = 'toast ' + kind, 4200);
}
function modal(html) {
  $('#modalBody').innerHTML = html; $('#modal').classList.remove('hide');
}
function closeModal() { $('#modal').classList.add('hide'); }
$('#modal').addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });

/* ───────────────────────────────────────────────────────────── identity */
// Attribution comes from the session, never from a name the browser hands
// over itself (that was the old, forgeable X-User header). This screen is
// only reachable once /api/v1/auth/me has confirmed a live session - see
// the boot() guard below - so ME is always trustworthy here.
async function loadMe() {
  const d = await api('/api/v1/auth/me');
  ME = d.user;
  if (!ME) { location.href = '/login.html'; return false; }
  $('#whoName').textContent = ME.display_name || ME.username;
  // Settings (the API-key screen) is admin-only - injected here rather than
  // hardcoded into index.html so this file stays the single place that
  // decides what a signed-in user can reach; nothing links there for a
  // non-admin. The server enforces this independently either way (see
  // core.auth.require_admin) - this is a convenience, not the boundary.
  if (ME.is_admin && !$('#settingsLink')) {
    const a = document.createElement('a');
    a.id = 'settingsLink'; a.href = '/settings.html'; a.className = 'who';
    a.style.display = 'block'; a.textContent = 'Settings';
    $('.railfoot').appendChild(a);
  }
  return true;
}
$('#whoBtn').onclick = async () => {
  if (confirm('Log out?')) {
    await api('/api/v1/auth/logout', { method: 'POST' }).catch(() => {});
    location.href = '/login.html';
  }
};

/* ───────────────────────────────────────────────────────────── boot */
async function boot() {
  if (!(await loadMe())) return;
  BOOT = await api('/api/bootstrap');
  $('#seedInfo').textContent = `${BOOT.counts.reserved.toLocaleString()} codes reserved`;
  const e = BOOT.erp;
  $('#erpChip').innerHTML = e.enabled
    ? `ERPNext <span class="pill ${e.dry_run ? 'dr' : 'erp'}">${e.dry_run ? 'dry-run' : 'live'}</span>`
    : `ERPNext <span class="pill no">off</span>`;
}

/* ───────────────────────────────────────────────────────────── nav */
$$('.nav').forEach(b => b.onclick = () => {
  $$('.nav').forEach(x => x.classList.toggle('on', x === b));
  $$('.view').forEach(v => v.classList.add('hide'));
  $('#v-' + b.dataset.view).classList.remove('hide');
  ({ master: loadMaster, dict: loadGroups, activity: loadActivity }[b.dataset.view] || (() => {}))();
});

/* ═══════════════════════════════════════════════════════ CREATE */
$$('.tab').forEach(t => t.onclick = () => {
  $$('.tab').forEach(x => x.classList.toggle('on', x === t));
  $('#in-text').classList.toggle('hide', t.dataset.intake !== 'text');
  $('#in-file').classList.toggle('hide', t.dataset.intake !== 'file');
});

$('#demoFill').onclick = () => {
  $('#rawText').value = [
    'Odonil Lavender air freshener 48 g',
    'Mseal epoxy sealant 100 gm',
    'Cylindrical NMC battery pack 32700 3.6 Kwh make LG',
    'A4 photocopy paper 75 gsm ream',
    'Hydraulic seal kit for JCB 3DX loader arm'
  ].join('\n');
};

$('#readText').onclick = async () => {
  const text = $('#rawText').value.trim();
  if (!text) return toast('Nothing to read', 'err');
  const d = await post('/api/ingest', { text });
  showLines(d.lines, 'typed text', d.note);
};

const drop = $('#drop'), file = $('#file');
drop.onclick = () => file.click();
['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.add('over');
}));
['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.remove('over');
}));
drop.addEventListener('drop', e => upload(e.dataTransfer.files[0]));
file.onchange = () => upload(file.files[0]);

async function upload(f) {
  if (!f) return;
  drop.innerHTML = `<b>Reading ${esc(f.name)}…</b><span>text layer first, OCR if needed</span>`;
  const fd = new FormData(); fd.append('file', f);
  try {
    const d = await api('/api/ingest', { method: 'POST', body: fd });
    showLines(d.lines, f.name, d.note);
  } catch (err) { toast(err.message, 'err'); }
  drop.innerHTML = `<b>Drop an invoice here</b><span>PDF · scan / photo · Excel · CSV · text<br>scanned pages go through OCR</span>`;
}

function showLines(lines, src, note) {
  state.lines = lines || [];
  $('#lineBox').classList.toggle('hide', !state.lines.length);
  $('#lineCount').textContent = `${state.lines.length} line${state.lines.length === 1 ? '' : 's'} from ${src}`;
  $('#fileNote').classList.toggle('hide', !note);
  $('#fileNote').textContent = note || '';
  $('#lineList').innerHTML = state.lines.map((l, i) => `
    <div class="lineitem"><span class="n">${String(i + 1).padStart(2, '0')}</span>
      <div class="d"><div>${esc(l.description)}</div>
        <div class="meta">
          ${l.qty ? `<span>qty ${l.qty} ${esc(l.uom || '')}</span>` : ''}
          ${l.rate ? `<span>rate ${l.rate}</span>` : ''}
          ${l.hsn ? `<span>HSN ${esc(l.hsn)}</span>` : ''}
          ${l.vendor ? `<span>vendor ${esc(l.vendor)}</span>` : ''}
          ${l.ocr ? `<span>via OCR</span>` : ''}
        </div></div></div>`).join('');
  if (!state.lines.length) toast('No item lines found in that file', 'err');
}

$('#resolveAll').onclick = async () => {
  if (!state.lines.length) return;
  $('#resList').innerHTML = `<div class="empty"><b>Resolving ${state.lines.length} line(s)…</b>
    <span>phase 1 existence · phase 2 taxonomy · phase 3 specifications</span></div>`;
  $('#resEmpty').classList.add('hide');
  const d = await post('/api/resolve_batch', { lines: state.lines });
  state.results = d.results;
  renderResults();
};

/* ───────────────────────────────────────────── result rendering */
function segHtml(code, segs) {
  if (!code) return '<span class="s0">— not enough information —</span>';
  if (!segs) return esc(code);
  const parts = [
    ['s1', segs.head], ['s1', segs.sub], ['s2', segs.group],
    ...(segs.specs || []).map((s, i) => [`s${3 + (i % 2)}`, s === null ? null : s])
  ];
  let out = `<i class="s1">${esc(segs.head)}${esc(segs.sub)}</i><i class="s2">${esc(segs.group)}</i>`;
  const body = code.slice(7);
  const n = body.length / 2;
  for (let i = 0; i < n; i++) {
    const isVendor = segs.vendor && i === n - 1;
    out += `<i class="${isVendor ? 's5' : (body.substr(i * 2, 2) === '00' ? 's0' : 's3')}">${body.substr(i * 2, 2)}</i>`;
  }
  return out;
}

function renderResults() {
  $('#resEmpty').classList.add('hide');
  $('#resList').innerHTML = state.results.map((r, i) => card(r, i)).join('');
  bindCards();
}

function card(r, i) {
  const inp = r.input || {};
  const title = esc(inp.name || inp.text || '(blank)');
  if (r.action === 'existing') {
    const h = r.phase1.hit;
    return `<div class="card" data-i="${i}">
      <div class="top"><div><div class="src">${title}</div>
        <div class="sub">phase 1 stopped here — this item is already coded</div></div>
        <span class="badge b-exists">exists</span></div>
      <div class="hits"><div class="hit"><code>${esc(h.code)}</code>
        <span>${esc(h.name || '')}</span>
        <span class="sc">${esc(h.source)} · ${esc(h.how)} · ${h.score}%</span></div></div>
      <div class="row"><button class="ghost sm" data-act="copy" data-code="${esc(h.code)}">Copy code</button>
        <button class="ghost sm" data-act="forcenew">Not the same item — code it anyway</button></div>
    </div>`;
  }

  const g = r.phase2 && r.phase2.group;
  const gstep = (r.phase2?.steps || []).find(s => s.level === 'group') || {};
  const near = r.phase1?.near || [];

  const slots = (r.phase3?.slots || []).map(s => {
    if (!s.label) return `<div class="slot off"><label>slot ${s.slot}</label>
      <span class="muted">not used by this group</span><span class="cc">–</span></div>`;
    const opts = (s.options || []).map(o =>
      `<option value="${o.id}" ${o.id === s.specval_id ? 'selected' : ''}>${esc(o.value)} · ${o.code}</option>`).join('');
    return `<div class="slot"><label title="${esc(s.label)}">${esc(s.label)}</label>
      <select data-slot="${s.slot}">
        <option value="">— none —</option>${opts}
        <option value="__new">+ add a new value…</option>
      </select>
      <span class="cc">${esc(s.code || s.proposed_code || '··')}</span></div>`;
  }).join('');

  const v = r.phase3?.vendor;
  const vendorRow = v ? (() => {
    const opts = (v.options || []).map(o =>
      `<option value="${o.id}" ${o.id === v.specval_id ? 'selected' : ''}>${esc(o.value)} · ${o.code}</option>`).join('');
    return `<div class="slot"><label>${esc(v.label)}</label>
      <select data-slot="5"><option value="">— none —</option>${opts}
      <option value="__new">+ add a new vendor…</option></select>
      <span class="cc">${esc(v.code || v.proposed_code || '··')}</span></div>`;
  })() : '';

  const blocked = (r.blockers || []).length;
  const newGroupBox = r.new_group ? (() => {
    const sug = r.phase2?.suggested_subheads || [];
    const chosen = r.phase2?.subhead?.id;
    const opts = BOOT.subheads.map(s => {
      const hint = sug.find(x => x.id === s.id);
      return `<option value="${s.id}" ${s.id === chosen ? 'selected' : ''}>${esc(s.head_name)} / ${esc(s.name)} (${s.head_code}${s.code2})${hint ? ' — like ' + esc(hint.because) : ''}</option>`;
    }).join('');
    return `<div class="slots" style="border-top:1px solid var(--line);padding-top:10px">
      <div class="slot"><label>New group</label>
        <input data-ng="name" placeholder="name this group, e.g. Epoxy Sealant"
               value="${esc((r.input.hints || {}).new_group_name || '')}"><span class="cc">${esc(r.segments?.group || '···')}</span></div>
      <div class="slot"><label>Belongs to</label><select data-ng="sub">${opts}</select><span class="cc"></span></div>
      <div class="slot"><label>Spec labels</label>
        <input data-ng="labels" placeholder="Type, Size   (comma separated, blank = no specs)"
               value="${esc((r.input.hints || {}).new_group_labels_text || '')}"><span class="cc"></span></div>
      <div class="slot"><label></label><button class="ghost sm" data-act="applyng">Apply</button><span class="cc"></span></div>
      <div class="slot"><label></label><span class="muted">The first item in a brand-new group gets
        the short group-level code. Spec values are added in the Dictionary, and every later item
        in this group then carries them.</span><span class="cc"></span></div>
    </div>`;
  })() : '';

  return `<div class="card" data-i="${i}">
    <div class="top"><div><div class="src">${title}</div>
      <div class="sub">${inp.hsn ? 'HSN ' + esc(inp.hsn) + ' · ' : ''}${inp.uom ? esc(inp.uom) + ' · ' : ''}${r.new_group ? 'no existing group fits — creating one' : 'new code proposed'}</div></div>
      <span class="badge ${blocked ? 'b-block' : 'b-new'}">${blocked ? 'needs input' : 'new'}</span></div>

    <div class="phases">
      <div class="ph ${near.length ? '' : 'hit'}"><div class="t">1 · exists?</div>
        <div class="v">${near.length ? `no exact match<br><span class="muted">${near.length} close call${near.length > 1 ? 's' : ''} below</span>` : 'not found — safe to create'}</div></div>
      <div class="ph ${g ? 'hit' : 'mk'}"><div class="t">2 · taxonomy</div>
        <div class="v">${g ? esc(g.name) : 'new group'}
          <span class="layer ${gstep.layer || ''}">${esc(gstep.layer || '–')} ${gstep.score ? gstep.score + '%' : ''}</span>
          ${g ? `<br><span class="muted">${esc(g.head_name)} / ${esc(g.sub_name)}</span>`
             : (r.phase2?.subhead ? `<br><span class="muted">suggested: ${esc(r.phase2.head.name)} / ${esc(r.phase2.subhead.name)}</span>` : '')}</div></div>
      <div class="ph mk"><div class="t">3 · specs</div>
        <div class="v">${(r.phase3?.slots || []).filter(s => s.label).length} slot(s) in use${v ? ' + vendor' : ''}</div></div>
    </div>
    ${newGroupBox}

    ${near.length ? `<div class="hits">${near.slice(0, 3).map(h =>
      `<div class="hit"><code>${esc(h.code)}</code><span>${esc(h.name)}</span>
        <span class="sc">${esc(h.source)} · ${h.score}%</span>
        <button class="ghost sm" data-act="use" data-code="${esc(h.code)}">use this</button></div>`).join('')}</div>` : ''}

    <div class="slots">${slots}${vendorRow}</div>

    <div class="codebar">
      <div class="code" data-code>${segHtml(r.code, r.segments)}</div>
      <span class="grow"></span>
      <button class="primary sm" data-act="submit" ${blocked ? 'disabled' : ''}>Submit &amp; issue</button>
    </div>
    <div class="legend">
      <span><i class="s1">▮</i> head + sub-head</span><span><i class="s2">▮</i> group</span>
      <span><i class="s3">▮</i> specification</span><span><i class="s0">▮</i> 00 = not applicable</span>
      <span><i class="s5">▮</i> vendor</span>
    </div>
    ${blocked ? `<div class="blockers">${r.blockers.map(esc).join('<br>')}</div>` : ''}
  </div>`;
}

function bindCards() {
  $$('.card').forEach(cardEl => {
    const i = +cardEl.dataset.i;
    const r = state.results[i];

    $$('[data-act]', cardEl).forEach(b => b.onclick = async () => {
      const act = b.dataset.act;
      if (act === 'copy' || act === 'use') {
        navigator.clipboard?.writeText(b.dataset.code);
        toast(`${b.dataset.code} copied`, 'ok'); return;
      }
      if (act === 'applyng') {
        const name = $('[data-ng="name"]', cardEl).value.trim();
        if (!name) return toast('Give the new group a name', 'err');
        const labelsText = $('[data-ng="labels"]', cardEl).value.trim();
        const labels = {};
        labelsText.split(',').map(s => s.trim()).filter(Boolean)
          .forEach((l, k) => { if (k < 4) labels[k + 1] = l; });
        const hints = {
          ...(r.input.hints || {}), new_group_name: name,
          subhead_id: +$('[data-ng="sub"]', cardEl).value,
          new_group_labels: labels, new_group_labels_text: labelsText
        };
        state.results[i] = await post('/api/resolve', { ...r.input, hints });
        renderResults(); return;
      }
      if (act === 'forcenew') {
        const inp = { ...r.input, hints: { ...(r.input.hints || {}), force_new: true } };
        state.results[i] = await post('/api/resolve', inp);
        renderResults(); return;
      }
      if (act === 'submit') {
        b.disabled = true;
        try {
          const d = await post('/api/commit', { proposal: r, push_erp: !!BOOT.erp.enabled });
          toast(`${d.code} issued${d.erp ? (d.erp.dry_run ? ' · ERPNext dry-run OK' : ' · created in ERPNext') : ''}`, 'ok');
          cardEl.querySelector('.badge').className = 'badge b-done';
          cardEl.querySelector('.badge').textContent = 'issued ' + d.code;
          $$('select,button', cardEl).forEach(x => x.disabled = true);
          boot();
        } catch (e) { toast(e.message, 'err'); b.disabled = false; }
      }
    });

    $$('select[data-slot]', cardEl).forEach(sel => sel.onchange = async () => {
      const slot = +sel.dataset.slot;
      if (sel.value === '__new') {
        const val = prompt('New value for this specification:');
        if (!val) { sel.value = ''; return; }
        const gid = r.phase2?.group?.id;
        if (!gid) { toast('Create the group first', 'err'); sel.value = ''; return; }
        const d = await post('/api/specval/add', { group_id: gid, slot, value: val });
        toast(`"${val}" added as ${d.code2}`, 'ok');
        sel.insertAdjacentHTML('beforeend', `<option value="${d.id}" selected>${esc(val)} · ${d.code2}</option>`);
        sel.value = d.id;
      }
      applySlot(r, slot, sel.value);
      const re = await post('/api/resolve', {
        ...r.input,
        hints: { ...(r.input.hints || {}), ...collectHints(cardEl, r) }
      });
      state.results[i] = re; renderResults();
    });
  });
}

function collectHints(cardEl, r) {
  const h = {};
  $$('select[data-slot]', cardEl).forEach(sel => {
    if (!sel.value || sel.value === '__new') return;
    const slot = +sel.dataset.slot;
    const label = sel.selectedOptions[0].textContent.split(' · ')[0];
    if (slot === 5) h.vendor_value = label; else h['s' + slot] = label;
  });
  if (r.phase2?.group) h.group_id = r.phase2.group.id;
  return h;
}
function applySlot(r, slot, val) {
  const target = slot === 5 ? r.phase3.vendor
    : (r.phase3.slots || []).find(s => s.slot === slot);
  if (target) target.specval_id = val ? +val : null;
}

/* ═══════════════════════════════════════════════════════ MASTER */
// Item master (editable directory), versions, revert and activity-with-
// diffs now live in web/master.js (Agent F) — loaded after this file in
// index.html, so `loadMaster` / `loadActivity` below resolve there. Kept
// out of this file so the master's own toolbar/filters can evolve without
// touching create/dictionary/decoder code.

/* ═══════════════════════════════════════════════════════ DICTIONARY */
async function loadGroups() {
  const gs = await api('/api/groups?limit=400&q=' + encodeURIComponent($('#gq').value));
  $('#gTable tbody').innerHTML = gs.map(g => `<tr data-g="${g.id}">
    <td><code>${esc(g.prefix)}</code></td><td>${esc(g.name)}</td>
    <td>${esc(g.head_name)}<br><span class="muted">${esc(g.sub_name)}</span></td>
    <td class="num">${g.n_specs}</td><td class="num">${g.n_items}</td></tr>`).join('');
  $$('#gTable tbody tr').forEach(tr => tr.onclick = () => {
    $$('#gTable tbody tr').forEach(x => x.classList.toggle('sel', x === tr));
    openGroup(+tr.dataset.g);
  });
}
$('#gq').addEventListener('input', loadGroups);

async function openGroup(id) {
  const g = await api('/api/group/' + id);
  state.group = g;
  const labelOf = s => s === 5 ? g.labels.vendor : g.labels[String(s)];
  const cards = [1, 2, 3, 4, 5].map(s => {
    const lbl = labelOf(s), vals = g.specs[String(s)] || [];
    if (!lbl && !vals.length) return '';
    return `<div class="slotcard"><div class="sh">
        <b>${s === 5 ? 'Vendor' : 'Spec ' + s}</b>
        <span class="muted">${esc(lbl || 'no label set')}</span>
        <span class="grow"></span><span class="muted">${vals.length} value(s)</span>
        <button class="ghost sm" data-addval="${s}">+ value</button></div>
      <div class="vals">${vals.map(v =>
        `<span class="val">${esc(v.value)}<i>${v.code2}</i></span>`).join('') ||
        '<span class="muted">none yet</span>'}</div></div>`;
  }).join('');

  $('#gDetail').innerHTML = `<div class="gd">
    <h2>${esc(g.name)} <span class="pfx">${esc(g.prefix)}</span></h2>
    <div class="crumbs">${esc(g.head_name)} → ${esc(g.sub_name)} → group ${esc(g.code3)}
      · ${g.items.length} item(s)</div>
    ${cards}
    <div class="gdact">
      <button class="ghost sm" id="gRename">Rename</button>
      <button class="ghost sm" id="gMove">Move to another sub-head</button>
      <button class="ghost sm" id="gMerge">Merge into another group</button>
      <button class="danger sm" id="gDelete">Retire</button>
    </div>
    <div class="tablewrap" style="max-height:220px"><table>
      <thead><tr><th>Code</th><th>Item</th><th>Status</th></tr></thead>
      <tbody>${g.items.map(i => `<tr class="dict-item-row" data-code="${esc(i.code)}" style="cursor:pointer"><td><code>${esc(i.code)}</code></td><td>${esc(i.name)}</td>
        <td><span class="pill ${i.status === 'in_erp' ? 'erp' : ''}">${esc(i.status)}</span>
        ${i.decodable ? '' : '<span class="pill dr">stale</span>'}</td></tr>`).join('')}</tbody>
    </table></div></div>`;

  $$('.dict-item-row', $('#gDetail')).forEach(tr => {
    tr.onclick = async () => {
      try {
        const d = await api('/api/v1/item/' + encodeURIComponent(tr.dataset.code));
        if (d.item && typeof renderItemModal === 'function') {
          renderItemModal(d.item);
        } else if (!d.item) {
          toast('Item not found', 'err');
        } else {
          toast('Item Master script is not loaded', 'err');
        }
      } catch (e) {
        toast(e.message || 'Failed to load item', 'err');
      }
    };
  });

  $$('[data-addval]').forEach(b => b.onclick = async () => {
    const val = prompt('New value:'); if (!val) return;
    const d = await post('/api/specval/add', { group_id: g.id, slot: +b.dataset.addval, value: val });
    toast(`"${val}" is ${d.code2}`, 'ok'); openGroup(g.id);
  });
  $('#gRename').onclick = async () => {
    const n = prompt('New name for this group:', g.name); if (!n) return;
    const d = await post('/api/rename', { scope: 'group', id: g.id, name: n });
    toast(`Renamed. ${d.codes_changed} codes changed — the old wording is kept as a search alias.`, 'ok');
    loadGroups(); openGroup(g.id);
  };
  $('#gDelete').onclick = async () => {
    const d = await post('/api/group/delete', { group_id: g.id, reason: 'retired from dictionary' });
    if (d.error) return toast(d.error, 'err');
    toast(`Retired. Number ${d.vacancy.code3} is parked for a future match.`, 'ok');
    loadGroups(); $('#gDetail').innerHTML = '';
  };
  $('#gMove').onclick = () => moveDialog(g);
  $('#gMerge').onclick = () => mergeDialog(g);
}

async function moveDialog(g) {
  const opts = BOOT.subheads.map(s =>
    `<option value="${s.id}" ${s.id === g.sub_id ? 'disabled' : ''}>${esc(s.head_name)} / ${esc(s.name)} (${s.head_code}${s.code2})</option>`).join('');
  modal(`<h3>Move “${esc(g.name)}”</h3>
    <div class="sub">Preview first. Codes already live in ERPNext are never rewritten.</div>
    <div class="row"><select id="mvTarget" style="flex:1">${opts}</select>
      <button class="ghost" id="mvPrev">Preview impact</button></div>
    <div id="mvOut"></div>`);
  $('#mvPrev').onclick = async () => {
    const pv = await post('/api/group/move/preview',
      { group_id: g.id, subhead_id: +$('#mvTarget').value });
    if (pv.error) return toast(pv.error, 'err');
    $('#mvOut').innerHTML = `
      <div class="impact">
        <div class="ibox"><b>${pv.counts.recode}</b><small>codes re-issued</small></div>
        <div class="ibox"><b>${pv.counts.frozen}</b><small>frozen in ERPNext — code kept, flagged stale</small></div>
      </div>
      <div class="sub"><code>${esc(pv.old_prefix)}</code> → <code>${esc(pv.new_prefix)}</code>
        · number <b>${esc(pv.vacancy_created.code3)}</b> is parked under ${esc(pv.vacancy_created.subhead)}
        ${pv.reused_vacancy_of ? `· reused a number freed by “${esc(pv.reused_vacancy_of)}”` : ''}</div>
      <div class="difflist">${(pv.will_recode.slice(0, 60).map(r =>
        `<div>${esc(r.old_code)} <span class="ar">→</span> ${esc(r.new_code)}  ${esc(r.item).slice(0, 40)}</div>`).join('')
        || '<div>no code changes</div>')}</div>
      <div class="row"><button class="primary" id="mvGo">Apply the move</button>
        <button class="ghost" id="mvNo">Cancel</button></div>`;
    $('#mvNo').onclick = closeModal;
    $('#mvGo').onclick = async () => {
      const d = await post('/api/group/move',
        { group_id: g.id, subhead_id: +$('#mvTarget').value, push_erp: !!BOOT.erp.enabled });
      closeModal(); toast(`Moved. ${d.counts.recode} re-issued, ${d.counts.frozen} kept.`, 'ok');
      loadGroups(); openGroup(g.id);
    };
  };
}

async function mergeDialog(g) {
  const gs = await api('/api/groups?limit=400');
  modal(`<h3>Merge “${esc(g.name)}” into another group</h3>
    <div class="sub">Its items move across, their spec values are matched or added, and codes are
      re-issued — except for anything frozen in ERPNext.</div>
    <div class="row"><select id="mgTarget" style="flex:1">${gs.filter(x => x.id !== g.id).map(x =>
      `<option value="${x.id}">${esc(x.head_name)} / ${esc(x.sub_name)} / ${esc(x.name)} (${esc(x.prefix)})</option>`).join('')}</select></div>
    <div class="row"><button class="primary" id="mgGo">Merge</button>
      <button class="ghost" id="mgNo">Cancel</button></div>`);
  $('#mgNo').onclick = closeModal;
  $('#mgGo').onclick = async () => {
    const d = await post('/api/group/merge', { source_id: g.id, target_id: +$('#mgTarget').value });
    if (d.error) return toast(d.error, 'err');
    closeModal();
    toast(`Merged into ${d.merged_into}: ${d.recoded.length} re-issued, ${d.kept_code.length} kept.`, 'ok');
    loadGroups();
  };
}

$('#btnNewGroup').onclick = () => {
  const opts = BOOT.subheads.map(s =>
    `<option value="${s.id}">${esc(s.head_name)} / ${esc(s.name)} (${s.head_code}${s.code2})</option>`).join('');
  modal(`<h3>New item group</h3>
    <div class="sub">Anyone can add one. The next free number in that sub-head is used —
      or a parked number, if the name is a close match to the group that freed it.</div>
    <div class="slots">
      <div class="slot"><label>Sub-head</label><select id="ngSub">${opts}</select><span class="cc"></span></div>
      <div class="slot"><label>Group name</label><input id="ngName"><span class="cc"></span></div>
      <div class="slot"><label>UoM</label><input id="ngUom" placeholder="Nos"><span class="cc"></span></div>
      ${[1, 2, 3, 4].map(i => `<div class="slot"><label>Spec ${i} label</label>
        <input id="ngL${i}" placeholder="${i === 1 ? 'e.g. Type' : 'leave blank if unused'}"><span class="cc"></span></div>`).join('')}
      <div class="slot"><label>Vendor label</label><input id="ngLV" placeholder="blank = no vendor in the code"><span class="cc"></span></div>
    </div>
    <div class="row"><button class="primary" id="ngGo">Create</button>
      <button class="ghost" id="ngNo">Cancel</button></div>`);
  $('#ngNo').onclick = closeModal;
  $('#ngGo').onclick = async () => {
    const labels = {};
    [1, 2, 3, 4].forEach(i => { const v = $('#ngL' + i).value.trim(); if (v) labels[i] = v; });
    const vl = $('#ngLV').value.trim(); if (vl) labels.vendor = vl;
    const d = await post('/api/group/add', {
      subhead_id: +$('#ngSub').value, name: $('#ngName').value.trim(),
      uom: $('#ngUom').value.trim(), labels
    });
    closeModal();
    toast(`Group created as ${d.code3}${d.reused_vacancy_of ? ` (reused the number freed by “${d.reused_vacancy_of}”)` : ''}`, 'ok');
    loadGroups();
  };
};

/* ═══════════════════════════════════════════════════════ DECODER */
$('#dGo').onclick = decode;
$('#dq').addEventListener('keydown', e => { if (e.key === 'Enter') decode(); });
async function decode() {
  const code = $('#dq').value.trim().toUpperCase();
  if (!code) return;
  const d = await api('/api/decode?code=' + encodeURIComponent(code));
  if (!d.wellformed) {
    $('#dOut').innerHTML = `<div class="dec"><div class="big">${esc(code)}</div>
      <div class="blockers">Not a valid code — ${esc(d.why)}</div></div>`;
    return;
  }
  const s = d.segments;
  const rows = [
    ['head', s.head, d.head || 'unknown head'],
    ['sub-head', s.sub, d.subhead || 'unknown sub-head'],
    ['group', s.group, d.group || 'unknown group'],
    ...(d.specs || []).map(x => [x.slot === 5 ? 'vendor' : 'spec ' + x.slot, x.code,
      `${x.value}${x.label ? ` <span class="muted">(${esc(x.label)})</span>` : ''}`])
  ];
  $('#dOut').innerHTML = `<div class="dec">
    <div class="big">${segHtml(code, { head: s.head, sub: s.sub, group: s.group, vendor: s.vendor })}</div>
    ${rows.map(([l, k, v]) => `<div class="decrow"><span class="l">${l}</span>
      <span class="k">${esc(k ?? '–')}</span><span>${v}</span></div>`).join('')}
    <div class="decrow"><span class="l">length</span><span class="k">${s.length}</span>
      <span class="muted">valid lengths are 7, 9, 11, 13, 15, 17</span></div>
    ${d.item ? `<div class="hits" style="margin-top:14px"><div class="hit"><code>${esc(d.item.code)}</code>
      <span>${esc(d.item.name || '')}</span><span class="sc">${esc(d.item.status)}</span></div></div>`
      : '<div class="sub" style="margin-top:12px">No item currently holds this code.</div>'}
  </div>`;
}

/* ═══════════════════════════════════════════════════════ ACTIVITY */
// `loadActivity` also lives in web/master.js now — see the note above MASTER.

boot();
