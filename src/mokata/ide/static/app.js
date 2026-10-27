// mokata ide — the shell, the changed-files navigator and Review mode (⌘P: features/palette.js).
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
  mode: "review", diff: null, files: [], sel: 0, filter: "all",
  impact: {}, outlines: {}, hunkIdx: -1, inspSym: null,
});

// ------------------------------------------------------------------------------- plumbing
// 0.0.26 P32 — this page's id: sent with every call, and on the event stream, so the server knows
// which jobs a page still wants (a job nobody wants is cancelled; index builds keep going).
MK.clientId = (window.crypto && crypto.randomUUID) ? crypto.randomUUID()
  : "p" + Math.random().toString(36).slice(2) + Date.now().toString(36);
// `opts.signal` aborts a superseded call (and leaves its job); a long answer comes back as a JOB
// (202 + handle) that MK.jobs.follow (features/activity.js) waits on — progress and partial
// answers go to `opts.onJob` / `opts.onPartial` — and the caller still gets the final answer.
async function api(path, body, opts) {
  const o = opts || {};
  const headers = { "X-Mokata-Client": MK.clientId };
  const opt = body === undefined ? { headers } : {
    method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(body) };
  if (o.signal) opt.signal = o.signal;
  const r = await fetch(path, opt);
  let data;
  try { data = await r.json(); } catch (_e) { data = null; }
  if (data === null || typeof data !== "object") {
    // 0.0.26 P34 (§5.4 rule 4): a proxy's 502/503 page, a dropped connection mid-body or an empty
    // answer is an ERROR the region names — never a thrown parse error that leaves it blank.
    return { error: "HTTP " + r.status, note: `the server answered ${r.status} without a readable body`, status: r.status };
  }
  if (r.status === 202 && data && data.job && MK.jobs) return MK.jobs.follow(data, path, body, o);
  if (r.status >= 500 && !data.error) data.error = "HTTP " + r.status;
  return data;
}
MK.api = api;

// §5.4 — one way for a region to say what it is doing: "loading" (a skeleton line IN the region),
// "empty" (why + the one next action) or "error" (what failed + Retry + the CLI twin). Never blank.
function regionSay(host, kind, text, opts) {
  const o = opts || {};
  if (!host) return null;
  const box = el(host.tagName === "UL" || host.tagName === "OL" ? "li" : "div", "region-msg " + kind);
  box.setAttribute("role", kind === "error" ? "alert" : "status");
  if (kind === "loading") box.append(el("span", "job-spin"));
  box.append(el("span", "", text));
  if (o.cli) { box.append(document.createTextNode(" · run "), el("code", "", o.cli)); }
  if (o.retry) {
    const b = el("button", "btn ghost small", "Retry");
    b.type = "button";
    b.onclick = o.retry;
    box.append(document.createTextNode(" "), b);
  }
  if (o.action) {
    const b = el("button", "link small", o.action[0]);
    b.type = "button";
    b.onclick = o.action[1];
    box.append(document.createTextNode(" "), b);
  }
  if (o.replace !== false) host.replaceChildren(box); else host.append(box);
  return box;
}
MK.regionSay = regionSay;
// What failed, in words, from an api() answer that has `error`.
MK.whyFailed = (d, what) => `could not ${what}: ${(d && (d.note || d.error)) || "no answer"}`;

function markSample(payload) {
  if (payload && payload.sample) { ui.sample = true; $("sample-banner").hidden = false; }
}
MK.markSample = markSample;

function shortOf(sid) {
  const row = ui.state && ui.state.focus_row;
  return row && row.id === sid ? row.short : String(sid || "").slice(0, 8);
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
MK.isSeen = isSeen;
function markSeenFile(f) { const m = loadSeen(); m[f.path] = sigOf(f); saveSeen(m); }

// ------------------------------------------------------------------------------- restore
// 0.0.26 P2 (ID-TABS): the window's restore is the SERVER's workspace — tabs, the active tab, its
// scroll, explorer dirs, mode and panel — owned by static/features/tabs.js, which defines
// MK.saveView / MK.restoreView. (The 0.0.23 C4 per-browser localStorage restore is gone.)
function saveView() { if (MK.saveView) MK.saveView(); }

// ------------------------------------------------------------------------------- screens
// 0.0.26 P34 (IA-ONE-NAV, R26) — ONE navigation: the rail IS the screen. A screen is a rail button
// (`data-screen`), a centre view (#view-<id>), optionally a side panel (#panel-<id>) and the
// inspector. `MK.setMode(id)` is the ONLY way to change screen: the rail highlight, the visible
// view, the side panel and body[data-mode] are all set from the same id, so they cannot disagree.
// (The id is `ui.mode` for historic reasons; there is no second "mode" any more — no mode bar, no
// left-panel switch.) A feature adds a screen through the registry (`panels: [{id, title, …}]`),
// which appends to MK.SCREENS. `chord` is the letter after `g` (§5.5).
const SCREENS = [
  { id: "sessions", title: "Sessions", chord: "s" },
  { id: "review", title: "Review", chord: "r" },
  { id: "navigate", title: "Code", chord: "c" },
  { id: "search", title: "Search", chord: null, keys: "⌘⇧F" },
  { id: "graph", title: "Impact", chord: "i" },
  { id: "plan", title: "Plan", chord: "p" },
];
MK.SCREENS = SCREENS;
const INSPECTOR = { review: "insp-review", navigate: "insp-nav" };

function setMode(id) {
  if (!SCREENS.some((s) => s.id === id)) return;
  const changedMode = ui.mode !== id;
  ui.mode = id;
  for (const b of document.querySelectorAll("nav.rail .rail-btn")) {
    const on = b.dataset.screen === id;
    b.classList.toggle("active", on);
    if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  }
  for (const s of SCREENS) {
    const v = $("view-" + s.id), p = $("panel-" + s.id);
    if (v) v.hidden = s.id !== id;
    if (p) p.hidden = s.id !== id;
  }
  for (const [sc, insp] of Object.entries(INSPECTOR)) $(insp).hidden = sc !== id;
  document.body.dataset.mode = id;
  document.body.dataset.side = $("panel-" + id) ? "1" : "0";
  document.body.dataset.insp = INSPECTOR[id] ? "1" : "0";
  saveView();
  if (MK.onMode) MK.onMode(id);
  if (changedMode && MK.refreshIfStale) MK.refreshIfStale();   // P35: views a delta did not refresh
}
MK.setMode = setMode;

// ------------------------------------------------------------------------------- viewing
// 0.0.26 P33 (R28) — ONE "Viewing" context: the session the window is focused on (or the
// uncommitted changes), the same for Review, the Code gutter (the server marks the file against
// it, `changes_against`) and — through `MK.viewing()` / `MK.onViewing` — every later view
// (P34 wires the search scope). The session LIST lives on the Sessions screen only (sessions.js).
function viewing() {
  const row = ui.state && ui.state.focus_row;
  const sess = ui.focus ? `session ${shortOf(ui.focus)}` : null;
  if (ui.view === "session" && ui.focus) {
    return { kind: "session", session: ui.focus, label: sess, title: row && row.prompt ? `${sess} · ${row.prompt}` : sess, row };
  }
  if (ui.view === "between") {
    const what = ui.picked.length === 2 ? "two moments" : "one write";
    return { kind: "write", session: ui.focus, label: sess ? `${sess} · ${what}` : what, title: what, row };
  }
  if (ui.view === "compare") {
    const lab = `${$("base").value || "?"}…${$("head").value || "?"}`;
    return { kind: "compare", session: null, label: lab, title: `branches ${lab}`, row: null };
  }
  if (ui.view === "pr") return { kind: "pr", session: null, label: "pull request", title: $("pr-url").value, row: null };
  if (ui.view === "worktree") return { kind: "worktree", session: ui.focus, label: "a session's worktree", title: "a session's worktree (Worktrees)", row };
  return { kind: "live", session: null, label: "uncommitted changes", title: "Uncommitted changes — working tree vs HEAD", row: null };
}
MK.viewing = viewing;
MK.onViewing = MK.onViewing || [];

// The files the Viewing session changed (R28 + P36's search scope "this session's files"):
// null when no session is in view, so the scope says why it is off.
MK.sessionFiles = () => {
  const v = viewing();
  if (!v.session || (v.kind !== "session" && v.kind !== "worktree")) return null;
  return ui.sessionPaths && ui.sessionPaths.sid === v.session ? ui.sessionPaths.paths.slice() : [];
};

function renderViewing() {
  const v = viewing();
  $("view-session").disabled = !ui.focus;
  $("view-session-sub").textContent = ui.focus ? `session ${shortOf(ui.focus)}` : "no session selected";
  for (const b of document.querySelectorAll("#viewing-pop button.view[data-view]")) b.classList.toggle("active", b.dataset.view === ui.view);
  const bt = $("viewing-between");
  bt.hidden = ui.view !== "between";
  bt.textContent = ui.view === "between" ? `Now: ${v.label} (picked on the Sessions screen).` : "";
  $("focus-chip").textContent = "Viewing: " + v.label;
  $("session-chip").title = v.title + " — click to change what Review, the Code gutter and Search's session scope show";
  const cv = $("code-viewing");
  if (cv) cv.textContent = v.kind === "live" ? "vs HEAD" : `vs ${v.label}`;
  for (const fn of MK.onViewing) { try { fn(v); } catch (e) { /* a view's hook never breaks the shell */ } }
}
MK.renderViewing = renderViewing;

function openViewing(open) {
  const p = $("viewing-pop");
  p.hidden = open === undefined ? !p.hidden : !open;
  $("session-chip").setAttribute("aria-expanded", String(!p.hidden));
  if (!p.hidden) renderViewing();
}
MK.openViewing = openViewing;

// Pick a source from Viewing ▾. A session is picked on the Sessions screen (one list); the others
// clear the session focus so the Code gutter and the search scope never disagree with Review.
async function pickView(view) {
  openViewing(false);
  if (view === "session") { if (ui.focus) { ui.view = "session"; await refresh(); } else setMode("sessions"); return; }
  ui.view = view;
  if (ui.focus) { ui.focus = null; ui.picked = []; await api("/api/focus", { session: null }); }
  if (!["review", "navigate", "search"].includes(ui.mode)) setMode("review");
  await refresh();
}
MK.pickView = pickView;

async function setFocus(sid) {
  ui.focus = sid;
  ui.picked = [];
  await api("/api/focus", { session: sid });
  ui.view = sid ? "session" : "live";
  await refresh();
}
MK.setFocus = setFocus;

// ------------------------------------------------------------------------------- one write / two moments
// The bottom timeline is gone (P34): a write is picked on the Sessions screen (Per write — click
// one to see it, shift-click a second to compare the two moments; or the Replay scrubber).
async function pick(e, add) {
  ui.picked = add ? (ui.picked.includes(e.id) ? ui.picked.filter((x) => x !== e.id) : [...ui.picked, e.id].slice(-2)) : [e.id];
  ui.view = "between";
  ui.pickedPaths[e.id] = e.path;
  setMode("review");
  await refresh();
}
MK.pickSnapshot = pick;

// ------------------------------------------------------------------------------- compare / PR
function renderBranches(state) {
  const branches = state.branches || [];
  // Viewing ▾ holds the branch pickers; a combo opened there keeps what was typed.
  for (const id of ["base", "head"]) MK.combo(id).setOptions(branches);
  if (branches.length > 1 && !$("head").dataset.touched) $("head").value = branches[1];
  $("compare-empty").hidden = branches.length > 1;
}

// ------------------------------------------------------------------------------- the diff
async function loadDiff() {
  if (ui.view === "session" && ui.focus) return api("/api/session?id=" + encodeURIComponent(ui.focus));
  if (ui.view === "between" && ui.picked.length) {
    // P33 (R9, B-F2): ONE pick = that write (the previous capture → this one); two = compare.
    if (ui.picked.length === 1) return api("/api/session/write?snap=" + encodeURIComponent(ui.picked[0]));
    return api(`/api/between?a=${encodeURIComponent(ui.picked[0])}&b=${encodeURIComponent(ui.picked[1])}`);
  }
  if (ui.view === "compare") return api(`/api/compare?base=${encodeURIComponent($("base").value)}&head=${encodeURIComponent($("head").value)}`);
  if (ui.view === "pr") return api("/api/pr", { url: $("pr-url").value });
  return api("/api/live" + (ui.showMokata ? "?mokata=1" : ""));
}

const EMPTY_WHY = {
  live: "The working tree matches HEAD. Changes appear here live as soon as a session (or you) edits a file.",
  session: "This session has not changed any file yet — its writes will show here as it makes them.",
  between: "This write changed nothing (or these two points hold the same content).",
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
  note.hidden = !d.note || !!d.error;
  note.textContent = d.note || "";
  $("diff-empty").hidden = !(ui.files.length === 0 && !d.note && !d.error);
  // G23 — the Live view hides the files mokata setup wrote, says how many, and shows them on ask.
  const hid = ui.view === "live" ? (d.hidden_mokata || 0) : 0;
  $("diff-mokata").hidden = !(hid || (ui.view === "live" && ui.showMokata));
  $("diff-mokata-n").textContent = ui.showMokata ? "Showing the files mokata setup wrote." : `${hid} file(s) mokata setup wrote are hidden.`;
  $("diff-mokata-btn").textContent = ui.showMokata ? "hide them" : "show them";
  $("diff-empty-why").textContent = EMPTY_WHY[ui.view] || EMPTY_WHY.live;
  $("nav-add").textContent = `+${d.added || 0}`;
  $("nav-del").textContent = `−${d.removed || 0}`;
  const badge = $("rail-count");
  badge.hidden = !ui.files.length;
  badge.textContent = String(ui.files.length);
  loadImpacts();
  renderNavigator();
  showFile(ui.sel, { keepScroll: true });
  if (d.error) {
    // §5.4 rule 4: what failed, Retry, and the CLI twin — in the region the user is looking at.
    $("diff-title").textContent = "Changes";
    regionSay($("diff"), "error", MK.whyFailed(d, `read ${viewing().label}`),
              { retry: () => refresh(), cli: ui.view === "live" ? "git diff HEAD" : null });
  }
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
  empty.hidden = vis.length > 0;
  const pending = ui.files.filter((f) => impactable(f) && !ui.impact[f.path]).length;
  empty.textContent = ui.diff && ui.diff.error ? "Could not read the changes — see the main view."
    : !ui.files.length ? `No changed file in ${viewing().label}.`
    : ui.filter === "untested" && pending
    ? `Checked ${ui.files.length - pending} of ${ui.files.length} file(s) against the graph…`
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
  wantImpact(f);
  if (MK.onDiffShown) MK.onDiffShown(f, o);       // tabs.js: the diff tab for this file
}
MK.showFile = showFile;

function renderFile(f) {
  const wrap = el("div", "file-body");
  if (f.too_large) {
    // R10 — changed, but over the snapshot cap: a row that says so, never a silent drop.
    const kb = f.size ? ` · ${(f.size / 1048576).toFixed(1)} MB` : "";
    wrap.append(el("div", "bin", `too large to diff${f.writes ? ` · ${f.writes} write(s)` : ""}${kb}`));
    return wrap;
  }
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
MK.renderFile = renderFile;

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

// Impact (callers, covering tests, untested) — lane S (the 1M-line scale run). It used to be asked
// for EVERY changed file on EVERY refresh; on a hub function one never finished and the page kept
// re-asking. Now: answers are cached by the file's FINGERPRINT (its diff), so a refresh that changed
// nothing about a file never asks again; the file in view is asked first, then at most IMPACT_AHEAD
// more in the background; the rest when they are opened (or when the "untested" filter asks for
// all). At most two in flight. A timed-out answer is kept (and shown) like any other — it is asked
// again only when its file changes, or when the file is opened again after IMPACT_RETRY_MS.
const IMPACT_AHEAD = 6;
const IMPACT_RETRY_MS = 60000;
const IMPACT_CACHE_MAX = 500;
const impactCache = new Map();      // path -> { fp, im, at }
const impactFlight = new Set();     // "path\0fp" asked and not yet answered
let impactQueue = [];
let impactRunning = 0;
let impactGen = 0;

function fileFp(f) {
  // A cheap content hash of the file's diff (FNV-1a over its hunks) + its counts.
  const s = `${f.status}|${f.added}|${f.removed}|${JSON.stringify(f.hunks || [])}`;
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  return `${s.length}:${h.toString(16)}`;
}
MK.fileFp = fileFp;

function impactable(f) { return f && f.status !== "deleted" && !f.binary; }

function loadImpacts(all) {
  const gen = ++impactGen;
  if (ui.view !== "live" || ui.sample) {
    // Only the working tree is what the graph indexes; another source's lines are not on disk.
    for (const f of ui.files) if (!ui.impact[f.path]) ui.impact[f.path] = { status: "unknown", is_test: /(^|\/)tests?\//.test(f.path), symbols: [], note: "impact is computed for the working-tree view" };
    impactQueue = [];
    return;
  }
  for (const f of ui.files) {
    const c = impactCache.get(f.path);
    if (c && c.fp === fileFp(f)) ui.impact[f.path] = c.im;
  }
  const order = new Set();
  const cur = ui.files[ui.sel];
  if (cur) order.add(cur);
  const vis = visibleFiles().map((o) => o.x);
  const at = Math.max(0, vis.indexOf(cur));
  const ahead = all || ui.filter === "untested" ? vis.concat(ui.files)
    : vis.slice(at + 1).concat(vis.slice(0, at)).slice(0, IMPACT_AHEAD);
  for (const f of ahead) order.add(f);
  impactQueue = [...order].filter((f) => impactable(f) && !ui.impact[f.path]);
  pumpImpacts(gen);
}
MK.loadImpacts = loadImpacts;

// The file the user opens is asked for at once (front of the queue), whatever the bound.
function wantImpact(f) {
  if (!impactable(f) || ui.view !== "live" || ui.sample) return;
  const c = impactCache.get(f.path);
  const fresh = c && c.fp === fileFp(f);
  if (fresh && !(c.im.failed && Date.now() - c.at > IMPACT_RETRY_MS)) return;
  if (fresh) { impactCache.delete(f.path); delete ui.impact[f.path]; }
  impactQueue = [f].concat(impactQueue.filter((x) => x !== f));
  pumpImpacts(impactGen);
}

function pumpImpacts(gen) {
  while (impactRunning < 2 && impactQueue.length && gen === impactGen) {
    const f = impactQueue.shift();
    const fp = fileFp(f);
    if (ui.impact[f.path] || impactFlight.has(f.path + "\0" + fp)) continue;
    impactRunning++;
    askImpact(f, fp).finally(() => { impactRunning--; pumpImpacts(impactGen); });
  }
}

async function askImpact(f, fp) {
  const key = f.path + "\0" + fp;
  impactFlight.add(key);
  const im = await api(`/api/impact?path=${encodeURIComponent(f.path)}&lines=${encodeURIComponent(changedLines(f))}`).catch(() => null);
  impactFlight.delete(key);
  const val = im && !im.error ? im : { status: "unknown", symbols: [], is_test: false, failed: true, note: (im && im.note) || "impact unavailable" };
  impactCache.delete(f.path);
  impactCache.set(f.path, { fp, im: val, at: Date.now() });
  while (impactCache.size > IMPACT_CACHE_MAX) impactCache.delete(impactCache.keys().next().value);
  if (val.graph_note && MK.noteGraph) MK.noteGraph(val.graph_note);
  const now = ui.files.find((x) => x.path === f.path);
  if (!now || fileFp(now) !== fp) return;          // the file moved on while we asked
  ui.impact[f.path] = val;
  renderNavigator();
  if (ui.files[ui.sel] === now) { $("diff").replaceChildren(renderFile(now)); renderInspector(); }
}

// The status bar names the graph that is ACTUALLY answering (lane S): an answer that says the
// adopted graph degraded updates it — no extra request.
MK.noteGraph = (note) => { const s = $("st-graph"); if (s && note) { s.textContent = note; s.classList.add("degraded"); } };

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
  // 0.0.25 — symbols the automatic pass did not calculate are `pending`, calculated on request.
  const syms = (im.symbols || []).concat((im.pending || []).map((p) => ({ ...p, pending: true, verdict: "not calculated" })));
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
  if (cur.pending) { renderPendingImpact(f, im, cur); return; }
  const callers = (cur.callers && cur.callers.references) || [];
  const nCallers = cur.callers.references_total ?? callers.length;
  const blastTxt = cur.blast.truncated
    ? `${cur.blast.count}${cur.blast.total ? " of " + cur.blast.total : "+"} in blast radius (cut)`
    : `${cur.blast.count} in blast radius`;
  $("insp-chips").append(el("span", "chip", `${nCallers} callers`),
                         el("span", "chip", `${cur.tests.length} tests`),
                         el("span", "chip" + (cur.blast.truncated ? " amber" : ""), blastTxt), basisBadge(cur.blast.basis));
  if ((im.pending || []).length) {
    // The cap is disclosed, and the rest is one click away (calculated on request).
    const c = el("span", "chip amber", `${(im.symbols || []).length} of ${im.symbols_changed} calculated`);
    c.title = im.cap_note || "";
    $("insp-chips").append(c, calcButton(f, im, im.pending.map((p) => p.name), `Calculate the other ${im.pending.length}`));
  }
  if (im.graph_note) { const c = el("span", "chip amber", "graph degraded"); c.title = im.graph_note; $("insp-chips").append(c); }
  for (const r of callers) $("insp-callers").append(refItem(r, cur.callers.basis));
  if (cur.callers.references_capped) $("insp-callers").append(el("li", "dim small", `showing ${callers.length} of ${nCallers} callers — open it in Focus or search its call sites for the rest`));
  if (!callers.length) $("insp-callers").append(el("li", "dim small", cur.callers.basis === "verified_empty" ? "nothing calls it (verified by the graph)" : "no callers found"));
  if (cur.verdict === "untested") {
    $("insp-notests").hidden = false;
    $("insp-notests").textContent = "No test reaches this change — the graph found no test on any path into it.";
  } else if (cur.verdict === "unknown" && cur.blast.not_determined) {
    $("insp-notests").hidden = false;
    $("insp-notests").textContent = `Coverage ${cur.blast.not_determined}.`;
    // 0.0.26 O7 — a radius the DEFAULT bound cut is one click from a full walk (on request only,
    // one symbol, hard-bounded server-side; leaving the page cancels it).
    if (cur.blast.full_available) {
      $("insp-notests").append(document.createTextNode(" "),
        calcButton(f, im, [cur.name], "Calculate the full blast radius", "full"));
    }
  } else if (cur.verdict === "unknown") {
    $("insp-notests").hidden = false;
    $("insp-notests").textContent = "Coverage unknown — the graph could not answer structurally, so mokata does not claim either way.";
  }
  for (const t of cur.tests) $("insp-tests").append(el("li", "mono small", t));
  loadBlastList(cur.name);
}
MK.renderInspector = renderInspector;

// ------------------------------------------------------------------------------- impact on request
function calcButton(f, im, names, label, budget) {
  const b = el("button", "btn ghost small", label);
  b.type = "button";
  if (budget === "full") b.title = "walks this symbol's whole blast radius — up to a minute; only when you ask";
  b.onclick = () => calculateImpact(f, im, names, b, budget);
  return b;
}

function renderPendingImpact(f, im, cur) {
  $("insp-chips").append(el("span", "chip amber", "not calculated yet"));
  const box = $("insp-notests");
  box.hidden = false;
  box.replaceChildren(document.createTextNode(
    `${cur.name}'s callers and tests were not calculated automatically (a file with many changed definitions gets the first ${(im.symbols || []).length}). `));
  box.append(calcButton(f, im, [cur.name], `Calculate ${cur.name}`));
  const rest = (im.pending || []).map((p) => p.name);
  if (rest.length > 1) box.append(document.createTextNode(" "), calcButton(f, im, rest, `Calculate all ${rest.length}`));
}

function impactStatus(im) {
  const v = new Set((im.symbols || []).map((s) => s.verdict));
  return v.has("untested") ? "untested" : v.has("unknown") ? "unknown" : (im.pending || []).length ? "partial" : "covered";
}

async function calculateImpact(f, im, names, btn, budget) {
  if (btn) { btn.disabled = true; btn.textContent = budget === "full" ? "walking the full blast radius…" : "calculating…"; }
  const want = budget === "full" ? names.slice(0, 1) : names.slice(0, 40);
  const extra = budget === "full" ? "&budget=full" : "";
  // P32: a long walk is a job — the button shows its progress in place (and the Activity chip has
  // its Cancel); no deadline, so it never answers "timed out".
  const onJob = (j) => { if (btn && MK.jobs) btn.textContent = MK.jobs.line(j); };
  const res = await api(`/api/impact?path=${encodeURIComponent(f.path)}&lines=${encodeURIComponent(changedLines(f))}&symbols=${encodeURIComponent(want.join(","))}${extra}`, undefined, { onJob }).catch(() => null);
  if (!res || res.error) {
    if (btn) { btn.disabled = false; btn.textContent = res && res.cancelled ? "cancelled — retry" : "failed — retry"; btn.title = (res && res.note) || ""; }
    return;
  }
  const got = new Set((res.symbols || []).map((s) => s.name));
  im.symbols = (im.symbols || []).filter((s) => !got.has(s.name)).concat(res.symbols || []);
  im.pending = (im.pending || []).filter((p) => !got.has(p.name));
  im.symbols_capped = im.pending.length > 0;
  im.status = impactStatus(im);
  if (ui.files[ui.sel] && ui.files[ui.sel].path === f.path) { renderNavigator(); $("diff").replaceChildren(renderFile(ui.files[ui.sel])); renderInspector(); }
}
MK.calculateImpact = calculateImpact;

async function loadBlastList(sym) {
  const ul = $("insp-blast");
  // P32 (§5.4): the loading state is IN the list the user is looking at, with the job's progress.
  ul.replaceChildren(el("li", "dim small job-inline", "asking the graph…"));
  const onJob = (j) => { if (ui.inspSym === sym && MK.jobs) ul.firstChild.textContent = MK.jobs.line(j); };
  const res = await api(`/api/query?kind=blast_radius&target=${encodeURIComponent(sym)}&depth=2`, undefined, { onJob }).catch(() => null);
  if (!res || ui.inspSym !== sym) return;
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
  $("repo").textContent = state.repo || "";
  renderBranches(state);
  ui.impact = {};
  // §5.4 rule 2: a read slower than 300 ms shows a skeleton IN the Review region.
  const slow = setTimeout(() => { if (!ui.files.length) regionSay($("diff"), "loading", `Reading ${viewing().label}…`); }, 300);
  const d = await loadDiff().catch((e) => ({ error: "unreachable", note: String(e && e.message || e) }));
  clearTimeout(slow);
  if (ui.view === "session" && ui.focus && !d.error) ui.sessionPaths = { sid: ui.focus, paths: (d.files || []).map((f) => f.path) };
  else if (ui.focus && (!ui.sessionPaths || ui.sessionPaths.sid !== ui.focus)) loadSessionPaths(ui.focus);
  renderViewing();
  acceptDiff(d);
  if (MK.onRefresh) MK.onRefresh();
}
MK.refresh = refresh;

// The Viewing session's files when Review shows another source (search scope, R28).
async function loadSessionPaths(sid) {
  const d = await api("/api/session?id=" + encodeURIComponent(sid)).catch(() => null);
  if (!d || d.error || ui.focus !== sid) return;
  ui.sessionPaths = { sid, paths: (d.files || []).map((f) => f.path) };
  renderViewing();
}

// ID.S3 follow mode: the server pushes each edit as a `bus` event ({kind, path, session}); with
// Follow on, the edited file opens (reloaded) in Navigate. Only the focused session is followed when
// one is focused. The path was contained server-side; it is only ever passed to the read API.
let follow = false;
let refreshTimer = 0;
function softRefresh() {
  if (ui.view === "pr" || refreshTimer) return;
  refreshTimer = setTimeout(() => { refreshTimer = 0; staleViews = false; refresh(); }, 250);
}
// 0.0.26 P35 (G16, audit B-F11): ONE edit used to trigger a full refresh — 13 requests (state,
// timeline, session(s), runs, saved-plans, lifecycle, file, outline, highlight, workspace …), and
// every edit by every session paid it. A live `edit` event now refreshes only what it touched:
// Review re-reads its change list (+ the edited file's impact); Navigate re-reads the open file
// when it is the edited one (file + outline in one request, + its highlight). Every other view is
// marked stale and refreshed when it is next shown.
let staleViews = false;
let lastEdit = 0;
let deltaTimer = 0;
const deltaPaths = new Set();
function deltaRefresh(path) {
  lastEdit = Date.now();
  deltaPaths.add(path);
  if (deltaTimer) return;
  deltaTimer = setTimeout(async () => {
    deltaTimer = 0;
    const paths = [...deltaPaths];
    deltaPaths.clear();
    staleViews = true;
    if (ui.mode === "review" && ui.view !== "pr") acceptDiff(await loadDiff());
    else if (MK.onDeltaNav) MK.onDeltaNav(paths);
  }, 250);
}
MK.deltaRefresh = deltaRefresh;
MK.refreshIfStale = () => { if (staleViews) softRefresh(); };
function onBus(ev) {
  if (MK.onBusExtra) MK.onBusExtra(ev);          // ID.S3 join events (bridge.js)
  let e;
  try { e = JSON.parse(ev.data); } catch (_) { return; }
  // ID3 `ide_open`: the agent points the human at code (the server contained the path).
  if (e && e.kind === "open" && typeof e.path === "string") {
    if (MK.openFile) MK.openFile(e.path, e.line || 1, null, { reload: true });
    return;
  }
  if (!e || e.kind !== "edit" || typeof e.path !== "string") return;
  $("live-dot").classList.add("on");
  deltaRefresh(e.path);
  if (!follow || !MK.openFile) return;
  if (ui.focus && e.session && e.session !== ui.focus) return;
  MK.openFile(e.path, 1, null, { reload: true });
}
function setFollow(on) {
  follow = !!on;
  const b = $("follow");
  if (b) b.setAttribute("aria-pressed", String(follow));
}
MK.setFollow = setFollow;
function initFollow() {
  const b = $("follow");
  if (!b) return;
  b.onclick = () => setFollow(!follow);
}
MK.initFollow = initFollow;

function live() {
  const es = new EventSource("/api/events?client=" + encodeURIComponent(MK.clientId));
  initFollow();
  es.addEventListener("bus", onBus);
  for (const hook of MK.liveHooks || []) hook(es);     // P32: features/activity.js — `job` events
  // The stream's FIRST `changed` is the server's opening fingerprint, sent the moment the page
  // connects — right after the page loaded everything. Re-fetching on it doubled every first-load
  // request (state, live, timeline, impact, runs, sessions, …). It is remembered, not acted on;
  // any later fingerprint (or a resync) refreshes.
  es.addEventListener("changed", (ev) => {
    $("live-dot").classList.add("on");
    const first = ui.lastFp === undefined;
    if (!first && ev.data && ev.data !== "resync" && ev.data === ui.lastFp) return;
    ui.lastFp = ev.data || "";
    // the fingerprint an edit event already delivered as a delta (P35): stale, not a refresh
    if (!first && Date.now() - lastEdit < 3000) { staleViews = true; return; }
    if (!first) softRefresh();
  });
  es.addEventListener("focus", (ev) => {
    const f = JSON.parse(ev.data).focus;
    if (f !== ui.focus) { ui.focus = f; ui.view = f ? "session" : "live"; refresh(); }
  });
  es.onerror = () => $("live-dot").classList.remove("on");
}
MK.live = live;

// ------------------------------------------------------------------------------- keyboard
// §5.5. The `g` chord goes to a screen (g s / g r / g c / g i / g p / g w — a feature screen names
// its own letter); `?` lists every key. Both are reserved in registry.js. The chord listener runs in
// the CAPTURE phase so the letter after `g` is the chord's, never a registry binding's.
const chord = { at: 0 };
const CHORD_MS = 1500;
function chordScreen(letter) {
  const s = SCREENS.find((x) => x.chord && x.chord === letter);
  return s ? s.id : null;
}
MK.chordScreen = chordScreen;
function onChord(ev) {
  if (typing(ev) || ev.metaKey || ev.ctrlKey || ev.altKey) { chord.at = 0; return; }
  if (chord.at && Date.now() - chord.at < CHORD_MS && ev.key.length === 1) {
    chord.at = 0;
    const id = chordScreen(ev.key.toLowerCase());
    if (id) { ev.preventDefault(); ev.stopImmediatePropagation(); setMode(id); }
    return;
  }
  chord.at = 0;
  if (ev.key === "g") { chord.at = Date.now(); ev.preventDefault(); ev.stopImmediatePropagation(); }
  else if (ev.key === "?") { ev.preventDefault(); ev.stopImmediatePropagation(); keysHelp(true); }
}

// The `?` overlay: §5.5's map, built from the screens and the registry (what you see is bound).
const CORE_KEYS = [
  ["] / [", "next / previous changed file"], ["j / k", "next / previous hunk or change"], ["u", "next unseen file"],
  ["e", "open the Review file in Code"], ["Alt+← / Alt+→", "back / forward"], ["> / < / !", "next / previous session · most urgent"],
  ["Esc", "close the overlay"], ["?", "this list"],
];
function keysHelp(open) {
  const box = $("keys-help");
  box.hidden = !open;
  if (!open) return;
  const t = $("keys-table");
  t.replaceChildren();
  const row = (k, what) => { const tr = el("tr"); tr.append(el("td", "mono", k), el("td", "", what)); t.append(tr); };
  for (const s of SCREENS) row(s.chord ? `g ${s.chord}` : (s.keys || "—"), `go to ${s.title}`);
  for (const [k, w] of CORE_KEYS) row(k, w);
  const mac = /Mac|iPhone|iPad/.test(navigator.platform || "");
  const fmt = (k) => (MK.palette && MK.palette.keyLabel ? MK.palette.keyLabel(k, mac) : k);
  for (const c of (window.mokataIDE ? window.mokataIDE.commands() : [])) if (c.keys.length) row(c.keys.map(fmt).join(" · "), c.title);
  $("keys-close").focus();
}
MK.keysHelp = keysHelp;

function onKey(ev) {
  const mod = ev.metaKey || ev.ctrlKey;
  if (ev.key === "Escape") {
    if (!$("keys-help").hidden) { keysHelp(false); return; }
    if (!$("viewing-pop").hidden) { openViewing(false); return; }
  }
  // ⌘P / ⌘K / ⌘⇧P / ⌘G and the palette's own keys live in static/features/palette.js (0.0.26 P3).
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
  $("compare-go").onclick = () => pickView("compare");
  $("pr-go").onclick = () => pickView("pr");
  for (const b of document.querySelectorAll("#viewing-pop button.view[data-view]")) b.onclick = () => pickView(b.dataset.view);
  $("viewing-pick").onclick = () => { openViewing(false); setMode("sessions"); };
  $("viewing-close").onclick = () => openViewing(false);
  $("session-chip").onclick = () => openViewing();
  document.addEventListener("mousedown", (ev) => {
    const p = $("viewing-pop");
    if (!p.hidden && !p.contains(ev.target) && !$("session-chip").contains(ev.target)
        && !(ev.target.closest && ev.target.closest(".combo-list"))) openViewing(false);
  });
  // The rail: one listener for every screen button, the core's and a feature's.
  document.querySelector("nav.rail").addEventListener("click", (ev) => {
    const b = ev.target.closest && ev.target.closest(".rail-btn");
    if (b && b.dataset.screen) setMode(b.dataset.screen);
  });
  for (const b of document.querySelectorAll("button.pill[data-filter]")) {
    b.onclick = () => { ui.filter = b.dataset.filter; renderNavigator(); if (ui.filter === "untested") loadImpacts(true); };
  }
  $("diff-mokata-btn").onclick = async () => { ui.showMokata = !ui.showMokata; await refresh(); };
  $("file-prev").onclick = () => moveFile(-1);
  $("file-next").onclick = () => moveFile(1);
  $("mark-seen").onclick = markSeenNext;
  $("open-nav").onclick = openCurrentInNavigate;
  $("insp-pick").onchange = () => { ui.inspSym = $("insp-pick").value; renderInspector(); };
  $("insp-ripple").onclick = () => { if (MK.openGraph && ui.inspSym) MK.openGraph("ripple", ui.inspSym); };
  $("keys-close").onclick = () => keysHelp(false);
  $("keys-help").addEventListener("mousedown", (ev) => { if (ev.target === $("keys-help")) keysHelp(false); });
  window.addEventListener("keydown", onChord, true);
  document.addEventListener("keydown", onKey);
};
