/* --------------------------------------------------------------- NETDOC
   Rack documentation: sites → racks → devices (U positions) → ports → links.
   Views: Διάταξη (visual rack), Συνδέσεις (table), Έλεγχος (audit).
   Quick Connect: click one port, click another → link is created with the
   next free cable number. A port can only appear in one link. */

const ND = { rackId: null, tab: 'layout', layout: null, quick: null, audit: null, deviceId: null };

async function loadNetdoc() {
  S.nd = S.nd || {};
  try {
    S.nd.tree = await api('/netdoc/tree');
    S.nd.links = await api('/netdoc/links');
  } catch (err) {
    S.nd.tree = { sites: [], links: 0 };
    S.nd.links = [];
  }
  if (ND.rackId) {
    try {
      ND.layout = await api(`/netdoc/rack/${ND.rackId}`);
    } catch (_) {
      ND.layout = null;
      ND.rackId = null;
    }
  }
}

function ndRacks() {
  return ((S.nd && S.nd.tree && S.nd.tree.sites) || []).flatMap((site) =>
    site.racks.map((r) => ({ ...r, siteName: site.name })));
}

async function ndSelectRack(rackId) {
  ND.rackId = rackId;
  ND.quick = null;
  ND.layout = await api(`/netdoc/rack/${rackId}`);
  renderPage();
}

function ndNextCable() {
  const used = (S.nd.links || [])
    .map((link) => (link.cable || '').match(/(\d+)\s*$/))
    .filter(Boolean)
    .map((match) => Number(match[1]));
  const next = (used.length ? Math.max(...used) : 0) + 1;
  return `CAT6-${String(next).padStart(2, '0')}`;
}

function ndPortButton(device, port) {
  const selected = ND.quick && ND.quick.portId === port.id;
  const cls = 'nd-port' + (port.link_id ? ' linked' : '') + (selected ? ' sel' : '');
  const title = port.link_id ? `→ ${port.peer || ''}` : 'ελεύθερο';
  return `<button class="${cls}" data-nd-port="${port.id}" data-nd-port-label="${esc(device.name)} · ${esc(port.name)}" title="${esc(title)}">${esc(port.name)}</button>`;
}

function ndLayoutView() {
  const layout = ND.layout;
  if (!layout) return '<p class="hint">Διάλεξε rack από αριστερά.</p>';
  const rack = layout.rack;
  const devices = layout.devices || [];
  const byTopUnit = new Map();
  devices.forEach((device) => byTopUnit.set(device.position + device.units - 1, device));

  const rows = [];
  for (let unit = rack.units; unit >= 1; unit -= 1) {
    const device = byTopUnit.get(unit);
    if (device) {
      const linked = device.ports.filter((p) => p.link_id).length;
      rows.push(`<div class="nd-device ${esc(device.kind)}" style="grid-row: span ${device.units}" data-nd-select="${device.id}" title="U${device.position}">
          <b>${esc(device.name)}</b>
          <span>${esc(device.kind)} · ${device.ports.length} ports${linked ? ` · ${linked} δεμένα` : ''}</span>
        </div>`);
      for (let skip = 1; skip < device.units; skip += 1) {
        rows.push(`<div class="nd-filler"></div>`);
      }
      unit -= device.units - 1;
    } else {
      rows.push(`<div class="nd-empty"></div>`);
    }
  }

  const selectedDevice = devices.find((d) => d.id === ND.deviceId) || devices[0] || null;
  const portList = selectedDevice
    ? `<div class="nd-ports">
        <div class="nd-ports-head">
          <b>${esc(selectedDevice.name)}</b>
          <span class="hint">${selectedDevice.ports.length} ports</span>
          <button class="btn ghost xs" data-nd-ports="${selectedDevice.id}">+24</button>
        </div>
        <div class="nd-port-grid">${selectedDevice.ports.map((p) => ndPortButton(selectedDevice, p)).join('') || '<p class="hint">Χωρίς ports.</p>'}</div>
      </div>`
    : '<p class="hint">Πρόσθεσε συσκευή για να δεις ports.</p>';

  const quickBar = ND.quick
    ? `<div class="nd-quick">Από: <b>${esc(ND.quick.label)}</b> → διάλεξε το ταίρι του
        <input id="nd-quick-cable" value="${esc(ND.quick.cable)}" placeholder="καλώδιο" />
        <button class="btn ghost xs" id="nd-quick-cancel">Άκυρο</button></div>`
    : '<div class="nd-quick idle">Γρήγορη σύνδεση: πάτησε ένα port και μετά το ταίρι του.</div>';

  return `<div class="nd-layout">
      ${quickBar}
      <div class="nd-rack-wrap">
        <div class="nd-rack-units">${Array.from({ length: rack.units }, (_, i) => `<span>U${rack.units - i}</span>`).join('')}</div>
        <div class="nd-rack-view" style="--units:${rack.units}">${rows.join('')}</div>
      </div>
      ${portList}
    </div>`;
}

function ndTableView() {
  const links = (S.nd && S.nd.links) || [];
  const rows = links.map((link) => `<tr>
      <td>${esc(link.device_a)} · ${esc(link.port_a_name)}</td>
      <td class="mono">→</td>
      <td>${esc(link.device_b)} · ${esc(link.port_b_name)}</td>
      <td class="mono">${esc(link.cable) || '—'}</td>
      <td class="mono">${link.length_m ? link.length_m + 'm' : '—'}</td>
      <td class="mono">${esc(link.rack_a)} → ${esc(link.rack_b)}</td>
    </tr>`).join('');
  return `<table class="nd-table">
      <thead><tr><th>Α</th><th></th><th>Β</th><th>Καλώδιο</th><th>Μήκος</th><th>Racks</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="6" class="hint">Καμία σύνδεση ακόμα.</td></tr>'}</tbody>
    </table>`;
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
  const items = audit.issues.map((issue) => `<li class="nd-issue ${esc(issue.kind)}">
      <b>${esc(labels[issue.kind] || issue.kind)}</b>
      <span>${esc(issue.rack)}${issue.device ? ' · ' + esc(issue.device) : ''}</span>
      <em>${esc(issue.detail)}</em></li>`).join('');
  const summary = audit.summary;
  return `<div class="nd-summary">
      <span><b>${summary.racks}</b> racks</span><span><b>${summary.ports}</b> ports</span>
      <span><b>${summary.links}</b> συνδέσεις</span>
      <span class="${summary.issues ? 'warn' : 'ok'}"><b>${summary.issues}</b> ευρήματα</span>
    </div>
    <ul class="nd-issues">${items || '<li class="nd-issue ok"><b>Καθαρό</b><em>Κανένα πρόβλημα.</em></li>'}</ul>`;
}

function renderRacks() {
  const nd = S.nd || { tree: { sites: [] }, links: [] };
  const sites = (nd.tree && nd.tree.sites) || [];
  const racks = ndRacks();
  const rack = racks.find((r) => r.id === ND.rackId) || null;
  const tab = ND.tab || 'layout';

  const treeHtml = sites.length ? sites.map((site) => `
      <div class="nd-site">
        <div class="nd-site-head"><span>${esc(site.name)}</span></div>
        ${site.racks.length ? site.racks.map((r) => `
          <button class="nd-rack${r.id === ND.rackId ? ' on' : ''}" data-nd-rack="${r.id}">
            <b>${esc(r.name)}</b><span>${r.devices.length} συσκευές · ${r.units}U</span>
          </button>`).join('') : '<p class="hint">χωρίς racks</p>'}
      </div>`).join('') : '<p class="hint">Κανένας χώρος ακόμα.</p>';

  $('#page-racks').innerHTML = `<div class="page-head">
      <div><h2>Racks &amp; Καλώδια</h2><p>Χώροι, racks, συσκευές, ports και συνδέσεις — με έλεγχο διπλών ports.</p></div>
      <div class="actions">
        <a class="btn ghost xs" href="https://netdoc.app" target="_blank" rel="noopener noreferrer">netdoc.app ↗</a>
        <a class="btn ghost xs" href="/api/netdoc/links.csv">CSV</a>
      </div>
    </div>
    <div class="nd-split">
      <aside class="panel nd-tree">
        <h2>Χώροι</h2>
        ${treeHtml}
        <div class="nd-form">
          <input id="nd-site-name" placeholder="Νέος χώρος" autocomplete="off" />
          <button class="btn ghost xs" id="nd-add-site">+</button>
        </div>
        <div class="nd-form">
          <select id="nd-rack-site" aria-label="Χώρος">${sites.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('')}</select>
          <input id="nd-rack-name" placeholder="Rack" autocomplete="off" />
          <button class="btn ghost xs" id="nd-add-rack">+</button>
        </div>
      </aside>

      <div class="panel nd-main">
        <div class="nd-tabs">
          <button class="nd-tab${tab === 'layout' ? ' on' : ''}" data-nd-tab="layout">Διάταξη</button>
          <button class="nd-tab${tab === 'table' ? ' on' : ''}" data-nd-tab="table">Συνδέσεις (${(nd.tree && nd.tree.links) || 0})</button>
          <button class="nd-tab${tab === 'audit' ? ' on' : ''}" data-nd-tab="audit">Έλεγχος</button>
          <span class="spacer"></span>
          ${rack ? `<span class="hint">${esc(rack.siteName)} · ${esc(rack.name)}</span>` : ''}
        </div>
        ${rack && tab === 'layout'
          ? `<div class="nd-form">
              <input id="nd-dev-name" placeholder="Συσκευή" autocomplete="off" />
              <select id="nd-dev-kind">
                <option value="switch">switch</option><option value="patch-panel">patch-panel</option>
                <option value="router">router</option><option value="server">server</option>
                <option value="pdu">pdu</option><option value="other">other</option>
              </select>
              <input id="nd-dev-pos" type="number" min="1" max="60" value="1" title="U θέση" />
              <button class="btn primary xs" id="nd-add-dev">+ Συσκευή</button>
            </div>`
          : ''}
        ${tab === 'layout' ? ndLayoutView() : tab === 'table' ? ndTableView() : ndAuditView()}
      </div>
    </div>`;
}

async function ndAudit() {
  ND.audit = await api('/netdoc/audit');
  return ND.audit;
}

async function ndQuickPort(portId, label) {
  if (!ND.quick) {
    ND.quick = { portId, label, cable: ndNextCable() };
    renderPage();
    return;
  }
  if (ND.quick.portId === portId) {
    ND.quick = null;
    renderPage();
    return;
  }
  const input = document.querySelector('#nd-quick-cable');
  const cable = (input && input.value.trim()) || ND.quick.cable;
  try {
    await api('/netdoc/links', { json: { port_a: ND.quick.portId, port_b: portId, cable } });
    ND.quick = null;
    await loadNetdoc();
    renderPage();
    toast('Η σύνδεση καταγράφηκε');
  } catch (err) {
    toast(err.message.startsWith('port_already_linked') ? 'Το port είναι ήδη συνδεδεμένο' : err.message, 'err');
  }
}
