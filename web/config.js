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
  device_without_profile: 'Η συσκευή δεν έχει συνδεδεμένο profile πρόσβασης — πρόσθεσε πρώτα SSH/Telnet.',
  config_requires_ssh: 'Η ανάκτηση configuration γίνεται μόνο μέσω SSH. Το Telnet/COM μένουν στο terminal.',
  vendor_not_supported: 'Δεν υπάρχει adapter για αυτόν τον vendor. Χρησιμοποίησε το terminal για raw config.',
  stale_config: 'Η διαμόρφωση άλλαξε από τότε που τη φόρτωσες — φέρε ξανά την τρέχουσα έκδοση.',
  confirmation_required: 'Χρειάζεται ρητή επιβεβαίωση.',
  no_observation: 'Δεν υπάρχει παρατηρημένη κατάσταση — πρώτα «Ανάκτηση configuration».',
};

function cfgErrorText(error) {
  const code = error && error.detail;
  if (code && CFG_ERRORS[code]) return CFG_ERRORS[code];
  return (error && error.message) || 'Άγνωστο σφάλμα';
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
  port_missing: 'θύρα που υπάρχει στη συσκευή και λείπει από την τεκμηρίωση',
  mgmt_ip: 'IP διαχείρισης',
  vlan: 'VLAN',
  port_not_observed: 'θύρα της τεκμηρίωσης δεν παρατηρήθηκε',
};

function cfgRender() {
  if (!CFG.el) return;
  const versions = CFG.versions.length
    ? CFG.versions.map((version) => {
      const checked = CFG.selected.indexOf(version.id) >= 0 ? ' checked' : '';
      return `<tr>
          <td><input type="checkbox" data-cfg-version="${version.id}"${checked} aria-label="Επιλογή έκδοσης ${version.id}" /></td>
          <td class="mono">#${version.id}</td>
          <td>${cfgEsc(cfgTime(version.created_at))}</td>
          <td>${cfgEsc(version.source)}</td>
          <td class="mono">${cfgEsc((version.sha256 || '').slice(0, 10))}</td>
          <td class="mono">${cfgSize(version.size)}</td>
          <td><button class="btn ghost xs" data-cfg-open="${version.id}">Προβολή</button></td>
        </tr>`;
    }).join('')
    : '<tr><td colspan="7" class="cfg-empty">Καμία έκδοση ακόμη — κάνε «Ανάκτηση configuration» ή επικόλληση.</td></tr>';

  const diff = CFG.diff
    ? `<div class="cfg-diff">
         <div class="cfg-diff-head"><b>+${CFG.diff.added}</b> / <b>−${CFG.diff.removed}</b>
           <span>έκδοση ${CFG.diff.left} → ${CFG.diff.right}</span></div>
         <pre class="mono">${CFG.diff.diff.split('\n').map((line) => {
    const cls = line.startsWith('+') && !line.startsWith('+++') ? 'add'
      : (line.startsWith('-') && !line.startsWith('---') ? 'del' : 'ctx');
    return `<i class="${cls}">${cfgEsc(line)}</i>`;
  }).join('\n')}</pre>
       </div>`
    : '';

  const actions = CFG.actions
    ? `<div class="cfg-actions">
         <h4>Διαφορές τεκμηρίωσης / συσκευής</h4>
         ${CFG.actions.length ? `<ul>${CFG.actions.map((action) => `<li>
             <b>${cfgEsc(CFG_ACTION_LABEL[action.kind] || action.kind)}</b>
             ${action.port ? `<span class="mono">${cfgEsc(action.port)}</span>` : ''}
             ${action.value ? `<span class="mono">${cfgEsc(action.value)}</span>` : ''}
             ${action.name ? `<span class="mono">${cfgEsc(action.name)}</span>` : ''}
             ${action.safe === false ? '<em class="cfg-warn">απαιτεί έλεγχο</em>' : ''}
           </li>`).join('')}</ul>` : '<p class="cfg-empty">Καμία διαφορά — η τεκμηρίωση συμφωνεί με την παρατηρημένη κατάσταση.</p>'}
         <label class="cfg-check"><input type="checkbox" id="cfg-confirm"${CFG.confirmApply ? ' checked' : ''} />
           <span>Επιβεβαιώνω ότι θέλω να ενημερωθεί η τεκμηρίωση (όχι η συσκευή)</span></label>
         <button class="btn ghost xs" id="cfg-apply"${CFG.confirmApply ? '' : ' disabled'}>Εφαρμογή στην τεκμηρίωση</button>
       </div>`
    : '';

  CFG.el.innerHTML = `<div class="cfg-panel">
    <header class="cfg-head">
      <div>
        <h3>Configuration · ${cfgEsc(CFG.deviceName || 'συσκευή')}</h3>
        <p class="cfg-sub">${CFG.vendor ? `vendor: ${cfgEsc(CFG.vendor)}` : 'χωρίς δηλωμένο vendor'}
          ${CFG.observed ? ` · παρατηρήθηκε ${cfgEsc(cfgTime(CFG.observed.collected_at))}` : ''}</p>
      </div>
      <div class="cfg-head-actions">
        <button class="btn primary xs" id="cfg-fetch"${CFG.busy ? ' disabled' : ''}>Ανάκτηση configuration</button>
        <button class="btn ghost xs" id="cfg-paste-toggle">Επικόλληση</button>
      </div>
    </header>

    <p class="cfg-error${CFG.error ? '' : ' hidden'}">${cfgEsc(CFG.error)}</p>
    <p class="cfg-notice${CFG.notice ? '' : ' hidden'}">${cfgEsc(CFG.notice)}</p>

    <div class="cfg-paste hidden" id="cfg-paste">
      <label class="cfg-field"><span>Επικόλλησε configuration (αποθηκεύεται κρυπτογραφημένο)</span>
        <textarea id="cfg-paste-text" rows="6" spellcheck="false" placeholder="/interface print&#10;/ip address print"></textarea></label>
      <div class="cfg-row">
        <label class="cfg-field"><span>Vendor</span>
          <select id="cfg-paste-vendor">
            <option value="mikrotik">MikroTik</option>
            <option value="cisco">Cisco</option>
            <option value="">Γενικό</option>
          </select></label>
        <button class="btn primary xs" id="cfg-paste-save">Αποθήκευση έκδοσης</button>
      </div>
    </div>

    <section class="cfg-block">
      <div class="cfg-block-head">
        <h4>Εκδόσεις</h4>
        <button class="btn ghost xs" id="cfg-diff-btn"${CFG.selected.length === 2 ? '' : ' disabled'}>Σύγκριση επιλεγμένων</button>
      </div>
      <table class="cfg-table">
        <thead><tr><th></th><th>#</th><th>Πότε</th><th>Πηγή</th><th>SHA-256</th><th>Μέγεθος</th><th></th></tr></thead>
        <tbody>${versions}</tbody>
      </table>
    </section>

    ${diff}
    ${actions}

    <section class="cfg-block">
      <div class="cfg-block-head">
        <h4>Συγχρονισμός</h4>
        <button class="btn ghost xs" id="cfg-reconcile">Προβολή διαφορών</button>
      </div>
      <p class="cfg-hint">Συγχρονίζονται μόνο υποστηριζόμενα λογικά στοιχεία (interfaces → θύρες, IP διαχείρισης, VLAN).
        Δεν δημιουργούνται καλώδια από configuration. Καμία εντολή δεν στέλνεται στη συσκευή από αυτό το panel.</p>
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
        const payload = await cfgApi(`/netdoc/config/versions/${button.dataset.cfgOpen}`);
        CFG.notice = `Έκδοση #${payload.id} · sha256 ${payload.sha256.slice(0, 12)}…`;
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
      cfgApi(`/netdoc/devices/${CFG.deviceId}/config/versions`),
      cfgApi(`/netdoc/devices/${CFG.deviceId}/observed`).catch(() => ({ observed: null })),
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
    const result = await cfgApi(`/netdoc/devices/${CFG.deviceId}/config/fetch`, {
      method: 'POST', json: { vendor: CFG.vendor, label: 'ανάκτηση' },
    });
    CFG.busy = false;
    CFG.notice = `Ανακτήθηκε έκδοση #${result.id} (${cfgSize(result.size)}) — διαβάστηκε ξανά από τη συσκευή.`;
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
  if (!text.trim()) { CFG.error = 'Το πεδίο είναι κενό.'; cfgRender(); return; }
  try {
    const result = await cfgApi(`/netdoc/devices/${CFG.deviceId}/config/paste`, {
      method: 'POST', json: { text, vendor },
    });
    CFG.error = '';
    CFG.notice = `Αποθηκεύτηκε έκδοση #${result.id} (${cfgSize(result.size)}).`;
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
    CFG.diff = await cfgApi(`/netdoc/config/diff?left=${left}&right=${right}`);
    CFG.error = '';
  } catch (error) {
    CFG.error = cfgErrorText(error);
  }
  cfgRender();
}

async function cfgReconcile(apply) {
  try {
    const result = await cfgApi(`/netdoc/devices/${CFG.deviceId}/reconcile`, {
      method: 'POST', json: { apply: !!apply },
    });
    CFG.actions = result.actions || [];
    CFG.error = '';
    CFG.notice = apply
      ? `Ενημερώθηκε η τεκμηρίωση: ${CFG.actions.length} στοιχεία. Καμία εντολή δεν στάλθηκε στη συσκευή.`
      : '';
    if (apply && typeof loadNetdoc === 'function') {
      try { await loadNetdoc(); } catch (_) { /* το panel δείχνει ό,τι διαβάστηκε */ }
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
    el.innerHTML = '<p class="cfg-empty">Διάλεξε συσκευή για να δεις το configuration της.</p>';
    return CFG;
  }
  el.classList.remove('hidden');
  cfgRender();
  await cfgLoad();
  return CFG;
}

window.MuxConfig = { mount: cfgMount, state: CFG, reload: cfgLoad };
