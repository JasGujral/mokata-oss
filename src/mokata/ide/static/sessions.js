// mokata ide — the Sessions screen (S1; 0.0.26 P33, first cut 0.0.25 ID3; 0.0.27 UX-SIMPLIFY):
// every session in one paginated list (the left side), and the picked session's header — title,
// facts, the run's lifecycle strip, "Review changes (n)", the resume command. Its history is the
// Timeline view (features/activity_panel.js), its plan Plan & spec (plan.js), its worktree the
// Worktree view (features/worktrees.js). Its diffs open in Review — one diff renderer, one place.
// What waits on you goes to the waiting bar (approvals.js). The server derives everything
// (`ide/session_views.py`); this file only draws it. Repo text goes in through textContent only.
"use strict";

(function () {
  const MK = window.MK;
  const { $, el, ui, api } = MK;
  MK.svgNS = "http://www.w3.org/2000/svg";   // the one SVG namespace constant (P13 Proof draws with it)
  const sv = { data: null };

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
    // the needs-you queue (risk-sorted) goes to the ONE waiting bar; the live count to the Sessions
    // tab; the list itself is `loadList`.
    const d = await api("/api/sessions").catch(() => null);
    if (!d || d.error) return;
    MK.markSample(d);
    sv.data = d;
    if (MK.waiting) MK.waiting.setNeeds(d.needs_you || []);
    const live = (d.sessions || []).filter((x) => x.status === "live").length;
    const n = $("tab-sessions-n");
    n.textContent = live ? String(live) : "";
    n.title = live ? `${live} live session(s)` : "";
    if (ui.tab === "sessions") { loadList(false); if (ui.focus) loadDetail(); }
  }
  MK.onRefreshSessions = loadBrowser;

  // A live row turns "ended" when its Claude process goes away; nothing pushes that, so while the
  // screen shows a live row it re-asks every 30 s (never at idle: no live row, no timer).
  function armAging() {
    if (s1.timer) { clearTimeout(s1.timer); s1.timer = 0; }
    if (ui.tab === "sessions" && s1.rows.some((r) => r.status === "live")) {
      s1.timer = setTimeout(() => { s1.timer = 0; if (ui.tab === "sessions") loadList(false); }, 30000);
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
      const on = !!ui.focus && (r.chain || [r.id]).includes(ui.focus);
      const b = el("button", "item s1-row st-" + r.status + (on ? " active" : "") + (r.waiting ? " waiting" : ""));
      b.type = "button";
      b.setAttribute("aria-current", on ? "true" : "false");
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
    for (const b of document.querySelectorAll("button.pill[data-s1filter]")) {
      b.classList.toggle("active", b.dataset.s1filter === f);
      b.setAttribute("aria-pressed", String(b.dataset.s1filter === f));
    }
    loadList(false);
  }

  // ----------------------------------------------------------------------------- S1 detail
  // ----------------------------------------------------------------------------- S1 detail
  // The header: what the picked session is. Its changes open in Review ("Review changes (n)").
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
    $("s1-review").textContent = "Review changes";
    const d = await api("/api/session?id=" + encodeURIComponent(sid)).catch((e) => ({ error: "unreachable", note: String(e && e.message || e) }));
    if (ui.focus !== sid) return;
    if (!d || d.error) {
      $("s1-note").hidden = false;
      $("s1-note").textContent = MK.whyFailed(d, "read this session's changes");
      return;
    }
    MK.markSample(d);
    const n = (d.files || []).length;
    $("s1-review").textContent = `Review changes (${n})`;
    $("s1-review").disabled = !n;
    ui.sessionPaths = { sid, paths: (d.files || []).map((f) => f.path) };   // the search scope (R28)
    if (MK.renderViewing) MK.renderViewing();
    const note = [d.note, d.basis === "approximate" ? "changes are approximate (a shared checkout)" : d.basis === "exact" ? `changes are exact (worktree ${d.branch || ""})` : ""].filter(Boolean).join(" · ");
    $("s1-note").hidden = !note;
    $("s1-note").textContent = note;
  }

  // ----------------------------------------------------------------------------- switcher
  // Clicking a session ANYWHERE (a row, the waiting bar, `>`/`<`, `!`, the palette) picks it HERE:
  // the Sessions screen, its header and the view last open (Timeline / Plan & spec / Worktree).
  // Focus is the server's, so the Code view and the Review pane read the same session (R28).
  async function switchTo(sid) {
    if (!sid) return;
    if (ui.tab !== "sessions") MK.setMode("sessions");
    if (sid !== ui.focus) {
      ui.focus = sid; ui.picked = [];
      await api("/api/focus", { session: sid });
      ui.view = "session";
      await MK.refresh();
    }
    for (const b of document.querySelectorAll("#s1-rows button.s1-row")) {
      const r = s1.rows.find((x) => x.id === b.dataset.sid);
      const on = !!r && (r.chain || [r.id]).includes(sid);
      b.classList.toggle("active", on);
      b.setAttribute("aria-current", on ? "true" : "false");
    }
    loadDetail();
    if (MK.onSessionPicked) MK.onSessionPicked(sid);
  }
  MK.switchSession = switchTo;

  function cycle(delta) {
    const rows = s1.rows;
    if (!rows.length) return;
    const i = rows.findIndex((x) => (x.chain || [x.id]).includes(ui.focus));
    switchTo(rows[(i + delta + rows.length) % rows.length].id);
  }
  function urgent() {
    const q = ((sv.data && sv.data.needs_you) || []).find((x) => x.session);
    if (q) switchTo(q.session);
  }
  MK.urgentSession = urgent;

  // ----------------------------------------------------------------------------- wiring
  let shown = false;
  MK.onModeSessions = () => {
    const on = ui.tab === "sessions" && $("sessions-head") && !$("sessions-head").hidden;
    if (on && !shown) { loadList(false); loadDetail(); }
    if (!on && s1.timer) { clearTimeout(s1.timer); s1.timer = 0; }
    shown = on;
  };

  MK.wireSessions = function wireSessions() {
    $("s1-q").addEventListener("input", () => {
      clearTimeout(s1.debounce);
      s1.debounce = setTimeout(() => { s1.q = $("s1-q").value.trim(); loadList(false); }, 80);
    });
    for (const b of document.querySelectorAll("button.pill[data-s1filter]")) b.onclick = () => setFilter(b.dataset.s1filter);
    $("s1-more").onclick = () => loadList(true);
    $("s1-review").onclick = () => { ui.view = "session"; MK.setMode("review"); MK.refresh(); };
    $("s1-resume").onclick = () => {
      if (!ui.focus) return;
      const cmd = `claude --resume ${ui.focus}`;
      const done = () => { $("s1-resume").textContent = "Copied: " + cmd; setTimeout(() => { $("s1-resume").textContent = "Copy resume command"; }, 2500); };
      if (navigator.clipboard) navigator.clipboard.writeText(cmd).then(done, () => { $("s1-resume").textContent = cmd; });
      else $("s1-resume").textContent = cmd;
    };
    document.addEventListener("keydown", (ev) => {
      if (MK.typing(ev) || ev.metaKey || ev.ctrlKey || ev.altKey || !$("qo").hidden || !MK.singleKeysOn()) return;
      if (ev.key === ">") { ev.preventDefault(); cycle(1); }
      else if (ev.key === "<") { ev.preventDefault(); cycle(-1); }
      else if (ev.key === "!") { ev.preventDefault(); urgent(); }
    });
  };
})();
