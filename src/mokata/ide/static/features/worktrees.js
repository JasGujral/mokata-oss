// mokata ide — Worktrees (0.0.26 P31, SESSION-WORKTREES). Every Claude Code session works in its own
// git worktree (.claude/worktrees/<name>/). This panel lists them — session/run, branch, base,
// ahead/behind, dirty count, last activity, merged? — and takes them home:
//   * Open diff  — what that session changed, EXACTLY (git diff from its base + untracked files, so
//                  a `sed -i` through Bash is there too), shown in the Review pane.
//   * Resume     — copies `claude --resume <id>` (run it from the main checkout; Claude Code returns
//                  the session to its worktree).
//   * Merge      — 0.0.27 P21, the GATED merge: the card shows the evidence AT THE BRANCH HEAD (the
//                  full suite, every AC's test, the review) and every blocker by name; choose the
//                  strategy and edit the message; the page PROPOSES, you approve in your terminal
//                  (`mokata approve <id>`), then "Merge now" runs it as a job (progress, Cancel
//                  until the commit point). The worktree is kept — delete it separately.
//   * Run suite  — mokata's full-suite runner in that worktree, then the spec legs: the merge's
//                  test and spec evidence (an absent leg shows as ⚠ NOT CHECKED, never ✓).
//   * Recover    — an interrupted merge (listed above the table) is finished or reported from git.
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
  let viewEl = null, noteEl = null;
  let data = null;
  let current = null;            // the session whose diff the Review pane shows
  const pending = {};            // path|action -> {proposal_id, command, discard}

  function toast(text) { if (noteEl) noteEl.textContent = text || ""; }

  async function load() {
    if (!viewEl) return;
    toast("loading worktrees…");
    try {
      data = await MK.api("/api/worktrees");
    } catch (e) { data = { error: "fetch", note: String(e) }; }
    MK.markSample(data);
    if (!data || data.error) {      // P39 (R33, §5.4 rule 4): what failed, Retry, the CLI twin
      toast("");
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
    head.append(sub, button("Refresh", "Re-read the worktrees", load));
    viewEl.append(head, noteEl);
    if (!data.worktrees.length) {
      viewEl.append(el("p", "wt-empty", "No session worktrees yet. A session enters its own when it starts editing (mokata asks it to)."));
      toast("");
      return;
    }
    for (const j of data.interrupted || []) viewEl.append(interruptedRow(j));
    const table = el("table", "wt-table");
    const hr = el("tr");
    for (const h of ["", "worktree", "session · run", "branch", "base", "↑ ahead", "↓ behind", "dirty", "size", "active", ""])
      hr.append(el("th", "", h));
    table.append(hr);
    const now = Date.now() / 1000;
    for (const w of data.worktrees) {
      const st = stateOf(w);
      const mine = !!MK.ui.focus && w.session === MK.ui.focus;
      const tr = el("tr", "wt-row wt-" + st + (mine ? " wt-focus" : ""));
      if (mine) tr.setAttribute("aria-current", "true");
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
      acts.append(button("Run suite", "Run the full test command and the spec legs in this worktree — the merge's evidence", () => runSuite(w)));
      acts.append(button("Merge…", "Plan the gated merge into " + (data.into || "the main branch") + " — evidence first, then you approve it", () => planMerge(w)));
      acts.append(button("Delete", "Delete this worktree — you approve it", () => del(w)));
      const p = pending[w.path + "|sync"] || pending[w.path + "|delete"];
      if (p) acts.append(completeRow(p));
      table.append(tr);
    }
    viewEl.append(table);
    // P21: each open gated-merge card is a BLOCK below the table, never inside a row — the table's
    // cells are nowrap, so a card in a cell overflowed the view (1440 px UX review).
    for (const w of data.worktrees) {
      if (!merges[w.path]) continue;
      const block = el("section", "wt-merge-block");
      block.append(mergeCard(w, merges[w.path]));
      viewEl.append(block);
    }
    const c = pending["|clean"];
    if (c) viewEl.append(completeRow(c));
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
      toast(res.deleted === false || res.synced === false ? `not done: ${res.reason || probs}` :
        `${p.action} done${probs ? " — " + probs : ""}`);
    }
    await load();
  }

  // --------------------------------------------------------------- 0.0.27 P21: the gated merge
  const merges = {};             // worktree path -> {strategy, message, plan, proposal, job, outcome}

  async function planMerge(w, keep) {
    const m = merges[w.path] = Object.assign({ strategy: "merge-commit", message: null }, keep || {});
    m.plan = null; m.proposal = null; m.outcome = null;
    toast("planning the merge…");
    const body = { path: w.path, strategy: m.strategy };
    if (m.message !== null) body.message = m.message;
    const r = await MK.api("/api/worktree/merge", body);
    if (!r || r.error) { toast((r && r.note) || "failed"); return; }
    if (r.stage === "sample") { toast(r.reason); delete merges[w.path]; return; }
    m.plan = r.plan || { blocked: r.blocked || [], card: {}, args: {} };
    m.state = r.state;
    if (m.message === null && m.plan.args && typeof m.plan.args.message === "string") m.message = m.plan.args.message;
    if (r.stage === "proposed") m.proposal = { proposal_id: r.proposal_id, command: r.command };
    toast(r.stage === "proposed" ? `approve it in your terminal: ${r.command} — then press Merge now` : `merge ${r.state}: ${r.reason || ""}`);
    render();
  }

  function mergeCard(w, m) {
    const box = el("div", "wt-merge-card");
    const plan = m.plan || {};
    const card = plan.card || {};
    box.append(el("h3", "", `Merge ${w.branch || "?"} → ${data.into || "?"}`));
    if (plan.args && plan.args.head)
      box.append(el("p", "wt-note", `${String(card.head || "").slice(0, 12)} onto ${String(card.base || "").slice(0, 12)} · ${card.commits ?? "?"} commit(s), ${card.files ?? "?"} file(s) · lands the tested tree`));
    const ev = el("ul", "wt-evidence");
    for (const e of card.evidence || []) ev.append(el("li", "wt-ok", "✓ " + e));
    for (const b of plan.blocked || []) ev.append(el("li", "wt-blocked", `✗ [${b.code}] ${b.text}`));
    if (card.override) ev.append(el("li", "wt-warn", "⚠ override: " + card.override));
    for (const u of card.unchecked || []) ev.append(el("li", "wt-warn", "⚠ " + u));
    box.append(ev);
    const ctl = el("div", "wt-merge-ctl");
    const sel = el("select", "wt-strategy");
    for (const s of ["merge-commit", "squash", "ff"]) {
      const o = el("option", "", s); o.value = s; if (s === m.strategy) o.selected = true; sel.append(o);
    }
    sel.addEventListener("change", () => { m.strategy = sel.value; planMerge(w, { strategy: m.strategy, message: m.message }); });
    ctl.append(el("label", "", "strategy "), sel);
    box.append(ctl);
    if (m.strategy !== "ff") {
      const ta = el("textarea", "wt-message");
      ta.value = m.message || "";
      ta.rows = 6;
      ta.addEventListener("change", () => { m.message = ta.value; planMerge(w, { strategy: m.strategy, message: m.message }); });
      box.append(ta);
    }
    box.append(el("p", "wt-note", "unsigned · the repo's hooks, filters and merge drivers are not run · the main checkout's index is locked while files are written"));
    if (m.proposal && !m.job) {
      box.append(MK.approvalCard({ proposal_id: m.proposal.proposal_id, command: m.proposal.command }));
      box.append(button("Merge now", "After approving: run the merge (once)", () => runMerge(w, m)));
    }
    if (m.job) {
      const host = el("div", "wt-merge-job");
      box.append(host);
      if (MK.jobs && MK.jobs.strip) { m.strip = MK.jobs.strip(host, "wt-strip"); if (m.job.snap) m.strip.set(m.job.snap); }
    }
    if (m.outcome) box.append(el("p", m.outcome.merged ? "wt-ok" : "wt-blocked", `${m.outcome.state}: ${m.outcome.message}`));
    box.append(button("Close", "Close the merge card", () => { delete merges[w.path]; render(); }));
    return box;
  }

  async function runMerge(w, m) {
    const r = await MK.api("/api/worktree/merge", { proposal_id: m.proposal.proposal_id });
    if (!r || r.error) { toast((r && r.note) || "failed"); return; }
    if (r.stage === "refused") { toast(`not merged: ${r.reason}`); return; }
    if (r.stage !== "started") { toast(r.reason || r.stage); return; }
    m.job = { id: r.id, snap: r.job };
    render();
    pollMerge(w, m, r.id);
  }

  async function pollMerge(w, m, id) {
    if (!m.job || m.job.id !== id) return;
    const r = await MK.api("/api/jobs/" + encodeURIComponent(id)).catch(() => null);
    if (r && r.job) { m.job.snap = r.job; if (m.strip) m.strip.set(r.job); }
    const st = m.job.snap && m.job.snap.state;
    if (["done", "failed", "cancelled"].includes(st)) {
      m.outcome = (r && r.result) || { state: st, merged: false, message: (m.job.snap.error || st) };
      m.job = null; m.proposal = null;
      toast(`merge: ${m.outcome.state}`);
      await load();
      return;
    }
    setTimeout(() => pollMerge(w, m, id), 400);
  }

  async function runSuite(w) {
    const r = await MK.api("/api/worktree/suite", { path: w.path });
    if (!r || r.error) { toast((r && r.note) || "could not start the suite"); return; }
    if (r.stage === "sample") { toast(r.reason); return; }
    toast("full suite running — see Activity; then plan the merge again");
  }

  function interruptedRow(j) {
    const box = el("div", "wt-interrupted");
    box.append(el("p", "wt-blocked", `interrupted merge ${String(j.merge_id || "").slice(0, 12)} of ${j.branch || "?"} into ${j.into || "?"} (${j.phase || "?"}${j.problem ? ": " + j.problem : ""})`));
    box.append(el("p", "wt-hint", "After a crash, do not run git commit in the main checkout: if its index.lock was deleted by hand a plain commit reverts the merge. Use Recover."));
    box.append(button("Recover", "Decide from git what this merge left, and finish or report it", async () => {
      const r = await MK.api("/api/worktree/merge-recover", { merge_id: j.merge_id, unlock_proposal: j._unlock || "" });
      const res = (r && r.result) || {};
      if (res.facts && res.facts.proposal_id) {
        j._unlock = res.facts.proposal_id;
        box.append(MK.approvalCard({ proposal_id: res.facts.proposal_id, command: res.facts.command }));
      }
      toast(`recover: ${res.state || (r && r.reason) || "?"} — ${res.message || ""}`);
      if (res.state === "merged" || res.state === "not-merged") load();
    }));
    return box;
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
  // §5 #12: "Clean merged" sits in the Sessions list toolbar; its approval card shows in Worktree.
  async function cleanMerged() {
    window.mokataIDE.show("worktrees");
    if (!data) await load();
    act("clean", {}, null);
  }
  MK.wireWorktreesClean = () => {
    const b = document.getElementById("s1-clean");
    if (b) b.onclick = cleanMerged;
  };
  // A live refresh re-reads the core diff for the current view; keep showing the worktree's.
  MK.onRefreshWorktrees = () => { if (MK.ui.view === "worktree") apply(); };

  window.mokataIDE.register({
    id: "worktrees",
    commands: [{ id: "worktrees.open", title: "Sessions: worktrees", run: () => window.mokataIDE.show("worktrees"),
                 keys: "Alt+Shift+W" },
               { id: "worktrees.clean", title: "Sessions: clean merged worktrees", run: () => cleanMerged() }],
    // A DOM-less page (the registry's node test) registers the command and key, not the view.
    panels: typeof document.createElement !== "function" ? [] : [{
      id: "worktrees", title: "Worktree", tab: "sessions", order: 30,
      mount(_sec, view) {
        noteEl = el("p", "wt-note", "");
        noteEl.setAttribute("role", "status");
        viewEl = view;
      },
      onShow() { load(); },
    }],
  });
})();
