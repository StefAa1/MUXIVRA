/* ------------------------------------------------------------------ racks
   NETDOC-style documentation: sites → racks → devices → ports → links.
   A port can be in one link only, so duplicate assignments are refused. */

async function loadNetdoc() {
  S.nd = S.nd || { selectedRack: null, ports: {} };
  try {
    S.nd.tree = await api('/netdoc/tree');
    S.nd.links = await api('/netdoc/links');
  } catch (err) {
    S.nd.tree = { sites: [], links: 0 };
    S.nd.links = [];
  }
}

function ndRacks() {
  return ((S.nd && S.nd.tree && S.nd.tree.sites) || []).flatMap((site) => site.racks.map((r) => ({ ...r, site: site.name })));
}

async function ndSelectRack(rackId) {
  S.nd.selectedRack = rackId;
  const rack = ndRacks().find((r) => r.id === rackId);
  S.nd.ports = {};
  if (rack) {
    await Promise.all(rack.devices.map(async (device) => {
      S.nd.ports[device.id] = await api(`/netdoc/devices/${device.id}/ports`);
    }));
  }
  renderPage();
}

function renderRacks() {
  const nd = S.nd || { tree: { sites: [] }, links: [], selectedRack: null, ports: {} };
  const sites = (nd.tree && nd.tree.sites) || [];
  const racks = ndRacks();
  const rack = racks.find((r) => r.id === nd.selectedRack) || null;

  const treeHtml = sites.length ? sites.map((site) => `
      <div class="nd-site">
        <div class="nd-site-head"><span>${esc(site.name)}</span></div>
        ${site.racks.length ? site.racks.map((r) => `
          <button class="nd-rack${r.id === nd.selectedRack ? ' on' : ''}" data-nd-rack="${r.id}">
            <b>${esc(r.name)}</b><span>${r.devices.length} συσκευές · ${r.units}U</span>
          </button>`).join('') : '<p class="hint">χωρίς racks</p>'}
      </div>`).join('') : '<p class="hint">Κανένας χώρος ακόμα.</p>';

  const siteOptions = sites.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('');

  const devicesHtml = rack ? rack.devices.map((device) => {
    const ports = nd.ports[device.id] || [];
    return `<tr>
        <td><b>${esc(device.name)}</b></td>
        <td class="mono">${esc(device.kind)}</td>
        <td class="mono">U${device.position}${device.units > 1 ? '–' + (device.position + device.units - 1) : ''}</td>
        <td class="mono">${ports.length ? ports.map((p) => `${esc(p.name)}${p.link_id ? '·' : ''}`).join(' ') : '—'}</td>
        <td><button class="btn ghost xs" data-nd-ports="${device.id}">+24 eth</button></td>
      </tr>`;
  }).join('') : '';

  const portOptions = rack ? rack.devices.flatMap((device) =>
    (nd.ports[device.id] || []).map((p) => `<option value="${p.id}">${esc(device.name)} · ${esc(p.name)}</option>`)
  ).join('') : '';

  const linksHtml = (nd.links || []).slice(0, 40).map((link) => `
      <tr><td>${esc(link.device_a)} · ${esc(link.port_a_name)}</td>
      <td class="mono">→</td>
      <td>${esc(link.device_b)} · ${esc(link.port_b_name)}</td>
      <td class="mono">${esc(link.cable) || '—'}</td>
      <td class="mono">${link.length_m ? link.length_m + 'm' : '—'}</td></tr>`).join('');

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
          <select id="nd-rack-site">${siteOptions}</select>
          <input id="nd-rack-name" placeholder="Rack" autocomplete="off" />
          <button class="btn ghost xs" id="nd-add-rack">+</button>
        </div>
      </aside>

      <div class="panel nd-main">
        ${rack ? `
          <div class="nd-main-head">
            <div><h2>${esc(rack.name)}</h2><p class="hint">${esc(rack.site)} · ${rack.units}U</p></div>
            <div class="nd-form">
              <input id="nd-dev-name" placeholder="Συσκευή" autocomplete="off" />
              <select id="nd-dev-kind">
                <option value="switch">switch</option><option value="patch-panel">patch-panel</option>
                <option value="router">router</option><option value="server">server</option>
                <option value="pdu">pdu</option><option value="other">other</option>
              </select>
              <input id="nd-dev-pos" type="number" min="1" max="60" value="1" title="U θέση" />
              <button class="btn primary xs" id="nd-add-dev">+ Συσκευή</button>
            </div>
          </div>
          <table class="nd-table">
            <thead><tr><th>Συσκευή</th><th>Τύπος</th><th>Θέση</th><th>Ports</th><th></th></tr></thead>
            <tbody>${devicesHtml || '<tr><td colspan="5" class="hint">Καμία συσκευή.</td></tr>'}</tbody>
          </table>
          <div class="nd-form link">
            <select id="nd-port-a">${portOptions || '<option value="">—</option>'}</select>
            <span class="mono">↔</span>
            <select id="nd-port-b">${portOptions || '<option value="">—</option>'}</select>
            <input id="nd-cable" placeholder="Καλώδιο (CAT6-01)" autocomplete="off" />
            <input id="nd-length" type="number" min="0" step="0.5" placeholder="m" />
            <button class="btn primary xs" id="nd-link">Σύνδεση</button>
          </div>` : '<p class="hint">Διάλεξε rack από αριστερά ή δημιούργησε χώρο και rack.</p>'}

        <h2 class="nd-sub">Συνδέσεις (${(S.nd && S.nd.tree && S.nd.tree.links) || 0})</h2>
        <table class="nd-table">
          <thead><tr><th>Α</th><th></th><th>Β</th><th>Καλώδιο</th><th>Μήκος</th></tr></thead>
          <tbody>${linksHtml || '<tr><td colspan="5" class="hint">Καμία σύνδεση ακόμα.</td></tr>'}</tbody>
        </table>
      </div>
    </div>`;
}
