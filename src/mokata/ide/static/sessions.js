// mokata ide — the Sessions screen (S1; 0.0.26 P33, first cut 0.0.25 ID3): every session in one
// paginated list, its changed files and diffs (cumulative or per write), the needs-you queue, the
// session switcher, the replay (time-lapse) and the change treemap. The server derives and lays out
// everything (`ide/session_views.py`); this file only draws it. Repo text goes in through
// textContent / SVG attributes, never markup.
"use strict";

(function () {
  const MK = window.MK;
  const { $, el, ui, api } = MK;
  const SVG = "http://www.w3.org/2000/svg";
  const sv = { data: null, tl: null, step: 0, timer: 0, size: "loc" };

  const svgEl = (tag, attrs) => {
    const n = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, String(v));
    return n;
  };
  const ago = (secs) => secs < 90 ? `${secs}s` : secs < 5400 ? `${Math.round(secs / 60)}m` : `${Math.round(secs / 3600)}h`;

  // ----------------------------------------------------------------------------- S1 list
  // 0.0.26 P33 — ONE session list: every session that ran in the repo (the durable index ∪
  // snapshots ∪ claims ∪ worktrees ∪ transcripts), newest first, paginated by 50, filtered on the
  // server. Click a row → its changed files → their diffs, right here (B-F1: no hidden view).
  const s1 = { rows: [], total: 0, q: "", filter: "all", stats: {}, diff: null, mode: "cumulative",
               writes: null, timer: 0, seq: 0, debounce: 0 };
  const PAGE = 50;
  const dur = (secs) => !secs ? "" : secs < 90 ? `${Math.round(secs)}s` : secs < 5400 ? `${Math.round(secs / 60)} min` : `${(secs / 3600).toFixed(1)} h`;
  const when = (t) => t ? new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";

  async function loadList(more) {
    const seq = ++s1.seq;
    const offset = more ? s1.rows.length : 0;
    const qs = `?offset=${offset}&limit=${PAGE}&filter=${encodeURIComponent(s1.filter)}&q=${encodeURIComponent(s1.q)}`;
    // §5.4: a skeleton IN the list after 300 ms; an error names itself with Retry (never blank).
    const slow = !more && setTimeout(() => { if (seq === s1.seq && !s1.rows.length) MK.regionSay($("s1-rows"), "loading", "Reading every session…"); }, 300);
    const d = await api("/api/sessions/list" + qs).catch((e) => ({ error: "unreachable", note: String(e && e.message || e) }));
    clearTimeout(slow);
    if (seq !== s1.seq) return;
    if (!d || d.error) {
      $("s1-empty").hidden = true;
      MK.regionSay($("s1-rows"), "error", MK.whyFailed(d, "list the sessions"), { retry: () => loadList(more), cli: "mokata status" });
      return;
    }
    MK.markSample(d);
    s1.rows = more ? s1.rows.concat(d.rows) : d.rows;
    s1.total = d.total;
    s1.all = d.all;
    s1.live = d.live;
    renderList();
    // +/− arrive after the list has drawn, and only for a list that is still the current one: a
    // filter being typed never waits behind (or competes with) the diffs of a superseded page.
    setTimeout(() => { if (seq === s1.seq) loadStats(d.rows); }, 350);
    armAging();
  }
  MK.loadSessionList = loadList;

  async function loadBrowser() {
    // needs-you badge + queue (risk-sorted); the list itself is `loadList`.
    const d = await api("/api/sessions").catch(() => null);
    if (!d || d.error) return;
    MK.markSample(d);
    sv.data = d;
    renderNeedsBadge(d);
    // P34: "waiting on you" and "live" are part of the Activity chip (§5.3), not top-bar buttons.
    if (MK.jobs && MK.jobs.setSessions) {
      MK.jobs.setSessions({ waiting: d.waiting || 0, live: (d.sessions || []).filter((s) => s.status === "live").length,
                            top: (d.needs_you || [])[0] || null });
    }
    if (ui.mode === "sessions") { renderNeeds(d); loadList(false); if (ui.focus) loadDetail(); }
  }
  MK.onRefreshSessions = loadBrowser;

  // A live row turns "ended" when its Claude process goes away; nothing pushes that, so while the
  // screen shows a live row it re-asks every 30 s (never at idle: no live row, no timer).
  function armAging() {
    if (s1.timer) { clearTimeout(s1.timer); s1.timer = 0; }
    if (ui.mode === "sessions" && s1.rows.some((r) => r.status === "live")) {
      s1.timer = setTimeout(() => { s1.timer = 0; if (ui.mode === "sessions") loadList(false); }, 30000);
    }
  }

  async function loadStats(rows) {
    const ids = rows.map((r) => r.id).filter((id) => !s1.stats[id]);
    if (!ids.length) return;
    const d = await api("/api/sessions/stats?ids=" + encodeURIComponent(ids.join(","))).catch(() => null);
    if (!d || d.error) return;
    Object.assign(s1.stats, d.stats || {});
    for (const id of ids) paintStats(id);
  }

  function paintStats(id) {
    const st = s1.stats[id];
    const cell = document.querySelector(`[data-s1stat="${CSS.escape(id)}"]`);
    if (!st || !cell) return;
    const r = s1.rows.find((x) => x.id === id);
    if (!st.files && r && r.kind !== "changed") { cell.textContent = r.kind; return; }
    cell.textContent = `${st.files} file(s) · +${st.added} −${st.removed}` + (st.too_large ? ` · ${st.too_large} too large` : "");
  }

  function renderList() {
    const ul = $("s1-rows");
    ul.replaceChildren();
    for (const r of s1.rows) {
      const b = el("button", "item s1-row st-" + r.status + (ui.focus && (r.chain || [r.id]).includes(ui.focus) ? " active" : "") + (r.waiting ? " waiting" : ""));
      b.type = "button";
      b.dataset.sid = r.id;
      const head = el("span", "s1-head");
      const dot = el("span", "st-dot st-" + r.status);
      dot.title = `${r.status} — ${r.status_basis}`;
      head.append(dot, el("span", "s1-prompt", r.prompt || `session ${r.short}`));
      b.append(head);
      const bits = [when(r.started), dur(r.duration), r.status === "live" ? "live" : r.status,
        r.branch ? "⎇ " + r.branch : "", r.run ? `run ${String(r.run).slice(0, 12)}${r.stage ? " · " + r.stage : ""}` : ""].filter(Boolean);
      const sub = el("span", "sub", bits.join(" · "));
      b.append(sub);
      const facts = el("span", "sub s1-facts");
      const stat = el("span", "", r.kind === "changed" ? `${(r.files || []).length} file(s)…` : r.kind);
      stat.dataset.s1stat = r.id;
      facts.append(stat);
      if ((r.chain || []).length > 1) facts.append(el("span", "chip resumed small", `resumed ×${r.chain.length - 1}`));
      if (r.worktree) facts.append(el("span", "chip small", "worktree"));
      if (r.waiting) facts.append(el("span", "ny-reason", "waiting on you"));
      b.append(facts);
      b.title = `${r.id}\n${r.status_basis}\nknown by: ${(r.known_by || []).join(", ")}`;
      b.onclick = () => switchTo(r.id);
      ul.append(MK.wrapLi(b));
      paintStats(r.id);
    }
    const none = !s1.rows.length;
    const empty = $("s1-empty");
    empty.hidden = !none;
    if (none && !s1.q && s1.filter === "all") {
      empty.replaceChildren(el("b", "", "No Claude Code session has run in this repo yet."), el("br"),
        document.createTextNode("Start one in its own worktree: "), el("code", "", "claude -w"),
        document.createTextNode(" — or plan first with "), el("code", "", "/mokata:brainstorm"), document.createTextNode("."));
    } else if (none) {
      empty.replaceChildren(document.createTextNode("No session matches — "));
      const clr = el("button", "link", "clear the filter");
      clr.type = "button";
      clr.onclick = () => { s1.q = ""; $("s1-q").value = ""; setFilter("all"); };
      empty.append(clr);
    }
    $("s1-more").hidden = s1.rows.length >= s1.total;
    $("s1-count").textContent = `${s1.total} of ${s1.all || s1.total} session(s)` + (s1.live ? ` · ${s1.live} live` : "")
      + (s1.rows.length < s1.total ? ` · showing ${s1.rows.length}` : "");
  }

  function setFilter(f) {
    s1.filter = f;
    for (const b of document.querySelectorAll("button.pill[data-s1filter]")) b.classList.toggle("active", b.dataset.s1filter === f);
    loadList(false);
  }

  // ----------------------------------------------------------------------------- S1 detail
  async function loadDetail() {
    const sid = ui.focus;
    $("s1-pick").hidden = !!sid;
    $("s1-box").hidden = !sid;
    if (!sid) return;
    const row = s1.rows.find((r) => (r.chain || [r.id]).includes(sid)) || (ui.state && ui.state.focus_row) || { id: sid, short: sid.slice(0, 8) };
    $("s1-title").textContent = row.prompt || `session ${String(sid).slice(0, 8)}`;
    $("s1-meta").textContent = [`session ${String(sid).slice(0, 8)}`, row.status ? `${row.status} (${row.status_basis})` : "",
      when(row.started), dur(row.duration), row.branch ? "⎇ " + row.branch : "",
      row.run ? `run ${row.run}${row.stage ? " · " + row.stage : ""}` : "",
      (row.chain || []).length > 1 ? `resumed chain: ${row.chain.map((x) => String(x).slice(0, 8)).join(" ← ")}` : ""].filter(Boolean).join(" · ");
    const slow = setTimeout(() => { if (ui.focus === sid && !s1.diff) MK.regionSay($("session-files"), "loading", "Reading this session's changes…"); }, 300);
    s1.diff = null;
    const d = await api("/api/session?id=" + encodeURIComponent(sid)).catch((e) => ({ error: "unreachable", note: String(e && e.message || e) }));
    clearTimeout(slow);
    if (ui.focus !== sid) return;
    if (!d || d.error) {
      $("session-files").hidden = false;
      MK.regionSay($("session-files"), "error", MK.whyFailed(d, "read this session's changes"), { retry: loadDetail });
      return;
    }
    MK.markSample(d);
    s1.diff = d;
    ui.sessionPaths = { sid, paths: (d.files || []).map((f) => f.path) };   // the search scope (R28)
    if (MK.renderViewing) MK.renderViewing();
    const note = [d.note, d.basis === "approximate" ? "basis: approximate (shared checkout)" : d.basis === "exact" ? `basis: exact (worktree ${d.branch || ""})` : ""].filter(Boolean).join(" · ");
    $("s1-note").hidden = !note;
    $("s1-note").textContent = note;
    if (s1.mode === "write") loadWrites(); else renderFiles(d);
  }

  function renderFiles(d) {
    $("session-files").hidden = false;
    $("s1-writes").hidden = true;
    $("s1-writes-hint").hidden = true;
    $("s1-write-diff").hidden = true;
    const ul = $("session-files");
    ul.replaceChildren();
    const files = d.files || [];
    if (!files.length) {
      const row = s1.rows.find((r) => (r.chain || [r.id]).includes(ui.focus));
      ul.append(el("li", "empty", row && row.kind !== "changed" ? `${row.kind} — this session changed no file.` : "This session has not changed any file."));
      return;
    }
    files.forEach((f, i) => {
      const li = el("li", "s1-file");
      const det = document.createElement("details");
      if (i === 0) det.open = true;
      const sum = el("summary", "mono");
      const letter = MK.ST_LETTER[f.status] || "M";
      sum.append(el("span", "st st-" + letter, letter), el("span", "", " " + f.path + " "),
        el("span", "n-add small", f.too_large ? "" : `+${f.added}`), el("span", "n-del small", f.too_large ? "too large" : ` −${f.removed}`));
      const open = el("button", "link small", "review");
      open.type = "button";
      open.title = "open this file's diff in Review";
      open.onclick = (ev) => { ev.preventDefault(); openInReview(f.path); };
      sum.append(document.createTextNode(" "), open);
      det.append(sum);
      det.addEventListener("toggle", () => { if (det.open && !det.dataset.drawn) { det.dataset.drawn = "1"; det.append(MK.renderFile(f)); } });
      if (det.open) { det.dataset.drawn = "1"; det.append(MK.renderFile(f)); }
      li.append(det);
      ul.append(li);
    });
  }

  async function loadWrites() {
    $("session-files").hidden = true;
    $("s1-writes").hidden = false;
    $("s1-writes-hint").hidden = false;
    const sid = ui.focus;
    const d = await api("/api/timelapse?session=" + encodeURIComponent(sid)).catch(() => null);
    if (!d || ui.focus !== sid) return;
    const ul = $("s1-writes");
    ul.replaceChildren();
    const writes = (d.steps || []).filter((st) => st.kind === "write");
    const nth = {};
    for (const st of writes) {
      nth[st.path] = (nth[st.path] || 0) + 1;
      const b = el("button", "item mono small");
      b.type = "button";
      b.append(el("span", "", `${new Date(st.t * 1000).toLocaleTimeString()} · ${st.path} · write ${nth[st.path]}`),
        el("span", "sub", st.measured ? `+${st.added} −${st.removed}` : "delta not measured"));
      // Click = that write (the previous capture → this one); shift-click a second = the two
      // moments compared, in Review (what the bottom timeline's "pick two" did).
      b.onclick = (ev) => {
        if (ev.shiftKey && s1.lastWrite && s1.lastWrite.snap !== st.snap) { compareWrites(s1.lastWrite, st); return; }
        s1.lastWrite = st;
        showWrite(st.snap, b);
      };
      ul.append(MK.wrapLi(b));
    }
    if (!writes.length) ul.append(el("li", "empty", "No Write/Edit was recorded for this session (Bash or worktree changes show under Cumulative)."));
  }

  function compareWrites(a, b) {
    ui.picked = [a.snap, b.snap];
    ui.pickedPaths[a.snap] = a.path; ui.pickedPaths[b.snap] = b.path;
    ui.view = "between";
    MK.setMode("review");
    MK.refresh();
  }

  async function showWrite(snap, btn) {
    for (const x of document.querySelectorAll("#s1-writes button.active")) x.classList.remove("active");
    if (btn) btn.classList.add("active");
    const d = await api("/api/session/write?snap=" + encodeURIComponent(snap)).catch(() => null);
    const box = $("s1-write-diff");
    box.hidden = false;
    box.replaceChildren();
    if (!d) return;
    box.append(el("h3", "mono small", d.title || ""));
    if (d.note) box.append(el("p", "small dim", d.note));
    for (const f of d.files || []) box.append(MK.renderFile(f));
    if (!(d.files || []).length || !(d.files[0].hunks || []).length && !d.files[0].too_large) box.append(el("p", "empty", "This write changed nothing."));
  }

  function openInReview(path) {
    ui.view = "session";
    MK.setMode("review");
    MK.refresh().then(() => {
      const i = ui.files.findIndex((f) => f.path === path);
      if (i >= 0) MK.showFile(i);
    });
  }

  function renderNeedsBadge(d) {
    const b = $("needs-you");
    b.hidden = !d.waiting;
    $("needs-you-n").textContent = String(d.waiting || 0);
    const top = (d.needs_you || [])[0];
    b.title = top ? `${d.waiting} waiting on you — most urgent: ${top.text}` : "";
  }

  function needsLine(r) {
    const s = el("span", "ny-reason", r.reason === "awaiting-approval" ? "AWAITING APPROVAL" : r.reason);
    s.title = r.text;
    return s;
  }

  function renderNeeds(d) {
    const ny = $("ny-list");
    ny.replaceChildren();
    for (const q of d.needs_you || []) {
      const li = el("li", "ny-item");
      const b = el("button", "link", q.session ? `session ${q.short}` : "this repo");
      b.type = "button";
      b.onclick = () => { if (q.session) switchTo(q.session); };
      li.append(needsLine(q), b, el("span", "dim small", ` waiting ${ago(q.waiting_secs)} · risk ${q.risk}`));
      const det = q.detail || {};
      if (det.command) li.append(el("code", "ny-cmd", det.command));
      else if (det.gate) li.append(el("span", "small mono", ` ${det.gate}: ${det.decision} ${det.subject || ""}`));
      ny.append(li);
    }
    $("ny-empty").hidden = (d.needs_you || []).length > 0;
    $("ny-formula").textContent = d.risk_formula || "";
  }

  // ----------------------------------------------------------------------------- switcher
  // Clicking a session ANYWHERE (a row, needs-you, `>`/`<`, `!`, the palette) opens it HERE: the
  // Sessions screen with its changed files and diffs (B-F1). Focus is the server's, so the Code
  // view and the Review pane read the same session (R28).
  async function switchTo(sid) {
    if (!sid) return;
    if (ui.mode !== "sessions") MK.setMode("sessions");
    if (sid !== ui.focus) {
      ui.focus = sid; ui.picked = [];
      await api("/api/focus", { session: sid });
      ui.view = "session";
      await MK.refresh();
    }
    for (const b of document.querySelectorAll("#s1-rows button.s1-row")) {
      const r = s1.rows.find((x) => x.id === b.dataset.sid);
      b.classList.toggle("active", !!r && (r.chain || [r.id]).includes(sid));
    }
    loadDetail();
    loadTimelapse();
    if ($("s1-map").open) loadTreemap();
  }
  MK.switchSession = switchTo;

  function cycle(delta) {
    const rows = s1.rows;
    if (!rows.length) return;
    const i = rows.findIndex((s) => (s.chain || [s.id]).includes(ui.focus));
    switchTo(rows[(i + delta + rows.length) % rows.length].id);
  }
  function urgent() {
    const q = ((sv.data && sv.data.needs_you) || []).find((x) => x.session);
    if (q) switchTo(q.session);
  }

  // ----------------------------------------------------------------------------- time-lapse
  async function loadTimelapse() {
    stop();
    $("tl-empty").hidden = !!ui.focus;
    $("tl-box").hidden = !ui.focus;
    if (!ui.focus) return;
    const d = await api("/api/timelapse?session=" + encodeURIComponent(ui.focus)).catch(() => null);
    if (!d || d.error) return;
    sv.tl = d;
    const r = $("tl-range");
    r.max = String(Math.max(0, d.steps.length - 1));
    sv.step = Math.max(0, d.steps.length - 1);
    r.value = String(sv.step);
    const joined = (d.joined || []).length > 1 ? ` · joined with ${d.joined.slice(1).map((x) => String(x).slice(0, 8)).join(", ")} (resumed)` : "";
    $("tl-note").textContent = `${d.count} step(s)${d.truncated ? " (newest shown)" : ""}${joined} · ${d.reads}`;
    drawStep();
  }

  function drawStep() {
    const d = sv.tl;
    if (!d) return;
    const st = d.steps[sv.step];
    const touched = {};
    for (const s of d.steps.slice(0, sv.step + 1)) {
      if (!s.path) continue;
      const t = touched[s.path] || (touched[s.path] = { writes: 0, lines: 0 });
      if (s.kind === "write") { t.writes += 1; t.lines += (s.added || 0) + (s.removed || 0); }
    }
    const ul = $("tl-files");
    ul.replaceChildren();
    for (const p of d.files) {
      const t = touched[p];
      const li = el("li", "tl-file mono" + (t ? " lit" : "") + (st && st.path === p ? " now" : ""));
      li.append(el("span", "", p), el("span", "dim small", t ? ` ${t.writes} write(s) · ${t.lines} line(s)` : " not yet"));
      ul.append(li);
    }
    const lab = $("tl-label");
    if (!st) { lab.textContent = "no steps recorded for this session"; return; }
    const when = new Date(st.t * 1000).toLocaleTimeString();
    lab.textContent = st.kind === "gate"
      ? `${sv.step + 1}/${d.steps.length} · ${when} · gate ${st.gate}: ${st.decision}${st.blocked ? " (BLOCKED)" : ""}`
      : st.kind === "resume"
      ? `${sv.step + 1}/${d.steps.length} · ${when} · resumed — session ${String(st.session).slice(0, 8)}`
        + (st.from && st.from !== st.session ? ` continues ${String(st.from).slice(0, 8)}` : "") + " (same run)"
      : `${sv.step + 1}/${d.steps.length} · ${when} · ${st.kind} ${st.path}`
        + (st.kind === "write" ? (st.measured ? ` (+${st.added} −${st.removed})` : " (delta not measured)") : "");
    $("tl-show").disabled = !st.snap;
  }

  function stop() { if (sv.timer) { clearInterval(sv.timer); sv.timer = 0; } $("tl-play").textContent = "Play"; }
  function play() {
    if (sv.timer) { stop(); return; }
    if (!sv.tl || !sv.tl.steps.length) return;
    if (sv.step >= sv.tl.steps.length - 1) sv.step = 0;
    $("tl-play").textContent = "Pause";
    sv.timer = setInterval(() => {
      if (!sv.tl || sv.step >= sv.tl.steps.length - 1) { stop(); return; }
      sv.step += 1; $("tl-range").value = String(sv.step); drawStep();
    }, 400);
  }

  // ----------------------------------------------------------------------------- treemap
  async function loadTreemap() {
    const qs = `?size=${sv.size}` + (ui.focus ? "&session=" + encodeURIComponent(ui.focus) : "");
    const d = await api("/api/treemap" + qs).catch(() => null);
    const svg = $("tm");
    svg.replaceChildren();
    if (!d || d.error) { $("tm-note").textContent = d ? `treemap unavailable: ${d.note || d.error}` : ""; return; }
    svg.setAttribute("viewBox", `0 0 ${d.width} ${d.height}`);
    for (const dr of d.dirs) {
      const r = svgEl("rect", { x: dr.x, y: dr.y, width: dr.w, height: dr.h, class: "tm-dir" });
      const t = svgEl("title"); t.textContent = dr.path + "/"; r.append(t);
      svg.append(r);
    }
    for (const f of d.files) {
      if (f.w <= 0 || f.h <= 0) continue;
      const r = svgEl("rect", { x: f.x, y: f.y, width: f.w, height: f.h, class: "tm-file tm-h" + f.heat });
      const t = svgEl("title");
      t.textContent = `${f.path} · ${f.loc ?? "?"} LOC` + (f.writes ? ` · ${f.writes} write(s) · +${f.added} −${f.removed}` : " · unchanged");
      r.append(t);
      r.addEventListener("click", () => MK.openFile && MK.openFile(f.path, 1, null));
      svg.append(r);
      if (f.w > 60 && f.h > 14) {
        const lab = svgEl("text", { x: f.x + 3, y: f.y + 11, class: "tm-label" });
        lab.textContent = f.path.slice(f.path.lastIndexOf("/") + 1);
        svg.append(lab);
      }
    }
    $("tm-note").textContent = `${d.count} file(s) sized by ${d.sized_by}; ${d.changed} changed`
      + `${ui.focus ? " by this session" : " by any session"}${d.omitted ? ` · ${d.omitted} small unchanged file(s) omitted` : ""}`;
  }

  // ----------------------------------------------------------------------------- wiring
  function renderAll() {
    if (sv.data) renderNeeds(sv.data);
    loadList(false);
    loadDetail();
    loadTimelapse();
    if ($("s1-map").open) loadTreemap();        // the Map is asked for only when it is open
  }
  MK.onModeSessions = (mode) => {
    if (mode === "sessions") renderAll();
    else { stop(); if (s1.timer) { clearTimeout(s1.timer); s1.timer = 0; } }
  };
  MK.onRefreshSessionViews = () => { if (ui.mode === "sessions") { loadTimelapse(); if ($("s1-map").open) loadTreemap(); } };

  MK.wireSessions = function wireSessions() {
    $("needs-you").onclick = () => { if (MK.jobs) MK.jobs.close(); urgent(); };
    $("s1-q").addEventListener("input", () => {
      clearTimeout(s1.debounce);
      s1.debounce = setTimeout(() => { s1.q = $("s1-q").value.trim(); loadList(false); }, 80);
    });
    for (const b of document.querySelectorAll("button.pill[data-s1filter]")) b.onclick = () => setFilter(b.dataset.s1filter);
    $("s1-more").onclick = () => loadList(true);
    for (const b of document.querySelectorAll("button.pill[data-s1diff]")) {
      b.onclick = () => {
        s1.mode = b.dataset.s1diff;
        for (const x of document.querySelectorAll("button.pill[data-s1diff]")) x.classList.toggle("active", x === b);
        if (s1.mode === "write") loadWrites(); else if (s1.diff) renderFiles(s1.diff); else loadDetail();
      };
    }
    $("s1-review").onclick = () => { ui.view = "session"; MK.setMode("review"); MK.refresh(); };
    $("s1-resume").onclick = () => {
      if (!ui.focus) return;
      const cmd = `claude --resume ${ui.focus}`;
      const done = () => { $("s1-resume").textContent = "Copied: " + cmd; setTimeout(() => { $("s1-resume").textContent = "Copy resume command"; }, 2500); };
      if (navigator.clipboard) navigator.clipboard.writeText(cmd).then(done, () => { $("s1-resume").textContent = cmd; });
      else $("s1-resume").textContent = cmd;
    };
    $("tl-range").oninput = () => { stop(); sv.step = +$("tl-range").value; drawStep(); };
    $("tl-play").onclick = play;
    $("tl-show").onclick = () => {
      const st = sv.tl && sv.tl.steps[sv.step];
      if (st && st.snap && MK.pickSnapshot) MK.pickSnapshot({ id: st.snap, path: st.path });
    };
    $("s1-map").addEventListener("toggle", () => { if ($("s1-map").open) loadTreemap(); });
    for (const b of document.querySelectorAll("button.pill[data-tmsize]")) {
      b.onclick = () => {
        sv.size = b.dataset.tmsize;
        for (const x of document.querySelectorAll("button.pill[data-tmsize]")) x.classList.toggle("active", x === b);
        loadTreemap();
      };
    }
    document.addEventListener("keydown", (ev) => {
      if (MK.typing(ev) || ev.metaKey || ev.ctrlKey || ev.altKey || !$("qo").hidden) return;
      if (ev.key === ">") { ev.preventDefault(); cycle(1); }
      else if (ev.key === "<") { ev.preventDefault(); cycle(-1); }
      else if (ev.key === "!") { ev.preventDefault(); urgent(); }
    });
  };
})();
