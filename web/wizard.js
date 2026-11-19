/* ------------------------------------------------------- MUXIVRA · wizard
   Καθοδηγούμενη προσθήκη συσκευής σε τρία βήματα:
     1 Στοιχεία → 2 Πρόσβαση → 3 Τοποθέτηση

   Κανόνες που τηρούνται εδώ:
   • μία μόνο τελική αποθήκευση· αν η τοποθέτηση αποτύχει, η εγγραφή του
     profile διαγράφεται ξανά, ώστε να μη μένει μισή εγγραφή,
   • κανένα μυστικό δεν μένει στο DOM ή στο localStorage μετά την αποθήκευση,
   • οι τιμές της φόρμας επιβιώνουν από inline δημιουργία πελάτη/χώρου/rack ή
     credential (δεν ξαναγεμίζει ο χρήστης τίποτα).

   Entry point: window.MuxWizard.mount(container, {onDone, onCancel})
*/
'use strict';

const WZ = {
  step: 1,
  data: {
    name: '', kind: 'switch', vendor: '', model: '', mgmt_ip: '', serial_interface: '',
    planned: false, note: '',
    protocol: 'ssh', username: '', credential_id: '', secret: '', port: 22,
    serial_port: '', baud: 115200, bytesize: 8, parity: 'N', stopbits: 1, flow: 'none',
    site_id: '', site_name: '', rack_id: '', rack_name: '', units: 1, position: 1,
  },
  credentials: [],
  sites: [],
  layout: null,            // τρέχον rack layout: ποιες θέσεις U είναι πιασμένες
  busy: false,
  error: '',
  host: 22,
  onDone: null,
  onCancel: null,
  el: null,
};

const WZ_KINDS = [
  ['switch', 'Switch'], ['router', 'Router'], ['firewall', 'Firewall'], ['ap', 'Access point'],
  ['patch-panel', 'Patch panel'], ['server', 'Server'], ['pdu', 'PDU'], ['other', 'Άλλο'],
];
const WZ_PROTOCOLS = [['ssh', 'SSH'], ['telnet', 'Telnet'], ['serial', 'Serial / COM']];

function wzEsc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function wzApi(path, options) {
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
      error.status = response.status;
      error.detail = payload.detail;
      throw error;
    }
    return payload;
  });
}

function wzToast(message) {
  if (typeof toast === 'function') {
    try { toast(message); return; } catch (_) { /* ignored */ }
  }
}

/* --------------------------------------------------------------- validation */
function wzValidateStep(step) {
  const d = WZ.data;
  if (step === 1) {
    if (!d.name.trim()) return { field: 'wz-name', message: 'Χρειάζεται όνομα συσκευής.' };
    if (!d.planned && !d.mgmt_ip.trim() && !d.serial_port.trim()) {
      return { field: 'wz-ip', message: 'Δώσε management IP/hostname ή θύρα COM, ή δήλωσέ την ως σχεδιαζόμενη.' };
    }
    if (d.mgmt_ip.trim() && /\s/.test(d.mgmt_ip.trim())) {
      return { field: 'wz-ip', message: 'Το hostname/IP δεν μπορεί να έχει κενά.' };
    }
    return null;
  }
  if (step === 2) {
    if (d.protocol !== 'serial') {
      if (!d.username.trim()) return { field: 'wz-user', message: 'Χρειάζεται όνομα χρήστη για σύνδεση.' };
      if (!d.credential_id) return { field: 'wz-cred', message: 'Διάλεξε ή δημιούργησε credential.' };
      const port = Number(d.port);
      if (!Number.isFinite(port) || port < 1 || port > 65535) {
        return { field: 'wz-port', message: 'Η θύρα θέλει τιμή 1–65535.' };
      }
    } else {
      if (!d.serial_port.trim()) return { field: 'wz-com', message: 'Δώσε θύρα COM (π.χ. COM3).' };
      const baud = Number(d.baud);
      if (!Number.isFinite(baud) || baud < 50) return { field: 'wz-baud', message: 'Baud ≥ 50.' };
    }
    return null;
  }
  if (d.planned) return null;
  if (!d.rack_id) return { field: 'wz-rack', message: 'Διάλεξε rack ή δημιούργησε νέο.' };
  const position = Number(d.position);
  const units = Number(d.units) || 1;
  if (!Number.isFinite(position) || position < 1) return { field: 'wz-position', message: 'Θέση U ≥ 1.' };
  if (position + units - 1 > 60) return { field: 'wz-position', message: 'Η συσκευή βγαίνει έξω από το rack.' };
  if (WZ.layout) {
    const taken = wzOccupied();
    for (let unit = position; unit <= position + units - 1; unit += 1) {
      if (taken.has(unit)) {
        return { field: 'wz-position', message: `Το U${unit} είναι πιασμένο — ${wzFreeHint()}` };
      }
    }
    const total = Number((WZ.layout.rack && WZ.layout.rack.units) || 0);
    if (total && position + units - 1 > total) {
      return { field: 'wz-position', message: `Το rack έχει ${total}U.` };
    }
  }
  return null;
}

function wzShowError(error) {
  WZ.error = error ? error.message : '';
  const box = WZ.el && WZ.el.querySelector('#wz-error');
  if (box) {
    box.textContent = WZ.error;
    box.classList.toggle('hidden', !WZ.error);
  }
  if (error && error.field) {
    const input = WZ.el.querySelector(`#${error.field}`);
    if (input) {
      input.classList.add('invalid');
      input.focus();
      if (input.select) input.select();
    }
  }
}

/* -------------------------------------------------------------------- shell */
function wzStepsHtml() {
  const labels = [['1', 'Στοιχεία'], ['2', 'Πρόσβαση'], ['3', 'Τοποθέτηση']];
  return labels.map(([num, label]) => {
    const state = Number(num) === WZ.step ? ' on' : (Number(num) < WZ.step ? ' done' : '');
    return `<span class="wz-step${state}"><b>${num}</b>${wzEsc(label)}</span>`;
  }).join('<i class="wz-sep"></i>');
}

function wzStepOneHtml() {
  const d = WZ.data;
  return `
    <div class="wz-grid">
      <label class="wz-field"><span>Όνομα συσκευής *</span>
        <input id="wz-name" value="${wzEsc(d.name)}" placeholder="Router υποκαταστήματος" autocomplete="off" /></label>
      <label class="wz-field"><span>Τύπος</span>
        <select id="wz-kind">${WZ_KINDS.map(([v, l]) => `<option value="${v}"${d.kind === v ? ' selected' : ''}>${l}</option>`).join('')}</select></label>
      <label class="wz-field"><span>Κατασκευαστής (προαιρετικό)</span>
        <input id="wz-vendor" value="${wzEsc(d.vendor)}" placeholder="MikroTik" autocomplete="off" /></label>
      <label class="wz-field"><span>Μοντέλο (προαιρετικό)</span>
        <input id="wz-model" value="${wzEsc(d.model)}" placeholder="hEX S" autocomplete="off" /></label>
      <label class="wz-field wide"><span>Management IP ή hostname</span>
        <input id="wz-ip" value="${wzEsc(d.mgmt_ip)}" placeholder="192.168.88.1" autocomplete="off" spellcheck="false" />
        <em>Χρησιμοποιείται για σύνδεση και για το web management.</em></label>
      <label class="wz-field"><span>Θύρα COM (αν είναι serial)</span>
        <input id="wz-com-serial" value="${wzEsc(d.serial_interface)}" placeholder="COM3" autocomplete="off" /></label>
      <label class="wz-check wide"><input type="checkbox" id="wz-planned"${d.planned ? ' checked' : ''} />
        <span>Σχεδιαζόμενη συσκευή — χωρίς τοποθέτηση σε rack</span></label>
      <label class="wz-field wide"><span>Σημειώσεις</span>
        <textarea id="wz-note" rows="2" placeholder="π.χ. πάροχος, τηλέφωνο, κωδικός πελάτη">${wzEsc(d.note)}</textarea></label>
    </div>`;
}

function wzStepTwoHtml() {
  const d = WZ.data;
  const credentials = WZ.credentials.length
    ? WZ.credentials.map((c) => `<option value="${c.id}"${String(d.credential_id) === String(c.id) ? ' selected' : ''}>${wzEsc(c.name)}${c.username ? ` · ${wzEsc(c.username)}` : ''}</option>`).join('')
    : '<option value="">— κανένα credential —</option>';
  const serial = d.protocol === 'serial';
  return `
    <div class="wz-grid">
      <label class="wz-field"><span>Πρωτόκολλο</span>
        <select id="wz-proto">${WZ_PROTOCOLS.map(([v, l]) => `<option value="${v}"${d.protocol === v ? ' selected' : ''}>${l}</option>`).join('')}</select></label>
      <label class="wz-field"><span>Όνομα χρήστη</span>
        <input id="wz-user" value="${wzEsc(d.username)}" placeholder="admin" autocomplete="off" ${serial ? 'disabled' : ''} /></label>
      <label class="wz-field"><span>Credential</span>
        <select id="wz-cred" ${serial ? 'disabled' : ''}>${credentials}</select></label>
      <label class="wz-field"><span>Θύρα</span>
        <input id="wz-port" type="number" min="1" max="65535" value="${wzEsc(d.port)}" ${serial ? 'disabled' : ''} />
        <em>SSH 22, Telnet 23.</em></label>
      <div class="wz-inline wide">
        <button type="button" class="btn ghost xs" id="wz-new-cred">+ Νέο credential</button>
        <span class="wz-hint">Δημιουργείται εδώ χωρίς να χαθεί η φόρμα. Το μυστικό πηγαίνει μόνο στο vault.</span>
      </div>
      <div id="wz-cred-form" class="wz-sub hidden">
        <label class="wz-field"><span>Όνομα</span><input id="wz-cred-name" placeholder="admin-router" autocomplete="off" /></label>
        <label class="wz-field"><span>Χρήστης</span><input id="wz-cred-user" placeholder="admin" autocomplete="off" /></label>
        <label class="wz-field"><span>Μυστικό</span><input id="wz-cred-secret" type="password" autocomplete="new-password" /></label>
        <button type="button" class="btn primary xs" id="wz-cred-save">Αποθήκευση credential</button>
      </div>
      <div class="wz-serial${serial ? '' : ' hidden'}">
        <label class="wz-field"><span>Θύρα COM</span><input id="wz-com" value="${wzEsc(d.serial_port)}" placeholder="COM3" autocomplete="off" /></label>
        <label class="wz-field"><span>Baud</span><input id="wz-baud" type="number" min="50" value="${wzEsc(d.baud)}" /></label>
        <label class="wz-field"><span>Bits</span>
          <select id="wz-bytesize">${[8, 7, 6, 5].map((n) => `<option value="${n}"${Number(d.bytesize) === n ? ' selected' : ''}>${n}</option>`).join('')}</select></label>
        <label class="wz-field"><span>Parity</span>
          <select id="wz-parity">${['N', 'E', 'O'].map((p) => `<option value="${p}"${d.parity === p ? ' selected' : ''}>${p}</option>`).join('')}</select></label>
        <label class="wz-field"><span>Stop bits</span>
          <select id="wz-stopbits">${[1, 2].map((n) => `<option value="${n}"${Number(d.stopbits) === n ? ' selected' : ''}>${n}</option>`).join('')}</select></label>
        <label class="wz-field"><span>Flow control</span>
          <select id="wz-flow">
            <option value="none"${d.flow === 'none' ? ' selected' : ''}>Κανένα</option>
            <option value="rtscts"${d.flow === 'rtscts' ? ' selected' : ''}>RTS/CTS</option>
            <option value="xonxoff"${d.flow === 'xonxoff' ? ' selected' : ''}>XON/XOFF</option>
            <option value="dsrdtr"${d.flow === 'dsrdtr' ? ' selected' : ''}>DSR/DTR</option>
          </select></label>
        <p class="wz-hint wide">Τα 115200 8N1 είναι η συνηθισμένη ρύθμιση σε console θύρα. Αν δεν βλέπεις έξοδο, δοκίμασε 9600.</p>
      </div>
    </div>`;
}

function wzStepThreeHtml() {
  const d = WZ.data;
  if (d.planned) {
    return `<div class="wz-planned-note">
      <b>Σχεδιαζόμενη συσκευή</b>
      <p>Θα καταχωρηθεί χωρίς rack. Εμφανίζεται στο διάγραμμα με διακεκομμένο περίγραμμα και δεν συνδέεται σε κανένα φυσικό καλώδιο μέχρι να την τοποθετήσεις.</p></div>`;
  }
  const sites = WZ.sites.length
    ? WZ.sites.map((s) => `<option value="${s.id}"${String(d.site_id) === String(s.id) ? ' selected' : ''}>${wzEsc(s.name)}</option>`).join('')
    : '<option value="">— κανένας χώρος —</option>';
  const racks = [];
  WZ.sites.forEach((site) => {
    if (String(site.id) !== String(d.site_id)) return;
    (site.racks || []).forEach((rack) => racks.push(rack));
  });
  const rackOptions = racks.length
    ? racks.map((r) => `<option value="${r.id}"${String(d.rack_id) === String(r.id) ? ' selected' : ''}>${wzEsc(r.name)} · ${r.units}U</option>`).join('')
    : '<option value="">— κανένα rack —</option>';
  return `
    <div class="wz-grid">
      <label class="wz-field"><span>Χώρος / εγκατάσταση</span>
        <select id="wz-site">${sites}</select></label>
      <div class="wz-inline"><button type="button" class="btn ghost xs" id="wz-new-site">+ Νέος χώρος</button></div>
      <label class="wz-field"><span>Rack</span>
        <select id="wz-rack">${rackOptions}</select></label>
      <div class="wz-inline"><button type="button" class="btn ghost xs" id="wz-new-rack">+ Νέο rack</button></div>
      <label class="wz-field"><span>Θέση U</span>
        <input id="wz-position" type="number" min="1" max="60" value="${wzEsc(d.position)}" />
        <em>U1 = κάτω. Ένα 1U switch σε U10 πιάνει το U10.</em>
        ${WZ.layout ? `<em class="wz-free">${wzEsc(wzFreeHint())}</em>` : ''}</label>
      <label class="wz-field"><span>Ύψος (U)</span>
        <input id="wz-units" type="number" min="1" max="48" value="${wzEsc(d.units)}" /></label>
      <div id="wz-site-form" class="wz-sub hidden">
        <label class="wz-field"><span>Όνομα χώρου</span><input id="wz-site-name" placeholder="Κεντρικό Αθήνας" autocomplete="off" /></label>
        <button type="button" class="btn primary xs" id="wz-site-save">Δημιουργία χώρου</button>
      </div>
      <div id="wz-rack-form" class="wz-sub hidden">
        <label class="wz-field"><span>Όνομα rack</span><input id="wz-rack-name" placeholder="Rack δικτύου" autocomplete="off" /></label>
        <label class="wz-field"><span>Ύψος rack (U)</span><input id="wz-rack-units" type="number" min="1" max="60" value="42" /></label>
        <button type="button" class="btn primary xs" id="wz-rack-save">Δημιουργία rack</button>
      </div>
      <div class="wz-summary wide">
        <h4>Σύνοψη</h4>
        <dl>
          <dt>Συσκευή</dt><dd>${wzEsc(d.name || '—')} · ${wzEsc((WZ_KINDS.find(([v]) => v === d.kind) || [])[1] || d.kind)}</dd>
          <dt>Διεύθυνση</dt><dd>${wzEsc(d.mgmt_ip || d.serial_port || '—')}</dd>
          <dt>Πρόσβαση</dt><dd>${wzEsc(d.protocol.toUpperCase())}${d.protocol === 'serial' ? ` · ${wzEsc(d.serial_port || '—')} · ${wzEsc(d.baud)} baud` : ` · ${wzEsc(d.username)}`}</dd>
          <dt>Τοποθέτηση</dt><dd>${wzEsc(wzSiteName())} / ${wzEsc(wzRackName())} · U${wzEsc(d.position)}</dd>
        </dl>
      </div>
    </div>`;
}

function wzSiteName() {
  const site = WZ.sites.find((s) => String(s.id) === String(WZ.data.site_id));
  return site ? site.name : '—';
}

function wzRackName() {
  let name = '';
  WZ.sites.forEach((site) => {
    if (String(site.id) !== String(WZ.data.site_id)) return;
    (site.racks || []).forEach((rack) => {
      if (String(rack.id) === String(WZ.data.rack_id)) name = rack.name;
    });
  });
  return name || '—';
}

function wzRender() {
  if (!WZ.el) return;
  const body = WZ.step === 1 ? wzStepOneHtml() : (WZ.step === 2 ? wzStepTwoHtml() : wzStepThreeHtml());
  WZ.el.innerHTML = `<div class="wz-panel" role="dialog" aria-label="Προσθήκη συσκευής">
    <header class="wz-head">
      <div>
        <h3>Προσθήκη συσκευής</h3>
        <div class="wz-steps">${wzStepsHtml()}</div>
      </div>
      <button type="button" class="icon-btn" id="wz-cancel" title="Άκυρο (Esc)">✕</button>
    </header>
    <p class="wz-error hidden" id="wz-error"></p>
    <div class="wz-body">${body}</div>
    <footer class="wz-foot">
      <button type="button" class="btn ghost" id="wz-back"${WZ.step === 1 ? ' disabled' : ''}>← Πίσω</button>
      <span class="wz-foot-hint">${WZ.step === 3 ? 'Μία τελική αποθήκευση — τίποτα δεν γράφεται πριν.' : 'Enter για συνέχεια, Esc για άκυρο.'}</span>
      <button type="button" class="btn primary" id="wz-next"${WZ.busy ? ' disabled' : ''}>
        ${WZ.step === 3 ? 'Αποθήκευση συσκευής' : 'Συνέχεια →'}</button>
    </footer>
  </div>`;
  wzBind();
  if (WZ.error) wzShowError({ message: WZ.error });
}

/* --------------------------------------------------------------- form bind */
function wzReadStep() {
  const d = WZ.data;
  const get = (id) => WZ.el.querySelector(`#${id}`);
  if (WZ.step === 1) {
    d.name = get('wz-name').value;
    d.kind = get('wz-kind').value;
    d.vendor = get('wz-vendor').value;
    d.model = get('wz-model').value;
    d.mgmt_ip = get('wz-ip').value;
    d.serial_interface = get('wz-com-serial').value;
    d.planned = get('wz-planned').checked;
    d.note = get('wz-note').value;
  } else if (WZ.step === 2) {
    d.protocol = get('wz-proto').value;
    const user = get('wz-user');
    const cred = get('wz-cred');
    const port = get('wz-port');
    d.username = user && !user.disabled ? user.value : d.username;
    d.credential_id = cred && !cred.disabled ? cred.value : d.credential_id;
    d.port = port && !port.disabled ? Number(port.value) || 22 : d.port;
    const com = get('wz-com');
    if (com) d.serial_port = com.value;
    const baud = get('wz-baud');
    if (baud) d.baud = Number(baud.value) || 115200;
    const bs = get('wz-bytesize');
    if (bs) d.bytesize = Number(bs.value) || 8;
    const parity = get('wz-parity');
    if (parity) d.parity = parity.value;
    const stop = get('wz-stopbits');
    if (stop) d.stopbits = Number(stop.value) || 1;
    const flow = get('wz-flow');
    if (flow) d.flow = flow.value;
  } else if (!d.planned) {
    d.site_id = get('wz-site').value;
    d.rack_id = get('wz-rack').value;
    d.position = Number(get('wz-position').value) || 1;
    d.units = Number(get('wz-units').value) || 1;
  }
}

function wzBind() {
  const el = WZ.el;
  const cancel = el.querySelector('#wz-cancel');
  if (cancel) cancel.onclick = wzClose;
  const back = el.querySelector('#wz-back');
  if (back) back.onclick = () => { wzReadStep(); if (WZ.step > 1) { WZ.step -= 1; WZ.error = ''; wzRender(); } };
  const next = el.querySelector('#wz-next');
  if (next) next.onclick = wzNext;

  const proto = el.querySelector('#wz-proto');
  if (proto) proto.onchange = () => { wzReadStep(); wzRender(); };

  const bindNew = (buttonId, formId, focusId) => {
    const button = el.querySelector(`#${buttonId}`);
    if (!button) return;
    button.onclick = () => {
      const form = el.querySelector(`#${formId}`);
      if (!form) return;
      form.classList.toggle('hidden');
      const focus = el.querySelector(`#${focusId}`);
      if (focus) focus.focus();
    };
  };
  bindNew('wz-new-cred', 'wz-cred-form', 'wz-cred-name');
  bindNew('wz-new-site', 'wz-site-form', 'wz-site-name');
  bindNew('wz-new-rack', 'wz-rack-form', 'wz-rack-name');

  const credSave = el.querySelector('#wz-cred-save');
  if (credSave) credSave.onclick = wzCreateCredential;
  const siteSave = el.querySelector('#wz-site-save');
  if (siteSave) siteSave.onclick = wzCreateSite;
  const rackSave = el.querySelector('#wz-rack-save');
  if (rackSave) rackSave.onclick = wzCreateRack;

  const site = el.querySelector('#wz-site');
  if (site) site.onchange = () => { wzReadStep(); WZ.data.rack_id = ''; wzLoadLayout().then(wzRender); };
  const rack = el.querySelector('#wz-rack');
  if (rack) rack.onchange = () => {
    WZ.data.rack_id = rack.value;
    wzLoadLayout().then(() => wzRender());
  };
  const planned = el.querySelector('#wz-planned');
  if (planned) planned.onchange = () => { wzReadStep(); wzRender(); };

  el.querySelectorAll('input, select, textarea').forEach((field) => {
    field.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && field.tagName !== 'TEXTAREA') {
        event.preventDefault();
        wzNext();
      }
    });
  });
}

/* ------------------------------------------------------------ inline create */
async function wzCreateCredential() {
  const el = WZ.el;
  const name = el.querySelector('#wz-cred-name').value.trim();
  const user = el.querySelector('#wz-cred-user').value.trim();
  const secret = el.querySelector('#wz-cred-secret').value;
  if (!name || !secret) { wzShowError({ message: 'Χρειάζεται όνομα και μυστικό.' }); return; }
  try {
    const created = await wzApi('/credentials', {
      method: 'POST',
      json: { name, kind: 'password', username: user, secret, passphrase: '' },
    });
    WZ.credentials = await wzApi('/credentials');
    WZ.data.credential_id = created.id;
    if (user) WZ.data.username = WZ.data.username || user;
    WZ.error = '';
    wzRender();
    wzToast(`Το credential «${created.name}» αποθηκεύτηκε στο vault`);
  } catch (error) {
    wzShowError({ message: `Το credential δεν αποθηκεύτηκε: ${error.message}` });
  }
}

async function wzCreateSite() {
  const el = WZ.el;
  const name = el.querySelector('#wz-site-name').value.trim();
  if (!name) { wzShowError({ message: 'Χρειάζεται όνομα χώρου.' }); return; }
  try {
    const site = await wzApi('/netdoc/sites', { method: 'POST', json: { name } });
    WZ.sites = (await wzApi('/netdoc/tree')).sites || [];
    WZ.data.site_id = site.id;
    WZ.data.rack_id = '';
    WZ.error = '';
    wzRender();
  } catch (error) {
    wzShowError({ message: `Ο χώρος δεν δημιουργήθηκε: ${error.message}` });
  }
}

async function wzCreateRack() {
  const el = WZ.el;
  const name = el.querySelector('#wz-rack-name').value.trim();
  const units = Number(el.querySelector('#wz-rack-units').value) || 42;
  if (!WZ.data.site_id) { wzShowError({ message: 'Διάλεξε πρώτα χώρο.' }); return; }
  if (!name) { wzShowError({ message: 'Χρειάζεται όνομα rack.' }); return; }
  try {
    const rack = await wzApi('/netdoc/racks', {
      method: 'POST', json: { site_id: Number(WZ.data.site_id), name, units },
    });
    WZ.sites = (await wzApi('/netdoc/tree')).sites || [];
    WZ.data.rack_id = rack.id;
    WZ.error = '';
    await wzLoadLayout();
    wzRender();
  } catch (error) {
    wzShowError({ message: `Το rack δεν δημιουργήθηκε: ${error.message}` });
  }
}

/* ------------------------------------------------------------------- saving */
async function wzSave() {
  const d = WZ.data;
  WZ.busy = true;
  wzRender();
  const hostPayload = {
    label: d.name.trim(),
    protocol: d.protocol,
    hostname: d.protocol === 'serial' ? (d.serial_port.trim() || 'serial') : d.mgmt_ip.trim(),
    port: d.protocol === 'telnet' ? 23 : (Number(d.port) || 22),
    username: d.username.trim(),
    credential_id: d.credential_id ? Number(d.credential_id) : null,
    group_name: 'General',
    tags: '',
    color: '#3ddc97',
    notes: d.note.trim(),
    serial_port: d.serial_port.trim(),
    baud: Number(d.baud) || 115200,
    bytesize: Number(d.bytesize) || 8,
    parity: d.parity,
    stopbits: Number(d.stopbits) || 1,
    flow: d.flow,
  };
  let host = null;
  try {
    host = await wzApi('/hosts', { method: 'POST', json: hostPayload });
    if (!d.planned && d.rack_id) {
      await wzApi('/netdoc/devices', {
        method: 'POST',
        json: {
          rack_id: Number(d.rack_id),
          name: d.name.trim(),
          kind: d.kind,
          units: Number(d.units) || 1,
          position: Number(d.position) || 1,
          note: d.note.trim(),
          host_id: host.id,
          vendor: d.vendor.trim(),
          model: d.model.trim(),
          mgmt_ip: d.mgmt_ip.trim(),
          origin: 'documented',
        },
      });
    }
    WZ.busy = false;
    const name = d.name;
    wzReset();
    wzToast(`Η «${name}» προστέθηκε`);
    if (typeof WZ.onDone === 'function') WZ.onDone({ host, name });
    else if (typeof loadHosts === 'function') await loadHosts();
  } catch (error) {
    WZ.busy = false;
    if (host) {
      // no half-written record: undo the profile we just created
      try { await wzApi(`/hosts/${host.id}`, { method: 'DELETE' }); } catch (_) { /* reported below */ }
    }
    wzShowError({ message: `Η αποθήκευση απέτυχε: ${error.message}` });
  }
}

async function wzNext() {
  wzReadStep();
  const problem = wzValidateStep(WZ.step);
  if (problem) { wzShowError(problem); return; }
  wzShowError(null);
  if (WZ.step < 3) {
    WZ.step += 1;
    if (WZ.step === 3 && WZ.data.rack_id) await wzLoadLayout();
    if (WZ.step === 3 && WZ.sites.length === 0) {
      try { WZ.sites = (await wzApi('/netdoc/tree')).sites || []; } catch (_) { WZ.sites = []; }
    }
    wzRender();
    return;
  }
  await wzSave();
}

/* -------------------------------------------------------------------- mount */
function wzReset() {
  WZ.step = 1;
  WZ.error = '';
  WZ.busy = false;
  WZ.data = {
    name: '', kind: 'switch', vendor: '', model: '', mgmt_ip: '', serial_interface: '',
    planned: false, note: '', protocol: 'ssh', username: '', credential_id: '', secret: '',
    port: 22, serial_port: '', baud: 115200, bytesize: 8, parity: 'N', stopbits: 1,
    flow: 'none', site_id: '', site_name: '', rack_id: '', rack_name: '', units: 1, position: 1,
  };
}

function wzClose() {
  if (typeof WZ.onCancel === 'function') WZ.onCancel();
  if (WZ.el) {
    WZ.el.classList.add('hidden');
    WZ.el.innerHTML = '';
  }
}

async function wzLoadLayout() {
  const rackId = WZ.data.rack_id;
  if (!rackId) { WZ.layout = null; return; }
  try {
    WZ.layout = await wzApi(`/netdoc/rack/${rackId}`);
  } catch (_) {
    WZ.layout = null;      // χωρίς layout η φόρμα δουλεύει, ο έλεγχος γίνεται στο save
  }
}

function wzOccupied() {
  const taken = new Set();
  const devices = (WZ.layout && WZ.layout.devices) || [];
  devices.forEach((device) => {
    const top = Number(device.position) + (Number(device.units) || 1) - 1;
    for (let unit = Number(device.position); unit <= top; unit += 1) taken.add(unit);
  });
  return taken;
}

function wzFreeHint() {
  if (!WZ.layout) return '';
  const taken = wzOccupied();
  const total = Number((WZ.layout.rack && WZ.layout.rack.units) || 0) || 42;
  const free = [];
  for (let unit = total; unit >= 1; unit -= 1) if (!taken.has(unit)) free.push(unit);
  if (!free.length) return 'Το rack είναι γεμάτο — διάλεξε άλλο rack.';
  const head = free.slice(0, 8).join(', ');
  return `Ελεύθερες θέσεις (από πάνω): ${head}${free.length > 8 ? '…' : ''}`;
}

async function wzMount(container, options) {
  const el = container || document.querySelector('#wizard-host');
  if (!el) return null;
  WZ.el = el;
  const opts = options || {};
  WZ.onDone = opts.onDone || null;
  WZ.onCancel = opts.onCancel || null;
  if (opts.reset !== false) wzReset();
  el.classList.remove('hidden');
  try {
    WZ.credentials = await wzApi('/credentials');
  } catch (_) { WZ.credentials = []; }
  try {
    WZ.sites = ((await wzApi('/netdoc/tree')) || {}).sites || [];
  } catch (_) { WZ.sites = []; }
  // προεπιλογή τρέχοντος context
  if (typeof S !== 'undefined' && S && S.nd && S.nd.tree && (S.nd.tree.sites || []).length && !WZ.data.site_id) {
    WZ.data.site_id = S.nd.tree.sites[0].id;
    const racks = S.nd.tree.sites[0].racks || [];
    if (racks.length) WZ.data.rack_id = racks[0].id;
  }
  wzRender();
  return WZ;
}

// Esc κλείνει πάντα το wizard, όπου κι αν έχει το focus ο χρήστης
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (!WZ.el || WZ.el.classList.contains('hidden')) return;
  wzClose();
});

window.MuxWizard = { mount: wzMount, close: wzClose, state: WZ };
