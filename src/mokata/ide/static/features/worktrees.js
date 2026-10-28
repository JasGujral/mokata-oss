// mokata ide — Worktrees (0.0.26 P31, SESSION-WORKTREES). Every Claude Code session works in its own
// git worktree (.claude/worktrees/<name>/). This panel lists them — session/run, branch, base,
// ahead/behind, dirty count, last activity, merged? — and takes them home:
//   * Open diff  — what that session changed, EXACTLY (git diff from its base + untracked files, so
//                  a `sed -i` through Bash is there too), shown in the Review pane.
//   * Resume     — copies `claude --resume <id>` (run it from the main checkout; Claude Code returns
//                  the session to its worktree).
//   * Merge      — gated: the page PROPOSES, you approve in your terminal (`mokata approve <id>`),
//                  then "Complete" merges into the main checkout's branch and removes the worktree.
//   * Delete     — gated the same way; a worktree with work is refused unless you tick "discard".
//   * Clean merged — gated bulk removal of merged, clean, unlocked worktrees.
//   * Size       — disk size, computed only when asked.
// SAFETY: repo text goes into the page through textContent only.
"use strict";

(function () {
  const MK = window.MK;

  // --------------------------------------------------------------------------- pure helpers
  function ago(ts, now) {
    if (!ts) return "—";
    const s = Math.max(0, Math.round((now || Date.now() / 1000) - ts));
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return Math.floor(s / 86400) + "d ago";
  }
  function bytes(n) {
    if (n === null || n === undefined) return "—";
    const u = ["B", "KB", "MB", "GB"];
    let i = 0, v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return (i ? v.toFixed(1) : String(v)) + " " + u[i];
  }
  // The one-word state a row wears (also what the filter chips count).
  function stateOf(w) {
    if (w.missing) return "missing";
    if (w.locked) return "running";
    if (w.merged && !w.dirty) return "merged";
    if (w.fresh) return "fresh";
    if ((w.ahead || 0) > 0 || (w.dirty || 0) > 0) return "work";
    return "idle";
  }
  MK.worktrees = { ago, bytes, stateOf };

  if (typeof document === "undefined") return;   // node tests of the pure helpers

  const el = MK.el;
  let listEl = null, viewEl = null, noteEl = null;
  let data = null;
  let current = null;            // the session whose diff the Review pane shows
  const pending = {};            // path|action -> {proposal_id, command, discard}

  function toast(text) { if (noteEl) noteEl.textContent = text || ""; }

  async function load() {
    if (!listEl) return;
    toast("loading worktrees…");
    try {
      data = await MK.api("/api/worktrees");
    } catch (e) { data = { error: "fetch", note: String(e) }; }
    MK.markSample(data);
    if (!data || data.error) {      // P39 (R33, §5.4 rule 4): what failed, Retry, the CLI twin
      toast("");
      listEl.replaceChildren();
      MK.regionSay(viewEl, "error", MK.whyFailed(data, "read the worktrees"),
                   { retry: load, cli: "mokata worktree list" });
      return;
    }
    render();
  }

  function cell(tr, text, cls) { const td = el("td", cls || "", text); tr.append(td); return td; }

  function button(label, title, fn) {
    const b = el("button", "wt-btn", label);
    b.type = "button"; b.title = title;
    b.addEventListener("click", (ev) => { ev.stopPropagation(); fn(b); });
    return b;
  }

  function render() {
    listEl.replaceChildren();
    viewEl.replaceChildren();
    const head = el("div", "wt-head");
    head.append(el("h2", "", "Worktrees"));
    if (!data || !data.ok) {
      head.append(el("p", "wt-note", (data && data.note) || "no worktree data"));
      viewEl.append(head);
      toast("");
      return;
    }
    const sub = el("p", "wt-note",
      `every session works in its own worktree · merging back goes into ${data.into || "(detached)"}` +
      (data.note ? ` · ${data.note}` : ""));
    head.append(sub, button("Clean merged", "Remove every merged, clean worktree (you approve it)", () => act("clean", {}, null)),
      button("Refresh", "Re-read the worktrees", load));
    viewEl.append(head);
    if (!data.worktrees.length) {
      viewEl.append(el("p", "wt-empty", "No session worktrees yet. A session enters its own when it starts editing (mokata asks it to)."));
      toast("");
      return;
    }
    const table = el("table", "wt-table");
    const hr = el("tr");
    for (const h of ["", "worktree", "session · run", "branch", "base", "↑ ahead", "↓ behind", "dirty", "size", "active", ""])
      hr.append(el("th", "", h));
    table.append(hr);
    const now = Date.now() / 1000;
    for (const w of data.worktrees) {
      const st = stateOf(w);
      const tr = el("tr", "wt-row wt-" + st);
      cell(tr, st, "wt-state");
      cell(tr, w.name).title = w.path;
      cell(tr, (w.session ? w.session.slice(0, 8) : "—") + (w.run ? " · " + String(w.run).slice(0, 10) : ""));
      cell(tr, w.branch || "(detached)");
      cell(tr, w.base ? w.base.slice(0, 10) : "—");
      cell(tr, w.ahead === null || w.ahead === undefined ? "?" : String(w.ahead));
      cell(tr, w.behind === null || w.behind === undefined ? "?" : String(w.behind));
      cell(tr, w.dirty === null || w.dirty === undefined ? "?" : String(w.dirty));
      const sz = cell(tr, "");
      sz.append(button("size", "Compute this worktree's disk size", async (b) => {
        b.disabled = true;
        const r = await MK.api("/api/worktree/size?path=" + encodeURIComponent(w.path));
        sz.replaceChildren(el("span", "", r && r.ok ? bytes(r.bytes) + (r.complete ? "" : "+") : "?"));
      }));
      cell(tr, ago(w.last_activity, now));
      const acts = cell(tr, "", "wt-acts");
      if (w.session) acts.append(button("Diff", "Open what this session changed in Review", () => openDiff(w.session)));
      if (w.resume) acts.append(button("Resume", "Copy the resume command (run it from the main checkout)", () => copy(w.resume)));
      // SYNC-BEFORE-SHIP: a branch behind its base is rebased first (the merge refuses until then).
      if ((w.behind || 0) > 0)
        acts.append(button("Sync", "Rebase onto " + (data.into || "the main branch") + " (" + w.behind +
          " new commit(s) there) — you approve it; then re-run its tests", () => act("sync", { path: w.path }, w)));
      acts.append(button("Merge", "Merge into " + (data.into || "the main branch") + " — you approve it", () => act("merge", { path: w.path }, w)));
      acts.append(button("Delete", "Delete this worktree — you approve it", () => del(w)));
      const p = pending[w.path + "|sync"] || pending[w.path + "|merge"] || pending[w.path + "|delete"];
      if (p) acts.append(completeRow(p));
      table.append(tr);
    }
    viewEl.append(table);
    const c = pending["|clean"];
    if (c) viewEl.append(completeRow(c));
    // The side panel: a short list for when the centre shows something else.
    for (const w of data.worktrees) {
      const li = el("li", "wt-li wt-" + stateOf(w), `${w.name} · ${stateOf(w)}`);
      li.title = w.path;
      li.addEventListener("click", () => { if (w.session) openDiff(w.session); });
      listEl.append(li);
    }
    toast("");
  }

  function completeRow(p) {
    // P38: the shared approval card (features/approvals.js) — the write in full, the command with
    // Copy, Approve/Reject with the terminal's one-time code when the window has one, live state.
    const box = el("div", "wt-pending");
    box.append(MK.approvalCard({ proposal_id: p.proposal_id, command: p.command }));
    box.append(button("Complete", "After approving: do it now (once)", () => complete(p)));
    return box;
  }

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); toast("copied: " + text); }
    catch (_e) { toast(text); }
  }

  function del(w) {
    const work = (w.ahead || 0) > 0 && !w.merged || (w.dirty || 0) > 0;
    if (!work) return act("delete", { path: w.path }, w);
    const ok = window.confirm(`${w.name} has work that is NOT merged (${w.ahead || 0} commit(s), ${w.dirty || 0} uncommitted change(s)).\n` +
      "Resuming its session needs it. Delete and DISCARD that work? (You will still approve it in your terminal.)");
    if (ok) act("delete", { path: w.path, discard: true }, w);
  }

  async function act(action, body, w) {
    toast(action + "…");
    const r = await MK.api("/api/worktree/" + action, body);
    if (!r || r.error) { toast((r && r.note) || "failed"); return; }
    if (r.stage === "sample") { toast(r.reason); return; }
    if (r.stage === "blocked" || r.stage === "refused") { toast(`${action} refused: ${r.reason}`); return; }
    if (r.stage === "proposed") {
      pending[(w ? w.path : "") + "|" + action] = { action, body, proposal_id: r.proposal_id, command: r.command };
      toast(`approve it in your terminal: ${r.command} — then press Complete`);
      render();
    }
  }

  async function complete(p) {
    const r = await MK.api("/api/worktree/" + p.action, Object.assign({}, p.body, { proposal_id: p.proposal_id }));
    if (!r || r.error) { toast((r && r.note) || "failed"); return; }
    if (r.stage === "refused") { toast(`not done: ${r.reason}`); return; }
    if (r.stage === "blocked") { toast(`not done: ${r.reason}`); }
    for (const k of Object.keys(pending)) if (pending[k] === p) delete pending[k];
    if (r.stage === "done") {
      const res = r.result || {};
      const probs = (res.problems || []).join("; ");
      toast(res.merged === false || res.deleted === false || res.synced === false ? `not done: ${res.reason || probs}` :
        `${p.action} done${probs ? " — " + probs : ""}`);
    }
    await load();
  }

  // The Review pane shows the worktree diff: same DiffSet shape, same renderer (navigator + file).
  async function openDiff(session) {
    current = session;
    MK.ui.view = "worktree";
    await apply();
    MK.setMode("review");
  }
  async function apply() {
    if (!current) return;
    const d = await MK.api("/api/worktree/diff?session=" + encodeURIComponent(current));
    MK.markSample(d);
    const ui = MK.ui;
    ui.diff = d; ui.files = d.files || []; ui.sel = 0; ui.hunkIdx = -1;
    const t = MK.$("diff-title"); if (t) t.textContent = d.title || "Worktree changes";
    const s = MK.$("diff-stats"); if (s) s.textContent = `${ui.files.length} file(s) · +${d.added || 0} −${d.removed || 0}`;
    const n = MK.$("diff-note"); if (n) { n.hidden = !d.note; n.textContent = d.note || ""; }
    const e = MK.$("diff-empty"); if (e) e.hidden = ui.files.length > 0 || !!d.note;
    MK.renderNavigator();
    MK.showFile(0, { keepScroll: false });
  }
  // A live refresh re-reads the core diff for the current view; keep showing the worktree's.
  MK.onRefreshWorktrees = () => { if (MK.ui.view === "worktree") apply(); };

  window.mokataIDE.register({
    id: "worktrees",
    commands: [{ id: "worktrees.open", title: "Worktrees: open", run: () => window.mokataIDE.show("worktrees"),
                 keys: "Alt+Shift+W" }],
    // A DOM-less page (the registry's node test) registers the command and key, not the panel.
    panels: typeof document.createElement !== "function" ? [] : [{
      id: "worktrees", title: "Worktrees", icon: "W", side: true, chord: "w",
      mount(sec, view) {
        sec.append(el("h3", "", "Worktrees"));
        noteEl = el("p", "wt-note", "");
        listEl = el("ul", "wt-list");
        sec.append(noteEl, listEl);
        viewEl = view;
      },
      onShow() { load(); },
    }],
  });
})();
