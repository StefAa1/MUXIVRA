/* ------------------------------------------------------- MUXIVRA · config
   Χώρος εργασίας configuration για μία συσκευή:
     Ανάκτηση → Εκδόσεις → Σύγκριση (diff) → Συγχρονισμός τεκμηρίωσης

   Τρία πράγματα που το panel δεν κάνει ποτέ:
   • δεν στέλνει εντολές στη συσκευή (apply γίνεται μόνο από το backend με ρητή
     επιβεβαίωση και φρέσκια έκδοση — εδώ δεν εκτίθεται),
   • δεν δημιουργεί καλώδια από configuration/IP,
   • δεν δηλώνει επιτυχία χωρίς επανάγνωση της κατάστασης.

   Entry point: window.MuxConfig.mount(container, {deviceId, deviceName})
*/
'use strict';

const CFG = {
  deviceId: null,
  deviceName: '',
  vendor: '',
  versions: [],
  selected: [],
  diff: null,
  observed: null,
  actions: null,
  busy: false,
  error: '',
  notice: '',
  confirmApply: false,
  el: null,
};

function cfgEsc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function cfgApi(path, options) {
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
      error.status = response.status;
      throw error;
    }
    return payload;
  });
}

function cfgToast(message) {
  if (typeof toast === 'function') {
    try { toast(message); return; } catch (_) { /* ignored */ }
  }
}

/* Τα 422 codes του backend μεταφράζονται σε συγκεκριμένη εξήγηση */
const CFG_ERRORS = {
  device_without_profile: 'This device has no access profile — add SSH/Telnet first.',
  config_requires_ssh: 'Configuration retrieval runs over SSH only. Telnet/COM stay in the terminal.',
  vendor_not_supported: 'No adapter for this vendor. Use the terminal for raw config.',
  stale_config: 'The configuration changed since you loaded it — fetch the current version again.',
  confirmation_required: 'Explicit confirmation is required.',
  no_observation: 'No observed state yet — run "Fetch configuration" first.',
};

function cfgErrorText(error) {
  const code = error && error.detail;
  if (code && CFG_ERRORS[code]) return CFG_ERRORS[code];
  return (error && error.message) || 'Unknown error';
}

function cfgSize(bytes) {
  const value = Number(bytes) || 0;
  if (value < 1024) return `${value} B`;
  return `${Math.round(value / 1024)} KB`;
}

function cfgTime(seconds) {
  const value = Number(seconds) * 1000;
  if (!Number.isFinite(value) || !value) return '—';
  const date = new Date(value);
  return date.toLocaleString('el-GR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

const CFG_ACTION_LABEL = {
  port_missing: 'port present on the device but missing from documentation',
  mgmt_ip: 'Management IP',
  vlan: 'VLAN',
  port_not_observed: 'documented port not observed on the device',
};

function cfgRender() {
  if (!CFG.el) return;
  const versions = CFG.versions.length
    ? CFG.versions.map((version) => {
      const checked = CFG.selected.indexOf(version.id) >= 0 ? ' checked' : '';
      return `<tr>
          <td><input type="checkbox" data-cfg-version="${version.id}"${checked} aria-label="Select version ${version.id}" /></td>
          <td class="mono">#${version.id}</td>
          <td>${cfgEsc(cfgTime(version.created_at))}</td>
          <td>${cfgEsc(version.source)}</td>
          <td class="mono">${cfgEsc((version.sha256 || '').slice(0, 10))}</td>
          <td class="mono">${cfgSize(version.size)}</td>
          <td><button class="btn ghost xs" data-cfg-open="${version.id}">View</button></td>
        </tr>`;
    }).join('')
    : '<tr><td colspan="7" class="cfg-empty">No versions yet — run "Fetch configuration" or paste one.</td></tr>';

  const diff = CFG.diff
    ? `<div class="cfg-diff">
         <div class="cfg-diff-head"><b>+${CFG.diff.added}</b> / <b>−${CFG.diff.removed}</b>
           <span>version ${CFG.diff.left} → ${CFG.diff.right}</span></div>
         <pre class="mono">${CFG.diff.diff.split('\n').map((line) => {
    const cls = line.startsWith('+') && !line.startsWith('+++') ? 'add'
      : (line.startsWith('-') && !line.startsWith('---') ? 'del' : 'ctx');
    return `<i class="${cls}">${cfgEsc(line)}</i>`;
  }).join('\n')}</pre>
       </div>`
    : '';

  const actions = CFG.actions
    ? `<div class="cfg-actions">
         <h4>Documentation vs device differences</h4>
         ${CFG.actions.length ? `<ul>${CFG.actions.map((action) => `<li>
             <b>${cfgEsc(CFG_ACTION_LABEL[action.kind] || action.kind)}</b>
             ${action.port ? `<span class="mono">${cfgEsc(action.port)}</span>` : ''}
             ${action.value ? `<span class="mono">${cfgEsc(action.value)}</span>` : ''}
             ${action.name ? `<span class="mono">${cfgEsc(action.name)}</span>` : ''}
             ${action.safe === false ? '<em class="cfg-warn">needs review</em>' : ''}
           </li>`).join('')}</ul>` : '<p class="cfg-empty">No differences — documentation matches the observed state.</p>'}
         <label class="cfg-check"><input type="checkbox" id="cfg-confirm"${CFG.confirmApply ? ' checked' : ''} />
           <span>I confirm I want to update the documentation (not the device)</span></label>
         <button class="btn ghost xs" id="cfg-apply"${CFG.confirmApply ? '' : ' disabled'}>Apply to documentation</button>
       </div>`
    : '';

  CFG.el.innerHTML = `<div class="cfg-panel">
    <header class="cfg-head">
      <div>
        <h3>Configuration · ${cfgEsc(CFG.deviceName || 'device')}</h3>
        <p class="cfg-sub">${CFG.vendor ? `vendor: ${cfgEsc(CFG.vendor)}` : 'no vendor declared'}
          ${CFG.observed ? ` · observed ${cfgEsc(cfgTime(CFG.observed.collected_at))}` : ''}</p>
      </div>
      <div class="cfg-head-actions">
        <button class="btn primary xs" id="cfg-fetch"${CFG.busy ? ' disabled' : ''}>Fetch configuration</button>
        <button class="btn ghost xs" id="cfg-paste-toggle">Paste</button>
      </div>
    </header>

    <p class="cfg-error${CFG.error ? '' : ' hidden'}">${cfgEsc(CFG.error)}</p>
    <p class="cfg-notice${CFG.notice ? '' : ' hidden'}">${cfgEsc(CFG.notice)}</p>

    <div class="cfg-paste hidden" id="cfg-paste">
      <label class="cfg-field"><span>Paste configuration (stored encrypted)</span>
        <textarea id="cfg-paste-text" rows="6" spellcheck="false" placeholder="/interface print&#10;/ip address print"></textarea></label>
      <div class="cfg-row">
        <label class="cfg-field"><span>Vendor</span>
          <select id="cfg-paste-vendor">
            <option value="mikrotik">MikroTik</option>
            <option value="cisco">Cisco</option>
            <option value="">General</option>
          </select></label>
        <button class="btn primary xs" id="cfg-paste-save">Save version</button>
      </div>
    </div>

    <section class="cfg-block">
      <div class="cfg-block-head">
        <h4>Versions</h4>
        <button class="btn ghost xs" id="cfg-diff-btn"${CFG.selected.length === 2 ? '' : ' disabled'}>Compare selected</button>
      </div>
      <table class="cfg-table">
        <thead><tr><th></th><th>#</th><th>When</th><th>Source</th><th>SHA-256</th><th>Size</th><th></th></tr></thead>
        <tbody>${versions}</tbody>
      </table>
    </section>

    ${diff}
    ${actions}

    <section class="cfg-block">
      <div class="cfg-block-head">
        <h4>Sync</h4>
        <button class="btn ghost xs" id="cfg-reconcile">Show differences</button>
      </div>
      <p class="cfg-hint">Only supported logical items are synced (interfaces → ports, management IP, VLAN).
        No cables are created from configuration. This panel never sends commands to the device.</p>
    </section>
  </div>`;
  cfgBind();
}

function cfgBind() {
  const el = CFG.el;
  const fetchButton = el.querySelector('#cfg-fetch');
  if (fetchButton) fetchButton.onclick = cfgFetch;
  const pasteToggle = el.querySelector('#cfg-paste-toggle');
  if (pasteToggle) pasteToggle.onclick = () => {
    const box = el.querySelector('#cfg-paste');
    box.classList.toggle('hidden');
    const field = el.querySelector('#cfg-paste-text');
    if (field && !box.classList.contains('hidden')) field.focus();
  };
  const pasteSave = el.querySelector('#cfg-paste-save');
  if (pasteSave) pasteSave.onclick = cfgPaste;
  const diffButton = el.querySelector('#cfg-diff-btn');
  if (diffButton) diffButton.onclick = cfgDiff;
  const reconcile = el.querySelector('#cfg-reconcile');
  if (reconcile) reconcile.onclick = () => cfgReconcile(false);
  const apply = el.querySelector('#cfg-apply');
  if (apply) apply.onclick = () => cfgReconcile(true);
  const confirm = el.querySelector('#cfg-confirm');
  if (confirm) confirm.onchange = () => {
    CFG.confirmApply = confirm.checked;
    const button = el.querySelector('#cfg-apply');
    if (button) button.disabled = !confirm.checked;
  };
  el.querySelectorAll('[data-cfg-version]').forEach((box) => {
    box.onchange = () => {
      const id = Number(box.dataset.cfgVersion);
      CFG.selected = CFG.selected.filter((v) => v !== id);
      if (box.checked) CFG.selected.push(id);
      if (CFG.selected.length > 2) {
        CFG.selected = CFG.selected.slice(-2);
        el.querySelectorAll('[data-cfg-version]').forEach((other) => {
          other.checked = CFG.selected.indexOf(Number(other.dataset.cfgVersion)) >= 0;
        });
      }
      const button = el.querySelector('#cfg-diff-btn');
      if (button) button.disabled = CFG.selected.length !== 2;
    };
  });
  el.querySelectorAll('[data-cfg-open]').forEach((button) => {
    button.onclick = async () => {
      try {
        const payload = await cfgApi(`/inventory/config/versions/${button.dataset.cfgOpen}`);
        CFG.notice = `Version #${payload.id} · sha256 ${payload.sha256.slice(0, 12)}…`;
        CFG.error = '';
        cfgRender();
        const pre = document.createElement('pre');
        pre.className = 'mono cfg-raw';
        pre.textContent = payload.text;
        CFG.el.querySelector('.cfg-panel').appendChild(pre);
      } catch (error) {
        CFG.error = cfgErrorText(error);
        cfgRender();
      }
    };
  });
}

async function cfgLoad() {
  try {
    const [versions, observed] = await Promise.all([
      cfgApi(`/inventory/devices/${CFG.deviceId}/config/versions`),
      cfgApi(`/inventory/devices/${CFG.deviceId}/observed`).catch(() => ({ observed: null })),
    ]);
    CFG.versions = versions.versions || [];
    CFG.vendor = versions.vendor || CFG.vendor;
    CFG.observed = observed.observed || null;
  } catch (error) {
    CFG.error = cfgErrorText(error);
  }
  cfgRender();
}

async function cfgFetch() {
  CFG.busy = true;
  CFG.error = '';
  CFG.notice = '';
  cfgRender();
  try {
    const result = await cfgApi(`/inventory/devices/${CFG.deviceId}/config/fetch`, {
      method: 'POST', json: { vendor: CFG.vendor, label: 'fetch' },
    });
    CFG.busy = false;
    CFG.notice = `Fetched version #${result.id} (${cfgSize(result.size)}) — read back from the device.`;
    await cfgLoad();
  } catch (error) {
    CFG.busy = false;
    CFG.error = cfgErrorText(error);
    cfgRender();
  }
}

async function cfgPaste() {
  const text = CFG.el.querySelector('#cfg-paste-text').value;
  const vendor = CFG.el.querySelector('#cfg-paste-vendor').value;
  if (!text.trim()) { CFG.error = 'The field is empty.'; cfgRender(); return; }
  try {
    const result = await cfgApi(`/inventory/devices/${CFG.deviceId}/config/paste`, {
      method: 'POST', json: { text, vendor },
    });
    CFG.error = '';
    CFG.notice = `Saved version #${result.id} (${cfgSize(result.size)}).`;
    await cfgLoad();
  } catch (error) {
    CFG.error = cfgErrorText(error);
    cfgRender();
  }
}

async function cfgDiff() {
  if (CFG.selected.length !== 2) return;
  const [left, right] = CFG.selected.slice().sort((a, b) => a - b);
  try {
    CFG.diff = await cfgApi(`/inventory/config/diff?left=${left}&right=${right}`);
    CFG.error = '';
  } catch (error) {
    CFG.error = cfgErrorText(error);
  }
  cfgRender();
}

async function cfgReconcile(apply) {
  try {
    const result = await cfgApi(`/inventory/devices/${CFG.deviceId}/reconcile`, {
      method: 'POST', json: { apply: !!apply },
    });
    CFG.actions = result.actions || [];
    CFG.error = '';
    CFG.notice = apply
      ? `Documentation updated: ${CFG.actions.length} items. No commands were sent to the device.`
      : '';
    if (apply && typeof loadInventory === 'function') {
      try { await loadInventory(); } catch (_) { /* το panel δείχνει ό,τι διαβάστηκε */ }
    }
    await cfgLoad();
  } catch (error) {
    CFG.error = cfgErrorText(error);
    cfgRender();
  }
}

async function cfgMount(container, context) {
  const el = container || document.querySelector('#config-host');
  if (!el) return null;
  CFG.el = el;
  CFG.deviceId = Number((context || {}).deviceId || 0) || null;
  CFG.deviceName = (context || {}).deviceName || '';
  CFG.vendor = (context || {}).vendor || '';
  CFG.versions = [];
  CFG.selected = [];
  CFG.diff = null;
  CFG.actions = null;
  CFG.confirmApply = false;
  CFG.error = '';
  CFG.notice = '';
  if (!CFG.deviceId) {
    el.innerHTML = '<p class="cfg-empty">Select a device to see its configuration.</p>';
    return CFG;
  }
  el.classList.remove('hidden');
  cfgRender();
  await cfgLoad();
  return CFG;
}

window.MuxConfig = { mount: cfgMount, state: CFG, reload: cfgLoad };
