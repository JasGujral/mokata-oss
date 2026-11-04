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

  // The Code inspector (UX-SIMPLIFY §5 #38): THREE tabs, one per question — References (who
  // names it: callers, definitions, references, implementers), Impact (what it reaches: blast
  // radius, callees, Expand graph) and History (where you have been). Inside a tab, one kind is
  // asked at a time (P35 G12/R22: only what is on screen asks the graph).
  const GROUPS = [
    ["refs-group", "References", [["callers", "Callers"], ["defs", "Definitions"], ["refs", "References"], ["implementers", "Implementers"]]],
    ["impact-group", "Impact", [["blast_radius", "Blast radius"], ["callees", "Callees"]]],
    ["history", "History", []],
  ];
  const groupOf = (kind) => (GROUPS.find((g) => g[0] === kind || g[2].some((k) => k[0] === kind)) || GROUPS[0])[0];

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
  nav.inflight = {};        // dir -> the listing request already on its way (one per directory)
  nav.expandRuns = 0;       // P11: Expand-all runs finished (a page can tell "this press is done" from the last one)
  nav.expandJob = null;     // P11: the "Expand all" run in progress {total, done, cancelled}
  nav.compact = true;       // P11: a single-child chain (src/mokata/ide) is ONE row
  try { if (localStorage.getItem("mokata-ide:compact") === "0") nav.compact = false; } catch (_e) { /* private mode */ }
  nav.flt = null;           // {q, entries, next, total}
  nav.dirErr = {};          // dir -> the listing that failed or was cancelled (a render never retries it by itself)
  nav.stale = new Set();    // dirs whose listing predates the last refresh: shown, re-listed when painted
  nav.curKey = null;        // the row that owns the tree's one tab stop (roving tabindex)
  nav.flat = null;          // the rows of the open tree, painted a window at a time
  const ROW_H = 27;         // px; .tree li > .tree-row is exactly this tall, so the window is arithmetic
  const TREE_OVERSCAN = 24;     // rows painted above and below the viewport

  function treeUrl(params) {
    const qs = Object.entries(params).filter(([, v]) => v !== "" && v !== undefined && v !== null)
      .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
    return "/api/tree" + (qs ? "?" + qs : "");
  }

  async function loadDir(dir, more, fresh) {
    const have = nav.dirs[dir];
    if (have && !more && !fresh) return have;
    if (!more && nav.inflight[dir]) return nav.inflight[dir];     // P11: one request per directory
    const run = (async () => {
      const t = await api(treeUrl({ dir, offset: more && have ? have.next : 0 })).catch((e) => ({ error: "fetch", note: String(e) }));
      if (!t || t.error) {
        nav.treeError = t || { error: "no answer" };
        if (dir) nav.dirErr[dir] = nav.treeError;
        if (fresh) delete nav.dirs[dir];                // a failed refresh shows its retry row, never stale rows
        nav.stale.delete(dir);
        return null;
      }
      nav.treeError = null;
      delete nav.dirErr[dir];
      nav.stale.delete(dir);
      MK.markSample(t);
      nav.tree = t;
      $("tree-count").textContent = String(t.count);
      const prev = more && have ? have.entries : [];
      nav.dirs[dir] = { entries: prev.concat(t.entries || []), next: t.next, total: t.total };
      return nav.dirs[dir];
    })();
    if (!more) {
      nav.inflight[dir] = run;
      run.then(() => { delete nav.inflight[dir]; }, () => { delete nav.inflight[dir]; });
    }
    return run;
  }

  async function loadTree(force) {
    if (force) {
      // keep what is listed on screen and re-list it lazily: only the folders that are painted (or
      // that the user opens) are asked again, through the bounded pool — never every open folder at once
      nav.stale = new Set(Object.keys(nav.dirs).filter((k) => k !== ""));
      nav.dirErr = {};
      pool.total -= pool.queue.length;           // what was queued before this refresh is not this job's
      pool.queue = []; pool.queued.clear();
      await loadDir("", false, true);
    } else if (!nav.dirs[""]) await loadDir("");
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
    b.title = p;
    if (f) { const L = MK.ST_LETTER[f.status] || "M"; b.append(el("span", "st st-" + L, L)); }
    b.onclick = () => openFile(p, 1, null);
    b.oncontextmenu = (ev) => openTreeMenu(ev, p);
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
    const t0 = performance.now();
    const anchor = ul.classList.contains("windowed") ? anchorBefore(nav.flat) : null;   // before anything moves
    nav.flat = null;
    if (!nav.tree) { ul.replaceChildren(); return; }
    const changed = changedSet();
    const only = $("only-changed").checked;
    const flt = $("tree-filter").value.trim();
    const counts = {};
    for (const p of Object.keys(changed)) {
      const parts = p.split("/");
      for (let i = 1; i < parts.length; i++) { const d = parts.slice(0, i).join("/"); counts[d] = (counts[d] || 0) + 1; }
    }
    const dirLi = (full, name, depth, isOpen, extra, onclick) => {
      const b = el("button", "tree-row dir");
      b.type = "button";
      b.style.paddingLeft = `${6 + depth * 14}px`;
      b.append(el("span", "caret", isOpen ? "▾" : "▸"), el("span", "", name));
      if (extra) b.append(el("span", "dim small", extra));
      if (counts[full]) { b.append(el("span", "chg-dot", "●")); b.append(el("span", "dim small", String(counts[full]))); }
      b.onclick = onclick;
      b.setAttribute("aria-expanded", String(!!isOpen));
      b.title = full;
      b.oncontextmenu = (ev) => openTreeMenu(ev, full);
      return MK.wrapLi(b);
    };
    const dirRow = (...a) => ul.append(dirLi(...a));
    ul.classList.toggle("windowed", !flt && !only);
    if (flt || only) { ul.replaceChildren(); painted = new Map(); }   // these views are built whole (small, or paged)
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
    // The open tree is a flat list of rows; only the ones in (or near) the viewport become DOM, so a
    // 70,000-row tree costs what the screen shows, however many folders the user has opened.
    // Each row knows the listing it came from (`parent`). A row from a listing that predates the
    // last refresh is marked stale and NEEDS that listing: whichever of its rows is painted asks
    // for it, so a folder whose files are on screen is re-listed even when its own row is far above.
    const flat = [];
    const NONE = [];
    const add = (id, sig, make, needs, focusable) => {
      const need = needs[0] || needs[1] ? needs.filter(Boolean) : NONE;      // most rows need nothing: share one array
      flat.push({ id, sig, make, need, focusable: focusable !== false });
    };
    const staleOf = (r) => r.parent !== undefined && nav.stale.has(r.parent);
    const mark = (make, stale) => !stale ? make : () => {
      const li = make();
      if (stale) { li.setAttribute("aria-busy", "true"); if (li.firstElementChild) li.firstElementChild.classList.add("stale"); }
      return li;
    };
    walkRows((r) => {
      const st = staleOf(r), up = st ? r.parent : "";
      if (r.t === "loading") add(`l|${r.dir}`, `l|${r.dir}`, () => el("li", "dim small", "loading…"), [r.dir], false);
      else if (r.t === "failed") add(`x|${r.dir}`, `x|${r.dir}|${r.depth}|${(nav.dirErr[r.dir] || {}).error}`, () => failedRow(r.dir, r.depth), [up]);
      else if (r.t === "dir") add(`d|${r.key}`, `d|${r.key}|${r.name}|${r.depth}|${r.isOpen}|${r.files}|${counts[r.key] || 0}|${st}`,
        mark(() => dirLi(r.key, r.name, r.depth, r.isOpen, r.files, () => toggleDir(r.key, r.isOpen)), st), [up, r.stale ? r.key : ""]);
      else if (r.t === "file") add(`f|${r.path}`, `f|${r.path}|${r.depth}|${r.path === nav.path}|${changed[r.path] ? changed[r.path].status : ""}|${st}`,
        mark(() => fileRow(r.path, r.depth, changed), st), [up]);
      else add(`m|${r.dir}`, `m|${r.dir}|${r.shown}|${r.total}|${r.depth}|${st}`, mark(() => moreRow(r.depth, r.shown, r.total, () => loadDir(r.dir, true).then(renderTree)), st), [up]);
    });
    if (nav.dirs[""] && !nav.dirs[""].entries.length) add("e", "empty", () => el("li", "dim small", "No file."), [], false);
    nav.flat = flat;
    paintWindow(true, anchorAfter(anchor));
    lastRenderMs = performance.now() - t0;
  }

  // A folder the user opens is asked for BY THE CLICK, at once: not through the pool, not when its
  // row happens to be painted, and never behind an Expand-all run (M3). One request per click.
  function toggleDir(key, wasOpen) {
    nav.collapsed[key] = wasOpen;
    nav.curKey = `d|${key}`;                    // the row the user acted on is the one the view keeps still
    if (!wasOpen && (!nav.dirs[key] || nav.stale.has(key))) {
      delete nav.dirErr[key];
      loadDir(key, false, nav.stale.has(key)).then(() => renderTree());
    }
    renderTree();
  }

  // Scroll anchoring. A render that adds or removes rows ABOVE what the user is looking at (an
  // Expand-all run, a refresh, a folder closing) keeps the user's row where it is on screen: the
  // row they last acted on when it is in view, else the first row in view.
  function anchorBefore(flat) {
    const box = $("lt-files");
    if (!flat || !flat.length || !box) return null;
    const y0 = box.scrollTop - treeTop(), view = box.clientHeight;
    const inView = (i) => i >= 0 && i * ROW_H >= y0 - 1 && (i + 1) * ROW_H <= y0 + view + 1;
    let i = nav.curKey ? flat.findIndex((r) => r.id === nav.curKey) : -1;
    if (!inView(i)) i = Math.max(0, Math.ceil(y0 / ROW_H));
    if (i >= flat.length) return null;
    return { id: flat[i].id, off: i * ROW_H - y0 };
  }

  function anchorAfter(a) {
    if (!a || !nav.flat) return undefined;
    const j = nav.flat.findIndex((r) => r.id === a.id);
    return j < 0 ? undefined : Math.max(0, treeTop() + j * ROW_H - a.off);
  }

  let lastRenderMs = 0;

  // The visible rows of the open tree, in order: every open folder's listing, each row once. Pure: it
  // asks for nothing. A row that needs a listing carries `need`; paintWindow asks for the ones in view.
  function walkRows(emit) {
    const walk = (dir, depth) => {
      const d = nav.dirs[dir];
      if (!d) {
        if (nav.dirErr[dir]) { emit({ t: "failed", dir, depth }); return; }
        emit({ t: "loading", dir, depth });
        return;
      }
      for (const e of d.entries) {
        if (e.kind === "dir") {
          const key = dirKey(e);
          const isOpen = nav.collapsed[key] === false;
          emit({ t: "dir", key, parent: dir, name: nav.compact && e.compact_name ? e.compact_name : e.name, depth, isOpen, files: String(e.files),
                 stale: isOpen && !!nav.dirs[key] && nav.stale.has(key) });
          if (isOpen) walk(key, depth + 1);
        } else emit({ t: "file", path: e.path, parent: dir, depth });
      }
      if (d.next !== null && d.next !== undefined) emit({ t: "more", dir, parent: dir, depth, shown: d.entries.length, total: d.total });
    };
    walk("", 0);
  }

  function failedRow(dir, depth) {
    const cancelled = (nav.dirErr[dir] || {}).error === "cancelled";
    const b = el("button", "tree-row more dim small", cancelled ? "not refreshed (cancelled) — retry" : "could not list this folder — retry");
    b.type = "button";
    b.style.paddingLeft = `${6 + depth * 14 + 14}px`;
    b.onclick = () => { delete nav.dirErr[dir]; loadDir(dir).then(() => renderTree()); renderTree(); };   // by hand: at once
    return MK.wrapLi(b);
  }

  // Rows already painted, by what they show. A repaint reuses a row whose signature is unchanged
  // (same element, never rebuilt), so a progress repaint while a run opens thousands of folders does
  // not replace the row under the user's pointer — a press that starts on a row ends on it.
  let painted = new Map();
  const pads = [null, null];

  function treeTop() {
    const ul = $("tree"), box = $("lt-files");
    return box ? ul.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop : 0;
  }

  function paintWindow(force, stWant) {
    const ul = $("tree"), flat = nav.flat;
    if (!flat) return;
    const box = $("lt-files");
    const view = (box && box.clientHeight) || 800;
    const top = treeTop();
    // The tree may have shrunk below the scroll position (a refresh dropped a subtree, a cancelled
    // refresh folded folders into retry rows): the window is clamped to the END of the tree as it is
    // now, so the panel shows its last rows, never padding sized for rows that no longer exist (R3-1).
    // What sits below the tree in the panel (the note, the Outline) stays reachable.
    const below = box ? Math.max(0, box.scrollHeight - (top + ul.offsetHeight)) : 0;
    const maxSt = Math.max(0, top + flat.length * ROW_H + below - view);
    let st = stWant !== undefined ? stWant : box ? box.scrollTop : 0;
    if (st > maxSt) { st = maxSt; if (stWant === undefined) stWant = st; }
    const first = Math.max(0, Math.floor((st - top) / ROW_H) - TREE_OVERSCAN);
    const last = Math.min(flat.length, Math.ceil((st - top + view) / ROW_H) + TREE_OVERSCAN);
    const pad = (n, rows) => {
      const li = pads[n] || (pads[n] = el("li"));
      li.setAttribute("aria-hidden", "true");
      li.style.height = `${rows * ROW_H}px`;
      return li;
    };
    const kids = [], next = new Map(), rows = [];
    if (first > 0) kids.push(pad(0, first));
    for (let i = first; i < last; i++) {
      const e = flat[i];
      let li = painted.get(e.sig);
      if (!li) {
        li = e.make();
        const b = li.firstElementChild;
        if (b && b.tagName === "BUTTON") b.dataset.rid = e.id;
      }
      next.set(e.sig, li);
      kids.push(li);
      rows.push([i, li, e]);
      for (const d of e.need) enqueue(d);          // a painted row that needs a listing asks for it, through the pool
    }
    if (last < flat.length) kids.push(pad(1, flat.length - last));
    painted = next;

    // Reconcile in place: a row that stays is never detached, so the focused row keeps focus while
    // the window shifts. Only rows that leave are removed and only new ones are inserted.
    const active = document.activeElement;
    const hadFocus = !!active && ul.contains(active);
    const keep = new Set(kids);
    for (const c of Array.from(ul.children)) if (!keep.has(c)) c.remove();
    let ref = ul.firstChild;
    for (const k of kids) { if (k === ref) ref = ref.nextSibling; else ul.insertBefore(k, ref); }

    // One tab stop (roving tabindex): the current row if it is painted, else the first focusable one.
    let stop = rows.find(([, , e]) => e.focusable && e.id === nav.curKey) || rows.find(([, , e]) => e.focusable);
    for (const r of rows) {
      const b = r[1].firstElementChild;
      if (b && b.tagName === "BUTTON") b.tabIndex = stop && r === stop ? 0 : -1;
      r[1].setAttribute("aria-setsize", String(flat.length));
      r[1].setAttribute("aria-posinset", String(r[0] + 1));
    }
    if (stWant !== undefined && box && Math.abs(box.scrollTop - stWant) > 0.5) box.scrollTop = stWant;   // the anchor holds
    // The focused row was rebuilt (Enter toggled or opened it, its refresh landed): focus moves to
    // the new row of the same id, so the next key acts again. It scrolled out of the window: focus
    // stays IN the tree, on the list itself (arrows pick up from nav.curKey).
    if (hadFocus && !document.contains(active)) {
      const rid = active.dataset ? active.dataset.rid : "";
      const same = rid ? rows.find(([, , e]) => e.id === rid) : null;
      const b = same && same[1].firstElementChild;
      if (b && b.tagName === "BUTTON") { b.tabIndex = 0; b.focus({ preventScroll: true }); }
      else ul.focus({ preventScroll: true });
    }
  }

  function focusRow(id) {
    const b = Array.from($("tree").querySelectorAll("[data-rid]")).find((x) => x.dataset.rid === id);
    if (b) b.focus({ preventScroll: true });
  }

  // Arrow keys / Home / End / Page keys move between rows by LOGICAL position, scrolling the window
  // along, so keyboard navigation works for rows that are not in the DOM yet.
  function treeKeys(ev) {
    const flat = nav.flat, box = $("lt-files");
    if (!flat || !flat.length || ev.altKey || ev.ctrlKey || ev.metaKey) return;
    const page = Math.max(1, Math.floor(((box && box.clientHeight) || 800) / ROW_H) - 1);
    const step = { ArrowDown: 1, ArrowUp: -1, PageDown: page, PageUp: -page }[ev.key];
    if (step === undefined && ev.key !== "Home" && ev.key !== "End") return;
    ev.preventDefault();
    let idx = flat.findIndex((f) => f.id === nav.curKey);
    if (idx < 0) idx = 0;
    let to = ev.key === "Home" ? 0 : ev.key === "End" ? flat.length - 1 : Math.max(0, Math.min(flat.length - 1, idx + step));
    const dir = ev.key === "Home" || step > 0 ? 1 : -1;
    while (to >= 0 && to < flat.length && !flat[to].focusable) to += dir;
    if (to < 0 || to >= flat.length) return;
    nav.curKey = flat[to].id;
    if (box) {
      const rowTop = treeTop() + to * ROW_H;
      if (rowTop < box.scrollTop) box.scrollTop = rowTop;
      else if (rowTop + ROW_H > box.scrollTop + box.clientHeight) box.scrollTop = rowTop + ROW_H - box.clientHeight;
    }
    paintWindow(true);
    focusRow(nav.curKey);
  }

  // ------------------------------------------------------------- the listing pool
  // Everything that lists a folder because a ROW needs it (a refresh, a restored workspace, a folder
  // opened by hand) goes through this pool: at most four requests at once, only for rows that are
  // painted, a visible job with Cancel once more is waiting than runs at once. Nothing fails silently:
  // a failed or cancelled listing leaves its retry row.
  const POOL = 4;
  const pool = { queue: [], queued: new Set(), running: 0, done: 0, total: 0, said: "", cancelled: false };
  nav.pool = pool;          // read by a page (and the e2e) to tell "still listing" from "settled"

  function enqueue(dir) {
    if (pool.queued.has(dir) || nav.dirErr[dir]) return;
    if (nav.inflight[dir] && !nav.stale.has(dir)) return;          // already on its way
    if (nav.inflight[dir]) return;
    pool.queued.add(dir); pool.queue.push(dir); pool.total++;
    pump();
  }

  function pump() {
    while (pool.running < POOL && pool.queue.length) {
      const dir = pool.queue.shift();
      pool.queued.delete(dir);
      pool.running++;
      loadDir(dir, false, nav.stale.has(dir)).then(() => { pool.done++; }, () => { pool.done++; }).then(() => {
        pool.running--;
        poolSay();
        scheduleRender();
        pump();
      });
    }
    poolSay();
  }

  function poolSay() {
    if (nav.expandJob) return;
    const pending = pool.queue.length + pool.running;
    if (!pending) {
      const mine = pool.said && $("tree-job") && $("tree-job").textContent.includes(pool.said);
      if (mine && !pool.cancelled) jobSay("");
      pool.said = ""; pool.done = 0; pool.total = 0; pool.cancelled = false;
      return;
    }
    if (pool.cancelled || pool.total <= POOL) return;              // fits in one go: no job line
    pool.said = `Listing folders… ${pool.done} of ${pool.total}`;
    jobSay(pool.said, cancelPool);
  }

  function cancelPool() {
    const n = pool.queue.length;
    for (const dir of pool.queue) {
      pool.queued.delete(dir);
      nav.dirErr[dir] = { error: "cancelled" };
      if (nav.stale.has(dir)) { delete nav.dirs[dir]; nav.stale.delete(dir); }
    }
    pool.queue = [];
    pool.cancelled = true;
    pool.said = "";
    jobSay(`Cancelled — ${n} folder${n === 1 ? "" : "s"} not listed; each shows a retry row.`);
    renderTree();
  }

  // ----------------------------------------------------------------------------- P11 explorer polish
  // Compact folders: the server marks a directory whose chain holds nothing but the next directory
  // (`compact_path` / `compact_name`); with the toggle on, the row IS the chain and opens its last
  // directory. Expand/collapse all and copy path work on what is LOADED — never a walk of the repo.
  function dirKey(e) { return nav.compact && e.compact_path ? e.compact_path : e.path; }

  // The folders the user can SEE and that are closed: the frontier Expand all opens. Folders inside
  // them are not visible yet, so one press is one level deeper than what is on screen — also after
  // Collapse all, whatever was loaded before.
  function visibleClosedDirs() {
    const keys = new Set();
    walkRows((r) => { if (r.t === "dir" && !r.isOpen) keys.add(r.key); });
    return Array.from(keys);
  }

  let renderSoon = 0;
  // a render costs what the tree has open, so a run that opens thousands of folders repaints less often
  function scheduleRender() { if (!renderSoon) renderSoon = setTimeout(() => { renderSoon = 0; renderTree(); }, Math.max(80, lastRenderMs * 4)); }

  // The job line. Progress updates in place: the Cancel button stays the SAME element while the
  // count ticks and does not move, so a press that starts on it also ends on it (a rebuilt button swallows the click).
  function jobSay(text, cancel) {
    const box = $("tree-job");
    if (!box) return;
    box.hidden = !text;
    if (!text) { box.replaceChildren(); return; }
    let msg = box.querySelector("span"), btn = box.querySelector("button");
    if (!msg) { box.replaceChildren(); msg = el("span", ""); box.append(msg); btn = null; }
    msg.textContent = text;
    if (cancel && !btn) {
      btn = el("button", "btn ghost small", "Cancel");
      btn.type = "button";
      box.prepend(btn);        // first, so the counter growing a digit never moves the button
    }
    if (btn && !cancel) btn.remove();
    if (btn && cancel) btn.onclick = cancel;
  }

  // Expand all: opens every folder that is VISIBLE and closed now, loading each one's children four
  // at a time as a visible job with Cancel. Folders inside them are not visible yet and stay closed,
  // so one press goes exactly one level deeper than the screen shows — the work is bounded by what
  // the user has opened, the DOM by the viewport (paintWindow), and nothing walks the whole repo.
  async function expandAll() {
    if (nav.expandJob) return;
    if ($("tree-filter").value.trim()) { jobSay("Clear the filter to expand folders."); return; }
    await loadTree();
    const targets = visibleClosedDirs();
    if (!targets.length) { jobSay("Every visible folder is already open."); return; }
    const job = (nav.expandJob = { total: targets.length, done: 0, failed: 0, cancelled: false });
    const cancel = () => { job.cancelled = true; jobSay(`Cancelling… ${job.done} of ${job.total} folders opened so far.`); };
    const say = () => { if (!job.cancelled) jobSay(`Opening folders… ${job.done} of ${job.total}`, cancel); };
    say();
    let next = 0;
    const worker = async () => {
      while (!job.cancelled && next < targets.length) {
        const key = targets[next++];
        nav.collapsed[key] = false;
        if ((!nav.dirs[key] || nav.stale.has(key)) && (await loadDir(key, false, nav.stale.has(key))) === null) job.failed++;
        job.done++;
        say();
        scheduleRender();
      }
    };
    await Promise.all([worker(), worker(), worker(), worker()]);
    nav.expandJob = null;
    nav.expandRuns++;
    const all = nav.tree && nav.tree.count ? ` ${Number(nav.tree.count).toLocaleString("en-US")}-file` : "";
    const head = job.cancelled ? `Cancelled after ${job.done} of ${job.total} folders — the rest stay closed.`
      : `Opened ${job.done} folder${job.done === 1 ? "" : "s"}${job.failed ? ` (${job.failed} could not be listed)` : ""}.`;
    jobSay(`${head} Press Expand all again to open the folders inside them, one level deeper each time — it never walks the whole${all} tree.`);
    renderTree();
  }

  function collapseAll() {
    if (nav.expandJob) nav.expandJob.cancelled = true;
    let n = 0;
    for (const k of Object.keys(nav.collapsed)) if (nav.collapsed[k] === false) { nav.collapsed[k] = true; n++; }
    jobSay(n ? `Closed ${n} folder${n === 1 ? "" : "s"}.` : "");
    renderTree();
  }

  function setCompact(on) {
    nav.compact = !!on;
    try { localStorage.setItem("mokata-ide:compact", on ? "1" : "0"); } catch (_e) { /* private mode */ }
    renderTree();
  }

  async function absolutePath(rel) {
    const t = await api("/api/code-tree").catch(() => null);
    const base = t && !t.error && !t.sample && typeof t.tree === "string" ? t.tree : "";
    if (!base) return null;
    const sep = base.includes("\\") && !base.includes("/") ? "\\" : "/";
    return base.replace(/[\\/]+$/, "") + sep + rel.split("/").join(sep);
  }

  async function copyPath(rel, absolute) {
    if (!rel) { jobSay("No file is open."); return; }
    let text = rel;
    if (absolute) {
      text = await absolutePath(rel);
      if (!text) { jobSay("The absolute path is not known here (SAMPLE data, or the server did not say)."); return; }
    }
    const ok = await (MK.copyText ? MK.copyText(text) : Promise.resolve(false));
    jobSay(ok ? `Copied ${text}` : `Could not copy (the browser blocked it): ${text}`);
  }

  let treeMenu = null;
  function closeTreeMenu() { if (treeMenu) { treeMenu.remove(); treeMenu = null; } }
  function openTreeMenu(ev, rel) {
    ev.preventDefault();
    closeTreeMenu();
    const m = (treeMenu = el("div", "tab-menu"));
    m.setAttribute("role", "menu");
    for (const [label, abs] of [["Copy relative path", false], ["Copy absolute path", true]]) {
      const b = el("button", "tab-menu-item", label);
      b.type = "button";
      b.setAttribute("role", "menuitem");
      b.onclick = () => { closeTreeMenu(); copyPath(rel, abs); };
      m.append(b);
    }
    m.onkeydown = (k) => { if (k.key === "Escape") { closeTreeMenu(); } };
    document.body.append(m);
    const x = ev.clientX || 40, y = ev.clientY || 40;
    m.style.left = `${Math.max(4, Math.min(x, window.innerWidth - 220))}px`;
    m.style.top = `${Math.max(4, Math.min(y, window.innerHeight - 100))}px`;
    m.querySelector("button").focus();
    setTimeout(() => document.addEventListener("click", closeTreeMenu, { once: true }), 0);
  }

  MK.explorer = {
    expandAll, collapseAll, setCompact,
    copyOpenPath: (absolute) => copyPath(nav.path, absolute),
  };

  // A pasted / linked `path:line:col` (the server parses it and contains it — Windows drive
  // letters included — then answers the workspace file): open it there, or say why not.
  MK.openLocator = async function openLocator(text) {
    const r = await api("/api/locate", { text: String(text || "") }).catch((e) => ({ error: "fetch", note: String(e) }));
    if (!r || r.error) { if (MK.toast) MK.toast(`Not opened: ${(r && r.note) || "no answer"}`); return false; }
    await openFile(r.path, r.line, null, { col: r.col || 0 });
    return true;
  };
  MK.locatorLooks = function locatorLooks(text) {
    const s = String(text || "").trim();
    return /^.+?:\d{1,9}(:\d{1,9})?:?$/.test(s) || /^([A-Za-z]:[\\/]|[\\/])/.test(s);
  };

  // ----------------------------------------------------------------------------- one file
  async function openFile(path, line, sym, opts) {
    const o = opts || {};
    if (!o.keepSymbol) MK.setMode("navigate");   // a live reload is already in Navigate (P35)
    if (!o.fromHistory) pushHistory({ path, line: line || 1, sym: sym || null });
    nav.line = line || 1;
    nav.col = o.col || 0;
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
      if (n === nav.line && nav.here && nav.col) { lt.classList.add("at-col"); lt.style.setProperty("--col", String(nav.col - 1)); }
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
    const cur = groupOf(nav.tab);
    for (const [g, label, kinds] of GROUPS) {
      const b = el("button", "pill itab" + (cur === g ? " active" : ""), label);
      b.type = "button";
      b.id = "rtab-" + g;
      b.dataset.rtab = g;
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(cur === g));
      b.setAttribute("aria-controls", "ref-pane");
      b.tabIndex = cur === g ? 0 : -1;
      b.onclick = () => pickGroup(g);
      box.append(b);
      void kinds;
    }
    $("ref-pane").setAttribute("aria-labelledby", "rtab-" + cur);
    const g = GROUPS.find((x) => x[0] === cur);
    let kb = $("ref-kinds");
    if (!kb) { kb = el("div", "filters ref-kinds"); kb.id = "ref-kinds"; kb.setAttribute("role", "group"); $("ref-pane").prepend(kb); }
    kb.setAttribute("aria-label", g[1] + ": show");
    kb.replaceChildren();
    kb.hidden = !g[2].length;
    for (const [k, label] of g[2]) {
      const a = nav.answers[k];
      const b = el("button", "pill" + (nav.tab === k ? " active" : ""), a && a.references ? `${label} ${a.references.length}` : label);
      b.type = "button";
      b.dataset.kind = k;
      b.setAttribute("aria-pressed", String(nav.tab === k));
      b.onclick = () => { nav.tab = k; renderTabs(); loadTab(k); };
      kb.append(b);
    }
    $("ref-expand").hidden = cur !== "impact-group" || !nav.sym;
  }
  function pickGroup(g) {
    const grp = GROUPS.find((x) => x[0] === g);
    nav.tab = g === "history" ? "history" : (grp[2].some((k) => k[0] === nav.tab) ? nav.tab : grp[2][0][0]);
    renderTabs();
    loadTab(nav.tab);
  }
  // Find Usages / a language-server answer: Code, References tab (the answer box sits at its top).
  MK.showCodeAnswer = () => {
    if (ui.mode !== "navigate") MK.setMode("navigate");
    if (groupOf(nav.tab) !== "refs-group") pickGroup("refs-group");
  };

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
    $("tree-expand").onclick = expandAll;
    $("tree-collapse").onclick = collapseAll;
    const tree = $("tree");
    tree.tabIndex = -1;
    tree.addEventListener("focusin", (ev) => { const r = ev.target.closest && ev.target.closest("[data-rid]"); if (r) nav.curKey = r.dataset.rid; });
    tree.addEventListener("keydown", treeKeys);
    let scrollTick = 0;
    const repaint = () => { if (!scrollTick) scrollTick = requestAnimationFrame(() => { scrollTick = 0; paintWindow(false); }); };
    $("lt-files").addEventListener("scroll", repaint, { passive: true });
    // a taller panel (window resize, zoom, a pane dragged) shows more rows without any scroll
    if (window.ResizeObserver) new ResizeObserver(repaint).observe($("lt-files"));
    $("tree-compact").checked = nav.compact;
    $("tree-compact").onchange = () => setCompact($("tree-compact").checked);
    let t = 0;
    $("tree-filter").oninput = () => { clearTimeout(t); t = setTimeout(renderTree, 150); };
    $("nav-back").onclick = () => navHistory(-1);
    $("nav-fwd").onclick = () => navHistory(1);
    $("change-prev").onclick = () => navChange(-1);
    $("change-next").onclick = () => navChange(1);
    $("ref-tabs").addEventListener("keydown", (ev) => MK.tabKeys(ev, "#ref-tabs [data-rtab]", (b) => pickGroup(b.dataset.rtab)));
    $("ref-expand").onclick = () => { if (MK.openGraph && nav.sym) MK.openGraph("ripple", nav.sym); };
    // Code's side: Files · Search · Outline (WAI-ARIA tabs)
    const ltabs = $("code-ltabs");
    ltabs.addEventListener("click", (ev) => { const b = ev.target.closest && ev.target.closest("[data-ltab]"); if (b) showSide(b.dataset.ltab); });
    ltabs.addEventListener("keydown", (ev) => MK.tabKeys(ev, "#code-ltabs [data-ltab]", (b) => showSide(b.dataset.ltab)));
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
  function showSide(which) {
    for (const b of document.querySelectorAll("#code-ltabs [data-ltab]")) {
      const on = b.dataset.ltab === which;
      b.setAttribute("aria-selected", String(on)); b.tabIndex = on ? 0 : -1; b.classList.toggle("active", on);
      $("lt-" + b.dataset.ltab).hidden = !on;
    }
    nav.side = which;
    if (which === "files") paintWindow(true);
  }
  MK.showCodeSide = showSide;
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
