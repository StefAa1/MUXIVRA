/* --------------------------------------------------------------- INVENTORY
   Rack documentation: sites → racks → devices (U positions) → ports → links.
   Views: Layout (visual rack), Συνδέσεις (paged table), Audit (audit).

   Quick Connect: pick one port (in the rack or through the cross-rack search
   over /inventory/ports) and then its partner; the next free cable number is
   suggested. A port can appear in one link only, so busy ports are locked.

   app.js owns the handlers for [data-nd-rack], [data-nd-tab],
   [data-nd-select], [data-nd-port], [data-nd-ports], #nd-quick-cancel and the
   #nd-add-* buttons; this file only adds its own delegated listener for the
   attributes it introduces, so nothing fires twice. */

'use strict';

const ND = {
  rackId: null,
  tab: 'layout',
  layout: null,
  quick: null,
  audit: null,
  deviceId: null,
  links: { q: '', offset: 0, limit: 50, total: 0, rows: [], loaded: false, loading: false, error: '' },
  portSearch: { q: '', rows: [], total: 0, seq: 0, loading: false, error: '' },
  portForm: { open: false, prefix: 'eth', count: 24, kind: 'rj45' },
  edit: null,
};

const ND_TPL_KEY = 'sshdeck.inventory.portTemplates';
const ND_KIND_LABEL = {
  switch: 'switch', router: 'router', 'patch-panel': 'patch panel',
  server: 'server', pdu: 'pdu', other: 'other',
};
const ND_PORT_KIND_LABEL = { rj45: 'rj45', sfp: 'sfp', fiber: 'fiber', power: 'power', other: 'other' };
const ND_DEVICE_KINDS = Object.keys(ND_KIND_LABEL);
const ND_PORT_KINDS = Object.keys(ND_PORT_KIND_LABEL);
const ND_TIMERS = {};

/* ----------------------------------------------------------------- helpers */
function ndDevice(deviceId) {
  const devices = (ND.layout && ND.layout.devices) || [];
  return devices.find((d) => d.id === deviceId) || null;
}

function ndErrorText(err, fallback) {
  const raw = (err && err.message ? String(err.message) : '').trim();
  if (!raw || raw === '[object Object]') return fallback;
  if (raw.indexOf('port_already_linked') === 0) return 'That port is already linked to another connection.';
  if (raw === 'same_port') return 'A port cannot be linked to itself.';
  if (raw.indexOf('position_taken:') === 0) return `That position is already taken by "${raw.slice(15)}".`;
  if (raw === 'device_outside_rack') return 'The device extends past the rack boundaries.';
  if (raw === 'bad_bulk_request') return 'Check the prefix (up to 12 characters) and the count (1–96).';
  if (raw === 'port_exists') return 'A port with that name already exists.';
  if (raw === 'not_found') return 'Record not found — it may have been deleted.';
  if (raw === 'unauthorized') return 'Your session expired — sign in again.';
  if (raw.toLowerCase().indexOf('failed to fetch') >= 0) return 'No connection to the server.';
  return raw.length > 160 ? fallback : raw;
}

function ndDebounce(key, fn, wait) {
  if (ND_TIMERS[key]) clearTimeout(ND_TIMERS[key]);
  ND_TIMERS[key] = setTimeout(() => { ND_TIMERS[key] = null; fn(); }, wait);
}

function ndKindIcon(kind) { return ND_KIND_LABEL[kind] || kind || '—'; }

/* ------------------------------------------------------------------- data */
async function loadInventory() {
  S.nd = S.nd || {};
  try {
    S.nd.tree = await api('/inventory/tree');
  } catch (_) {
    S.nd.tree = { sites: [], links: 0 };
  }
  try {
    S.nd.links = await api('/inventory/links');
  } catch (_) {
    S.nd.links = [];
  }
  ND.links.loaded = false;
  ND.links.error = '';
  if (ND.rackId) {
    try {
      ND.layout = await api(`/inventory/rack/${ND.rackId}`);
    } catch (_) {
      ND.layout = null;
      ND.rackId = null;
      ND.deviceId = null;
    }
  } else {
    ND.layout = null;
  }
  if (ND.tab === 'table') await ndLoadLinks({ silent: true });
  if (ND.tab === 'audit' && ND.audit) {
    try { ND.audit = await api('/inventory/audit'); } catch (_) { /* κρατάμε το παλιό */ }
  }
}

function ndRacks() {
  return ((S.nd && S.nd.tree && S.nd.tree.sites) || []).flatMap((site) =>
    (site.racks || []).map((r) => ({ ...r, siteName: site.name })));
}

async function ndSelectRack(rackId) {
  ND.rackId = rackId;
  ND.quick = null;
  ND.portSearch = { q: '', rows: [], total: 0, seq: ND.portSearch.seq, loading: false, error: '' };
  ND.portForm.open = false;
  try {
    ND.layout = await api(`/inventory/rack/${rackId}`);
  } catch (err) {
    ND.layout = null;
    toast(ndErrorText(err, 'The rack failed to load'), 'err');
  }
  renderPage();
}

function ndNextCable() {
  const used = (S.nd && S.nd.links ? S.nd.links : [])
    .map((link) => (link.cable || '').match(/(\d+)\s*$/))
    .filter(Boolean)
    .map((match) => Number(match[1]));
  const next = (used.length ? Math.max(...used) : 0) + 1;
  return `CAT6-${String(next).padStart(2, '0')}`;
}

/* --------------------------------------------------------- port templates */
function ndTemplates() {
  try {
    const raw = window.localStorage.getItem(ND_TPL_KEY);
    const list = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(list)) return [];
    return list
      .filter((t) => t && typeof t.prefix === 'string' && Number(t.count) > 0)
      .slice(0, 3);
  } catch (_) {
    return [];
  }
}

function ndWriteTemplates(list) {
  try {
    window.localStorage.setItem(ND_TPL_KEY, JSON.stringify(list.slice(0, 3)));
  } catch (_) { /* ιδιωτική περιήγηση: τα πρότυπα μένουν μόνο στη μνήμη */ }
}

function ndRememberTemplate(tpl) {
  const list = ndTemplates().filter((t) => !(
    t.prefix === tpl.prefix && Number(t.count) === Number(tpl.count) && t.kind === tpl.kind));
  list.unshift({ prefix: tpl.prefix, count: Number(tpl.count), kind: tpl.kind });
  ndWriteTemplates(list);
}

/* ------------------------------------------------------------ quick connect */
function ndPortButton(device, port) {
  const selected = ND.quick && ND.quick.portId === port.id;
  const busy = !!port.link_id;
  const cls = 'nd-port' + (busy ? ' linked' : '') + (selected ? ' sel' : '');
  const title = busy ? `in use → ${port.peer || ''}` : 'free';
  return `<button class="${cls}" data-nd-port="${port.id}" data-nd-port-label="${esc(device.name)} · ${esc(port.name)}" title="${esc(title)}"${busy ? ' disabled' : ''}>${esc(port.name)}</button>`;
}

function ndPortResultsHtml(st) {
  if (st.error) return `<p class="hint">${esc(st.error)}</p>`;
  if (!st.q || !st.q.trim()) return '';
  if (st.loading) return '<p class="hint">Searching…</p>';
  if (!st.rows.length) return '<p class="hint">No matching port.</p>';
  return st.rows.map((p) => {
    const busy = Number(p.linked) > 0;
    const selected = ND.quick && ND.quick.portId === p.id;
    return `<button class="nd-pick${selected ? ' sel' : ''}" data-nd-qpick="${p.id}"
        data-nd-qpick-label="${esc(`${p.device} · ${p.name}`)}"
        data-nd-qpick-rack="${esc(`${p.site} · ${p.rack}`)}"
        title="${busy ? 'Already linked' : 'Select this port'}"${busy ? ' disabled' : ''}>
        <b>${esc(p.name)}</b><span>${esc(p.device)}</span>
        <em>${esc(p.site)} · ${esc(p.rack)}</em>${busy ? '<i>in use</i>' : ''}
      </button>`;
  }).join('');
}

function ndQuickSearchLine() {
  const st = ND.portSearch;
  const hint = st.loading ? 'Searching…' : (st.q.trim() ? `${st.total} ports` : 'e.g. sw-01, eth3, R2');
  return `<div class="nd-quick-search">
      <input id="nd-quick-q" type="search" value="${esc(st.q)}" autocomplete="off"
        placeholder="Search for a port in another rack or site…" aria-label="Search for a port in another rack" />
      <span class="nd-quick-hint" id="nd-quick-hint">${esc(hint)}</span>
    </div>
    <div class="nd-pick-list" id="nd-quick-results">${ndPortResultsHtml(st)}</div>`;
}

function ndQuickBar() {
  const q = ND.quick;
  if (!q) {
    return `<div class="nd-quick idle">
        <div class="nd-quick-line">Quick link: click a port in the rack or pick one from the search, then its counterpart — even in another rack.</div>
        ${ndQuickSearchLine()}
      </div>`;
  }
  return `<div class="nd-quick">
      <div class="nd-quick-line">
        <span>From: <b>${esc(q.label)}</b>${q.rack ? ` <span class="muted">(${esc(q.rack)})</span>` : ''} → pick its counterpart</span>
        <input id="nd-quick-cable" value="${esc(q.cable)}" placeholder="cable" aria-label="Cable" />
        <input id="nd-quick-len" type="number" min="0" max="10000" step="0.5" placeholder="meters" aria-label="Length in meters" />
        <button class="btn ghost xs" id="nd-quick-cancel">Cancel</button>
      </div>
      ${ndQuickSearchLine()}
    </div>`;
}

function ndPaintPortSearch() {
  const box = document.querySelector('#nd-quick-results');
  if (box) box.innerHTML = ndPortResultsHtml(ND.portSearch);
  const hint = document.querySelector('#nd-quick-hint');
  if (hint) {
    const st = ND.portSearch;
    hint.textContent = st.loading ? 'Searching…' : (st.q.trim() ? `${st.total} ports` : 'e.g. sw-01, eth3, R2');
  }
}

async function ndRunPortSearch(query) {
  const st = ND.portSearch;
  st.q = String(query || '');
  st.seq += 1;
  const seq = st.seq;
  st.error = '';
  if (!st.q.trim()) {
    st.rows = [];
    st.total = 0;
    st.loading = false;
    ndPaintPortSearch();
    return;
  }
  st.loading = true;
  ndPaintPortSearch();
  try {
    const page = await api(`/inventory/ports?q=${encodeURIComponent(st.q.trim())}&limit=15`);
    if (seq !== st.seq) return;
    st.rows = page.ports || [];
    st.total = Number(page.total || st.rows.length);
  } catch (err) {
    if (seq !== st.seq) return;
    st.rows = [];
    st.total = 0;
    st.error = ndErrorText(err, 'Port search failed.');
  }
  if (seq !== st.seq) return;
  st.loading = false;
  ndPaintPortSearch();
}

function ndRackLabel() {
  if (!ND.layout) return '';
  const rack = ND.layout.rack || {};
  return `${ND.layout.site || ''} · ${rack.name || ''}`;
}

async function ndQuickPort(portId, label, meta) {
  const info = meta || {};
  if (!ND.quick) {
    ND.quick = { portId, label, cable: ndNextCable(), rack: info.rack || ndRackLabel() };
    ND.portSearch = { q: '', rows: [], total: 0, seq: ND.portSearch.seq, loading: false, error: '' };
    renderPage();
    const input = document.querySelector('#nd-quick-cable');
    if (input) { input.focus(); input.select(); }
    return;
  }
  if (ND.quick.portId === portId) {
    ND.quick = null;
    renderPage();
    return;
  }
  await ndCreateLink(ND.quick.portId, portId);
}

async function ndCreateLink(portA, portB) {
  const cableInput = document.querySelector('#nd-quick-cable');
  const lengthInput = document.querySelector('#nd-quick-len');
  const cable = ((cableInput && cableInput.value) || (ND.quick && ND.quick.cable) || ndNextCable()).trim();
  const rawLength = lengthInput && lengthInput.value !== '' ? Number(lengthInput.value) : 0;
  if (!Number.isFinite(rawLength) || rawLength < 0 || rawLength > 10000) {
    toast('Length must be between 0 and 10000 meters', 'err');
    return;
  }
  if (cable.length > 80) {
    toast('Cable name must be 80 characters or fewer', 'err');
    return;
  }
  try {
    await api('/inventory/links', { json: { port_a: portA, port_b: portB, cable, length_m: rawLength } });
    ND.quick = null;
    ND.portSearch = { q: '', rows: [], total: 0, seq: ND.portSearch.seq, loading: false, error: '' };
    await loadInventory();
    renderPage();
    toast('Link recorded');
  } catch (err) {
    toast(ndErrorText(err, 'Linking failed'), 'err');
    await loadInventory();
    renderPage();
  }
}

function ndQuickPick(portId, label, rack) {
  ndQuickPort(portId, label, { rack });
}

/* ------------------------------------------------------------ links table */
function ndCountHtml() {
  const st = ND.links;
  if (st.loading && !st.loaded) return 'Loading…';
  const pages = Math.max(1, Math.ceil(st.total / st.limit));
  const page = Math.min(pages, Math.floor(st.offset / st.limit) + 1);
  let html = `Total <b>${st.total}</b> ${st.total === 1 ? 'link' : 'links'}`;
  if (st.total > st.limit) html += `<span class="sep">·</span>page <b>${page}</b> / ${pages}`;
  return html;
}

function ndPagerHtml() {
  const st = ND.links;
  if (st.total <= st.limit) return '';
  const pages = Math.max(1, Math.ceil(st.total / st.limit));
  const page = Math.min(pages, Math.floor(st.offset / st.limit) + 1);
  return `<div class="nd-pager">
      <button class="btn ghost xs" data-nd-page="first"${page <= 1 ? ' disabled' : ''}>« First</button>
      <button class="btn ghost xs" data-nd-page="prev"${page <= 1 ? ' disabled' : ''}>‹ Previous</button>
      <span class="nd-page-info">Page <b>${page}</b> / ${pages}</span>
      <button class="btn ghost xs" data-nd-page="next"${page >= pages ? ' disabled' : ''}>Next ›</button>
      <button class="btn ghost xs" data-nd-page="last"${page >= pages ? ' disabled' : ''}>Last »</button>
    </div>`;
}

function ndTableBody() {
  const st = ND.links;
  if (!st.loaded) return '<p class="hint">Loading links…</p>';
  if (st.error) {
    return `<div class="nd-error"><p>${esc(st.error)}</p>
        <button class="btn ghost xs" data-nd-links-refresh>Try again</button></div>`;
  }
  if (!st.rows.length) {
    return `<p class="hint">${st.q.trim()
      ? `No links for "${esc(st.q.trim())}".`
      : 'No links yet — pick two ports in "Layout".'}</p>`;
  }
  const rows = st.rows.map((link) => `<tr>
      <td><div class="nd-cell"><span>${esc(link.device_a)}</span><span class="nd-port-ref">${esc(link.port_a_name)}</span></div></td>
      <td class="nd-arrow">→</td>
      <td><div class="nd-cell"><span>${esc(link.device_b)}</span><span class="nd-port-ref">${esc(link.port_b_name)}</span></div></td>
      <td><div class="nd-cell"><span class="mono">${esc(link.cable) || '—'}</span><span class="nd-port-ref">${link.length_m ? `${link.length_m} m` : ''}</span></div></td>
      <td class="mono nd-racks">${esc(link.rack_a)} → ${esc(link.rack_b)}</td>
      <td class="nd-row-actions">
        <button class="btn ghost xs" data-nd-link-edit="${link.id}">Edit</button>
        <button class="btn ghost xs danger" data-nd-link-del="${link.id}">Unlink</button>
      </td>
    </tr>`).join('');
  return `<table class="nd-table">
      <thead><tr><th>A</th><th></th><th>B</th><th>Cable</th><th>Racks</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    ${ndPagerHtml()}`;
}

function ndRenderTable() {
  const wrap = document.querySelector('#nd-table-wrap');
  if (wrap) wrap.innerHTML = ndTableBody();
  const count = document.querySelector('#nd-links-count');
  if (count) count.innerHTML = ndCountHtml();
  const search = document.querySelector('#nd-links-q');
  if (search && search.value !== ND.links.q) search.value = ND.links.q;
}

async function ndLoadLinks(opts = {}) {
  const st = ND.links;
  if (st.loading) return;
  st.loading = true;
  st.error = '';
  const queryUsed = st.q;
  let fetchedOffset = st.offset;
  try {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      fetchedOffset = st.offset;
      const page = await api(`/inventory/links/page?q=${encodeURIComponent(queryUsed.trim())}&limit=${st.limit}&offset=${fetchedOffset}`);
      st.rows = page.links || [];
      st.total = Number(page.total || 0);
      st.limit = Number(page.limit || st.limit) || 50;
      if (st.offset > 0 && !st.rows.length && st.total > 0) {
        st.offset = Math.floor((st.total - 1) / st.limit) * st.limit;
        continue;
      }
      break;
    }
  } catch (err) {
    st.error = ndErrorText(err, 'The link list failed to load.');
    st.rows = [];
    st.total = 0;
  }
  st.loaded = true;
  st.loading = false;
  // Η αναζήτηση ή η σελίδα άλλαξε όσο έτρεχε το αίτημα: φέρνουμε τα σωστά.
  if (st.q !== queryUsed || st.offset !== fetchedOffset) {
    ndLoadLinks(opts);
    return;
  }
  if (!opts.silent) ndRenderTable();
}

function ndPage(dir) {
  const st = ND.links;
  const pages = Math.max(1, Math.ceil(st.total / st.limit));
  const page = Math.min(pages, Math.floor(st.offset / st.limit) + 1);
  let next = page;
  if (dir === 'first') next = 1;
  else if (dir === 'prev') next = Math.max(1, page - 1);
  else if (dir === 'next') next = Math.min(pages, page + 1);
  else if (dir === 'last') next = pages;
  if (next === page) return;
  st.offset = (next - 1) * st.limit;
  ndLoadLinks();
}

function ndSearchLinks(value) {
  const st = ND.links;
  st.q = String(value || '');
  st.offset = 0;
  st.loaded = false;
  ndLoadLinks();
}

/* ----------------------------------------------------------- link editing */
function ndLinkEdit(linkId) {
  const link = ND.links.rows.find((l) => l.id === linkId);
  if (!link) {
    toast('That link is not on the current page — refresh the table', 'err');
    return;
  }
  ND.edit = { id: linkId, side: 'a', rows: [], q: '', seq: 0, loading: false, error: '' };
  openModal(`<h3>Edit link</h3>
    <p class="lead">#${link.id} · ${esc(link.device_a)} ↔ ${esc(link.device_b)}</p>
    <form id="nd-link-form" autocomplete="off">
      <div class="row">
        <label class="field"><span>Cable</span><input id="nd-edit-cable" maxlength="80" value="${esc(link.cable || '')}" placeholder="CAT6-12" /></label>
        <label class="field"><span>Length (m)</span><input id="nd-edit-len" type="number" min="0" max="10000" step="0.5" value="${link.length_m ? Number(link.length_m) : ''}" /></label>
      </div>
      <p class="nd-side-head">Endpoints</p>
      <div class="nd-side-row on" id="nd-side-a">
        <div><b>${esc(link.device_a)} · ${esc(link.port_a_name)}</b><span>${esc(link.rack_a)}</span></div>
        <button type="button" class="btn ghost xs" data-nd-edit-side="a">Change</button>
      </div>
      <div class="nd-side-row" id="nd-side-b">
        <div><b>${esc(link.device_b)} · ${esc(link.port_b_name)}</b><span>${esc(link.rack_b)}</span></div>
        <button type="button" class="btn ghost xs" data-nd-edit-side="b">Change</button>
      </div>
      <input type="hidden" id="nd-edit-a" value="${Number(link.port_a)}" />
      <input type="hidden" id="nd-edit-b" value="${Number(link.port_b)}" />
      <div class="nd-edit-search">
        <input id="nd-edit-q" type="search" placeholder="Search for a port for side A…" autocomplete="off" aria-label="Search for a port to change the endpoint" />
      </div>
      <div class="nd-edit-results" id="nd-edit-results"></div>
      <p class="error-text" id="nd-edit-error"></p>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-close>Cancel</button>
        <button type="submit" class="btn primary">Save</button>
      </div>
    </form>`);
  const form = document.querySelector('#nd-link-form');
  if (form) form.onsubmit = ndSubmitLinkEdit;
}

function ndEditSide(side) {
  if (!ND.edit) return;
  ND.edit.side = side === 'b' ? 'b' : 'a';
  const rowA = document.querySelector('#nd-side-a');
  const rowB = document.querySelector('#nd-side-b');
  if (rowA) rowA.classList.toggle('on', ND.edit.side === 'a');
  if (rowB) rowB.classList.toggle('on', ND.edit.side === 'b');
  const input = document.querySelector('#nd-edit-q');
  if (input) {
    input.placeholder = ND.edit.side === 'a' ? 'Search for a port for side A…' : 'Search for a port for side B…';
    input.focus();
  }
  const box = document.querySelector('#nd-edit-results');
  if (box) box.innerHTML = '';
  ND.edit.rows = [];
  ND.edit.q = '';
  if (input) input.value = '';
}

function ndEditResultsHtml(st) {
  if (st.error) return `<p class="hint">${esc(st.error)}</p>`;
  if (!st.q.trim()) return '';
  if (st.loading) return '<p class="hint">Searching…</p>';
  if (!st.rows.length) return '<p class="hint">No matching port.</p>';
  return st.rows.map((p) => {
    const busy = Number(p.linked) > 0;
    return `<button type="button" class="nd-pick" data-nd-edit-pick="${p.id}"
        data-nd-qpick-label="${esc(`${p.device} · ${p.name}`)}"
        data-nd-qpick-rack="${esc(`${p.site} · ${p.rack}`)}"${busy ? ' disabled' : ''}>
        <b>${esc(p.name)}</b><span>${esc(p.device)}</span>
        <em>${esc(p.site)} · ${esc(p.rack)}</em>${busy ? '<i>in use</i>' : ''}
      </button>`;
  }).join('');
}

async function ndRunEditSearch(query) {
  const e = ND.edit;
  if (!e) return;
  e.q = String(query || '');
  e.seq += 1;
  const seq = e.seq;
  e.error = '';
  const box = document.querySelector('#nd-edit-results');
  if (!e.q.trim()) {
    e.rows = [];
    e.loading = false;
    if (box) box.innerHTML = '';
    return;
  }
  e.loading = true;
  if (box) box.innerHTML = '<p class="hint">Searching…</p>';
  try {
    const page = await api(`/inventory/ports?q=${encodeURIComponent(e.q.trim())}&limit=12`);
    if (seq !== e.seq) return;
    e.rows = page.ports || [];
  } catch (err) {
    if (seq !== e.seq) return;
    e.rows = [];
    e.error = ndErrorText(err, 'Search failed.');
  }
  if (seq !== e.seq) return;
  e.loading = false;
  if (box) box.innerHTML = ndEditResultsHtml(e);
}

function ndEditPick(portId, label, rack) {
  const e = ND.edit;
  if (!e) return;
  const side = e.side === 'b' ? 'b' : 'a';
  const hidden = document.querySelector(side === 'a' ? '#nd-edit-a' : '#nd-edit-b');
  if (hidden) hidden.value = String(portId);
  const row = document.querySelector(side === 'a' ? '#nd-side-a' : '#nd-side-b');
  if (row) {
    const name = row.querySelector('b');
    const place = row.querySelector('span');
    if (name) name.textContent = label;
    if (place) place.textContent = rack;
  }
  e.rows = [];
  e.q = '';
  e.seq += 1;
  const input = document.querySelector('#nd-edit-q');
  if (input) input.value = '';
  const box = document.querySelector('#nd-edit-results');
  if (box) box.innerHTML = '';
}

async function ndSubmitLinkEdit(event) {
  event.preventDefault();
  const e = ND.edit;
  if (!e) return;
  const cableEl = document.querySelector('#nd-edit-cable');
  const lenEl = document.querySelector('#nd-edit-len');
  const aEl = document.querySelector('#nd-edit-a');
  const bEl = document.querySelector('#nd-edit-b');
  const errBox = document.querySelector('#nd-edit-error');
  const cable = (cableEl && cableEl.value ? cableEl.value : '').trim();
  const rawLength = lenEl && lenEl.value !== '' ? Number(lenEl.value) : 0;
  const portA = Number(aEl && aEl.value);
  const portB = Number(bEl && bEl.value);
  const fail = (message) => { if (errBox) errBox.textContent = message; };
  if (!portA || !portB) { fail('Both endpoints need a port.'); return; }
  if (portA === portB) { fail('The two endpoints cannot be the same port.'); return; }
  if (cable.length > 80) { fail('Cable must be 80 characters or fewer.'); return; }
  if (!Number.isFinite(rawLength) || rawLength < 0 || rawLength > 10000) {
    fail('Length must be between 0 and 10000 meters.');
    return;
  }
  try {
    await api(`/inventory/links/${e.id}`, {
      method: 'PATCH',
      json: { cable, length_m: rawLength, port_a: portA, port_b: portB },
    });
    ND.edit = null;
    closeModal();
    await loadInventory();
    renderPage();
    toast('Link updated');
  } catch (err) {
    fail(ndErrorText(err, 'Update failed.'));
  }
}

function ndUnlink(linkId) {
  const link = ND.links.rows.find((l) => l.id === linkId);
  const name = link
    ? `${link.device_a} · ${link.port_a_name} ↔ ${link.device_b} · ${link.port_b_name}`
    : `link #${linkId}`;
  confirmDialog('Unlink', `The link ${name} will be deleted. Its ports will stay free.`, async () => {
    try {
      await api(`/inventory/links/${linkId}`, { method: 'DELETE' });
      await loadInventory();
      renderPage();
      toast('Link deleted');
    } catch (err) {
      toast(ndErrorText(err, 'Unlink failed'), 'err');
    }
  }, 'Unlink');
}

/* ---------------------------------------------------------- device editor */
async function ndOpenDeviceModal(deviceId) {
  const device = ndDevice(deviceId);
  if (!device) {
    toast('The device is not in the current rack', 'err');
    return;
  }
  let hosts = Array.isArray(S.hosts) ? S.hosts : [];
  try {
    const fresh = await api('/hosts');
    if (Array.isArray(fresh)) hosts = fresh;
  } catch (_) { /* δουλεύουμε με ό,τι έχει ήδη φορτώσει το app.js */ }

  const hostOptions = hosts.map((h) => {
    const target = h.hostname ? `${h.label} · ${h.hostname}` : h.label;
    return `<option value="${h.id}"${Number(device.host_id) === Number(h.id) ? ' selected' : ''}>${esc(target)}</option>`;
  }).join('');
  const kindOptions = ND_DEVICE_KINDS.map((kind) =>
    `<option value="${kind}"${device.kind === kind ? ' selected' : ''}>${esc(ND_KIND_LABEL[kind])}</option>`).join('');
  const rackName = (ND.layout && ND.layout.rack && ND.layout.rack.name) || '';

  openModal(`<h3>Edit device</h3>
    <p class="lead">${esc(rackName)} · U${device.position}${device.units > 1 ? `–U${device.position + device.units - 1}` : ''} · ${device.ports.length} ports</p>
    <form id="nd-device-form" data-device-id="${device.id}" autocomplete="off">
      <div class="row">
        <label class="field"><span>Name</span><input name="name" required maxlength="80" value="${esc(device.name)}" /></label>
        <label class="field"><span>Type</span><select name="kind">${kindOptions}</select></label>
      </div>
      <div class="row three">
        <label class="field"><span>U position</span><input name="position" type="number" min="1" max="60" value="${device.position}" /></label>
        <label class="field"><span>U height</span><input name="units" type="number" min="1" max="48" value="${device.units}" /></label>
        <label class="field"><span>Rack</span><input value="${esc(rackName)}" disabled /></label>
      </div>
      <div class="row">
        <label class="field"><span>Vendor</span><input name="vendor" maxlength="60" value="${esc(device.vendor || '')}" placeholder="Cisco" /></label>
        <label class="field"><span>Model</span><input name="model" maxlength="60" value="${esc(device.model || '')}" placeholder="C9300-24T" /></label>
      </div>
      <div class="row">
        <label class="field"><span>Mgmt IP</span><input name="mgmt_ip" maxlength="64" value="${esc(device.mgmt_ip || '')}" placeholder="10.0.0.10" /></label>
        <label class="field"><span>Host</span>
          <select name="host_id" id="nd-device-host" data-had-host="${device.host_id ? '1' : ''}">
            <option value="">— no host —</option>${hostOptions}
          </select>
          <span class="nd-note" id="nd-host-note"></span>
        </label>
      </div>
      <label class="field"><span>Note</span><textarea name="note" rows="2" maxlength="400" placeholder="e.g. uplink to the provider">${esc(device.note || '')}</textarea></label>
      <p class="error-text" id="nd-device-error"></p>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-close>Cancel</button>
        <button type="submit" class="btn primary">Save</button>
      </div>
      <div class="danger-zone">
        <span class="hint">Deleting removes the device, its ports and its links.</span>
        <button type="button" class="btn danger xs" data-nd-dev-del="${device.id}">Delete</button>
      </div>
    </form>`);

  const form = document.querySelector('#nd-device-form');
  if (form) form.onsubmit = ndSubmitDevice;
  ndHostNote();
}

function ndHostNote() {
  const select = document.querySelector('#nd-device-host');
  const note = document.querySelector('#nd-host-note');
  if (!select || !note) return;
  const hadHost = select.dataset.hadHost === '1';
  note.textContent = hadHost && select.value === ''
    ? 'The API does not allow removing the host: keep the current one or pick another.'
    : '';
}

async function ndSubmitDevice(event) {
  event.preventDefault();
  const form = event.target;
  const errBox = document.querySelector('#nd-device-error');
  const fail = (message) => { if (errBox) errBox.textContent = message; };
  const data = new FormData(form);
  const name = String(data.get('name') || '').trim();
  const kind = String(data.get('kind') || 'switch');
  const position = Number(data.get('position'));
  const units = Number(data.get('units'));
  const hostRaw = String(data.get('host_id') || '');
  const deviceId = Number(form.dataset.deviceId || 0);
  const rackUnits = (ND.layout && ND.layout.rack && Number(ND.layout.rack.units)) || 0;

  if (!deviceId) { fail('Device not found — reopen the rack.'); return; }
  if (!name) { fail('Name is required.'); return; }
  if (!Number.isInteger(position) || position < 1 || position > 60) { fail('U position must be 1–60.'); return; }
  if (!Number.isInteger(units) || units < 1 || units > 48) { fail('U height must be 1–48.'); return; }
  if (rackUnits && position + units - 1 > rackUnits) {
    fail(`The device extends past the rack (${rackUnits}U).`);
    return;
  }

  const payload = {
    name,
    kind: ND_DEVICE_KINDS.indexOf(kind) >= 0 ? kind : 'switch',
    position,
    units,
    note: String(data.get('note') || '').trim(),
    vendor: String(data.get('vendor') || '').trim(),
    model: String(data.get('model') || '').trim(),
    mgmt_ip: String(data.get('mgmt_ip') || '').trim(),
    host_id: hostRaw ? Number(hostRaw) : null,
  };
  if (payload.note.length > 400) { fail('Note must be 400 characters or fewer.'); return; }

  try {
    const saved = await api(`/inventory/devices/${deviceId}`, { method: 'PATCH', json: payload });
    const hostKept = !payload.host_id && saved && saved.host_id;
    closeModal();
    ND.deviceId = deviceId;
    await loadInventory();
    renderPage();
    if (hostKept) {
      toast('Saved — the API does not allow removing the host, so it was kept', 'err');
    } else {
      toast('Device updated');
    }
  } catch (err) {
    fail(ndErrorText(err, 'Save failed — check the fields.'));
  }
}

async function ndDeleteDevice(deviceId) {
  const device = ndDevice(deviceId);
  const label = device ? device.name : `#${deviceId}`;
  let deps;
  try {
    deps = await api(`/inventory/devices/${deviceId}/dependents`);
  } catch (err) {
    toast(ndErrorText(err, 'Failed to load the device dependencies'), 'err');
    return;
  }
  const ports = Number(deps.ports || 0);
  const links = Number(deps.links || 0);
  const text = links
    ? `The device "${label}" has ${ports} ports and ${links} ${links === 1 ? 'link' : 'links'}. It will be deleted along with all its ports and links.`
    : `The device "${label}" has ${ports} ports and no links. It will be deleted along with its ports.`;
  confirmDialog('Delete device', text, async () => {
    try {
      await api(`/inventory/devices/${deviceId}`, { method: 'DELETE' });
      ND.deviceId = null;
      await loadInventory();
      renderPage();
      toast('Device deleted');
    } catch (err) {
      toast(ndErrorText(err, 'Delete failed'), 'err');
    }
  }, 'Delete');
}

/* ------------------------------------------------------------ ports / bulk */
function ndPortFormHtml(device) {
  const form = ND.portForm;
  const templates = ndTemplates();
  const chips = templates.length
    ? `<div class="nd-tpl-list">${templates.map((t, index) => `<span class="nd-tpl">
          <button type="button" class="nd-tpl-apply" data-nd-tpl="${index}" title="Fill in the form">${esc(t.prefix)} · ${Number(t.count)} · ${esc(ND_PORT_KIND_LABEL[t.kind] || t.kind)}</button>
          <button type="button" class="nd-tpl-del" data-nd-tpl-del="${index}" title="Remove preset" aria-label="Remove preset">×</button>
        </span>`).join('')}</div>`
    : '<span class="nd-pf-label">No saved presets yet — the last 3 stay in the browser.</span>';
  return `<form class="nd-portform" id="nd-port-form" data-device="${device.id}" autocomplete="off">
      <label class="nd-pf"><span>Prefix</span><input id="nd-tpl-prefix" maxlength="12" value="${esc(form.prefix)}" /></label>
      <label class="nd-pf"><span>Count</span><input id="nd-tpl-count" type="number" min="1" max="96" value="${Number(form.count) || 24}" /></label>
      <label class="nd-pf"><span>Type</span><select id="nd-tpl-kind">${ND_PORT_KINDS.map((kind) =>
        `<option value="${kind}"${form.kind === kind ? ' selected' : ''}>${esc(ND_PORT_KIND_LABEL[kind])}</option>`).join('')}</select></label>
      <button class="btn primary xs" type="submit">Add</button>
      <button class="btn ghost xs" type="button" data-nd-portform="${device.id}">Cancel</button>
      <div class="nd-tpl-wrap"><span class="nd-pf-label">Presets (last 3):</span>${chips}</div>
    </form>`;
}

async function ndSubmitPortForm(event) {
  event.preventDefault();
  const form = event.target;
  const deviceId = Number(form.dataset.device || 0);
  const prefix = String((document.querySelector('#nd-tpl-prefix') || {}).value || '').trim();
  const count = Number((document.querySelector('#nd-tpl-count') || {}).value || 0);
  const kind = String((document.querySelector('#nd-tpl-kind') || {}).value || 'rj45');
  ND.portForm = { open: true, prefix, count: count || 24, kind: ND_PORT_KINDS.indexOf(kind) >= 0 ? kind : 'rj45' };
  if (!deviceId) { toast('Device not found', 'err'); return; }
  if (!prefix) { toast('Enter a prefix (e.g. eth)', 'err'); return; }
  if (prefix.length > 12) { toast('Prefix must be 12 characters or fewer', 'err'); return; }
  if (!Number.isInteger(count) || count < 1 || count > 96) { toast('Count must be 1–96', 'err'); return; }
  try {
    const created = await api('/inventory/ports/bulk', {
      json: { device_id: deviceId, prefix, count, kind: ND.portForm.kind },
    });
    const made = (created && created.created) || [];
    ndRememberTemplate({ prefix, count, kind: ND.portForm.kind });
    ND.portForm.open = false;
    ND.deviceId = deviceId;
    await loadInventory();
    renderPage();
    toast(`${made.length} ports added`);
  } catch (err) {
    toast(ndErrorText(err, 'Bulk add failed'), 'err');
  }
}

function ndApplyTemplate(index) {
  const template = ndTemplates()[index];
  if (!template) return;
  const prefix = document.querySelector('#nd-tpl-prefix');
  const count = document.querySelector('#nd-tpl-count');
  const kind = document.querySelector('#nd-tpl-kind');
  if (prefix) prefix.value = template.prefix;
  if (count) count.value = String(template.count);
  if (kind) kind.value = template.kind;
  if (count) count.focus();
}

function ndForgetTemplate(index) {
  const list = ndTemplates();
  if (!list[index]) return;
  list.splice(index, 1);
  ndWriteTemplates(list);
  renderPage();
  toast('Preset removed');
}

/* --------------------------------------------------------------- views */
function ndLayoutView() {
  const layout = ND.layout;
  if (!layout) return '<p class="hint">Pick a rack on the left.</p>';
  const rack = layout.rack || { units: 42, name: '' };
  const devices = layout.devices || [];
  const byTopUnit = new Map();
  devices.forEach((device) => byTopUnit.set(device.position + device.units - 1, device));

  const cells = [];
  let row = 1;
  let unit = rack.units;
  // κάθε U είναι δική του γραμμή: το rack διαβάζεται κάθετα, όχι σαν λωρίδα
  while (unit >= 1) {
    const device = byTopUnit.get(unit);
    if (device) {
      const linked = device.ports.filter((p) => p.link_id).length;
      for (let extra = 0; extra < device.units; extra += 1) {
        cells.push(`<div class="nd-u" style="grid-row:${row + extra}">U${unit - extra}</div>`);
      }
      cells.push(`<div class="nd-device ${esc(device.kind)}${device.units === 1 ? ' one' : ''}" style="grid-row:${row} / span ${device.units}" data-nd-select="${device.id}" title="U${device.position}–U${unit} · ${esc(device.name)} · ${device.ports.length} ports">
          <b>${esc(device.name)}</b>
          <span>${esc(ndKindIcon(device.kind))} · ${device.ports.length} ports${linked ? ` · ${linked} linked` : ''}</span>
        </div>`);
      row += device.units;
      unit -= device.units;
      continue;
    }
    const free = !byTopUnit.get(unit);
    if (free) {
      cells.push(`<div class="nd-u${device ? '' : ' muted'}" style="grid-row:${row}">U${unit}</div>`);
      cells.push(`<div class="nd-empty" style="grid-row:${row}"></div>`);
    }
    row += 1;
    unit -= 1;
  }

  const selectedDevice = devices.find((d) => d.id === ND.deviceId) || devices[0] || null;
  const portList = selectedDevice
    ? `<div class="nd-ports">
        <div class="nd-ports-head">
          <b>${esc(selectedDevice.name)}</b>
          <span class="hint">${selectedDevice.ports.length} ports · U${selectedDevice.position}</span>
          <button class="btn ghost xs" data-nd-dev-edit="${selectedDevice.id}">Edit</button>
          <button class="btn ghost xs" data-nd-portform="${selectedDevice.id}">${ND.portForm.open ? 'Close' : 'Ports'}</button>
          <button class="btn ghost xs danger" data-nd-dev-del="${selectedDevice.id}">Delete</button>
        </div>
        ${ND.portForm.open ? ndPortFormHtml(selectedDevice) : ''}
        <div class="nd-port-grid">${selectedDevice.ports.map((p) => ndPortButton(selectedDevice, p)).join('')
          || '<p class="hint">No ports — press "Ports" to add them in bulk.</p>'}</div>
        <p class="nd-port-locked">Ports already in use are locked; to change or unlink one, open "Links".</p>
      </div>`
    : '<p class="hint">Add a device to see its ports.</p>';

  return `<div class="nd-layout">
      ${ndQuickBar()}
      <div class="nd-rack-frame" data-rack-frame="${rack.id}">
        <div class="nd-rack-view" style="--units:${rack.units}">${cells.join('')}</div>
        <p class="nd-rack-legend">U1 at the bottom · ${rack.units}U · click a device to see its ports</p>
        ${cells.length ? '' : ''}
      </div>
      ${portList}
    </div>`;
}

/* Το rack είναι 42 γραμμές· άνοιξέ το εκεί που είναι οι συσκευές, όχι στο κενό. */
function ndScrollRackToDevices() {
  const view = document.querySelector('.nd-rack-view');
  if (!view) return;
  const target = view.querySelector('.nd-device.sel') || view.querySelector('.nd-device');
  if (!target) { view.scrollTop = view.scrollHeight; return; }
  const offset = target.offsetTop - view.clientHeight / 2 + target.offsetHeight / 2;
  view.scrollTop = Math.max(0, Math.min(view.scrollHeight - view.clientHeight, offset));
}

function ndTableView() {
  return `<div class="nd-links">
      <div class="nd-links-head">
        <input id="nd-links-q" class="nd-links-input" type="search" value="${esc(ND.links.q)}" autocomplete="off"
          placeholder="Search by device, port or cable…" aria-label="Search links" />
        <div class="nd-links-tools">
          <button class="btn ghost xs" data-nd-links-clear>Clear</button>
          <button class="btn ghost xs" data-nd-links-refresh>Refresh</button>
          <span class="nd-count" id="nd-links-count">${ndCountHtml()}</span>
        </div>
      </div>
      <div id="nd-table-wrap">${ndTableBody()}</div>
    </div>`;
}

function ndAuditView() {
  const audit = ND.audit;
  if (!audit) return '<p class="hint">Press "Audit" to scan the data.</p>';
  const labels = {
    overlap: 'U overlap',
    outside_rack: 'Outside the rack',
    duplicate_device: 'Duplicate name',
    unlinked_ports: 'Unlinked ports',
    duplicate_port: 'Port in two links',
  };
  const items = (audit.issues || []).map((issue) => `<li class="nd-issue ${esc(issue.kind)}">
      <b>${esc(labels[issue.kind] || issue.kind)}</b>
      <span>${esc(issue.rack)}${issue.device ? ' · ' + esc(issue.device) : ''}</span>
      <em>${esc(issue.detail)}</em></li>`).join('');
  const summary = audit.summary || { racks: 0, ports: 0, links: 0, issues: 0 };
  return `<div class="nd-summary">
      <span><b>${summary.racks}</b> racks</span><span><b>${summary.ports}</b> ports</span>
      <span><b>${summary.links}</b> links</span>
      <span class="${summary.issues ? 'warn' : 'ok'}"><b>${summary.issues}</b> findings</span>
    </div>
    <ul class="nd-issues">${items || '<li class="nd-issue ok"><b>Clean</b><em>No problems found.</em></li>'}</ul>`;
}

/* ----------------------------------------------------------------- print */
function ndPrintRackHtml(layout) {
  const devices = (layout.devices || []).slice()
    .sort((a, b) => (b.position + b.units) - (a.position + a.units));
  const rows = devices.map((d) => {
    const top = d.position + d.units - 1;
    const linked = d.ports.filter((p) => p.link_id).length;
    return `<tr>
        <td class="mono">${d.units > 1 ? `U${d.position}–U${top}` : `U${d.position}`}</td>
        <td><b>${esc(d.name)}</b></td>
        <td>${esc(ndKindIcon(d.kind))}</td>
        <td class="mono">${d.ports.length}</td>
        <td class="mono">${linked}</td>
        <td>${esc([d.vendor, d.model].filter(Boolean).join(' '))}</td>
        <td class="mono">${esc(d.mgmt_ip || '')}</td>
        <td>${esc(d.host_label || '')}</td>
      </tr>`;
  }).join('');
  return `<section class="nd-print-block">
      <h2>Rack ${esc(layout.rack.name)} — ${esc(layout.site || '')} · ${Number(layout.rack.units)}U</h2>
      <table class="nd-print-table">
        <thead><tr><th>Position</th><th>Device</th><th>Type</th><th>Ports</th><th>Linked</th><th>Vendor / Model</th><th>Mgmt IP</th><th>Host</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="8">No devices.</td></tr>'}</tbody>
      </table>
    </section>`;
}

function ndPrintSheetHtml(rows, total, note) {
  const layout = ND.layout;
  const when = new Date().toLocaleString('el-GR');
  const place = layout ? `${layout.site || ''} · ${layout.rack.name}` : 'All racks';
  const linkRows = rows.map((link) => `<tr>
      <td>${esc(link.device_a)}</td>
      <td class="mono">${esc(link.port_a_name)}</td>
      <td>${esc(link.device_b)}</td>
      <td class="mono">${esc(link.port_b_name)}</td>
      <td class="mono">${esc(link.cable || '')}</td>
      <td class="mono">${link.length_m ? `${link.length_m} m` : ''}</td>
      <td class="mono">${esc(link.rack_a)} → ${esc(link.rack_b)}</td>
    </tr>`).join('');
  return `<header class="nd-print-head">
      <div><b>MUXIVRA</b><span>Rack &amp; cabling documentation</span></div>
      <div class="nd-print-meta"><span>${esc(place)}</span><span>${esc(when)}</span></div>
    </header>
    ${layout ? ndPrintRackHtml(layout) : '<p class="nd-print-note">No rack selected — only the link table will print.</p>'}
    <section class="nd-print-block">
      <h2>Links (${rows.length} of ${total})</h2>
      <table class="nd-print-table">
        <thead><tr><th>Device A</th><th>Port A</th><th>Device B</th><th>Port B</th><th>Cable</th><th>Length</th><th>Racks</th></tr></thead>
        <tbody>${linkRows || '<tr><td colspan="7">No links.</td></tr>'}</tbody>
      </table>
    </section>
    ${note ? `<p class="nd-print-note">${esc(note)}</p>` : ''}
    ${total > rows.length ? `<p class="nd-print-note">Printing the first ${rows.length} of ${total} links (limit 500). Use CSV for the complete file.</p>` : ''}`;
}

async function ndPrint() {
  const sheet = document.querySelector('#nd-print');
  if (!sheet) {
    toast('Open the rack or the links first', 'err');
    return;
  }
  let rows = (ND.links.rows || []).slice();
  let total = Number(ND.links.total || rows.length);
  let note = '';
  try {
    const all = await api('/inventory/links/page?limit=500');
    rows = all.links || rows;
    total = Number(all.total || rows.length);
  } catch (err) {
    note = 'The full list failed to load — printing the rows of the current page.';
    if (!rows.length) {
      toast(ndErrorText(err, 'No links to print'), 'err');
      return;
    }
  }
  sheet.innerHTML = ndPrintSheetHtml(rows, total, note);
  document.body.classList.add('print-rack');
  const cleanup = () => {
    document.body.classList.remove('print-rack');
    window.removeEventListener('afterprint', cleanup);
  };
  window.addEventListener('afterprint', cleanup);
  try {
    window.print();
  } catch (_) {
    cleanup();
    toast('The browser did not open a print window', 'err');
  }
}

/* -------------------------------------------------------------- audit load */
async function ndAudit() {
  ND.audit = await api('/inventory/audit');
  return ND.audit;
}

/* ------------------------------------------------------------------ render */
function renderRacks() {
  const nd = S.nd || {};
  const tree = (nd && nd.tree) || { sites: [] };
  const sites = Array.isArray(tree.sites) ? tree.sites : [];
  const racks = ndRacks();
  const rack = racks.find((r) => r.id === ND.rackId) || null;
  const tab = ND.tab || 'layout';
  const linkTotal = Number((nd && nd.tree && nd.tree.links) || 0);

  const treeHtml = sites.length ? sites.map((site) => `
      <div class="nd-site">
        <div class="nd-site-head">
          <span>${esc(site.name)}</span>
          <span class="nd-site-actions">
            <button class="btn ghost xs" data-nd-site-edit="${site.id}" title="Rename site">Rename</button>
            <button class="btn ghost xs danger" data-nd-site-del="${site.id}" title="Delete site">Delete</button>
          </span>
        </div>
        ${(site.racks || []).length ? (site.racks || []).map((r) => `
          <button class="nd-rack${r.id === ND.rackId ? ' on' : ''}" data-nd-rack="${r.id}">
            <b>${esc(r.name)}</b><span>${(r.devices || []).length} devices · ${r.units}U</span>
          </button>`).join('') : '<p class="hint">no racks</p>'}
      </div>`).join('') : '<p class="hint">No sites yet.</p>';

  const addDevice = rack && tab === 'layout'
    ? `<div class="nd-form">
        <input id="nd-dev-name" placeholder="Device" autocomplete="off" />
        <select id="nd-dev-kind" aria-label="Device type">
          ${ND_DEVICE_KINDS.map((kind) => `<option value="${kind}">${esc(ND_KIND_LABEL[kind])}</option>`).join('')}
        </select>
        <input id="nd-dev-pos" type="number" min="1" max="60" value="1" title="U position" aria-label="U position" />
        <button class="btn primary xs" id="nd-add-dev">+ Device</button>
      </div>`
    : '';

  if (tab === 'table' && !ND.links.loaded && !ND.links.loading) ndLoadLinks();

  $('#page-racks').innerHTML = `<div class="page-head">
      <div><h2>Racks &amp; Cables</h2><p>Sites, racks, devices, ports and links — with duplicate-port checks.</p></div>
      <div class="actions">
        <button class="btn primary xs" data-nd-newcable title="Link two ports with a summary before saving">New cable</button>
        <button class="btn ghost xs" data-nd-print>Print / PDF</button>
        <button class="btn ghost xs" data-nd-csv>CSV</button>
      </div>
    </div>
    <div class="nd-split">
      <aside class="panel nd-tree">
        <h2>Sites</h2>
        ${treeHtml}
        <div class="nd-form">
          <input id="nd-site-name" placeholder="New site" autocomplete="off" />
          <button class="btn ghost xs" id="nd-add-site" aria-label="Add site">+</button>
        </div>
        <div class="nd-form">
          <select id="nd-rack-site" aria-label="Site">${sites.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('')}</select>
          <input id="nd-rack-name" placeholder="Rack" autocomplete="off" />
          <button class="btn ghost xs" id="nd-add-rack" aria-label="Add rack">+</button>
        </div>
      </aside>

      <div class="panel nd-main">
        <div class="nd-tabs">
          <button class="nd-tab${tab === 'layout' ? ' on' : ''}" data-nd-tab="layout">Layout</button>
          <button class="nd-tab${tab === 'table' ? ' on' : ''}" data-nd-tab="table">Links (${linkTotal})</button>
          <button class="nd-tab${tab === 'audit' ? ' on' : ''}" data-nd-tab="audit">Audit</button>
          <span class="spacer"></span>
          ${rack ? `<span class="hint">${esc(rack.siteName)} · ${esc(rack.name)}</span>` : ''}
        </div>
        ${addDevice}
        ${tab === 'layout' ? ndLayoutView() : tab === 'table' ? ndTableView() : ndAuditView()}
      </div>
    </div>
    <div class="nd-print" id="nd-print" aria-hidden="true"></div>`;
  if ((tab === 'layout') && ND.layout && (ND.layout.devices || []).length) {
    window.setTimeout(ndScrollRackToDevices, 0);
  }
}

/* -------------------------------------------------------- delegated events */
function ndOwnedClick(event) {
  const target = event.target;
  if (!target || typeof target.closest !== 'function') return false;
  const take = (el) => {
    event.preventDefault();
    event.stopPropagation();
    return el;
  };

  let el = target.closest('[data-nd-qpick]');
  if (el) { take(el); ndQuickPick(Number(el.dataset.ndQpick), el.dataset.ndQpickLabel || '', el.dataset.ndQpickRack || ''); return true; }

  el = target.closest('[data-nd-edit-pick]');
  if (el) { take(el); ndEditPick(Number(el.dataset.ndEditPick), el.dataset.ndQpickLabel || '', el.dataset.ndQpickRack || ''); return true; }

  el = target.closest('[data-nd-edit-side]');
  if (el) { take(el); ndEditSide(el.dataset.ndEditSide); return true; }

  el = target.closest('[data-nd-link-edit]');
  if (el) { take(el); ndLinkEdit(Number(el.dataset.ndLinkEdit)); return true; }

  el = target.closest('[data-nd-link-del]');
  if (el) { take(el); ndUnlink(Number(el.dataset.ndLinkDel)); return true; }

  el = target.closest('[data-nd-page]');
  if (el) { take(el); if (!el.disabled) ndPage(el.dataset.ndPage); return true; }

  el = target.closest('[data-nd-links-clear]');
  if (el) {
    take(el);
    const input = document.querySelector('#nd-links-q');
    if (input) input.value = '';
    ndSearchLinks('');
    return true;
  }

  el = target.closest('[data-nd-links-refresh]');
  if (el) { take(el); ND.links.loaded = false; ndLoadLinks(); return true; }

  el = target.closest('[data-nd-dev-edit]');
  if (el) { take(el); ndOpenDeviceModal(Number(el.dataset.ndDevEdit)); return true; }

  el = target.closest('[data-nd-dev-del]');
  if (el) { take(el); ndDeleteDevice(Number(el.dataset.ndDevDel)); return true; }

  el = target.closest('[data-nd-portform]');
  if (el) {
    take(el);
    ND.portForm.open = !ND.portForm.open;
    renderPage();
    return true;
  }

  el = target.closest('[data-nd-tpl]');
  if (el) { take(el); ndApplyTemplate(Number(el.dataset.ndTpl)); return true; }

  el = target.closest('[data-nd-tpl-del]');
  if (el) { take(el); ndForgetTemplate(Number(el.dataset.ndTplDel)); return true; }

  el = target.closest('[data-nd-print]');
  if (el) { take(el); ndPrint(); return true; }

  return false;
}

document.addEventListener('click', (event) => { ndOwnedClick(event); }, true);

document.addEventListener('input', (event) => {
  const target = event.target;
  if (!target || !target.id) return;
  if (target.id === 'nd-links-q') {
    ndDebounce('links', () => ndSearchLinks(target.value), 250);
    return;
  }
  if (target.id === 'nd-quick-q') {
    ndDebounce('quick', () => ndRunPortSearch(target.value), 220);
    return;
  }
  if (target.id === 'nd-edit-q') {
    ndDebounce('edit', () => ndRunEditSearch(target.value), 220);
  }
});

document.addEventListener('keydown', (event) => {
  const target = event.target;
  if (!target || !target.id) return;
  if (event.key === 'Enter' && target.id === 'nd-links-q') {
    event.preventDefault();
    if (ND_TIMERS.links) { clearTimeout(ND_TIMERS.links); ND_TIMERS.links = null; }
    ND.links.q = target.value;
    ND.links.offset = 0;
    ND.links.loaded = false;
    ndLoadLinks();
    return;
  }
  if (event.key === 'Enter' && target.id === 'nd-quick-q') {
    event.preventDefault();
    const first = document.querySelector('#nd-quick-results .nd-pick:not([disabled])');
    if (first) first.click();
    return;
  }
  if (event.key === 'Enter' && target.id === 'nd-edit-q') {
    event.preventDefault();
    const first = document.querySelector('#nd-edit-results .nd-pick:not([disabled])');
    if (first) first.click();
  }
});

document.addEventListener('change', (event) => {
  if (event.target && event.target.id === 'nd-device-host') ndHostNote();
});

document.addEventListener('submit', (event) => {
  if (event.target && event.target.id === 'nd-port-form') {
    event.preventDefault();
    event.stopPropagation();
    ndSubmitPortForm(event);
  }
}, true);

// «New cable» από την κεφαλίδα (delegated: το header ξαναγράφεται σε κάθε render)
document.addEventListener('click', (event) => {
  const button = event.target.closest ? event.target.closest('[data-nd-newcable]') : null;
  if (!button) return;
  event.preventDefault();
  ndcOpen();
});

// CSV download με το customer context του api() (ένα σκέτο href δεν στέλνει header)
document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-nd-csv]');
  if (!button) return;
  event.preventDefault();
  try {
    const response = await fetch('/api/inventory/links.csv', {
      headers: { 'x-requested-with': 'sshdeck', 'x-customer-id': String(window.S?.customerId || '') },
      credentials: 'same-origin',
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const blob = await response.blob();
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'links.csv';
    link.click();
    URL.revokeObjectURL(link.href);
  } catch (error) {
    alert('CSV export failed: ' + error.message);
  }
});


// --- χώροι: ορατή μετονομασία και διαγραφή με προεπισκόπηση εξαρτήσεων ---
document.addEventListener('click', async (event) => {
  const edit = event.target.closest('[data-nd-site-edit]');
  const del = event.target.closest('[data-nd-site-del]');
  if (!edit && !del) return;
  event.preventDefault();
  const siteId = Number((edit || del).dataset.ndSiteEdit || (edit || del).dataset.ndSiteDel);
  const site = (nd.tree?.sites || []).find((s) => s.id === siteId);
  if (!site) return;

  if (edit) {
    const name = prompt('New site name:', site.name);
    if (!name || name.trim() === site.name) return;
    try {
      await api(`/inventory/sites/${siteId}`, { method: 'PATCH', json: { name: name.trim() } });
      await loadInventory();
      renderInventory();
    } catch (error) {
      alert('Rename failed: ' + error.message);
    }
    return;
  }

  let deps = null;
  try {
    deps = await api(`/inventory/site/${siteId}/dependents`);
  } catch (error) {
    alert('Could not check dependencies: ' + error.message);
    return;
  }
  const answer = prompt(
    `Delete "${site.name}"?\n\nThis will remove:\n` +
    `• ${deps.racks} racks\n• ${deps.devices} devices\n• ${deps.ports} ports\n• ${deps.links} cables\n\n` +
    'Type DELETE to confirm. No commands are sent to real equipment.',
    ''
  );
  if ((answer || '').trim().toUpperCase() !== 'DELETE') return;
  try {
    await api(`/inventory/sites/${siteId}`, { method: 'DELETE' });
    if (ND.rackId && site.racks?.some((r) => r.id === ND.rackId)) ND.rackId = null;
    await loadInventory();
    renderInventory();
  } catch (error) {
    alert('Delete failed: ' + error.message);
  }
});

/* --------------------------------------------------------- New cable ροή
   Δύο βήματα με καθαρή σύνοψη: Α (συσκευή/θύρα) → Β (συσκευή/θύρα) → αποθήκευση.
   Το Α δεν χάνεται όταν αλλάζει rack/χώρος, και δεν αποθηκεύεται τίποτα με το
   δεύτερο click — μόνο με το κουμπί της σύνοψης.
*/
const NDC = { a: null, b: null, deviceA: '', deviceB: '', query: '' };

function ndcState() {
  return (typeof S !== 'undefined' && S && S.nd) ? S.nd : {};
}

function ndcDevices() {
  const out = [];
  (ndcState().tree?.sites || []).forEach((site) => {
    (site.racks || []).forEach((rack) => {
      (rack.devices || []).forEach((device) => out.push({ ...device, siteName: site.name, rackName: rack.name }));
    });
  });
  return out;
}

const NDC_PORTS = new Map();   // rack_id → devices με ports (από /inventory/rack/{id})

function ndcPorts(deviceId) {
  const device = ndcDevices().find((item) => String(item.id) === String(deviceId));
  if (!device) return [];
  const rack = NDC_PORTS.get(String(device.rackId ?? device.rack_id));
  const fromRack = rack && rack.find((item) => String(item.id) === String(deviceId));
  if (fromRack && fromRack.ports) return fromRack.ports;
  return device.ports || [];
}

async function ndcLoadPorts() {
  const racks = new Map();
  (ndcState().tree?.sites || []).forEach((site) => {
    (site.racks || []).forEach((rack) => racks.set(String(rack.id), rack));
  });
  await Promise.all(Array.from(racks.keys()).map(async (rackId) => {
    if (NDC_PORTS.has(rackId)) return;
    try {
      const layout = await api(`/inventory/rack/${rackId}`);
      NDC_PORTS.set(rackId, layout.devices || []);
    } catch (_) { NDC_PORTS.set(rackId, []); }
  }));
}

function ndcCableName() {
  const count = (ndcState().links || []).length + 1;
  return `C-${String(count).padStart(3, '0')}`;
}

function ndcRender() {
  const body = document.querySelector('#ndc-body');
  if (!body) return;
  const devices = ndcDevices();
  const option = (device) => `<option value="${device.id}"${String(NDC[device === NDC.deviceA ? 'deviceA' : 'deviceB']) === String(device.id) ? ' selected' : ''}>${esc(device.name)} · ${esc(device.rackName)}</option>`;
  const deviceSelect = (id, value, label) => `
    <label class="ndc-field"><span>${label}</span>
      <select id="${id}">
        <option value="">— select a device —</option>
        ${devices.map((device) => `<option value="${device.id}"${String(device.id) === String(value) ? ' selected' : ''}>${esc(device.name)} · ${esc(device.siteName)} / ${esc(device.rackName)}</option>`).join('')}
      </select></label>`;
  const ports = (deviceId, side) => {
    const list = ndcPorts(deviceId);
    if (!deviceId) return '<p class="ndc-hint">Select a device first.</p>';
    if (!list.length) return '<p class="ndc-hint">This device has no ports — add them from "Ports".</p>';
    return `<div class="ndc-ports">${list.map((port) => {
      const chosen = side === 'a' ? NDC.a : NDC.b;
      const active = chosen && String(chosen.portId) === String(port.id);
      const busy = !!port.link_id;
      return `<button type="button" class="ndc-port${active ? ' on' : ''}${busy ? ' busy' : ''}"
        data-ndc-side="${side}" data-ndc-port="${port.id}" data-ndc-label="${esc(port.name)}"
        ${busy ? 'disabled' : ''} title="${busy ? `in use → ${esc(port.peer || '')}` : 'free'}">${esc(port.name)}</button>`;
    }).join('')}</div>`;
  };
  const summary = (NDC.a && NDC.b)
    ? `<div class="ndc-summary"><b>${esc(NDC.a.device)}</b> · ${esc(NDC.a.port)}
         <span>→</span> <b>${esc(NDC.b.device)}</b> · ${esc(NDC.b.port)}
         <em>${esc(NDC.a.rack)} → ${esc(NDC.b.rack)}</em></div>`
    : `<div class="ndc-summary idle">${NDC.a ? `A: ${esc(NDC.a.device)} · ${esc(NDC.a.port)} — pick B` : 'Pick port A and port B to see the summary.'}</div>`;

  body.innerHTML = `
    <div class="ndc-grid">
      <div class="ndc-side">
        <h4>A · where it starts</h4>
        ${deviceSelect('ndc-device-a', NDC.deviceA, 'Device A')}
        ${ports(NDC.deviceA, 'a')}
      </div>
      <div class="ndc-side">
        <h4>B · where it ends</h4>
        ${deviceSelect('ndc-device-b', NDC.deviceB, 'Device B')}
        ${ports(NDC.deviceB, 'b')}
      </div>
    </div>
    ${summary}
    <label class="ndc-field"><span>Cable label</span>
      <input id="ndc-cable" value="${esc(NDC.cable || ndcCableName())}" maxlength="40" /></label>
    <div class="ndc-foot">
      <span class="ndc-hint">Nothing is saved until you press "Save cable".</span>
      <button type="button" class="btn primary" id="ndc-save"${NDC.a && NDC.b ? '' : ' disabled'}>Save cable</button>
    </div>`;

  body.querySelector('#ndc-device-a').onchange = (event) => {
    NDC.deviceA = event.target.value; NDC.a = null; ndcRender();
  };
  body.querySelector('#ndc-device-b').onchange = (event) => {
    NDC.deviceB = event.target.value; NDC.b = null; ndcRender();
  };
  const cable = body.querySelector('#ndc-cable');
  if (cable) cable.oninput = () => { NDC.cable = cable.value; };
  body.querySelectorAll('[data-ndc-port]').forEach((button) => {
    button.onclick = () => {
      const side = button.dataset.ndcSide;
      const deviceId = side === 'a' ? NDC.deviceA : NDC.deviceB;
      const device = ndcDevices().find((item) => String(item.id) === String(deviceId));
      if (!device) return;
      const portId = Number(button.dataset.ndcPort);
      const chosen = { deviceId: device.id, device: device.name, rack: device.rackName, port: button.dataset.ndcLabel, portId };
      if (side === 'a') NDC.a = chosen; else NDC.b = chosen;
      if (NDC.a && NDC.b && NDC.a.portId === NDC.b.portId) {
        toast('Pick two different ports', 'err');
        if (side === 'a') NDC.a = null; else NDC.b = null;
      }
      ndcRender();
    };
  });
  const save = body.querySelector('#ndc-save');
  if (save) save.onclick = ndcSave;
}

async function ndcSave() {
  if (!NDC.a || !NDC.b) return;
  const save = document.querySelector('#ndc-save');
  if (save) { save.disabled = true; save.textContent = 'Saving…'; }
  try {
    await api('/inventory/links', {
      method: 'POST',
      json: {
        port_a: NDC.a.portId,
        port_b: NDC.b.portId,
        cable: (document.querySelector('#ndc-cable')?.value || ndcCableName()).slice(0, 40),
      },
    });
    closeModal();
    NDC.a = null; NDC.b = null;
    await loadInventory();
    renderInventory();
    toast('Cable recorded');
  } catch (error) {
    if (save) { save.disabled = false; save.textContent = 'Save cable'; }
    toast(`Cable was not saved: ${error.message}`, 'err');
  }
}

async function ndcOpen(preset) {
  NDC.deviceA = preset?.deviceId ? String(preset.deviceId) : '';
  NDC.deviceB = '';
  NDC.a = preset?.port ? { ...preset.port } : null;
  NDC.b = null;
  NDC.cable = ndcCableName();
  openModal(`<h3>New cable</h3>
    <p class="lead">Pick port A, then port B — even in another rack or site. A summary is shown before saving.</p>
    <div id="ndc-body"><p class="ndc-hint">Loading ports…</p></div>`);
  await ndcLoadPorts();
  ndcRender();
}
