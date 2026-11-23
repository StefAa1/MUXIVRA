/* MUXIVRA frontend — vanilla JS, xterm.js */
'use strict';

const S = {
  status: null,
  hosts: [],
  creds: [],
  snippets: [],
  events: [],
  tabs: [],
  activeTabId: null,
  view: 'hosts',
  collapsed: new Set(),
  search: '',
  sftp: { hostId: null, path: '.' },
  paletteIndex: 0,
  paletteItems: [],
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function hostTargetText(h) {
  const proto = h.protocol || 'ssh';
  if (proto === 'serial') return `${h.serial_port} @${h.baud} ${h.bytesize}${h.parity}${h.stopbits}`;
  if (proto === 'telnet') return `${h.hostname}:${h.port}`;
  return `${h.username}@${h.hostname}:${h.port}`;
}

function fmtSize(bytes) {
  if (!bytes && bytes !== 0) return '';
  const units = ['B', 'K', 'M', 'G', 'T'];
  let i = 0, n = bytes;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return (i === 0 ? n : n.toFixed(n < 10 ? 1 : 0)) + units[i];
}

function fmtWhen(ts) {
  if (!ts) return '—';
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString('el-GR');
}

function toast(message, kind) {
  const el = document.createElement('div');
  el.className = 'toast' + (kind === 'err' ? ' err' : '');
  el.textContent = message;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; }, 3600);
  setTimeout(() => el.remove(), 4000);
}

async function api(path, opts = {}) {
  const headers = { 'x-requested-with': 'sshdeck', ...(opts.headers || {}) };
  if (S.customerId) headers['x-customer-id'] = String(S.customerId);
  const init = { credentials: 'same-origin', ...opts, headers };
  if (opts.json !== undefined) {
    headers['content-type'] = 'application/json';
    init.body = JSON.stringify(opts.json);
    init.method = opts.method || 'POST';
    delete init.json;
  }
  const res = await fetch('/api' + path, init);
  if (res.status === 401) {
    if (S.status) S.status.authenticated = false;
    showAuth(false);
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { const data = await res.json(); detail = data.detail || detail; } catch (_) { /* ignore */ }
    // το detail μπορεί να είναι λίστα σφαλμάτων validation, όχι string
    let text = detail;
    if (Array.isArray(detail)) {
      text = detail.map((item) => (item && item.msg ? `${(item.loc || []).slice(1).join('.')}: ${item.msg}` : String(item))).join('; ');
    } else if (detail && typeof detail === 'object') {
      text = detail.message || detail.code || JSON.stringify(detail);
    }
    const error = new Error(`${text || res.status}${res.status && typeof text !== 'string' ? '' : ` (HTTP ${res.status})`}`);
    error.detail = detail;
    error.status = res.status;
    throw error;
  }
  const type = res.headers.get('content-type') || '';
  return type.includes('application/json') ? res.json() : res;
}

/* ------------------------------------------------------------------ auth */
function showAuth(setupMode) {
  $('#app').classList.add('hidden');
  const auth = $('#auth');
  auth.classList.remove('hidden');
  const setup = setupMode || !(S.status && S.status.initialized);
  $('#auth-confirm-wrap').classList.toggle('hidden', !setup);
  $('#auth-password2').required = setup;
  $('#auth-label').textContent = setup ? 'New master password' : 'Master password';
  $('#auth-submit').textContent = setup ? 'Create vault' : 'Sign in';
  $('#auth-tag').textContent = setup
    ? 'First run: set the master password that protects your vault.'
    : 'Your personal SSH control room.';
  $('#auth-message').textContent = '';
  setTimeout(() => $('#auth-password').focus(), 120);
}

async function handleAuthSubmit(event) {
  event.preventDefault();
  const setup = !$('#auth-confirm-wrap').classList.contains('hidden');
  const password = $('#auth-password').value;
  const message = $('#auth-message');
  message.textContent = '';
  try {
    if (setup) {
      if (password.length < 8) throw new Error('At least 8 characters are required.');
      if (password !== $('#auth-password2').value) throw new Error('The two passwords do not match.');
      await api('/setup', { json: { username: 'admin', password } });
      toast('Vault created.');
    } else {
      await api('/login', { json: { password } });
    }
    $('#auth-password').value = '';
    $('#auth-password2').value = '';
    await enterApp();
  } catch (err) {
    message.textContent = err.message === 'bad_password' ? 'Wrong password.' : err.message;
  }
}

/* --------------------------------------------------------------- app boot */
async function enterApp() {
  $('#auth').classList.add('hidden');
  $('#app').classList.remove('hidden');
  const status = await api('/status');
  S.status = status;
  $('#sb-user').textContent = status.username || 'admin';
  await loadCustomers();
  await Promise.all([loadHosts(), loadCreds(), loadSnippets(), loadEvents(), loadUpdateState()]);
  if (typeof loadInventory === 'function') await loadInventory();
  setView('hosts');
  renderSidebar();
  renderPage();
}

async function loadHosts() { S.hosts = await api('/hosts'); }
async function loadCreds() { S.creds = await api('/credentials'); }
async function loadSnippets() { S.snippets = await api('/snippets'); }
async function loadEvents() { S.events = await api('/events'); }
async function loadCustomers() {
  S.customers = await api('/customers');
  if (!S.customerId && S.customers.length) S.customerId = S.customers[0].id;
  const select = $('#customer-select');
  if (select) {
    select.innerHTML = S.customers
      .map((c) => `<option value="${c.id}"${c.id === S.customerId ? ' selected' : ''}>${esc(c.name)}</option>`)
      .join('');
    select.onchange = async () => {
      S.customerId = Number(select.value);
      await Promise.all([loadHosts(), loadCreds(), loadSnippets(), loadInventory()]);
      renderSidebar();
      renderPage();
      toast(`Active customer: ${S.customers.find((c) => c.id === S.customerId)?.name || ''}`);
    };
  }
  const dot = $('#ctx-dot');
  const active = (S.customers || []).find((c) => c.id === S.customerId);
  if (dot && active) dot.style.background = active.color || '#2f7bff';
  renderCustomerPill();
  const add = $('#customer-add');
  if (add) add.onclick = openCustomerModal;
}

function json_opts_empty() {
  return { json: {} };   // η πηγή είναι πλέον σταθερή στο backend
}

function renderCustomerPill() {
  const pill = $('#customer-pill');
  const name = $('#customer-pill-name');
  const menu = $('#customer-menu');
  const select = $('#customer-select');
  const list = S.customers || [];
  const active = list.find((c) => c.id === S.customerId) || list[0] || null;
  if (name) name.textContent = active ? active.name : '—';
  if (select && active) select.value = String(active.id);
  if (!menu) return;
  menu.innerHTML = list.map((customer) => `
      <button type="button" class="ctx-item${customer.id === S.customerId ? ' on' : ''}" data-customer-id="${customer.id}" role="option">
        <i class="ctx-dot" style="background:${esc(customer.color || '#2f7bff')}"></i>
        <span>${esc(customer.name)}</span>
        ${customer.code ? `<small>${esc(customer.code)}</small>` : ''}
      </button>`).join('')
    + '<button type="button" class="ctx-item ctx-item-new" data-customer-new>+ New customer</button>';

  if (pill && !pill.dataset.wired) {
    pill.dataset.wired = '1';
    pill.onclick = (event) => {
      event.stopPropagation();
      const open = menu.classList.toggle('hidden');
      pill.setAttribute('aria-expanded', open ? 'false' : 'true');
    };
    document.addEventListener('click', () => {
      menu.classList.add('hidden');
      pill.setAttribute('aria-expanded', 'false');
    });
    menu.addEventListener('click', async (event) => {
      const item = event.target.closest('[data-customer-id]');
      const create = event.target.closest('[data-customer-new]');
      menu.classList.add('hidden');
      pill.setAttribute('aria-expanded', 'false');
      if (create) { openCustomerModal(); return; }
      if (!item) return;
      S.customerId = Number(item.dataset.customerId);
      await Promise.all([loadHosts(), loadCreds(), loadSnippets(), loadInventory()]);
      renderCustomerPill();
      renderSidebar();
      renderPage();
      const chosen = (S.customers || []).find((c) => c.id === S.customerId);
      toast(`Customer: ${chosen ? chosen.name : ''}`);
    });
  }
  if (select && !select.dataset.wiredPill) {
    select.dataset.wiredPill = '1';
    select.addEventListener('change', () => {
      S.customerId = Number(select.value);
      renderCustomerPill();
    });
  }
}

function openCustomerModal() {
  openModal(`<h3>Add customer</h3>
    <form id="customer-form">
      <label class="field"><span>Name</span><input name="name" required placeholder="Athens HQ" /></label>
      <label class="field"><span>Code (optional)</span><input name="code" maxlength="12" placeholder="ATH" /></label>
      <label class="field"><span>Notes</span><input name="notes" placeholder="Contact details, contract" /></label>
      <p class="error-text" id="customer-error"></p>
      <div class="modal-actions"><button type="button" class="btn ghost" data-close>Cancel</button>
      <button class="btn primary" id="customer-save">Create</button></div>
    </form>`);
  $('#customer-save').onclick = async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData($('#customer-form')).entries());
    try {
      const created = await api('/customers', { json: data });
      S.customerId = created.id;
      await loadCustomers();
      await Promise.all([loadHosts(), loadCreds(), loadInventory()]);
      closeModal();
      renderSidebar();
      renderPage();
      toast(`Customer "${created.name}" added`);
    } catch (err) {
      $('#customer-error').textContent = err.message === 'duplicate_name'
        ? 'A customer with that name already exists.' : err.message;
    }
  };
}

async function loadUpdateState() {
  try { S.update = await api('/update/state'); } catch (_) { S.update = {}; }
}

/* ---------------------------------------------------------------- sidebar */
function filteredHosts() {
  const q = S.search.trim().toLowerCase();
  if (!q) return S.hosts;
  return S.hosts.filter((h) =>
    [h.label, h.hostname, h.username, h.group_name, (h.tags || []).join(' ')]
      .join(' ').toLowerCase().includes(q));
}

function renderSidebar() {
  const list = $('#host-list');
  const hosts = filteredHosts();
  if (!hosts.length) {
    list.innerHTML = `<div class="empty" style="margin:10px 4px;padding:22px 14px">
      <b>${S.hosts.length ? 'No results' : 'No servers yet'}</b>
      ${S.hosts.length ? 'Try a different term.' : 'Add your first server or import ~/.ssh/config.'}
    </div>`;
    return;
  }
  const groups = new Map();
  hosts.forEach((host) => {
    const key = host.group_name || 'General';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(host);
  });
  let html = '';
  for (const [group, rows] of groups) {
    const collapsed = S.collapsed.has(group);
    html += `<div class="group-head" data-group="${esc(group)}">
      <span>${collapsed ? '▸' : '▾'}</span><span>${esc(group)}</span>
      <span class="count">${rows.length}</span></div>`;
    if (collapsed) continue;
    rows.forEach((host) => { html += hostRowHtml(host); });
  }
  list.innerHTML = html;
}

function hostRowHtml(host) {
  const active = S.tabs.some((t) => t.host.id === host.id);
  const tags = (host.tags || []).slice(0, 2).map((t) => `<span class="chip">${esc(t)}</span>`).join('');
  const proto = host.protocol || 'ssh';
  const protoChip = proto === 'ssh' ? '' : `<span class="chip proto ${proto}">${proto === 'serial' ? 'COM' : 'TELNET'}</span>`;
  const cred = host.credential_name ? `<span class="chip accent">${esc(host.credential_name)}</span>` : '';
  const sub = proto === 'serial'
    ? `${esc(host.serial_port)} · ${host.baud} ${host.bytesize}${host.parity}${host.stopbits}`
    : `${esc(host.hostname)}:${host.port}`;
  return `<div class="host-row${active ? ' active' : ''}" data-host="${host.id}">
    <span class="host-rail" style="background:${esc(host.color || '#2f7bff')}"></span>
    <div class="host-meta">
      <div class="host-name">${esc(host.label)}</div>
      <div class="host-sub">${sub}</div>
      <div class="host-tags">${protoChip}${cred}${tags}
        <button class="chip web-chip" data-act="web" data-id="${host.id}" title="Open the device web interface">Web</button>
      </div>
    </div>
    <div class="host-actions">
      <button class="icon-btn" data-act="web" data-id="${host.id}" title="Device web interface">🌐</button>
      <button class="icon-btn" data-act="edit" data-id="${host.id}" title="Edit">✎</button>
      <button class="icon-btn" data-act="del" data-id="${host.id}" title="Delete">🗑</button>
    </div>
  </div>`;
}

/* ---------------------------------------------------------------- terminal */
function activeTab() { return S.tabs.find((t) => t.id === S.activeTabId) || null; }

function setView(view) {
  S.view = view;                       // keep the selected tab: switching sections is not closing
  $$('.rail-btn[data-view]').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  renderTabs();
  renderPage();
}

function activateTab(id) {
  S.activeTabId = id;
  S.view = 'hosts';                    // a terminal always belongs to the hosts view
  $$('.rail-btn[data-view]').forEach((b) => b.classList.toggle('active', b.dataset.view === 'hosts'));
  S.tabs.forEach((tab) => {
    tab.pane.classList.toggle('active', tab.id === id);
  });
  showPage('hosts');                  // shared transition: view-* classes, pages, terminals
  renderTabs();
  renderSidebar();
  requestAnimationFrame(() => {
    const tab = activeTab();
    if (!tab) return;
    tab.fit.fit();
    sendResize(tab);
    scheduleFit(tab);
    tab.term.focus();
  });
}

function refitActive() {
  const tab = activeTab();
  if (!tab) return;
  tab.fit.fit();
  sendResize(tab);
}

/* xterm measures its character cell only after the first layout, so an early
   fit() silently no-ops. Retry a few times and once webfonts are ready. */
function scheduleFit(tab) {
  [0, 120, 350, 900].forEach((delay) => {
    setTimeout(() => {
      if (!tab.term || !tab.pane.isConnected || !tab.pane.classList.contains('active')) return;
      tab.fit.fit();
      sendResize(tab);
    }, delay);
  });
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => {
      if (tab.term && tab.pane.classList.contains('active')) { tab.fit.fit(); sendResize(tab); }
    });
  }
}

function renderTabs() {
  const list = $('#tabs-list');
  list.innerHTML = S.tabs.map((tab) => {
    const state = tab.state || 'connecting';
    const dotClass = state === 'open' ? 'ok' : state === 'closed' ? 'bad' : '';
    const stateText = state === 'open' ? 'Ready' : state === 'closed' ? 'Disconnected' : 'Connecting';
    return `<div class="tab${tab.id === S.activeTabId ? ' active' : ''}" data-tab="${tab.id}" title="${stateText}">
      <span class="dot ${dotClass}"></span><span>${esc(tab.host.label)}</span>
      <span class="tab-state ${dotClass}" aria-label="${stateText}">${stateText}</span>
      <span class="x" data-tab-close="${tab.id}">✕</span></div>`;
  }).join('');
  updateStatusbar();
}

function updateStatusbar() {
  const tab = activeTab();
  if (!tab) {
    $('#status-main').textContent = 'Ready';
    $('#status-fp').textContent = '';
    $('#status-size').textContent = '';
    return;
  }
  $('#status-main').textContent = tab.target || hostTargetText(tab.host);
  $('#status-fp').textContent = tab.fingerprint || '';
  $('#status-size').textContent = tab.term ? `${tab.term.cols}×${tab.term.rows}` : '';
}

function openTerminal(host) {
  const existing = S.tabs.find((t) => t.host.id === host.id);
  if (existing) { setView('hosts'); activateTab(existing.id); return; }
  const id = 'tab-' + Date.now();
  const pane = document.createElement('div');
  pane.className = 'term-pane';
  const shell = document.createElement('div');
  shell.className = 'term-shell';
  pane.appendChild(shell);
  $('#terminals').appendChild(pane);

  const term = new Terminal({
    cursorBlink: true,
    fontSize: 13,
    fontFamily: 'ui-monospace, "JetBrains Mono", "SF Mono", monospace',
    scrollback: 8000,
    allowProposedApi: true,
    theme: {
      background: '#080a0e', foreground: '#dce8f7', cursor: '#2f7bff', selectionBackground: '#132a52',
      black: '#0b0f14', red: '#ff6b6b', green: '#2f7bff', yellow: '#ffc75f', blue: '#4f8bff',
      magenta: '#c792ea', cyan: '#5ee7f0', white: '#dce8f7',
      brightBlack: '#5c6a7d', brightRed: '#ff8f8f', brightGreen: '#6cf5b8', brightYellow: '#ffd98a',
      brightBlue: '#9db4ff', brightMagenta: '#dbaef5', brightCyan: '#8bf1f7', brightWhite: '#ffffff',
    },
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(shell);

  const tab = { id, host, term, fit, pane, ws: null, state: 'connecting', fingerprint: '' };
  tab.observer = new ResizeObserver(() => {
    if (tab.pane.classList.contains('active')) {
      tab.fit.fit();
      sendResize(tab);
    }
  });
  tab.observer.observe(pane);
  S.tabs.push(tab);
  setView('hosts');
  activateTab(id);
  renderSidebar();
  connectTab(tab);
}

function connectTab(tab) {
  tab.state = 'connecting';
  renderTabs();
  tab.term.writeln(`\x1b[38;5;79m▚ sshdeck\x1b[0m  ${(tab.host.protocol || 'ssh').toUpperCase()} → \x1b[1m${tab.host.label}\x1b[0m (${hostTargetText(tab.host)})…`);
  tab.fit.fit();
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url = `${proto}://${location.host}/api/ws/terminal/${tab.host.id}?cols=${tab.term.cols}&rows=${tab.term.rows}`;
  const scoped = url + (url.includes('?') ? '&' : '?') + `customer=${encodeURIComponent(tab.customerId ?? S.customerId ?? '')}`;
  const ws = new WebSocket(scoped);
  tab.ws = ws;

  ws.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch (_) { return; }
    if (msg.type === 'data') {
      const raw = atob(msg.b64);
      const bytes = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
      tab.term.write(bytes);
    } else if (msg.type === 'ready') {
      tab.state = 'open';
      tab.fingerprint = msg.fingerprint || '';
      tab.target = msg.target || hostTargetText(tab.host);
      tab.protocol = msg.protocol || (tab.host.protocol || 'ssh');
      scheduleFit(tab);
      renderTabs();
      renderSidebar();
      updateStatusbar();
    } else if (msg.type === 'error') {
      if (msg.code === 'host_key_unknown') {
        termPromptHostKey(tab, msg);
        return;
      }
      tab.state = 'closed';
      tab.term.writeln(`\x1b[31m✕ ${msg.message || msg.code}\x1b[0m`);
      toast(`Connection failed: ${msg.code}`, 'err');
      renderTabs();
    } else if (msg.type === 'closed') {
      tab.state = 'closed';
      tab.term.writeln('\r\n\x1b[38;5;244m— session closed —\x1b[0m');
      renderTabs();
    }
  };
  ws.onclose = () => {
    if (tab.state !== 'closed') {
      tab.state = 'closed';
      tab.term.writeln('\r\n\x1b[38;5;244m— connection closed —\x1b[0m');
      renderTabs();
    }
  };
  ws.onerror = () => {};

  tab.onData = tab.term.onData((data) => {
    if (ws.readyState === 1) { ws.send(JSON.stringify({ type: 'input', data })); return; }
    // δεν καταπίνουμε χαρακτήρες: λέμε στον χρήστη γιατί δεν έφυγαν
    inputNotReady(tab);
  });
  tab.onResize = tab.term.onResize(() => sendResize(tab));
  attachTerminalClipboard(tab);
}

let inputNotReadyShown = new WeakSet();

function inputNotReady(tab) {
  if (inputNotReadyShown.has(tab)) return;
  inputNotReadyShown.add(tab);
  tab.term.writeln('\r\n\x1b[33m! not connected — input was not sent\x1b[0m');
  toast('Not connected yet — wait for “Ready”', 'err');
  setTimeout(() => inputNotReadyShown.delete(tab), 4000);
}

// ---------------------------------------------------------------- clipboard
function nativeClip() {
  // pywebview bridge: το native clipboard του παραθύρου, χωρίς browser permissions
  try {
    const api = window.pywebview && window.pywebview.api;
    if (api && typeof api.clip_set === 'function') return api;
  } catch (_) { /* not in the native window */ }
  return null;
}

async function clipWrite(text) {
  try {
    const native = nativeClip();
    if (native) { await native.clip_set(text); return true; }
  } catch (_) { /* fall through to the browser path */ }
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_) {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
    ta.remove();
    return ok;
  }
}

async function clipRead() {
  try {
    const native = nativeClip();
    if (native) {
      const text = await native.clip_get();
      if (typeof text === 'string') return text;
    }
  } catch (_) { /* fall through */ }
  try { return await navigator.clipboard.readText(); } catch (_) { return null; }
}

function terminalCopy(tab) {
  const sel = tab.term.getSelection();
  if (!sel) { toast('Nothing selected'); return; }
  clipWrite(sel).then((ok) => toast(ok ? `Copied ${sel.length} chars` : 'Copy blocked — use Ctrl+Shift+C', ok ? 'ok' : 'err'));
}

async function terminalPaste(tab) {
  const text = await clipRead();
  if (text === null) { toast('Paste blocked — use Ctrl+Shift+V', 'err'); return; }
  if (!text) return;
  if (tab.ws && tab.ws.readyState !== 1) { inputNotReady(tab); return; }
  const lines = text.split(/\r?\n/).filter((l) => l.trim() !== '');
  if (lines.length > 1) {
    // πολυγραμμικό paste: in-app preview — ποτέ native dialog μέσα στο Qt webview
    const ok = await confirmInline(`Send ${lines.length} lines to ${tab.host.label}?`, text.slice(0, 600));
    if (!ok) return;
  }
  try {
    tab.term.paste(text);        // bracketed paste μέσω xterm, μία φορά
  } catch (err) {
    toast(`Paste failed: ${err && err.message ? err.message : err}`, 'err');
  }
  tab.term.focus();
}

function attachTerminalClipboard(tab) {
  tab.term.attachCustomKeyEventHandler((ev) => {
    if (ev.type !== 'keydown') return true;
    const ctrlShift = ev.ctrlKey && ev.shiftKey;
    // Ctrl+Shift+C: το xterm ήδη αντιγράφει την επιλογή· δεν το κόβουμε
    if (ctrlShift && ev.code === 'KeyC') return true;
    // Ctrl+V / Ctrl+Shift+V μένουν στο native paste του xterm (bracketed paste,
    // χωρίς άδειες clipboard). Μόνο το Shift+Insert περνά από εμάς.
    if (ev.shiftKey && ev.code === 'Insert') { terminalPaste(tab); return false; }
    if (ctrlShift && ev.code === 'KeyA') { tab.term.selectAll(); return false; }
    return true;                 // Ctrl+C παραμένει interrupt
  });

  tab.pane.addEventListener('contextmenu', (ev) => {
    ev.preventDefault();
    showTermMenu(ev.clientX, ev.clientY, tab);
  });
}

/* in-app επιβεβαίωση: το window.confirm μπλοκάρει/κλείνει το Qt webview */
function confirmInline(title, detail) {
  return new Promise((resolve) => {
    const wrap = document.createElement('div');
    wrap.className = 'modal-backdrop';
    wrap.innerHTML = `<div class="modal" role="dialog" aria-modal="true">
        <h3>${esc(title)}</h3>
        <pre class="mono modal-pre">${esc(detail)}</pre>
        <div class="modal-actions">
          <button class="btn ghost sm" data-ans="0">Cancel</button>
          <button class="btn primary sm" data-ans="1">Send</button>
        </div>
      </div>`;
    document.body.appendChild(wrap);
    const done = (value) => { wrap.remove(); resolve(value); };
    wrap.addEventListener('click', (ev) => {
      const btn = ev.target.closest('[data-ans]');
      if (btn) done(btn.dataset.ans === '1');
      else if (ev.target === wrap) done(false);
    });
    wrap.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') done(true);
      if (ev.key === 'Escape') done(false);
    });
    const primary = wrap.querySelector('[data-ans="1"]');
    if (primary) primary.focus();
  });
}

let termMenuEl = null;
function showTermMenu(x, y, tab) {
  termMenuEl && termMenuEl.remove();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.innerHTML = `
    <button data-act="copy">Copy selection<span class="kbd">Ctrl+Shift+C</span></button>
    <button data-act="paste">Paste<span class="kbd">Ctrl+Shift+V</span></button>
    <button data-act="all">Select all<span class="kbd">Ctrl+Shift+A</span></button>`;
  menu.style.left = `${x}px`; menu.style.top = `${y}px`;
  document.body.appendChild(menu);
  termMenuEl = menu;
  menu.addEventListener('click', (ev) => {
    const act = ev.target.closest('[data-act]');
    if (!act) return;
    if (act.dataset.act === 'copy') { try { terminalCopy(tab); } catch (err) { toast(String(err), 'err'); } }
    if (act.dataset.act === 'paste') terminalPaste(tab).catch((err) => toast(`Paste failed: ${err}`, 'err'));
    if (act.dataset.act === 'all') tab.term.selectAll();
    menu.remove(); termMenuEl = null;
  });
  setTimeout(() => document.addEventListener('mousedown', function off(e) {
    if (!menu.contains(e.target)) { menu.remove(); termMenuEl = null; document.removeEventListener('mousedown', off); }
  }), 0);
}

function sendResize(tab) {
  if (tab.ws && tab.ws.readyState === 1) {
    tab.ws.send(JSON.stringify({ type: 'resize', cols: tab.term.cols, rows: tab.term.rows }));
  }
  updateStatusbar();
}

function closeTab(id) {
  const index = S.tabs.findIndex((t) => t.id === id);
  if (index < 0) return;
  const tab = S.tabs[index];
  try { tab.onData && tab.onData.dispose(); } catch (_) {}
  try { tab.onResize && tab.onResize.dispose(); } catch (_) {}
  try { tab.observer && tab.observer.disconnect(); } catch (_) {}
  try { tab.ws && tab.ws.close(); } catch (_) {}
  try { tab.term.dispose(); } catch (_) {}
  tab.pane.remove();
  S.tabs.splice(index, 1);
  if (S.activeTabId === id) {
    const next = S.tabs[Math.min(index, S.tabs.length - 1)];
    S.activeTabId = next ? next.id : null;
    if (next) activateTab(next.id); else setView('hosts');
  }
  renderTabs();
  renderSidebar();
}

window.addEventListener('resize', () => {
  const tab = activeTab();
  if (tab) { tab.fit.fit(); sendResize(tab); }
});

/* ------------------------------------------------------------------ pages */
/* Τα JS errors πάνε σε τοπικό αρχείο — αλλιώς «κράσαρε» σημαίνει «δεν ξέρω». */
let clientLogBudget = 20;
function reportClientError(message, source, line, stack) {
  if (clientLogBudget <= 0) return;
  clientLogBudget -= 1;
  try {
    fetch('/api/client-log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-requested-with': 'sshdeck' },
      body: JSON.stringify({ message: String(message || '').slice(0, 400),
        source: String(source || ''), line: Number(line) || 0,
        stack: String(stack || '').slice(0, 800) }),
    }).catch(() => {});
  } catch (_) { /* logging must never break the UI */ }
}
window.addEventListener('error', (ev) => reportClientError(ev.message, ev.filename, ev.lineno, ev.error && ev.error.stack));
window.addEventListener('unhandledrejection', (ev) => {
  const reason = ev.reason || {};
  reportClientError(reason.message || reason, 'promise', 0, reason.stack);
});

function renderPage() {
  renderHero();
  renderKeys();
  renderSnippets();
  renderActivity();
  if (typeof renderRacks === 'function') renderRacks();
  if (S.view === 'racks') { decorateDeviceConnections(); decorateInventoryBackup(); }
  if (S.view === 'diagram' && typeof renderDiagram === 'function') { renderDiagram(); setTimeout(watchDiagramPanel, 60); }
  renderSettings();
  showPage(S.view);
}

/* One canonical rule: exactly one page is visible, and the terminal area only
   belongs to the hosts view. Everything else leads to overlapping pages. */
function showPage(view) {
  const target = 'page-' + view;
  const tabOpen = view === 'hosts' && !!(S.activeTabId && S.tabs.some((t) => t.id === S.activeTabId));
  document.querySelectorAll('.page').forEach((page) => {
    page.classList.toggle('hidden', page.id !== target || tabOpen);
  });
  const app = $('#app');
  if (app) {
    app.classList.toggle('view-hosts', view === 'hosts');
    app.classList.toggle('view-racks', view === 'racks');
    app.classList.toggle('view-diagram', view === 'diagram');
  }
  // terminals belong to the hosts view only; the stage keeps the pages
  const terminals = $('#terminals');
  if (terminals) terminals.classList.toggle('hidden', !tabOpen);
  document.querySelectorAll('.page').forEach((page) => {
    page.style.zIndex = page.id === target ? '5' : '1';
  });
}

function renderHero() {
  const stats = [
    ['Servers', S.hosts.length],
    ['Groups', new Set(S.hosts.map((h) => h.group_name)).size],
    ['Keys', S.creds.length],
    ['Snippets', S.snippets.length],
  ];
  $('#hero-stats').innerHTML = stats.map(([label, value]) =>
    `<div class="stat"><b>${value}</b><span>${label}</span></div>`).join('');
  const recent = S.events.slice(0, 6);
  $('#hero-recent').innerHTML = recent.length
    ? `<h4>Recent activity</h4>` + recent.map((e) => `<div class="event-row">
        <time>${fmtWhen(e.created_at)}</time><span>${esc(e.kind)}</span>
        <b>${esc(e.host_label || e.detail || '')}</b></div>`).join('')
    : '';
}

const KIND_LABEL = {
  login_ok: 'Signed in to panel', login_failed: 'Failed sign-in',
  host_created: 'New connection', host_deleted: 'Delete server',
  credential_created: 'New credential', credential_deleted: 'Delete credential',
  session_open: 'Session opened', session_close: 'Session closed',
  host_key_learned: 'New host key', host_key_forgotten: 'Delete host key',
  ssh_config_imported: 'Import ssh config',
};

function renderKeys() {
  const rows = S.creds.map((c) => `<tr>
    <td><b>${esc(c.name)}</b></td>
    <td><span class="chip">${c.kind === 'key' ? 'private key' : 'password'}</span></td>
    <td class="mono" style="font-size:11px">${esc(c.username || '—')}</td>
    <td class="mono" style="font-size:11px">${esc(c.preview)}</td>
    <td class="mono" style="font-size:11px">${fmtWhen(c.updated_at)}</td>
    <td><div class="actions">
      <button class="icon-btn" data-cred-edit="${c.id}" title="Edit">✎</button>
      <button class="icon-btn" data-cred-del="${c.id}" title="Delete">🗑</button>
    </div></td></tr>`).join('');
  $('#page-keys').innerHTML = `<div class="page-head">
      <div><h2>Keychain</h2><p>Passwords and private keys, encrypted with AES in the vault.</p></div>
      <button class="btn primary" id="key-add">+ New credential</button>
    </div>
    ${S.creds.length ? `<table class="table"><thead><tr>
      <th>Name</th><th>Type</th><th>Username</th><th>Secret</th><th>Updated</th><th></th>
    </tr></thead><tbody>${rows}</tbody></table>`
    : `<div class="empty"><b>No credentials</b>Add a password or private key to connect without prompts.</div>`}`;
}

function renderSnippets() {
  $('#page-snippets').innerHTML = `<div class="page-head">
      <div><h2>Snippets</h2><p>Ready-made commands, one click into the active terminal.</p></div>
      <button class="btn primary" id="snippet-add">+ New snippet</button>
    </div>
    ${S.snippets.length ? `<div class="card-grid">${S.snippets.map((s) => `<div class="card">
        <h3>${esc(s.label)}</h3>
        <div class="body"><pre class="code">${esc(s.command)}</pre></div>
        <div class="actions">
          <button class="btn xs" data-snip-run="${s.id}">▶ Run</button>
          <button class="btn ghost xs" data-snip-edit="${s.id}">Edit</button>
          <button class="btn ghost xs danger" data-snip-del="${s.id}">Delete</button>
        </div></div>`).join('')}</div>`
    : `<div class="empty"><b>No snippets</b>e.g. <span class="mono">docker ps</span> or <span class="mono">journalctl -u nginx -f</span>.</div>`}`;
}

function renderActivity() {
  $('#page-activity').innerHTML = `<div class="page-head">
      <div><h2>History</h2><p>Connections, vault changes and host keys.</p></div>
      <button class="btn ghost" id="events-refresh">⟳ Refresh</button>
    </div>
    <table class="table"><thead><tr><th>When</th><th>Event</th><th>Server</th><th>Detail</th></tr></thead>
    <tbody>${S.events.map((e) => `<tr>
      <td class="mono" style="font-size:11px">${fmtWhen(e.created_at)}</td>
      <td>${esc(KIND_LABEL[e.kind] || e.kind)}</td>
      <td>${esc(e.host_label || '—')}</td>
      <td class="mono" style="font-size:11px;color:var(--muted)">${esc(e.detail || '')}</td>
    </tr>`).join('')}</tbody></table>`;
}

async function downloadInventoryBackup() {
  const res = await api('/inventory/export');
  const blob = new Blob([JSON.stringify(res, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `muxivra-inventory-${new Date().toISOString().slice(0, 10)}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  toast('Backup prepared (without secrets)');
}

async function importInventoryBackup(file) {
  const text = await file.text();
  let payload;
  try { payload = JSON.parse(text); } catch (_) { toast('The file is not valid JSON', 'err'); return; }
  const data = payload.data && payload.data.format ? payload.data : payload;
  let preview;
  try {
    preview = await api('/inventory/import', { json: { data, dry_run: true } });
  } catch (err) { toast(err.message, 'err'); return; }
  const created = preview.created || {};
  const conflicts = (preview.conflicts || []).length;
  confirmDialog(
    'Restore backup',
    `Will be added: ${created.sites || 0} sites, ${created.racks || 0} racks, ` +
    `${created.devices || 0} devices, ${created.ports || 0} ports, ${created.links || 0} links. ` +
    `Existing items are not replaced (${conflicts} matches to existing items).`,
    async () => {
      try {
        const done = await api('/inventory/import', { json: { data, dry_run: false } });
        toast(`Restore: ${done.created.devices || 0} devices, ${done.created.links || 0} links`);
        await loadInventory();
        renderPage();
      } catch (err) { toast(err.message, 'err'); }
    },
    'Restore',
  );
}

function decorateInventoryBackup() {
  const actions = document.querySelector('#page-racks .page-head .actions');
  if (!actions || actions.querySelector('#nd-backup')) return;
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'application/json,.json';
  input.id = 'nd-import-file';
  input.className = 'hidden';
  input.addEventListener('change', () => {
    const file = input.files && input.files[0];
    input.value = '';
    if (file) importInventoryBackup(file);
  });
  actions.appendChild(input);
  const backup = document.createElement('button');
  backup.className = 'btn ghost xs';
  backup.id = 'nd-backup';
  backup.textContent = 'Backup JSON';
  backup.onclick = () => downloadInventoryBackup().catch((err) => toast(err.message, 'err'));
  const restore = document.createElement('button');
  restore.className = 'btn ghost xs';
  restore.id = 'nd-restore';
  restore.textContent = 'Restore';
  restore.onclick = () => input.click();
  actions.appendChild(backup);
  actions.appendChild(restore);
}

function watchDiagramPanel() {
  const panel = document.querySelector('#dg-panel');
  if (!panel || panel.dataset.connWatch) return;
  panel.dataset.connWatch = '1';
  let inFlight = false;
  const decorate = async () => {
    if (inFlight || panel.classList.contains('hidden')) return;
    const deviceId = (typeof DG !== 'undefined' && DG.selected) || null;
    if (!deviceId) return;
    const currentRow = panel.querySelector('.nd-conn-row');
    if (currentRow && currentRow.dataset.forDevice === String(deviceId) && currentRow.isConnected) {
      return;                                   // this render already shows this device
    }
    inFlight = true;
    let payload;
    try {
      payload = await api(`/inventory/devices/${deviceId}/connections`);
    } catch (_) {
      inFlight = false;
      return;
    }
    inFlight = false;
    // the user may have clicked another node while the request was in flight:
    // drop this answer and let the new selection render itself
    if (((typeof DG !== 'undefined' && DG.selected) || null) !== deviceId) {
      setTimeout(decorate, 0);
      return;
    }
    if (panel.classList.contains('hidden')) return;
    panel.querySelectorAll('.nd-conn-row').forEach((el) => el.remove());
    const row = document.createElement('div');
    row.className = 'nd-conn-row';
    row.dataset.forDevice = String(deviceId);
    row.innerHTML = `<span class="hint">Connection</span>${
      (payload.hosts || []).map((h) => `<button class="btn ghost xs" data-nd-open="${h.id}">${esc((h.protocol || 'ssh').toUpperCase())} · ${esc(h.label)}</button>`).join('')
        || '<span class="hint">no profile</span>'
    }${payload.device && payload.device.mgmt_ip ? `<button class="btn ghost xs" data-nd-routeros="${deviceId}">RouterOS client</button>
      <button class="btn ghost xs" data-nd-web="${deviceId}" title="Device web interface in its own window">Web UI</button>` : ''}`;
    const target = panel.querySelector('[data-dg-act="open-rack"]');
    if (target && target.parentElement) target.parentElement.insertBefore(row, target);
  };
  new MutationObserver(() => { decorate(); }).observe(panel, { childList: true, subtree: true });
}

async function decorateDeviceConnections() {
  const layout = (typeof ND !== 'undefined' && ND.layout) || null;
  if (!layout || !layout.devices || !layout.devices.length) return;
  const device = layout.devices.find((d) => d.id === ND.deviceId) || layout.devices[0];
  const panel = document.querySelector('.nd-ports');
  if (!panel || panel.querySelector('.nd-conn-row')) return;
  let payload;
  try {
    payload = await api(`/inventory/devices/${device.id}/connections`);
    ND.connections = ND.connections || {};
    ND.connections[device.id] = payload;
  } catch (err) {
    return;
  }
  const hosts = payload.hosts || [];
  const buttons = hosts.map((h) => `<button class="btn ghost xs" data-nd-open="${h.id}" title="${esc(h.hostname)}:${h.port}">${esc((h.protocol || 'ssh').toUpperCase())} · ${esc(h.label)}</button>`).join('');
  const routeros_client = device.mgmt_ip
    ? `<button class="btn ghost xs" data-nd-routeros="${device.id}">RouterOS client${payload.routeros_client && payload.routeros_client.available ? '' : ' (missing)'}</button>`
    : '';
  const row = document.createElement('div');
  row.className = 'nd-conn-row';
  row.dataset.deviceId = device.id;
  row.innerHTML = `<span class="hint">Device connections</span>${
    buttons || '<span class="hint">no profile</span>'
  }<button class="btn ghost xs" data-nd-add-profile="${device.id}" title="Add profile">+ profile</button>${
    hosts.length ? `<button class="btn ghost xs" data-nd-drop-profile="${device.id}" title="Remove all profiles">× all</button>` : ''
  }${routeros_client}<button class="btn ghost xs" data-nd-web="${device.id}" title="Web management in a separate window, without a browser">Web</button>`;
  panel.insertBefore(row, panel.querySelector('.nd-port-grid'));
}

async function renderVaultState() {
  const state = await api('/vault');
  S.vault = state;
  const label = $('#vault-state');
  if (label) {
    label.textContent = state.locked
      ? 'Status: locked — secrets cannot be read'
      : 'Status: unlocked';
  }
  const input = $('#vault-autolock');
  if (input) input.value = Math.round((state.auto_lock_seconds || 0) / 60);
}

function renderSettings() {
  const status = S.status || {};
  const upd = S.update || {};
  const tab = activeTab();
  $('#page-settings').innerHTML = `<div class="page-head">
      <div><h2>Settings</h2><p>Account, vault and host keys.</p></div>
    </div>
    <div class="card-grid">
      <div class="card"><h3>Account</h3>
        <div class="sub">${esc(status.username || 'admin')} · v${esc(status.version || '')}</div>
        <div class="body">Change the master password. All active sessions will be closed.</div>
        <div class="actions"><button class="btn xs" id="pw-change">Change password</button>
        <button class="btn ghost xs" id="logout2">Sign out</button></div></div>
      <div class="card"><h3>Host keys (TOFU)</h3>
        <div class="body">The first host key is stored and checked on every connection.
        If it changes, the connection is rejected.</div>
        <div class="actions">${tab ? `<button class="btn ghost xs" id="forget-key">Delete key for ${esc(tab.host.label)}</button>`
          : '<span class="muted" style="font-size:12px">Open a session to see the fingerprint.</span>'}</div></div>
      <div class="card"><h3>Import</h3>
        <div class="body">Read hosts from the server's <span class="mono">~/.ssh/config</span> or paste the contents.</div>
        <div class="actions"><button class="btn ghost xs" id="settings-import">Import ssh config</button></div></div>
      <div class="card"><h3>Updates</h3>
        <div class="sub">v${esc(upd.current || S.status?.version || '')} · build ${esc((S.status && S.status.build) || 'dev')} · ${esc((S.status && S.status.mode) || 'source')}</div>
        <div class="body mono" style="font-size:11.5px;word-break:break-all">data: ${esc((S.status && S.status.data_dir) || '')}</div>
        <div class="body">
          <p class="hint">Official source: <b class="mono">${esc(upd.repo || 'StefAa1/MUXIVRA')}</b>
            ${upd.token ? ' · private repo token saved' : ''}</p>
          <p class="hint" id="upd-status">${upd.last_check ? 'last check: ' + esc(upd.last_check) : 'no checks yet'}</p>
          <div class="actions">
            <button class="btn xs" id="upd-check">Check for updates</button>
            <button class="btn primary xs${upd.asset ? '' : ' hidden'}" id="upd-prepare">Update now</button>
          </div>
        </div></div>
      <div class="card"><h3>Vault</h3>
        <div class="sub" id="vault-status">${esc((upd.vault_key || {}).mechanism || 'file-0600')}</div>
        <div class="body">Credentials are encrypted (Fernet). ${(upd.vault_key || {}).protected
          ? 'The key is bound to your Windows account (DPAPI) — a copy on another PC cannot open it.'
          : 'The key is protected by file permissions 0600.'}</div>
        <div class="body" id="vault-state">—</div>
        <div class="nd-form">
          <label class="field" style="flex:1"><span>Auto-lock (minutes)</span>
            <input id="vault-autolock" type="number" min="0" max="1440" step="1" value="0" /></label>
          <button class="btn xs" id="vault-save-lock">Save</button>
        </div>
        <div class="actions">
          <button class="btn ghost xs" id="vault-lock">Lock</button>
          <button class="btn primary xs" id="vault-unlock">Unlock</button>
        </div>
      </div>
    </div>`;

  if (S.view === 'settings') renderVaultState().catch(() => {});
  const ev = $('#events-refresh');
  if (ev) ev.onclick = async () => { await loadEvents(); renderPage(); toast('Refreshed'); };
}

/* --------------------------------------------------------------- host form */
function openModal(html) {
  $('#modal').innerHTML = html;
  $('#modal-backdrop').classList.remove('hidden');
}
function closeModal() { $('#modal-backdrop').classList.add('hidden'); $('#modal').innerHTML = ''; }

function credOptions(selected) {
  const opts = S.creds.map((c) => `<option value="${c.id}"${c.id === selected ? ' selected' : ''}>${esc(c.name)} (${c.kind === 'key' ? 'key' : 'password'})</option>`).join('');
  return `<option value="">— no credential (ssh-agent) —</option>${opts}`;
}

function openHostModal(host) {
  const h = host || {
    label: '', protocol: 'ssh', hostname: '', port: 22, username: 'root', credential_id: null,
    group_name: 'General', tags: [], color: '#2f7bff', notes: '',
    serial_port: '', baud: 115200, bytesize: 8, parity: 'N', stopbits: 1, flow: 'none',
  };
  const proto = h.protocol || 'ssh';
  openModal(`<h3>${host ? 'Edit server' : 'New connection'}</h3>
    <p class="lead">SSH, Telnet or serial port — same terminal, same inventory.</p>
    <form id="host-form">
      <div class="proto-switch" id="proto-switch">
        <button type="button" data-proto="ssh">SSH</button>
        <button type="button" data-proto="telnet">Telnet</button>
        <button type="button" data-proto="serial">Serial / COM</button>
      </div>
      <input type="hidden" name="protocol" value="${esc(proto)}" />
      <div class="row">
        <label class="field"><span>Name</span><input name="label" required value="${esc(h.label)}" placeholder="web-01" /></label>
        <label class="field" data-pane="net"><span>Hostname / IP</span><input name="hostname" value="${esc(h.hostname)}" placeholder="10.10.10.250" /></label>
      </div>
      <div class="row net" data-pane="net">
        <label class="field"><span>Port</span><input name="port" type="number" min="1" max="65535" value="${h.port}" /></label>
        <label class="field"><span>Username</span><input name="username" value="${esc(h.username)}" placeholder="root" /></label>
        <label class="field" data-pane="ssh"><span>Credential <button type="button" class="mini-add" id="cred-new" title="New credential">+ New</button></span><select name="credential_id">${credOptions(h.credential_id)}</select></label>
      </div>
      <div class="row serial" data-pane="serial">
        <label class="field"><span>Port</span><input name="serial_port" list="serial-port-list" value="${esc(h.serial_port || '')}" placeholder="COM3 or /dev/ttyUSB0" autocomplete="off" /></label>
        <label class="field"><span>Baud</span><input name="baud" type="number" min="50" step="1" value="${h.baud || 115200}" /></label>
        <label class="field"><span>Bits</span><select name="bytesize">
          ${[8, 7, 6, 5].map((b) => `<option value="${b}"${Number(h.bytesize) === b ? ' selected' : ''}>${b}</option>`).join('')}
        </select></label>
      </div>
      <div class="row three" data-pane="serial">
        <label class="field"><span>Parity</span><select name="parity">
          ${[['N', 'None'], ['E', 'Even'], ['O', 'Odd'], ['M', 'Mark'], ['S', 'Space']].map(([v, l]) =>
            `<option value="${v}"${(h.parity || 'N') === v ? ' selected' : ''}>${l}</option>`).join('')}
        </select></label>
        <label class="field"><span>Flow control</span><select name="flow">
          ${[["none","none"],["rtscts","RTS/CTS"],["xonxoff","XON/XOFF"],["dsrdtr","DSR/DTR"]]
            .map(([val, label]) => `<option value="${val}"${(h.flow || 'none') === val ? ' selected' : ''}>${label}</option>`).join('')}
        </select></label>
        <label class="field"><span>Stop bits</span><select name="stopbits">
          ${[1, 1.5, 2].map((v) => `<option value="${v}"${Number(h.stopbits) === v ? ' selected' : ''}>${v}</option>`).join('')}
        </select></label>
      </div>
      <p class="hint" id="serial-hint" data-pane="serial">Checking ports…</p>
      <datalist id="serial-port-list"></datalist>
      <div class="row">
        <label class="field"><span>Group</span><input name="group_name" value="${esc(h.group_name)}" placeholder="General" /></label>
        <label class="field"><span>Tags (comma-separated)</span><input name="tags" value="${esc((h.tags || []).join(', '))}" placeholder="prod, nginx" /></label>
      </div>
      <label class="field"><span>Notes</span><textarea name="notes" rows="2">${esc(h.notes)}</textarea></label>
      <p class="error-text" id="host-error"></p>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-close>Cancel</button>
        <button type="submit" class="btn primary">${host ? 'Save' : 'Create'}</button>
      </div>
    </form>`);

  const form = $('#host-form');
  const setProto = (next) => {
    form.querySelector('[name=protocol]').value = next;
    $$('#proto-switch button').forEach((b) => b.classList.toggle('on', b.dataset.proto === next));
    $$('[data-pane]').forEach((el) => {
      const pane = el.dataset.pane;
      const visible = pane === 'net' ? next !== 'serial' : pane === 'ssh' ? next === 'ssh' : next === 'serial';
      el.classList.toggle('hidden', !visible);
    });
    const portField = form.querySelector('[name=port]');
    if (next === 'telnet' && ['22', ''].includes(portField.value)) portField.value = '23';
    if (next === 'ssh' && ['23', ''].includes(portField.value)) portField.value = '22';
    if (next === 'serial') form.querySelector('[name=serial_port]').focus();
  };
  $$('#proto-switch button').forEach((b) => { b.onclick = () => setProto(b.dataset.proto); });
  setProto(proto);

  const draftFromForm = () => {
    const d = Object.fromEntries(new FormData(form).entries());
    return {
      ...h,
      label: d.label || '',
      protocol: d.protocol,
      hostname: d.hostname || '',
      port: Number(d.port) || (d.protocol === 'telnet' ? 23 : 22),
      username: d.username || '',
      credential_id: d.credential_id ? Number(d.credential_id) : null,
      group_name: d.group_name || 'General',
      tags: (d.tags || '').split(',').map((s) => s.trim()).filter(Boolean),
      notes: d.notes || '',
      serial_port: d.serial_port || '',
      baud: Number(d.baud) || 115200,
      bytesize: Number(d.bytesize) || 8,
      parity: d.parity || 'N',
      stopbits: Number(d.stopbits) || 1,
      flow: d.flow || 'none',
    };
  };

  const credNew = $('#cred-new');
  if (credNew) {
    credNew.onclick = () => {
      const draft = draftFromForm();
      openCredModal(null, (created) => {
        draft.credential_id = created.id;
        openHostModal(draft);
        setProto(draft.protocol);
      });
    };
  }

  api('/serial/ports').then((data) => {
    const list = $('#serial-port-list');
    const hint = $('#serial-hint');
    if (!list) return;
    list.innerHTML = (data.ports || []).map((p) =>
      `<option value="${esc(p.device)}">${esc(p.description || p.device)}</option>`).join('');
    if (hint) {
      hint.textContent = (data.ports || []).length
        ? `${data.ports.length} port(s): ${data.ports.slice(0, 3).map((p) => p.device).join(', ')}`
        : 'No serial ports';
    }
  }).catch(() => {
    const hint = $('#serial-hint');
    if (hint) hint.textContent = 'Could not read ports';
  });

  form.onsubmit = async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target).entries());
    const payload = {
      label: data.label.trim(),
      protocol: data.protocol,
      hostname: (data.hostname || '').trim(),
      port: Number(data.port) || (data.protocol === 'telnet' ? 23 : 22),
      username: (data.username || '').trim(),
      credential_id: data.credential_id ? Number(data.credential_id) : null,
      group_name: data.group_name.trim() || 'General',
      tags: data.tags,
      color: h.color || '#2f7bff',
      notes: data.notes,
      serial_port: (data.serial_port || '').trim(),
      baud: Number(data.baud) || 115200,
      bytesize: Number(data.bytesize) || 8,
      parity: data.parity || 'N',
      stopbits: Number(data.stopbits) || 1,
      flow: data.flow || 'none',
    };
    try {
      if (host) await api(`/hosts/${host.id}`, { method: 'PUT', json: payload });
      else await api('/hosts', { json: payload });
      closeModal();
      await loadHosts();
      renderSidebar();
      renderPage();
      toast(host ? 'Connection updated' : 'Connection added');
    } catch (err) {
      const map = {
        hostname_required: 'A hostname/IP is required.',
        serial_port_required: 'A serial port is required (e.g. COM3).',
        unsupported_protocol: 'Unsupported protocol.',
      };
      $('#host-error').textContent = map[err.message] || err.message;
    }
  };
}

function openCredModal(cred, onSaved) {
  const c = cred || { name: '', kind: 'password', username: '', preview: '' };
  openModal(`<h3>${cred ? 'Edit credential' : 'New credential'}</h3>
    <p class="lead">Encrypted with Fernet (AES-128-CBC + HMAC) and kept on the server disk.</p>
    <form id="cred-form">
      <div class="row">
        <label class="field"><span>Name</span><input name="name" required value="${esc(c.name)}" placeholder="prod-root" /></label>
        <label class="field"><span>Type</span><select name="kind">
          <option value="password"${c.kind === 'password' ? ' selected' : ''}>Password</option>
          <option value="key"${c.kind === 'key' ? ' selected' : ''}>Private key</option>
        </select></label>
      </div>
      <label class="field"><span>Username (default)</span><input name="username" value="${esc(c.username || '')}" placeholder="root" /></label>
      <label class="field"><span>Secret ${cred ? '(blank = unchanged)' : ''}</span>
        <textarea name="secret" rows="5" placeholder="${cred ? '•••••• (unchanged)' : 'password or -----BEGIN OPENSSH PRIVATE KEY-----'}" spellcheck="false"></textarea></label>
      <label class="field"><span>Key passphrase (optional)</span><input name="passphrase" type="password" autocomplete="new-password" /></label>
      <p class="error-text" id="cred-error"></p>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-close>Cancel</button>
        <button type="submit" class="btn primary">${cred ? 'Save' : 'Create'}</button>
      </div>
    </form>`);
  $('#cred-form').onsubmit = async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target).entries());
    try {
      const payload = { name: data.name.trim(), kind: data.kind, username: data.username.trim(), secret: data.secret.trim(), passphrase: data.passphrase };
      const saved = cred
        ? await api(`/credentials/${cred.id}`, { method: 'PUT', json: payload })
        : await api('/credentials', { json: payload });
      closeModal();
      await loadCreds();
      renderPage();
      renderSidebar();
      toast('Credential saved');
      if (onSaved) onSaved(saved);
    } catch (err) {
      $('#cred-error').textContent = err.message === 'not_a_private_key' ? 'That does not look like a private key.' : err.message;
    }
  };
}

function openSnippetModal(snippet) {
  const s = snippet || { label: '', command: '' };
  openModal(`<h3>${snippet ? 'Edit snippet' : 'New snippet'}</h3>
    <form id="snip-form">
      <label class="field"><span>Name</span><input name="label" required value="${esc(s.label)}" placeholder="Disk usage" /></label>
      <label class="field"><span>Command</span><textarea name="command" rows="4" required spellcheck="false">${esc(s.command)}</textarea></label>
      <p class="error-text" id="snip-error"></p>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-close>Cancel</button>
        <button type="submit" class="btn primary">Save</button>
      </div></form>`);
  $('#snip-form').onsubmit = async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target).entries());
    try {
      if (snippet) await api(`/snippets/${snippet.id}`, { method: 'PUT', json: data });
      else await api('/snippets', { json: data });
      closeModal();
      await loadSnippets();
      renderPage();
      toast('Snippet saved');
    } catch (err) { $('#snip-error').textContent = err.message; }
  };
}

function openImportModal() {
  openModal(`<h3>Import from ssh config</h3>
    <p class="lead">Read <span class="mono">~/.ssh/config</span> from this machine or paste the contents.</p>
    <form id="import-form">
      <label class="field"><span>Path on the server</span><input name="path" value="/root/.ssh/config" /></label>
      <label class="field"><span>or contents</span><textarea name="text" rows="5" placeholder="Host web-01&#10;  HostName 10.0.0.5&#10;  User root" spellcheck="false"></textarea></label>
      <div class="row">
        <label class="field"><span>Credential for all</span><select name="credential_id">${credOptions(null)}</select></label>
        <label class="field"><span>Group</span><input name="group_name" value="Imported" /></label>
      </div>
      <p class="error-text" id="import-error"></p>
      <div class="modal-actions"><button type="button" class="btn ghost" data-close>Cancel</button>
      <button type="submit" class="btn primary">Import</button></div></form>`);
  $('#import-form').onsubmit = async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target).entries());
    try {
      const result = await api('/import/ssh-config', { json: {
        path: data.path.trim(), text: data.text, group_name: data.group_name || 'Imported',
        credential_id: data.credential_id ? Number(data.credential_id) : null,
      } });
      closeModal();
      await loadHosts();
      renderSidebar();
      renderPage();
      toast(`Import: ${result.imported} hosts (${result.skipped} already existed)`);
    } catch (err) {
      $('#import-error').textContent = err.message === 'config_unreadable' ? 'The file cannot be read.' : err.message;
    }
  };
}

/* First contact with a host key: the user must see and approve the fingerprint. */
function termPromptHostKey(tab, frame) {
  tab.state = 'closed';
  const host = tab.host;
  const port = host.port || 22;
  tab.term.writeln('\r\n\x1b[33m? Unknown host key — connection was not established.\x1b[0m');
  tab.term.writeln(`  host: ${frame.hostname || host.hostname}:${port}`);
  tab.term.writeln(`  type: ${frame.key_type || 'ssh'}  fingerprint: ${frame.fingerprint}`);
  tab.term.writeln('  Confirm in the dialog to add it to your trusted keys.');
  renderTabs();

  openModal(`<h3>New host key</h3>
    <p class="lead">This server is not known. Check the fingerprint before trusting it.</p>
    <dl class="kv">
      <dt>Host</dt><dd class="mono">${esc(frame.hostname || host.hostname)}:${esc(port)}</dd>
      <dt>Type</dt><dd class="mono">${esc(frame.key_type || 'ssh')}</dd>
      <dt>Fingerprint</dt><dd class="mono" id="hk-fingerprint">${esc(frame.fingerprint || '')}</dd>
    </dl>
    <p class="hint">Compare the fingerprint with the one on the server (e.g. <span class="mono">ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub</span>).</p>
    <p class="error-text" id="hk-error"></p>
    <div class="modal-actions">
      <button class="btn ghost" data-close>Cancel</button>
      <button class="btn primary" id="hk-trust">Accept and retry</button>
    </div>`);
  $('#hk-trust').onclick = async () => {
    try {
      await api(`/hosts/${host.id}/trust`, { json: { fingerprint: frame.fingerprint } });
      closeModal();
      toast('Host key added to trusted keys');
      closeTab(tab.id);
      openTerminal(host);
    } catch (err) {
      $('#hk-error').textContent = err.message === 'no_pending_key' || err.message.startsWith('no_pending_key')
        ? 'The confirmation expired — try connecting again.'
        : err.message;
    }
  };
}

function confirmDialog(title, text, onConfirm, confirmLabel) {
  openModal(`<h3>${esc(title)}</h3><p class="lead">${esc(text)}</p>
    <div class="modal-actions"><button class="btn ghost" data-close>Cancel</button>
    <button class="btn danger" id="confirm-ok">${esc(confirmLabel || 'Delete')}</button></div>`);
  $('#confirm-ok').onclick = async () => { closeModal(); await onConfirm(); };
}

/* ------------------------------------------------------------------- SFTP */
function openDrawer() {
  const tab = activeTab();
  const host = tab ? tab.host : S.hosts[0];
  if (!host) { toast('Add a server first', 'err'); return; }
  if ((host.protocol || 'ssh') !== 'ssh') {
    toast('SFTP is only supported for SSH connections', 'err');
    return;
  }
  S.sftp.hostId = host.id;
  S.sftp.path = '.';
  $('#sftp-host').textContent = hostTargetText(host);
  $('#drawer').classList.add('open');
  $('#app').classList.add('drawer-open');
  $('#sftp-path').value = '.';
  loadFiles('.');
  setTimeout(refitActive, 220);
}

function closeDrawer() {
  $('#drawer').classList.remove('open');
  $('#app').classList.remove('drawer-open');
  setTimeout(refitActive, 220);
}

async function loadFiles(path) {
  const hostId = S.sftp.hostId;
  if (!hostId) return;
  $('#sftp-list').innerHTML = '<div class="empty" style="margin:12px;padding:18px">Loading…</div>';
  try {
    const data = await api(`/hosts/${hostId}/files?path=${encodeURIComponent(path || '.')}`);
    S.sftp.path = data.cwd;
    $('#sftp-path').value = data.cwd;
    const rows = [];
    if (data.cwd !== '/') rows.push(`<div class="frow dir" data-path="${esc(parentPath(data.cwd))}"><span class="ic">↩</span><span class="nm">..</span></div>`);
    data.entries.forEach((e) => {
      const icon = e.is_dir ? '▸' : '·';
      rows.push(`<div class="frow${e.is_dir ? ' dir' : ''}" data-path="${esc(e.path)}" data-dir="${e.is_dir}">
        <span class="ic">${icon}</span><span class="nm">${esc(e.name)}</span>
        <span class="sz">${e.is_dir ? 'dir' : fmtSize(e.size)}</span>
        ${e.is_dir ? '' : `<span class="fa" data-dl="${esc(e.path)}" title="Download">⇩</span>`}
        <span class="fa" data-rm="${esc(e.path)}" data-isdir="${e.is_dir}" title="Delete">✕</span>
      </div>`);
    });
    $('#sftp-list').innerHTML = rows.join('') || '<div class="empty" style="margin:12px">Empty folder</div>';
  } catch (err) {
    $('#sftp-list').innerHTML = `<div class="empty" style="margin:12px;padding:18px"><b>Error</b>${esc(err.message)}</div>`;
  }
}

function parentPath(path) {
  const parts = path.split('/').filter(Boolean);
  parts.pop();
  return '/' + parts.join('/');
}

/* ---------------------------------------------------------------- palette */
function openPalette() {
  $('#palette-backdrop').classList.remove('hidden');
  const input = $('#palette-input');
  input.value = '';
  S.paletteIndex = 0;
  renderPalette('');
  setTimeout(() => input.focus(), 40);
}
function closePalette() { $('#palette-backdrop').classList.add('hidden'); }

function renderPalette(query) {
  const q = query.trim().toLowerCase();
  S.paletteItems = S.hosts.filter((h) => !q || [h.label, h.hostname, h.username, (h.tags || []).join(' ')]
    .join(' ').toLowerCase().includes(q)).slice(0, 12);
  if (S.paletteIndex >= S.paletteItems.length) S.paletteIndex = 0;
  $('#palette-list').innerHTML = S.paletteItems.map((h, i) => `<div class="pal-item${i === S.paletteIndex ? ' sel' : ''}" data-pal="${h.id}">
      <span class="dot ok"></span><span>${esc(h.label)}</span>
      <span class="h">${esc(h.username)}@${esc(h.hostname)}:${h.port}</span></div>`).join('')
    || '<div class="pal-item"><span class="muted">No servers — add one first.</span></div>';
}

function connectFromPalette() {
  const host = S.paletteItems[S.paletteIndex];
  if (!host) return;
  closePalette();
  openTerminal(host);
}

/* ----------------------------------------------------------------- events */
document.addEventListener('click', async (event) => {
  const ndOpen = event.target.closest('[data-nd-open]');
  if (ndOpen) {
    const host = (S.hosts || []).find((h) => h.id === Number(ndOpen.dataset.ndOpen));
    if (host) openTerminal(host); else toast('Profile not found', 'err');
    return;
  }

  const ndLaunch = event.target.closest('[data-nd-routeros]');
  if (ndLaunch) {
    try {
      const deviceId = Number(ndLaunch.dataset.ndRouteros);
      const device = ((ND.layout || {}).devices || []).find((d) => d.id === deviceId) || {};
      const res = await api(`/inventory/devices/${deviceId}/routeros-client`, { json: { address: device.mgmt_ip || '' } });
      toast(`RouterOS client: pid ${res.pid || '—'}`);
    } catch (err) {
      toast(err.message.startsWith('routeros_client') ? 'RouterOS client is not installed' : err.message, 'err');
    }
    return;
  }

  const ndWeb = event.target.closest('[data-nd-web]');
  if (ndWeb) {
    const deviceId = Number(ndWeb.dataset.ndWeb);
    try {
      const res = await api(`/inventory/devices/${deviceId}/web`, { method: 'POST', json: {} });
      if (res.mode === 'window') toast(`Web management: ${res.url} — separate window`);
      else {
        toast(res.warning, 'err');
        if (res.url) window.open(res.url, '_blank', 'noopener');
      }
    } catch (err) {
      toast(err.detail === 'no_address'
        ? 'This device has no management IP'
        : `Web management did not open: ${err.message}`, 'err');
    }
    return;
  }

  const ndAddProfile = event.target.closest('[data-nd-add-profile]');
  if (ndAddProfile) {
    const deviceId = Number(ndAddProfile.dataset.ndAddProfile);
    const options = (S.hosts || []).map((h) => `<option value="${h.id}">${esc(h.label)} · ${esc(h.hostname || '')}</option>`).join('');
    openModal(`<h3>Add profile</h3>
      <p class="lead">The device ⇄ host relation is explicit: only what you add here is shown as a connection.</p>
      <label class="field"><span>Host</span><select id="ndp-host">${options || '<option value="">— no host —</option>'}</select></label>
      <p class="error-text" id="ndp-error"></p>
      <div class="modal-actions"><button class="btn ghost" data-close>Cancel</button>
      <button class="btn primary" id="ndp-save">Add</button></div>`);
    $('#ndp-save').onclick = async () => {
      const hostId = Number($('#ndp-host').value || 0);
      if (!hostId) { $('#ndp-error').textContent = 'No host available to connect.'; return; }
      try {
        await api(`/inventory/devices/${deviceId}/profiles`, { json: { host_id: hostId } });
        closeModal();
        await loadInventory();
        renderPage();
        toast('Profile added');
      } catch (err) { $('#ndp-error').textContent = err.message; }
    };
    return;
  }

  const ndDropProfile = event.target.closest('[data-nd-drop-profile]');
  if (ndDropProfile) {
    const deviceId = Number(ndDropProfile.dataset.ndDropProfile);
    const payload = await api(`/inventory/devices/${deviceId}/connections`);
    try {
      for (const h of payload.hosts) {
        await api(`/inventory/devices/${deviceId}/profiles/${h.id}`, { method: 'DELETE' });
      }
      await loadInventory();
      renderPage();
      toast('Profiles removed');
    } catch (err) { toast(err.message, 'err'); }
    return;
  }

  const ndTab = event.target.closest('[data-nd-tab]');
  if (ndTab) {
    ND.tab = ndTab.dataset.ndTab;
    if (ND.tab === 'audit') { try { await ndAudit(); } catch (err) { toast(err.message, 'err'); } }
    renderPage();
    return;
  }

  const ndSel = event.target.closest('[data-nd-select]');
  if (ndSel) { ND.deviceId = Number(ndSel.dataset.ndSelect); renderPage(); return; }

  const ndPort = event.target.closest('[data-nd-port]');
  if (ndPort) { await ndQuickPort(Number(ndPort.dataset.ndPort), ndPort.dataset.ndPortLabel || ''); return; }

  if (event.target.closest('#nd-quick-cancel')) { ND.quick = null; renderPage(); return; }

  const ndRack = event.target.closest('[data-nd-rack]');
  if (ndRack) { await ndSelectRack(Number(ndRack.dataset.ndRack)); return; }

  const ndPorts = event.target.closest('[data-nd-ports]');
  if (ndPorts) {
    const deviceId = Number(ndPorts.dataset.ndPorts);
    try {
      const created = await api('/inventory/ports/bulk', { json: { device_id: deviceId, prefix: 'eth', count: 24 } });
      await loadInventory();
      renderPage();
      toast(`${created.created.length} ports added`);
    } catch (err) { toast(err.message, 'err'); }
    return;
  }

  const closeBtn = event.target.closest('[data-close]');
  if (closeBtn) { closeModal(); return; }

  const webAct = event.target.closest('[data-act="web"]');
  if (webAct) {
    event.stopPropagation();
    try {
      const res = await api(`/hosts/${Number(webAct.dataset.id)}/web`, { method: 'POST', json: {} });
      if (res.mode === 'window') toast(`Web UI: ${res.url}`);
      else {
        toast(res.warning, 'err');
        if (res.url) window.open(res.url, '_blank', 'noopener');
      }
    } catch (err) {
      toast(err.detail === 'no_address' ? 'This device has no address yet'
        : `Web UI did not open: ${err.message}`, 'err');
    }
    return;
  }

  const hostRow = event.target.closest('.host-row');
  if (hostRow) {
    const id = Number(hostRow.dataset.host);
    const host = S.hosts.find((h) => h.id === id);
    const act = event.target.closest('[data-act]');
    if (act && act.dataset.act === 'edit') { event.stopPropagation(); openHostModal(host); return; }
    if (act && act.dataset.act === 'del') {
      event.stopPropagation();
      confirmDialog('Delete server', `"${host.label}" will be removed from the inventory.`, async () => {
        await api(`/hosts/${id}`, { method: 'DELETE' });
        await loadHosts(); renderSidebar(); renderPage(); toast('Server deleted');
      });
      return;
    }
    if (host) openTerminal(host);
    return;
  }

  const groupHead = event.target.closest('.group-head');
  if (groupHead) {
    const group = groupHead.dataset.group;
    if (S.collapsed.has(group)) S.collapsed.delete(group); else S.collapsed.add(group);
    renderSidebar();
    return;
  }

  const tabEl = event.target.closest('[data-tab]');
  if (tabEl && !event.target.closest('[data-tab-close]')) { activateTab(tabEl.dataset.tab); return; }
  const closeTabEl = event.target.closest('[data-tab-close]');
  if (closeTabEl) { closeTab(closeTabEl.dataset.tabClose); return; }

  const railBtn = event.target.closest('.rail-btn[data-view]');
  if (railBtn) { setView(railBtn.dataset.view); return; }

  const credEdit = event.target.closest('[data-cred-edit]');
  if (credEdit) { openCredModal(S.creds.find((c) => c.id === Number(credEdit.dataset.credEdit))); return; }
  const credDel = event.target.closest('[data-cred-del]');
  if (credDel) {
    const cred = S.creds.find((c) => c.id === Number(credDel.dataset.credDel));
    confirmDialog('Delete credential', `Delete "${cred.name}"?`, async () => {
      try {
        await api(`/credentials/${cred.id}`, { method: 'DELETE' });
        await loadCreds(); renderPage(); toast('Credential deleted');
      } catch (err) { toast(err.message, 'err'); }
    });
    return;
  }

  const snipRun = event.target.closest('[data-snip-run]');
  if (snipRun) {
    const snippet = S.snippets.find((s) => s.id === Number(snipRun.dataset.snipRun));
    runSnippet(snippet);
    return;
  }
  const snipEdit = event.target.closest('[data-snip-edit]');
  if (snipEdit) { openSnippetModal(S.snippets.find((s) => s.id === Number(snipEdit.dataset.snipEdit))); return; }
  const snipDel = event.target.closest('[data-snip-del]');
  if (snipDel) {
    const snippet = S.snippets.find((s) => s.id === Number(snipDel.dataset.snipDel));
    confirmDialog('Delete snippet', `Delete "${snippet.label}"?`, async () => {
      await api(`/snippets/${snippet.id}`, { method: 'DELETE' });
      await loadSnippets(); renderPage(); toast('Deleted');
    });
    return;
  }

  const palItem = event.target.closest('[data-pal]');
  if (palItem) {
    const host = S.hosts.find((h) => h.id === Number(palItem.dataset.pal));
    closePalette();
    if (host) openTerminal(host);
    return;
  }

  const dropItem = event.target.closest('.frow');
  if (dropItem && !event.target.dataset.dl && !event.target.dataset.rm) {
    const path = dropItem.dataset.path;
    if (dropItem.dataset.dir === 'true') loadFiles(path);
    return;
  }
  const dl = event.target.closest('[data-dl]');
  if (dl) {
    const path = dl.dataset.dl;
    const url = `/api/hosts/${S.sftp.hostId}/download?path=${encodeURIComponent(path)}`;
    const res = await fetch(url, { credentials: 'same-origin' });
    if (!res.ok) { toast('Download failed', 'err'); return; }
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = path.split('/').pop();
    a.click();
    URL.revokeObjectURL(a.href);
    toast('Download: ' + a.download);
    return;
  }
  const rm = event.target.closest('[data-rm]');
  if (rm) {
    event.stopPropagation();
    const path = rm.dataset.rm;
    confirmDialog('Delete', `Delete ${path}?`, async () => {
      try {
        await api(`/hosts/${S.sftp.hostId}/delete-path`, { json: { path } });
        await loadFiles(S.sftp.path);
        toast('Deleted');
      } catch (err) { toast(err.message, 'err'); }
    });
    return;
  }

  if (event.target.closest('#modal-backdrop') && event.target.id === 'modal-backdrop') closeModal();
  if (!$('#palette-backdrop').classList.contains('hidden') && !event.target.closest('.palette')) closePalette();
});

function runSnippet(snippet) {
  const tab = activeTab();
  if (!tab || tab.state !== 'open') { toast('Open a session first', 'err'); return; }
  tab.ws.send(JSON.stringify({ type: 'input', data: snippet.command + '\r' }));
  toast(`▶ ${snippet.label}`);
}

document.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    if (event.target && event.target.closest && event.target.closest('.xterm')) return;  // terminal κρατά το Ctrl+K
    event.preventDefault(); openPalette(); return;
  }
  if (event.key === 'Escape') { closeModal(); closePalette(); closeDrawer(); return; }
  if (!$('#palette-backdrop').classList.contains('hidden')) {
    if (event.key === 'ArrowDown') { event.preventDefault(); S.paletteIndex += 1; renderPalette($('#palette-input').value); }
    if (event.key === 'ArrowUp') { event.preventDefault(); S.paletteIndex = Math.max(0, S.paletteIndex - 1); renderPalette($('#palette-input').value); }
    if (event.key === 'Enter') { event.preventDefault(); connectFromPalette(); }
  }
});

/* --------------------------------------------------------------- wiring up */
$('#auth-form').addEventListener('submit', handleAuthSubmit);
$('#search').addEventListener('input', (event) => { S.search = event.target.value; renderSidebar(); });
$('#btn-add-host').onclick = () => openHostModal(null);
$('#hero-add').onclick = () => openHostModal(null);
$('#btn-import').onclick = openImportModal;
$('#hero-import').onclick = openImportModal;
$('#hero-palette').onclick = openPalette;
$('#tab-add').onclick = openPalette;
$('#btn-sftp').onclick = openDrawer;
$('#btn-files-inline').onclick = openDrawer;
$('#sftp-close').onclick = closeDrawer;
$('#sftp-up').onclick = () => loadFiles(parentPath(S.sftp.path));
$('#sftp-refresh').onclick = () => loadFiles(S.sftp.path);
$('#sftp-path').addEventListener('keydown', (event) => { if (event.key === 'Enter') loadFiles(event.target.value.trim() || '.'); });
$('#btn-snippets-inline').onclick = () => { setView('snippets'); };
$('#btn-logout').onclick = async () => {
  await api('/logout', { json: {} });
  S.tabs.forEach((t) => { try { t.ws && t.ws.close(); } catch (_) {} });
  S.tabs = []; S.activeTabId = null;
  showAuth(false);
};
$('#palette-input').addEventListener('input', (event) => renderPalette(event.target.value));

$('#sftp-mkdir').onclick = () => {
  openModal(`<h3>New folder</h3><form id="mkdir-form">
    <label class="field"><span>Path</span><input name="path" value="${esc(S.sftp.path === '/' ? '/newdir' : S.sftp.path + '/newdir')}" /></label>
    <div class="modal-actions"><button type="button" class="btn ghost" data-close>Cancel</button>
    <button class="btn primary" type="submit">Create</button></div></form>`);
  $('#mkdir-form').onsubmit = async (event) => {
    event.preventDefault();
    const path = new FormData(event.target).get('path');
    try { await api(`/hosts/${S.sftp.hostId}/mkdir`, { json: { path } }); closeModal(); loadFiles(S.sftp.path); }
    catch (err) { toast(err.message, 'err'); }
  };
};

$('#sftp-upload').onclick = () => $('#sftp-file').click();
$('#sftp-file').onchange = async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  const target = (S.sftp.path === '/' ? '' : S.sftp.path) + '/' + file.name;
  try {
    const res = await fetch(`/api/hosts/${S.sftp.hostId}/upload?path=${encodeURIComponent(target)}`, {
      method: 'POST', body: form, credentials: 'same-origin', headers: { 'x-requested-with': 'sshdeck' },
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'upload failed');
    toast(`Uploaded: ${file.name}`);
    loadFiles(S.sftp.path);
  } catch (err) { toast('Upload: ' + err.message, 'err'); }
  event.target.value = '';
};

document.addEventListener('click', (event) => {
  const map = {
    'key-add': () => openCredModal(null),
    'snippet-add': () => openSnippetModal(null),
    'settings-import': openImportModal,
    'nd-add-site': async () => {
      const name = (($('#nd-site-name') || {}).value || '').trim();
      if (!name) { toast('Enter a site name', 'err'); return; }
      try {
        await api('/inventory/sites', { json: { name } });
        await loadInventory();
        renderPage();
        toast('Site added');
      } catch (err) { toast(err.message, 'err'); }
    },
    'nd-add-rack': async () => {
      const siteId = Number((($('#nd-rack-site') || {}).value) || 0);
      const name = (($('#nd-rack-name') || {}).value || '').trim();
      if (!siteId || !name) { toast('Select a site and enter a rack name', 'err'); return; }
      try {
        const units = Number((($('#nd-rack-units') || {}).value) || 42);
        const rack = await api('/inventory/racks', { json: { site_id: siteId, name, units } });
        await loadInventory();
        await ndSelectRack(rack.id);
        toast('Rack added');
      } catch (err) { toast(err.message, 'err'); }
    },
    'nd-add-dev': async () => {
      const rackId = (typeof ND !== 'undefined' && ND.rackId) || null;
      const name = (($('#nd-dev-name') || {}).value || '').trim();
      const kind = (($('#nd-dev-kind') || {}).value) || 'switch';
      const position = Number((($('#nd-dev-pos') || {}).value) || 1);
      if (!rackId || !name) { toast('Enter a device name', 'err'); return; }
      try {
        await api('/inventory/devices', { json: { rack_id: rackId, name, kind, position, units: 1 } });
        await loadInventory();
        await ndSelectRack(rackId);
        toast('Device added');
      } catch (err) {
        toast(err.message.startsWith('position_taken') ? 'That position is already taken' : err.message, 'err');
      }
    },
    'vault-lock': async () => {
      try { await api('/vault/lock', { json: {} }); await renderVaultState(); toast('Vault locked'); }
      catch (err) { toast(err.message, 'err'); }
    },
    'vault-unlock': async () => {
      try { await api('/vault/unlock', { json: {} }); await renderVaultState(); toast('Vault unlocked'); }
      catch (err) { toast(err.message, 'err'); }
    },
    'vault-save-lock': async () => {
      const minutes = Number((($('#vault-autolock') || {}).value) || 0);
      try {
        await api('/vault/auto-lock', { json: { seconds: Math.max(0, minutes) * 60 } });
        await renderVaultState();
        toast(minutes ? `Auto-lock: ${minutes} min` : 'Auto-lock off');
      } catch (err) { toast(err.message, 'err'); }
    },
    'upd-check': async () => {
      const line = $('#upd-status');
      if (line) line.textContent = 'Checking…';
      try {
        const result = await api('/update/check', json_opts_empty());
        // κρατάμε ΟΛΟ το release (asset + manifest + signature): το prepare τα χρειάζεται
        S.updateRelease = result;
        S.update = { ...(S.update || {}), ...result, last_check: new Date().toLocaleString('en-GB') };
        renderPage();
        toast(result.available ? `New version: ${result.latest}` : `You are on the latest (${result.latest || result.current})`);
      } catch (err) {
        if (line) line.textContent = err.message;
        toast(err.message, 'err');
      }
    },
    'upd-prepare': async () => {
      const release = S.updateRelease || (S.update || {});
      const asset = release.asset;
      if (!asset) { toast('Run a check first', 'err'); return; }
      const line = $('#upd-status');
      if (line) line.textContent = 'Downloading and verifying…';
      try {
        const result = await api('/update/prepare', {
          json: {
            latest: release.latest,
            asset: release.asset,
            manifest: release.manifest || null,
            signature: release.signature || null,
          },
        });
        if (line) line.textContent = `Prepared ${result.version} — installing and restarting…`;
        // one click: αμέσως μετά το staging ξεκινά η εγκατάσταση και η επανεκκίνηση
        const started = await api('/update/apply', { json: {} });
        if (line) line.textContent = started.started
          ? 'Installing — MUXIVRA restarts itself, no manual steps.'
          : 'Staged, but the installer could not start. Open Details.';
        toast(started.started ? 'Installing update — the app will restart' : 'Could not start the installer', started.started ? 'ok' : 'err');
      } catch (err) {
        if (line) line.textContent = err.message;
        toast(err.message, 'err');
      }
    },
    'pw-change': openPasswordModal,
    'logout2': async () => { await api('/logout', { json: {} }); showAuth(false); },
    'forget-key': async () => {
      const tab = activeTab();
      if (!tab) return;
      await api(`/hosts/${tab.host.id}/trust`, { method: 'DELETE' });
      toast(`Trust removed for ${tab.host.hostname}:${tab.host.port || 22}`);
    },
  };
  for (const [id, fn] of Object.entries(map)) {
    if (event.target.closest('#' + id)) { fn(); return; }
  }
});

function openPasswordModal() {
  openModal(`<h3>Change master password</h3>
    <form id="pw-form">
      <label class="field"><span>Current</span><input name="current" type="password" required autocomplete="current-password" /></label>
      <label class="field"><span>New (min 8)</span><input name="new" type="password" required minlength="8" autocomplete="new-password" /></label>
      <p class="error-text" id="pw-error"></p>
      <div class="modal-actions"><button type="button" class="btn ghost" data-close>Cancel</button>
      <button class="btn primary" type="submit">Change</button></div></form>`);
  $('#pw-form').onsubmit = async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target).entries());
    try {
      await api('/password', { json: { current: data.current, new: data.new } });
      closeModal();
      toast('Password changed — sign in again');
      showAuth(false);
    } catch (err) { $('#pw-error').textContent = err.message === 'bad_password' ? 'Wrong current password.' : err.message; }
  };
}

(async function boot() {
  const params = new URLSearchParams(location.search);
  const desktopToken = params.get('desktop_token');
  if (desktopToken) {
    try {
      await api('/desktop-login', { json: { token: desktopToken } });
      document.body.classList.add('desktop');
    } catch (err) {
      console.warn('desktop handshake failed', err);
    }
    history.replaceState({}, '', '/');
  }
  try {
    S.status = await api('/status');
  } catch (_) {
    S.status = { initialized: false, authenticated: false };
  }
  if (S.status.authenticated) {
    try { await enterApp(); } catch (_) { showAuth(false); }
  } else {
    showAuth(!S.status.initialized);
  }
})();
