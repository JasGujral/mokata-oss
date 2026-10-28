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

  // 0.0.26 P35 (G13/R19): the explorer is LAZY. `/api/tree?dir=` answers one directory's
  // children (sub-directories with their file counts, then files), `/api/tree?filter=` searches
  // EVERY path server-side; both page with `next`. Nothing is truncated: a long list ends in a
  // "more" row that asks for the next page. Only the changed files (small) are built here.
  nav.dirs = {};            // dir -> {entries, next, total}
  nav.flt = null;           // {q, entries, next, total}

  function treeUrl(params) {
    const qs = Object.entries(params).filter(([, v]) => v !== "" && v !== undefined && v !== null)
      .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
    return "/api/tree" + (qs ? "?" + qs : "");
  }

  async function loadDir(dir, more) {
    const have = nav.dirs[dir];
    if (have && !more) return have;
    const t = await api(treeUrl({ dir, offset: more && have ? have.next : 0 })).catch((e) => ({ error: "fetch", note: String(e) }));
    if (!t || t.error) { nav.treeError = t || { error: "no answer" }; return null; }
    nav.treeError = null;
    MK.markSample(t);
    nav.tree = t;
    $("tree-count").textContent = String(t.count);
    const prev = more && have ? have.entries : [];
    nav.dirs[dir] = { entries: prev.concat(t.entries || []), next: t.next, total: t.total };
    return nav.dirs[dir];
  }

  async function loadTree(force) {
    if (force) nav.dirs = {};
    if (!nav.dirs[""]) await loadDir("");
    if (!nav.dirs[""] && nav.treeError) {        // P39 (R33): a failed tree says so, with Retry
      MK.regionSay($("tree"), "error", MK.whyFailed(nav.treeError, "list the files"),
                   { retry: () => loadTree(true), cli: "git ls-files" });
      return;
    }
    $("tree-note").textContent = nav.tree && nav.tree.pruned && nav.tree.pruned !== "SAMPLE" ? "venv/, .venv/, conda envs and vendor dirs are never listed — they are not your code." : "";
    renderTree();
  }
  MK.loadTree = loadTree;

  async function loadFilter(q, more) {
    const prev = more && nav.flt && nav.flt.q === q ? nav.flt : null;
    const t = await api(treeUrl({ filter: q, offset: prev ? prev.next : 0 })).catch(() => null);
    if (!t || t.error || $("tree-filter").value.trim() !== q) return;
    nav.flt = { q, entries: (prev ? prev.entries : []).concat(t.entries || []), next: t.next, total: t.total };
    renderTree();
  }

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

  function fileRow(p, depth, changed, label) {
    const f = changed[p];
    const b = el("button", "tree-row file" + (p === nav.path ? " active" : ""));
    b.type = "button";
    b.style.paddingLeft = `${6 + depth * 14 + 14}px`;
    b.append(el("span", "", label || p.slice(p.lastIndexOf("/") + 1)));
    if (f) { const L = MK.ST_LETTER[f.status] || "M"; b.append(el("span", "st st-" + L, L)); }
    b.onclick = () => openFile(p, 1, null);
    return MK.wrapLi(b);
  }

  function moreRow(depth, shown, total, onMore) {
    const b = el("button", "tree-row more dim small", `… ${total - shown} more (${shown} of ${total} shown)`);
    b.type = "button";
    b.style.paddingLeft = `${6 + depth * 14 + 14}px`;
    b.onclick = onMore;
    return MK.wrapLi(b);
  }

  function renderTree() {
    const ul = $("tree");
    ul.replaceChildren();
    if (!nav.tree) return;
    const changed = changedSet();
    const only = $("only-changed").checked;
    const flt = $("tree-filter").value.trim();
    const counts = {};
    for (const p of Object.keys(changed)) {
      const parts = p.split("/");
      for (let i = 1; i < parts.length; i++) { const d = parts.slice(0, i).join("/"); counts[d] = (counts[d] || 0) + 1; }
    }
    const dirRow = (full, name, depth, isOpen, extra, onclick) => {
      const b = el("button", "tree-row dir");
      b.type = "button";
      b.style.paddingLeft = `${6 + depth * 14}px`;
      b.append(el("span", "caret", isOpen ? "▾" : "▸"), el("span", "", name));
      if (extra) b.append(el("span", "dim small", extra));
      if (counts[full]) { b.append(el("span", "chg-dot", "●")); b.append(el("span", "dim small", String(counts[full]))); }
      b.onclick = onclick;
      ul.append(MK.wrapLi(b));
    };
    if (flt) {
      // a server-side filter over every path (R19: TypeScript `checker.ts` → src/compiler/checker.ts)
      if (!nav.flt || nav.flt.q !== flt) {
        ul.append(el("li", "dim small", "searching every path…"));
        loadFilter(flt);
        return;
      }
      for (const e of nav.flt.entries) if (!only || changed[e.path]) ul.append(fileRow(e.path, 0, changed, e.path));
      if (nav.flt.next !== null && nav.flt.next !== undefined) ul.append(moreRow(0, nav.flt.entries.length, nav.flt.total, () => loadFilter(flt, true)));
      if (!nav.flt.total) ul.append(el("li", "dim small", "No path matches."));
      return;
    }
    if (only) {
      const tree = buildTree(Object.keys(changed));
      const walk = (node, prefix, depth) => {
        for (const name of Object.keys(node.dirs).sort()) {
          const full = prefix ? `${prefix}/${name}` : name;
          dirRow(full, name, depth, true, "", () => {});
          walk(node.dirs[name], full, depth + 1);
        }
        for (const p of node.files.sort()) ul.append(fileRow(p, depth, changed));
      };
      walk(tree, "", 0);
      if (!Object.keys(changed).length) ul.append(el("li", "dim small", "No changed file in the tree."));
      return;
    }
    const walk = (dir, depth) => {
      const d = nav.dirs[dir];
      if (!d) {
        ul.append(el("li", "dim small", "loading…"));
        loadDir(dir).then(renderTree);
        return;
      }
      for (const e of d.entries) {
        if (e.kind === "dir") {
          const isOpen = nav.collapsed[e.path] === false;
          dirRow(e.path, e.name, depth, isOpen, String(e.files), () => { nav.collapsed[e.path] = isOpen; renderTree(); });
          if (isOpen) walk(e.path, depth + 1);
        } else ul.append(fileRow(e.path, depth, changed));
      }
      if (d.next !== null && d.next !== undefined) ul.append(moreRow(depth, d.entries.length, d.total, () => loadDir(dir, true).then(renderTree)));
    };
    walk("", 0);
    if (nav.dirs[""] && !nav.dirs[""].entries.length) ul.append(el("li", "dim small", "No file."));
  }

  // ----------------------------------------------------------------------------- one file
  async function openFile(path, line, sym, opts) {
    const o = opts || {};
    if (!o.keepSymbol) MK.setMode("navigate");   // a live reload is already in Navigate (P35)
    if (!o.fromHistory) pushHistory({ path, line: line || 1, sym: sym || null });
    nav.line = line || 1;
    if (nav.path !== path || o.reload) {
      nav.path = path;
      // P35 (G16): the file and its outline in one request
      const file = await api("/api/file?outline=1&path=" + encodeURIComponent(path)).catch((e) => ({ error: "fetch", note: String(e) }));
      if (nav.path !== path) return;
      nav.file = file;
      nav.outline = file && file.error ? file : file ? file.outline || null : null;
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
    if (o.keepSymbol) { /* a live reload: the inspector keeps its answer (P35 G16) */ }
    else if (sym) selectSymbol(sym);
    else {
      const s = nav.outline && symbolAt(nav.line);
      if (s) selectSymbol(s.name, true);
    }
    renderCrumbs();
    if (o.keepSymbol) return;                  // a live reload (P35 G16): no tab or view to save
    if (MK.onFileOpened) MK.onFileOpened(path, nav.line, o);   // tabs.js: the file's tab
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

  // ----------------------------------------------------------------- the code view (virtualised)
  // 0.0.26 P1: only the rows in view (± OVERSCAN) are in the DOM, so a 200k-line file costs what
  // a screenful does. The text is the file's own (`/api/file`) or, for a file past the whole-file
  // cap (`windowed`), windows from `/api/highlight`. The colour is MK.hl's
  // (static/features/highlight.js): token spans per line, fetched per 1,000-line chunk; until a
  // chunk arrives (or without MK.hl) a row is plain text.
  const OVERSCAN = 80;
  const cv = (nav.cv = { lines: null, total: 0, marks: {}, rowH: 0, rf: 0, rl: 0, inner: null });

  function rowHeight(box) {
    const probe = el("div", "cl");
    probe.append(el("span", "gut"), el("span", "ln", "1"), el("span", "lt", "x"));
    box.append(probe);
    const h = probe.getBoundingClientRect().height || 22;
    probe.remove();
    return h;
  }

  function renderCode() {
    const box = $("code");
    box.replaceChildren();
    box.style.height = "";
    cv.inner = null;
    const f = nav.file;
    $("code-empty").hidden = !!f;
    const note = $("code-note");
    note.hidden = true;
    if (!f) return;
    if (MK.hl) MK.hl.open(nav.path, f);
    if (!f.windowed && (f.error || f.text === null || f.text === undefined)) {
      note.hidden = false;
      note.textContent = f.error ? `could not open: ${f.note || f.error}` : (f.note || "not shown");
      $("change-float").hidden = true;
      return;
    }
    if (f.windowed) {
      cv.lines = null;
      cv.total = f.lines || 0;
      note.hidden = false;
      note.textContent = f.note || "large file — read in windows";
    } else {
      const lines = f.text.split("\n");
      if (lines.length && lines[lines.length - 1] === "") lines.pop();
      cv.lines = lines;
      cv.total = lines.length;
    }
    const marks = {};
    for (const c of f.changes || []) {
      for (let n = c.start; n <= Math.min(c.end, cv.total || 1); n++) if (!marks[n] || c.kind !== "del") marks[n] = c.kind;
    }
    cv.marks = marks;
    cv.rowH = rowHeight(box);
    box.style.position = "relative";
    box.style.height = `${Math.max(1, cv.total) * cv.rowH}px`;
    cv.inner = el("div", "cv-rows");
    box.append(cv.inner);
    cv.rf = cv.rl = 0;
    paintRows(true);
    nav.changeIdx = -1;
    paintChangeFloat();
  }

  // Where the code box starts inside the scroller's content (offsetTop depends on positioning).
  function codeTop(sc) {
    return $("code").getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop;
  }

  function rowText(n) {
    if (cv.lines) return cv.lines[n - 1];
    const spans = MK.hl ? MK.hl.row(nav.path, n) : null;
    return spans ? spans.map((s) => s[1]).join("") : null;
  }

  // Re-render only when the view leaves the rendered band (a drag-selection inside it survives).
  function paintRows(force) {
    if (!cv.inner || !cv.total) return;
    const sc = $("code-scroll");
    const y = sc.scrollTop - codeTop(sc);
    const vf = Math.max(1, Math.floor(y / cv.rowH) + 1);
    const vl = Math.min(cv.total, Math.ceil((y + sc.clientHeight) / cv.rowH) + 1);
    if (!force && cv.rf && vf >= cv.rf && vl <= cv.rl) return;
    const first = Math.max(1, vf - OVERSCAN);
    const last = Math.min(cv.total, vl + OVERSCAN);
    cv.rf = first; cv.rl = last;
    const frag = document.createDocumentFragment();
    for (let n = first; n <= last; n++) {
      const row = el("div", "cl" + (cv.marks[n] ? " ch-" + cv.marks[n] : "") + (n === nav.line && nav.here ? " here" : ""));
      row.dataset.n = String(n);
      const lt = el("span", "lt");
      const spans = MK.hl ? MK.hl.row(nav.path, n) : null;
      if (spans) MK.hl.paint(lt, spans);
      else {
        const t = rowText(n);
        lt.textContent = t === null || t === undefined ? "…" : (t || " ");
        if (t === null || t === undefined) lt.classList.add("dim");
      }
      row.append(el("span", "gut"), el("span", "ln", n), lt);
      frag.append(row);
    }
    cv.inner.style.transform = `translateY(${(first - 1) * cv.rowH}px)`;
    cv.inner.replaceChildren(frag);
    if (MK.hl) {
      const path = nav.path;
      MK.hl.want(path, first, last, () => { if (nav.path === path) paintRows(true); });
    }
  }
  MK.paintCodeRows = paintRows;

  function scrollToLine(n) {
    n = Math.max(1, n | 0);
    nav.here = true;
    const sc = $("code-scroll");
    if (cv.inner && cv.rowH) {
      sc.scrollTop = codeTop(sc) + (n - 1) * cv.rowH - sc.clientHeight / 2 + cv.rowH / 2;
      paintRows(true);
    }
    for (const r of $("code").querySelectorAll("div.cl.here")) r.classList.remove("here");
    const row = $("code").querySelector(`div.cl[data-n="${n}"]`);
    if (row) row.classList.add("here");
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
    if (ol && ol.error) {       // P39 (R33, §5.4 rule 4): what failed, Retry, the CLI twin
      empty.hidden = true;
      const p = nav.path;
      MK.regionSay(ul, "error", MK.whyFailed(ol, "read the outline of " + p),
                   { retry: () => openFile(p, nav.line, null, { reload: true }), cli: `mokata query defs <name> (${p})` });
      return;
    }
    if (!ol) { empty.hidden = false; empty.textContent = "no outline"; return; }
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
    // 0.0.26 P35 (G12/R22): only the VISIBLE tab asks the graph — a selection used to fire all six
    // questions (defs, callers, callees, refs, implementers, blast radius) at the two-worker lane,
    // on every file open and outline click. A tab's count appears once it has been opened.
    loadTab(nav.tab);
  }
  MK.selectSymbol = selectSymbol;

  // ----------------------------------------------------------------- go to definition (F12, ⌘-click)
  // 0.0.26 P35 (G12/R21): one `defs` question — from the code graph's node table in every language
  // it indexes — then jump. One definition: the viewer opens it at its line. Several: the Defs tab
  // lists them (the one in this file first) to pick from. None: said where the answer goes.
  function wordAt(node, offset) {
    const text = node && node.nodeType === 3 ? node.textContent : "";
    if (!text) return "";
    let a = offset, b = offset;
    while (a > 0 && /[\w$]/.test(text[a - 1])) a--;
    while (b < text.length && /[\w$]/.test(text[b])) b++;
    const w = text.slice(a, b);
    return /^[A-Za-z_$][\w$]*$/.test(w) ? w : "";
  }

  function wordAtPoint(x, y) {
    if (document.caretPositionFromPoint) {
      const pos = document.caretPositionFromPoint(x, y);
      return pos ? wordAt(pos.offsetNode, pos.offset) : "";
    }
    if (document.caretRangeFromPoint) {
      const r = document.caretRangeFromPoint(x, y);
      return r ? wordAt(r.startContainer, r.startOffset) : "";
    }
    return "";
  }

  function wordAtSelection() {
    const sel = window.getSelection ? window.getSelection() : null;
    if (!sel) return "";
    const s = String(sel).trim();
    if (/^[A-Za-z_$][\w$]*$/.test(s)) return s;
    return sel.anchorNode ? wordAt(sel.anchorNode, sel.anchorOffset) : "";
  }

  async function goToDefinition(word) {
    const sym = word || wordAtSelection() || nav.sym;
    if (!sym) { $("ref-meta").textContent = "Put the caret on a name (or ⌘-click it) to go to its definition."; return; }
    pushHistory({ path: nav.path, line: nav.line, sym: nav.sym });
    if (nav.sym !== sym) nav.answers = {};
    nav.sym = sym;
    $("ref-sym").textContent = sym;
    nav.tab = "defs";
    renderTabs();
    $("ref-meta").textContent = `finding the definition of ${sym}…`;
    const res = await fetchAnswer("defs");
    if (!res || nav.sym !== sym) return;
    const refs = (res.references || []).slice().sort((a, b) => (b.path === nav.path) - (a.path === nav.path));
    if (refs.length === 1) {
      await openFile(refs[0].path, refs[0].line, sym);
      return;
    }
    loadTab("defs");
    if (!refs.length) $("ref-meta").textContent = `No definition of ${sym} found (${res.note || res.backend || "no answer"}).`;
  }
  MK.goToDefinition = goToDefinition;

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
    if (res.definitions > 1) {
      // P35 (G28/R37): an ambiguous name is answered over all its definitions, labelled
      meta.append(el("span", "chip small", ` ${res.definitions} definitions named ${res.target} — merged`));
    }
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
    $("tree-filter").oninput = () => { clearTimeout(t); t = setTimeout(renderTree, 150); };
    $("nav-back").onclick = () => navHistory(-1);
    $("nav-fwd").onclick = () => navHistory(1);
    $("change-prev").onclick = () => navChange(-1);
    $("change-next").onclick = () => navChange(1);
    $("as-diff").onclick = () => {
      const i = (ui.files || []).findIndex((f) => f.path === nav.path);
      MK.setMode("review"); if (i >= 0) MK.showFile(i);
    };
    // Double-click a name in the code to ask the graph about it.
    $("code").ondblclick = () => {
      const s = String(window.getSelection ? window.getSelection() : "").trim();
      if (/^[A-Za-z_$][\w$]*$/.test(s)) { pushHistory({ path: nav.path, line: nav.line, sym: s }); selectSymbol(s); }
    };
    let raf = 0;
    $("code-scroll").addEventListener("scroll", () => {
      if (!raf) raf = requestAnimationFrame(() => { raf = 0; paintRows(false); });
    }, { passive: true });
    $("code").onclick = (ev) => {
      const row = ev.target && ev.target.closest ? ev.target.closest("div.cl") : null;
      if (row) { nav.line = +row.dataset.n; renderCrumbs(); }
      if (ev.metaKey || ev.ctrlKey) {            // ⌘/Ctrl-click: go to definition (P35 R21)
        const w = wordAtPoint(ev.clientX, ev.clientY);
        if (w) { ev.preventDefault(); goToDefinition(w); }
      }
    };
    renderTabs();
  };
  MK.onModeNav = (mode) => { if (mode === "navigate") { loadTree(); if (!nav.sym) loadTab(nav.tab); } };
  MK.onRefreshNav = () => {
    if (ui.mode === "navigate") { if (nav.path) openFile(nav.path, nav.line, null, { fromHistory: true, reload: true }); loadTree(true); }
  };
  // P35 (G16): ONE live edit event → only what it touched. The open file (and its outline) is
  // re-read when it is one of the edited paths; nothing else is asked. Explorer listings are
  // re-read when next shown (a new file appears on the next refresh of its directory).
  MK.onDeltaNav = (paths) => {
    for (const p of paths) {
      const d = p.lastIndexOf("/") >= 0 ? p.slice(0, p.lastIndexOf("/")) : "";
      const have = nav.dirs[d];
      if (have && !have.entries.some((e) => e.path === p)) delete nav.dirs[d];
    }
    if (ui.mode === "navigate" && nav.path && paths.includes(nav.path)) {
      openFile(nav.path, nav.line, null, { fromHistory: true, reload: true, keepSymbol: true });
    }
  };
})();
