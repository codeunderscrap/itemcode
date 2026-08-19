/* Item Code Studio — the Create screen (Agent E).
 *
 * Self-contained: wrapped in an IIFE so nothing here collides with the
 * top-level `let`/`const` bindings web/app.js already declares in the same
 * document (classic <script> tags share one lexical scope — redeclaring
 * `state`, `$`, etc. at the top level would be a SyntaxError). This file
 * takes over the #v-create section's contents at runtime; it never edits
 * web/index.html or web/app.js on disk, and app.js's own nav/toast/modal
 * plumbing keeps working untouched for the other views.
 *
 * Every code shown here is computed on the server by core.codes.assemble
 * (via /api/v1/resolve or /api/v1/resolve/preview) — nothing in this file
 * ever builds a code string itself (CONTRACTS.md §3).
 */
(function () {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => (s ?? "").toString().replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const uid = () => (window.crypto && crypto.randomUUID)
    ? crypto.randomUUID()
    : "id-" + Date.now() + "-" + Math.random().toString(16).slice(2);

  // ─────────────────────────────────────────────────────────── identity
  // Real sessions (Agent B, core/auth.py): a signed HttpOnly cookie the
  // browser attaches on its own to any same-origin fetch — nothing here
  // reads or sends a client-supplied username. This screen is only ever
  // reached after app.js's own boot()/loadMe() has confirmed a live
  // session (it redirects to /login.html otherwise), so by the time a
  // person can click anything on this screen they are already signed in;
  // apiV1() below still handles a session that expires mid-use.
  function ensureUser() { /* no-op: identity comes from the session cookie, not from here */ }

  // ────────────────────────────────────────────────────────────── fetch
  async function apiV1(path, opts = {}) {
    const headers = {
      ...(opts.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(opts.headers || {}),
    };
    const r = await fetch(path, { ...opts, credentials: "same-origin", headers });
    if (r.status === 401) {
      location.href = "/login.html?expired=1";
      throw new Error("session ended");
    }
    let d;
    try { d = await r.json(); } catch (e) { d = { ok: false, error: { code: "INTERNAL", message: "bad response from the server" } }; }
    if (!d.ok) {
      const e = new Error((d.error && d.error.message) || "request failed");
      e.code = d.error && d.error.code;
      e.detail = d.error && d.error.detail;
      throw e;
    }
    return d;
  }
  const getJSON = (p) => apiV1(p);
  const postJSON = (p, b) => apiV1(p, { method: "POST", body: JSON.stringify(b) });

  let ERP_ENABLED = false;
  fetch("/api/bootstrap", { credentials: "same-origin" }).then((r) => r.json())
    .then((d) => { ERP_ENABLED = !!(d.erp && d.erp.enabled); }).catch(() => {});

  // toast()/modal()/closeModal() are plain `function` declarations in
  // app.js, so they're real globals — reused as-is for a consistent feel,
  // never redefined here.

  // ──────────────────────────────────────────────────────────── state
  const state = { lines: [], cards: [] };
  let HEADS_CACHE = null;
  async function ensureHeads() {
    if (!HEADS_CACHE) HEADS_CACHE = (await getJSON("/api/v1/cascade/heads")).heads;
    return HEADS_CACHE;
  }

  let TAX_CACHE = null;
  async function ensureTaxes() {
    if (!ERP_ENABLED) return [];
    if (TAX_CACHE === null) TAX_CACHE = (await getJSON("/api/v1/cascade/taxes")).taxes || [];
    return TAX_CACHE;
  }

  // ──────────────────────────────────────────────────────────── markup
  function shell() {
    return `
    <header>
      <h1>Create codes</h1>
      <p>Paste a line, or drop an invoice. Nothing is written until you submit.</p>
    </header>
    <div class="split">
      <div class="panel intake">
        <div class="tabs">
          <button class="tab on" data-intake="text">Type / paste</button>
          <button class="tab" data-intake="file">Invoice file</button>
        </div>

        <div class="intake-body" id="ce-in-text">
          <textarea id="ce-raw" rows="9" spellcheck="false"
            placeholder="One item per line, for example:&#10;Odonil Lavender air freshener 48 g&#10;M-Seal epoxy sealant 100gm&#10;Cylindrical NMC battery pack 32700 make LG"></textarea>
          <div class="row">
            <button class="primary" id="ce-read">Read lines <span class="muted">(Ctrl+Enter)</span></button>
            <button class="ghost" id="ce-demo">Load demo lines</button>
          </div>
        </div>

        <div class="intake-body hide" id="ce-in-file">
          <div class="drop" id="ce-drop">
            <b>Drop an invoice here</b>
            <span>PDF · scan / photo · Excel · CSV · text<br>scanned pages go through OCR — this can take a while</span>
            <input type="file" id="ce-file" hidden
                   accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.xlsx,.xlsm,.csv,.txt">
          </div>
          <div id="ce-progress" class="note hide"></div>
          <div id="ce-filenote" class="note hide"></div>
        </div>

        <div class="lines hide" id="ce-linebox">
          <div class="lines-head"><b id="ce-linecount">0 lines</b>
            <button class="primary sm" id="ce-resolve-all">Resolve all</button></div>
          <div id="ce-linelist"></div>
        </div>
      </div>

      <div class="panel results">
        <div class="empty" id="ce-empty">
          <div class="bigmark">⌘</div>
          <b>Resolution results appear here</b>
          <span>Each line runs three phases: does the code exist → does the taxonomy exist →
          what are the specifications. One bad line never blocks the rest.</span>
        </div>
        <div id="ce-reslist"></div>
      </div>
    </div>`;
  }

  function injectStyle() {
    if ($("#ce-style")) return;
    const st = document.createElement("style");
    st.id = "ce-style";
    st.textContent = `
      .ce-cascade{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin:10px 0}
      .ce-cascade .slot{grid-template-columns:56px 1fr}
      .ce-phaseline{font-size:12.5px;padding:6px 0;border-bottom:1px dashed var(--line);
        display:flex;flex-wrap:wrap;gap:4px;align-items:baseline}
      .ce-phaseline:last-of-type{border-bottom:0}
      .ce-phaseline b{color:var(--tx3);font-weight:700;font-size:10.5px;margin-right:2px}
      .ce-specok code{font-family:var(--mono);font-size:11px;color:var(--acc)}
      .ce-specneed{display:inline-flex;gap:5px;align-items:center;background:var(--panel2);
        border:1px solid var(--warn);border-radius:6px;padding:2px 6px}
      .ce-specneed label{font-size:11px;color:var(--warn)}
      .ce-specneed select,.ce-specneed input{padding:2px 5px;font-size:11.5px;min-width:90px}
      .ce-tag{font-size:11px;color:var(--tx3);background:var(--panel2);border:1px solid var(--line);
        border-radius:5px;padding:2px 6px;margin-right:6px}
      .ce-tag.learn{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 45%,transparent);
        background:color-mix(in srgb,var(--ok) 12%,transparent);padding:6px 10px;margin-top:8px;display:inline-block}
      .ce-newgroup{border-top:1px dashed var(--line);padding-top:8px}
      .layer.exact{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 45%,transparent)}
      .layer.operator{color:var(--seg4);border-color:color-mix(in srgb,var(--seg4) 45%,transparent)}
      .layer.rules{color:var(--acc);border-color:color-mix(in srgb,var(--acc) 40%,transparent)}
      @keyframes ce-spin{to{transform:rotate(360deg)}}
      .ce-spin{display:inline-block;width:12px;height:12px;border:2px solid var(--line);
        border-top-color:var(--acc);border-radius:50%;animation:ce-spin .7s linear infinite;
        vertical-align:middle;margin-right:4px}
    `;
    document.head.appendChild(st);
  }

  // ──────────────────────────────────────────────────────────── intake
  function wireIntake() {
    $$(".tab").forEach((t) => t.onclick = () => {
      $$(".tab").forEach((x) => x.classList.toggle("on", x === t));
      $("#ce-in-text").classList.toggle("hide", t.dataset.intake !== "text");
      $("#ce-in-file").classList.toggle("hide", t.dataset.intake !== "file");
    });

    $("#ce-demo").onclick = () => {
      $("#ce-raw").value = [
        "Odonil Lavendar Air Freshner 48gm",
        "Mseal epoxy sealant 100 gm",
        "Cylindrical NMC battery pack 32700 3.6 Kwh make LG",
        "A4 photocopy paper 75 gsm ream",
        "Hydraulic seal kit for JCB 3DX loader arm",
      ].join("\n");
    };

    $("#ce-read").onclick = async () => {
      ensureUser();
      const text = $("#ce-raw").value.trim();
      if (!text) return toast("Nothing to read", "err");
      try {
        const d = await postJSON("/api/v1/ingest", { text });
        (d.lines || []).forEach((l) => (l.__file = false));
        showLines(d.lines, "typed text", d.note);
      } catch (err) { toast(err.message, "err"); }
    };
    $("#ce-raw").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $("#ce-read").click(); }
    });

    const dropEl = $("#ce-drop"), fileEl = $("#ce-file");
    dropEl.onclick = () => fileEl.click();
    ["dragenter", "dragover"].forEach((ev) => dropEl.addEventListener(ev, (e) => { e.preventDefault(); dropEl.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => dropEl.addEventListener(ev, (e) => { e.preventDefault(); dropEl.classList.remove("over"); }));
    dropEl.addEventListener("drop", (e) => uploadFile(e.dataTransfer.files[0]));
    fileEl.onchange = () => uploadFile(fileEl.files[0]);

    $("#ce-resolve-all").onclick = resolveAll;
  }

  async function uploadFile(f) {
    if (!f) return;
    ensureUser();
    const prog = $("#ce-progress"), note = $("#ce-filenote");
    note.classList.add("hide");
    prog.classList.remove("hide");
    const t0 = Date.now();
    const paint = () => {
      const s = ((Date.now() - t0) / 1000).toFixed(0);
      prog.innerHTML = `<span class="ce-spin"></span> reading ${esc(f.name)}… ${s}s
        <br><span class="muted">scanned pages go through OCR — a ten-page scan can take a minute or two</span>`;
    };
    paint();
    const timer = setInterval(paint, 500);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const d = await apiV1("/api/v1/ingest", { method: "POST", body: fd });
      (d.lines || []).forEach((l) => (l.__file = true));
      const pageInfo = d.pages && d.pages.length ? ` across ${d.pages.length} page(s)` : "";
      const ocrInfo = d.ocr_lines ? `, ${d.ocr_lines} line(s) via OCR` : "";
      showLines(d.lines, f.name, d.note, `${(d.lines || []).length} line(s) found${pageInfo}${ocrInfo}`);
    } catch (err) {
      toast(err.message || "could not read that file", "err");
    } finally {
      clearInterval(timer);
      prog.classList.add("hide");
    }
  }

  function showLines(lines, src, note, summary) {
    state.lines = lines || [];
    $("#ce-linebox").classList.toggle("hide", !state.lines.length);
    $("#ce-linecount").textContent = summary || `${state.lines.length} line${state.lines.length === 1 ? "" : "s"} from ${src}`;
    $("#ce-filenote").classList.toggle("hide", !note);
    $("#ce-filenote").textContent = note || "";
    $("#ce-linelist").innerHTML = state.lines.map((l, idx) => `
      <div class="lineitem"><span class="n">${String(idx + 1).padStart(2, "0")}</span>
        <div class="d"><div>${esc(l.description)}</div>
          <div class="meta">
            ${l.qty ? `<span>qty ${l.qty} ${esc(l.uom || "")}</span>` : ""}
            ${l.rate ? `<span>rate ${l.rate}</span>` : ""}
            ${l.hsn ? `<span>HSN ${esc(l.hsn)}</span>` : ""}
            ${l.vendor ? `<span>vendor on line: ${esc(l.vendor)}</span>` : ""}
            ${l.ocr ? `<span>via OCR</span>` : ""}
          </div></div></div>`).join("");
    if (!state.lines.length) toast("No item lines found in that input", "err");
  }

  // ─────────────────────────────────────────────────────────── resolve
  function lineToPayload(l) {
    return { text: l.description, name: null, hsn: l.hsn, uom: l.uom, vendor: l.vendor, hints: {} };
  }

  async function resolveAll() {
    if (!state.lines.length) return;
    ensureUser();
    $("#ce-empty").classList.add("hide");
    state.cards = state.lines.map((l, i) => ({
      i, line: l, input: lineToPayload(l), res: null, status: "pending",
      idemKey: uid(), editing: false, sel: null, previewRes: null,
      submitting: false, submitted: false, submittedCode: null, learnedAlias: null,
    }));
    $("#ce-reslist").innerHTML = state.cards.map(cardHtml).join("");

    // fired together (one wave, not 20 sequential round trips), rendered
    // progressively as each settles — a slow or failing line never blocks
    // the other nineteen (agents/AGENT_E_CREATE.md done-when).
    const POOL = 4;
    let next = 0;
    const worker = async () => {
      while (next < state.cards.length) {
        const c = state.cards[next++];
        try {
          c.res = await postJSON("/api/v1/resolve", c.input);
          c.status = "ready";
          await beginEdit(c);
        } catch (err) {
          c.res = { outcome: "error", blockers: [err.message || "could not resolve this line"] };
          c.status = "error";
        }
        updateCard(c);
      }
    };
    await Promise.all(Array.from({ length: Math.min(POOL, state.cards.length) }, worker));
  }

  function updateCard(c) {
    const el = document.querySelector(`#ce-reslist .card[data-i="${c.i}"]`);
    if (el) el.outerHTML = cardHtml(c);
  }

  // ─────────────────────────────────────────────────────── card markup
  function segHtml(code, segs) {
    if (!code) return '<span class="s0">— not enough information —</span>';
    if (!segs) return esc(code);
    let out = `<i class="s1">${esc(segs.head)}${esc(segs.sub)}</i><i class="s2">${esc(segs.group)}</i>`;
    const body = code.slice(7);
    const n = body.length / 2;
    for (let i = 0; i < n; i++) {
      const isVendor = segs.vendor && i === n - 1;
      out += `<i class="${isVendor ? "s5" : (body.substr(i * 2, 2) === "00" ? "s0" : "s3")}">${body.substr(i * 2, 2)}</i>`;
    }
    return out;
  }

  function invoiceTagsHtml(line) {
    if (!line) return "";
    const bits = [];
    if (line.qty) bits.push(`qty ${line.qty} ${esc(line.uom || "")}`);
    if (line.rate) bits.push(`rate ${line.rate}`);
    if (line.hsn) bits.push(`HSN ${esc(line.hsn)}`);
    if (line.vendor) bits.push(`vendor on line: ${esc(line.vendor)}`);
    if (line.ocr) bits.push("via OCR");
    if (!bits.length) return "";
    return `<span class="ce-tag">${bits.join(" · ")}${line.__file ? " — from invoice" : " — typed"}</span> `;
  }

  function phase1Line(res) {
    const near = (res.phase1 && res.phase1.near) || [];
    if (!near.length) return `<b>1</b> no existing code — nothing close enough to flag`;
    const top = near[0];
    return `<b>1</b> no existing code &nbsp; nearest: <code>${esc(top.code)}</code> (${top.score}%)`;
  }

  function phase2Line(res) {
    const g = res.phase2 && res.phase2.group;
    const step = ((res.phase2 && res.phase2.steps) || []).find((s) => s.level === "group") || {};
    const mb = res.matched_by;
    const mbNote = mb === "rules" && !res.llm_available ? " · LLM not configured" : "";
    if (g) {
      return `<b>2</b> ${esc(g.name)} &nbsp; matched ${step.score != null ? step.score + "%" : ""}
        <span class="layer ${esc(mb)}">by ${esc(mb)}${mbNote}</span>`;
    }
    if (res.phase2 && res.phase2.subhead) {
      return `<b>2</b> no matching group — suggested home: ${esc(res.phase2.head.name)} / ${esc(res.phase2.subhead.name)}`;
    }
    return `<b>2</b> no matching group, and nowhere close — pick a head and sub-head (Edit)`;
  }

  function phase3Line(c) {
    const res = c.res;
    const slots = (res.phase3 && res.phase3.slots) || [];
    const used = slots.filter((s) => s.label);
    const v = res.phase3 && res.phase3.vendor;
    if (!used.length && !(v && v.label)) return `<b>3</b> this group has no specifications`;
    const parts = used.map((s) => {
      if (s.value) return `<span class="ce-specok">${esc(s.label)} ${esc(s.value)} <code>${esc(s.code || s.proposed_code || "··")}</code></span>`;
      if ((s.options || []).length) {
        const opts = s.options.map((o) => `<option value="${o.id}">${esc(o.value)} · ${o.code}</option>`).join("");
        return `<span class="ce-specneed"><label>${esc(s.label)}?</label>
          <select data-quickslot="${s.slot}" data-i="${c.i}"><option value="">— choose —</option>${opts}
          <option value="__new">+ new…</option></select></span>`;
      }
      return `<span class="ce-specneed"><label>${esc(s.label)}?</label>
        <input data-quickslot-text="${s.slot}" data-i="${c.i}" placeholder="type it, Enter to set" size="14"></span>`;
    });
    if (v && v.label) {
      if (v.value) parts.push(`<span class="ce-specok">${esc(v.label)} ${esc(v.value)} <code>${esc(v.code || v.proposed_code || "··")}</code></span>`);
      else parts.push(`<span class="ce-specneed"><label>${esc(v.label)}?</label>
        <input data-quickvendor-text="1" data-i="${c.i}" placeholder="only if named on this line, Enter to set" size="22"></span>`);
    }
    return `<b>3</b> ${parts.join(" &nbsp; ")}`;
  }

  function renderCascade(c) {
    const heads = HEADS_CACHE || [];
    const headOpts = heads.map((h) => `<option value="${h.id}" data-code2="${h.code2}" ${String(h.id) === String(c.sel.headId) ? "selected" : ""}>${esc(h.name)} (${h.code2})</option>`).join("")
      + `<option value="__newhead" ${c.sel.headId === "__newhead" ? "selected" : ""}>+ create a new head here…</option>`;
    const subOpts = (c.sel.subheads || []).map((s) => `<option value="${s.id}" data-code2="${s.code2}" ${String(s.id) === String(c.sel.subId) ? "selected" : ""}>${esc(s.name)} (${s.code2})</option>`).join("")
      + `<option value="__newsubhead" ${c.sel.subId === "__newsubhead" ? "selected" : ""}>+ create a new sub-head here…</option>`;
    const groupOpts = (c.sel.groups || []).map((g) => `<option value="${g.id}" data-code3="${g.code3}" data-name="${esc(g.name)}" ${String(g.id) === String(c.sel.groupId) ? "selected" : ""}>${esc(g.name)} (${g.code3})</option>`).join("")
      + `<option value="__newgroup" ${c.sel.groupId === "__newgroup" ? "selected" : ""}>+ create a new group here…</option>`;
    return `<div class="ce-cascade">
      <div class="slot"><label>Head</label><select data-cas="head" data-i="${c.i}">
        <option value="">— choose —</option>${headOpts}</select><span class="cc"></span></div>
      <div class="slot"><label>Sub-head</label><select data-cas="subhead" data-i="${c.i}" ${c.sel.headId ? "" : "disabled"}>
        <option value="">— choose —</option>${subOpts}</select><span class="cc"></span></div>
      <div class="slot"><label>Group</label><select data-cas="group" data-i="${c.i}" ${c.sel.subId ? "" : "disabled"}>
        <option value="">— choose —</option>${groupOpts}</select><span class="cc"></span></div>
    </div>`;
  }

  function renderSpecSelects(c) {
    if (!c.sel.groupId || c.sel.groupId === "__newgroup") return "";
    const codeFor = (slot) => (c.previewRes && (c.previewRes.slots || []).find((s) => s.slot === slot) || {}).code;
    const rows = [1, 2, 3, 4].map((slot) => {
      const label = c.sel.labels[String(slot)] || `Spec ${slot}`;
      const cur = c.sel.slots[slot];
      const opts = (c.sel.specOptions[slot] || []).map((o) => `<option value="${o.id}" ${String(o.id) === String(cur) ? "selected" : ""}>${esc(o.value)}</option>`).join("");
      const newOpt = cur && String(cur).startsWith("new:") ? `<option value="${esc(cur)}" selected>${esc(cur.slice(4))}</option>` : "";
      return `<div class="slot"><label title="${esc(label)}">${esc(label)}</label>
        <select data-cas-slot="${slot}" data-i="${c.i}">
          <option value="">— none —</option>${opts}${newOpt}
          <option value="__new">+ add a new value…</option>
        </select><span class="cc">${esc(codeFor(slot) || "··")}</span></div>`;
    }).join("");

    let vrow = "";
    const vopts = c.sel.vendorOptions && c.sel.vendorOptions.length;
    const vlabel = c.sel.labels.vendor || "Vendor";
    const curV = c.sel.vendor;
    const voptsHTML = (c.sel.vendorOptions || []).map((o) => `<option value="${o.id}" ${String(o.id) === String(curV) ? "selected" : ""}>${esc(o.value)}</option>`).join("");
    const newVOpt = curV && String(curV).startsWith("new:") ? `<option value="${esc(curV)}" selected>${esc(curV.slice(4))}</option>` : "";
    const vcode = c.previewRes && c.previewRes.vendor ? c.previewRes.vendor.code : null;
    vrow = `<div class="slot"><label>${esc(vlabel)}</label>
      <select data-cas-vendor="1" data-i="${c.i}"><option value="">— none —</option>${voptsHTML}${newVOpt}
      <option value="__new">+ add a new vendor…</option></select>
      <span class="cc">${esc(vcode || "··")}</span></div>`;

    let trow = "";
    if (ERP_ENABLED && TAX_CACHE !== null) {
      const taxes = TAX_CACHE || [];
      // Auto-select if only 1 option and none is selected yet
      if (taxes.length === 1 && !c.sel.tax) c.sel.tax = taxes[0];
      
      const taxOpts = taxes.map((t) => `<option value="${esc(t)}" ${t === c.sel.tax ? "selected" : ""}>${esc(t)}</option>`).join("");
      trow = `<div class="slot" style="margin-top:12px; border-top:1px solid var(--mm-b0); padding-top:12px;">
        <label>Tax Template <span style="color:var(--mm-bad)">*</span></label>
        <select data-cas-tax="1" data-i="${c.i}">
          <option value="">— mandatory —</option>${taxOpts}
        </select><span class="cc"></span></div>
        <div class="slot" style="margin-top:12px; border-top:1px solid var(--mm-b0); padding-top:12px;">
        <label>HSN Code (if required)</label>
        <input type="text" data-cas-hsn="1" data-i="${c.i}" value="${esc(c.sel.hsn || c.input.hsn || "")}" placeholder="Enter HSN Code">
        <span class="cc"></span></div>`;
    }

    return `<div class="slots">${rows}${vrow}${trow}</div>`;
  }

  function renderNewGroupForm(c) {
    const hints = c.input.hints || {};
    const hrow = c.sel.headId === "__newhead" ? `<div class="slot"><label>New Head</label><input data-ng="hname" data-i="${c.i}" placeholder="name this head" value="${esc(hints.new_head_name || '')}"><span class="cc"></span></div>` : "";
    const srow = c.sel.subId === "__newsubhead" ? `<div class="slot"><label>New Sub-head</label><input data-ng="sname" data-i="${c.i}" placeholder="name this sub-head" value="${esc(hints.new_subhead_name || '')}"><span class="cc"></span></div>` : "";
    
    const l1 = hints.new_group_labels && hints.new_group_labels["1"] ? esc(hints.new_group_labels["1"]) : "";
    const l2 = hints.new_group_labels && hints.new_group_labels["2"] ? esc(hints.new_group_labels["2"]) : "";
    const l3 = hints.new_group_labels && hints.new_group_labels["3"] ? esc(hints.new_group_labels["3"]) : "";
    const l4 = hints.new_group_labels && hints.new_group_labels["4"] ? esc(hints.new_group_labels["4"]) : "";
    const vl = hints.new_group_labels && hints.new_group_labels["vendor"] ? esc(hints.new_group_labels["vendor"]) : "";
    
    return `<div class="slots ce-newgroup">
      ${hrow}
      ${srow}
      <div class="slot"><label>New Group</label><input data-ng="name" data-i="${c.i}" placeholder="name this group" value="${esc(hints.new_group_name || '')}"><span class="cc"></span></div>
      <div class="slot"><label>UoM</label><input data-ng="uom" data-i="${c.i}" placeholder="Nos" value="${esc(c.input.uom || '')}"><span class="cc"></span></div>
      <div class="slot"><label>Spec 1 label</label>
        <input data-ng="label1" data-i="${c.i}" placeholder="Category (e.g. Type)" value="${l1}" style="width:33%; margin-right:3%">
        <input data-ng="val1" data-i="${c.i}" placeholder="Value (e.g. U+2)" value="${esc(hints.s1 || '')}" style="width:40%">
        <span class="cc">${esc(hints.s1 ? "01" : "··")}</span>
      </div>
      <div class="slot"><label>Spec 2 label</label>
        <input data-ng="label2" data-i="${c.i}" placeholder="Category (e.g. Size)" value="${l2}" style="width:33%; margin-right:3%">
        <input data-ng="val2" data-i="${c.i}" placeholder="Value (e.g. 90x90)" value="${esc(hints.s2 || '')}" style="width:40%">
        <span class="cc">${esc(hints.s2 ? "01" : "··")}</span>
      </div>
      <div class="slot"><label>Spec 3 label</label>
        <input data-ng="label3" data-i="${c.i}" value="${l3}" style="width:33%; margin-right:3%" placeholder="Category">
        <input data-ng="val3" data-i="${c.i}" placeholder="Value" value="${esc(hints.s3 || '')}" style="width:40%">
        <span class="cc">${esc(hints.s3 ? "01" : "··")}</span>
      </div>
      <div class="slot"><label>Spec 4 label</label>
        <input data-ng="label4" data-i="${c.i}" value="${l4}" style="width:33%; margin-right:3%" placeholder="Category">
        <input data-ng="val4" data-i="${c.i}" placeholder="Value" value="${esc(hints.s4 || '')}" style="width:40%">
        <span class="cc">${esc(hints.s4 ? "01" : "··")}</span>
      </div>
      <div class="slot"><label>Vendor label</label>
        <input data-ng="vlabel" data-i="${c.i}" placeholder="blank = no vendor" value="${vl}" style="width:33%; margin-right:3%">
        <input data-ng="vval" data-i="${c.i}" placeholder="Value" value="${esc(hints.vendor_text || '')}" style="width:40%">
        <span class="cc">${esc(hints.vendor_text ? "00" : "··")}</span>
      </div>
      <div class="slot"><label></label><button class="ghost sm" data-act="apply-newgroup" data-i="${c.i}">Create &amp; use this hierarchy</button><span class="cc"></span></div>
    </div>`;
  }

  function cardHtml(c) {
    if (c.status === "pending") {
      return `<div class="card" data-i="${c.i}"><div class="top"><div><div class="src">${esc(c.line.description)}</div>
        <div class="sub"><span class="ce-spin"></span>resolving…</div></div></div></div>`;
    }
    const res = c.res;
    if (c.status === "error" || !res) {
      return `<div class="card" data-i="${c.i}"><div class="top"><div><div class="src">${esc(c.line.description)}</div>
        <div class="sub">could not resolve this line</div></div><span class="badge b-block">error</span></div>
        <div class="blockers">${esc((res && res.blockers && res.blockers[0]) || "unknown error")}</div></div>`;
    }

    const tags = invoiceTagsHtml(c.line);

    if (c.submitted) {
      const isPushPending = ERP_ENABLED && !c.pushedToErp;
      return `<div class="card" data-i="${c.i}">
        <div class="top"><div><div class="src">${esc(c.line.description)}</div>
          <div class="sub">${tags}issued ${c.pushedToErp ? "· synced to ERP" : ""}</div></div>
          <span class="badge b-done">${esc(c.submittedCode)}</span></div>
        ${c.learnedAlias ? `<div class="ce-tag learn">learned — this wording now matches "${esc(c.learnedAlias)}" automatically next time</div>` : ""}
        ${isPushPending
          ? `<div class="codebar" style="margin-top:12px; border-top:1px solid var(--mm-b0,#262f3d); padding-top:12px;">
              <span class="muted" style="font-size:13px; color:var(--tx3,#687986);">Not synced to ERPNext yet.</span>
              <span class="grow"></span>
              <button class="primary sm" data-act="push-card" data-i="${c.i}" ${c.pushing ? "disabled" : ""}>
                ${c.pushing ? "Pushing..." : "Push to ERP"}
              </button>
             </div>`
          : ""}
      </div>`;
    }

    const code = c.editing ? (c.previewRes && c.previewRes.code) : res.code;
    const segs = c.editing ? (c.previewRes && c.previewRes.segments) : res.segments;
    const blockers = c.editing
      ? (c.previewRes ? c.previewRes.blockers : ["choose a head, sub-head and group"])
      : (res.blockers || []);
    const isConflictOnly = blockers.length === 1 && blockers[0].includes("already issued");
    const disabled = !code || (blockers.length > 0 && !isConflictOnly);
    const isExisting = res.outcome === "exists";
    const badgeClass = isExisting ? "b-exists" : "b-new";
    const badgeText = isExisting ? "already exists" : (c.editing ? "editing" : "new");

    const existingBlock = isExisting ? `
      <div class="hits" style="margin-bottom:15px"><div class="hit"><code>${esc(res.phase1.hit.code)}</code><span>${esc(res.phase1.hit.name || "")}</span>
        <span class="sc">${esc(res.phase1.hit.source)} · ${esc(res.phase1.hit.how)} · ${res.phase1.hit.score}%</span></div></div>
      <div class="row" style="margin-bottom:15px; padding-bottom:15px; border-bottom:1px dashed var(--line);">
        <button class="ghost sm" data-act="copy" data-code="${esc(res.phase1.hit.code)}" data-i="${c.i}">Copy existing code</button>
        <span style="font-size:12px; color:var(--tx2); margin-left:10px;">Or continue editing below to create a new one</span>
      </div>
    ` : '';

    return `<div class="card" data-i="${c.i}">
      <div class="top"><div><div class="src">${esc(c.line.description)}</div>
        <div class="sub">${tags}${isExisting ? "phase 1 stopped here — this item already has a code" : "proposed code"}</div></div>
        <span class="badge ${badgeClass}">${badgeText}</span></div>

      ${existingBlock}

      ${c.sel ? `${renderCascade(c)}${c.sel.groupId === "__newgroup" ? renderNewGroupForm(c) : renderSpecSelects(c)}` : ""}

      <div class="codebar">
        <div class="code">${segHtml(code, segs)}</div>
        <span class="grow"></span>
        ${c.sel && c.sel.groupId && c.sel.groupId !== "__newgroup" ? `<button class="ghost sm" data-act="view-group-items" data-i="${c.i}">View Group Items</button>` : ""}
        <button class="primary sm" data-act="submit" data-i="${c.i}" ${disabled || c.submitting ? "disabled" : ""}>${c.submitting ? "Submitting…" : "Submit (Enter)"}</button>
      </div>
      <div class="legend">
        <span><i class="s1">▮</i> head + sub-head</span><span><i class="s2">▮</i> group</span>
        <span><i class="s3">▮</i> specification</span><span><i class="s0">▮</i> 00 = not applicable</span>
        <span><i class="s5">▮</i> vendor</span>
      </div>
      ${blockers.length ? `<div class="blockers">${blockers.map(esc).join("<br>")}</div>` : ""}
    </div>`;
  }

  // ─────────────────────────────────────────────────── cascade fetching
  async function loadSubheads(c, headId) {
    const d = await getJSON(`/api/v1/cascade/subheads?head=${encodeURIComponent(headId)}`);
    c.sel.subheads = d.subheads;
    updateCard(c);
  }
  async function loadGroups(c, subId) {
    const d = await getJSON(`/api/v1/cascade/groups?subhead=${encodeURIComponent(subId)}`);
    c.sel.groups = d.groups;
    updateCard(c);
  }
  async function loadSlots(c, groupId) {
    const d = await getJSON(`/api/v1/cascade/slots?group=${encodeURIComponent(groupId)}`);
    c.sel.labels = d.labels || {};
    c.sel.specOptions = { 1: (d.specs["1"] || []), 2: (d.specs["2"] || []), 3: (d.specs["3"] || []), 4: (d.specs["4"] || []) };
    c.sel.vendorOptions = d.specs.vendor || [];
    updateCard(c);
  }

  async function previewCard(c) {
    if (!c.sel || !c.sel.groupId || c.sel.groupId === "__newgroup") { updateCard(c); return; }
    const body = { group_id: c.sel.groupId };
    [1, 2, 3, 4].forEach((slot) => { if (c.sel.slots[slot]) body["s" + slot] = c.sel.slots[slot]; });
    if (c.sel.vendor) body.vendor = c.sel.vendor;
    try {
      c.previewRes = await postJSON("/api/v1/resolve/preview", body);
    } catch (err) {
      c.previewRes = { code: null, blockers: [err.message], segments: null };
    }
    c.idemKey = uid();
    updateCard(c);
  }

  async function beginEdit(c) {
    ensureUser();
    await ensureHeads();
    await ensureTaxes();
    c.editing = true;
    c.previewRes = null;
    c.sel = {
      headId: null, headCode: null, subId: null, subCode: null, subheads: [],
      groupId: null, groupName: null, groupCode3: null, groups: [],
      labels: {}, slots: {}, specOptions: { 1: [], 2: [], 3: [], 4: [] }, vendor: null, vendorOptions: [],
      tax: c.input.tax || null,
    };
    updateCard(c);

    const g = c.res.phase2 && c.res.phase2.group;
    if (g) {
      c.sel.headId = g.head_id; c.sel.headCode = g.head_code;
      await loadSubheads(c, g.head_id);
      c.sel.subId = g.sub_id; c.sel.subCode = g.sub_code;
      await loadGroups(c, g.sub_id);
      c.sel.groupId = g.id; c.sel.groupName = g.name; c.sel.groupCode3 = g.code3;
      await loadSlots(c, g.id);
      (c.res.phase3.slots || []).forEach((s) => {
        if (s.specval_id) c.sel.slots[s.slot] = String(s.specval_id);
        else if (s.value) c.sel.slots[s.slot] = "new:" + s.value;
      });
      const v = c.res.phase3.vendor;
      if (v) {
        if (v.specval_id) c.sel.vendor = String(v.specval_id);
        else if (v.value) c.sel.vendor = "new:" + v.value;
      }
      await previewCard(c);
    } else if (c.input.hints && c.input.hints.new_group_name) {
      if (c.input.hints.new_head_name) {
        c.sel.headId = "__newhead";
        c.sel.subId = "__newsubhead";
        c.sel.groupId = "__newgroup";
      } else if (c.input.hints.new_subhead_name) {
        c.sel.headId = c.res.phase2.head.id; c.sel.headCode = c.res.phase2.head.code2;
        await loadSubheads(c, c.sel.headId);
        c.sel.subId = "__newsubhead";
        c.sel.groupId = "__newgroup";
      } else {
        c.sel.headId = c.res.phase2.head.id; c.sel.headCode = c.res.phase2.head.code2;
        await loadSubheads(c, c.sel.headId);
        c.sel.subId = c.res.phase2.subhead.id; c.sel.subCode = c.res.phase2.subhead.code2;
        await loadGroups(c, c.sel.subId);
        c.sel.groupId = "__newgroup";
      }
      updateCard(c);
    } else if (res_subhead(c)) {
      c.sel.headId = c.res.phase2.head.id; c.sel.headCode = c.res.phase2.head.code2;
      await loadSubheads(c, c.sel.headId);
      c.sel.subId = c.res.phase2.subhead.id; c.sel.subCode = c.res.phase2.subhead.code2;
      await loadGroups(c, c.sel.subId);
    } else {
      updateCard(c);
    }
  }
  function res_subhead(c) { return c.res.phase2 && c.res.phase2.subhead; }

  function buildManualPhase3(c) {
    const slots = [];
    for (let slot = 1; slot <= 4; slot++) {
      const label = c.sel.labels[String(slot)] || `Spec ${slot}`;
      const chosen = c.sel.slots[slot];
      if (!chosen) { slots.push({ slot, label, specval_id: null, code: null }); continue; }
      if (String(chosen).startsWith("new:")) {
        const pv = ((c.previewRes && c.previewRes.slots) || []).find((s) => s.slot === slot) || {};
        slots.push({ slot, label, value: chosen.slice(4), proposed_code: pv.code, code: pv.code });
      } else {
        const opt = (c.sel.specOptions[slot] || []).find((o) => String(o.id) === String(chosen));
        slots.push({ slot, label, specval_id: opt ? opt.id : null, code: opt ? opt.code2 : null });
      }
    }
    let vendor = null;
    const vlabel = c.sel.labels.vendor || "Vendor";
    const chosenV = c.sel.vendor;
    if (chosenV && String(chosenV).startsWith("new:")) {
      const pv = (c.previewRes && c.previewRes.vendor) || {};
      vendor = { label: vlabel, value: chosenV.slice(4), proposed_code: pv.code, code: pv.code };
    } else if (chosenV) {
      const opt = (c.sel.vendorOptions || []).find((o) => String(o.id) === String(chosenV));
      vendor = { label: vlabel, specval_id: opt ? opt.id : null, code: opt ? opt.code2 : null, value: opt ? opt.value : null };
    }
    return { slots, vendor };
  }

  async function quickFill(c, hintPatch, vendorText) {
    const newInput = { ...c.input, hints: { ...(c.input.hints || {}), ...hintPatch } };
    if (vendorText !== undefined) newInput.vendor = vendorText;
    try {
      const d = await postJSON("/api/v1/resolve", newInput);
      c.input = newInput; c.res = d; c.idemKey = uid();
    } catch (err) { toast(err.message, "err"); }
    updateCard(c);
  }

  async function submitCard(c) {
    if (c.submitting || c.submitted) return;
    ensureUser();
    c.submitting = true;
    updateCard(c);
    try {
      let proposal, originalGroupId = null;
      if (c.editing) {
        if (!c.sel.groupId || c.sel.groupId === "__newgroup" || !c.previewRes || (c.previewRes.blockers || []).length) {
          toast("Finish choosing the group and every specification first", "err");
          c.submitting = false; updateCard(c); return;
        }
        if (ERP_ENABLED && TAX_CACHE !== null && !c.sel.tax) {
          toast("Please select an Item Tax Template", "err");
          c.submitting = false; updateCard(c); return;
        }
        
        c.input.tax = c.sel.tax;
        const { slots, vendor } = buildManualPhase3(c);
        proposal = {
          input: c.input,
          phase2: { group: { id: c.sel.groupId, name: c.sel.groupName, code3: c.sel.groupCode3, head_code: c.sel.headCode, sub_code: c.sel.subCode, labels: c.sel.labels } },
          phase3: { slots, vendor },
          code: c.previewRes.code, blockers: [], new_group: false,
        };
        originalGroupId = c.res.phase2 && c.res.phase2.group && c.res.phase2.group.id;
      } else {
        proposal = c.res;
      }
      const d = await postJSON("/api/v1/commit", { proposal, idempotency_key: c.idemKey, push_erp: !!ERP_ENABLED });
      c.submitted = true; c.submittedCode = d.code;
      c.pushedToErp = !!(d.erp && d.erp.ok);
      toast(d.idempotent ? `${d.code} — already issued (that click was caught)` : `${d.code} issued`, "ok");

      if (c.editing && c.sel.groupId && String(c.sel.groupId) !== String(originalGroupId || "")) {
        try {
          await postJSON("/api/v1/alias/add", { scope: "group", ref_id: c.sel.groupId, term: c.line.description });
          c.learnedAlias = c.sel.groupName;
          toast(`Learned: this wording now matches "${c.sel.groupName}"`, "ok");
        } catch (e) { /* non-fatal — the item is already issued either way */ }
      }
    } catch (err) {
      if (err.code === "CONFLICT" && err.detail && err.detail.existing_item) {
        showConflictModal(c, err.detail);
      } else {
        toast(err.message || "could not submit", "err");
      }
    }
    c.submitting = false;
    updateCard(c);
  }

  async function pushCardToErp(c) {
    if (c.pushing) return;
    c.pushing = true;
    updateCard(c);
    try {
      const res = await postJSON(`/api/v1/item/${encodeURIComponent(c.submittedCode)}/push`, {});
      if (!res.ok) throw new Error(res.error || "Failed to push");
      c.pushedToErp = true;
      toast(`${c.submittedCode} synced to ERPNext`, "ok");
    } catch (e) {
      toast(e.message || "Failed to push to ERPNext", "err");
    } finally {
      c.pushing = false;
      updateCard(c);
    }
  }

  async function showGroupItemsModal(c) {
    if (!c.sel || !c.sel.groupId || c.sel.groupId === "__newgroup") return;
    try {
      const d = await getJSON(`/api/v1/item?group_id=${c.sel.groupId}&status=active`);
      const items = d.items || [];
      const listHtml = items.length ? items.map(i => `
        <div style="padding:10px; border-bottom:1px solid var(--line); display:flex; flex-direction:column; gap:4px;">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <code style="color:var(--acc); font-size:13px; font-weight:bold;">${esc(i.code)}</code>
            ${ERP_ENABLED && !i.erp_synced_at ? '<span style="font-size:10px; background:var(--warn); color:#fff; padding:2px 4px; border-radius:3px;">NOT SYNCED</span>' : ''}
          </div>
          <div style="font-weight:600; font-size:13px;">${esc(i.name)}</div>
          ${i.description && i.description !== i.name ? `<div style="color:var(--tx2); font-size:12px;">${esc(i.description)}</div>` : ""}
        </div>
      `).join("") : '<div style="color:var(--tx2); padding:20px 0; text-align:center;">No items exist in this group yet.</div>';

      modal(`
        <h2 style="margin-top:0; color:var(--tx1)">Existing Items in ${esc(c.sel.groupName)}</h2>
        <div style="max-height:400px; overflow-y:auto; background:var(--panel2); border-radius:6px; border:1px solid var(--line); margin-bottom:15px;">
          ${listHtml}
        </div>
        <div class="btnrow" style="display:flex; justify-content:flex-end; gap:8px;">
          <button class="ghost" onclick="closeModal()">Close</button>
        </div>
      `);
    } catch (err) {
      toast("Could not fetch group items: " + err.message, "err");
    }
  }

  window.acceptExistingItem = async function(cIdx, code, name, desc) {
    const c = state.cards[cIdx];
    if (!c) return;
    c.res.outcome = "exists";
    c.res.phase1 = { hit: { code, name, source: "accepted conflict", how: "manual", score: 100 } };
    c.submitted = false; // reset so it shows the top phase1 match
    closeModal();
    updateCard(c);
  };

  async function showConflictModal(c, detail) {
    const ex = detail.existing_item;
    
    // Fetch group context if possible
    let groupItemsHtml = '<div class="ce-spin"></div> Fetching other items in this group...';
    
    modal(`
      <h2 style="margin-top:0; color:var(--warn)">Code Collision</h2>
      <p style="margin-bottom:15px;">The code <code>${esc(detail.code)}</code> has already been issued to another item.</p>
      
      <div style="background:var(--panel2); padding:12px; border-radius:6px; margin-bottom:15px; border:1px solid var(--line); border-left:4px solid var(--warn);">
        <div style="font-size:11px; color:var(--tx3); text-transform:uppercase; letter-spacing:1px; margin-bottom:6px;">Conflicting Item</div>
        <div style="font-weight:600; margin-bottom:4px;">${esc(ex.name)}</div>
        ${ex.description && ex.description !== ex.name ? `<div style="color:var(--tx2); font-size:13px;">${esc(ex.description)}</div>` : ""}
      </div>
      
      <h3 style="font-size:13px; color:var(--tx2); margin-top:20px; margin-bottom:10px;">Other items in this group:</h3>
      <div id="conflict-group-list" style="max-height:200px; overflow-y:auto; background:var(--panel2); border-radius:6px; border:1px solid var(--line); margin-bottom:15px; padding:10px;">
        ${groupItemsHtml}
      </div>

      <p style="margin-bottom:20px; color:var(--tx2); font-size:13px;">
        To proceed, either change your specifications to differentiate this item, or accept the existing code.
      </p>
      
      <div class="btnrow" style="display:flex; justify-content:flex-end; gap:8px;">
        <button class="ghost" onclick="closeModal()">Close & Edit Specs</button>
        <button class="primary" onclick="acceptExistingItem(${c.i}, '${esc(detail.code)}', '${esc(ex.name).replace(/'/g, "\\'")}', '${esc(ex.description || '').replace(/'/g, "\\'")}')">Accept Existing Item</button>
      </div>
    `);

    if (c.sel && c.sel.groupId && c.sel.groupId !== "__newgroup") {
      try {
        const d = await getJSON(`/api/v1/item?group_id=${c.sel.groupId}&status=active`);
        const items = d.items || [];
        const listHtml = items.length ? items.map(i => `
          <div style="padding:6px 0; border-bottom:1px solid var(--line); display:flex; gap:10px; align-items:baseline;">
            <code style="color:var(--acc); font-size:12px;">${esc(i.code)}</code>
            <span style="font-size:12px;">${esc(i.name)}</span>
          </div>
        `).join("") : '<div style="color:var(--tx2); font-size:12px;">No other items exist in this group yet.</div>';
        const listEl = document.getElementById('conflict-group-list');
        if (listEl) listEl.innerHTML = listHtml;
      } catch (err) {
        const listEl = document.getElementById('conflict-group-list');
        if (listEl) listEl.innerHTML = '<span style="color:var(--warn)">Failed to load group items.</span>';
      }
    } else {
      const listEl = document.getElementById('conflict-group-list');
      if (listEl) listEl.innerHTML = '<span style="color:var(--tx2)">Group context not available.</span>';
    }
  }

  // ──────────────────────────────────────────────────────── delegation
  function wireResultsDelegation() {
    const box = $("#ce-reslist");

    box.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-act]");
      if (!b) return;
      const i = +b.dataset.i;
      const c = state.cards[i];
      if (!c) return;
      const act = b.dataset.act;

      if (act === "copy") {
        navigator.clipboard?.writeText(b.dataset.code);
        toast(`${b.dataset.code} copied`, "ok");
        return;
      }
      if (act === "forcenew") {
        c.status = "pending"; updateCard(c);
        try {
          c.res = await postJSON("/api/v1/resolve", { ...c.input, hints: { ...(c.input.hints || {}), force_new: true } });
          c.status = "ready"; c.idemKey = uid();
        } catch (err) { c.res = { outcome: "error", blockers: [err.message] }; c.status = "error"; }
        updateCard(c);
        return;
      }
      if (act === "edit") { await beginEdit(c); return; }
      if (act === "cancel-edit") { c.editing = false; c.sel = null; c.previewRes = null; updateCard(c); return; }
      if (act === "submit") { await submitCard(c); return; }
      if (act === "view-group-items") { await showGroupItemsModal(c); return; }
      if (act === "push-card") { await pushCardToErp(c); return; }

      if (act === "apply-newgroup") {
        const card = document.querySelector(`.card[data-i="${i}"]`);
        
        let headName = null;
        if (c.sel.headId === "__newhead") {
          headName = card.querySelector('[data-ng="hname"]')?.value.trim();
          if (!headName) return toast("Name the new head", "err");
        }
        
        let subName = null;
        if (c.sel.subId === "__newsubhead") {
          subName = card.querySelector('[data-ng="sname"]')?.value.trim();
          if (!subName) return toast("Name the new sub-head", "err");
        }

        const name = card.querySelector('[data-ng="name"]').value.trim();
        if (!name) return toast("Name the new group", "err");
        
        const uom = card.querySelector('[data-ng="uom"]').value.trim();
        
        const labels = {};
        const hintsPatch = {};
        
        [1, 2, 3, 4].forEach(n => {
          const lbl = card.querySelector(`[data-ng="label${n}"]`)?.value.trim();
          const val = card.querySelector(`[data-ng="val${n}"]`)?.value.trim();
          if (lbl) labels[String(n)] = lbl;
          if (val) hintsPatch[`s${n}`] = val;
        });

        const vlabel = card.querySelector('[data-ng="vlabel"]')?.value.trim();
        const vval = card.querySelector('[data-ng="vval"]')?.value.trim();
        if (vlabel) labels.vendor = vlabel;
        if (vval) hintsPatch.vendor_text = vval;
        
        try {
          const d = await postJSON("/api/v1/resolve", {
            ...c.input, uom: uom || c.input.uom,
            hints: { 
              ...(c.input.hints || {}),
              ...hintsPatch,
              new_head_name: headName,
              new_subhead_name: subName,
              subhead_id: c.sel.subId === "__newsubhead" ? null : c.sel.subId, 
              new_group_name: name, 
              new_group_labels: labels 
            },
          });
          
          // Save the hints in c.input so they survive edit toggles
          c.input.uom = uom || c.input.uom;
          c.input.hints = d.input.hints; // the backend returns the updated hints
          
          c.res = d; c.editing = false; c.sel = null; c.idemKey = uid();
          toast(`Hierarchy updated. Proposed as ${(d.segments || {}).group || "···"}`, "ok");
        } catch (err) { toast(err.message, "err"); }
        updateCard(c);
      }
    });

    box.addEventListener("change", async (e) => {
      const t = e.target;
      const i = +t.dataset.i;
      if (Number.isNaN(i)) return;
      const c = state.cards[i];
      if (!c) return;

      if (t.dataset.cas === "head") {
        c.sel.headId = t.value || null;
        c.sel.headCode = t.value && t.value !== "__newhead" ? t.selectedOptions[0].dataset.code2 : null;
        if (t.value === "__newhead") {
          c.sel.subId = "__newsubhead";
          c.sel.groupId = "__newgroup";
        } else {
          Object.assign(c.sel, { subId: null, subCode: null, groupId: null, groupName: null, groupCode3: null, labels: {}, slots: {}, vendor: null, subheads: [], groups: [] });
        }
        c.previewRes = null;
        updateCard(c);
        if (c.sel.headId && c.sel.headId !== "__newhead") await loadSubheads(c, c.sel.headId);
        return;
      }
      if (t.dataset.cas === "subhead") {
        c.sel.subId = t.value || null;
        c.sel.subCode = t.value && t.value !== "__newsubhead" ? t.selectedOptions[0].dataset.code2 : null;
        if (t.value === "__newsubhead") {
          c.sel.groupId = "__newgroup";
        } else {
          Object.assign(c.sel, { groupId: null, groupName: null, groupCode3: null, labels: {}, slots: {}, vendor: null, groups: [] });
        }
        c.previewRes = null;
        updateCard(c);
        if (c.sel.subId && c.sel.subId !== "__newsubhead") await loadGroups(c, c.sel.subId);
        return;
      }
      if (t.dataset.cas === "group") {
        c.previewRes = null;
        if (t.value === "__newgroup") { c.sel.groupId = "__newgroup"; updateCard(c); return; }
        c.sel.groupId = t.value || null;
        const opt = t.value ? t.selectedOptions[0] : null;
        c.sel.groupName = opt ? opt.dataset.name : null;
        c.sel.groupCode3 = opt ? opt.dataset.code3 : null;
        c.sel.slots = {}; c.sel.vendor = null;
        updateCard(c);
        if (c.sel.groupId) await loadSlots(c, c.sel.groupId);
        if (c.sel.groupId) await previewCard(c);
        return;
      }
      if (t.dataset.casSlot) {
        const slot = t.dataset.casSlot;
        if (t.value === "__new") {
          const val = prompt("New value for this specification:");
          if (!val) { t.value = ""; return; }
          c.sel.slots[slot] = "new:" + val;
        } else {
          c.sel.slots[slot] = t.value || null;
        }
        await previewCard(c);
        return;
      }
      if (t.dataset.casVendor) {
        if (t.value === "__new") {
          const val = prompt("New vendor / maker name:");
          if (!val) { t.value = ""; c.sel.vendor = null; }
          else {
          c.sel.vendor = "new:" + val;
          }
        } else {
          c.sel.vendor = t.value || null;
        }
        await previewCard(c);
      }
      if (t.dataset.casTax) {
        c.sel.tax = t.value || null;
        updateCard(c);
      }
      if (t.dataset.casHsn) {
        c.sel.hsn = t.value.trim() || null;
        updateCard(c);
      }
      if (t.dataset.quickslot) {
        let val = t.value;
        if (val === "__new") { val = prompt("New value:"); if (!val) { t.value = ""; return; } }
        if (!val) return;
        await quickFill(c, { ["s" + t.dataset.quickslot]: val });
      }
    });

    box.addEventListener("change", async (e) => {
      const t = e.target;
      if (t.dataset.casHsn) {
        const i = +t.dataset.i;
        if (!Number.isNaN(i) && state.cards[i]) {
            state.cards[i].sel.hsn = t.value.trim() || null;
        }
        return;
      }
    });

    box.addEventListener("input", async (e) => {
      const t = e.target;
      if (t.dataset.casHsn) {
        const i = +t.dataset.i;
        if (!Number.isNaN(i) && state.cards[i]) {
            state.cards[i].sel.hsn = t.value.trim() || null;
        }
      }
    });

    box.addEventListener("keydown", async (e) => {
      const t = e.target;
      if (e.key === "Enter" && (t.dataset.quickslotText || t.dataset.quickvendorText)) {
        e.preventDefault();
        const i = +t.dataset.i;
        const c = state.cards[i];
        const val = t.value.trim();
        if (!val) return;
        if (t.dataset.quickslotText) await quickFill(c, { ["s" + t.dataset.quickslotText]: val });
        else await quickFill(c, {}, val);
        return;
      }
      // Enter submits the card (unless typing in a select/textarea/quick-fill
      // field, where Enter has its own job); Esc backs out of Edit mode.
      // Keeps the screen usable all day without reaching for the mouse.
      if (e.key === "Enter" && !e.shiftKey && !["SELECT", "TEXTAREA"].includes(t.tagName)
          && !t.dataset.quickslotText && !t.dataset.quickvendorText) {
        const card = t.closest(".card");
        if (!card) return;
        const btn = card.querySelector('[data-act="submit"]:not([disabled])');
        if (btn) { e.preventDefault(); btn.click(); }
        return;
      }
      if (e.key === "Escape") {
        const card = t.closest(".card");
        if (!card) return;
        const c = state.cards[+card.dataset.i];
        if (c && c.editing) { c.editing = false; c.sel = null; c.previewRes = null; updateCard(c); }
      }
    });
  }

  // ──────────────────────────────────────────────────────────── init
  function init() {
    const host = document.getElementById("v-create");
    if (!host) return;
    injectStyle();
    host.innerHTML = shell();
    wireIntake();
    wireResultsDelegation();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
