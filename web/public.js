/* Item Code Studio — public face (Agent A). No login, nothing here writes:
 * every call below is a GET against /api/v1/decode, /api/v1/directory* and
 * /api/v1/dictionary/* (agents/CONTRACTS.md §6, §9).
 */
const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => (s ?? '').toString().replace(/[&<>"]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

async function api(path) {
  const r = await fetch(path);
  const d = await r.json().catch(() => ({ ok: false, error: { message: 'bad response from server' } }));
  return d;
}

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

/* ─────────────────────────────────────────────────────────────── modal */
const modalEl = $('#pubModal');
function openModal(html) {
  $('#pubModalBody').innerHTML = html;
  modalEl.classList.add('on');
}
function closeModal() { modalEl.classList.remove('on'); }
$('#pubClose').onclick = closeModal;
modalEl.addEventListener('click', e => { if (e.target === modalEl) closeModal(); });

/* ═══════════════════════════════════════════════════════════ DECODER ═══ */
const decIn = $('#decIn'), decOut = $('#decOut');

function renderDecode(d) {
  if (!d.ok) {
    decOut.innerHTML = `<div class="dec-note bad">${esc(d.error.message)}</div>`;
    return;
  }
  const seg = d.segments;
  let html = `<div class="dec-code">${esc(d.code)}</div>`;

  if (!d.known) {
    html += `<div class="dec-path">${esc(seg.head)} · ${esc(seg.sub)} · ${esc(seg.group)}</div>`;
    html += `<div class="dec-note">${esc(d.note)}</div>`;
    decOut.innerHTML = html;
    return;
  }

  html += `<div class="dec-path">${esc(d.head)} · ${esc(d.subhead)} · ${esc(d.group)}</div>`;
  html += `<div class="dec-rows">`;
  html += `<div class="dec-row head"><span class="cc">${esc(seg.head)}</span><span class="lbl">Head</span><span class="val">${esc(d.head)}</span></div>`;
  html += `<div class="dec-row head"><span class="cc">${esc(seg.sub)}</span><span class="lbl">Sub-head</span><span class="val">${esc(d.subhead)}</span></div>`;
  html += `<div class="dec-row head"><span class="cc">${esc(seg.group)}</span><span class="lbl">Group</span><span class="val">${esc(d.group)}</span></div>`;
  for (const s of d.specs) {
    const lbl = s.slot === 5 ? (s.label || 'Vendor') : (s.label || `Spec ${s.slot}`);
    html += `<div class="dec-row"><span class="cc">${esc(s.code)}</span><span class="lbl">${esc(lbl)}</span><span class="val">${esc(s.value)}</span></div>`;
  }
  html += `</div>`;

  if (d.item) {
    const status = d.item.status || '';
    html += `<div class="dec-item"><b>${esc(d.item.name)}</b>${esc(d.item.code)} — ${esc(status)}</div>`;
  } else if (d.note) {
    html += `<div class="dec-note">${esc(d.note)}</div>`;
  }
  decOut.innerHTML = html;
}

async function runDecode() {
  const code = decIn.value.trim();
  if (!code) {
    decOut.innerHTML = `<div class="dec-empty">Type or paste any item code to see what it means.</div>`;
    return;
  }
  const d = await api('/api/v1/decode?code=' + encodeURIComponent(code));
  renderDecode(d);
}
$('#decGo').onclick = runDecode;
decIn.addEventListener('keydown', e => { if (e.key === 'Enter') runDecode(); });
decIn.addEventListener('input', debounce(runDecode, 250));

/* ══════════════════════════════════════════════════════════ DIRECTORY ═ */
const dirQ = $('#dirQ'), dirOut = $('#dirOut'), dirPager = $('#dirPager');
const dirState = { offset: 0, limit: 20, total: 0, q: '' };

function statusPill(status) {
  const s = (status || '').toLowerCase();
  if (s.includes('erp')) return `<span class="pill erp">${esc(status)}</span>`;
  if (s.includes('draft')) return `<span class="pill draft">${esc(status)}</span>`;
  return `<span class="pill">${esc(status || '—')}</span>`;
}

function renderDirectory(d) {
  if (!d.ok) {
    dirOut.innerHTML = `<div class="dec-note bad">${esc(d.error.message)}</div>`;
    dirPager.innerHTML = '';
    return;
  }
  dirState.total = d.total;
  if (!d.items.length) {
    dirOut.innerHTML = `<div class="dir-empty">No items match.</div>`;
    dirPager.innerHTML = '';
    return;
  }
  dirOut.innerHTML = `<div class="dir-list">` + d.items.map(it => `
    <div class="dir-row" data-code="${esc(it.code)}">
      <span class="code">${esc(it.code)}</span>
      <span class="main">
        <div class="name">${esc(it.name)}</div>
        <div class="meta">${esc(it.group_name || '')}${it.uom ? ' · ' + esc(it.uom) : ''}${it.hsn ? ' · HSN ' + esc(it.hsn) : ''}</div>
      </span>
      ${statusPill(it.status)}
    </div>`).join('') + `</div>`;

  $$('.dir-row', dirOut).forEach(row => row.onclick = () => openItem(row.dataset.code));

  const from = d.total ? dirState.offset + 1 : 0;
  const to = Math.min(dirState.offset + d.items.length, d.total);
  dirPager.innerHTML = `
    <button id="dirPrev" ${dirState.offset === 0 ? 'disabled' : ''}>‹ prev</button>
    <span>${from}–${to} of ${d.total}</span>
    <button id="dirNext" ${to >= d.total ? 'disabled' : ''}>next ›</button>`;
  $('#dirPrev').onclick = () => { dirState.offset = Math.max(0, dirState.offset - dirState.limit); loadDirectory(); };
  $('#dirNext').onclick = () => { dirState.offset += dirState.limit; loadDirectory(); };
}

async function loadDirectory() {
  const params = new URLSearchParams({ q: dirState.q, limit: dirState.limit, offset: dirState.offset });
  const d = await api('/api/v1/directory?' + params);
  renderDirectory(d);
}
const debouncedDirSearch = debounce(() => { dirState.offset = 0; dirState.q = dirQ.value.trim(); loadDirectory(); }, 200);
dirQ.addEventListener('input', debouncedDirSearch);

async function openItem(code) {
  const d = await api('/api/v1/directory/' + encodeURIComponent(code));
  if (!d.ok) { openModal(`<div class="dec-note bad">${esc(d.error.message)}</div>`); return; }
  const it = d.item;
  let specsHtml = '';
  if (d.specs && d.specs.length) {
    specsHtml = '<div class="kv">' + d.specs.map(s =>
      `<div class="k">${esc(s.label || `Spec ${s.slot}`)}</div><div>${esc(s.code)} — ${esc(s.value)}</div>`
    ).join('') + '</div>';
  }
  openModal(`
    <h3>${esc(it.name)}</h3>
    <div class="sub">${esc(it.code)}</div>
    <div class="kv">
      <div class="k">Group</div><div>${esc(it.head_name || '')} · ${esc(it.sub_name || '')} · ${esc(it.group_name || '')}</div>
      <div class="k">Description</div><div>${esc(it.description || '—')}</div>
      <div class="k">UoM</div><div>${esc(it.uom || '—')}${it.alt_uom ? ' (alt ' + esc(it.alt_uom) + ')' : ''}</div>
      <div class="k">HSN</div><div>${esc(it.hsn || '—')}</div>
      <div class="k">Status</div><div>${statusPill(it.status)}${it.frozen ? ' <span class="pill">frozen</span>' : ''}</div>
    </div>
    ${specsHtml}
  `);
}

/* ══════════════════════════════════════════════════════════ DICTIONARY ═ */
const dictQ = $('#dictQ'), dictOut = $('#dictOut'), dictPager = $('#dictPager');
const dictState = { offset: 0, limit: 20, total: 0, q: '' };

function renderDictionary(d) {
  if (!d.ok) {
    dictOut.innerHTML = `<div class="dec-note bad">${esc(d.error.message)}</div>`;
    dictPager.innerHTML = '';
    return;
  }
  dictState.total = d.total;
  if (!d.groups.length) {
    dictOut.innerHTML = `<div class="dir-empty">No groups match.</div>`;
    dictPager.innerHTML = '';
    return;
  }
  dictOut.innerHTML = `<div class="dir-list">` + d.groups.map(g => `
    <div class="dir-row" data-id="${g.id}">
      <span class="code">${esc(g.prefix)}</span>
      <span class="main">
        <div class="name">${esc(g.name)}</div>
        <div class="meta">${esc(g.head_name)} · ${esc(g.sub_name)} · ${g.n_items} item${g.n_items === 1 ? '' : 's'}</div>
      </span>
    </div>`).join('') + `</div>`;

  $$('.dir-row', dictOut).forEach(row => row.onclick = () => openGroup(row.dataset.id));

  const from = d.total ? dictState.offset + 1 : 0;
  const to = Math.min(dictState.offset + d.groups.length, d.total);
  dictPager.innerHTML = `
    <button id="dictPrev" ${dictState.offset === 0 ? 'disabled' : ''}>‹ prev</button>
    <span>${from}–${to} of ${d.total}</span>
    <button id="dictNext" ${to >= d.total ? 'disabled' : ''}>next ›</button>`;
  $('#dictPrev').onclick = () => { dictState.offset = Math.max(0, dictState.offset - dictState.limit); loadDictionary(); };
  $('#dictNext').onclick = () => { dictState.offset += dictState.limit; loadDictionary(); };
}

async function loadDictionary() {
  const params = new URLSearchParams({ q: dictState.q, limit: dictState.limit, offset: dictState.offset });
  const d = await api('/api/v1/dictionary/groups?' + params);
  renderDictionary(d);
}
const debouncedDictSearch = debounce(() => { dictState.offset = 0; dictState.q = dictQ.value.trim(); loadDictionary(); }, 200);
dictQ.addEventListener('input', debouncedDictSearch);

async function openGroup(id) {
  const d = await api('/api/v1/dictionary/group/' + encodeURIComponent(id));
  if (!d.ok) { openModal(`<div class="dec-note bad">${esc(d.error.message)}</div>`); return; }
  const g = d.group;
  let html = `<h3>${esc(g.name)}</h3>
    <div class="sub">${esc(g.prefix)} — ${esc(g.head)} · ${esc(g.subhead)}${g.uom ? ' · UoM ' + esc(g.uom) : ''} · ${d.item_count} item${d.item_count === 1 ? '' : 's'}</div>`;
  for (const s of d.specs) {
    html += `<div class="slotcard"><div class="sh">Slot ${s.slot} — ${esc(s.label)}</div>
      <div class="vals">${s.values.map(v => `<span class="slotval"><i>${esc(v.code)}</i>${esc(v.value)}</span>`).join('')}</div></div>`;
  }
  if (d.vendor) {
    html += `<div class="slotcard"><div class="sh">Vendor — ${esc(d.vendor.label)}</div>
      <div class="vals">${d.vendor.values.map(v => `<span class="slotval"><i>${esc(v.code)}</i>${esc(v.value)}</span>`).join('')}</div></div>`;
  }
  openModal(html);
}

/* ───────────────────────────────────────────────────────────────── tabs */
$$('.pub-tab').forEach(b => b.onclick = () => {
  $$('.pub-tab').forEach(x => x.classList.toggle('on', x === b));
  $('#tab-items').classList.toggle('hide', b.dataset.tab !== 'items');
  $('#tab-dict').classList.toggle('hide', b.dataset.tab !== 'dict');
});
$('#toDict').onclick = () => $('.pub-tab[data-tab="dict"]').click();

/* ─────────────────────────────────────────────────────────────── boot */
loadDirectory();
loadDictionary();
