/* --------------------------------------------------------- MUXIVRA · shell
   Μεταβάσεις και context που δεν ανήκουν σε κανένα επιμέρους module:
     • ενότητες: Συσκευές · Καλωδίωση · Διάγραμμα · Τερματικά (+ Βοηθητικά)
     • μόνιμη γραμμή context (πελάτης / εγκατάσταση / αναζήτηση / Προσθήκη συσκευής)
     • split view: διάγραμμα δίπλα στο terminal
     • ενέργειες συσκευής στο modal της: SSH/Telnet/COM, web management, configuration
   Δεν ξαναγράφει τα δεδομένα· καλεί τις υπάρχουσες συναρτήσεις του app.js.
*/
'use strict';

const SH = {
  split: false,
  splitWidth: 0,
  splitDragging: false,
  sections: [
    ['hosts', 'Devices', 'Device list and connections'],
    ['racks', 'Cabling', 'Sites, racks, ports and cables'],
    ['diagram', 'Diagram', 'Network design and view'],
    ['terminals', 'Terminals', 'Open SSH / Telnet / COM sessions'],
  ],
  helpers: [
    ['keys', 'Vault', 'Credentials and keys'],
    ['snippets', 'Snippets', 'Ready-made commands'],
    ['activity', 'History', 'Activity log'],
    ['settings', 'Settings', 'Updates, backup, auto-lock'],
  ],
};

function shEsc(value) {
  if (typeof esc === 'function') {
    try { return esc(value); } catch (_) { /* fallback below */ }
  }
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function shToast(message) {
  if (typeof toast === 'function') {
    try { toast(message); return; } catch (_) { /* ignored */ }
  }
}

/* --------------------------------------------------------------- navigation */
function shBuildNav() {
  const rail = document.querySelector('.rail');
  if (!rail) return;
  const logo = rail.querySelector('.rail-logo');
  const spacer = rail.querySelector('.rail-spacer');
  rail.querySelectorAll('.rail-btn[data-view]').forEach((button) => {
    const view = button.dataset.view;
    const primary = SH.sections.find(([value]) => value === view);
    const helper = SH.helpers.find(([value]) => value === view);
    const entry = primary || helper;
    if (!entry) return;
    button.title = entry[2];
    const label = button.querySelector('em');
    if (label) label.textContent = entry[1];
    button.classList.toggle('rail-helper', !!helper);
    button.addEventListener('click', () => window.setTimeout(shFillSites, 350));
  });

  // «Τερματικά» δεν είχε δικό του rail item — παίρνει ένα, πριν τα βοηθητικά
  if (!rail.querySelector('.rail-btn[data-view="terminals"]')) {
    const button = document.createElement('button');
    button.className = 'rail-btn';
    button.dataset.view = 'terminals';
    button.title = 'Open SSH / Telnet / COM sessions';
    button.innerHTML = '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="14" rx="2"/>'
      + '<path d="M7 9l3 3-3 3M13 15h4"/></svg><em>Terminals</em>';
    button.onclick = () => {
      if (typeof S !== 'undefined' && S) S.view = 'hosts';
      if (typeof showPage === 'function') showPage('hosts');
      const stage = document.querySelector('#stage');
      if (stage) stage.scrollTop = 0;
      shFocusTerminals();
    };
    if (spacer && spacer.parentNode) spacer.parentNode.insertBefore(button, spacer);
    else rail.appendChild(button);
  }

  // διαχωριστικό «Βοηθητικά»
  if (!rail.querySelector('.rail-sep')) {
    const sep = document.createElement('div');
    sep.className = 'rail-sep';
    sep.title = 'Utilities';
    const first = rail.querySelector('.rail-btn[data-view="keys"]');
    if (first && first.parentNode) first.parentNode.insertBefore(sep, first);
  }
  if (logo) logo.textContent = '▚';
}

function shFocusTerminals() {
  const tabs = document.querySelectorAll('#tabs-list [data-tab], #tabs-list .tab');
  if (!tabs.length) {
    shToast('No open terminal — start a session from the device list.');
    return;
  }
  const last = tabs[tabs.length - 1];
  if (typeof last.click === 'function') last.click();
}

/* -------------------------------------------------------------- context bar */
async function shFillSites() {
  const select = document.querySelector('#site-select');
  if (!select) return;
  let sites = [];
  try {
    if (typeof S !== 'undefined' && S && S.nd && S.nd.tree) sites = S.nd.tree.sites || [];
  } catch (_) { sites = []; }
  if (!sites.length && typeof api === 'function') {
    try { sites = ((await api('/netdoc/tree')) || {}).sites || []; } catch (_) { sites = []; }
  }
  const current = select.value;
  select.innerHTML = '<option value="all">All sites</option>'
    + sites.map((site) => `<option value="${site.id}">${shEsc(site.name)}</option>`).join('');
  if (current && Array.from(select.options).some((option) => option.value === current)) {
    select.value = current;
  }
}

function shFilterHosts(query) {
  const term = String(query || '').trim().toLowerCase();
  const rows = document.querySelectorAll('#page-hosts [data-host-row], #page-hosts .host-row, #page-hosts tbody tr');
  let shown = 0;
  rows.forEach((row) => {
    const text = (row.textContent || '').toLowerCase();
    const visible = !term || text.indexOf(term) >= 0;
    row.classList.toggle('sh-hidden', !visible);
    if (visible) shown += 1;
  });
  const counter = document.querySelector('#device-count');
  if (counter) counter.textContent = `${shown} devices`;
  if (typeof S !== 'undefined' && S) S.hostFilter = term;
}

/* --------------------------------------------------------------- split view */
function shRefitTerminals() {
  const tabs = (typeof S !== 'undefined' && S && Array.isArray(S.tabs)) ? S.tabs : [];
  tabs.forEach((tab) => {
    if (!tab || !tab.fit || !tab.pane) return;
    window.setTimeout(() => {
      try {
        tab.fit.fit();
        if (typeof sendResize === 'function') sendResize(tab);
      } catch (_) { /* ο τερματικός θα επανέλθει στο επόμενο resize */ }
    }, 60);
  });
}

function shApplySplit() {
  const main = document.querySelector('.main');
  const pane = document.querySelector('#split-pane');
  const handle = document.querySelector('#split-handle');
  const stage = document.querySelector('#stage');
  const page = document.querySelector('#page-diagram');
  const terminals = document.querySelector('#terminals');
  if (!main || !pane || !stage || !page) return;

  main.classList.toggle('split-on', SH.split);
  pane.classList.toggle('hidden', !SH.split);
  if (handle) handle.classList.toggle('hidden', !SH.split);
  const button = document.querySelector('#btn-split');
  if (button) {
    button.classList.toggle('on', SH.split);
    button.setAttribute('aria-pressed', SH.split ? 'true' : 'false');
    button.textContent = SH.split ? 'Close split' : 'Diagram side-by-side';
  }

  const hasTab = typeof S !== 'undefined' && S && S.activeTabId
    && Array.isArray(S.tabs) && S.tabs.some((tab) => tab.id === S.activeTabId);

  if (SH.split) {
    if (SH.splitWidth) pane.style.width = `${SH.splitWidth}px`;
    // το διάγραμμα πάει στο πλαϊνό panel· η κύρια σκηνή κρατά σελίδα ή terminal
    pane.innerHTML = '';
    pane.appendChild(page);
    page.classList.remove('hidden');
    if (typeof showPage === 'function') {
      const keep = (S.view && S.view !== 'diagram') ? S.view : 'hosts';
      showPage(keep);
      // το showPage κρύβει όλα τα .page — το διάγραμμα ζει πλέον στο pane
      page.classList.remove('hidden');
    }
  } else {
    if (stage && page.parentNode !== stage) stage.appendChild(page);
    if (stage && terminals && terminals.parentNode !== stage) stage.appendChild(terminals);
    pane.innerHTML = '';
    if (typeof showPage === 'function') showPage(S.view || 'hosts');
  }

  shRefitTerminals();
  if (typeof renderDiagram === 'function') {
    try { renderDiagram(true); } catch (_) { /* ignored */ }
  }
  try { window.localStorage.setItem('muxivra.split.on', SH.split ? '1' : '0'); } catch (_) { /* ignored */ }
}

function shWireSplit() {
  const button = document.querySelector('#btn-split');
  if (button) {
    button.onclick = () => { SH.split = !SH.split; shApplySplit(); };
  }
  const handle = document.querySelector('#split-handle');
  if (!handle) return;
  handle.addEventListener('pointerdown', (event) => {
    SH.splitDragging = true;
    handle.setPointerCapture(event.pointerId);
  });
  handle.addEventListener('pointermove', (event) => {
    if (!SH.splitDragging) return;
    const main = document.querySelector('.main');
    const pane = document.querySelector('#split-pane');
    if (!main || !pane) return;
    const bounds = main.getBoundingClientRect();
    const width = Math.max(360, Math.min(bounds.width - 420, bounds.right - event.clientX));
    pane.style.width = `${Math.round(width)}px`;
    SH.splitWidth = Math.round(width);
  });
  window.addEventListener('resize', () => {
    if (!SH.split) return;
    shRefitTerminals();
    if (typeof renderDiagram === 'function') {
      try { renderDiagram(true); } catch (_) { /* ignored */ }
    }
  });
  handle.addEventListener('pointerup', () => {
    SH.splitDragging = false;
    try { window.localStorage.setItem('muxivra.split.width', String(SH.splitWidth)); } catch (_) { /* ignored */ }
    if (typeof renderDiagram === 'function') {
      try { renderDiagram(true); } catch (_) { /* ignored */ }
    }
    shRefitTerminals();
  });
  try {
    const saved = Number(window.localStorage.getItem('muxivra.split.width'));
    if (Number.isFinite(saved) && saved > 320) SH.splitWidth = saved;
    SH.split = window.localStorage.getItem('muxivra.split.on') === '1';
  } catch (_) { /* ignored */ }
}

/* ----------------------------------------------------------- device actions */
function shApi(path, options) {
  if (typeof api === 'function') return api(path, options || {});
  const opts = options || {};
  const headers = { 'x-requested-with': 'sshdeck' };
  if (typeof S !== 'undefined' && S && S.customerId) headers['x-customer-id'] = String(S.customerId);
  const init = { method: opts.method || 'GET', headers, credentials: 'same-origin' };
  if (opts.json) {
    headers['content-type'] = 'application/json';
    init.body = JSON.stringify(opts.json);
  }
  return fetch(`/api${path}`, init).then(async (response) => {
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.detail || `HTTP ${response.status}`);
      error.detail = payload.detail;
      throw error;
    }
    return payload;
  });
}

async function shOpenDeviceWeb(deviceId, deviceName) {
  try {
    const result = await shApi(`/netdoc/devices/${deviceId}/web`, { method: 'POST', json: {} });
    if (result.mode === 'window') {
      shToast(`Web management: ${result.url} — separate window, no access to the app.`);
    } else {
      shToast(`${result.warning} (${result.url})`);
      if (result.url && typeof window !== 'undefined') window.open(result.url, '_blank', 'noopener');
    }
  } catch (error) {
    const text = error.detail === 'no_address'
      ? 'This device has no management IP — add it in the device details.'
      : `Web management did not open: ${error.message}`;
    shToast(text);
  }
}

function shOpenConfig(deviceId, deviceName) {
  const host = document.querySelector('#config-page');
  if (!host) return;
  if (typeof setView === 'function') { try { setView('config'); } catch (_) { /* fallback */ } }
  if (typeof showPage === 'function') showPage('config');
  if (window.MuxConfig) window.MuxConfig.mount(host, { deviceId, deviceName });
}

function shDecorateDeviceModal(deviceId, deviceName) {
  const modal = document.querySelector('.modal-card, .modal');
  if (!modal) return;
  if (modal.querySelector('#device-actions')) return;
  const bar = document.createElement('div');
  bar.id = 'device-actions';
  bar.className = 'device-actions';
  bar.innerHTML = `<button type="button" class="btn ghost xs" data-dev-act="web">Web management</button>
    <button type="button" class="btn ghost xs" data-dev-act="config">Configuration</button>
    <span class="device-actions-hint">Both apply to this device only.</span>`;
  bar.addEventListener('click', (event) => {
    const button = event.target.closest('[data-dev-act]');
    if (!button) return;
    if (button.dataset.devAct === 'web') shOpenDeviceWeb(deviceId, deviceName);
    else shOpenConfig(deviceId, deviceName);
  });
  const form = modal.querySelector('form');
  if (form && form.parentNode) form.parentNode.insertBefore(bar, form);
  else modal.appendChild(bar);
}

function shWrapDeviceModal() {
  if (typeof window.ndOpenDeviceModal !== 'function' || window.ndOpenDeviceModal.__wrapped) return;
  const original = window.ndOpenDeviceModal;
  const wrapped = async function (deviceId) {
    const result = await original.apply(this, arguments);
    let name = '';
    try {
      const layout = (typeof ND !== 'undefined' && ND.layout) ? ND.layout : null;
      const device = layout && (layout.devices || []).find((item) => Number(item.id) === Number(deviceId));
      name = device ? device.name : '';
    } catch (_) { name = ''; }
    window.setTimeout(() => shDecorateDeviceModal(Number(deviceId), name), 60);
    return result;
  };
  wrapped.__wrapped = true;
  window.ndOpenDeviceModal = wrapped;
}

/* -------------------------------------------------------------------- boot */
function shWrapShowPage() {
  if (typeof window.showPage !== 'function' || window.showPage.__wrapped) return;
  const original = window.showPage;
  const wrapped = function (view) {
    const result = original.apply(this, arguments);
    if (view === 'racks' || view === 'diagram') window.setTimeout(shFillSites, 250);
    if (view === 'hosts') window.setTimeout(() => {
      const search = document.querySelector('#device-search');
      if (search && search.value) shFilterHosts(search.value);
    }, 150);
    return result;
  };
  wrapped.__wrapped = true;
  window.showPage = wrapped;
  if (typeof ShowPageIsGlobal === 'undefined') { /* το app.js το καλεί ως showPage τοπικά */ }
}

function shBoot() {
  shBuildNav();
  shWrapShowPage();
  shWireSplit();
  shFillSites();
  shWrapDeviceModal();

  const addButton = document.querySelector('#btn-add-device');
  if (addButton) {
    addButton.onclick = () => {
      const host = document.querySelector('#wizard-page');
      if (typeof showPage === 'function') showPage('wizard');
      if (window.MuxWizard) {
        window.MuxWizard.mount(host, {
          onDone: async (info) => {
            if (typeof loadHosts === 'function') { try { await loadHosts(); } catch (_) { /* ignored */ } }
            if (typeof loadNetdoc === 'function') { try { await loadNetdoc(); } catch (_) { /* ignored */ } }
            if (typeof showPage === 'function') showPage('hosts');
            shFillSites();
            if (info && info.name) shToast(`${info.name} was added`);
          },
          onCancel: () => { if (typeof showPage === 'function') showPage('hosts'); },
        });
      }
    };
  }

  const search = document.querySelector('#device-search');
  if (search) {
    search.addEventListener('input', () => shFilterHosts(search.value));
    search.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { search.value = ''; shFilterHosts(''); }
    });
  }

  const site = document.querySelector('#site-select');
  if (site) {
    site.onchange = () => {
      if (typeof S !== 'undefined' && S) S.siteFilter = site.value;
      shFilterHosts(document.querySelector('#device-search') ? document.querySelector('#device-search').value : '');
      if (typeof renderPage === 'function') { try { renderPage(); } catch (_) { /* ignored */ } }
    };
  }

  if (SH.split) shApplySplit();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => window.setTimeout(shBoot, 0));
} else {
  window.setTimeout(shBoot, 0);
}

window.MuxShell = { boot: shBoot, split: shApplySplit, state: SH, openConfig: shOpenConfig, openWeb: shOpenDeviceWeb };
