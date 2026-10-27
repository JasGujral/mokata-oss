// mokata ide — tabs + session restore (0.0.26 P2, ID-TABS; doc 109 §13.4).
//
// ONE tab per file (0.0.26 P34, G20/R31): the tab shows the file in Code or its change in Review —
// the Δ on the active tab toggles between the two. Tabs are unique by (path, kind), restore
// included. Pinned commit tabs are a reserved format (`kind: "commit"`, see ide/workspace.py) no
// view opens yet. Close menu (Close, Close Others, Close to the Right, Close to the Left, Close All
// — pinned tabs survive the bulk ones), reopen closed tab, Alt+1..9 — every key through the
// registry, and every key an Alt chord: Ctrl/⌘+W, Ctrl/⌘+Tab and Ctrl/⌘+Shift+T belong to the
// browser (Ctrl+W closed the IDE's window, B-F16) and the registry refuses them (R30).
//
// The workspace is the SERVER's (`/api/workspace`, per repo under .mokata/temp_local/ide/), not
// this browser's: a `mokata ide` relaunch or `/mokata:review-window` re-opens the same tabs,
// scroll positions and explorer dirs, and the restore line says what changed while the window was
// away (the focused session, its run's stage, approvals waiting on you).
//
// The tab-list rules are the PURE model below (MK.tabsModel — no DOM; tests run it under node).
// SAFETY: repo text enters the page through textContent only.
"use strict";

(function (root) {
  const MK = (root.MK = root.MK || {});

  // ============================================================================ the model
  const MAX_TABS = 40;      // = ide/workspace.py MAX_TABS (the server bounds what it stores too)
  const MAX_CLOSED = 20;
  const MAX_DIRS = 400;     // = ide/workspace.py MAX_DIRS
  const VIEWS = ["code", "diff"];          // = ide/workspace.py TAB_VIEWS

  function keyOf(t) {
    if (t.kind === "commit") return `commit:${t.sha}:${t.path || ""}`;
    return `file:${t.path}`;               // one tab per file, whichever view it shows
  }

  function mk(t) {
    const tab = { kind: t.kind === "commit" ? "commit" : "file", path: t.path, line: t.line || 1,
                  scroll: t.scroll || 0, pinned: !!t.pinned, preview: !!t.preview };
    if (tab.kind === "file") tab.view = VIEWS.includes(t.view) ? t.view : "code";
    if (tab.kind === "commit") tab.sha = t.sha;
    tab.key = keyOf(tab);
    return tab;
  }

  function empty() { return { tabs: [], active: null, closed: [] }; }

  function pushClosed(closed, gone) {
    const out = closed.concat(gone.map((t) => ({ ...t, preview: false })));
    return out.slice(-MAX_CLOSED);
  }

  // Open (or re-activate) a tab. A `preview` tab is the one reusable slot walking with ] / [
  // fills; opening another preview REPLACES it; opening it for good (or double-clicking) keeps it.
  // Over MAX_TABS the oldest unpinned, inactive tab goes to the closed list (reopen brings it back).
  function open(m, t, opts) {
    const o = opts || {};
    const tab = mk({ ...t, preview: !!o.preview });
    let tabs = m.tabs.slice();
    let closed = m.closed;
    const at = tabs.findIndex((x) => x.key === tab.key);
    if (at >= 0) {
      tabs[at] = { ...tabs[at], line: t.line || tabs[at].line, view: t.view || tabs[at].view,
                   preview: tabs[at].preview && !!o.preview };
      return { ...m, tabs, active: tab.key };
    }
    const pv = tabs.findIndex((x) => x.preview && !x.pinned && x.kind === tab.kind);
    if (o.preview && pv >= 0) {
      tabs[pv] = tab;
    } else {
      const cur = tabs.findIndex((x) => x.key === m.active);
      tabs.splice(cur >= 0 ? cur + 1 : tabs.length, 0, tab);
    }
    while (tabs.length > MAX_TABS) {
      const i = tabs.findIndex((x) => !x.pinned && x.key !== tab.key);
      if (i < 0) break;
      closed = pushClosed(closed, [tabs[i]]);
      tabs.splice(i, 1);
    }
    return { tabs, active: tab.key, closed };
  }

  // how: "one" | "others" | "right" | "left" | "all". The bulk closes keep pinned tabs; closing
  // ONE tab closes it even if pinned (you asked for that tab). The active tab moves to the
  // right-hand neighbour of what closed, else the left (or to `key` for others/right/left).
  function close(m, key, how) {
    const h = how || "one";
    const i = m.tabs.findIndex((x) => x.key === key);
    if (i < 0 && h !== "all") return m;
    const gone = (t, k) => {
      if (h === "one") return t.key === key;
      if (t.pinned) return false;
      if (h === "others") return t.key !== key;
      if (h === "right") return k > i;
      if (h === "left") return k < i;
      return true;                                               // all
    };
    const out = [], removed = [];
    m.tabs.forEach((t, k) => (gone(t, k) ? removed : out).push(t));
    if (!removed.length) return m;
    let active = m.active;
    if (!out.some((t) => t.key === active)) {
      if (h !== "one" && h !== "all" && out.some((t) => t.key === key)) active = key;
      else {
        const was = Math.max(0, m.tabs.findIndex((t) => t.key === m.active));
        const right = m.tabs.slice(was + 1).find((t) => out.includes(t));
        const left = m.tabs.slice(0, was).reverse().find((t) => out.includes(t));
        active = (right || left || { key: null }).key;
      }
    }
    return { tabs: out, active, closed: pushClosed(m.closed, removed) };
  }

  // Reopen the most recently closed tab (one per call, newest first), active, at the end.
  function reopen(m) {
    if (!m.closed.length) return m;
    const closed = m.closed.slice();
    let t = closed.pop();
    while (t && m.tabs.some((x) => x.key === t.key)) t = closed.pop();
    if (!t) return { ...m, closed };
    return { tabs: m.tabs.concat([{ ...t, preview: false }]), active: t.key, closed };
  }

  // Ctrl+Tab: the next tab to the right (wrapping); delta -1 the left.
  function cycle(m, delta) {
    if (!m.tabs.length) return m;
    const i = m.tabs.findIndex((t) => t.key === m.active);
    const n = m.tabs.length;
    return { ...m, active: m.tabs[((i < 0 ? 0 : i + delta) % n + n) % n].key };
  }

  // Alt+1..8 = that tab; Alt+9 = the LAST tab (as browsers and editors do).
  function nth(m, n) {
    if (!m.tabs.length) return m;
    const t = n >= 9 ? m.tabs[m.tabs.length - 1] : m.tabs[n - 1];
    return t ? { ...m, active: t.key } : m;
  }

  function keep(m, key) {
    return { ...m, tabs: m.tabs.map((t) => (t.key === key ? { ...t, preview: false } : t)) };
  }

  // A saved workspace's tabs merged with any the page opened before it arrived (kept, after the
  // saved ones); the saved active tab stays the active one. Unique by key, even when the saved
  // list itself repeats one (R31: a restore never shows a file twice).
  function merge(m, saved) {
    const seen = new Set(m.tabs.map((x) => x.key));
    const tabs = (saved.tabs || []).map((t) => mk(t)).filter((t) => (seen.has(t.key) ? false : (seen.add(t.key), true)));
    const all = tabs.concat(m.tabs).slice(-MAX_TABS);
    const active = all.some((t) => t.key === saved.active) ? saved.active : m.active;
    return { tabs: all, active, closed: (saved.closed || []).map((t) => mk(t)).concat(m.closed).slice(-MAX_CLOSED) };
  }

  // What the server stores (the preview flag is a page-only notion).
  function toSaved(m) {
    const strip = (t) => { const o = { ...t }; delete o.preview; return o; };
    return { tabs: m.tabs.map(strip), active: m.active, closed: m.closed.map(strip) };
  }

  const EXT = {
    py: ["py", "py"], pyi: ["py", "py"], js: ["js", "js"], mjs: ["js", "js"], cjs: ["js", "js"],
    jsx: ["jsx", "js"], ts: ["ts", "ts"], tsx: ["tsx", "ts"], json: ["{}", "data"],
    md: ["md", "doc"], rst: ["rst", "doc"], txt: ["txt", "doc"], html: ["<>", "web"],
    css: ["#", "web"], scss: ["#", "web"], sh: ["$", "sh"], bash: ["$", "sh"], ps1: ["ps", "sh"],
    go: ["go", "go"], rs: ["rs", "rs"], java: ["jv", "jv"], kt: ["kt", "jv"], rb: ["rb", "rb"],
    c: ["c", "c"], h: ["h", "c"], cc: ["c++", "c"], cpp: ["c++", "c"], hpp: ["h++", "c"],
    cs: ["c#", "c"], swift: ["sw", "rs"], yml: ["yml", "data"], yaml: ["yml", "data"],
    toml: ["tml", "data"], ini: ["ini", "data"], cfg: ["cfg", "data"], sql: ["sql", "data"],
    lock: ["lck", "data"],
  };
  // [badge text, colour family] for a path: the tab's language icon (text — no image, no URL).
  function langOf(path) {
    const name = String(path || "").slice(String(path || "").lastIndexOf("/") + 1);
    if (/^dockerfile$/i.test(name)) return ["dkr", "sh"];
    if (/^makefile$/i.test(name)) return ["mk", "sh"];
    const dot = name.lastIndexOf(".");
    return (dot > 0 && EXT[name.slice(dot + 1).toLowerCase()]) || ["·", "plain"];
  }

  // The Δ toggle: the same tab, the other view.
  function toggleView(m, key) {
    return { ...m, tabs: m.tabs.map((t) => (t.key === key && t.kind === "file" ? { ...t, view: t.view === "diff" ? "code" : "diff" } : t)) };
  }

  MK.tabsModel = { MAX_TABS, MAX_CLOSED, VIEWS, keyOf, mk, empty, open, close, reopen, cycle, nth, keep,
                   merge, toSaved, langOf, toggleView };

  // ============================================================================ the page
  if (!root.document || !root.mokataIDE || !MK.ui) return;
  const M = MK.tabsModel;
  const { $, el, ui } = MK;
  let model = M.empty();
  let loaded = false, ws = null, banner = null;
  let saveTimer = 0, lastFocus;
  const strip = el("div", "tabstrip");
  strip.id = "tabstrip";
  strip.setAttribute("role", "tablist");
  strip.setAttribute("aria-label", "open tabs");
  const list = el("div", "tabs-list");
  const note = el("button", "tab-restored small");
  note.type = "button";
  note.hidden = true;
  const menu = el("div", "tab-menu");
  menu.setAttribute("role", "menu");
  menu.hidden = true;

  function activeTab() { return model.tabs.find((t) => t.key === model.active) || null; }

  // ---------------------------------------------------------------------------- render
  function render() {
    list.replaceChildren();
    model.tabs.forEach((t, k) => {
      const b = el("div", "tab" + (t.key === model.active ? " active" : "") + (t.preview ? " preview" : "")
                   + (t.pinned ? " pinned" : ""));
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(t.key === model.active));
      b.tabIndex = t.key === model.active ? 0 : -1;
      b.title = `${t.view === "diff" ? "change · " : ""}${t.path || t.sha}${k < 9 ? `  (Alt+${k === model.tabs.length - 1 && k >= 8 ? 9 : k + 1})` : ""}`;
      const [badge, fam] = t.kind === "commit" ? ["@", "plain"] : M.langOf(t.path);
      b.append(el("span", "tab-lang lang-" + fam, badge));
      const p = String(t.path || t.sha || "");
      b.append(el("span", "tab-name mono", p.slice(p.lastIndexOf("/") + 1)));
      if (t.kind === "file") {
        // Δ: this file's change (Review) ⇄ the file (Code) — one tab, two views (G20).
        const dl = el("button", "tab-delta" + (t.view === "diff" ? " on" : ""), "Δ");
        dl.type = "button";
        dl.title = t.view === "diff" ? "showing the change — click for the file" : "showing the file — click for its change";
        dl.setAttribute("aria-pressed", String(t.view === "diff"));
        dl.setAttribute("aria-label", "toggle change view for " + p);
        dl.onclick = (ev) => { ev.stopPropagation(); flip(t.key); };
        b.append(dl);
      }
      const x = el("button", "tab-x", "×");
      x.type = "button";
      x.title = "Close (Alt+W)";
      x.setAttribute("aria-label", "close " + p);
      x.onclick = (ev) => { ev.stopPropagation(); doClose(t.key, "one"); };
      b.append(x);
      b.onclick = () => activate(t.key);
      b.ondblclick = () => { model = M.keep(model, t.key); render(); save(); };
      b.onauxclick = (ev) => { if (ev.button === 1) { ev.preventDefault(); doClose(t.key, "one"); } };
      b.oncontextmenu = (ev) => { ev.preventDefault(); openMenu(t.key, ev.clientX, ev.clientY); };
      list.append(b);
    });
    strip.hidden = !model.tabs.length && note.hidden;
  }

  // ---------------------------------------------------------------------------- the close menu
  const MENU = [["Close", "one"], ["Close Others", "others"], ["Close to the Right", "right"],
                ["Close to the Left", "left"], ["Close All", "all"]];
  function openMenu(key, x, y) {
    menu.replaceChildren();
    for (const [label, how] of MENU) {
      const b = el("button", "tab-menu-item", label);
      b.type = "button";
      b.setAttribute("role", "menuitem");
      const after = M.close(model, key, how);
      b.disabled = after === model;
      b.onclick = () => { closeMenu(); doClose(key, how); };
      menu.append(b);
    }
    const t = model.tabs.find((z) => z.key === key);
    const pin = el("button", "tab-menu-item", t && t.pinned ? "Unpin" : "Pin");
    pin.type = "button";
    pin.onclick = () => {
      closeMenu();
      model = { ...model, tabs: model.tabs.map((z) => (z.key === key ? { ...z, pinned: !z.pinned, preview: false } : z)) };
      render(); save();
    };
    const re = el("button", "tab-menu-item", "Reopen Closed Tab");
    re.type = "button";
    re.disabled = !model.closed.length;
    re.onclick = () => { closeMenu(); doReopen(); };
    menu.append(el("hr", "tab-menu-sep"), pin, re);
    menu.style.left = `${Math.max(4, Math.min(x, root.innerWidth - 220))}px`;
    menu.style.top = `${Math.max(4, Math.min(y, root.innerHeight - 260))}px`;
    menu.hidden = false;
    const first = menu.querySelector("button:not([disabled])");
    if (first) first.focus();
  }
  function closeMenu() { menu.hidden = true; }

  // ---------------------------------------------------------------------------- actions
  async function activate(key) {
    const t = model.tabs.find((z) => z.key === key);
    if (!t) return;
    remember();
    model = { ...model, active: key };
    render();
    if (t.kind === "file" && t.view === "diff") {
      MK.setMode("review");
      const i = (ui.files || []).findIndex((f) => f.path === t.path);
      if (i >= 0) { MK.showFile(i, { tab: true }); if (t.scroll) $("review-scroll").scrollTop = t.scroll; }
      else flash(`${t.path} has no change in ${MK.viewing ? MK.viewing().label : "this view"}`);
    } else if (t.kind === "file" && MK.openFile) {
      await MK.openFile(t.path, t.line || 1, null, { tab: true });
      if (t.scroll && MK.nav && MK.nav.path === t.path) $("code-scroll").scrollTop = t.scroll;
    }
    save();
  }

  function flip(key) {
    const t = model.tabs.find((z) => z.key === key);
    if (t && t.view !== "diff" && !(ui.files || []).some((f) => f.path === t.path)) {
      flash(`${t.path} has no change in ${MK.viewing ? MK.viewing().label : "this view"}`);
      return;
    }
    remember();
    model = M.toggleView({ ...model, active: key }, key);
    render();
    activate(key);
  }

  function doClose(key, how) {
    const before = model.active;
    const next = M.close(model, key || model.active, how);
    if (next === model) return;
    model = next;
    render();
    if (model.active && model.active !== before) activate(model.active);
    save();
  }
  function doReopen() {
    const next = M.reopen(model);
    if (next === model) return;
    model = next;
    render();
    activate(model.active);
  }
  function go(next) { if (next.active && next.active !== model.active) { model = next; activate(model.active); } }

  // The active tab's line and scroll, taken from the view before it changes.
  function remember() {
    const t = activeTab();
    if (!t) return;
    let line = t.line, scroll = t.scroll;
    if (t.kind === "file" && MK.nav && MK.nav.path === t.path) { line = MK.nav.line || line; scroll = $("code-scroll").scrollTop; }
    if (t.view === "diff" && ui.files && ui.files[ui.sel] && ui.files[ui.sel].path === t.path) scroll = $("review-scroll").scrollTop;
    if (line !== t.line || scroll !== t.scroll) {
      model = { ...model, tabs: model.tabs.map((z) => (z.key === t.key ? { ...z, line, scroll } : z)) };
    }
  }

  function flash(text) {
    note.hidden = false;
    note.classList.add("warn-note");
    note.textContent = text;
    note.onclick = () => { note.hidden = true; render(); };
    render();
  }

  // ---------------------------------------------------------------------------- the views' hooks
  // nav.js calls this after every file it shows (tree, ⌘P, a reference, follow, a refresh).
  MK.onFileOpened = (path, line, opts) => {
    const o = opts || {};
    const prev = activeTab();
    if (o.reload && o.fromHistory && prev && prev.kind === "file" && prev.path === path) {
      // In-place reload (a write or a reindex — nav.js onRefreshNav): the same line, the same
      // scroll, no jump. (An `ide_open` / Follow reload carries no fromHistory: it jumps.)
      if (prev.scroll) $("code-scroll").scrollTop = prev.scroll;
      return;
    }
    if (o.tab) return;                                           // a tab switch: already active
    remember();
    model = M.open(model, { kind: "file", path, line, view: "code" });
    render(); save();
  };
  // app.js calls this after every diff file it shows. Walking with ] / [ fills ONE preview tab —
  // the file's own tab (switched to its change) when the file already has one. A refresh re-shows
  // the file in view (same key, same view: nothing to do); outside Review it is not shown.
  MK.onDiffShown = (f, opts) => {
    const o = opts || {};
    if (o.tab || !f || ui.mode !== "review") return;
    const key = M.keyOf({ kind: "file", path: f.path });
    const cur = model.tabs.find((t) => t.key === key);
    if (model.active === key && cur && cur.view === "diff") return;
    remember();
    model = M.open(model, { kind: "file", path: f.path, view: "diff" }, { preview: !cur });
    render(); save();
  };

  // ---------------------------------------------------------------------------- persistence
  function payload() {
    remember();
    const collapsed = (MK.nav && MK.nav.collapsed) || {};
    const row = ui.state && ui.state.focus_row && ui.state.focus_row.id === ui.focus ? ui.state.focus_row : null;
    return { ...M.toSaved(model), mode: ui.mode,
             expanded: Object.keys(collapsed).filter((d) => collapsed[d] === false).slice(-MAX_DIRS),
             collapsed: Object.keys(collapsed).filter((d) => collapsed[d] === true).slice(-MAX_DIRS),
             run: (row && row.run) || null };
  }
  function save(now) {
    if (!loaded || ui.restoring) return;                         // never overwrite before restore
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      saveTimer = 0;
      root.fetch("/api/workspace", { method: "POST", keepalive: !!now,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload()) }).catch(() => {});
    }, now ? 0 : 500);
  }
  MK.saveView = () => save();                                     // setMode / openFile

  function since(w) {
    const parts = [];
    if (w.refused) return { text: "saved tabs refused — starting clean (see the server log)", warn: true, title: w.refused };
    if (model.tabs.length) parts.push(`restored ${model.tabs.length} tab(s)`);
    const live = w.live || {};
    if (live.focus) parts.push(`session ${String(live.focus).slice(0, 8)}`);
    if (live.run) {
      parts.push(live.run.finished ? `run ${live.run.run} finished`
        : live.run.current ? `run ${live.run.run} at ${live.run.current}${live.run.regressed ? " (back at spec)" : ""}`
        : `run ${live.run.run}`);
    }
    const n = (live.pending || []).length;
    if (n) parts.push(`${n} approval(s) waiting on you`);
    const notes = (live.notes || []).concat(live.run && live.run.note ? [live.run.note] : []);
    if (notes.length) parts.push(`${notes.length} could not be read`);
    if (!parts.length) return null;
    const title = (live.pending || []).map((p) => `${p.tool} · ${p.command}`).concat(notes).join("\n");
    return { text: parts.join(" · "), warn: n > 0, title };
  }

  async function load() {
    const w = await MK.api("/api/workspace").catch(() => null);
    ws = w && !w.error ? w : { tabs: [], closed: [], expanded: [], collapsed: [], live: {} };
    model = M.merge(model, ws);
    if (MK.nav) {
      for (const d of ws.expanded || []) MK.nav.collapsed[d] = false;
      for (const d of ws.collapsed || []) MK.nav.collapsed[d] = true;
    }
    banner = since(ws);
    if (banner) {
      note.hidden = false;
      note.textContent = banner.text;
      note.title = banner.title || "";
      note.classList.toggle("warn-note", !!banner.warn);
      note.onclick = () => { note.hidden = true; render(); if ((ws.live.pending || []).length) MK.setMode("sessions"); };
    }
    lastFocus = ui.focus;
    loaded = true;
    render();
    return ws;
  }
  let loading = null;

  // boot.js: called after the first refresh unless an /open link chose the file. Puts back the
  // mode, the panel and the active tab (with its scroll) from the SERVER's workspace.
  MK.restoreView = async function restoreView() {
    const w = await loading;
    if (!w) return;
    ui.restoring = true;
    try {
      const t = activeTab();
      if (t && (w.mode === "review" || w.mode === "navigate" || !w.mode)) await activate(t.key);
      else if (w.mode && MK.SCREENS.some((s) => s.id === w.mode)) MK.setMode(w.mode);
    } finally { ui.restoring = false; ui.restored = true; }   // P32: the scale test waits on it
  };

  MK.onRefreshTabs = () => { if (loaded && ui.focus !== lastFocus) { lastFocus = ui.focus; save(); } };

  MK.wireTabs = function wireTabs() {
    strip.append(list, note);
    const center = root.document.querySelector("main.center");
    if (center) center.prepend(strip);
    root.document.body.append(menu);
    strip.hidden = true;
    for (const id of ["code-scroll", "review-scroll"]) {
      const sc = $(id);
      if (sc) sc.addEventListener("scroll", () => { if (loaded) { clearTimeout(sc._t); sc._t = setTimeout(() => save(), 600); } }, { passive: true });
    }
    const tree = $("tree");
    if (tree) tree.addEventListener("click", () => save(), true);
    root.document.addEventListener("mousedown", (ev) => { if (!menu.hidden && !menu.contains(ev.target)) closeMenu(); });
    menu.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); closeMenu(); }
    });
    root.addEventListener("pagehide", () => { if (loaded) { clearTimeout(saveTimer); save(true); } });
    loading = load();
  };

  // ---------------------------------------------------------------------------- commands + keys
  const commands = [
    { id: "tabs.next", title: "Tabs: next tab", keys: "Alt+PageDown", inFields: true, run: () => go(M.cycle(model, 1)) },
    { id: "tabs.prev", title: "Tabs: previous tab", keys: "Alt+PageUp", inFields: true, run: () => go(M.cycle(model, -1)) },
    { id: "tabs.close", title: "Tabs: close tab", keys: "Alt+W", inFields: true, run: () => doClose(model.active, "one") },
    { id: "tabs.reopen", title: "Tabs: reopen closed tab", keys: "Alt+Shift+T", inFields: true, run: doReopen },
    { id: "tabs.toggleView", title: "Tabs: toggle the file ⇄ its change (Δ)", run: () => { if (model.active) flip(model.active); } },
    { id: "tabs.closeOthers", title: "Tabs: close others", run: () => doClose(model.active, "others") },
    { id: "tabs.closeRight", title: "Tabs: close to the right", run: () => doClose(model.active, "right") },
    { id: "tabs.closeLeft", title: "Tabs: close to the left", run: () => doClose(model.active, "left") },
    { id: "tabs.closeAll", title: "Tabs: close all", run: () => doClose(model.active, "all") },
    { id: "tabs.keep", title: "Tabs: keep the preview tab open", run: () => { if (model.active) { model = M.keep(model, model.active); render(); save(); } } },
  ];
  for (let n = 1; n <= 9; n++) {
    commands.push({ id: "tabs.go" + n, title: n === 9 ? "Tabs: last tab" : `Tabs: tab ${n}`,
                    keys: "Alt+" + n, run: () => go(M.nth(model, n)) });
  }
  root.mokataIDE.register({ id: "tabs", commands });
})(typeof window !== "undefined" ? window : globalThis);
