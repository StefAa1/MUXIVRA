/* ------------------------------------------------------------- diagram
   Placeholder until the diagram module lands: renders nodes/edges from the
   same /api/netdoc data as the racks view. */
function renderDiagram() {
  const el = document.querySelector('#page-diagram');
  if (!el) return;
  el.innerHTML = '<div class="page-head"><div><h2>Διάγραμμα</h2><p>Το module φορτώνεται…</p></div></div>';
}
