/* -------------------------------------------------------- MUXIVRA · diagram
   Block diagram of the documented network.

   Contract with the rest of the app (no other module changes required):
     • nodes  = devices from GET /api/inventory/tree (sites → racks → devices)
     • edges  = recorded port↔port links from GET /api/inventory/links/page
                (limit 500, paginated until `total` is reached)
     • nothing is inferred from IP addresses or naming conventions.

   The view is hand-written SVG (no library, no CDN) laid out in site/rack
   columns, with pointer drag, wheel zoom, background pan and fit-to-view.
   Node positions live in localStorage under `muxivra.diagram.positions`.

   Entry point: renderDiagram(force) — called by the shell and by the hooks at
   the bottom of this file when the #page-diagram section becomes visible.
*/
'use strict';

function dgCustomerId() {
  try {
    if (typeof S !== 'undefined' && S && S.customerId) return String(S.customerId);
    const select = document.querySelector('#customer-select');
    if (select && select.value) return String(select.value);
  } catch (_) { /* rendering before the shell is ready */ }
  return 'all';
}

function dgKey() {
  return `muxivra.diagram.positions.${dgCustomerId()}.${DG.dataset || 'documented'}`;
}

const DG = {
  KEY: 'muxivra.diagram.positions',
  dataset: 'documented',       // documented (real inventory) | planned (design)
  planned: [],                 // planned links: design intent, NOT physical cables
  NS: 'http://www.w3.org/2000/svg',
  W: 196,              // node width
  H: 54,               // node height
  COL_GAP: 58,
  ROW_GAP: 42,         // vertical room for edge labels + self-loops
  TOP: 122,            // first node row: leaves space for band + rack labels
  GROUP_PAD: 112,      // band header height above the first row
  MAX_PER_COL: 14,     // taller racks continue in a neighbouring column
  el: null,
  svg: null,
  viewport: null,
  layers: null,
  nodes: [],
  edgeList: [],
  byId: null,
  nodeById: null,
  edgeById: null,
  inc: null,
  nodeEls: null,
  edgeEls: null,
  columns: [],
  bandsData: [],
  links: [],
  treeRef: null,
  rev: 0,
  paintedSig: '',
  filters: { site: 'all', rack: 'all', q: '', linkedOnly: false },
  pos: null,
  view: { x: 0, y: 0, k: 1 },
  selected: null,
  built: false,
  busy: null,
  drag: null,
  pan: null,
  fitted: false,
  hideLabels: false,
  rackCache: null,
};

const DG_KINDS = ['switch', 'router', 'firewall', 'ap', 'patch-panel', 'server', 'pdu', 'other'];
const DG_KIND_LABEL = {
  switch: 'switch',
  router: 'router',
  firewall: 'firewall',
  ap: 'access point',
  'patch-panel': 'patch-panel',
  server: 'server',
  pdu: 'pdu',
  other: 'other',
};

/* --------------------------------------------------------------- utilities */
function dgEsc(value) {
  if (typeof esc === 'function') return esc(value);
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* app.js declares `const S` — classic scripts share that lexical binding but it
   is NOT a property of window, so `window.S` would always be undefined. */
function dgApp() {
  return typeof S !== 'undefined' && S ? S : null;
}

function dgClamp(n, min, max) { return Math.min(max, Math.max(min, n)); }
function dgRound(n) { return Math.round(n * 100) / 100; }
function dgClipText(value, max) {
  const text = String(value == null ? '' : value);
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}
function dgDebounce(fn, ms) {
  let timer = 0;
  return function dgDebounced(...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), ms);
  };
}

function dgSvg(tag, attrs, text) {
  const el = document.createElementNS(DG.NS, tag);
  if (attrs) {
    Object.keys(attrs).forEach((key) => {
      if (attrs[key] != null) el.setAttribute(key, attrs[key]);
    });
  }
  if (text != null) el.textContent = text;
  return el;
}

/* -------------------------------------------------------------- data access */
function dgSites() {
  const app = dgApp();
  return (app && app.nd && app.nd.tree && Array.isArray(app.nd.tree.sites)) ? app.nd.tree.sites : [];
}

function dgDevices() {
  const out = [];
  dgSites().forEach((site) => {
    (site.racks || []).forEach((rack) => {
      (rack.devices || []).forEach((dev) => {
        const kind = DG_KINDS.indexOf(dev.kind) >= 0 ? dev.kind : 'other';
        out.push({
          id: Number(dev.id),
          name: dev.name || `device ${dev.id}`,
          kind,
          units: Number(dev.units) || 1,
          position: Number(dev.position) || 1,
          vendor: dev.vendor || '',
          model: dev.model || '',
          mgmt_ip: dev.mgmt_ip || '',
          origin: (dev.origin === 'planned') ? 'planned' : 'documented',
          observed: !!dev.observed_at,
          siteId: site.id,
          siteName: site.name || 'No site',
          rackId: rack.id,
          rackName: rack.name || 'Rack',
        });
      });
    });
  });
  return out;
}

function dgRacks() {
  const out = [];
  dgSites().forEach((site) => {
    (site.racks || []).forEach((rack) => {
      out.push({
        id: rack.id,
        name: rack.name || 'Rack',
        units: Number(rack.units) || 0,
        siteId: site.id,
        siteName: site.name || 'No site',
        devices: (rack.devices || []).length,
      });
    });
  });
  return out;
}

function dgPositions() {
  if (DG.pos) return DG.pos;
  DG.pos = {};
  try {
    const raw = window.localStorage.getItem(dgKey());
    const parsed = raw ? JSON.parse(raw) : null;
    if (parsed && typeof parsed === 'object') {
      Object.keys(parsed).forEach((key) => {
        const point = parsed[key];
        const x = Number(point && point.x);
        const y = Number(point && point.y);
        if (Number.isFinite(x) && Number.isFinite(y)) DG.pos[key] = { x, y };
      });
    }
  } catch (_) {
    DG.pos = {};
  }
  return DG.pos;
}

function dgWritePositions() {
  try {
    window.localStorage.setItem(dgKey(), JSON.stringify(dgPositions()));
  } catch (_) {
    /* storage disabled or full — dragging still works for this session */
  }
}

async function dgReload(force) {
  // A refresh must re-read the tree as well: devices/racks can be added while
  // the diagram is open. loadInventory() refetches S.nd.tree and S.nd.links.
  if ((force || !dgSites().length) && typeof loadInventory === 'function') {
    try { await loadInventory(); } catch (_) { /* handled below via empty tree */ }
  }
  const app = dgApp();
  const tree = (app && app.nd && app.nd.tree) ? app.nd.tree : { sites: [] };
  if (!force && DG.links.length && DG.treeRef === tree) return;
  DG.treeRef = tree;
  DG.rackCache = new Map();

  let links = [];
  let lastError = null;
  try {
    DG.planned = await api('/inventory/planned-links');
  } catch (_) {
    DG.planned = [];        // design links are optional; cables still render
  }
  let loaded = false;
  try {
    let offset = 0;
    for (let page = 0; page < 40; page += 1) {
      const res = await api(`/inventory/links/page?limit=500&offset=${offset}`);
      const batch = (res && Array.isArray(res.links)) ? res.links : [];
      links = links.concat(batch);
      loaded = true;
      const total = Number(res && res.total);
      offset += batch.length;
      if (!batch.length || (Number.isFinite(total) && offset >= total)) break;
    }
  } catch (err) {
    lastError = err;
    loaded = false;
  }

  if (!loaded) {
    const fallback = (app && app.nd && Array.isArray(app.nd.links)) ? app.nd.links : [];
    if (!fallback.length) {
      DG.links = [];
      DG.rev += 1;
      throw lastError || new Error('links_unavailable');
    }
    links = fallback;
  }

  DG.links = links.filter((link) => link
    && Number.isFinite(Number(link.device_a_id))
    && Number.isFinite(Number(link.device_b_id)));
  DG.rev += 1;
}

/* ------------------------------------------------------------------- layout */
function dgAutoLayout(nodes) {
  const positions = dgPositions();
  const rackOrder = dgRacks();
  const buckets = new Map();

  nodes.forEach((node) => {
    const key = `${node.siteId}:${node.rackId}`;
    if (!buckets.has(key)) {
      buckets.set(key, {
        key,
        siteId: node.siteId,
        siteName: node.siteName,
        rackId: node.rackId,
        rackName: node.rackName,
        nodes: [],
      });
    }
    buckets.get(key).nodes.push(node);
  });

  const ordered = [];
  rackOrder.forEach((rack) => {
    const bucket = buckets.get(`${rack.siteId}:${rack.id}`);
    if (bucket) ordered.push(bucket);
  });
  buckets.forEach((bucket) => {
    if (ordered.indexOf(bucket) < 0) ordered.push(bucket);
  });

  const columns = [];
  ordered.forEach((bucket) => {
    const total = Math.max(1, Math.ceil(bucket.nodes.length / DG.MAX_PER_COL));
    for (let part = 0; part < total; part += 1) {
      columns.push({
        rackId: bucket.rackId,
        rackName: bucket.rackName,
        siteId: bucket.siteId,
        siteName: bucket.siteName,
        part: part + 1,
        parts: total,
        nodes: bucket.nodes.slice(part * DG.MAX_PER_COL, (part + 1) * DG.MAX_PER_COL),
      });
    }
  });

  let x = 0;
  columns.forEach((column) => {
    column.x = x;
    let y = DG.TOP;
    column.nodes.forEach((node) => {
      const saved = positions[node.id];
      node.gx = saved && Number.isFinite(saved.x) ? saved.x : x;
      node.gy = saved && Number.isFinite(saved.y) ? saved.y : y;
      node.defaultX = x;
      node.defaultY = y;
      y += DG.H + DG.ROW_GAP;
    });
    column.bottom = column.nodes.length ? y - DG.ROW_GAP : DG.TOP;
    x += DG.W + DG.COL_GAP;
  });

  // Site bands: consecutive columns belonging to the same site.
  const bands = [];
  let index = 0;
  while (index < columns.length) {
    const siteId = columns[index].siteId;
    let last = index;
    while (last + 1 < columns.length && columns[last + 1].siteId === siteId) last += 1;
    const firstCol = columns[index];
    const lastCol = columns[last];
    let bottom = DG.TOP;
    for (let i = index; i <= last; i += 1) bottom = Math.max(bottom, columns[i].bottom);
    const top = DG.TOP - DG.GROUP_PAD;
    const left = firstCol.x - 44;            // room for self-loop labels
    bands.push({
      x: left,
      y: top,
      w: (lastCol.x + DG.W + 22) - left,
      h: (bottom + 18) - top,
      siteName: firstCol.siteName,
    });
    index = last + 1;
  }

  DG.columns = columns;
  DG.bandsData = bands;
}

/* --------------------------------------------------------------- graph build */
function dgBuild() {
  const all = dgDevices();
  const deviceById = new Map(all.map((device) => [device.id, device]));
  DG.byId = deviceById;

  const links = DG.links.filter((link) => deviceById.has(Number(link.device_a_id))
    && deviceById.has(Number(link.device_b_id)));
  const degree = new Map();
  links.forEach((link) => {
    const a = Number(link.device_a_id);
    const b = Number(link.device_b_id);
    degree.set(a, (degree.get(a) || 0) + 1);
    degree.set(b, (degree.get(b) || 0) + 1);
  });

  const query = String(DG.filters.q || '').trim().toLowerCase();
  const nodes = all.filter((node) => {
    if (DG.dataset === 'documented' && node.origin === 'planned') return false;
    if (DG.filters.site !== 'all' && String(node.siteId) !== String(DG.filters.site)) return false;
    if (DG.filters.rack !== 'all' && String(node.rackId) !== String(DG.filters.rack)) return false;
    if (DG.filters.linkedOnly && !(degree.get(node.id) > 0)) return false;
    if (query) {
      const haystack = [node.name, node.mgmt_ip, node.vendor, node.model, node.kind,
        node.rackName, node.siteName].join(' ').toLowerCase();
      if (haystack.indexOf(query) < 0) return false;
    }
    return true;
  });

  nodes.sort((a, b) => a.siteName.localeCompare(b.siteName, 'el')
    || a.rackName.localeCompare(b.rackName, 'el')
    || b.position - a.position
    || a.name.localeCompare(b.name, 'el'));

  DG.nodes = nodes;
  DG.nodeById = new Map(nodes.map((node) => [node.id, node]));
  // design edges: kept in a separate list so nothing can mistake them for cables
  DG.plannedEdges = (DG.planned || []).filter((link) => DG.nodeById.has(Number(link.from_device))
    && DG.nodeById.has(Number(link.to_device)))
    .map((link) => ({
      id: `planned-${link.id}`,
      planned: true,
      device_a_id: Number(link.from_device),
      device_b_id: Number(link.to_device),
      port_a_name: link.from_port || '',
      port_b_name: link.to_port || '',
      cable: link.label || 'planned',
      label: link.label || 'planned',
      length_m: '',
    }));
  DG.edgeList = links.filter((link) => DG.nodeById.has(Number(link.device_a_id))
    && DG.nodeById.has(Number(link.device_b_id)));
  if (DG.dataset === 'planned') {
    DG.edgeList = DG.plannedEdges.slice();
    DG.edgeById = new Map(DG.edgeList.map((edge) => [edge.id, edge]));
  } else {
    DG.edgeById = new Map(DG.edgeList.map((edge) => [edge.id, edge]));
  }
  DG.nodes.forEach((node) => { node.deg = degree.get(node.id) || 0; });

  // Parallel edges between the same pair get separate bows and labels.
  const pairs = new Map();
  DG.edgeList.forEach((edge) => {
    const a = Number(edge.device_a_id);
    const b = Number(edge.device_b_id);
    const key = a <= b ? `${a}:${b}` : `${b}:${a}`;
    if (!pairs.has(key)) pairs.set(key, []);
    pairs.get(key).push(edge);
  });
  pairs.forEach((group) => {
    group.sort((x, y) => x.id - y.id);
    group.forEach((edge, i) => {
      edge.parallel = group.length > 1;
      edge.curve = group.length === 1 ? 0 : (i - (group.length - 1) / 2) * 40;
      edge.labelShift = group.length === 1 ? 0 : (i - (group.length - 1) / 2) * 0.14;
    });
  });

  DG.inc = new Map();
  DG.edgeList.forEach((edge) => {
    [edge.device_a_id, edge.device_b_id].forEach((id) => {
      const key = Number(id);
      if (!DG.inc.has(key)) DG.inc.set(key, []);
      DG.inc.get(key).push(edge);
    });
  });

  dgAutoLayout(nodes);
  dgAssignLanes();
}

/* Vertical cables between devices of the same rack share one gutter; give each
   one a lane so their rotated labels never sit on top of each other. */
function dgAssignLanes() {
  const byColumn = new Map();
  DG.edgeList.forEach((edge) => {
    const a = DG.nodeById.get(Number(edge.device_a_id));
    const b = DG.nodeById.get(Number(edge.device_b_id));
    if (!a || !b || a === b) return;
    if (Math.abs(a.gx - b.gx) >= 1) return;
    const key = Math.round(a.gx);
    if (!byColumn.has(key)) byColumn.set(key, []);
    byColumn.get(key).push({
      edge,
      y0: Math.min(a.gy, b.gy) - 70,
      y1: Math.max(a.gy, b.gy) + DG.H + 70,
    });
  });
  byColumn.forEach((list) => {
    const laneEnd = [];
    list.sort((p1, p2) => p1.y0 - p2.y0);
    list.forEach((item) => {
      let lane = 0;
      while (lane < 4 && laneEnd[lane] != null && laneEnd[lane] > item.y0) lane += 1;
      item.edge.lane = lane;
      laneEnd[lane] = item.y1;
    });
  });
}

/* ------------------------------------------------------------------- shell */
function dgShell(el) {
  const legend = [
    ['switch', 'switch'], ['router', 'router'], ['patch-panel', 'patch-panel'],
    ['server', 'server'], ['pdu', 'pdu'], ['other', 'other'],
  ].map(([kind, label]) => `<span class="dg-chip dg-kind-${kind}"><i></i>${label}</span>`).join('');

  el.innerHTML = `<div class="dg-shell">
      <div class="dg-head">
        <div>
          <h2>Network diagram</h2>
          <p>Node = device from Racks · edge = recorded port↔port link. Nothing is inferred from IP.</p>
        </div>
        <div class="dg-head-actions">
          <span class="dg-stats" id="dg-stats">—</span>
          <button class="btn ghost xs" data-dg-act="reload" title="Reload from the API">⟳ Refresh</button>
          <button class="btn ghost xs" data-dg-act="export-svg" title="Export to an SVG file">SVG</button>
          <button class="btn ghost xs" data-dg-act="export-png" title="Export to PNG">PNG</button>
        </div>
      </div>

      <div class="dg-bar">
        <label class="dg-field"><span>Dataset</span>
          <select id="dg-dataset" aria-label="Dataset">
            <option value="documented">Documented network</option>
            <option value="planned">Planned</option>
          </select>
        </label>
        <label class="dg-field"><span>Site</span><select id="dg-site" aria-label="Site filter"></select></label>
        <label class="dg-field"><span>Rack</span><select id="dg-rack" aria-label="Rack filter"></select></label>
        <label class="dg-field grow"><span>Search</span>
          <input id="dg-search" placeholder="device, mgmt IP, model…" autocomplete="off" spellcheck="false" />
        </label>
        <label class="dg-check"><input type="checkbox" id="dg-linked" /><span>Linked only</span></label>
        <label class="dg-check"><input type="checkbox" id="dg-labels" checked /><span>Labels</span></label>
        <div class="dg-zoom">
          <button class="icon-btn" data-dg-act="zoom-out" title="Zoom out">−</button>
          <button class="dg-zoom-val mono" data-dg-act="fit" title="Fit to window" id="dg-zoom">100%</button>
          <button class="icon-btn" data-dg-act="zoom-in" title="Zoom in">+</button>
        </div>
        <button class="btn ghost xs" data-dg-act="reset-pos" title="Reset auto layout">↺ Layout</button>
      </div>

      <div class="dg-readkey">
        <span><b class="dg-solid"></b> documented cable</span>
        <span><b class="dg-dash"></b> planned</span>
        <span><b class="dg-planned-box"></b> planned device</span>
        <span><b class="dg-obs-chip">observed</b> actual state</span>
        <span class="dg-readkey-warn">The diagram never writes configuration to equipment.</span>
      </div>

      <div class="dg-note hidden" id="dg-note"></div>

      <div class="dg-canvas" id="dg-canvas">
        <svg class="dg-svg" id="dg-svg" role="img" aria-label="Network diagram">
          <defs>
            <pattern id="dg-grid" width="26" height="26" patternUnits="userSpaceOnUse">
              <path d="M26 0H0V26" fill="none" stroke="rgba(255,255,255,0.045)" stroke-width="1" />
            </pattern>
          </defs>
          <rect class="dg-grid-rect" x="-12000" y="-12000" width="24000" height="24000" fill="url(#dg-grid)" />
          <g class="dg-viewport">
            <g class="dg-bands"></g>
            <g class="dg-edges"></g>
            <g class="dg-nodes"></g>
          </g>
        </svg>
        <div class="dg-legend">${legend}</div>
        <aside class="dg-panel hidden" id="dg-panel"></aside>
        <div class="dg-empty hidden" id="dg-empty"></div>
      </div>
    </div>`;

  DG.svg = el.querySelector('#dg-svg');
  DG.viewport = DG.svg.querySelector('.dg-viewport');
  DG.layers = {
    bands: DG.svg.querySelector('.dg-bands'),
    edges: DG.svg.querySelector('.dg-edges'),
    nodes: DG.svg.querySelector('.dg-nodes'),
  };
  DG.built = true;
  dgAttach(el);
}

/* ------------------------------------------------------------------ filters */
function dgSyncFilters() {
  if (!DG.el) return;
  const siteSel = DG.el.querySelector('#dg-site');
  const rackSel = DG.el.querySelector('#dg-rack');
  if (!siteSel || !rackSel) return;

  const sites = dgSites();
  siteSel.innerHTML = '<option value="all">All sites</option>'
    + sites.map((site) => `<option value="${site.id}">${dgEsc(site.name)}</option>`).join('');
  if (DG.filters.site !== 'all' && !sites.some((site) => String(site.id) === String(DG.filters.site))) {
    DG.filters.site = 'all';
  }
  siteSel.value = DG.filters.site;

  const racks = dgRacks().filter((rack) => DG.filters.site === 'all'
    || String(rack.siteId) === String(DG.filters.site));
  rackSel.innerHTML = '<option value="all">All racks</option>'
    + racks.map((rack) => `<option value="${rack.id}">${dgEsc(rack.name)}`
      + `${DG.filters.site === 'all' ? ` · ${dgEsc(rack.siteName)}` : ''}</option>`).join('');
  if (DG.filters.rack !== 'all' && !racks.some((rack) => String(rack.id) === String(DG.filters.rack))) {
    DG.filters.rack = 'all';
  }
  rackSel.value = DG.filters.rack;

  const search = DG.el.querySelector('#dg-search');
  if (search && search.value !== DG.filters.q) search.value = DG.filters.q;
  const linked = DG.el.querySelector('#dg-linked');
  if (linked) linked.checked = !!DG.filters.linkedOnly;
  const labels = DG.el.querySelector('#dg-labels');
  if (labels) labels.checked = !DG.hideLabels;
  const dataset = DG.el.querySelector('#dg-dataset');
  if (dataset) dataset.value = DG.dataset || 'documented';
}

/* --------------------------------------------------------------- node shape */
function dgRoundRect(w, h, radius) {
  const r = Math.min(radius, h / 2, w / 2);
  return `M${r},0 H${w - r} A${r},${r} 0 0 1 ${w},${r} V${h - r}`
    + ` A${r},${r} 0 0 1 ${w - r},${h} H${r} A${r},${r} 0 0 1 0,${h - r}`
    + ` V${r} A${r},${r} 0 0 1 ${r},0 Z`;
}

function dgShapeDef(kind) {
  const w = DG.W;
  const h = DG.H;
  const chamfer = 15;
  if (kind === 'router') {
    return {
      d: `M${chamfer},0 H${w - chamfer} L${w},${chamfer} V${h - chamfer}`
        + ` L${w - chamfer},${h} H${chamfer} L0,${h - chamfer} V${chamfer} Z`,
      decor: [],
    };
  }
  if (kind === 'firewall') {
    return { d: dgRoundRect(w, h, 3), decor: [[w - 34, 12, w - 10, 12], [w - 34, 26, w - 10, 26],
      [w - 34, 40, w - 10, 40]] };
  }
  if (kind === 'ap') {
    return { d: dgRoundRect(w, h, 18), decor: [] };
  }
  if (kind === 'patch-panel') {
    return {
      d: dgRoundRect(w, h, 3),
      decor: [[w - 40, h - 12, w - 12, h - 12], [w - 40, h - 20, w - 12, h - 20],
        [w - 40, h - 28, w - 12, h - 28]],
    };
  }
  if (kind === 'server') {
    return {
      d: dgRoundRect(w, h, 5),
      decor: [[w - 42, 16, w - 12, 16], [w - 42, 30, w - 12, 30]],
    };
  }
  if (kind === 'pdu') {
    return {
      d: dgRoundRect(w, h, 5),
      decor: [[w - 40, 12, w - 40, h - 12], [w - 30, 12, w - 30, h - 12],
        [w - 20, 12, w - 20, h - 12]],
    };
  }
  if (kind === 'other') {
    return { d: dgRoundRect(w, h, 13), decor: [] };
  }
  return { d: dgRoundRect(w, h, 8), decor: [] };
}

function dgIconDef(kind) {
  // όλα τα glyphs ζουν στο 0..16 πλαίσιο: τίποτα δεν βγαίνει έξω από το σχήμα
  if (kind === 'router') return 'M3 8h10M10 5l3 3-3 3M3 11h4';
  if (kind === 'firewall') return 'M2 5h12M2 8h12M2 11h12M6 5v3M10 8v3M6 11v0';
  if (kind === 'ap') return 'M5 12a4 4 0 0 1 6 0M3 9a7 7 0 0 1 10 0M8 13h.01';
  if (kind === 'patch-panel') return 'M4 3v10M8 3v10M12 3v10';
  if (kind === 'server') return 'M2 4h12v3H2zM2 9h12v3H2z';
  if (kind === 'pdu') return 'M9 2L5 9h3l-1 5 4-7H8z';
  if (kind === 'other') return 'M8 3v10M3 8h10';
  return 'M3 6h8M8 3l3 3-3 3M13 10H5M8 13l-3-3 3-3';
}

function dgNodeEl(node) {
  const group = dgSvg('g', {
    class: `dg-node dg-kind-${node.kind}${node.origin === 'planned' ? ' dg-planned' : ''}`,
    transform: `translate(${dgRound(node.gx)},${dgRound(node.gy)})`,
  });
  group.dataset.dgNode = String(node.id);

  const shape = dgShapeDef(node.kind);
  group.appendChild(dgSvg('title', null,
    `${node.name} · ${node.kind}${node.mgmt_ip ? ` · ${node.mgmt_ip}` : ''}`
    + ` · ${node.siteName} / ${node.rackName} · U${node.position}`));
  group.appendChild(dgSvg('path', { class: 'dg-halo', d: shape.d }));
  group.appendChild(dgSvg('path', { class: 'dg-shape', d: shape.d }));
  if (shape.decor.length) {
    const decor = dgSvg('g', { class: 'dg-decor' });
    shape.decor.forEach((line) => {
      decor.appendChild(dgSvg('line', { x1: line[0], y1: line[1], x2: line[2], y2: line[3] }));
    });
    group.appendChild(decor);
  }
  const badge = dgSvg('g', { class: 'dg-badge', transform: 'translate(12,15)' });
  badge.appendChild(dgSvg('rect', { class: 'dg-badge-bg', x: 0, y: 0, width: 16, height: 16, rx: 4 }));
  badge.appendChild(dgSvg('path', { class: 'dg-icon', d: dgIconDef(node.kind) }));
  group.appendChild(badge);
  group.appendChild(dgSvg('text', { class: 'dg-name', x: 36, y: DG.H / 2 - 2 }, dgClipText(node.name, 24)));
  group.appendChild(dgSvg('text', { class: 'dg-sub mono', x: 36, y: DG.H / 2 + 15 },
    dgClipText(node.mgmt_ip || 'no mgmt IP', 27)));
  group.appendChild(dgSvg('rect', {
    class: 'dg-hit', x: -6, y: -6, width: DG.W + 12, height: DG.H + 12,
  }));
  return group;
}

/* ------------------------------------------------------------------- edges */
function dgClipToNode(node, towardX, towardY) {
  const cx = node.gx + DG.W / 2;
  const cy = node.gy + DG.H / 2;
  const dx = towardX - cx;
  const dy = towardY - cy;
  if (!dx && !dy) return { x: cx, y: cy };
  const halfW = DG.W / 2 + 2;
  const halfH = DG.H / 2 + 2;
  const scaleX = dx ? halfW / Math.abs(dx) : Infinity;
  const scaleY = dy ? halfH / Math.abs(dy) : Infinity;
  const scale = Math.min(scaleX, scaleY);
  return { x: cx + dx * scale, y: cy + dy * scale };
}

function dgEdgeGeometry(a, b, edge) {
  if (a.id === b.id) {
    // Loop back into the same port pair: draw it in the left gutter so neither
    // the arc nor its label can touch a node above or below in the column.
    const spread = 26 + Math.abs(edge.curve || 0) * 0.8;
    const x = a.gx;
    const y0 = a.gy + 14;
    const y1 = a.gy + DG.H - 14;
    return {
      d: `M${dgRound(x)},${dgRound(y0)} C${dgRound(x - spread)},${dgRound(y0 - 10)}`
        + ` ${dgRound(x - spread)},${dgRound(y1 + 10)} ${dgRound(x)},${dgRound(y1)}`,
      lx: a.gx - 34,
      ly: a.gy + DG.H / 2,
      rot: 0,
      a: null,
      b: null,
    };
  }
  // Same rack column: a straight vertical edge would run through the boxes in
  // between and its label would land on a device. Bow it into the right gutter
  // and set the label vertically along the gutter instead.
  if (Math.abs(a.gx - b.gx) < 1) {
    const rightX = Math.max(a.gx, b.gx) + DG.W;
    const startY = a.gy + DG.H / 2;
    const endY = b.gy + DG.H / 2;
    const lane = Math.min(edge.lane || 0, 4);
    const bulge = 20 + lane * 9 + (edge.curve || 0) * 0.3;
    return {
      d: `M${dgRound(rightX)},${dgRound(startY)}`
        + ` C${dgRound(rightX + bulge)},${dgRound(startY + (endY - startY) * 0.3)}`
        + ` ${dgRound(rightX + bulge)},${dgRound(startY + (endY - startY) * 0.7)}`
        + ` ${dgRound(rightX)},${dgRound(endY)}`,
      lx: rightX + bulge + 10,
      ly: (startY + endY) / 2 + (lane - 1.5) * 16,   // σκάλες: καμία επικάλυψη
      rot: 0,
      vertical: true,
      a: { x: rightX, y: startY },
      b: { x: rightX, y: endY },
    };
  }
  // Parallel edges must bow in a stable direction regardless of which end is
  // port_a: order the endpoints by device id before computing the normal.
  const from = Number(a.id) <= Number(b.id) ? a : b;
  const to = from === a ? b : a;
  const ac = { x: from.gx + DG.W / 2, y: from.gy + DG.H / 2 };
  const bc = { x: to.gx + DG.W / 2, y: to.gy + DG.H / 2 };
  const A = dgClipToNode(from, bc.x, bc.y);
  const B = dgClipToNode(to, ac.x, ac.y);
  const dx = B.x - A.x;
  const dy = B.y - A.y;
  const len = Math.hypot(dx, dy) || 1;
  const offset = edge.curve || 0;
  const cx = (A.x + B.x) / 2 + (-dy / len) * offset;
  const cy = (A.y + B.y) / 2 + (dx / len) * offset;
  if (Math.abs(offset) < 0.5) {
    const label = dgLabelPoint(A, B, null, edge, 0.5);
    return {
      d: `M${dgRound(A.x)},${dgRound(A.y)} L${dgRound(B.x)},${dgRound(B.y)}`,
      lx: label.x,
      ly: label.y,
      fit: dgFits(label.x, label.y),
      a: A,
      b: B,
    };
  }
  // Parallel edges bow by different amounts and their labels slide along the
  // curve, so two cables between the same pair never share a label position.
  const label = dgLabelPoint(A, B, { x: cx, y: cy }, edge, 0.44);
  return {
    d: `M${dgRound(A.x)},${dgRound(A.y)} Q${dgRound(cx)},${dgRound(cy)}`
      + ` ${dgRound(B.x)},${dgRound(B.y)}`,
    lx: label.x,
    ly: label.y,
    fit: dgFits(label.x, label.y),
    a: A,
    b: B,
  };
}

/* Choose the point on the edge that keeps the label clear of device boxes:
   sample the path, score the distance to the nearest node, prefer the sample
   closest to `desired` (0..1 along the edge). */
function dgPointOnEdge(A, B, control, t) {
  const mt = 1 - t;
  return control
    ? { x: mt * mt * A.x + 2 * mt * t * control.x + t * t * B.x,
      y: mt * mt * A.y + 2 * mt * t * control.y + t * t * B.y }
    : { x: A.x + (B.x - A.x) * t, y: A.y + (B.y - A.y) * t };
}

/* Parallel cables keep a fixed, shifted label point (their bows already differ);
   single cables pick the point on the path that is furthest from any device. */
function dgLabelPoint(A, B, control, edge, base) {
  const desired = base + (edge.labelShift || 0);
  if (edge.parallel) return dgPointOnEdge(A, B, control, dgClamp(desired, 0.16, 0.84));
  return dgLabelSpot(A, B, control, desired);
}

function dgLabelSpot(A, B, control, desired) {
  const samples = 9;
  let best = null;
  for (let i = 0; i < samples; i += 1) {
    const t = 0.08 + (0.84 * i) / (samples - 1);
    const point = dgPointOnEdge(A, B, control, t);
    const score = dgClearance(point.x, point.y) - Math.abs(t - dgClamp(desired, 0.1, 0.9)) * 26;
    if (!best || score > best.score) best = { x: point.x, y: point.y, score };
  }
  return best || { x: (A.x + B.x) / 2, y: (A.y + B.y) / 2 };
}

/* True when a label at that point clears every device box. */
function dgFits(x, y) {
  return dgClearance(x, y) >= 3;
}

/* Distance from a label box (approximate text extents) to the nearest node. */
function dgClearance(x, y) {
  const halfW = 132;         // worst-case edge label width (44 chars mono)
  const halfH = 10;
  let best = Infinity;
  for (let i = 0; i < DG.nodes.length; i += 1) {
    const node = DG.nodes[i];
    const dx = Math.max(node.gx - (x + halfW), (x - halfW) - (node.gx + DG.W), 0);
    const dy = Math.max(node.gy - (y + halfH), (y - halfH) - (node.gy + DG.H), 0);
    const distance = Math.sqrt(dx * dx + dy * dy);
    if (distance < best) best = distance;
  }
  return best === Infinity ? 1000 : best;
}

function dgEdgeLabel(edge, max) {
  const cable = edge.cable ? ` · ${edge.cable}` : '';
  return dgClipText(`${edge.port_a_name || '?'} ↔ ${edge.port_b_name || '?'}${cable}`, max || 44);
}

function dgEdgeEl(edge) {
  const a = DG.nodeById.get(Number(edge.device_a_id));
  const b = DG.nodeById.get(Number(edge.device_b_id));
  const geo = dgEdgeGeometry(a, b, edge);
  const group = dgSvg('g', {
    class: `dg-edge${String(edge.rack_a_id) !== String(edge.rack_b_id) ? ' inter' : ''}`
      + `${geo.fit === false && !edge.parallel ? ' cramped' : ''}`
      + `${edge.planned ? ' dg-edge-planned' : ''}`,
  });
  group.dataset.dgEdge = String(edge.id);
  group.appendChild(dgSvg('path', { class: 'dg-edge-line', d: geo.d, fill: 'none' }));
  if (!geo.a) {
    const loopLabel = dgSvg('text', {
      class: 'dg-edge-label', x: dgRound(geo.lx), y: dgRound(geo.ly), 'text-anchor': 'middle',
    }, dgEdgeLabel(edge, 32));
    if (geo.rot) {
      loopLabel.setAttribute('transform', `rotate(${geo.rot} ${dgRound(geo.lx)} ${dgRound(geo.ly)})`);
    }
    group.appendChild(loopLabel);
    return group;
  }
  group.appendChild(dgSvg('circle', {
    class: 'dg-term', cx: dgRound(geo.a.x), cy: dgRound(geo.a.y), r: 2.3,
  }));
  group.appendChild(dgSvg('circle', {
    class: 'dg-term', cx: dgRound(geo.b.x), cy: dgRound(geo.b.y), r: 2.3,
  }));
  const label = dgSvg('text', {
    class: 'dg-edge-label', x: dgRound(geo.lx), y: dgRound(geo.ly - 4), 'text-anchor': 'middle',
  }, dgEdgeLabel(edge, geo.vertical ? 16 : (geo.rot ? 30 : 44)));
  if (geo.rot) {
    label.setAttribute('x', dgRound(geo.lx));
    label.setAttribute('y', dgRound(geo.ly));
    label.setAttribute('transform', `rotate(${geo.rot} ${dgRound(geo.lx)} ${dgRound(geo.ly)})`);
  }
  group.appendChild(label);
  return group;
}

function dgUpdateEdge(edge) {
  const group = DG.edgeEls && DG.edgeEls.get(edge.id);
  if (!group) return;
  const a = DG.nodeById.get(Number(edge.device_a_id));
  const b = DG.nodeById.get(Number(edge.device_b_id));
  if (!a || !b) return;
  const geo = dgEdgeGeometry(a, b, edge);
  const line = group.querySelector('.dg-edge-line');
  const label = group.querySelector('.dg-edge-label');
  if (line) line.setAttribute('d', geo.d);
  if (label) {
    label.setAttribute('x', dgRound(geo.lx));
    label.setAttribute('y', dgRound(geo.rot ? geo.ly : geo.ly - 4));
    if (geo.rot) label.setAttribute('transform', `rotate(${geo.rot} ${dgRound(geo.lx)} ${dgRound(geo.ly)})`);
    else label.removeAttribute('transform');
  }
  const terms = group.querySelectorAll('.dg-term');
  if (terms.length === 2 && geo.a && geo.b) {
    terms[0].setAttribute('cx', dgRound(geo.a.x));
    terms[0].setAttribute('cy', dgRound(geo.a.y));
    terms[1].setAttribute('cx', dgRound(geo.b.x));
    terms[1].setAttribute('cy', dgRound(geo.b.y));
  }
}

/* ----------------------------------------------------------------- painting */
function dgPaint() {
  if (!DG.built) return;
  DG.layers.bands.textContent = '';
  DG.layers.edges.textContent = '';
  DG.layers.nodes.textContent = '';
  DG.nodeEls = new Map();
  DG.edgeEls = new Map();

  const bandFragment = document.createDocumentFragment();
  DG.bandsData.forEach((band) => {
    bandFragment.appendChild(dgSvg('rect', {
      class: 'dg-band', x: dgRound(band.x), y: dgRound(band.y),
      width: dgRound(band.w), height: dgRound(band.h), rx: 14,
    }));
    bandFragment.appendChild(dgSvg('text', {
      class: 'dg-band-label', x: dgRound(band.x + 16), y: dgRound(band.y + 22),
    }, band.siteName));
  });
  DG.columns.forEach((column) => {
    bandFragment.appendChild(dgSvg('text', {
      class: 'dg-col-label', x: dgRound(column.x), y: DG.TOP - 62,
    }, column.parts > 1 ? `${column.rackName} ·${column.part}/${column.parts}` : column.rackName));
  });
  DG.layers.bands.appendChild(bandFragment);

  const nodeFragment = document.createDocumentFragment();
  DG.nodes.forEach((node) => {
    const el = dgNodeEl(node);
    DG.nodeEls.set(node.id, el);
    nodeFragment.appendChild(el);
  });
  DG.layers.nodes.appendChild(nodeFragment);

  const edgeFragment = document.createDocumentFragment();
  DG.edgeList.forEach((edge) => {
    const el = dgEdgeEl(edge);
    DG.edgeEls.set(edge.id, el);
    edgeFragment.appendChild(el);
  });
  DG.layers.edges.appendChild(edgeFragment);

  DG.svg.classList.toggle('dg-no-labels', DG.hideLabels);
  if (DG.selected && !DG.nodeById.has(DG.selected)) DG.selected = null;
  dgSyncSelection();
  dgUpdateStats();
  dgUpdateOverlays();
  dgApplyView();
  DG.paintedSig = dgSig();
}

function dgSyncSelection() {
  if (DG.nodeEls) {
    DG.nodeEls.forEach((el, id) => el.classList.toggle('sel', id === DG.selected));
  }
  if (DG.edgeEls) {
    DG.edgeEls.forEach((el, id) => {
      const edge = DG.edgeById.get(id);
      const on = !!edge && (Number(edge.device_a_id) === DG.selected
        || Number(edge.device_b_id) === DG.selected);
      el.classList.toggle('on', on);
    });
  }
  dgRenderPanel();
}

function dgUpdateStats() {
  const stats = DG.el && DG.el.querySelector('#dg-stats');
  if (!stats) return;
  const totalDevices = dgDevices().length;
  const totalLinks = DG.links.length;
  const filtered = DG.nodes.length !== totalDevices || DG.edgeList.length !== totalLinks;
  stats.innerHTML = `<b>${DG.nodes.length}</b> nodes · <b>${DG.edgeList.length}</b> links`
    + (filtered ? ` <span class="muted">(of ${totalDevices} / ${totalLinks})</span>` : '');
}

function dgUpdateOverlays() {
  const empty = DG.el && DG.el.querySelector('#dg-empty');
  const note = DG.el && DG.el.querySelector('#dg-note');
  if (!empty || !note) return;

  const sites = dgSites();
  const anyDevice = dgDevices().length > 0;

  if (!sites.length || !anyDevice) {
    empty.innerHTML = `<div class="dg-empty-card">
        <b>No diagram yet</b>
        <p>The diagram is built only from the devices and the port↔port
           links you record in Racks.</p>
        <ol>
          <li>Open <b>Racks</b> and add a site and a rack.</li>
          <li>Add a device with a name, type and mgmt IP.</li>
          <li>Link two ports — the "portA ↔ portB · cable" edge will appear here.</li>
        </ol>
        <button class="btn primary sm" data-dg-act="goto-racks">Open Racks</button>
      </div>`;
    empty.classList.remove('hidden');
  } else if (!DG.nodes.length) {
    empty.innerHTML = `<div class="dg-empty-card">
        <b>No nodes match these filters</b>
        <p>Devices exist, but none match the site/rack/search filter.</p>
        <button class="btn ghost sm" data-dg-act="clear-filters">Clear filters</button>
      </div>`;
    empty.classList.remove('hidden');
  } else {
    empty.classList.add('hidden');
  }

  if (DG.nodes.length && !DG.links.length) {
    note.innerHTML = `<span>No recorded links yet — nodes are shown without edges.</span>
      <button class="btn ghost xs" data-dg-act="goto-racks">Link ports in Racks</button>`;
    note.classList.remove('hidden');
  } else if (DG.nodes.length && !DG.edgeList.length) {
    note.innerHTML = `<span>Nodes are shown, but no link connects two visible nodes.</span>`;
    note.classList.remove('hidden');
  } else {
    note.classList.add('hidden');
  }
}

/* -------------------------------------------------------------- view control */
function dgApplyView() {
  if (!DG.viewport) return;
  DG.viewport.setAttribute('transform',
    `translate(${dgRound(DG.view.x)},${dgRound(DG.view.y)}) scale(${dgRound(DG.view.k)})`);
  const zoom = DG.el && DG.el.querySelector('#dg-zoom');
  if (zoom) zoom.textContent = `${Math.round(DG.view.k * 100)}%`;
  if (DG.svg) {
    DG.svg.classList.toggle('dg-far', DG.view.k < 0.45);
    // σε μικρό zoom οι ετικέτες καλωδίων γίνονται θόρυβος — τις κρύβουμε
    DG.svg.classList.toggle('dg-tiny', DG.view.k < 0.72 && !DG.hideLabels);
  }
}

function dgZoomAt(px, py, factor) {
  const k0 = DG.view.k;
  const k1 = dgClamp(k0 * factor, 0.08, 3);
  if (k1 === k0) return;
  DG.view.x = px - (px - DG.view.x) * (k1 / k0);
  DG.view.y = py - (py - DG.view.y) * (k1 / k0);
  DG.view.k = k1;
  dgApplyView();
}

function dgZoomCenter(factor) {
  const rect = DG.svg.getBoundingClientRect();
  dgZoomAt(rect.width / 2, rect.height / 2, factor);
}

function dgBounds() {
  if (!DG.nodes.length && !DG.bandsData.length) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const add = (x, y) => {
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
  };
  DG.nodes.forEach((node) => {
    add(node.gx, node.gy);
    add(node.gx + DG.W, node.gy + DG.H);
  });
  DG.bandsData.forEach((band) => {
    add(band.x, band.y);
    add(band.x + band.w, band.y + band.h);
  });
  if (!Number.isFinite(minX)) return null;
  // χώρος για ετικέτες καλωδίων: δεξιά/αριστερά δεν πρέπει να κόβονται
  const labelPadX = 150;
  const labelPadY = 26;
  return {
    x: minX - 24,
    y: minY - labelPadY,
    w: maxX - minX + 24 + labelPadX,
    h: maxY - minY + labelPadY * 2,
  };
}

function dgFit() {
  if (!DG.svg) return;
  const rect = DG.svg.getBoundingClientRect();
  const bounds = dgBounds();
  if (!rect.width || !rect.height || !bounds) {
    DG.fitted = false;
    return;
  }
  const pad = 56;
  // μην ανοίγεις το διάγραμμα σε δυσανάγνωστο zoom: ποτέ κάτω από 55%
  const k = dgClamp(Math.min(
    (rect.width - pad * 2) / Math.max(bounds.w, 1),
    (rect.height - pad * 2) / Math.max(bounds.h, 1),
  ), 0.55, 1.2);
  DG.view.k = k;
  DG.view.x = (rect.width - bounds.w * k) / 2 - bounds.x * k;
  DG.view.y = (rect.height - bounds.h * k) / 2 - bounds.y * k;
  dgApplyView();
  DG.fitted = true;
}

/* ----------------------------------------------------------- drag / zoom in */
function dgPointerDown(event) {
  if (event.button !== 0) return;
  const nodeEl = event.target.closest ? event.target.closest('.dg-node') : null;
  if (nodeEl) {
    const id = Number(nodeEl.dataset.dgNode);
    const node = DG.nodeById.get(id);
    if (!node) return;
    DG.drag = {
      id,
      node,
      el: nodeEl,
      sx: event.clientX,
      sy: event.clientY,
      ox: node.gx,
      oy: node.gy,
      moved: false,
    };
    nodeEl.classList.add('drag');
    try { DG.svg.setPointerCapture(event.pointerId); } catch (_) { /* older browsers */ }
    event.preventDefault();
    event.stopPropagation();
    return;
  }
  DG.pan = {
    sx: event.clientX,
    sy: event.clientY,
    vx: DG.view.x,
    vy: DG.view.y,
    moved: false,
  };
  DG.svg.classList.add('panning');
  try { DG.svg.setPointerCapture(event.pointerId); } catch (_) { /* older browsers */ }
  event.preventDefault();
}

function dgPointerMove(event) {
  if (DG.drag) {
    const drag = DG.drag;
    const k = DG.view.k || 1;
    const dx = (event.clientX - drag.sx) / k;
    const dy = (event.clientY - drag.sy) / k;
    if (Math.abs(dx) + Math.abs(dy) > 2) drag.moved = true;
    drag.node.gx = Math.round(drag.ox + dx);
    drag.node.gy = Math.round(drag.oy + dy);
    drag.el.setAttribute('transform',
      `translate(${dgRound(drag.node.gx)},${dgRound(drag.node.gy)})`);
    (DG.inc.get(drag.id) || []).forEach((edge) => dgUpdateEdge(edge));
    return;
  }
  if (DG.pan) {
    const dx = event.clientX - DG.pan.sx;
    const dy = event.clientY - DG.pan.sy;
    if (Math.abs(dx) + Math.abs(dy) > 2) DG.pan.moved = true;
    DG.view.x = DG.pan.vx + dx;
    DG.view.y = DG.pan.vy + dy;
    dgApplyView();
  }
}

function dgPointerUp(event) {
  if (DG.drag) {
    const drag = DG.drag;
    drag.el.classList.remove('drag');
    if (drag.moved) {
      dgPositions()[drag.id] = { x: drag.node.gx, y: drag.node.gy };
      dgWritePositions();
    } else {
      dgSelect(DG.selected === drag.id ? null : drag.id);
    }
    DG.drag = null;
    DG.fitted = true;
  } else if (DG.pan) {
    if (!DG.pan.moved) dgSelect(null);
    DG.pan = null;
  }
  DG.svg.classList.remove('panning');
  try { DG.svg.releasePointerCapture(event.pointerId); } catch (_) { /* ignore */ }
}

/* ------------------------------------------------------------------- panel */
function dgSelect(id) {
  DG.selected = id == null ? null : Number(id);
  dgSyncSelection();
}

function dgRenderPanel() {
  const panel = DG.el && DG.el.querySelector('#dg-panel');
  if (!panel) return;
  const node = DG.selected != null ? DG.nodeById.get(DG.selected) : null;
  if (!node) {
    panel.classList.add('hidden');
    panel.innerHTML = '';
    return;
  }

  const rows = [
    ['Type', DG_KIND_LABEL[node.kind] || node.kind],
    ['Site', node.siteName],
    ['Rack', node.rackName],
    ['Position', `U${node.position}${node.units > 1 ? `–U${node.position + node.units - 1}` : ''} · ${node.units}U`],
    ['Vendor', node.vendor || '—'],
    ['Model', node.model || '—'],
    ['Mgmt IP', node.mgmt_ip || '—'],
    ['Links', String(node.deg || 0)],
  ];

  panel.innerHTML = `<header class="dg-panel-head">
      <div>
        <h3>${dgEsc(node.name)}</h3>
        <p>${dgEsc(node.siteName)} · ${dgEsc(node.rackName)}</p>
      </div>
      <button class="icon-btn" data-dg-act="close-panel" title="Close">✕</button>
    </header>
    <div class="dg-panel-body">
      <dl class="dg-kv">
        ${rows.map(([label, value]) => `<dt>${label}</dt><dd>${dgEsc(value)}</dd>`).join('')}
        <dt>Host</dt><dd id="dg-host-label" class="mono muted">—</dd>
      </dl>
      <h4>Ports <span id="dg-port-summary" class="muted"></span></h4>
      <div class="dg-ports" id="dg-port-list"><p class="hint">Loading ports…</p></div>
      <button class="btn primary sm wide" data-dg-act="open-rack">Open rack</button>
    </div>`;
  panel.classList.remove('hidden');

  dgLoadRack(node.rackId).then((rack) => {
    if (DG.selected !== node.id) return;
    dgFillPanel(rack, node);
  });
}

function dgFillPanel(rack, node) {
  const list = DG.el && DG.el.querySelector('#dg-port-list');
  const summary = DG.el && DG.el.querySelector('#dg-port-summary');
  const hostRow = DG.el && DG.el.querySelector('#dg-host-label');
  if (!list) return;
  const device = rack && Array.isArray(rack.devices)
    ? rack.devices.find((item) => Number(item.id) === node.id)
    : null;
  if (!device) {
    list.innerHTML = '<p class="hint">No ports loaded for this device.</p>';
    return;
  }
  if (hostRow) {
    hostRow.textContent = device.host_label ? String(device.host_label) : '—';
    hostRow.classList.toggle('muted', !device.host_label);
  }
  const ports = Array.isArray(device.ports) ? device.ports : [];
  const linked = ports.filter((port) => port.link_id).length;
  if (summary) summary.textContent = `${linked}/${ports.length} linked`;
  list.innerHTML = ports.length
    ? ports.map((port) => `<span class="dg-port${port.link_id ? ' linked' : ''}"
        title="${dgEsc(port.peer || 'free')}"><b>${dgEsc(port.name)}</b>
        <em>${dgEsc(port.peer || 'free')}</em></span>`).join('')
    : '<p class="hint">This device has no ports.</p>';
}

function dgLoadRack(rackId) {
  DG.rackCache = DG.rackCache || new Map();
  if (!DG.rackCache.has(rackId)) {
    const request = api(`/inventory/rack/${rackId}`).catch(() => null);
    DG.rackCache.set(rackId, request);
  }
  return DG.rackCache.get(rackId);
}

function dgLeavePage() {
  // app.js only reveals pages; hide ours so it never covers the racks view.
  const page = document.querySelector('#page-diagram');
  if (page) page.classList.add('hidden');
}

async function dgOpenRack(deviceId) {
  const node = deviceId != null ? DG.nodeById.get(Number(deviceId)) : null;
  if (!node) return;
  dgLeavePage();
  if (typeof ND !== 'undefined' && ND) {
    ND.rackId = node.rackId;
    ND.deviceId = node.id;
    ND.tab = 'layout';
  }
  if (typeof setView === 'function') setView('racks');
  if (typeof ndSelectRack === 'function') {
    try {
      await ndSelectRack(node.rackId);
    } catch (err) {
      if (typeof toast === 'function') toast(err && err.message ? err.message : 'The rack failed to load', 'err');
    }
  } else if (typeof renderPage === 'function') {
    renderPage();
  }
}

function dgGotoRacks() {
  dgLeavePage();
  if (typeof setView === 'function') setView('racks');
}

/* ------------------------------------------------------------------ export */
const DG_EXPORT_CSS = `
.dg-band{fill:#0b1017;stroke:#1d2733;stroke-width:1}
.dg-band-label{fill:#7d8b9d;font:600 10px ui-monospace,Menlo,Consolas,monospace;letter-spacing:.08em;text-transform:uppercase}
.dg-col-label{fill:#9fadc0;font:600 11px -apple-system,Segoe UI,Roboto,sans-serif}
.dg-node .dg-shape{fill:#111823;stroke:#59697c;stroke-width:1.3}
.dg-node .dg-halo{fill:none;stroke:#3ddc97;stroke-width:4;opacity:0}
.dg-node.sel .dg-halo{opacity:.4}
.dg-kind-switch .dg-shape{fill:#0f2419;stroke:#3ddc97}
.dg-kind-router .dg-shape{fill:#141b31;stroke:#7c9cff}
.dg-kind-patch-panel .dg-shape{fill:#2a2110;stroke:#ffc75f}
.dg-kind-server .dg-shape{fill:#221733;stroke:#c48cff}
.dg-kind-pdu .dg-shape{fill:#2b1712;stroke:#ff8f6b}
.dg-kind-other .dg-shape{fill:#161e28;stroke:#8593a5;stroke-dasharray:5 4}
.dg-node.sel .dg-shape{stroke-width:2.4}
.dg-decor{stroke:#5b6b7d;stroke-width:1}
.dg-icon{fill:none;stroke:#c9d6e4;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}
.dg-kind-pdu .dg-icon{fill:#ff8f6b;stroke:#ff8f6b}
.dg-name{fill:#e8eef5;font:600 12.5px -apple-system,Segoe UI,Roboto,sans-serif}
.dg-sub{fill:#8593a5;font:10.5px ui-monospace,Menlo,Consolas,monospace}
.dg-edge-line{fill:none;stroke:#33465a;stroke-width:1.15}
.dg-edge.inter .dg-edge-line{stroke-dasharray:5 4}
.dg-edge.on .dg-edge-line{stroke:#3ddc97;stroke-width:1.7}
.dg-edge-label{fill:#93a2b5;font:9.5px ui-monospace,Menlo,Consolas,monospace;paint-order:stroke;stroke:#080a0e;stroke-width:3;stroke-linejoin:round}
.dg-term{fill:#4a5f75}
`;

function dgExportSvgString() {
  if (!DG.built || !DG.nodes.length) return null;
  const bounds = dgBounds();
  if (!bounds) return null;
  const pad = 44;
  const width = Math.ceil(bounds.w + pad * 2);
  const height = Math.ceil(bounds.h + pad * 2);

  const clone = DG.svg.cloneNode(true);
  clone.querySelectorAll('.dg-hit').forEach((el) => el.remove());
  clone.querySelectorAll('[id]').forEach((el) => el.removeAttribute('id'));
  const viewport = clone.querySelector('.dg-viewport');
  if (viewport) viewport.setAttribute('transform', 'translate(0,0) scale(1)');
  clone.setAttribute('class', 'dg-svg');
  clone.setAttribute('xmlns', DG.NS);
  clone.setAttribute('viewBox', `${dgRound(bounds.x - pad)} ${dgRound(bounds.y - pad)} ${width} ${height}`);
  clone.setAttribute('width', String(width));
  clone.setAttribute('height', String(height));

  const style = dgSvg('style', null, DG_EXPORT_CSS);
  clone.insertBefore(style, clone.firstChild);
  const background = dgSvg('rect', {
    x: dgRound(bounds.x - pad), y: dgRound(bounds.y - pad),
    width, height, fill: '#080a0e',
  });
  clone.insertBefore(background, style.nextSibling);

  return {
    svg: `<?xml version="1.0" encoding="UTF-8"?>\n${new XMLSerializer().serializeToString(clone)}`,
    width,
    height,
  };
}

function dgDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

function dgExportSvg() {
  const out = dgExportSvgString();
  if (!out) {
    if (typeof toast === 'function') toast('No diagram to export', 'err');
    return;
  }
  dgDownload(new Blob([out.svg], { type: 'image/svg+xml;charset=utf-8' }), 'muxivra-diagram.svg');
  if (typeof toast === 'function') toast('SVG downloaded');
}

function dgExportPng() {
  const out = dgExportSvgString();
  if (!out) {
    if (typeof toast === 'function') toast('No diagram to export', 'err');
    return;
  }
  const scale = dgClamp(2, 1, 6000 / Math.max(out.width, out.height));
  const url = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(out.svg)}`;
  const image = new Image();
  image.onload = () => {
    try {
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(out.width * scale);
      canvas.height = Math.round(out.height * scale);
      const ctx = canvas.getContext('2d');
      ctx.scale(scale, scale);
      ctx.drawImage(image, 0, 0);
      canvas.toBlob((blob) => {
        if (!blob) {
          if (typeof toast === 'function') toast('PNG export failed', 'err');
          return;
        }
        dgDownload(blob, 'muxivra-diagram.png');
        if (typeof toast === 'function') toast('PNG downloaded');
      }, 'image/png');
    } catch (err) {
      if (typeof toast === 'function') toast('PNG export failed', 'err');
    }
  };
  image.onerror = () => {
    if (typeof toast === 'function') toast('PNG export failed', 'err');
  };
  image.src = url;
}

/* --------------------------------------------------------------- behaviour */
function dgRefresh() {
  dgBuild();
  dgPaint();
}

function dgResetPositions() {
  DG.pos = {};
  dgWritePositions();
  DG.fitted = false;
  dgRefresh();
  dgFit();
  if (typeof toast === 'function') toast('Layout positions reset');
}

function dgAttach(el) {
  el.addEventListener('click', (event) => {
    const action = event.target.closest ? event.target.closest('[data-dg-act]') : null;
    if (!action) return;
    const act = action.dataset.dgAct;
    event.preventDefault();
    event.stopPropagation();
    if (act === 'fit') dgFit();
    else if (act === 'zoom-in') dgZoomCenter(1.25);
    else if (act === 'zoom-out') dgZoomCenter(1 / 1.25);
    else if (act === 'reset-pos') dgResetPositions();
    else if (act === 'export-svg') dgExportSvg();
    else if (act === 'export-png') dgExportPng();
    else if (act === 'close-panel') dgSelect(null);
    else if (act === 'open-rack') dgOpenRack(DG.selected);
    else if (act === 'goto-racks') dgGotoRacks();
    else if (act === 'clear-filters') {
      DG.filters = { site: 'all', rack: 'all', q: '', linkedOnly: false };
      DG.fitted = false;
      dgSyncFilters();
      dgRefresh();
      dgFit();
    } else if (act === 'reload') {
      DG.fitted = false;
      DG.built = true;
      renderDiagram('reload').then(() => {
        if (typeof toast === 'function') toast('Diagram refreshed');
      });
    }
  });

  el.addEventListener('change', (event) => {
    const target = event.target;
    if (target.id === 'dg-site') {
      DG.filters.site = target.value;
      DG.filters.rack = 'all';
      DG.fitted = false;
      dgSyncFilters();
      dgRefresh();
      dgFit();
    } else if (target.id === 'dg-dataset') {
      DG.dataset = target.value === 'planned' ? 'planned' : 'documented';
      DG.pos = null;                 // separate layout per dataset and customer
      DG.fitted = false;
      dgRefresh();
      dgFit();
    } else if (target.id === 'dg-rack') {
      DG.filters.rack = target.value;
      DG.fitted = false;
      dgSyncFilters();
      dgRefresh();
      dgFit();
    } else if (target.id === 'dg-linked') {
      DG.filters.linkedOnly = target.checked;
      DG.fitted = false;
      dgRefresh();
      dgFit();
    } else if (target.id === 'dg-labels') {
      DG.hideLabels = !target.checked;
      if (DG.svg) DG.svg.classList.toggle('dg-no-labels', DG.hideLabels);
    }
  });

  el.addEventListener('input', dgDebounce((event) => {
    if (event.target.id !== 'dg-search') return;
    DG.filters.q = event.target.value;
    DG.fitted = false;
    dgRefresh();
    dgFit();
  }, 160));

  DG.svg.addEventListener('wheel', (event) => {
    event.preventDefault();
    const rect = DG.svg.getBoundingClientRect();
    const factor = Math.exp(-event.deltaY * 0.0016);
    dgZoomAt(event.clientX - rect.left, event.clientY - rect.top, factor);
  }, { passive: false });

  DG.svg.addEventListener('pointerdown', dgPointerDown);
  DG.svg.addEventListener('pointermove', dgPointerMove);
  DG.svg.addEventListener('pointerup', dgPointerUp);
  DG.svg.addEventListener('pointercancel', dgPointerUp);

  DG.svg.addEventListener('dblclick', (event) => {
    const nodeEl = event.target.closest ? event.target.closest('.dg-node') : null;
    if (nodeEl) {
      dgOpenRack(Number(nodeEl.dataset.dgNode));
      return;
    }
    dgFit();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (!DG.el || DG.el.classList.contains('hidden')) return;
    if (DG.selected != null) dgSelect(null);
  });
}

function dgSig() {
  return [
    DG.rev,
    DG.filters.site,
    DG.filters.rack,
    DG.filters.q,
    DG.filters.linkedOnly ? 1 : 0,
    DG.nodes.length,
    DG.edgeList.length,
    DG.links.length,
  ].join('|');
}

function dgError(err) {
  const message = err && err.message === 'links_unavailable'
    ? 'Links could not be loaded from the API'
    : (err && err.message ? err.message : 'Load error');
  if (typeof toast === 'function') toast(message, 'err');
  const empty = DG.el && DG.el.querySelector('#dg-empty');
  if (empty) {
    empty.innerHTML = `<div class="dg-empty-card">
        <b>The diagram failed to load</b>
        <p>${dgEsc(message)}</p>
        <button class="btn ghost sm" data-dg-act="reload">Try again</button>
      </div>`;
    empty.classList.remove('hidden');
  }
}

/* ---------------------------------------------------------------- entry pt */
async function renderDiagram(force) {
  const el = document.querySelector('#page-diagram');
  if (!el) return;
  DG.el = el;
  if (!DG.built || !el.querySelector('.dg-shell')) dgShell(el);
  if (DG.busy) return DG.busy;

  DG.busy = (async () => {
    try {
      await dgReload(force === true || force === 'reload');
      dgSyncFilters();
      dgBuild();
      if (force || DG.paintedSig !== dgSig() || !DG.edgeEls) dgPaint();
      if (!DG.fitted) dgFit();
    } catch (err) {
      dgError(err);
    } finally {
      DG.busy = null;
    }
  })();
  return DG.busy;
}

/* --------------------------------------------------------------- shell hook
   The rail button only toggles visibility; render on demand when the section
   becomes visible (click on the rail button, class change, or a direct call). */
(function dgHook() {
  const onRailClick = (event) => {
    const button = event.target.closest ? event.target.closest('.rail-btn[data-view]') : null;
    if (!button) return;
    const view = button.dataset.view;
    const page = document.querySelector('#page-diagram');
    if (!page) return;
    if (view === 'diagram') {
      requestAnimationFrame(() => { renderDiagram(); });
      return;
    }
    // This module owns #page-diagram visibility; keep it from covering other views.
    page.classList.add('hidden');
  };

  document.addEventListener('click', onRailClick, true);

  const watch = () => {
    const page = document.querySelector('#page-diagram');
    if (!page) return;
    new MutationObserver(() => {
      if (!page.classList.contains('hidden')) renderDiagram();
    }).observe(page, { attributes: true, attributeFilter: ['class'] });
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watch);
  else watch();

  window.addEventListener('resize', () => { if (DG.fitted) dgApplyView(); });
})();
