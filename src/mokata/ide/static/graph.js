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
        // Width from the label length (monospace), not the deprecated label-sized width: that value is
        // only resolved when a layout measures the nodes, so a node placed by position (the ring
        // layout) stayed zero-width — invisible — and the ripple showed the centre alone.
        width: (n) => Math.max(18, String(n.data("label") || "").length * (n.data("kind") === "center" ? 7.9 : 6.7)), height: 24, padding: "7px", "background-color": c.panel, "border-width": 1, "border-color": c.line2 } },
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

  // Rings around the symbol, each ring turned a little from the last: with a plain concentric
  // layout a lone node per ring lined up on one ray, so an edge that skips a ring (a test calling
  // the symbol directly) ran straight THROUGH the node between and read as that node's edge.
  function ringPositions(data) {
    const byRing = {};
    for (const n of data.nodes) (byRing[n.ring || 0] = byRing[n.ring || 0] || []).push(n.id);
    const pos = {};
    // Circles are spaced by RANK, not by hop count: tests carry an outer ring number, and an empty
    // ring between them and the callers only shrank the picture.
    const rings = Object.keys(byRing).map(Number).sort((a, b) => a - b);
    let last = 0;
    for (const ring of rings) {
      const ids = byRing[ring];
      const rank = rings.indexOf(ring);
      const radius = rank === 0 ? 0 : Math.max(last + 110, (ids.length * 110) / (2 * Math.PI));
      last = radius;
      const base = Math.PI + rank * 0.7;
      ids.forEach((id, i) => {
        const a = base + (2 * Math.PI * i) / ids.length;
        pos[id] = { x: radius * Math.cos(a), y: radius * Math.sin(a) };
      });
    }
    return pos;
  }
  const RINGED = (data) => !$("g-organic").checked && (data.view === "ripple" || data.view === "untested");

  function elements(data) {
    const pos = RINGED(data) ? ringPositions(data) : {};
    const nodes = data.nodes.map((n) => (pos[n.id] ? { data: { ...n }, position: pos[n.id] } : { data: { ...n } }));
    const cov = {};
    for (const n of data.nodes) cov[n.id] = n.coverage;
    const edges = data.edges.map((e) => ({ data: { ...e, risk: cov[e.source] === "untested" ? 1 : 0 } }));
    return nodes.concat(edges);
  }

  function layoutFor(data) {
    if ($("g-organic").checked) return { name: "fcose", animate: false, randomize: true, nodeSeparation: 90, idealEdgeLength: 110 };
    if (RINGED(data)) return { name: "preset", animate: false, fit: false };
    return { name: "dagre", rankDir: "LR", nodeSep: 26, rankSep: 90, animate: false };
  }

  // What each view answers, in the user's words — shown under the bar so the picture is never a puzzle.
  const VIEW_HINT = {
    ripple: "Ripple — what is affected if this symbol changes: callers ring by ring (ring 1 = calls it directly), tests on the outer ring, amber = no test reaches it.",
    focus: "Focus — one step each way: who calls this symbol (left) and what it calls (right).",
    path: "Path to… — the shortest chain of calls from the first symbol to the second. Fill in both names.",
    untested: "Untested paths — the caller chains into this symbol that no test runs: where a change could break something unnoticed.",
  };
  const EMPTY_TEXT = {
    ripple: "Ripple then shows everything that would be affected if you changed it.",
    focus: "Focus then shows who calls it and what it calls.",
    path: "Path to… needs two: pick the start in the first box and the end in the second — the shortest chain of calls between them appears here.",
    untested: "Untested paths then shows the callers of it that no test runs.",
  };
  function hint() {
    $("g-hint").textContent = VIEW_HINT[G.view] || "";
    $("g-empty-text").textContent = EMPTY_TEXT[G.view] || "";
  }

  // --- the symbol picker: type a few letters, choose a real symbol (name · kind · file:line) ----
  const SUG = { gen: 0, rows: [], at: -1, input: null, timer: 0 };
  function sugHide() { $("g-sug").hidden = true; SUG.rows = []; SUG.at = -1; }
  function sugPlace(input) {
    const box = $("g-sug");
    const r = input.getBoundingClientRect();
    const host = box.parentElement.getBoundingClientRect();
    box.style.left = Math.max(0, r.left - host.left) + "px";
    box.style.top = "0px";
    box.style.minWidth = Math.max(280, r.width) + "px";
  }
  function sugRender(syms, input) {
    const box = $("g-sug");
    box.replaceChildren();
    SUG.rows = syms;
    SUG.at = syms.length ? 0 : -1;
    if (!syms.length) {
      box.append(el("li", "g-sug-empty dim small", input.value.trim()
        ? "No function or class by that name — check the spelling, or try part of it."
        : "Start typing a function or class name."));
    }
    syms.forEach((sy, i) => {
      const li = el("li", "g-sug-row" + (i === SUG.at ? " active" : ""));
      li.setAttribute("role", "option");
      li.append(el("span", "mono", sy.name), el("span", "g-kind", sy.kind || "symbol"),
                el("span", "dim small mono", `${sy.path}${sy.line ? ":" + sy.line : ""}`));
      li.onmousedown = (ev) => { ev.preventDefault(); sugPick(i); };
      box.append(li);
    });
    sugPlace(input);
    box.hidden = false;
  }
  function sugMove(d) {
    if (!SUG.rows.length) return;
    SUG.at = (SUG.at + d + SUG.rows.length) % SUG.rows.length;
    [...$("g-sug").children].forEach((li, i) => li.classList.toggle("active", i === SUG.at));
  }
  function sugPick(i) {
    const sy = SUG.rows[i];
    if (!sy || !SUG.input) return;
    SUG.input.value = sy.name;
    sugHide();
    if (G.view === "path" && SUG.input.id === "g-symbol" && !$("g-to").value.trim()) { $("g-to").focus(); return; }
    show();
  }
  function sugQuery(input) {
    SUG.input = input;
    clearTimeout(SUG.timer);
    SUG.timer = setTimeout(async () => {
      const gen = ++SUG.gen;
      const q = input.value.trim();
      if (!q) { sugRender([], input); return; }
      const res = await api("/api/quickopen", { q, changed: [] }).catch(() => null);
      if (gen !== SUG.gen || document.activeElement !== input) return;
      sugRender(((res && res.symbols) || []).slice(0, 8), input);
    }, 120);
  }

  // --- suggestions on the empty stage: the functions/classes in the files changed in this tree ---
  async function suggest() {
    const box = $("g-suggest");
    if (!box || box.dataset.done) return;
    box.dataset.done = "1";
    const files = ((MK.ui && MK.ui.files) || []).filter((f) => f.status !== "deleted" && !f.binary).slice(0, 6);
    const picks = [];
    for (const f of files) {
      // The definitions this change TOUCHED (from the Review page's impact answer) come first.
      const im = (MK.ui.impact || {})[f.path];
      const touched = im ? (im.symbols || []).concat(im.pending || []) : [];
      if (touched.length) {
        for (const sy of touched.slice(0, 3)) picks.push({ name: sy.name, kind: sy.kind || "function", line: sy.line, path: f.path });
        if (picks.length >= 12) break;
        continue;
      }
      const o = await api("/api/outline?path=" + encodeURIComponent(f.path)).catch(() => null);
      let fromFile = 0;                     // a few per file, so one big file cannot crowd out the rest
      for (const sy of (o && o.symbols) || []) {
        if (["function", "class", "method"].includes(sy.kind)) { picks.push({ ...sy, path: f.path }); fromFile++; }
        if (picks.length >= 12 || fromFile >= 3) break;
      }
      if (picks.length >= 12) break;
    }
    box.replaceChildren();
    if (!picks.length) return;
    box.append(el("p", "small", "Changed here:"));
    const wrap = el("div", "chips");
    for (const sy of picks) {
      const b = el("button", "chip g-chip");
      b.type = "button";
      b.title = `${sy.kind} in ${sy.path}:${sy.line} — show it`;
      b.append(el("span", "mono", sy.name), el("span", "dim small", " " + sy.kind));
      b.onclick = () => show(G.view === "path" ? "ripple" : G.view, sy.name);
      wrap.append(b);
    }
    box.append(wrap);
  }

  async function show(view, symbol, to) {
    G.view = view || G.view;
    hint();
    for (const b of document.querySelectorAll("button[data-gview]")) b.classList.toggle("active", b.dataset.gview === G.view);
    $("g-to-wrap").hidden = G.view !== "path";
    if (symbol !== undefined) $("g-symbol").value = symbol;
    if (to !== undefined) $("g-to").value = to;
    const sym = $("g-symbol").value.trim();
    const dst = $("g-to").value.trim();
    $("g-sel").hidden = true;
    if (!sym || (G.view === "path" && !dst)) {
      $("g-empty").hidden = false;
      if (G.cy) G.cy.elements().remove();
      G.data = null;
      for (const id of ["g-title", "g-summary", "g-note"]) $(id).textContent = "";
      $("g-chains").replaceChildren(); $("g-chains-h").hidden = true;
      $("g-guide").open = true;
      suggest();
      return;
    }
    $("g-empty").hidden = true;
    $("g-title").textContent = "asking the graph…";
    const depth = G.view === "path" ? Math.max(3, +$("g-depth").value) : +$("g-depth").value;
    const url = `/api/graph?view=${G.view}&symbol=${encodeURIComponent(sym)}&to=${encodeURIComponent(dst)}&depth=${depth}`;
    const [data] = await Promise.all([api(url).catch(() => null), loadLibs().catch((e) => { G.libError = e; })]);
    if (G.libError) { $("g-title").textContent = "graph view unavailable"; $("g-note").textContent = String(G.libError.message); return; }
    if (!data || data.error || data.sample) {
      $("g-title").textContent = data && data.timed_out ? "timed out" : "no graph";
      $("g-summary").textContent = data ? (data.note || data.error) : "the server did not answer";
      if (data) MK.markSample(data);
      if (G.cy) G.cy.elements().remove();
      return;
    }
    G.data = data;
    if (!G.guidePinned) $("g-guide").open = false;     // the picture gets the room; ? reopens it
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
      const rings = Object.keys(byRing).sort().map((r) => `${byRing[r]} at ${r} hop(s)`).join(", ")
        || `nothing in this repo calls ${sym} yet (new, unused, or only called from outside) — Focus shows what it calls`;
      sum.textContent = data.defs === 0
        ? `The graph holds no definition of ${sym} — nothing here is claimed about it.`
        : `${rings}; ${tests} test node(s). Edges: ${bases}.`;
      const ch = data.untested_chains || [];
      if (ch.length) {
        chainsH.hidden = false;
        // lane S: the list is capped — say so, with the count (or a floor of it) behind it.
        const total = data.untested_chains_total || ch.length;
        chainsH.textContent = total > ch.length
          ? `showing ${ch.length} of ${data.untested_chains_exact === false ? "at least " : ""}${total} path(s) that reach no test`
          : `${ch.length} path(s) reach no test`;
        for (const c of ch) chains.append(el("li", "", c.join(" → ")));
      }
    }
    const notes = [];
    if (data.graph_note) { notes.push(data.graph_note); if (MK.noteGraph) MK.noteGraph(data.graph_note); }
    if (data.truncated) notes.push(data.truncation_note || `stopped at ${data.max_nodes} nodes — lower the depth`);
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
    for (const id of ["g-symbol", "g-to"]) {
      const input = $(id);
      input.oninput = () => sugQuery(input);
      input.onfocus = () => { if (input.value.trim()) sugQuery(input); };
      input.onblur = () => setTimeout(sugHide, 120);
      input.onkeydown = (ev) => {
        const open = !$("g-sug").hidden && SUG.input === input;
        if (open && ev.key === "ArrowDown") { ev.preventDefault(); sugMove(1); }
        else if (open && ev.key === "ArrowUp") { ev.preventDefault(); sugMove(-1); }
        else if (open && ev.key === "Escape") { sugHide(); }
        else if (ev.key === "Enter") {
          ev.preventDefault();
          if (open && SUG.at >= 0) sugPick(SUG.at); else { sugHide(); show(); }
        }
      };
    }
    $("g-depth").onchange = () => show();
    $("g-organic").onchange = () => { if (G.data) draw(G.data); };
    for (const b of document.querySelectorAll("button[data-gview]")) b.classList.toggle("active", b.dataset.gview === G.view);
    const guide = $("g-guide");
    $("g-help").onclick = () => {
      guide.open = !guide.open;
      G.guidePinned = guide.open;
      if (guide.open) guide.scrollIntoView({ block: "start" });
    };
    guide.querySelector("summary").addEventListener("click", () => setTimeout(() => { G.guidePinned = guide.open; }, 0));
    hint();
  };
  MK.onModeGraph = (mode) => {
    if (mode !== "graph") return;
    const eng = ($("st-graph").textContent || "").trim();
    const FULL = "install the code graph for proven, multi-language edges: mokata setup claude --mode full";
    $("g-engine").textContent = !eng || eng === "…" ? "checking…"
      : /code-review-graph|crg/i.test(eng) ? `${eng} — the full code graph`
      : eng === "ast" ? `ast — mokata's built-in Python reader (Python files only; ${FULL})`
      : eng === "grep" ? `grep — text search only, so arrows are name matches (${FULL})`
      : /none|unavailable/i.test(eng) ? `${eng} — ${FULL}` : eng;
    if (!$("g-symbol").value && MK.ui.inspSym) show(G.view, MK.ui.inspSym);
    if (G.cy) setTimeout(() => { G.cy.resize(); fit(); }, 0);
  };
})();
