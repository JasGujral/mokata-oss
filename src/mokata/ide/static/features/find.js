// mokata ide — find in the current view (0.0.26 P3, ID-INFILE-FIND). ⌘F opens a find bar over
// WHATEVER the centre shows: the code viewer (only the code — never line numbers), a diff in
// Review, search results, and the RENDERED plan, spec and acceptance criteria in Plan. Seeded
// from the selection; match case and regex toggles; ↵ / ⇧↵ (or F3 / ⇧F3) next / previous; a
// live "n of m" count. Matches are wrapped in <mark> built with DOM calls (text nodes are split,
// never re-parsed) and unwrapped on close, so the page's text is left exactly as it was.
// Bounded: at most MAX_MARKS highlights and MAX_NODES text nodes are looked at.
// SAFETY: repo text stays in text nodes; nothing is re-parsed as markup.
"use strict";

(function () {
  const MK = window.MK;
  const MAX_MARKS = 5000, MAX_NODES = 200000;

  // --------------------------------------------------------------------------- pure logic
  function escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  // [[start, end], ...] of `q` in `text`. Literal by default (escaped into a RegExp so case-folding
  // never shifts offsets); `regex: true` takes q as a pattern — a bad pattern THROWS (the bar says
  // so). Empty matches are skipped, never looped on.
  function compile(q, opts) {
    const o = opts || {};
    if (!q) return null;
    return new RegExp(o.regex ? q : escapeRe(q), "g" + (o.caseSensitive ? "" : "i"));
  }
  function spans(text, re, limit) {
    const out = [];
    if (!re) return out;
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      if (m[0].length === 0) { re.lastIndex++; continue; }
      out.push([m.index, m.index + m[0].length]);
      if (limit && out.length >= limit) break;
    }
    return out;
  }
  function findSpans(text, q, opts) { return spans(String(text), compile(q, opts), 0); }

  MK.find = { findSpans, compile };

  if (typeof document === "undefined" || !document.getElementById) return;   // node tests

  // --------------------------------------------------------------------------- the scope
  // The visible centre pane; in Navigate, just the code column's text.
  function scope() {
    const pane = document.querySelector("main.center > section.view-pane:not([hidden])");
    if (!pane) return null;
    if (pane.id === "view-navigate") {
      const code = document.getElementById("code");
      const path = MK.nav && MK.nav.path;
      return { root: code, code: true, label: path ? path.slice(path.lastIndexOf("/") + 1) : "file" };
    }
    const mode = pane.id.replace(/^view-/, "");
    return { root: pane, code: false, label: mode.charAt(0).toUpperCase() + mode.slice(1) };
  }

  function textNodes(sc) {
    const SKIP = "script,style,input,textarea,select,button,.ln,.gut,[hidden],mark.fm";
    const walker = document.createTreeWalker(sc.root, NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        const p = n.parentElement;
        if (!p || !n.data) return NodeFilter.FILTER_REJECT;
        if (p.closest(SKIP)) return NodeFilter.FILTER_REJECT;
        if (sc.code && !p.closest(".lt")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    const out = [];
    let n;
    while ((n = walker.nextNode()) && out.length < MAX_NODES) out.push(n);
    return out;
  }

  // --------------------------------------------------------------------------- state
  const f = { open: false, marks: [], idx: -1, sc: null, timer: 0, obs: null, capped: false,
              caseSensitive: false, regex: false };
  let bar, input, count, scopeEl, bCase, bRe;

  function clearMarks() {
    const parents = new Set();
    for (const m of f.marks) {
      const p = m.parentNode;
      if (!p) continue;
      p.replaceChild(document.createTextNode(m.textContent), m);
      parents.add(p);
    }
    for (const p of parents) p.normalize();
    f.marks = []; f.idx = -1; f.capped = false;
  }

  function observe(on) {
    if (f.obs) { f.obs.disconnect(); f.obs = null; }
    if (!on || !f.sc || !f.sc.root || typeof MutationObserver === "undefined") return;
    // the view re-rendered (another file opened, the plan refreshed): find again.
    f.obs = new MutationObserver(() => { clearTimeout(f.timer); f.timer = setTimeout(() => search(true, true), 60); });
    f.obs.observe(f.sc.root, { childList: true, subtree: true });
  }

  function search(keepIdx, quiet) {
    const before = f.idx;
    observe(false);
    clearMarks();
    f.sc = scope();
    scopeEl.textContent = f.sc ? "in " + f.sc.label : "";
    input.classList.remove("bad");
    const q = input.value;
    let re = null;
    try { re = compile(q, { caseSensitive: f.caseSensitive, regex: f.regex }); }
    catch (e) { input.classList.add("bad"); count.textContent = "bad pattern"; count.title = String(e.message || e); return; }
    count.title = "";
    if (!re || !f.sc || !f.sc.root) { count.textContent = ""; observe(true); return; }
    for (const node of textNodes(f.sc)) {
      const hits = spans(node.data, re, MAX_MARKS - f.marks.length);
      const made = [];
      for (let i = hits.length - 1; i >= 0; i--) {       // from the end: earlier offsets stay valid
        const [a, b] = hits[i];
        node.splitText(b);
        const mid = node.splitText(a);
        const mark = document.createElement("mark");
        mark.className = "fm";
        mid.parentNode.replaceChild(mark, mid);
        mark.append(mid);
        made.push(mark);
      }
      made.reverse();
      f.marks.push(...made);
      if (f.marks.length >= MAX_MARKS) { f.capped = true; break; }
    }
    f.idx = f.marks.length ? (keepIdx && before >= 0 ? Math.min(before, f.marks.length - 1) : firstVisible()) : -1;
    focusMark(quiet);
    observe(true);
  }

  // Start from the first match at or below the top of the view — find continues where you read.
  function firstVisible() {
    for (let i = 0; i < f.marks.length; i++) {
      const r = f.marks[i].getBoundingClientRect();
      if (r.bottom >= 0) return i;
    }
    return 0;
  }

  function focusMark(quiet) {
    for (const m of f.marks) m.classList.remove("cur");
    const m = f.marks[f.idx];
    if (m) { m.classList.add("cur"); if (!quiet) m.scrollIntoView({ block: "center" }); }
    const n = f.marks.length;
    count.textContent = !input.value ? "" : n ? `${f.idx + 1} of ${n}${f.capped ? "+" : ""}` : "no matches";
  }

  function step(delta) {
    if (!f.open) { openFind(); return; }
    if (!f.marks.length || !document.contains(f.marks[0])) { search(false); if (!f.marks.length) return; }
    f.idx = (f.idx + delta + f.marks.length) % f.marks.length;
    focusMark();
  }

  function openFind() {
    const sel = window.getSelection ? String(window.getSelection() || "") : "";
    f.open = true;
    bar.hidden = false;
    if (sel && sel.length <= 200 && !/\n/.test(sel)) input.value = f.regex ? escapeRe(sel) : sel;
    input.focus();
    input.select();
    search(false);
  }
  function closeFind() {
    f.open = false;
    observe(false);
    clearMarks();
    bar.hidden = true;
    count.textContent = "";
  }
  MK.openFind = openFind;
  // a mode change shows another pane: find there.
  MK.onModeFind = () => { if (f.open) setTimeout(() => search(false), 0); };

  // --------------------------------------------------------------------------- the bar
  function btn(text, title, cls) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "fb-btn" + (cls ? " " + cls : ""); b.textContent = text;
    b.title = title; b.setAttribute("aria-label", title);
    return b;
  }
  function toggle(b, key) {
    b.setAttribute("aria-pressed", "false");
    b.onclick = () => { f[key] = !f[key]; b.setAttribute("aria-pressed", String(f[key])); search(false); input.focus(); };
  }

  MK.wireFind = function wireFind() {
    bar = document.createElement("div");
    bar.id = "find-bar"; bar.className = "find-bar"; bar.hidden = true;
    bar.setAttribute("role", "search");
    input = document.createElement("input");
    input.id = "find-input"; input.autocomplete = "off"; input.spellcheck = false;
    input.placeholder = "Find"; input.setAttribute("aria-label", "Find in this view");
    bCase = btn("Aa", "Match case"); bRe = btn(".*", "Regular expression");
    toggle(bCase, "caseSensitive"); toggle(bRe, "regex");
    count = document.createElement("span"); count.className = "fb-count small dim"; count.setAttribute("aria-live", "polite");
    scopeEl = document.createElement("span"); scopeEl.className = "fb-scope small dim";
    const prev = btn("↑", "Previous match (⇧↵)"), next = btn("↓", "Next match (↵)"), x = btn("×", "Close (Esc)");
    prev.onclick = () => step(-1); next.onclick = () => step(1); x.onclick = closeFind;
    bar.append(input, bCase, bRe, count, scopeEl, prev, next, x);
    const center = document.querySelector("main.center");
    (center || document.body).append(bar);
    input.oninput = () => { clearTimeout(f.timer); f.timer = setTimeout(() => search(false), 80); };
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") { ev.preventDefault(); closeFind(); }
      else if (ev.key === "Enter") { ev.preventDefault(); step(ev.shiftKey ? -1 : 1); }
    });
  };

  window.mokataIDE.register({
    id: "find",
    commands: [
      { id: "find.open", title: "Find in this view…", keys: "Mod+F", inFields: true,
        run: () => { if (f.open && document.activeElement === input) { input.select(); return; } openFind(); } },
      { id: "find.next", title: "Find: next match", keys: "F3", run: () => step(1) },
      { id: "find.prev", title: "Find: previous match", keys: "Shift+F3", run: () => step(-1) },
    ],
  });
})();
