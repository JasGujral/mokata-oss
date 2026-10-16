// mokata ide — Graph: the blast-radius ripple, symbol focus, call-path tracer and untested-path
// finder, drawn with the VENDORED Cytoscape.js (+ fcose, + dagre) from /static/vendor/ — never a
// CDN. Every node and edge comes from the server's graph answer; nothing is drawn that the graph
// cannot justify, and every edge shows its basis (solid = structural, dashed = lexical).
// SAFETY: labels go into the canvas as text and into the page through textContent only.
"use strict";

(function () {
  const MK = window.MK;
  const { $, el, api } = MK;
  const G = (MK.graph = { view: "ripple", cy: null, loading: null, data: null });
  const VENDOR = ["cytoscape.min.js", "layout-base.js", "cose-base.js", "cytoscape-fcose.js", "cytoscape-dagre.min.js"];

  // Load the vendored libraries once, in order, on first use.
  function loadLibs() {
    if (window.cytoscape && window.cytoscapeFcose && window.cytoscapeDagre) return Promise.resolve();
    if (G.loading) return G.loading;
    G.loading = VENDOR.reduce((p, f) => p.then(() => new Promise((ok, bad) => {
      const s = document.createElement("script");
      s.src = "/static/vendor/" + f;
      s.onload = ok;
      s.onerror = () => bad(new Error("could not load " + f));
      document.head.append(s);
    })), Promise.resolve()).then(() => {
      window.cytoscape.use(window.cytoscapeFcose);
      window.cytoscape.use(window.cytoscapeDagre);
    });
    return G.loading;
  }

  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  function style() {
    const c = { text: css("--text"), text2: css("--text2"), panel: css("--panel"), line2: css("--line2"),
      accent: css("--accent"), amber: css("--amber"), blue: css("--blue"), add: css("--add"), edge: css("--edge"), sel: css("--sel") };
    return [
      { selector: "node", style: {
        label: "data(label)", "font-family": "JetBrains Mono, ui-monospace, monospace", "font-size": 11,
        color: c.text, "text-valign": "center", "text-halign": "center", shape: "round-rectangle",
        width: "label", height: 24, padding: "7px", "background-color": c.panel, "border-width": 1, "border-color": c.line2 } },
      { selector: "node[kind = 'center']", style: { "border-width": 2, "border-color": c.accent, "background-color": c.sel, "font-size": 13 } },
      { selector: "node[kind = 'test']", style: { shape: "round-rectangle", "border-color": c.add, "corner-radius": 12 } },
      { selector: "node[kind = 'module']", style: { "border-style": "dashed" } },
      { selector: "node[coverage = 'untested']", style: { "border-color": c.amber, color: c.amber } },
      { selector: "node[kind = 'center'][coverage = 'untested']", style: { "border-color": c.amber, "border-width": 2 } },
      { selector: "edge", style: { width: 1.6, "curve-style": "bezier", "target-arrow-shape": "triangle",
        "arrow-scale": 0.8, "line-color": c.edge, "target-arrow-color": c.edge } },
      { selector: "edge[basis = 'lexical']", style: { "line-style": "dashed", "line-color": c.blue, "target-arrow-color": c.blue } },
      { selector: "edge[risk = 1]", style: { "line-color": c.amber, "target-arrow-color": c.amber } },
      { selector: "edge[kind = 'IMPORTS']", style: { "line-style": "dotted" } },
      { selector: "edge[kind = 'IMPORTS'][basis = 'lexical']", style: { "line-style": "dashed" } },
      { selector: ":selected", style: { "overlay-color": c.accent, "overlay-opacity": 0.15 } },
    ];
  }

  function elements(data) {
    const nodes = data.nodes.map((n) => ({ data: { ...n } }));
    const cov = {};
    for (const n of data.nodes) cov[n.id] = n.coverage;
    const edges = data.edges.map((e) => ({ data: { ...e, risk: cov[e.source] === "untested" ? 1 : 0 } }));
    return nodes.concat(edges);
  }

  function layoutFor(data) {
    if ($("g-organic").checked) return { name: "fcose", animate: false, randomize: true, nodeSeparation: 90, idealEdgeLength: 110 };
    if (data.view === "ripple" || data.view === "untested") {
      const maxRing = Math.max(0, ...data.nodes.map((n) => n.ring || 0));
      return { name: "concentric", animate: false, minNodeSpacing: 34, startAngle: Math.PI,
               concentric: (n) => maxRing - (n.data("ring") || 0), levelWidth: () => 1 };
    }
    return { name: "dagre", rankDir: "LR", nodeSep: 26, rankSep: 90, animate: false };
  }

  async function show(view, symbol, to) {
    G.view = view || G.view;
    for (const b of document.querySelectorAll("button[data-gview]")) b.classList.toggle("active", b.dataset.gview === G.view);
    $("g-to-wrap").hidden = G.view !== "path";
    if (symbol !== undefined) $("g-symbol").value = symbol;
    if (to !== undefined) $("g-to").value = to;
    const sym = $("g-symbol").value.trim();
    const dst = $("g-to").value.trim();
    $("g-sel").hidden = true;
    if (!sym || (G.view === "path" && !dst)) { $("g-empty").hidden = false; if (G.cy) G.cy.elements().remove(); return; }
    $("g-empty").hidden = true;
    $("g-title").textContent = "asking the graph…";
    const depth = G.view === "path" ? Math.max(3, +$("g-depth").value) : +$("g-depth").value;
    const url = `/api/graph?view=${G.view}&symbol=${encodeURIComponent(sym)}&to=${encodeURIComponent(dst)}&depth=${depth}`;
    const [data] = await Promise.all([api(url).catch(() => null), loadLibs().catch((e) => { G.libError = e; })]);
    if (G.libError) { $("g-title").textContent = "graph view unavailable"; $("g-note").textContent = String(G.libError.message); return; }
    if (!data || data.error || data.sample) {
      $("g-title").textContent = "no graph";
      $("g-summary").textContent = data ? (data.note || data.error) : "the server did not answer";
      if (data) MK.markSample(data);
      if (G.cy) G.cy.elements().remove();
      return;
    }
    G.data = data;
    draw(data);
    summarize(data);
  }
  MK.openGraph = (view, symbol, to) => { MK.setMode("graph"); MK.setPanel("graph", true); show(view, symbol, to); };

  function draw(data) {
    const box = $("cy");
    if (!G.cy) {
      G.cy = window.cytoscape({ container: box, style: style(), wheelSensitivity: 0.3, minZoom: 0.1, maxZoom: 3 });
      G.cy.on("tap", "node", (ev) => select(ev.target.data(), "node"));
      G.cy.on("tap", "edge", (ev) => select(ev.target.data(), "edge"));
      G.cy.on("dbltap", "node", (ev) => { const d = ev.target.data(); if (d.symbol) show(G.view === "path" ? "ripple" : G.view, d.symbol); });
      if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => G.cy.style(style()));
    }
    G.cy.elements().remove();
    G.cy.add(elements(data));
    G.cy.layout(layoutFor(data)).run();
    fit();
  }

  function fit() {
    G.cy.fit(undefined, 40);
    if (G.cy.zoom() > 1.25) { G.cy.zoom(1.25); G.cy.center(); }
  }

  function summarize(data) {
    const sym = data.symbol || data.source;
    const title = $("g-title");
    const sum = $("g-summary");
    const chainsH = $("g-chains-h");
    const chains = $("g-chains");
    chains.replaceChildren();
    chainsH.hidden = true;
    const bases = Object.entries(data.bases || {}).map(([b, n]) => `${n} ${b}`).join(", ") || "no edges";
    if (data.view === "path") {
      title.textContent = `${data.source} → ${data.target}`;
      sum.textContent = data.found ? `a call path of ${data.nodes.length - 1} hop(s) — ${bases}` : (data.note || "no path");
    } else if (data.view === "focus") {
      const left = data.nodes.filter((n) => n.side === "left").length;
      const right = data.nodes.filter((n) => n.side === "right").length;
      title.textContent = sym;
      sum.textContent = `${left} caller(s) on the left, ${right} callee(s) on the right — edges: ${bases}`;
    } else {
      const byRing = {};
      for (const n of data.nodes) if (n.kind !== "test" && n.kind !== "center") byRing[n.ring] = (byRing[n.ring] || 0) + 1;
      const tests = data.nodes.filter((n) => n.kind === "test").length;
      title.textContent = `If ${sym} changes`;
      const rings = Object.keys(byRing).sort().map((r) => `${byRing[r]} at ${r} hop(s)`).join(", ") || "nothing calls it";
      sum.textContent = data.defs === 0
        ? `The graph holds no definition of ${sym} — nothing here is claimed about it.`
        : `${rings}; ${tests} test node(s). Edges: ${bases}.`;
      const ch = data.untested_chains || [];
      if (ch.length) {
        chainsH.hidden = false;
        chainsH.textContent = `${ch.length} path(s) reach no test`;
        for (const c of ch) chains.append(el("li", "", c.join(" → ")));
      }
    }
    const notes = [];
    if (data.truncated) notes.push(`stopped at ${data.max_nodes} nodes — lower the depth`);
    if ((data.bases || {}).lexical) notes.push("dashed edges are lexical (a name match, not a graph edge): they never mark anything untested");
    else if ((data.answer_bases || []).includes("lexical")) notes.push("at least one question had no structural answer (it fell to the lexical floor), so an empty part of this picture is not a verified zero");
    $("g-note").textContent = notes.join(" · ");
  }

  function select(d, what) {
    const box = $("g-sel");
    box.hidden = false;
    box.replaceChildren();
    if (what === "edge") {
      box.append(el("b", "mono", `${d.kind}`), document.createTextNode(" "), MK.basisBadge(d.basis));
      box.append(el("div", "mono small", `site: ${d.site}`));
      const row = el("div", "row2");
      const b = el("button", "btn", "Open the site");
      b.type = "button";
      const i = d.site.lastIndexOf(":");
      b.onclick = () => MK.openFile(d.site.slice(0, i), +d.site.slice(i + 1), null);
      row.append(b);
      box.append(row);
      return;
    }
    box.append(el("b", "mono", d.label));
    box.append(el("div", "small dim", `${d.kind}${d.coverage ? " · " + d.coverage : ""}${d.path ? " · " + d.path + ":" + d.line : ""}`));
    const row = el("div", "row2");
    const open = el("button", "btn", "Open in Navigate");
    open.type = "button";
    open.onclick = async () => {
      if (d.path) { MK.openFile(d.path, d.line || 1, d.symbol); return; }
      if (!d.symbol) return;
      const res = await api(`/api/query?kind=defs&target=${encodeURIComponent(d.symbol)}`);
      const r = res && res.references && res.references[0];
      if (r) MK.openFile(r.path, r.line, d.symbol);
      else { open.disabled = true; open.textContent = "no definition in the graph"; }
    };
    row.append(open);
    if (d.symbol) {
      for (const v of ["ripple", "focus"]) {
        const b = el("button", "btn ghost", v);
        b.type = "button";
        b.onclick = () => show(v, d.symbol);
        row.append(b);
      }
    }
    box.append(row);
  }

  MK.wireGraph = function wireGraph() {
    for (const b of document.querySelectorAll("button[data-gview]")) b.onclick = () => show(b.dataset.gview);
    $("g-go").onclick = () => show();
    for (const id of ["g-symbol", "g-to"]) $(id).onkeydown = (ev) => { if (ev.key === "Enter") { ev.preventDefault(); show(); } };
    $("g-depth").onchange = () => show();
    $("g-organic").onchange = () => { if (G.data) draw(G.data); };
    for (const b of document.querySelectorAll("button[data-gview]")) b.classList.toggle("active", b.dataset.gview === G.view);
  };
  MK.onModeGraph = (mode) => {
    if (mode !== "graph") return;
    if (!$("g-symbol").value && MK.ui.inspSym) show(G.view, MK.ui.inspSym);
    if (G.cy) setTimeout(() => { G.cy.resize(); fit(); }, 0);
  };
})();
