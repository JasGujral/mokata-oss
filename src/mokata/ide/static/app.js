// mokata ide — the read-only review window. Vanilla JS, no build step (ADR doc 85 §1b).
// SAFETY: every piece of repo text goes into the page through textContent, never as markup —
// the code under review can contain markup, and a review window must not execute it.
"use strict";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
};

const ui = { focus: null, view: "live", picked: [], sample: false, state: null, first: true };

function shortOf(sid) {
  const row = ((ui.state && ui.state.sessions) || []).find((s) => s.id === sid);
  return row ? row.short : String(sid || "").slice(0, 8);
}

async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  return r.json();
}

function markSample(payload) {
  if (payload && payload.sample) { ui.sample = true; $("sample-banner").hidden = false; }
}

// ------------------------------------------------------------------------------- sessions
function renderSessions(state) {
  const ul = $("sessions");
  ul.replaceChildren();
  const all = el("button", "item" + (ui.focus ? "" : " active"));
  all.append(el("span", "", "All sessions"), el("span", "sub", "live diff of the whole tree"));
  all.onclick = () => setFocus(null);
  ul.append(wrapLi(all));
  for (const s of state.sessions || []) {
    const b = el("button", "item" + (ui.focus === s.id ? " active" : ""));
    b.append(el("span", "", `session ${s.short}${s.claim === "bound" ? " · run " + s.run : ""}`));
    const files = (s.files || []).length;
    b.append(el("span", "sub", `${files} file(s) · ${s.writes} write(s) · ${s.last_iso || ""}`));
    b.onclick = () => setFocus(s.id);
    ul.append(wrapLi(b));
  }
  $("sessions-empty").hidden = (state.sessions || []).length > 0;
  $("view-session").disabled = !ui.focus;
  $("focus-chip").textContent = ui.focus ? `session ${shortOf(ui.focus)}` : "all sessions";
}

function wrapLi(child) { const li = el("li"); li.append(child); return li; }

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
    const when = new Date(e.t * 1000).toLocaleTimeString();
    b.append(el("span", "", `${when}  ${e.kind === "before" ? "before" : "write"}  ${e.path}`));
    b.append(el("span", "sub", `session ${shortOf(e.session)}${e.state !== "ok" ? " · " + e.state : ""}`));
    b.onclick = () => pick(e);
    ul.append(wrapLi(b));
  }
  $("timeline-empty").hidden = entries.length > 0;
}

async function pick(e) {
  ui.picked = ui.picked.includes(e.id) ? ui.picked.filter((x) => x !== e.id) : [...ui.picked, e.id].slice(-2);
  ui.view = "between";
  ui.pickedPaths = ui.pickedPaths || {};
  ui.pickedPaths[e.id] = e.path;
  await refresh();
}

// ------------------------------------------------------------------------------- compare / PR
function renderBranches(state) {
  const branches = state.branches || [];
  for (const id of ["base", "head"]) {
    const s = $(id);
    const keep = s.value;
    s.replaceChildren(...branches.map((b) => { const o = el("option", "", b); o.value = b; return o; }));
    if (keep && branches.includes(keep)) s.value = keep;
  }
  if (branches.length > 1 && !$("head").dataset.touched) $("head").value = branches[1];
  $("compare-empty").hidden = branches.length > 1;
}
$("head").onchange = () => { $("head").dataset.touched = "1"; };
$("compare-go").onclick = async () => { ui.view = "compare"; await refresh(); };
$("pr-go").onclick = async () => { ui.view = "pr"; await refresh(); };
for (const b of document.querySelectorAll("button.view")) {
  b.onclick = async () => { ui.view = b.dataset.view; await refresh(); };
}

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

function renderDiff(d) {
  markSample(d);
  $("diff-title").textContent = d.title || "Changes";
  $("diff-stats").textContent = d.files ? `${d.files.length} file(s) · +${d.added} −${d.removed}` : "";
  const note = $("diff-note");
  note.hidden = !d.note;
  note.textContent = d.note || "";
  const box = $("diff");
  box.replaceChildren();
  const files = d.files || [];
  $("diff-empty").hidden = !(files.length === 0 && !d.note && !d.error);
  $("diff-empty-why").textContent = EMPTY_WHY[ui.view] || EMPTY_WHY.live;
  if (d.error) { note.hidden = false; note.textContent = `could not read: ${d.error} ${d.note || ""}`; }
  for (const f of files) box.append(renderFile(f));
}

function renderFile(f) {
  const det = el("details", "file");
  det.open = true;
  const sum = el("summary");
  sum.append(el("span", "", f.old_path ? `${f.old_path} → ${f.path}` : f.path), el("span", "st", f.status),
             el("span", "n-add", `+${f.added}`), el("span", "n-del", `−${f.removed}`));
  det.append(sum);
  if (f.binary) { det.append(el("div", "bin", "binary or too large to show")); return det; }
  const t = el("table", "d");
  for (const h of f.hunks) {
    const hr = el("tr", "hh");
    const td = el("td", "", h.header); td.colSpan = 3; hr.append(td); t.append(hr);
    for (const [op, o, n, text] of h.lines) {
      const tr = el("tr", op === "+" ? "add" : op === "-" ? "del" : "");
      tr.append(el("td", "no", o ?? ""), el("td", "no", n ?? ""), el("td", "tx", (op === " " ? " " : op) + text));
      t.append(tr);
    }
  }
  det.append(t);
  return det;
}

// ------------------------------------------------------------------------------- refresh + live
async function refresh() {
  const state = await api("/api/state");
  ui.state = state;
  markSample(state);
  if (ui.focus === null && state.focus) ui.focus = state.focus;
  if (ui.first) { ui.first = false; if (ui.focus) ui.view = "session"; }   // open on the session that asked
  for (const b of document.querySelectorAll("button.view")) {
    b.classList.toggle("active", b.dataset.view === ui.view);
  }
  $("repo").textContent = state.repo || "";
  renderSessions(state);
  renderBranches(state);
  await renderTimeline();
  renderDiff(await loadDiff());
}

function live() {
  const es = new EventSource("/api/events");
  es.addEventListener("changed", () => { $("live-dot").classList.add("on"); if (ui.view !== "pr") refresh(); });
  es.addEventListener("focus", (ev) => {
    const f = JSON.parse(ev.data).focus;
    if (f !== ui.focus) { ui.focus = f; ui.view = f ? "session" : "live"; refresh(); }
  });
  es.onerror = () => $("live-dot").classList.remove("on");
}

refresh().then(live);
