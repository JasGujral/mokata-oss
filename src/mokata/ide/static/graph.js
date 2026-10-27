// mokata ide — Graph: the blast-radius ripple, symbol focus, call-path tracer and untested-path
// finder, drawn with the VENDORED Cytoscape.js (+ fcose, + dagre) from /static/vendor/ — never a
// CDN. Every node and edge comes from the server's graph answer; nothing is drawn that the graph
// cannot justify, and every edge shows its basis (solid = structural, dashed = lexical).
// SAFETY: labels go into the canvas as text and into the page through textContent only.
"use strict";

(function () {
  const MK = window.MK;
  const { $, el, api } = MK;
  const G = (MK.graph = { view: "ripple", cy: null, loading: null, data: null, open: new Map() });
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
      // P37 — a CLUSTER stands for the nodes not drawn (by directory, with the count); click opens it.
      { selector: "node[kind = 'cluster']", style: { shape: "round-rectangle", "border-style": "double", "border-width": 3,
        "border-color": c.text2, color: c.text2, "background-color": c.panel } },
      { selector: "node[kind = 'cluster'][untested > 0]", style: { "border-color": c.amber } },
      { selector: "edge[kind = 'GROUP']", style: { width: "mapData(count, 1, 50, 1.2, 5)", "line-style": "solid", opacity: 0.55 } },
      // P37 — drawn from the code graph's index, not yet confirmed by its answer: faded until it is.
      { selector: "node[?pending]", style: { opacity: 0.55 } },
      { selector: "edge[?pending]", style: { opacity: 0.4 } },
    ];
  }

  // P37 (R24) — labels are cut to LABEL_MAX characters (the full name is in the details panel)
  // and every ring is packed into as many concentric BANDS as it needs, so 150 nodes take a disc,
  // not one huge circle that only fits on screen at an unreadable zoom.
  const LABEL_MAX = 30;
  const CHAR_W = 6.7;
  function shortLabel(n) {
    let t = String(n.label || n.id);
    if (n.kind === "site" || n.kind === "module") t = t.split("/").pop();
    return t.length > LABEL_MAX ? t.slice(0, LABEL_MAX - 1) + "…" : t;
  }
  function ringPositions(nodes) {
    const byRing = {};
    for (const n of nodes) (byRing[n.ring || 0] = byRing[n.ring || 0] || []).push(n);
    const pos = {};
    // Rings are spaced by RANK, not by hop count: an empty ring only shrank the picture.
    const rings = Object.keys(byRing).map(Number).sort((a, b) => a - b);
    let radius = 0;
    rings.forEach((ring, rank) => {
      const list = byRing[ring];
      if (rank === 0) { list.forEach((n, i) => { pos[n.id] = { x: 0, y: i * 40 }; }); return; }
      let i = 0;
      let band = 0;
      radius += 80;
      while (i < list.length) {
        const w = Math.max(...list.slice(i, i + 40).map((n) => (n.short || "").length * CHAR_W + 30));
        const cap = Math.max(4, Math.floor((2 * Math.PI * radius) / w));
        const take = list.slice(i, i + cap);
        // each band (and ring) turned a little: a lone node per ring would line up on one ray
        const base = Math.PI + rank * 0.7 + band * 0.37;
        take.forEach((n, k) => {
          const ang = base + (2 * Math.PI * k) / take.length;
          pos[n.id] = { x: radius * Math.cos(ang), y: radius * Math.sin(ang) };
        });
        i += take.length;
        band += 1;
        if (i < list.length) radius += 44;
      }
    });
    return pos;
  }
  const RINGED = (data) => data.view === "ripple" || data.view === "untested";

  // P37 (R24, G19) — what is drawn: the server's draw plan (≤150: the most relevant nodes, one
  // CLUSTER per directory group for the rest), plus the clusters this page opened (G.open).
  function visible(data) {
    const plan = data.draw || { shown: data.nodes.map((n) => n.id), clusters: [], limit: 150 };
    const byId = new Map(data.nodes.map((n) => [n.id, n]));
    const shown = new Set(plan.shown);
    const clusters = [];
    for (const c of plan.clusters) {
      const opened = (G.open.get(c.id) || 0);
      c.members.slice(0, opened).forEach((id) => shown.add(id));
      const left = c.members.slice(opened);
      if (left.length) clusters.push({ ...c, members: left, count: left.length, opened });
    }
    const of = new Map();
    for (const c of clusters) for (const id of c.members) of.set(id, c.id);
    const nodes = [...shown].map((id) => byId.get(id)).filter(Boolean).map((n) => ({ ...n, short: shortLabel(n) }));
    for (const c of clusters) {
      const label = (c.opened ? "+" : "") + c.label.replace(/ · \d+$/, " · " + c.count);
      nodes.push({ id: c.id, kind: "cluster", label, short: label.length > LABEL_MAX + 8 ? "…" + label.slice(-(LABEL_MAX + 7)) : label,
        ring: c.ring, count: c.count, untested: c.untested || 0, cluster: c, symbol: null });
    }
    const edges = new Map();
    for (const e of data.edges) {
      const s = shown.has(e.source) ? e.source : of.get(e.source);
      const t = shown.has(e.target) ? e.target : of.get(e.target);
      if (!s || !t || s === t) continue;
      if (s === e.source && t === e.target) { edges.set(e.id, e); continue; }
      const key = `g:${s}>${t}`;
      const g = edges.get(key);
      if (g) { g.count += 1; if (e.basis === "lexical") g.basis = "lexical"; }
      else edges.set(key, { id: key, source: s, target: t, kind: "GROUP", basis: e.basis, count: 1, site: e.site, pending: e.pending });
    }
    return { nodes, edges: [...edges.values()], clusters, limit: plan.limit || 150, total: plan.total || data.nodes.length };
  }

  function elements(data, vis) {
    const pos = RINGED(data) ? ringPositions(vis.nodes) : {};
    const cov = {};
    for (const n of vis.nodes) cov[n.id] = n.coverage;
    const nodes = vis.nodes.map((n) => {
      const d = { ...n, label: n.short };
      delete d.cluster;
      d.full = n.label;
      return pos[n.id] ? { data: d, position: pos[n.id] } : { data: d };
    });
    const edges = vis.edges.map((e) => ({ data: { ...e, risk: cov[e.source] === "untested" ? 1 : 0 } }));
    return nodes.concat(edges);
  }

  function layoutFor(data) {
    // P37 (§5.1: "organic" → auto) — rings for what-breaks, a left-to-right chain for who-calls
    // and path, and the organic layout when a who-calls picture is too tall for one column.
    if (!RINGED(data) && data.view === "focus" && (G.vis ? G.vis.nodes.length : data.nodes.length) > 60) {
      return { name: "fcose", animate: false, randomize: true, nodeSeparation: 90, idealEdgeLength: 110 };
    }
    if (RINGED(data)) return { name: "preset", animate: false, fit: false };
    return { name: "dagre", rankDir: "LR", nodeSep: 26, rankSep: 90, animate: false };
  }

  // What each view answers, in the user's words — shown under the bar so the picture is never a puzzle.
  const VIEW_HINT = {
    ripple: "What breaks — what is affected if this symbol changes: callers ring by ring (ring 1 = calls it directly), tests on the outer ring, amber = no test reaches it.",
    focus: "Who calls — one step each way: who calls this symbol (left) and what it calls (right).",
    path: "Path — the shortest chain of calls from the first symbol to the second. Fill in both names.",
    untested: "What breaks, untested only — the caller chains into this symbol that no test runs: where a change could break something unnoticed.",
  };
  const EMPTY_TEXT = {
    ripple: "What breaks then shows everything that would be affected if you changed it.",
    focus: "Who calls then shows who calls it and what it calls.",
    path: "Path needs two: pick the start in the first box and the end in the second — the shortest chain of calls between them appears here.",
    untested: "Untested only then shows the callers of it that no test runs.",
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

  async function show(view, symbol, to, page) {
    G.page = page || 1;
    G.view = view || G.view;
    hint();
    for (const b of document.querySelectorAll("button[data-gview]")) b.classList.toggle("active", b.dataset.gview === G.view || (b.dataset.gview === "ripple" && G.view === "untested"));
    $("g-untested").checked = G.view === "untested";
    $("g-untested-wrap").hidden = G.view === "focus" || G.view === "path";
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
      clearChrome(); message();
      suggest();
      return;
    }
    $("g-empty").hidden = true;
    if (G.page === 1) G.open = new Map();              // a new question starts with every group closed
    message();
    // P32 — the question is a job: its progress is shown ON the canvas (§5.4 — the old "asking the
    // graph…" sat in #g-title, under the guide, off-screen), its rings are drawn as they arrive,
    // and a newer question aborts this one (its job is left; nobody else wants it, so it stops).
    if (G.ctl) G.ctl.abort();
    const ctl = (G.ctl = new AbortController());
    const gen = (G.gen = (G.gen || 0) + 1);
    const strip = progressStrip();
    strip.say(`asking the graph: ${G.view} of ${sym}${dst ? " → " + dst : ""}…`);
    $("cy").classList.add("stale");                   // the old picture is not this question's
    for (const id of ["g-title", "g-summary", "g-note"]) $(id).textContent = "";
    $("g-title").textContent = `${G.view} · ${sym}${G.view === "path" ? " → " + dst : ""}`;
    const depth = G.view === "path" ? Math.max(3, +$("g-depth").value) : +$("g-depth").value;
    const url = `/api/graph?view=${G.view}&symbol=${encodeURIComponent(sym)}&to=${encodeURIComponent(dst)}&depth=${depth}${G.page > 1 ? "&page=" + G.page : ""}`;
    const libs = loadLibs().catch((e) => { G.libError = e; });
    const opts = {
      signal: ctl.signal,
      onJob: (j) => { if (gen === G.gen) strip.set(j); },
      onPartial: async (part) => {
        await libs;
        if (gen !== G.gen || G.libError || !part || !part.nodes) return;
        G.data = part;
        $("cy").classList.remove("stale");
        draw(part);
        summarize(part);
      },
    };
    let data;
    try {
      [data] = await Promise.all([api(url, undefined, opts), libs]);
    } catch (e) {
      if (e && e.name === "AbortError") return;      // superseded: the newer question owns the canvas
      data = null;
    }
    if (gen !== G.gen) return;
    if (G.libError) { strip.done(); $("g-title").textContent = "graph view unavailable"; $("g-note").textContent = String(G.libError.message); return; }
    if (!data || data.error || data.sample) {
      if (data && (data.cancelled || data.failed) && data.job) strip.set(data.job); else strip.done();
      $("cy").classList.remove("stale");
      if (data) MK.markSample(data);
      const keep = data && (data.cancelled || data.failed) && G.data && G.data.partial && G.cy;
      if (G.cy && !keep) G.cy.elements().remove();
      if (keep) status(G.data);
      else clearChrome();
      failedState(data, keep, sym, dst);
      return;
    }    strip.done();
    $("cy").classList.remove("stale");
    G.data = data;
    if (!G.guidePinned) $("g-guide").open = false;     // the picture gets the room; ? reopens it
    draw(data);
    summarize(data);
  }

  // §5.4 — never blank, errors name what failed, keep the partial picture, offer Retry + the CLI
  // twin. The message sits ON the canvas (the region the user is looking at).
  function cliTwin(sym, dst) {
    return G.view === "path" ? `mokata query callees ${sym} --json (then each callee, toward ${dst})`
      : `mokata query callers ${sym} --json`;
  }
  function msgHost() {
    let box = $("g-msg");
    if (!box) {
      box = el("div", "g-msg small");
      box.id = "g-msg";
      document.querySelector(".graph-stage").append(box);
    }
    return box;
  }
  function message(text, opts) {
    const box = msgHost();
    if (!text) { box.hidden = true; box.replaceChildren(); return; }
    MK.regionSay(box, "error", text, opts);
    box.hidden = false;
  }
  function failedState(data, kept, sym, dst) {
    const cancelled = data && data.cancelled;
    const what = !data ? "the graph question could not be asked (the IDE server is not reachable)"
      : cancelled ? "cancelled" : data.sample ? (data.note || "the SAMPLE has no code graph")
      : `the graph question failed — ${data.note || data.error}`;
    $("g-title").textContent = cancelled ? `${G.view} · ${sym} — cancelled` : `${G.view} · ${sym} — not answered`;
    // R25 — a kept partial picture still says its basis, the same word as its status line
    $("g-summary").textContent = kept ? `${what}; the partial picture so far is kept (labelled partial). Basis: ${basisWord(G.data)}.` : what + ".";
    if (data && data.sample) { message(what); return; }
    message(kept ? `${what} — showing the partial picture.` : what, { retry: () => show(G.view, undefined, undefined, G.page), cli: cliTwin(sym, dst) });
  }

  function progressStrip() {
    if (!G.strip && MK.jobs) G.strip = MK.jobs.strip(document.querySelector(".graph-stage"), "on-canvas");
    return G.strip || { say() {}, set() {}, done() {} };
  }
  MK.openGraph = (view, symbol, to) => { MK.setMode("graph"); show(view, symbol, to); };

  function draw(data) {
    const box = $("cy");
    if (!G.cy) {
      G.cy = window.cytoscape({ container: box, style: style(), wheelSensitivity: 0.3, minZoom: 0.05, maxZoom: 3 });
      G.cy.on("tap", "node", (ev) => { const d = ev.target.data(); if (d.kind === "cluster") openCluster(d.id); else select(d, "node"); });
      G.cy.on("tap", "edge", (ev) => select(ev.target.data(), "edge"));
      G.cy.on("dbltap", "node", (ev) => { const d = ev.target.data(); if (d.symbol) show(G.view === "path" ? "ripple" : G.view, d.symbol); });
      if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => G.cy.style(style()));
    }
    const vis = visible(data);
    G.vis = vis;
    G.cy.elements().remove();
    G.cy.add(elements(data, vis));
    G.cy.layout(layoutFor(data)).run();
    fit();
    status(data);
  }

  // A cluster opens into its members, most relevant first, never past the draw limit: when the
  // picture is full, the clusters opened before give their room back first.
  function openCluster(id) {
    const data = G.data;
    if (!data || !G.vis) return;
    const c = G.vis.clusters.find((x) => x.id === id);
    if (!c) return;
    let room = G.vis.limit - G.vis.nodes.length + 1;          // + the cluster node itself
    if (room < Math.min(c.count, 12)) {
      for (const k of [...G.open.keys()]) if (k !== id) { room += G.open.get(k); G.open.delete(k); }
    }
    const take = c.count <= room ? c.count : Math.max(1, room - 1);
    G.open.set(id, (G.open.get(id) || 0) + take);
    draw(data);
  }

  // P37 (R24) — at fit every label is drawn at ≥ 11 px: when the whole picture only fits below
  // 1:1 the view centres on the symbol at 1:1 instead (the most relevant rings are around it),
  // and the status line says the rest is a scroll away ("fit all" shows everything, smaller).
  function fit(all) {
    G.cy.fit(undefined, 40);
    G.fitAll = !!all;
    if (G.cy.zoom() > 1.25) { G.cy.zoom(1.25); G.cy.center(); }
    else if (!all && G.cy.zoom() < 1) {
      const c = G.cy.nodes("[kind = 'center']").first();
      G.cy.zoom(1);
      if (c && c.length) G.cy.center(c); else G.cy.center();
    }
  }

  // --- P37 (R25): the ONE basis word — status line, legend and summary all say data.basis -----
  const BASIS_SAYS = {
    structural: "structural — every edge is a call or import the code graph proved",
    approximate: "approximate — some edges are name matches (dashed), the rest are proved",
    floor: "floor — no code graph answered: every edge is a name match (lexical floor)",
    pending: "pending — nothing answered yet",
  };
  function basisWord(data) { return (data && data.basis) || "pending"; }
  function ensureChrome() {
    const stage = document.querySelector(".graph-stage");
    if (!$("g-status")) {
      const st = el("div", "g-status small");
      st.id = "g-status";
      st.setAttribute("role", "status");
      st.hidden = true;
      const txt = el("span", "g-status-text");
      txt.id = "g-status-text";
      const all = el("button", "btn ghost small", "fit all");
      all.type = "button";
      all.id = "g-fit-all";
      all.onclick = () => { if (G.cy) { if (G.fitAll) fit(); else fit(true); all.textContent = G.fitAll ? "read size" : "fit all"; } };
      const more = el("button", "btn small", "Continue");
      more.type = "button";
      more.id = "g-continue";
      more.hidden = true;
      more.title = "Draw the next page of this picture — the walk resumes where it stopped";
      more.onclick = () => { const d = G.data; if (d) show(G.view, undefined, undefined, (d.page || 1) + 1); };
      st.append(txt, more, all);
      stage.append(st);
    }
    const legend = stage.querySelector(".legend");
    if (legend && !$("g-legend-basis")) {
      const b = el("span", "g-legend-basis");
      b.id = "g-legend-basis";
      legend.prepend(b);
    }
  }
  function status(data) {
    ensureChrome();
    const word = basisWord(data);
    const vis = G.vis;
    const parts = [];
    if (data.partial) parts.push(data.view === "path" ? "partial — a path from the index, being confirmed"
      : `partial — ${data.rings_done || 0} of ${data.depth || "?"} ring(s) confirmed` + (data.index_rings ? `, ${data.index_rings} read from the index (faded = not yet confirmed)` : ""));
    else parts.push("done");
    parts.push("basis: " + word);
    if (vis && vis.total > vis.nodes.length - vis.clusters.length) {
      parts.push(`showing ${vis.nodes.length - vis.clusters.length} of ${vis.total} nodes, ${vis.clusters.length} group(s) — click a group to open it`);
    }
    if (G.cy && !G.fitAll && G.cy.zoom() <= 1 && vis && vis.nodes.length > 30) parts.push("centred at reading size — scroll or “fit all” for the rest");
    // P37 (U10 overruled: no hard cap) — a full page says so and offers the next one.
    if (data.more && !data.partial) parts.push(data.more_note || "more to draw");
    $("g-status-text").textContent = parts.join(" · ");
    $("g-continue").hidden = !(data.more && !data.partial);
    $("g-status").hidden = false;
    $("g-status").dataset.basis = word;
    const lg = $("g-legend-basis");
    lg.replaceChildren(el("b", "", "basis: " + word));
    lg.title = BASIS_SAYS[word] || word;
    lg.dataset.basis = word;
  }
  function clearChrome() {
    ensureChrome();
    $("g-status").hidden = true;
    $("g-legend-basis").replaceChildren();
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
      title.textContent = data.partial ? `If ${sym} changes — ring ${data.rings_done || 0} so far` : `If ${sym} changes`;
      const rings = Object.keys(byRing).sort().map((r) => `${byRing[r]} at ${r} hop(s)`).join(", ")
        || (data.partial ? `no caller yet`
          : basisWord(data) === "structural" ? `0 callers (structural): nothing in this repo calls ${sym} (new, unused, or only called from outside) — Focus shows what it calls`
          : `no caller of ${sym} found by name match — that is not a verified zero (no code graph answered)`);
      sum.textContent = data.partial ? `showing partial results — ${rings}. Edges so far: ${bases}.`
        : data.defs === 0
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
    // R25 — the summary says the same basis word as the status line and the legend
    sum.textContent = `${sum.textContent.replace(/\s+$/, "")} Basis: ${basisWord(data)}.`;
    const notes = [];
    // R25 — the status bar is told "degraded" only when THIS picture is not structural: the last
    // degrade any question saw must not contradict the basis this picture says.
    if (data.graph_note && basisWord(data) !== "structural") { notes.push(data.graph_note); if (MK.noteGraph) MK.noteGraph(data.graph_note); }
    if (data.more && data.more_note) notes.push(data.more_note);
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
    box.append(el("b", "mono", d.full || d.label));
    box.append(el("div", "small dim", `${d.pending ? "from the index, not yet confirmed · " : ""}${d.kind}${d.coverage ? " · " + d.coverage : ""}${d.path ? " · " + d.path + ":" + d.line : ""}`));
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
      for (const [v, name] of [["ripple", "what breaks"], ["focus", "who calls"]]) {
        const b = el("button", "btn ghost", name);
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
    $("g-untested").onchange = () => show($("g-untested").checked ? "untested" : "ripple");
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
    loadLibs().catch(() => null);                  // P37: the canvas is ready before the first ring
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
