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
  if (diff < 60) return 'μόλις τώρα';
  if (diff < 3600) return `${Math.floor(diff / 60)}λ πριν`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}ω πριν`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}μ πριν`;
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
    throw new Error(detail);
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
  $('#auth-label').textContent = setup ? 'Νέο master password' : 'Master password';
  $('#auth-submit').textContent = setup ? 'Δημιουργία vault' : 'Σύνδεση';
  $('#auth-tag').textContent = setup
    ? 'Πρώτη εκκίνηση: όρισε το master password που προστατεύει το vault σου.'
    : 'Το προσωπικό σου SSH control room.';
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
      if (password.length < 8) throw new Error('Χρειάζονται τουλάχιστον 8 χαρακτήρες.');
      if (password !== $('#auth-password2').value) throw new Error('Τα δύο passwords δεν ταιριάζουν.');
      await api('/setup', { json: { username: 'admin', password } });
      toast('Το vault δημιουργήθηκε.');
    } else {
      await api('/login', { json: { password } });
    }
    $('#auth-password').value = '';
    $('#auth-password2').value = '';
    await enterApp();
  } catch (err) {
    message.textContent = err.message === 'bad_password' ? 'Λάθος password.' : err.message;
  }
}

/* --------------------------------------------------------------- app boot */
async function enterApp() {
  $('#auth').classList.add('hidden');
  $('#app').classList.remove('hidden');
  const status = await api('/status');
  S.status = status;
  $('#sb-user').textContent = status.username || 'admin';
  await Promise.all([loadHosts(), loadCreds(), loadSnippets(), loadEvents(), loadUpdateState()]);
  if (typeof loadNetdoc === 'function') await loadNetdoc();
  setView('hosts');
  renderSidebar();
  renderPage();
}

async function loadHosts() { S.hosts = await api('/hosts'); }
async function loadCreds() { S.creds = await api('/credentials'); }
async function loadSnippets() { S.snippets = await api('/snippets'); }
async function loadEvents() { S.events = await api('/events'); }
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
      <b>${S.hosts.length ? 'Κανένα αποτέλεσμα' : 'Κανένας server ακόμα'}</b>
      ${S.hosts.length ? 'Δοκίμασε άλλο όρο.' : 'Πρόσθεσε τον πρώτο server ή κάνε import το ~/.ssh/config.'}
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
    <span class="host-rail" style="background:${esc(host.color || '#3ddc97')}"></span>
    <div class="host-meta">
      <div class="host-name">${esc(host.label)}</div>
      <div class="host-sub">${sub}</div>
      ${protoChip || cred || tags ? `<div class="host-tags">${protoChip}${cred}${tags}</div>` : ''}
    </div>
    <div class="host-actions">
      <button class="icon-btn" data-act="edit" data-id="${host.id}" title="Επεξεργασία">✎</button>
      <button class="icon-btn" data-act="del" data-id="${host.id}" title="Διαγραφή">🗑</button>
    </div>
  </div>`;
}

/* ---------------------------------------------------------------- terminal */
function activeTab() { return S.tabs.find((t) => t.id === S.activeTabId) || null; }

function setView(view) {
  S.view = view;
  $$('.rail-btn[data-view]').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  if (view === 'hosts') S.activeTabId = null;
  renderTabs();
  renderPage();
  const hasTabs = S.tabs.length > 0 && S.activeTabId;
  $('#page-hosts').classList.toggle('hidden', view !== 'hosts' || !!hasTabs);
}

function activateTab(id) {
  S.activeTabId = id;
  S.tabs.forEach((tab) => {
    tab.pane.classList.toggle('active', tab.id === id);
  });
  $$('.page').forEach((p) => p.classList.add('hidden'));
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
    return `<div class="tab${tab.id === S.activeTabId ? ' active' : ''}" data-tab="${tab.id}">
      <span class="dot ${dotClass}"></span><span>${esc(tab.host.label)}</span>
      <span class="x" data-tab-close="${tab.id}">✕</span></div>`;
  }).join('');
  updateStatusbar();
}

function updateStatusbar() {
  const tab = activeTab();
  if (!tab) {
    $('#status-main').textContent = 'Έτοιμο';
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
      background: '#080a0e', foreground: '#dfe9e3', cursor: '#3ddc97', selectionBackground: '#1e4034',
      black: '#0b0f14', red: '#ff6b6b', green: '#3ddc97', yellow: '#ffc75f', blue: '#7c9cff',
      magenta: '#c792ea', cyan: '#5ee7f0', white: '#dfe9e3',
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
  const ws = new WebSocket(url);
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
      tab.state = 'closed';
      tab.term.writeln(`\x1b[31m✕ ${msg.message || msg.code}\x1b[0m`);
      toast(`Σύνδεση απέτυχε: ${msg.code}`, 'err');
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
      tab.term.writeln('\r\n\x1b[38;5;244m— σύνδεση έκλεισε —\x1b[0m');
      renderTabs();
    }
  };
  ws.onerror = () => {};

  tab.onData = tab.term.onData((data) => {
    if (ws.readyState === 1) ws.send(JSON.stringify({ type: 'input', data }));
  });
  tab.onResize = tab.term.onResize(() => sendResize(tab));
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
function renderPage() {
  renderHero();
  renderKeys();
  renderSnippets();
  renderActivity();
  if (typeof renderRacks === 'function') renderRacks();
  renderSettings();
  if (S.view !== 'hosts') {
    $('#page-hosts').classList.add('hidden');
    const page = $('#page-' + S.view);
    if (page) page.classList.remove('hidden');
  }
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
    ? `<h4>Πρόσφατη δραστηριότητα</h4>` + recent.map((e) => `<div class="event-row">
        <time>${fmtWhen(e.created_at)}</time><span>${esc(e.kind)}</span>
        <b>${esc(e.host_label || e.detail || '')}</b></div>`).join('')
    : '';
}

const KIND_LABEL = {
  login_ok: 'Σύνδεση στο panel', login_failed: 'Αποτυχημένη σύνδεση',
  host_created: 'Νέα σύνδεση', host_deleted: 'Διαγραφή server',
  credential_created: 'Νέο credential', credential_deleted: 'Διαγραφή credential',
  session_open: 'Άνοιγμα session', session_close: 'Κλείσιμο session',
  host_key_learned: 'Νέο host key', host_key_forgotten: 'Διαγραφή host key',
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
      <button class="icon-btn" data-cred-edit="${c.id}" title="Επεξεργασία">✎</button>
      <button class="icon-btn" data-cred-del="${c.id}" title="Διαγραφή">🗑</button>
    </div></td></tr>`).join('');
  $('#page-keys').innerHTML = `<div class="page-head">
      <div><h2>Keychain</h2><p>Passwords και private keys, κρυπτογραφημένα με AES στο vault.</p></div>
      <button class="btn primary" id="key-add">+ Νέο credential</button>
    </div>
    ${S.creds.length ? `<table class="table"><thead><tr>
      <th>Όνομα</th><th>Τύπος</th><th>Username</th><th>Secret</th><th>Ενημέρωση</th><th></th>
    </tr></thead><tbody>${rows}</tbody></table>`
    : `<div class="empty"><b>Κανένα credential</b>Πρόσθεσε password ή private key για να συνδέεσαι χωρίς prompts.</div>`}`;
}

function renderSnippets() {
  $('#page-snippets').innerHTML = `<div class="page-head">
      <div><h2>Snippets</h2><p>Έτοιμες εντολές, ένα κλικ μέσα στο ενεργό terminal.</p></div>
      <button class="btn primary" id="snippet-add">+ Νέο snippet</button>
    </div>
    ${S.snippets.length ? `<div class="card-grid">${S.snippets.map((s) => `<div class="card">
        <h3>${esc(s.label)}</h3>
        <div class="body"><pre class="code">${esc(s.command)}</pre></div>
        <div class="actions">
          <button class="btn xs" data-snip-run="${s.id}">▶ Εκτέλεση</button>
          <button class="btn ghost xs" data-snip-edit="${s.id}">Επεξεργασία</button>
          <button class="btn ghost xs danger" data-snip-del="${s.id}">Διαγραφή</button>
        </div></div>`).join('')}</div>`
    : `<div class="empty"><b>Κανένα snippet</b>π.χ. <span class="mono">docker ps</span> ή <span class="mono">journalctl -u nginx -f</span>.</div>`}`;
}

function renderActivity() {
  $('#page-activity').innerHTML = `<div class="page-head">
      <div><h2>Ιστορικό</h2><p>Συνδέσεις, αλλαγές vault και host keys.</p></div>
      <button class="btn ghost" id="events-refresh">⟳ Ανανέωση</button>
    </div>
    <table class="table"><thead><tr><th>Πότε</th><th>Γεγονός</th><th>Server</th><th>Λεπτομέρεια</th></tr></thead>
    <tbody>${S.events.map((e) => `<tr>
      <td class="mono" style="font-size:11px">${fmtWhen(e.created_at)}</td>
      <td>${esc(KIND_LABEL[e.kind] || e.kind)}</td>
      <td>${esc(e.host_label || '—')}</td>
      <td class="mono" style="font-size:11px;color:var(--muted)">${esc(e.detail || '')}</td>
    </tr>`).join('')}</tbody></table>`;
}

function renderSettings() {
  const status = S.status || {};
  const upd = S.update || {};
  const tab = activeTab();
  $('#page-settings').innerHTML = `<div class="page-head">
      <div><h2>Ρυθμίσεις</h2><p>Λογαριασμός, vault και host keys.</p></div>
    </div>
    <div class="card-grid">
      <div class="card"><h3>Λογαριασμός</h3>
        <div class="sub">${esc(status.username || 'admin')} · v${esc(status.version || '')}</div>
        <div class="body">Άλλαξε το master password. Όλες οι ενεργές συνεδρίες θα κλείσουν.</div>
        <div class="actions"><button class="btn xs" id="pw-change">Αλλαγή password</button>
        <button class="btn ghost xs" id="logout2">Έξοδος</button></div></div>
      <div class="card"><h3>Host keys (TOFU)</h3>
        <div class="body">Το πρώτο host key αποθηκεύεται και ελέγχεται σε κάθε σύνδεση.
        Αν αλλάξει, η σύνδεση απορρίπτεται.</div>
        <div class="actions">${tab ? `<button class="btn ghost xs" id="forget-key">Διαγραφή key για ${esc(tab.host.label)}</button>`
          : '<span class="muted" style="font-size:12px">Άνοιξε session για να δεις fingerprint.</span>'}</div></div>
      <div class="card"><h3>Import</h3>
        <div class="body">Διάβασε hosts από το <span class="mono">~/.ssh/config</span> του server ή επικόλλησε το περιεχόμενο.</div>
        <div class="actions"><button class="btn ghost xs" id="settings-import">Import ssh config</button></div></div>
      <div class="card"><h3>Ενημερώσεις</h3>
        <div class="sub">v${esc(upd.current || S.status?.version || '')} · build ${esc((S.status && S.status.build) || 'dev')} · ${esc((S.status && S.status.mode) || 'source')}</div>
        <div class="body mono" style="font-size:11.5px;word-break:break-all">data: ${esc((S.status && S.status.data_dir) || '')}</div>
        <div class="body">
          <label class="field"><span>GitHub repo</span>
            <input id="upd-repo" value="${esc(upd.repo || '')}" placeholder="owner/repo" autocomplete="off" /></label>
          <p class="hint" id="upd-status">${upd.last_check ? 'τελευταίος έλεγχος: ' + esc(upd.last_check) : 'κανένας έλεγχος ακόμα'}</p>
          <div class="actions">
            <button class="btn xs" id="upd-save">Αποθήκευση</button>
            <button class="btn xs" id="upd-check">Έλεγχος τώρα</button>
            <button class="btn primary xs${upd.asset ? '' : ' hidden'}" id="upd-prepare">Κατέβασε &amp; ετοίμασε</button>
          </div>
        </div></div>
      <div class="card"><h3>Vault</h3>
        <div class="sub">${esc((upd.vault_key || {}).mechanism || 'file-0600')}</div>
        <div class="body">Τα credentials είναι κρυπτογραφημένα (Fernet). ${(upd.vault_key || {}).protected
          ? 'Το κλειδί είναι δεμένο με τον Windows λογαριασμό σου (DPAPI) — αντίγραφο σε άλλο PC δεν το ανοίγει.'
          : 'Το κλειδί προστατεύεται με δικαιώματα αρχείου 0600.'}</div>
      </div>
    </div>`;

  const ev = $('#events-refresh');
  if (ev) ev.onclick = async () => { await loadEvents(); renderPage(); toast('Ανανεώθηκε'); };
}

/* --------------------------------------------------------------- host form */
function openModal(html) {
  $('#modal').innerHTML = html;
  $('#modal-backdrop').classList.remove('hidden');
}
function closeModal() { $('#modal-backdrop').classList.add('hidden'); $('#modal').innerHTML = ''; }

function credOptions(selected) {
  const opts = S.creds.map((c) => `<option value="${c.id}"${c.id === selected ? ' selected' : ''}>${esc(c.name)} (${c.kind === 'key' ? 'key' : 'password'})</option>`).join('');
  return `<option value="">— χωρίς credential (ask/agent) —</option>${opts}`;
}

function openHostModal(host) {
  const h = host || {
    label: '', protocol: 'ssh', hostname: '', port: 22, username: 'root', credential_id: null,
    group_name: 'General', tags: [], color: '#3ddc97', notes: '',
    serial_port: '', baud: 115200, bytesize: 8, parity: 'N', stopbits: 1,
  };
  const proto = h.protocol || 'ssh';
  openModal(`<h3>${host ? 'Επεξεργασία server' : 'Νέα σύνδεση'}</h3>
    <p class="lead">SSH, Telnet ή σειριακή θύρα — ίδιο terminal, ίδιο inventory.</p>
    <form id="host-form">
      <div class="proto-switch" id="proto-switch">
        <button type="button" data-proto="ssh">SSH</button>
        <button type="button" data-proto="telnet">Telnet</button>
        <button type="button" data-proto="serial">Serial / COM</button>
      </div>
      <input type="hidden" name="protocol" value="${esc(proto)}" />
      <div class="row">
        <label class="field"><span>Όνομα</span><input name="label" required value="${esc(h.label)}" placeholder="web-01" /></label>
        <label class="field" data-pane="net"><span>Hostname / IP</span><input name="hostname" value="${esc(h.hostname)}" placeholder="10.10.10.250" /></label>
      </div>
      <div class="row net" data-pane="net">
        <label class="field"><span>Port</span><input name="port" type="number" min="1" max="65535" value="${h.port}" /></label>
        <label class="field"><span>Username</span><input name="username" value="${esc(h.username)}" placeholder="root" /></label>
        <label class="field" data-pane="ssh"><span>Credential <button type="button" class="mini-add" id="cred-new" title="Νέο credential">+ Νέο</button></span><select name="credential_id">${credOptions(h.credential_id)}</select></label>
      </div>
      <div class="row serial" data-pane="serial">
        <label class="field"><span>Θύρα</span><input name="serial_port" list="serial-port-list" value="${esc(h.serial_port || '')}" placeholder="COM3 ή /dev/ttyUSB0" autocomplete="off" /></label>
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
        <label class="field"><span>Stop bits</span><select name="stopbits">
          ${[1, 1.5, 2].map((v) => `<option value="${v}"${Number(h.stopbits) === v ? ' selected' : ''}>${v}</option>`).join('')}
        </select></label>
      </div>
      <p class="hint" id="serial-hint" data-pane="serial">Έλεγχος θυρών…</p>
      <datalist id="serial-port-list"></datalist>
      <div class="row">
        <label class="field"><span>Group</span><input name="group_name" value="${esc(h.group_name)}" placeholder="General" /></label>
        <label class="field"><span>Tags (χωρισμένα με κόμμα)</span><input name="tags" value="${esc((h.tags || []).join(', '))}" placeholder="prod, nginx" /></label>
      </div>
      <label class="field"><span>Σημειώσεις</span><textarea name="notes" rows="2">${esc(h.notes)}</textarea></label>
      <p class="error-text" id="host-error"></p>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-close>Άκυρο</button>
        <button type="submit" class="btn primary">${host ? 'Αποθήκευση' : 'Δημιουργία'}</button>
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
        ? `${data.ports.length} θύρα(ες): ${data.ports.slice(0, 3).map((p) => p.device).join(', ')}`
        : 'Καμία σειριακή θύρα';
    }
  }).catch(() => {
    const hint = $('#serial-hint');
    if (hint) hint.textContent = 'Αδυναμία ανάγνωσης θυρών';
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
      color: h.color || '#3ddc97',
      notes: data.notes,
      serial_port: (data.serial_port || '').trim(),
      baud: Number(data.baud) || 115200,
      bytesize: Number(data.bytesize) || 8,
      parity: data.parity || 'N',
      stopbits: Number(data.stopbits) || 1,
    };
    try {
      if (host) await api(`/hosts/${host.id}`, { method: 'PUT', json: payload });
      else await api('/hosts', { json: payload });
      closeModal();
      await loadHosts();
      renderSidebar();
      renderPage();
      toast(host ? 'Η σύνδεση ενημερώθηκε' : 'Η σύνδεση προστέθηκε');
    } catch (err) {
      const map = {
        hostname_required: 'Χρειάζεται hostname/IP.',
        serial_port_required: 'Χρειάζεται σειριακή θύρα (π.χ. COM3).',
        unsupported_protocol: 'Μη υποστηριζόμενο πρωτόκολλο.',
      };
      $('#host-error').textContent = map[err.message] || err.message;
    }
  };
}

function openCredModal(cred, onSaved) {
  const c = cred || { name: '', kind: 'password', username: '', preview: '' };
  openModal(`<h3>${cred ? 'Επεξεργασία credential' : 'Νέο credential'}</h3>
    <p class="lead">Κρυπτογραφείται με Fernet (AES-128-CBC + HMAC) και μένει στον δίσκο του server.</p>
    <form id="cred-form">
      <div class="row">
        <label class="field"><span>Όνομα</span><input name="name" required value="${esc(c.name)}" placeholder="prod-root" /></label>
        <label class="field"><span>Τύπος</span><select name="kind">
          <option value="password"${c.kind === 'password' ? ' selected' : ''}>Password</option>
          <option value="key"${c.kind === 'key' ? ' selected' : ''}>Private key</option>
        </select></label>
      </div>
      <label class="field"><span>Username (default)</span><input name="username" value="${esc(c.username || '')}" placeholder="root" /></label>
      <label class="field"><span>Secret ${cred ? '(κενό = αμετάβλητο)' : ''}</span>
        <textarea name="secret" rows="5" placeholder="${cred ? '•••••• (αμετάβλητο)' : 'password ή -----BEGIN OPENSSH PRIVATE KEY-----'}" spellcheck="false"></textarea></label>
      <label class="field"><span>Passphrase key (προαιρετικό)</span><input name="passphrase" type="password" autocomplete="new-password" /></label>
      <p class="error-text" id="cred-error"></p>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-close>Άκυρο</button>
        <button type="submit" class="btn primary">${cred ? 'Αποθήκευση' : 'Δημιουργία'}</button>
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
      toast('Το credential αποθηκεύτηκε');
      if (onSaved) onSaved(saved);
    } catch (err) {
      $('#cred-error').textContent = err.message === 'not_a_private_key' ? 'Δεν μοιάζει με private key.' : err.message;
    }
  };
}

function openSnippetModal(snippet) {
  const s = snippet || { label: '', command: '' };
  openModal(`<h3>${snippet ? 'Επεξεργασία snippet' : 'Νέο snippet'}</h3>
    <form id="snip-form">
      <label class="field"><span>Όνομα</span><input name="label" required value="${esc(s.label)}" placeholder="Disk usage" /></label>
      <label class="field"><span>Εντολή</span><textarea name="command" rows="4" required spellcheck="false">${esc(s.command)}</textarea></label>
      <p class="error-text" id="snip-error"></p>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-close>Άκυρο</button>
        <button type="submit" class="btn primary">Αποθήκευση</button>
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
      toast('Το snippet αποθηκεύτηκε');
    } catch (err) { $('#snip-error').textContent = err.message; }
  };
}

function openImportModal() {
  openModal(`<h3>Import από ssh config</h3>
    <p class="lead">Διάβασε το <span class="mono">~/.ssh/config</span> του μηχανήματος ή επικόλλησε το περιεχόμενο.</p>
    <form id="import-form">
      <label class="field"><span>Διαδρομή στον server</span><input name="path" value="/root/.ssh/config" /></label>
      <label class="field"><span>ή περιεχόμενο</span><textarea name="text" rows="5" placeholder="Host web-01&#10;  HostName 10.0.0.5&#10;  User root" spellcheck="false"></textarea></label>
      <div class="row">
        <label class="field"><span>Credential για όλους</span><select name="credential_id">${credOptions(null)}</select></label>
        <label class="field"><span>Group</span><input name="group_name" value="Imported" /></label>
      </div>
      <p class="error-text" id="import-error"></p>
      <div class="modal-actions"><button type="button" class="btn ghost" data-close>Άκυρο</button>
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
      toast(`Import: ${result.imported} hosts (${result.skipped} υπήρχαν)`);
    } catch (err) {
      $('#import-error').textContent = err.message === 'config_unreadable' ? 'Το αρχείο δεν είναι αναγνώσιμο.' : err.message;
    }
  };
}

function confirmDialog(title, text, onConfirm, confirmLabel) {
  openModal(`<h3>${esc(title)}</h3><p class="lead">${esc(text)}</p>
    <div class="modal-actions"><button class="btn ghost" data-close>Άκυρο</button>
    <button class="btn danger" id="confirm-ok">${esc(confirmLabel || 'Διαγραφή')}</button></div>`);
  $('#confirm-ok').onclick = async () => { closeModal(); await onConfirm(); };
}

/* ------------------------------------------------------------------- SFTP */
function openDrawer() {
  const tab = activeTab();
  const host = tab ? tab.host : S.hosts[0];
  if (!host) { toast('Πρόσθεσε πρώτα server', 'err'); return; }
  if ((host.protocol || 'ssh') !== 'ssh') {
    toast('Το SFTP υποστηρίζεται μόνο για SSH συνδέσεις', 'err');
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
  $('#sftp-list').innerHTML = '<div class="empty" style="margin:12px;padding:18px">Φόρτωση…</div>';
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
        <span class="fa" data-rm="${esc(e.path)}" data-isdir="${e.is_dir}" title="Διαγραφή">✕</span>
      </div>`);
    });
    $('#sftp-list').innerHTML = rows.join('') || '<div class="empty" style="margin:12px">Άδειος φάκελος</div>';
  } catch (err) {
    $('#sftp-list').innerHTML = `<div class="empty" style="margin:12px;padding:18px"><b>Σφάλμα</b>${esc(err.message)}</div>`;
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
    || '<div class="pal-item"><span class="muted">Κανένας server — πρόσθεσε έναν πρώτα.</span></div>';
}

function connectFromPalette() {
  const host = S.paletteItems[S.paletteIndex];
  if (!host) return;
  closePalette();
  openTerminal(host);
}

/* ----------------------------------------------------------------- events */
document.addEventListener('click', async (event) => {
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
      const created = await api('/netdoc/ports/bulk', { json: { device_id: deviceId, prefix: 'eth', count: 24 } });
      await loadNetdoc();
      renderPage();
      toast(`${created.created.length} ports προστέθηκαν`);
    } catch (err) { toast(err.message, 'err'); }
    return;
  }

  const closeBtn = event.target.closest('[data-close]');
  if (closeBtn) { closeModal(); return; }

  const hostRow = event.target.closest('.host-row');
  if (hostRow) {
    const id = Number(hostRow.dataset.host);
    const host = S.hosts.find((h) => h.id === id);
    const act = event.target.closest('[data-act]');
    if (act && act.dataset.act === 'edit') { event.stopPropagation(); openHostModal(host); return; }
    if (act && act.dataset.act === 'del') {
      event.stopPropagation();
      confirmDialog('Διαγραφή server', `Ο/Η «${host.label}» θα αφαιρεθεί από το inventory.`, async () => {
        await api(`/hosts/${id}`, { method: 'DELETE' });
        await loadHosts(); renderSidebar(); renderPage(); toast('Ο server διαγράφηκε');
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
    confirmDialog('Διαγραφή credential', `Να διαγραφεί το «${cred.name}»;`, async () => {
      try {
        await api(`/credentials/${cred.id}`, { method: 'DELETE' });
        await loadCreds(); renderPage(); toast('Το credential διαγράφηκε');
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
    confirmDialog('Διαγραφή snippet', `Να διαγραφεί το «${snippet.label}»;`, async () => {
      await api(`/snippets/${snippet.id}`, { method: 'DELETE' });
      await loadSnippets(); renderPage(); toast('Διαγράφηκε');
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
    if (!res.ok) { toast('Download απέτυχε', 'err'); return; }
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = path.split('/').pop();
    a.click();
    URL.revokeObjectURL(a.href);
    toast('Λήψη: ' + a.download);
    return;
  }
  const rm = event.target.closest('[data-rm]');
  if (rm) {
    event.stopPropagation();
    const path = rm.dataset.rm;
    confirmDialog('Διαγραφή', `Να διαγραφεί το ${path};`, async () => {
      try {
        await api(`/hosts/${S.sftp.hostId}/delete-path`, { json: { path } });
        await loadFiles(S.sftp.path);
        toast('Διαγράφηκε');
      } catch (err) { toast(err.message, 'err'); }
    });
    return;
  }

  if (event.target.closest('#modal-backdrop') && event.target.id === 'modal-backdrop') closeModal();
  if (!$('#palette-backdrop').classList.contains('hidden') && !event.target.closest('.palette')) closePalette();
});

function runSnippet(snippet) {
  const tab = activeTab();
  if (!tab || tab.state !== 'open') { toast('Άνοιξε πρώτα session', 'err'); return; }
  tab.ws.send(JSON.stringify({ type: 'input', data: snippet.command + '\r' }));
  toast(`▶ ${snippet.label}`);
}

document.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); openPalette(); return; }
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
  openModal(`<h3>Νέος φάκελος</h3><form id="mkdir-form">
    <label class="field"><span>Διαδρομή</span><input name="path" value="${esc(S.sftp.path === '/' ? '/newdir' : S.sftp.path + '/newdir')}" /></label>
    <div class="modal-actions"><button type="button" class="btn ghost" data-close>Άκυρο</button>
    <button class="btn primary" type="submit">Δημιουργία</button></div></form>`);
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
    toast(`Ανέβηκε: ${file.name}`);
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
      if (!name) { toast('Δώσε όνομα χώρου', 'err'); return; }
      try {
        await api('/netdoc/sites', { json: { name } });
        await loadNetdoc();
        renderPage();
        toast('Ο χώρος προστέθηκε');
      } catch (err) { toast(err.message, 'err'); }
    },
    'nd-add-rack': async () => {
      const siteId = Number((($('#nd-rack-site') || {}).value) || 0);
      const name = (($('#nd-rack-name') || {}).value || '').trim();
      if (!siteId || !name) { toast('Διάλεξε χώρο και όνομα rack', 'err'); return; }
      try {
        const rack = await api('/netdoc/racks', { json: { site_id: siteId, name } });
        await loadNetdoc();
        await ndSelectRack(rack.id);
        toast('Το rack προστέθηκε');
      } catch (err) { toast(err.message, 'err'); }
    },
    'nd-add-dev': async () => {
      const rackId = (typeof ND !== 'undefined' && ND.rackId) || null;
      const name = (($('#nd-dev-name') || {}).value || '').trim();
      const kind = (($('#nd-dev-kind') || {}).value) || 'switch';
      const position = Number((($('#nd-dev-pos') || {}).value) || 1);
      if (!rackId || !name) { toast('Δώσε όνομα συσκευής', 'err'); return; }
      try {
        await api('/netdoc/devices', { json: { rack_id: rackId, name, kind, position, units: 1 } });
        await loadNetdoc();
        await ndSelectRack(rackId);
        toast('Η συσκευή προστέθηκε');
      } catch (err) {
        toast(err.message.startsWith('position_taken') ? 'Η θέση είναι πιασμένη' : err.message, 'err');
      }
    },
    'upd-save': async () => {
      const repo = ($('#upd-repo') || {}).value || '';
      try {
        await api('/update/repo', { json: { repo: repo.trim() } });
        await loadUpdateState();
        renderPage();
        toast('Αποθηκεύτηκε');
      } catch (err) { toast(err.message === 'invalid_repo' ? 'Μορφή: owner/repo' : err.message, 'err'); }
    },
    'upd-check': async () => {
      const repo = ($('#upd-repo') || {}).value || '';
      const line = $('#upd-status');
      if (line) line.textContent = 'Έλεγχος…';
      try {
        const result = await api('/update/check', { json: { repo: repo.trim() } });
        S.update = { ...(S.update || {}), ...result, asset: result.asset, last_check: new Date().toLocaleString('el-GR') };
        renderPage();
        toast(result.available ? `Νέα έκδοση: ${result.latest}` : `Είσαι στην τελευταία (${result.latest || result.current})`);
      } catch (err) {
        if (line) line.textContent = err.message;
        toast(err.message, 'err');
      }
    },
    'upd-prepare': async () => {
      const asset = (S.update || {}).asset;
      if (!asset) { toast('Κάνε πρώτα έλεγχο', 'err'); return; }
      const line = $('#upd-status');
      if (line) line.textContent = 'Κατέβασμα και επαλήθευση…';
      try {
        const result = await api('/update/prepare', { json: asset });
        if (line) line.textContent = 'Έτοιμο: ' + result.script;
        toast('Κλείσε το MUXIVRA και τρέξε apply-update.cmd');
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
      await api(`/hosts/${tab.host.id}/forget-host-key`, { json: {} });
      toast('Το host key διαγράφηκε — η επόμενη σύνδεση θα το ξαναμάθει');
    },
  };
  for (const [id, fn] of Object.entries(map)) {
    if (event.target.closest('#' + id)) { fn(); return; }
  }
});

function openPasswordModal() {
  openModal(`<h3>Αλλαγή master password</h3>
    <form id="pw-form">
      <label class="field"><span>Τρέχον</span><input name="current" type="password" required autocomplete="current-password" /></label>
      <label class="field"><span>Νέο (min 8)</span><input name="new" type="password" required minlength="8" autocomplete="new-password" /></label>
      <p class="error-text" id="pw-error"></p>
      <div class="modal-actions"><button type="button" class="btn ghost" data-close>Άκυρο</button>
      <button class="btn primary" type="submit">Αλλαγή</button></div></form>`);
  $('#pw-form').onsubmit = async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target).entries());
    try {
      await api('/password', { json: { current: data.current, new: data.new } });
      closeModal();
      toast('Το password άλλαξε — συνδέσου ξανά');
      showAuth(false);
    } catch (err) { $('#pw-error').textContent = err.message === 'bad_password' ? 'Λάθος τρέχον password.' : err.message; }
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
