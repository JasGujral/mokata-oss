// mokata ide — the shell, the changed-files navigator, Review mode and ⌘P.
// Vanilla JS, no build step (ADR doc 85 §1b).
// SAFETY: every piece of repo text goes into the page through textContent, never as markup —
// the code under review can contain markup, and a review window must not execute it.
"use strict";

const MK = (window.MK = window.MK || {});
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
};
MK.$ = $;
MK.el = el;

const ui = (MK.ui = {
  focus: null, view: "live", picked: [], pickedPaths: {}, sample: false, state: null, first: true,
  mode: "review", panel: "changes", diff: null, files: [], sel: 0, filter: "all",
  impact: {}, outlines: {}, hunkIdx: -1, inspSym: null,
});

// ------------------------------------------------------------------------------- plumbing
async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  return r.json();
}
MK.api = api;

function markSample(payload) {
  if (payload && payload.sample) { ui.sample = true; $("sample-banner").hidden = false; }
}
MK.markSample = markSample;

function shortOf(sid) {
  const row = ((ui.state && ui.state.sessions) || []).find((s) => s.id === sid);
  return row ? row.short : String(sid || "").slice(0, 8);
}

function wrapLi(child) { const li = el("li"); li.append(child); return li; }
MK.wrapLi = wrapLi;

function splitPath(p) {
  const i = p.lastIndexOf("/");
  return i < 0 ? ["", p] : [p.slice(0, i + 1), p.slice(i + 1)];
}
MK.splitPath = splitPath;

const ST_LETTER = { added: "A", deleted: "D", renamed: "R", modified: "M" };
MK.ST_LETTER = ST_LETTER;

// A basis badge: solid = structural (the graph answered), dashed = lexical (a name match).
function basisBadge(basis) {
  const b = basis || "lexical";
  const structural = b === "structural" || b === "verified_empty";
  const s = el("span", "basis " + (structural ? "structural" : "lexical"),
               b === "verified_empty" ? "verified empty" : b);
  s.title = structural ? "structural — the graph holds this edge"
                       : "lexical — matched by name only; the graph has no edge here";
  return s;
}
MK.basisBadge = basisBadge;

// Keystrokes typed into a field are TEXT, never commands.
function typing(ev) {
  const t = ev.target;
  if (!t || t === document.body) return false;
  const tag = (t.tagName || "").toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || t.isContentEditable;
}
MK.typing = typing;

// ------------------------------------------------------------------------------- seen marks
// "Unseen since last look" — per session (or "all"), per diff source, keyed by a signature of
// the file's diff so a file that changes AGAIN is unseen again. localStorage can be absent or
// throw (private windows, blocked storage); the navigator works without it.
function seenKey() {
  const repo = (ui.state && ui.state.repo) || "";
  return `mokata-ide:seen:${repo}:${ui.focus || "all"}:${ui.view}`;
}
function loadSeen() {
  try { return JSON.parse(window.localStorage.getItem(seenKey()) || "{}") || {}; } catch (e) { return {}; }
}
function saveSeen(map) {
  try { window.localStorage.setItem(seenKey(), JSON.stringify(map)); } catch (e) { /* no storage */ }
}
function sigOf(f) {
  let h = 0;
  const s = [f.path, f.status, f.added, f.removed, ...(f.hunks || []).map((x) => x.header)].join("|");
  for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return String(h);
}
function isSeen(f) { return loadSeen()[f.path] === sigOf(f); }
function markSeenFile(f) { const m = loadSeen(); m[f.path] = sigOf(f); saveSeen(m); }

// ------------------------------------------------------------------------------- restore
// px0 parity (doc 109 §13, 0.0.23 C4): reopening the window puts you back where you were — the
// mode, the left panel, and the file + line + symbol open in Navigate. Per repository; the same
// throw-safe storage rule as the seen marks (absent storage = a fresh start, never an error).
function viewKey() { return `mokata-ide:view:${(ui.state && ui.state.repo) || ""}`; }
function saveView() {
  if (ui.restoring || !ui.state) return;
  const n = MK.nav || {};
  try {
    window.localStorage.setItem(viewKey(), JSON.stringify({ mode: ui.mode, panel: ui.panel,
      path: n.path || null, line: n.line || 1, sym: n.sym || null }));
  } catch (e) { /* no storage: nothing to restore next time */ }
}
MK.saveView = saveView;
function loadView() {
  try { return JSON.parse(window.localStorage.getItem(viewKey()) || "null"); } catch (e) { return null; }
}
MK.restoreView = function restoreView() {
  const v = loadView();
  if (!v) return;
  ui.restoring = true;
  try {
    if (v.mode === "navigate" && v.path && MK.openFile) MK.openFile(v.path, v.line || 1, v.sym || null);
    else if (MODES.includes(v.mode)) setMode(v.mode);
    if (PANELS.includes(v.panel)) setPanel(v.panel, true);
  } finally { ui.restoring = false; }
};

// ------------------------------------------------------------------------------- modes / panels
const MODES = ["review", "navigate", "search", "graph", "plan"];
const PANELS = ["changes", "files", "search", "sessions", "graph", "plan"];
MK.MODES = MODES;

function setMode(mode) {
  ui.mode = mode;
  for (const b of document.querySelectorAll("button.mode")) b.classList.toggle("active", b.dataset.mode === mode);
  for (const id of MODES) $("view-" + id).hidden = id !== mode;
  $("insp-review").hidden = mode !== "review";
  $("insp-nav").hidden = mode !== "navigate";
  document.body.dataset.mode = mode;
  saveView();
  if (mode === "review" && ui.panel !== "changes" && ui.panel !== "sessions") setPanel("changes", true);
  if (mode === "navigate" && ui.panel !== "files") setPanel("files", true);
  if (mode === "plan" && ui.panel !== "plan") setPanel("plan", true);
  if (MK.onMode) MK.onMode(mode);
}
MK.setMode = setMode;

function setPanel(panel, quiet) {
  ui.panel = panel;
  for (const b of document.querySelectorAll("button.rail-btn")) b.classList.toggle("active", b.dataset.panel === panel);
  for (const id of PANELS) $("panel-" + id).hidden = id !== panel;
  saveView();
  if (quiet) return;
  if (panel === "files" && ui.mode !== "navigate") setMode("navigate");
  if (panel === "changes" && ui.mode !== "review") setMode("review");
  if (panel === "search") setMode("search");
  if (panel === "graph") setMode("graph");
  if (panel === "plan") setMode("plan");
}
MK.setPanel = setPanel;

// ------------------------------------------------------------------------------- sessions
function renderSessions(state) {
  const ul = $("sessions");
  ul.replaceChildren();
  const all = el("button", "item" + (ui.focus ? "" : " active"));
  all.type = "button";
  all.append(el("span", "", "All sessions"), el("span", "sub", "live diff of the whole tree"));
  all.onclick = () => setFocus(null);
  ul.append(wrapLi(all));
  for (const s of state.sessions || []) {
    const b = el("button", "item" + (ui.focus === s.id ? " active" : ""));
    b.type = "button";
    b.append(el("span", "", `session ${s.short}${s.claim === "bound" ? " · run " + s.run : ""}`));
    const files = (s.files || []).length;
    b.append(el("span", "sub", `${files} file(s) · ${s.writes} write(s) · ${s.last_iso || ""}`));
    b.onclick = () => setFocus(s.id);
    ul.append(wrapLi(b));
  }
  $("sessions-empty").hidden = (state.sessions || []).length > 0;
  // the same sessions, as the searchable chooser (C5): type a session id, a run, a file count
  MK.combo("session-pick").setOptions([{ value: "", label: "All sessions", sub: "live diff of the whole tree" },
    ...(state.sessions || []).map((s) => ({ value: s.id, label: `session ${s.short}${s.run ? " · run " + String(s.run).slice(0, 8) : ""}`,
      sub: `${(s.files || []).length} file(s) · ${s.writes} write(s) · ${s.last_iso || ""}` }))], ui.focus || "");
  $("view-session").disabled = !ui.focus;
  $("focus-chip").textContent = ui.focus ? `session ${shortOf(ui.focus)}` : "all sessions";
}

async function setFocus(sid) {
  ui.focus = sid;
  ui.picked = [];
  await api("/api/focus", { session: sid });
  ui.view = sid ? "session" : "live";
  await refresh();
}

// ------------------------------------------------------------------------------- timeline
async function renderTimeline() {
  const data = await api("/api/timeline" + (ui.focus ? "?session=" + encodeURIComponent(ui.focus) : ""));
  markSample(data);
  const ul = $("timeline");
  ul.replaceChildren();
  const entries = (data.entries || []).slice().reverse();
  for (const e of entries) {
    const b = el("button", "item" + (ui.picked.includes(e.id) ? " sel" : ""));
    b.type = "button";
    const when = new Date(e.t * 1000).toLocaleTimeString();
    b.append(el("span", "", `${when}  ${e.kind === "before" ? "before" : "write"}  ${e.path}`));
    b.append(el("span", "sub", `session ${shortOf(e.session)}${e.state !== "ok" ? " · " + e.state : ""}`));
    b.onclick = () => pick(e);
    ul.append(wrapLi(b));
  }
  $("timeline-empty").hidden = entries.length > 0;
  renderTicks(data.entries || []);
}

function renderTicks(entries) {
  const box = $("ticks");
  box.replaceChildren(el("div", "tick-line"));
  if (!entries.length) { $("ticks-label").textContent = "no writes recorded"; return; }
  const t0 = entries[0].t, t1 = entries[entries.length - 1].t;
  const span = Math.max(1, t1 - t0);
  const shown = entries.slice(-300);
  shown.forEach((e, i) => {
    const b = el("button", "tick" + (ui.picked.includes(e.id) ? " sel" : "") + (i === shown.length - 1 ? " last" : ""));
    b.type = "button";
    b.style.left = `${((e.t - t0) / span) * 98 + 1}%`;
    b.title = `${new Date(e.t * 1000).toLocaleTimeString()} ${e.kind} ${e.path}`;
    b.setAttribute("aria-label", b.title);
    b.onclick = () => pick(e);
    box.append(b);
  });
  const fmt = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  $("ticks-label").textContent = `${entries.length} writes · ${fmt(t0)} → ${fmt(t1)}`;
}

async function pick(e) {
  ui.picked = ui.picked.includes(e.id) ? ui.picked.filter((x) => x !== e.id) : [...ui.picked, e.id].slice(-2);
  ui.view = "between";
  ui.pickedPaths[e.id] = e.path;
  setMode("review");
  await refresh();
}

// ------------------------------------------------------------------------------- compare / PR
function renderBranches(state) {
  const branches = state.branches || [];
  for (const id of ["base", "head"]) MK.combo(id).setOptions(branches);
  if (branches.length > 1 && !$("head").dataset.touched) $("head").value = branches[1];
  $("compare-empty").hidden = branches.length > 1;
}

const SRC_LABEL = {
  live: "working tree vs HEAD", session: "this session's writes", between: "two moments",
  compare: "branch…branch", pr: "pull request",
};

// ------------------------------------------------------------------------------- the diff
async function loadDiff() {
  if (ui.view === "session" && ui.focus) return api("/api/session?id=" + encodeURIComponent(ui.focus));
  if (ui.view === "between" && ui.picked.length) {
    const [a, b] = ui.picked.length === 2 ? ui.picked : [ui.picked[0], "work"];
    return api(`/api/between?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
  }
  if (ui.view === "compare") return api(`/api/compare?base=${encodeURIComponent($("base").value)}&head=${encodeURIComponent($("head").value)}`);
  if (ui.view === "pr") return api("/api/pr", { url: $("pr-url").value });
  return api("/api/live");
}

const EMPTY_WHY = {
  live: "The working tree matches HEAD. Changes appear here live as soon as a session (or you) edits a file.",
  session: "This session has not changed any file yet — its writes will show here as it makes them.",
  between: "These two points hold the same content.",
  compare: "The two branches have no differences.",
  pr: "This pull request changes nothing.",
};

function acceptDiff(d) {
  markSample(d);
  const prevPath = ui.files[ui.sel] && ui.files[ui.sel].path;
  ui.diff = d;
  ui.files = d.files || [];
  const keep = ui.files.findIndex((f) => f.path === prevPath);
  ui.sel = keep >= 0 ? keep : 0;
  ui.hunkIdx = -1;
  $("diff-title").textContent = d.title || "Changes";
  $("diff-stats").textContent = d.files ? `${d.files.length} file(s) · +${d.added} −${d.removed}` : "";
  const note = $("diff-note");
  note.hidden = !d.note;
  note.textContent = d.note || "";
  if (d.error) { note.hidden = false; note.textContent = `could not read: ${d.error} ${d.note || ""}`; }
  $("diff-empty").hidden = !(ui.files.length === 0 && !d.note && !d.error);
  $("diff-empty-why").textContent = EMPTY_WHY[ui.view] || EMPTY_WHY.live;
  $("src-label").textContent = SRC_LABEL[ui.view] || SRC_LABEL.live;
  $("nav-add").textContent = `+${d.added || 0}`;
  $("nav-del").textContent = `−${d.removed || 0}`;
  const badge = $("rail-count");
  badge.hidden = !ui.files.length;
  badge.textContent = String(ui.files.length);
  loadImpacts();
  renderNavigator();
  showFile(ui.sel, { keepScroll: true });
}

// ------------------------------------------------------------------------------- the navigator
function visibleFiles() {
  const f = ui.filter;
  return ui.files.map((x, i) => ({ x, i })).filter(({ x }) => {
    if (f === "unseen") return !isSeen(x);
    if (f === "untested") { const im = ui.impact[x.path]; return !!(im && im.status === "untested"); }
    if (f === "tests") { const im = ui.impact[x.path]; return im ? im.is_test : /(^|\/)tests?\//.test(x.path); }
    return true;
  });
}
MK.visibleFiles = visibleFiles;

function renderNavigator() {
  const ul = $("nav-list");
  ul.replaceChildren();
  const vis = visibleFiles();
  const unseen = ui.files.filter((f) => !isSeen(f)).length;
  $("nav-count").textContent = `${ui.files.length} files · ${unseen} unseen`;
  for (const b of document.querySelectorAll("button.pill[data-filter]")) {
    b.classList.toggle("active", b.dataset.filter === ui.filter);
  }
  let lastDir = null;
  for (const { x, i } of vis) {
    const [dir, name] = splitPath(x.path);
    if (dir !== lastDir) { ul.append(el("li", "nav-dir mono", dir || "./")); lastDir = dir; }
    const seen = isSeen(x);
    const b = el("button", "nav-row" + (i === ui.sel ? " active" : "") + (seen ? " seen" : ""));
    b.type = "button";
    const dot = el("span", "seen-dot" + (seen ? " seen" : ""));
    dot.title = seen ? "seen" : "not seen yet";
    const letter = ST_LETTER[x.status] || "M";
    b.append(dot, el("span", "st st-" + letter, letter));
    const nm = el("span", "nm mono", x.old_path ? `${x.old_path} → ${name}` : name);
    nm.title = x.path;
    b.append(nm);
    const im = ui.impact[x.path];
    if (im && im.status === "untested") {
      const r = el("span", "risk", "untested");
      r.title = "changed code no test reaches (structural answer)";
      b.append(r);
    }
    b.append(el("span", "n-add mono small", `+${x.added}`), el("span", "n-del mono small", `−${x.removed}`));
    b.onclick = () => { showFile(i); setMode("review"); };
    ul.append(wrapLi(b));
  }
  const empty = $("nav-empty");
  empty.hidden = vis.length > 0 || ui.files.length === 0;
  const pending = ui.files.filter((f) => !ui.impact[f.path]).length;
  empty.textContent = ui.filter === "untested" && pending
    ? `Checking ${pending} file(s) against the graph…`
    : `No file matches “${ui.filter}”.`;
  const pos = vis.findIndex((o) => o.i === ui.sel);
  $("pos-label").textContent = ui.files.length ? `${pos >= 0 ? pos + 1 : "–"} of ${vis.length}` : "";
}
MK.renderNavigator = renderNavigator;

// The current file — ONE at a time, its hunks, each hunk naming the symbol it sits in.
function showFile(i, opts) {
  const o = opts || {};
  const next = Math.max(0, Math.min(i, ui.files.length - 1));
  if (next !== ui.sel) ui.inspSym = null;
  ui.sel = next;
  ui.hunkIdx = -1;
  const box = $("diff");
  box.replaceChildren();
  const f = ui.files[ui.sel];
  for (const id of ["cur-dir", "cur-name", "cur-status", "hunk-count"]) $(id).textContent = "";
  $("mark-seen").disabled = !f;
  $("open-nav").disabled = !f || f.status === "deleted";
  if (!f) { renderNavigator(); renderInspector(); return; }
  const [dir, name] = splitPath(f.path);
  $("cur-dir").textContent = f.old_path ? `${f.old_path} → ${dir}` : dir;
  $("cur-name").textContent = name;
  const st = $("cur-status");
  st.textContent = f.status;
  st.className = "st-badge st-" + (ST_LETTER[f.status] || "M");
  $("hunk-count").textContent = `${(f.hunks || []).length} hunk(s)`;
  box.append(renderFile(f));
  if (!o.keepScroll) $("review-scroll").scrollTop = 0;
  renderNavigator();
  renderInspector();
  ensureOutline(f);
}
MK.showFile = showFile;

function renderFile(f) {
  const wrap = el("div", "file-body");
  if (f.binary) { wrap.append(el("div", "bin", "binary or too large to show")); return wrap; }
  const im = ui.impact[f.path];
  (f.hunks || []).forEach((h, k) => {
    const sec = el("section", "hunk");
    sec.dataset.k = String(k);
    const head = el("div", "hunk-head mono");
    head.append(el("span", "", h.header));
    const sym = hunkSymbol(f, h);
    if (sym) { const s = el("span", "in-sym"); s.append("in ", el("b", "", sym)); head.append(s); }
    head.append(el("span", "spacer"));
    const verdict = hunkVerdict(im, sym, f);
    if (verdict) head.append(el("span", "hunk-badge " + verdict.cls, verdict.text));
    sec.append(head);
    const t = el("table", "d");
    for (const [op, o, n, text] of h.lines) {
      const tr = el("tr", op === "+" ? "add" : op === "-" ? "del" : "");
      tr.append(el("td", "no", o ?? ""), el("td", "no", n ?? ""), el("td", "tx", (op === " " ? " " : op) + text));
      t.append(tr);
    }
    sec.append(t);
    wrap.append(sec);
  });
  return wrap;
}

function hunkRange(h) {
  const m = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/.exec(h.header || "");
  if (!m) return null;
  const start = +m[1], len = m[2] === undefined ? 1 : +m[2];
  return [Math.max(1, start), Math.max(start, start + len - 1)];
}
MK.hunkRange = hunkRange;

// The NEW-side lines a hunk actually changes — its context lines are not changes. A removal is
// placed at the new-side line it used to precede, so a deletion still names its symbol.
function hunkChanged(h) {
  const r = hunkRange(h);
  let cur = r ? r[0] - 1 : 0;
  const out = [];
  for (const [op, , n] of h.lines || []) {
    if (op === "+") { out.push(n); cur = n; }
    else if (op === "-") out.push(Math.max(1, cur + 1));
    else if (n) cur = n;
  }
  return out;
}
MK.hunkChanged = hunkChanged;

function changedLines(f) {
  const lines = [...new Set((f.hunks || []).flatMap(hunkChanged))].sort((a, b) => a - b);
  const ranges = [];
  for (const n of lines) {
    const last = ranges[ranges.length - 1];
    if (last && n <= last[1] + 1) last[1] = n; else ranges.push([n, n]);
  }
  return ranges.map(([a, b]) => `${a}-${b}`).join(",");
}

function hunkSymbol(f, h) {
  // Only on a working-tree view is the file on disk the "new" side of the hunk.
  if (ui.view !== "live" || ui.sample) return null;
  const ol = ui.outlines[f.path];
  const first = hunkChanged(h)[0];
  if (!ol || !first) return null;
  // The innermost definition holding the hunk's FIRST changed line (not its context).
  let best = null;
  for (const s of ol.symbols || []) {
    const end = s.end || 1e9;
    if (s.line <= first && first <= end && (!best || s.line >= best.line)) best = s;
  }
  return best ? best.name : null;
}

function hunkVerdict(im, sym, f) {
  if (!im) return null;
  if (im.is_test) return { cls: "", text: "test file" };
  const s = (im.symbols || []).find((x) => x.name === sym);
  if (!s) return null;
  if (s.verdict === "covered") return { cls: "", text: `covered · ${s.tests.length} test(s)` };
  if (s.verdict === "untested") return { cls: "risk", text: "untested change" };
  return { cls: "lex", text: "coverage unknown (lexical)" };
}

async function ensureOutline(f) {
  if (ui.view !== "live" || ui.sample || f.status === "deleted" || ui.outlines[f.path]) return;
  const ol = await api("/api/outline?path=" + encodeURIComponent(f.path));
  if (ol && !ol.error) {
    ui.outlines[f.path] = ol;
    if (ui.files[ui.sel] === f) { const box = $("diff"); box.replaceChildren(renderFile(f)); }
  }
}

// Impact (callers, covering tests, untested) for every changed file — at most two at a time.
let impactGen = 0;
async function loadImpacts() {
  const gen = ++impactGen;
  if (ui.view !== "live" || ui.sample) {
    // Only the working tree is what the graph indexes; another source's lines are not on disk.
    for (const f of ui.files) if (!ui.impact[f.path]) ui.impact[f.path] = { status: "unknown", is_test: /(^|\/)tests?\//.test(f.path), symbols: [], note: "impact is computed for the working-tree view" };
    return;
  }
  const queue = ui.files.filter((f) => f.status !== "deleted" && !f.binary);
  const worker = async () => {
    while (queue.length && gen === impactGen) {
      const f = queue.shift();
      const im = await api(`/api/impact?path=${encodeURIComponent(f.path)}&lines=${encodeURIComponent(changedLines(f))}`).catch(() => null);
      if (gen !== impactGen) return;
      ui.impact[f.path] = im && !im.error ? im : { status: "unknown", symbols: [], is_test: false, note: (im && im.note) || "impact unavailable" };
      renderNavigator();
      if (ui.files[ui.sel] === f) { $("diff").replaceChildren(renderFile(f)); renderInspector(); }
    }
  };
  await Promise.all([worker(), worker()]);
}

// ------------------------------------------------------------------------------- inspector
function refItem(r, basis) {
  const li = el("li", "ref");
  const a = el("button", "link mono", `${r.symbol ? r.symbol + " · " : ""}${r.path}:${r.line}`);
  a.type = "button";
  a.onclick = () => MK.openFile && MK.openFile(r.path, r.line, r.symbol || null);
  li.append(a, basisBadge(basis));
  return li;
}
MK.refItem = refItem;

function renderInspector() {
  const f = ui.files[ui.sel];
  const im = f ? ui.impact[f.path] : null;
  for (const id of ["insp-callers", "insp-tests", "insp-blast", "insp-chips"]) $(id).replaceChildren();
  const pickSel = $("insp-pick");
  $("insp-notests").hidden = true;
  $("insp-ripple").disabled = true;
  if (!f) { $("insp-sym").textContent = "no file selected"; pickSel.hidden = true; return; }
  if (!im) { $("insp-sym").textContent = "asking the graph…"; pickSel.hidden = true; return; }
  const syms = im.symbols || [];
  if (!syms.length) {
    $("insp-sym").textContent = im.is_test ? "a test file" : (im.note || "no changed symbol the graph can name");
    pickSel.hidden = true;
    return;
  }
  MK.combo(pickSel).setOptions(syms.map((s) => ({ value: s.name, label: s.name, sub: s.verdict || "" })));
  pickSel.hidden = syms.length < 2;
  const h = (f.hunks || [])[Math.max(0, ui.hunkIdx)];
  const atHunk = h ? hunkSymbol(f, h) : null;
  const cur = syms.find((s) => s.name === ui.inspSym) || syms.find((s) => s.name === atHunk) || syms[0];
  ui.inspSym = cur.name;
  pickSel.value = cur.name;
  $("insp-sym").textContent = cur.name;
  $("insp-ripple").disabled = false;
  const callers = (cur.callers && cur.callers.references) || [];
  $("insp-chips").append(el("span", "chip", `${callers.length} callers`),
                         el("span", "chip", `${cur.tests.length} tests`),
                         el("span", "chip", `${cur.blast.count} in blast radius`), basisBadge(cur.blast.basis));
  for (const r of callers) $("insp-callers").append(refItem(r, cur.callers.basis));
  if (!callers.length) $("insp-callers").append(el("li", "dim small", cur.callers.basis === "verified_empty" ? "nothing calls it (verified by the graph)" : "no callers found"));
  if (cur.verdict === "untested") {
    $("insp-notests").hidden = false;
    $("insp-notests").textContent = "No test reaches this change — the graph found no test on any path into it.";
  } else if (cur.verdict === "unknown") {
    $("insp-notests").hidden = false;
    $("insp-notests").textContent = "Coverage unknown — the graph could not answer structurally, so mokata does not claim either way.";
  }
  for (const t of cur.tests) $("insp-tests").append(el("li", "mono small", t));
  loadBlastList(cur.name);
}
MK.renderInspector = renderInspector;

async function loadBlastList(sym) {
  const res = await api(`/api/query?kind=blast_radius&target=${encodeURIComponent(sym)}&depth=2`).catch(() => null);
  if (!res || ui.inspSym !== sym) return;
  const ul = $("insp-blast");
  ul.replaceChildren();
  if (res.error || res.sample) { ul.append(el("li", "dim small", res.note || "unavailable")); return; }
  for (const r of (res.references || []).slice(0, 60)) ul.append(refItem(r, res.basis));
  if (!(res.references || []).length) ul.append(el("li", "dim small", res.basis === "verified_empty" ? "nothing reaches it (verified)" : "no answer"));
}

// ------------------------------------------------------------------------------- moving around
function moveFile(delta) {
  const vis = visibleFiles();
  if (!vis.length) return;
  let pos = vis.findIndex((o) => o.i === ui.sel);
  pos = pos < 0 ? 0 : (pos + delta + vis.length) % vis.length;
  showFile(vis[pos].i);
}
MK.moveFile = moveFile;

function moveHunk(delta) {
  const secs = [...document.querySelectorAll("#diff section.hunk")];
  if (!secs.length) return;
  ui.hunkIdx = Math.max(0, Math.min(secs.length - 1, ui.hunkIdx + delta));
  secs.forEach((s, k) => s.classList.toggle("cur", k === ui.hunkIdx));
  secs[ui.hunkIdx].scrollIntoView({ block: "start", behavior: "smooth" });
  const f = ui.files[ui.sel];
  const sym = f && f.hunks[ui.hunkIdx] ? hunkSymbol(f, f.hunks[ui.hunkIdx]) : null;
  if (sym && sym !== ui.inspSym) { ui.inspSym = sym; renderInspector(); }
}

function nextUnseen() {
  const n = ui.files.length;
  for (let k = 1; k <= n; k++) {
    const t = (ui.sel + k) % n;
    if (!isSeen(ui.files[t])) { showFile(t); return true; }
  }
  return false;
}

function markSeenNext() {
  const f = ui.files[ui.sel];
  if (!f) return;
  markSeenFile(f);
  if (!nextUnseen()) renderNavigator();
}

function openCurrentInNavigate() {
  const f = ui.files[ui.sel];
  if (!f || f.status === "deleted" || !MK.openFile) return;
  const r = f.hunks && f.hunks.length ? hunkRange(f.hunks[Math.max(0, ui.hunkIdx)]) : null;
  MK.openFile(f.path, r ? r[0] : 1, null);
}

// ------------------------------------------------------------------------------- refresh + live
async function refresh() {
  const state = await api("/api/state");
  ui.state = state;
  markSample(state);
  if (ui.focus === null && state.focus) ui.focus = state.focus;
  if (ui.first) { ui.first = false; if (ui.focus) ui.view = "session"; }   // open on the session that asked
  for (const b of document.querySelectorAll("button.view")) b.classList.toggle("active", b.dataset.view === ui.view);
  $("repo").textContent = state.repo || "";
  renderSessions(state);
  renderBranches(state);
  await renderTimeline();
  ui.impact = {};
  acceptDiff(await loadDiff());
  if (MK.onRefresh) MK.onRefresh();
}
MK.refresh = refresh;

function live() {
  const es = new EventSource("/api/events");
  es.addEventListener("changed", () => { $("live-dot").classList.add("on"); if (ui.view !== "pr") refresh(); });
  es.addEventListener("focus", (ev) => {
    const f = JSON.parse(ev.data).focus;
    if (f !== ui.focus) { ui.focus = f; ui.view = f ? "session" : "live"; refresh(); }
  });
  es.onerror = () => $("live-dot").classList.remove("on");
}
MK.live = live;

// ------------------------------------------------------------------------------- ⌘P
const qo = { open: false, changedOnly: false, rows: [], idx: 0, gen: 0, timer: 0 };

function openQuick() {
  if (qo.open) { qo.changedOnly = !qo.changedOnly; runQuick(); return; }
  qo.open = true; qo.changedOnly = false;
  $("qo").hidden = false;
  const inp = $("qo-input");
  inp.value = "";
  inp.focus();
  runQuick();
}
MK.openQuick = openQuick;
function closeQuick() { qo.open = false; $("qo").hidden = true; }

async function runQuick() {
  const gen = ++qo.gen;
  const q = $("qo-input").value;
  if (q.startsWith("#")) {
    renderQuick({ changed: [], files: [], symbols: [], text: q.slice(1) }, q);
    return;
  }
  const changed = ui.files.filter((f) => f.status !== "deleted").map((f) => f.path);
  const res = await api("/api/quickopen", { q, changed }).catch(() => null);
  if (gen !== qo.gen || !res) return;
  if (qo.changedOnly) { res.files = []; res.symbols = []; }
  renderQuick(res, q);
}

function hl(text, q) {
  // Highlight the matched subsequence — built from text nodes, never markup.
  const frag = document.createDocumentFragment();
  const ql = (q || "").replace(/^[@#]/, "").toLowerCase();
  let j = 0;
  for (const ch of text) {
    if (j < ql.length && ch.toLowerCase() === ql[j]) { frag.append(el("b", "m", ch)); j++; }
    else frag.append(document.createTextNode(ch));
  }
  return frag;
}

function renderQuick(res, q) {
  const box = $("qo-results");
  box.replaceChildren();
  qo.rows = [];
  const section = (title, note) => {
    const h = el("div", "qo-sec"); h.append(el("h2", "", title)); if (note) h.append(el("span", "dim small", note)); box.append(h);
    const ul = el("ul", "qo-list"); box.append(ul); return ul;
  };
  if (res.text !== undefined) {
    const ul = section("Text");
    addRow(ul, () => { const li = el("li", "qo-row"); li.append(el("span", "", `Search the repo for “${res.text}”`)); return li; },
           () => { closeQuick(); if (MK.openSearch) MK.openSearch(res.text); });
  }
  if ((res.changed || []).length) {
    const ul = section(ui.focus ? "Changed in this session" : "Changed", "always ranked first");
    for (const r of res.changed) {
      const f = ui.files.find((x) => x.path === r.path) || { status: "modified", added: 0, removed: 0 };
      const [dir, name] = splitPath(r.path);
      addRow(ul, () => {
        const li = el("li", "qo-row");
        const letter = ST_LETTER[f.status] || "M";
        li.append(el("span", "seen-dot" + (isSeen(f) ? " seen" : "")), el("span", "st st-" + letter, letter));
        const nm = el("span", "nm mono"); nm.append(hl(name, q), el("span", "dim small", "  " + dir)); li.append(nm);
        const im = ui.impact[r.path];
        if (im && im.status === "untested") li.append(el("span", "risk", "untested"));
        li.append(el("span", "n-add mono small", `+${f.added}`), el("span", "n-del mono small", `−${f.removed}`), el("span", "dim small", "↵ diff"));
        return li;
      }, (alt) => {
        closeQuick();
        if (alt && MK.openFile) { MK.openFile(r.path, 1, null); return; }
        const i = ui.files.findIndex((x) => x.path === r.path);
        setMode("review"); setPanel("changes", true); if (i >= 0) showFile(i);
      });
    }
  }
  if ((res.files || []).length) {
    const ul = section("Files");
    for (const r of res.files) {
      const [dir, name] = splitPath(r.path);
      addRow(ul, () => { const li = el("li", "qo-row"); const nm = el("span", "nm mono"); nm.append(hl(name, q), el("span", "dim small", "  " + dir)); li.append(nm); return li; },
             () => { closeQuick(); if (MK.openFile) MK.openFile(r.path, 1, null); });
    }
  }
  if ((res.symbols || []).length) {
    const ul = section("Symbols");
    for (const s of res.symbols) {
      addRow(ul, () => {
        const li = el("li", "qo-row");
        li.append(el("span", "kind", s.kind === "class" ? "cls" : "fn"));
        const nm = el("span", "nm mono"); nm.append(hl(s.name, q)); li.append(nm);
        li.append(el("span", "dim small mono", `${s.path}:${s.line}`), basisBadge(s.basis));
        return li;
      }, () => { closeQuick(); if (MK.openFile) MK.openFile(s.path, s.line, s.name); });
    }
  }
  if (!qo.rows.length) box.append(el("p", "empty", res.error ? `could not search: ${res.note || res.error}` : "No match."));
  qo.idx = 0;
  paintQuick();
  $("qo-matcher").textContent = res.matcher ? `matcher: ${res.matcher}${res.matcher === "python-floor" ? " (pure-Python floor — pip install rapidfuzz)" : ""}${qo.changedOnly ? " · changed only" : ""}` : "";
}

function addRow(ul, make, act) {
  const li = make();
  const k = qo.rows.length;
  li.onclick = (ev) => act(ev.altKey);
  li.onmousemove = () => { if (qo.idx !== k) { qo.idx = k; paintQuick(); } };
  qo.rows.push({ li, act });
  ul.append(li);
}
function paintQuick() {
  qo.rows.forEach((r, k) => r.li.classList.toggle("active", k === qo.idx));
  const cur = qo.rows[qo.idx];
  if (cur) cur.li.scrollIntoView({ block: "nearest" });
}

// ------------------------------------------------------------------------------- keyboard
function onKey(ev) {
  const mod = ev.metaKey || ev.ctrlKey;
  if (mod && (ev.key === "p" || ev.key === "P")) { ev.preventDefault(); openQuick(); return; }
  if (qo.open) {
    if (ev.key === "Escape") { ev.preventDefault(); closeQuick(); }
    else if (ev.key === "ArrowDown") { ev.preventDefault(); qo.idx = Math.min(qo.rows.length - 1, qo.idx + 1); paintQuick(); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); qo.idx = Math.max(0, qo.idx - 1); paintQuick(); }
    else if (ev.key === "Enter") { ev.preventDefault(); const r = qo.rows[qo.idx]; if (r) r.act(ev.altKey); }
    return;
  }
  if (typing(ev) || mod || ev.altKey && !(ev.key === "ArrowLeft" || ev.key === "ArrowRight")) return;
  if (ev.altKey && MK.navHistory) {
    if (ev.key === "ArrowLeft") { ev.preventDefault(); MK.navHistory(-1); }
    if (ev.key === "ArrowRight") { ev.preventDefault(); MK.navHistory(1); }
    return;
  }
  const inNav = ui.mode === "navigate";
  switch (ev.key) {
    case "]": ev.preventDefault(); if (inNav && MK.navChangedFile) MK.navChangedFile(1); else moveFile(1); break;
    case "[": ev.preventDefault(); if (inNav && MK.navChangedFile) MK.navChangedFile(-1); else moveFile(-1); break;
    case "j": ev.preventDefault(); if (inNav && MK.navChange) MK.navChange(1); else moveHunk(1); break;
    case "k": ev.preventDefault(); if (inNav && MK.navChange) MK.navChange(-1); else moveHunk(-1); break;
    case "u": ev.preventDefault(); setMode("review"); nextUnseen(); break;
    case "e": if (ui.mode === "review") { ev.preventDefault(); openCurrentInNavigate(); } break;
    default:
  }
}
MK.onKey = onKey;

// ------------------------------------------------------------------------------- wiring
MK.wireCore = function wireCore() {
  $("head").onchange = () => { $("head").dataset.touched = "1"; };
  $("compare-go").onclick = async () => { ui.view = "compare"; setMode("review"); await refresh(); };
  $("pr-go").onclick = async () => { ui.view = "pr"; setMode("review"); await refresh(); };
  for (const b of document.querySelectorAll("button.view")) {
    b.onclick = async () => { ui.view = b.dataset.view; setMode("review"); await refresh(); };
  }
  for (const b of document.querySelectorAll("button.mode")) b.onclick = () => setMode(b.dataset.mode);
  for (const b of document.querySelectorAll("button.rail-btn")) b.onclick = () => setPanel(b.dataset.panel);
  for (const b of document.querySelectorAll("button.pill[data-filter]")) {
    b.onclick = () => { ui.filter = b.dataset.filter; renderNavigator(); };
  }
  $("session-chip").onclick = () => setPanel("sessions", ui.mode !== "review" ? false : true);
  $("src-change").onclick = () => setPanel("sessions", true);
  $("file-prev").onclick = () => moveFile(-1);
  $("file-next").onclick = () => moveFile(1);
  $("mark-seen").onclick = markSeenNext;
  $("open-nav").onclick = openCurrentInNavigate;
  $("insp-pick").onchange = () => { ui.inspSym = $("insp-pick").value; renderInspector(); };
  $("session-pick").onchange = () => setFocus($("session-pick").value || null);
  $("insp-ripple").onclick = () => { if (MK.openGraph && ui.inspSym) MK.openGraph("ripple", ui.inspSym); };
  $("qo-open").onclick = openQuick;
  $("qo-input").oninput = () => { clearTimeout(qo.timer); qo.timer = setTimeout(runQuick, 70); };
  $("qo").onclick = (ev) => { if (ev.target === $("qo")) closeQuick(); };
  document.addEventListener("keydown", onKey);
};
