// mokata ide — Navigate: the explorer, the outline, a read-only file with its changes in the
// gutter, and the structural questions (defs / refs / callers / callees / implementers / blast
// radius) — every answer is `mokata query`'s, byte for byte, and carries its basis.
// SAFETY: repo text enters the page through textContent only.
"use strict";

(function () {
  const MK = window.MK;
  const { $, el, ui, api } = MK;

  const nav = (MK.nav = {
    tree: null, path: null, file: null, outline: null, line: 1, sym: null, tab: "callers",
    changeIdx: -1, hist: [], histIdx: -1, collapsed: {}, answers: {},
  });

  const KINDS = [
    ["defs", "Defs"], ["callers", "Callers"], ["callees", "Callees"], ["refs", "Refs"],
    ["implementers", "Implementers"], ["blast_radius", "Blast radius"], ["history", "History"],
  ];

  // ----------------------------------------------------------------------------- explorer
  function changedSet() {
    const m = {};
    for (const f of ui.files || []) if (f.status !== "deleted") m[f.path] = f;
    return m;
  }

  async function loadTree(force) {
    if (nav.tree && !force) { renderTree(); return; }
    const t = await api("/api/tree").catch(() => null);
    if (!t) return;
    MK.markSample(t);
    nav.tree = t;
    $("tree-count").textContent = `${t.count}${t.truncated ? " (first " + t.files.length + ")" : ""}`;
    $("tree-note").textContent = t.pruned && t.pruned !== "SAMPLE" ? "venv/, .venv/, conda envs and vendor dirs are never listed — they are not your code." : "";
    renderTree();
  }
  MK.loadTree = loadTree;

  function buildTree(files) {
    const root = { dirs: {}, files: [] };
    for (const p of files) {
      const parts = p.split("/");
      let n = root;
      for (let i = 0; i < parts.length - 1; i++) {
        n = n.dirs[parts[i]] || (n.dirs[parts[i]] = { dirs: {}, files: [] });
      }
      n.files.push(p);
    }
    return root;
  }

  function renderTree() {
    const ul = $("tree");
    ul.replaceChildren();
    if (!nav.tree) return;
    const changed = changedSet();
    const only = $("only-changed").checked;
    const flt = $("tree-filter").value.trim().toLowerCase();
    let files = nav.tree.files;
    if (only) files = files.filter((p) => changed[p]);
    if (flt) files = files.filter((p) => p.toLowerCase().includes(flt));
    const tree = buildTree(files);
    const counts = {};
    for (const p of Object.keys(changed)) {
      const parts = p.split("/");
      for (let i = 1; i < parts.length; i++) { const d = parts.slice(0, i).join("/"); counts[d] = (counts[d] || 0) + 1; }
    }
    let rows = 0;
    const LIMIT = 4000;
    const walk = (node, prefix, depth) => {
      for (const name of Object.keys(node.dirs).sort()) {
        if (rows > LIMIT) return;
        const full = prefix ? `${prefix}/${name}` : name;
        const isOpen = flt || only || nav.collapsed[full] === false || (nav.collapsed[full] === undefined && depth < 1);
        const b = el("button", "tree-row dir");
        b.type = "button";
        b.style.paddingLeft = `${6 + depth * 14}px`;
        b.append(el("span", "caret", isOpen ? "▾" : "▸"), el("span", "", name));
        if (counts[full]) { b.append(el("span", "chg-dot", "●")); b.append(el("span", "dim small", String(counts[full]))); }
        b.onclick = () => { nav.collapsed[full] = isOpen; renderTree(); };
        ul.append(MK.wrapLi(b));
        rows++;
        if (isOpen) walk(node.dirs[name], full, depth + 1);
      }
      for (const p of node.files.sort()) {
        if (rows > LIMIT) return;
        const f = changed[p];
        const b = el("button", "tree-row file" + (p === nav.path ? " active" : ""));
        b.type = "button";
        b.style.paddingLeft = `${6 + depth * 14 + 14}px`;
        b.append(el("span", "", p.slice(p.lastIndexOf("/") + 1)));
        if (f) { const L = MK.ST_LETTER[f.status] || "M"; b.append(el("span", "st st-" + L, L)); }
        b.onclick = () => openFile(p, 1, null);
        ul.append(MK.wrapLi(b));
        rows++;
      }
    };
    walk(tree, "", 0);
    if (rows > LIMIT) ul.append(el("li", "dim small", "… more — use ⌘P or the filter"));
    if (!files.length) ul.append(el("li", "dim small", only ? "No changed file in the tree." : "No file."));
  }

  // ----------------------------------------------------------------------------- one file
  async function openFile(path, line, sym, opts) {
    const o = opts || {};
    MK.setMode("navigate");
    if (!o.fromHistory) pushHistory({ path, line: line || 1, sym: sym || null });
    nav.line = line || 1;
    if (nav.path !== path || o.reload) {
      nav.path = path;
      const [file, outline] = await Promise.all([
        api("/api/file?path=" + encodeURIComponent(path)).catch(() => null),
        api("/api/outline?path=" + encodeURIComponent(path)).catch(() => null),
      ]);
      if (nav.path !== path) return;
      nav.file = file;
      nav.outline = outline;
      MK.markSample(file);
      renderCode();
      renderOutline();
      loadTree();
    }
    scrollToLine(nav.line);
    const ch = (nav.file && nav.file.changes) || [];
    const at = ch.findIndex((c) => c.end >= nav.line && c.start <= nav.line);
    nav.changeIdx = at;
    paintChangeFloat();
    if (sym) selectSymbol(sym);
    else {
      const s = nav.outline && symbolAt(nav.line);
      if (s) selectSymbol(s.name, true);
    }
    renderCrumbs();
    if (MK.saveView) MK.saveView();
  }
  MK.openFile = openFile;

  function symbolAt(line) {
    let best = null;
    for (const s of (nav.outline && nav.outline.symbols) || []) {
      const end = s.end || 1e9;
      if (s.line <= line && line <= end && (!best || s.line >= best.line)) best = s;
    }
    return best;
  }

  function renderCrumbs() {
    const c = $("crumbs");
    c.replaceChildren();
    if (!nav.path) return;
    const parts = nav.path.split("/");
    parts.forEach((p, i) => {
      if (i) c.append(el("span", "dim", " › "));
      c.append(el(i === parts.length - 1 ? "b" : "span", "", p));
    });
    const s = symbolAt(nav.line);
    if (s) { c.append(el("span", "dim", " › "), el("span", "", s.name)); }
    $("nav-back").disabled = nav.histIdx <= 0;
    $("nav-fwd").disabled = nav.histIdx >= nav.hist.length - 1;
    $("as-diff").hidden = !(ui.files || []).some((f) => f.path === nav.path);
  }

  function renderCode() {
    const box = $("code");
    box.replaceChildren();
    const f = nav.file;
    $("code-empty").hidden = !!f;
    const note = $("code-note");
    note.hidden = true;
    if (!f) return;
    if (f.error || f.text === null || f.text === undefined) {
      note.hidden = false;
      note.textContent = f.error ? `could not open: ${f.note || f.error}` : (f.note || "not shown");
      $("change-float").hidden = true;
      return;
    }
    const lines = f.text.split("\n");
    if (lines.length && lines[lines.length - 1] === "") lines.pop();
    const marks = {};
    for (const c of f.changes || []) {
      for (let n = c.start; n <= Math.min(c.end, lines.length || 1); n++) if (!marks[n] || c.kind !== "del") marks[n] = c.kind;
    }
    const MAX = 20000;
    const frag = document.createDocumentFragment();
    lines.slice(0, MAX).forEach((t, i) => {
      const n = i + 1;
      const row = el("div", "cl" + (marks[n] ? " ch-" + marks[n] : ""));
      row.dataset.n = String(n);
      row.append(el("span", "gut"), el("span", "ln", n), el("span", "lt", t || " "));
      frag.append(row);
    });
    box.append(frag);
    if (lines.length > MAX) { note.hidden = false; note.textContent = `showing the first ${MAX} of ${lines.length} lines`; }
    nav.changeIdx = -1;
    paintChangeFloat();
  }

  function scrollToLine(n) {
    const row = $("code").querySelector(`div.cl[data-n="${Math.max(1, n | 0)}"]`);
    for (const r of $("code").querySelectorAll("div.cl.here")) r.classList.remove("here");
    if (row) { row.classList.add("here"); row.scrollIntoView({ block: "center" }); }
  }

  function paintChangeFloat() {
    const ch = (nav.file && nav.file.changes) || [];
    $("change-float").hidden = !ch.length;
    if (!ch.length) return;
    $("change-label").textContent = nav.changeIdx < 0
      ? `${ch.length} change(s) in this file (vs ${nav.file.changes_against || "HEAD"})`
      : `change ${nav.changeIdx + 1} of ${ch.length} in this file (vs ${nav.file.changes_against || "HEAD"})`;
  }

  function navChange(delta) {
    const ch = (nav.file && nav.file.changes) || [];
    if (!ch.length) return;
    nav.changeIdx = (nav.changeIdx + delta + ch.length) % ch.length;
    if (nav.changeIdx < 0) nav.changeIdx = 0;
    nav.line = ch[nav.changeIdx].start;
    scrollToLine(nav.line);
    paintChangeFloat();
    const s = symbolAt(nav.line);
    if (s && s.name !== nav.sym) selectSymbol(s.name, true);
    renderCrumbs();
  }
  MK.navChange = navChange;

  function navChangedFile(delta) {
    const files = MK.visibleFiles().map((o) => o.x).filter((f) => f.status !== "deleted");
    if (!files.length) return;
    let i = files.findIndex((f) => f.path === nav.path);
    i = i < 0 ? 0 : (i + delta + files.length) % files.length;
    openFile(files[i].path, 1, null).then(() => navChange(1));
  }
  MK.navChangedFile = navChangedFile;

  // ----------------------------------------------------------------------------- outline
  function renderOutline() {
    const ul = $("outline");
    ul.replaceChildren();
    const ol = nav.outline;
    const basis = $("outline-basis");
    basis.replaceChildren();
    const empty = $("outline-empty");
    if (!ol || ol.error) { empty.hidden = false; empty.textContent = ol && ol.note ? ol.note : "no outline"; return; }
    if (ol.basis) basis.append(MK.basisBadge(ol.basis));
    const changes = (nav.file && nav.file.changes) || [];
    const syms = ol.symbols || [];
    empty.hidden = syms.length > 0;
    empty.textContent = ol.note || "no definitions in this file";
    for (const s of syms) {
      const touched = changes.some((c) => s.line <= c.end && (s.end || s.line) >= c.start);
      const b = el("button", "ol-row" + (touched ? " changed" : ""));
      b.type = "button";
      b.style.paddingLeft = `${6 + (s.depth || 0) * 12}px`;
      b.append(el("span", "kind", s.kind === "class" ? "cls" : s.kind === "method" ? "m" : "fn"), el("span", "mono", s.name));
      if (touched) b.append(el("span", "small chg", "● changed"));
      b.onclick = () => openFile(nav.path, s.line, s.name);
      ul.append(MK.wrapLi(b));
    }
  }

  // ----------------------------------------------------------------------------- references
  function selectSymbol(sym, quiet) {
    if (!sym) return;
    if (nav.sym !== sym) nav.answers = {};
    nav.sym = sym;
    $("ref-sym").textContent = sym;
    void quiet;
    renderTabs();
    loadTab(nav.tab);
    for (const k of ["defs", "callers", "callees", "refs", "implementers", "blast_radius"]) if (k !== nav.tab) loadCount(k);
  }
  MK.selectSymbol = selectSymbol;

  function renderTabs() {
    const box = $("ref-tabs");
    box.replaceChildren();
    for (const [k, label] of KINDS) {
      const a = nav.answers[k];
      const b = el("button", "pill" + (nav.tab === k ? " active" : ""), a && a.references ? `${label} ${a.references.length}` : label);
      b.type = "button";
      b.setAttribute("role", "tab");
      b.onclick = () => { nav.tab = k; renderTabs(); loadTab(k); };
      box.append(b);
    }
  }

  async function fetchAnswer(kind) {
    if (nav.answers[kind]) return nav.answers[kind];
    const sym = nav.sym;
    const res = await api(`/api/query?kind=${kind}&target=${encodeURIComponent(sym)}&depth=2`).catch(() => null);
    if (sym !== nav.sym) return null;
    nav.answers[kind] = res || { error: "unreachable" };
    return nav.answers[kind];
  }
  async function loadCount(kind) { await fetchAnswer(kind); renderTabs(); }

  async function loadTab(kind) {
    const list = $("ref-list");
    const meta = $("ref-meta");
    list.replaceChildren();
    meta.textContent = "";
    $("ref-same").textContent = "";
    if (kind === "history") { renderHistory(); return; }
    if (!nav.sym) { meta.textContent = "Pick a symbol in the outline, double-click a name in the code, or ⌘P @name."; return; }
    meta.textContent = "asking the graph…";
    const res = await fetchAnswer(kind);
    if (!res || nav.tab !== kind) return;
    renderTabs();
    list.replaceChildren();
    if (res.error || res.sample) { meta.textContent = res.note || res.error; return; }
    meta.replaceChildren(el("span", "", `${res.references.length} via ${res.backend} `), MK.basisBadge(res.basis));
    if (res.note) {
      const d = el("details", "note-fold");
      d.append(el("summary", "dim small", "how this was answered"), el("div", "dim small", res.note));
      meta.append(d);
    }
    for (const r of res.references) {
      const structural = res.basis === "structural";
      const card = el("li", "ref-card" + (structural ? "" : " lexical"));
      const head = el("button", "ref-head mono");
      head.type = "button";
      head.append(el("span", "grow", `${r.path}:${r.line}`));
      if (r.symbol) head.append(el("span", "dim", r.symbol));
      head.append(MK.basisBadge(res.basis));
      head.onclick = () => openFile(r.path, r.line, kind === "callees" && r.symbol ? r.symbol : null);
      card.append(head);
      if (r.snippet) card.append(el("div", "ref-snip mono", r.snippet));
      else if (!structural) card.append(el("div", "ref-snip dim", "Matched by name only — the graph has no edge here."));
      list.append(card);
    }
    if (!res.references.length) list.append(el("li", "dim small", res.basis === "verified_empty" ? "None — verified by the graph (it holds the definition and no edge reaches it)." : "No answer."));
    const same = $("ref-same");
    same.replaceChildren(el("span", "", "Same answer as "), el("code", "", `mokata query ${kind} ${nav.sym} --json`), el("span", "", ", byte for byte."));
  }

  // ----------------------------------------------------------------------------- history
  function pushHistory(loc) {
    const cur = nav.hist[nav.histIdx];
    if (cur && cur.path === loc.path && cur.line === loc.line && cur.sym === loc.sym) return;
    nav.hist = nav.hist.slice(0, nav.histIdx + 1);
    nav.hist.push(loc);
    if (nav.hist.length > 200) nav.hist.shift();
    nav.histIdx = nav.hist.length - 1;
  }
  function navHistory(delta) {
    const i = nav.histIdx + delta;
    if (i < 0 || i >= nav.hist.length) return;
    nav.histIdx = i;
    const loc = nav.hist[i];
    openFile(loc.path, loc.line, loc.sym, { fromHistory: true });
  }
  MK.navHistory = navHistory;
  function renderHistory() {
    const list = $("ref-list");
    $("ref-meta").textContent = "where you have been (Alt+← / Alt+→)";
    nav.hist.slice().reverse().forEach((loc, k) => {
      const i = nav.hist.length - 1 - k;
      const b = el("button", "ref-head mono" + (i === nav.histIdx ? " here" : ""), `${loc.path}:${loc.line}${loc.sym ? " · " + loc.sym : ""}`);
      b.type = "button";
      b.onclick = () => { nav.histIdx = i; openFile(loc.path, loc.line, loc.sym, { fromHistory: true }); };
      list.append(MK.wrapLi(b));
    });
  }

  // ----------------------------------------------------------------------------- wiring
  MK.wireNav = function wireNav() {
    $("only-changed").onchange = renderTree;
    let t = 0;
    $("tree-filter").oninput = () => { clearTimeout(t); t = setTimeout(renderTree, 80); };
    $("nav-back").onclick = () => navHistory(-1);
    $("nav-fwd").onclick = () => navHistory(1);
    $("change-prev").onclick = () => navChange(-1);
    $("change-next").onclick = () => navChange(1);
    $("as-diff").onclick = () => {
      const i = (ui.files || []).findIndex((f) => f.path === nav.path);
      MK.setMode("review"); MK.setPanel("changes", true); if (i >= 0) MK.showFile(i);
    };
    // Double-click a name in the code to ask the graph about it.
    $("code").ondblclick = () => {
      const s = String(window.getSelection ? window.getSelection() : "").trim();
      if (/^[A-Za-z_$][\w$]*$/.test(s)) { pushHistory({ path: nav.path, line: nav.line, sym: s }); selectSymbol(s); }
    };
    $("code").onclick = (ev) => {
      const row = ev.target && ev.target.closest ? ev.target.closest("div.cl") : null;
      if (row) { nav.line = +row.dataset.n; renderCrumbs(); }
    };
    renderTabs();
  };
  MK.onModeNav = (mode) => { if (mode === "navigate") { loadTree(); if (!nav.sym) loadTab(nav.tab); } };
  MK.onRefreshNav = () => {
    if (ui.mode === "navigate") { if (nav.path) openFile(nav.path, nav.line, null, { fromHistory: true, reload: true }); renderTree(); }
  };
})();
