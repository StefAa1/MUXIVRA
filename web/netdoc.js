/* --------------------------------------------------------------- NETDOC
   Rack documentation: sites → racks → devices (U positions) → ports → links.
   Views: Διάταξη (visual rack), Συνδέσεις (paged table), Έλεγχος (audit).

   Quick Connect: pick one port (in the rack or through the cross-rack search
   over /netdoc/ports) and then its partner; the next free cable number is
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

const ND_TPL_KEY = 'sshdeck.netdoc.portTemplates';
const ND_KIND_LABEL = {
  switch: 'switch', router: 'router', 'patch-panel': 'patch panel',
  server: 'server', pdu: 'pdu', other: 'άλλο',
};
const ND_PORT_KIND_LABEL = { rj45: 'rj45', sfp: 'sfp', fiber: 'fiber', power: 'power', other: 'άλλο' };
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
  if (raw.indexOf('port_already_linked') === 0) return 'Το port είναι ήδη δεσμευμένο σε άλλη σύνδεση.';
  if (raw === 'same_port') return 'Δεν μπορείς να συνδέσεις ένα port με τον εαυτό του.';
  if (raw.indexOf('position_taken:') === 0) return `Η θέση είναι πιασμένη από «${raw.slice(15)}».`;
  if (raw === 'device_outside_rack') return 'Η συσκευή βγαίνει έξω από τα όρια του rack.';
  if (raw === 'bad_bulk_request') return 'Έλεγξε prefix (έως 12 χαρακτήρες) και πλήθος (1–96).';
  if (raw === 'port_exists') return 'Υπάρχει ήδη port με αυτό το όνομα.';
  if (raw === 'not_found') return 'Δεν βρέθηκε η εγγραφή — μπορεί να διαγράφηκε.';
  if (raw === 'unauthorized') return 'Η συνεδρία έληξε — συνδέσου ξανά.';
  if (raw.toLowerCase().indexOf('failed to fetch') >= 0) return 'Δεν υπάρχει σύνδεση με τον server.';
  return raw.length > 160 ? fallback : raw;
}

function ndDebounce(key, fn, wait) {
  if (ND_TIMERS[key]) clearTimeout(ND_TIMERS[key]);
  ND_TIMERS[key] = setTimeout(() => { ND_TIMERS[key] = null; fn(); }, wait);
}

function ndKindIcon(kind) { return ND_KIND_LABEL[kind] || kind || '—'; }

/* ------------------------------------------------------------------- data */
async function loadNetdoc() {
  S.nd = S.nd || {};
  try {
    S.nd.tree = await api('/netdoc/tree');
  } catch (_) {
    S.nd.tree = { sites: [], links: 0 };
  }
  try {
    S.nd.links = await api('/netdoc/links');
  } catch (_) {
    S.nd.links = [];
  }
  ND.links.loaded = false;
  ND.links.error = '';
  if (ND.rackId) {
    try {
      ND.layout = await api(`/netdoc/rack/${ND.rackId}`);
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
    try { ND.audit = await api('/netdoc/audit'); } catch (_) { /* κρατάμε το παλιό */ }
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
    ND.layout = await api(`/netdoc/rack/${rackId}`);
  } catch (err) {
    ND.layout = null;
    toast(ndErrorText(err, 'Το rack δεν φόρτωσε'), 'err');
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
  const title = busy ? `δεσμευμένο → ${port.peer || ''}` : 'ελεύθερο';
  return `<button class="${cls}" data-nd-port="${port.id}" data-nd-port-label="${esc(device.name)} · ${esc(port.name)}" title="${esc(title)}"${busy ? ' disabled' : ''}>${esc(port.name)}</button>`;
}

function ndPortResultsHtml(st) {
  if (st.error) return `<p class="hint">${esc(st.error)}</p>`;
  if (!st.q || !st.q.trim()) return '';
  if (st.loading) return '<p class="hint">Αναζήτηση…</p>';
  if (!st.rows.length) return '<p class="hint">Κανένα port δεν ταιριάζει.</p>';
  return st.rows.map((p) => {
    const busy = Number(p.linked) > 0;
    const selected = ND.quick && ND.quick.portId === p.id;
    return `<button class="nd-pick${selected ? ' sel' : ''}" data-nd-qpick="${p.id}"
        data-nd-qpick-label="${esc(`${p.device} · ${p.name}`)}"
        data-nd-qpick-rack="${esc(`${p.site} · ${p.rack}`)}"
        title="${busy ? 'Δεσμευμένο σε σύνδεση' : 'Διάλεξε αυτό το port'}"${busy ? ' disabled' : ''}>
        <b>${esc(p.name)}</b><span>${esc(p.device)}</span>
        <em>${esc(p.site)} · ${esc(p.rack)}</em>${busy ? '<i>δεσμευμένο</i>' : ''}
      </button>`;
  }).join('');
}

function ndQuickSearchLine() {
  const st = ND.portSearch;
  const hint = st.loading ? 'Αναζήτηση…' : (st.q.trim() ? `${st.total} ports` : 'π.χ. sw-01, eth3, R2');
  return `<div class="nd-quick-search">
      <input id="nd-quick-q" type="search" value="${esc(st.q)}" autocomplete="off"
        placeholder="Ψάξε port σε άλλο rack ή χώρο…" aria-label="Αναζήτηση port σε άλλο rack" />
      <span class="nd-quick-hint" id="nd-quick-hint">${esc(hint)}</span>
    </div>
    <div class="nd-pick-list" id="nd-quick-results">${ndPortResultsHtml(st)}</div>`;
}

function ndQuickBar() {
  const q = ND.quick;
  if (!q) {
    return `<div class="nd-quick idle">
        <div class="nd-quick-line">Γρήγορη σύνδεση: πάτησε ένα port στο rack ή διάλεξε από την αναζήτηση, μετά το ταίρι του — ακόμη και σε άλλο rack.</div>
        ${ndQuickSearchLine()}
      </div>`;
  }
  return `<div class="nd-quick">
      <div class="nd-quick-line">
        <span>Από: <b>${esc(q.label)}</b>${q.rack ? ` <span class="muted">(${esc(q.rack)})</span>` : ''} → διάλεξε το ταίρι του</span>
        <input id="nd-quick-cable" value="${esc(q.cable)}" placeholder="καλώδιο" aria-label="Καλώδιο" />
        <input id="nd-quick-len" type="number" min="0" max="10000" step="0.5" placeholder="μέτρα" aria-label="Μήκος σε μέτρα" />
        <button class="btn ghost xs" id="nd-quick-cancel">Άκυρο</button>
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
    hint.textContent = st.loading ? 'Αναζήτηση…' : (st.q.trim() ? `${st.total} ports` : 'π.χ. sw-01, eth3, R2');
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
    const page = await api(`/netdoc/ports?q=${encodeURIComponent(st.q.trim())}&limit=15`);
    if (seq !== st.seq) return;
    st.rows = page.ports || [];
    st.total = Number(page.total || st.rows.length);
  } catch (err) {
    if (seq !== st.seq) return;
    st.rows = [];
    st.total = 0;
    st.error = ndErrorText(err, 'Η αναζήτηση ports απέτυχε.');
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
    toast('Το μήκος πρέπει να είναι 0–10000 μέτρα', 'err');
    return;
  }
  if (cable.length > 80) {
    toast('Το όνομα καλωδίου έως 80 χαρακτήρες', 'err');
    return;
  }
  try {
    await api('/netdoc/links', { json: { port_a: portA, port_b: portB, cable, length_m: rawLength } });
    ND.quick = null;
    ND.portSearch = { q: '', rows: [], total: 0, seq: ND.portSearch.seq, loading: false, error: '' };
    await loadNetdoc();
    renderPage();
    toast('Η σύνδεση καταγράφηκε');
  } catch (err) {
    toast(ndErrorText(err, 'Η σύνδεση απέτυχε'), 'err');
    await loadNetdoc();
    renderPage();
  }
}

function ndQuickPick(portId, label, rack) {
  ndQuickPort(portId, label, { rack });
}

/* ------------------------------------------------------------ links table */
function ndCountHtml() {
  const st = ND.links;
  if (st.loading && !st.loaded) return 'Φόρτωση…';
  const pages = Math.max(1, Math.ceil(st.total / st.limit));
  const page = Math.min(pages, Math.floor(st.offset / st.limit) + 1);
  let html = `Σύνολο <b>${st.total}</b> ${st.total === 1 ? 'σύνδεση' : 'συνδέσεις'}`;
  if (st.total > st.limit) html += `<span class="sep">·</span>σελίδα <b>${page}</b> / ${pages}`;
  return html;
}

function ndPagerHtml() {
  const st = ND.links;
  if (st.total <= st.limit) return '';
  const pages = Math.max(1, Math.ceil(st.total / st.limit));
  const page = Math.min(pages, Math.floor(st.offset / st.limit) + 1);
  return `<div class="nd-pager">
      <button class="btn ghost xs" data-nd-page="first"${page <= 1 ? ' disabled' : ''}>« Πρώτη</button>
      <button class="btn ghost xs" data-nd-page="prev"${page <= 1 ? ' disabled' : ''}>‹ Προηγούμενη</button>
      <span class="nd-page-info">Σελίδα <b>${page}</b> / ${pages}</span>
      <button class="btn ghost xs" data-nd-page="next"${page >= pages ? ' disabled' : ''}>Επόμενη ›</button>
      <button class="btn ghost xs" data-nd-page="last"${page >= pages ? ' disabled' : ''}>Τελευταία »</button>
    </div>`;
}

function ndTableBody() {
  const st = ND.links;
  if (!st.loaded) return '<p class="hint">Φόρτωση συνδέσεων…</p>';
  if (st.error) {
    return `<div class="nd-error"><p>${esc(st.error)}</p>
        <button class="btn ghost xs" data-nd-links-refresh>Δοκίμασε ξανά</button></div>`;
  }
  if (!st.rows.length) {
    return `<p class="hint">${st.q.trim()
      ? `Καμία σύνδεση για «${esc(st.q.trim())}».`
      : 'Καμία σύνδεση ακόμα — διάλεξε δύο ports στη «Διάταξη».'}</p>`;
  }
  const rows = st.rows.map((link) => `<tr>
      <td><div class="nd-cell"><span>${esc(link.device_a)}</span><span class="nd-port-ref">${esc(link.port_a_name)}</span></div></td>
      <td class="nd-arrow">→</td>
      <td><div class="nd-cell"><span>${esc(link.device_b)}</span><span class="nd-port-ref">${esc(link.port_b_name)}</span></div></td>
      <td><div class="nd-cell"><span class="mono">${esc(link.cable) || '—'}</span><span class="nd-port-ref">${link.length_m ? `${link.length_m} m` : ''}</span></div></td>
      <td class="mono nd-racks">${esc(link.rack_a)} → ${esc(link.rack_b)}</td>
      <td class="nd-row-actions">
        <button class="btn ghost xs" data-nd-link-edit="${link.id}">Επεξεργασία</button>
        <button class="btn ghost xs danger" data-nd-link-del="${link.id}">Αποσύνδεση</button>
      </td>
    </tr>`).join('');
  return `<table class="nd-table">
      <thead><tr><th>Α</th><th></th><th>Β</th><th>Καλώδιο</th><th>Racks</th><th></th></tr></thead>
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
      const page = await api(`/netdoc/links/page?q=${encodeURIComponent(queryUsed.trim())}&limit=${st.limit}&offset=${fetchedOffset}`);
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
    st.error = ndErrorText(err, 'Η λίστα συνδέσεων δεν φόρτωσε.');
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
    toast('Η σύνδεση δεν είναι στην τρέχουσα σελίδα — ανανέωσε τον πίνακα', 'err');
    return;
  }
  ND.edit = { id: linkId, side: 'a', rows: [], q: '', seq: 0, loading: false, error: '' };
  openModal(`<h3>Επεξεργασία σύνδεσης</h3>
    <p class="lead">#${link.id} · ${esc(link.device_a)} ↔ ${esc(link.device_b)}</p>
    <form id="nd-link-form" autocomplete="off">
      <div class="row">
        <label class="field"><span>Καλώδιο</span><input id="nd-edit-cable" maxlength="80" value="${esc(link.cable || '')}" placeholder="CAT6-12" /></label>
        <label class="field"><span>Μήκος (m)</span><input id="nd-edit-len" type="number" min="0" max="10000" step="0.5" value="${link.length_m ? Number(link.length_m) : ''}" /></label>
      </div>
      <p class="nd-side-head">Άκρα</p>
      <div class="nd-side-row on" id="nd-side-a">
        <div><b>${esc(link.device_a)} · ${esc(link.port_a_name)}</b><span>${esc(link.rack_a)}</span></div>
        <button type="button" class="btn ghost xs" data-nd-edit-side="a">Άλλαξε</button>
      </div>
      <div class="nd-side-row" id="nd-side-b">
        <div><b>${esc(link.device_b)} · ${esc(link.port_b_name)}</b><span>${esc(link.rack_b)}</span></div>
        <button type="button" class="btn ghost xs" data-nd-edit-side="b">Άλλαξε</button>
      </div>
      <input type="hidden" id="nd-edit-a" value="${Number(link.port_a)}" />
      <input type="hidden" id="nd-edit-b" value="${Number(link.port_b)}" />
      <div class="nd-edit-search">
        <input id="nd-edit-q" type="search" placeholder="Ψάξε port για την πλευρά Α…" autocomplete="off" aria-label="Αναζήτηση port για αλλαγή άκρου" />
      </div>
      <div class="nd-edit-results" id="nd-edit-results"></div>
      <p class="error-text" id="nd-edit-error"></p>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-close>Άκυρο</button>
        <button type="submit" class="btn primary">Αποθήκευση</button>
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
    input.placeholder = ND.edit.side === 'a' ? 'Ψάξε port για την πλευρά Α…' : 'Ψάξε port για την πλευρά Β…';
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
  if (st.loading) return '<p class="hint">Αναζήτηση…</p>';
  if (!st.rows.length) return '<p class="hint">Κανένα port δεν ταιριάζει.</p>';
  return st.rows.map((p) => {
    const busy = Number(p.linked) > 0;
    return `<button type="button" class="nd-pick" data-nd-edit-pick="${p.id}"
        data-nd-qpick-label="${esc(`${p.device} · ${p.name}`)}"
        data-nd-qpick-rack="${esc(`${p.site} · ${p.rack}`)}"${busy ? ' disabled' : ''}>
        <b>${esc(p.name)}</b><span>${esc(p.device)}</span>
        <em>${esc(p.site)} · ${esc(p.rack)}</em>${busy ? '<i>δεσμευμένο</i>' : ''}
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
  if (box) box.innerHTML = '<p class="hint">Αναζήτηση…</p>';
  try {
    const page = await api(`/netdoc/ports?q=${encodeURIComponent(e.q.trim())}&limit=12`);
    if (seq !== e.seq) return;
    e.rows = page.ports || [];
  } catch (err) {
    if (seq !== e.seq) return;
    e.rows = [];
    e.error = ndErrorText(err, 'Η αναζήτηση απέτυχε.');
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
  if (!portA || !portB) { fail('Και τα δύο άκρα χρειάζονται port.'); return; }
  if (portA === portB) { fail('Τα δύο άκρα δεν μπορεί να είναι το ίδιο port.'); return; }
  if (cable.length > 80) { fail('Το καλώδιο έως 80 χαρακτήρες.'); return; }
  if (!Number.isFinite(rawLength) || rawLength < 0 || rawLength > 10000) {
    fail('Το μήκος πρέπει να είναι 0–10000 μέτρα.');
    return;
  }
  try {
    await api(`/netdoc/links/${e.id}`, {
      method: 'PATCH',
      json: { cable, length_m: rawLength, port_a: portA, port_b: portB },
    });
    ND.edit = null;
    closeModal();
    await loadNetdoc();
    renderPage();
    toast('Η σύνδεση ενημερώθηκε');
  } catch (err) {
    fail(ndErrorText(err, 'Η ενημέρωση απέτυχε.'));
  }
}

function ndUnlink(linkId) {
  const link = ND.links.rows.find((l) => l.id === linkId);
  const name = link
    ? `${link.device_a} · ${link.port_a_name} ↔ ${link.device_b} · ${link.port_b_name}`
    : `σύνδεση #${linkId}`;
  confirmDialog('Αποσύνδεση', `Η σύνδεση ${name} θα διαγραφεί. Τα ports θα μείνουν ελεύθερα.`, async () => {
    try {
      await api(`/netdoc/links/${linkId}`, { method: 'DELETE' });
      await loadNetdoc();
      renderPage();
      toast('Η σύνδεση διαγράφηκε');
    } catch (err) {
      toast(ndErrorText(err, 'Η αποσύνδεση απέτυχε'), 'err');
    }
  }, 'Αποσύνδεση');
}

/* ---------------------------------------------------------- device editor */
async function ndOpenDeviceModal(deviceId) {
  const device = ndDevice(deviceId);
  if (!device) {
    toast('Η συσκευή δεν βρίσκεται στο τρέχον rack', 'err');
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

  openModal(`<h3>Επεξεργασία συσκευής</h3>
    <p class="lead">${esc(rackName)} · U${device.position}${device.units > 1 ? `–U${device.position + device.units - 1}` : ''} · ${device.ports.length} ports</p>
    <form id="nd-device-form" data-device-id="${device.id}" autocomplete="off">
      <div class="row">
        <label class="field"><span>Όνομα</span><input name="name" required maxlength="80" value="${esc(device.name)}" /></label>
        <label class="field"><span>Τύπος</span><select name="kind">${kindOptions}</select></label>
      </div>
      <div class="row three">
        <label class="field"><span>Θέση U</span><input name="position" type="number" min="1" max="60" value="${device.position}" /></label>
        <label class="field"><span>Ύψος U</span><input name="units" type="number" min="1" max="48" value="${device.units}" /></label>
        <label class="field"><span>Rack</span><input value="${esc(rackName)}" disabled /></label>
      </div>
      <div class="row">
        <label class="field"><span>Vendor</span><input name="vendor" maxlength="60" value="${esc(device.vendor || '')}" placeholder="Cisco" /></label>
        <label class="field"><span>Μοντέλο</span><input name="model" maxlength="60" value="${esc(device.model || '')}" placeholder="C9300-24T" /></label>
      </div>
      <div class="row">
        <label class="field"><span>Mgmt IP</span><input name="mgmt_ip" maxlength="64" value="${esc(device.mgmt_ip || '')}" placeholder="10.0.0.10" /></label>
        <label class="field"><span>Host</span>
          <select name="host_id" id="nd-device-host" data-had-host="${device.host_id ? '1' : ''}">
            <option value="">— χωρίς host —</option>${hostOptions}
          </select>
          <span class="nd-note" id="nd-host-note"></span>
        </label>
      </div>
      <label class="field"><span>Σημείωση</span><textarea name="note" rows="2" maxlength="400" placeholder="π.χ. uplink στον πάροχο">${esc(device.note || '')}</textarea></label>
      <p class="error-text" id="nd-device-error"></p>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-close>Άκυρο</button>
        <button type="submit" class="btn primary">Αποθήκευση</button>
      </div>
      <div class="danger-zone">
        <span class="hint">Η διαγραφή αφαιρεί τη συσκευή, τα ports και τις συνδέσεις της.</span>
        <button type="button" class="btn danger xs" data-nd-dev-del="${device.id}">Διαγραφή</button>
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
    ? 'Το API δεν επιτρέπει αφαίρεση host: άφησε τον τρέχοντα ή διάλεξε άλλον.'
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

  if (!deviceId) { fail('Δεν βρέθηκε η συσκευή — άνοιξε ξανά το rack.'); return; }
  if (!name) { fail('Το όνομα είναι υποχρεωτικό.'); return; }
  if (!Number.isInteger(position) || position < 1 || position > 60) { fail('Η θέση U πρέπει 1–60.'); return; }
  if (!Number.isInteger(units) || units < 1 || units > 48) { fail('Το ύψος U πρέπει 1–48.'); return; }
  if (rackUnits && position + units - 1 > rackUnits) {
    fail(`Η συσκευή βγαίνει έξω από το rack (${rackUnits}U).`);
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
  if (payload.note.length > 400) { fail('Η σημείωση έως 400 χαρακτήρες.'); return; }

  try {
    const saved = await api(`/netdoc/devices/${deviceId}`, { method: 'PATCH', json: payload });
    const hostKept = !payload.host_id && saved && saved.host_id;
    closeModal();
    ND.deviceId = deviceId;
    await loadNetdoc();
    renderPage();
    if (hostKept) {
      toast('Αποθηκεύτηκε — το API δεν επιτρέπει αφαίρεση host, ο host παρέμεινε', 'err');
    } else {
      toast('Η συσκευή ενημερώθηκε');
    }
  } catch (err) {
    fail(ndErrorText(err, 'Η αποθήκευση απέτυχε — έλεγξε τα πεδία.'));
  }
}

async function ndDeleteDevice(deviceId) {
  const device = ndDevice(deviceId);
  const label = device ? device.name : `#${deviceId}`;
  let deps;
  try {
    deps = await api(`/netdoc/devices/${deviceId}/dependents`);
  } catch (err) {
    toast(ndErrorText(err, 'Δεν φόρτωσαν τα εξαρτήματα της συσκευής'), 'err');
    return;
  }
  const ports = Number(deps.ports || 0);
  const links = Number(deps.links || 0);
  const text = links
    ? `Η συσκευή «${label}» έχει ${ports} ${ports === 1 ? 'port' : 'ports'} και ${links} ${links === 1 ? 'σύνδεση' : 'συνδέσεις'}. Θα διαγραφεί μαζί με όλα της τα ports και τις συνδέσεις.`
    : `Η συσκευή «${label}» έχει ${ports} ${ports === 1 ? 'port' : 'ports'} χωρίς συνδέσεις. Θα διαγραφεί μαζί με τα ports της.`;
  confirmDialog('Διαγραφή συσκευής', text, async () => {
    try {
      await api(`/netdoc/devices/${deviceId}`, { method: 'DELETE' });
      ND.deviceId = null;
      await loadNetdoc();
      renderPage();
      toast('Η συσκευή διαγράφηκε');
    } catch (err) {
      toast(ndErrorText(err, 'Η διαγραφή απέτυχε'), 'err');
    }
  }, 'Διαγραφή');
}

/* ------------------------------------------------------------ ports / bulk */
function ndPortFormHtml(device) {
  const form = ND.portForm;
  const templates = ndTemplates();
  const chips = templates.length
    ? `<div class="nd-tpl-list">${templates.map((t, index) => `<span class="nd-tpl">
          <button type="button" class="nd-tpl-apply" data-nd-tpl="${index}" title="Συμπλήρωσε τη φόρμα">${esc(t.prefix)} · ${Number(t.count)} · ${esc(ND_PORT_KIND_LABEL[t.kind] || t.kind)}</button>
          <button type="button" class="nd-tpl-del" data-nd-tpl-del="${index}" title="Αφαίρεση προτύπου" aria-label="Αφαίρεση προτύπου">×</button>
        </span>`).join('')}</div>`
    : '<span class="nd-pf-label">Δεν έχεις αποθηκευμένα πρότυπα ακόμη — τα 3 τελευταία μένουν στον browser.</span>';
  return `<form class="nd-portform" id="nd-port-form" data-device="${device.id}" autocomplete="off">
      <label class="nd-pf"><span>Prefix</span><input id="nd-tpl-prefix" maxlength="12" value="${esc(form.prefix)}" /></label>
      <label class="nd-pf"><span>Πλήθος</span><input id="nd-tpl-count" type="number" min="1" max="96" value="${Number(form.count) || 24}" /></label>
      <label class="nd-pf"><span>Τύπος</span><select id="nd-tpl-kind">${ND_PORT_KINDS.map((kind) =>
        `<option value="${kind}"${form.kind === kind ? ' selected' : ''}>${esc(ND_PORT_KIND_LABEL[kind])}</option>`).join('')}</select></label>
      <button class="btn primary xs" type="submit">Προσθήκη</button>
      <button class="btn ghost xs" type="button" data-nd-portform="${device.id}">Άκυρο</button>
      <div class="nd-tpl-wrap"><span class="nd-pf-label">Πρότυπα (τελευταία 3):</span>${chips}</div>
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
  if (!deviceId) { toast('Δεν βρέθηκε η συσκευή', 'err'); return; }
  if (!prefix) { toast('Δώσε prefix (π.χ. eth)', 'err'); return; }
  if (prefix.length > 12) { toast('Το prefix έως 12 χαρακτήρες', 'err'); return; }
  if (!Number.isInteger(count) || count < 1 || count > 96) { toast('Το πλήθος πρέπει 1–96', 'err'); return; }
  try {
    const created = await api('/netdoc/ports/bulk', {
      json: { device_id: deviceId, prefix, count, kind: ND.portForm.kind },
    });
    const made = (created && created.created) || [];
    ndRememberTemplate({ prefix, count, kind: ND.portForm.kind });
    ND.portForm.open = false;
    ND.deviceId = deviceId;
    await loadNetdoc();
    renderPage();
    toast(`${made.length} ports προστέθηκαν`);
  } catch (err) {
    toast(ndErrorText(err, 'Η μαζική προσθήκη απέτυχε'), 'err');
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
  toast('Το πρότυπο αφαιρέθηκε');
}

/* --------------------------------------------------------------- views */
function ndLayoutView() {
  const layout = ND.layout;
  if (!layout) return '<p class="hint">Διάλεξε rack από αριστερά.</p>';
  const rack = layout.rack || { units: 42, name: '' };
  const devices = layout.devices || [];
  const byTopUnit = new Map();
  devices.forEach((device) => byTopUnit.set(device.position + device.units - 1, device));

  const cells = [];
  let row = 1;
  let unit = rack.units;
  while (unit >= 1) {
    const device = byTopUnit.get(unit);
    if (device) {
      const linked = device.ports.filter((p) => p.link_id).length;
      cells.push(`<div class="nd-u" style="grid-row:${row}">U${unit}</div>`);
      for (let extra = 1; extra < device.units; extra += 1) {
        cells.push(`<div class="nd-u" style="grid-row:${row + extra}">U${unit - extra}</div>`);
      }
      cells.push(`<div class="nd-device ${esc(device.kind)}" style="grid-row:${row} / span ${device.units}" data-nd-select="${device.id}" title="U${device.position}–U${unit} · ${esc(device.name)}">
          <b>${esc(device.name)}</b>
          <span>${esc(ndKindIcon(device.kind))} · ${device.ports.length} ports${linked ? ` · ${linked} δεμένα` : ''}</span>
        </div>`);
      row += device.units;
      unit -= device.units;
      continue;
    }
    // collapse long runs of empty units so the rack stays readable
    let run = 0;
    while (unit - run >= 1 && !byTopUnit.get(unit - run)) run += 1;
    if (run > 4) {
      cells.push(`<div class="nd-u muted" style="grid-row:${row}">…</div>`);
      cells.push(`<div class="nd-gap" style="grid-row:${row}">${run} κενές U (U${unit - run + 1}–U${unit})</div>`);
      row += 1;
      unit -= run;
    } else {
      cells.push(`<div class="nd-u" style="grid-row:${row}">U${unit}</div>`);
      cells.push(`<div class="nd-empty" style="grid-row:${row}"></div>`);
      row += 1;
      unit -= 1;
    }
  }

  const selectedDevice = devices.find((d) => d.id === ND.deviceId) || devices[0] || null;
  const portList = selectedDevice
    ? `<div class="nd-ports">
        <div class="nd-ports-head">
          <b>${esc(selectedDevice.name)}</b>
          <span class="hint">${selectedDevice.ports.length} ports · U${selectedDevice.position}</span>
          <button class="btn ghost xs" data-nd-dev-edit="${selectedDevice.id}">Επεξεργασία</button>
          <button class="btn ghost xs" data-nd-portform="${selectedDevice.id}">${ND.portForm.open ? 'Κλείσιμο' : 'Ports'}</button>
          <button class="btn ghost xs danger" data-nd-dev-del="${selectedDevice.id}">Διαγραφή</button>
        </div>
        ${ND.portForm.open ? ndPortFormHtml(selectedDevice) : ''}
        <div class="nd-port-grid">${selectedDevice.ports.map((p) => ndPortButton(selectedDevice, p)).join('')
          || '<p class="hint">Χωρίς ports — πάτησε «Ports» για μαζική προσθήκη.</p>'}</div>
        <p class="nd-port-locked">Τα δεσμευμένα ports είναι κλειστά· για αλλαγή ή αποσύνδεση άνοιξε «Συνδέσεις».</p>
      </div>`
    : '<p class="hint">Πρόσθεσε συσκευή για να δεις ports.</p>';

  return `<div class="nd-layout">
      ${ndQuickBar()}
      <div class="nd-rack-view" style="--units:${rack.units}">${cells.join('')}</div>
      ${portList}
    </div>`;
}

function ndTableView() {
  return `<div class="nd-links">
      <div class="nd-links-head">
        <input id="nd-links-q" class="nd-links-input" type="search" value="${esc(ND.links.q)}" autocomplete="off"
          placeholder="Αναζήτηση σε συσκευή, port ή καλώδιο…" aria-label="Αναζήτηση συνδέσεων" />
        <div class="nd-links-tools">
          <button class="btn ghost xs" data-nd-links-clear>Καθαρισμός</button>
          <button class="btn ghost xs" data-nd-links-refresh>Ανανέωση</button>
          <span class="nd-count" id="nd-links-count">${ndCountHtml()}</span>
        </div>
      </div>
      <div id="nd-table-wrap">${ndTableBody()}</div>
    </div>`;
}

function ndAuditView() {
  const audit = ND.audit;
  if (!audit) return '<p class="hint">Πάτησε «Έλεγχος» για να σαρωθούν τα δεδομένα.</p>';
  const labels = {
    overlap: 'Επικάλυψη U',
    outside_rack: 'Έξω από το rack',
    duplicate_device: 'Διπλό όνομα',
    unlinked_ports: 'Ports χωρίς σύνδεση',
    duplicate_port: 'Port σε δύο συνδέσεις',
  };
  const items = (audit.issues || []).map((issue) => `<li class="nd-issue ${esc(issue.kind)}">
      <b>${esc(labels[issue.kind] || issue.kind)}</b>
      <span>${esc(issue.rack)}${issue.device ? ' · ' + esc(issue.device) : ''}</span>
      <em>${esc(issue.detail)}</em></li>`).join('');
  const summary = audit.summary || { racks: 0, ports: 0, links: 0, issues: 0 };
  return `<div class="nd-summary">
      <span><b>${summary.racks}</b> racks</span><span><b>${summary.ports}</b> ports</span>
      <span><b>${summary.links}</b> συνδέσεις</span>
      <span class="${summary.issues ? 'warn' : 'ok'}"><b>${summary.issues}</b> ευρήματα</span>
    </div>
    <ul class="nd-issues">${items || '<li class="nd-issue ok"><b>Καθαρό</b><em>Κανένα πρόβλημα.</em></li>'}</ul>`;
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
        <thead><tr><th>Θέση</th><th>Συσκευή</th><th>Τύπος</th><th>Ports</th><th>Δεμένα</th><th>Vendor / Μοντέλο</th><th>Mgmt IP</th><th>Host</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="8">Καμία συσκευή.</td></tr>'}</tbody>
      </table>
    </section>`;
}

function ndPrintSheetHtml(rows, total, note) {
  const layout = ND.layout;
  const when = new Date().toLocaleString('el-GR');
  const place = layout ? `${layout.site || ''} · ${layout.rack.name}` : 'Όλα τα racks';
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
      <div><b>MUXIVRA</b><span>Τεκμηρίωση rack &amp; καλωδίων</span></div>
      <div class="nd-print-meta"><span>${esc(place)}</span><span>${esc(when)}</span></div>
    </header>
    ${layout ? ndPrintRackHtml(layout) : '<p class="nd-print-note">Δεν έχει επιλεγεί rack — τυπώνεται μόνο ο πίνακας συνδέσεων.</p>'}
    <section class="nd-print-block">
      <h2>Συνδέσεις (${rows.length} από ${total})</h2>
      <table class="nd-print-table">
        <thead><tr><th>Συσκευή Α</th><th>Port Α</th><th>Συσκευή Β</th><th>Port Β</th><th>Καλώδιο</th><th>Μήκος</th><th>Racks</th></tr></thead>
        <tbody>${linkRows || '<tr><td colspan="7">Καμία σύνδεση.</td></tr>'}</tbody>
      </table>
    </section>
    ${note ? `<p class="nd-print-note">${esc(note)}</p>` : ''}
    ${total > rows.length ? `<p class="nd-print-note">Τυπώνονται οι πρώτες ${rows.length} από ${total} συνδέσεις (όριο 500). Χρησιμοποίησε CSV για το πλήρες αρχείο.</p>` : ''}`;
}

async function ndPrint() {
  const sheet = document.querySelector('#nd-print');
  if (!sheet) {
    toast('Άνοιξε πρώτα το rack ή τις συνδέσεις', 'err');
    return;
  }
  let rows = (ND.links.rows || []).slice();
  let total = Number(ND.links.total || rows.length);
  let note = '';
  try {
    const all = await api('/netdoc/links/page?limit=500');
    rows = all.links || rows;
    total = Number(all.total || rows.length);
  } catch (err) {
    note = 'Η πλήρης λίστα δεν φόρτωσε — τυπώνονται οι γραμμές της τρέχουσας σελίδας.';
    if (!rows.length) {
      toast(ndErrorText(err, 'Δεν υπάρχουν συνδέσεις για εκτύπωση'), 'err');
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
    toast('Ο browser δεν άνοιξε παράθυρο εκτύπωσης', 'err');
  }
}

/* -------------------------------------------------------------- audit load */
async function ndAudit() {
  ND.audit = await api('/netdoc/audit');
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
            <button class="btn ghost xs" data-nd-site-edit="${site.id}" title="Μετονομασία χώρου">Μετονομασία</button>
            <button class="btn ghost xs danger" data-nd-site-del="${site.id}" title="Διαγραφή χώρου">Διαγραφή</button>
          </span>
        </div>
        ${(site.racks || []).length ? (site.racks || []).map((r) => `
          <button class="nd-rack${r.id === ND.rackId ? ' on' : ''}" data-nd-rack="${r.id}">
            <b>${esc(r.name)}</b><span>${(r.devices || []).length} συσκευές · ${r.units}U</span>
          </button>`).join('') : '<p class="hint">χωρίς racks</p>'}
      </div>`).join('') : '<p class="hint">Κανένας χώρος ακόμα.</p>';

  const addDevice = rack && tab === 'layout'
    ? `<div class="nd-form">
        <input id="nd-dev-name" placeholder="Συσκευή" autocomplete="off" />
        <select id="nd-dev-kind" aria-label="Τύπος συσκευής">
          ${ND_DEVICE_KINDS.map((kind) => `<option value="${kind}">${esc(ND_KIND_LABEL[kind])}</option>`).join('')}
        </select>
        <input id="nd-dev-pos" type="number" min="1" max="60" value="1" title="U θέση" aria-label="Θέση U" />
        <button class="btn primary xs" id="nd-add-dev">+ Συσκευή</button>
      </div>`
    : '';

  if (tab === 'table' && !ND.links.loaded && !ND.links.loading) ndLoadLinks();

  $('#page-racks').innerHTML = `<div class="page-head">
      <div><h2>Racks &amp; Καλώδια</h2><p>Χώροι, racks, συσκευές, ports και συνδέσεις — με έλεγχο διπλών ports.</p></div>
      <div class="actions">
        <button class="btn ghost xs" data-nd-print>Εκτύπωση / PDF</button>
        <button class="btn ghost xs" data-nd-csv>CSV</button>
      </div>
    </div>
    <div class="nd-split">
      <aside class="panel nd-tree">
        <h2>Χώροι</h2>
        ${treeHtml}
        <div class="nd-form">
          <input id="nd-site-name" placeholder="Νέος χώρος" autocomplete="off" />
          <button class="btn ghost xs" id="nd-add-site" aria-label="Προσθήκη χώρου">+</button>
        </div>
        <div class="nd-form">
          <select id="nd-rack-site" aria-label="Χώρος">${sites.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('')}</select>
          <input id="nd-rack-name" placeholder="Rack" autocomplete="off" />
          <button class="btn ghost xs" id="nd-add-rack" aria-label="Προσθήκη rack">+</button>
        </div>
      </aside>

      <div class="panel nd-main">
        <div class="nd-tabs">
          <button class="nd-tab${tab === 'layout' ? ' on' : ''}" data-nd-tab="layout">Διάταξη</button>
          <button class="nd-tab${tab === 'table' ? ' on' : ''}" data-nd-tab="table">Συνδέσεις (${linkTotal})</button>
          <button class="nd-tab${tab === 'audit' ? ' on' : ''}" data-nd-tab="audit">Έλεγχος</button>
          <span class="spacer"></span>
          ${rack ? `<span class="hint">${esc(rack.siteName)} · ${esc(rack.name)}</span>` : ''}
        </div>
        ${addDevice}
        ${tab === 'layout' ? ndLayoutView() : tab === 'table' ? ndTableView() : ndAuditView()}
      </div>
    </div>
    <div class="nd-print" id="nd-print" aria-hidden="true"></div>`;
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

// CSV download με το customer context του api() (ένα σκέτο href δεν στέλνει header)
document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-nd-csv]');
  if (!button) return;
  event.preventDefault();
  try {
    const response = await fetch('/api/netdoc/links.csv', {
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
    alert('Η εξαγωγή CSV απέτυχε: ' + error.message);
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
    const name = prompt('Νέο όνομα χώρου:', site.name);
    if (!name || name.trim() === site.name) return;
    try {
      await api(`/netdoc/sites/${siteId}`, { method: 'PATCH', json: { name: name.trim() } });
      await loadNetdoc();
      renderNetdoc();
    } catch (error) {
      alert('Η μετονομασία απέτυχε: ' + error.message);
    }
    return;
  }

  let deps = null;
  try {
    deps = await api(`/netdoc/site/${siteId}/dependents`);
  } catch (error) {
    alert('Δεν ήταν δυνατός ο έλεγχος εξαρτήσεων: ' + error.message);
    return;
  }
  const answer = prompt(
    `Διαγραφή «${site.name}»;\n\nΘα αφαιρεθούν:\n` +
    `• ${deps.racks} racks\n• ${deps.devices} συσκευές\n• ${deps.ports} θύρες\n• ${deps.links} καλώδια\n\n` +
    'Γράψε ΔΙΑΓΡΑΦΗ για επιβεβαίωση. Καμία εντολή δεν στέλνεται σε πραγματική συσκευή.',
    ''
  );
  if ((answer || '').trim().toUpperCase() !== 'ΔΙΑΓΡΑΦΗ') return;
  try {
    await api(`/netdoc/sites/${siteId}`, { method: 'DELETE' });
    if (ND.rackId && site.racks?.some((r) => r.id === ND.rackId)) ND.rackId = null;
    await loadNetdoc();
    renderNetdoc();
  } catch (error) {
    alert('Η διαγραφή απέτυχε: ' + error.message);
  }
});
