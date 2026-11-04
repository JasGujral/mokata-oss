// mokata ide — review context (0.0.26 P7, ID-REVIEW-CONTEXT).
//   * Tests by AC — the run's tests grouped by its spec's acceptance criteria, each test's latest
//     status and red/green history, untested ACs flagged, the approval that let the change in, and
//     Re-run (the project's own test command, as a job with progress, Cancel and its output).
//     0.0.27 UX-SIMPLIFY #35: it lives under Review › Proof (a subset of the same change set) —
//     `MK.testsByAC.mount(host)` puts it in the host Proof gives it, `MK.testsByAC.load()` reads it;
//   * Blame on a hunk — who last wrote each line OF THAT HUNK and the hunk's line history (an item
//     of the hunk's ⋯ menu, MK.hunkMenu — UX-SIMPLIFY #36);
//   * Rendered diff of a changed Markdown file, and the rendered spec diff of the run (any two
//     versions side by side, with the AC changes);
//   * Preview of a Markdown file in the Code view.
// Rendering is MK.render (features/render.js: vendored markdown-it + mermaid, sanitised).
// SAFETY: repo text goes into the page through textContent (or MK.render's allowlist) only.
"use strict";

(function () {
  const MK = window.MK;
  if (typeof document === "undefined") return;
  const el = MK.el;
  const rv = { run: null, page: 1, status: "", data: null, job: null, open: new Set(), tpages: {},
               mdOn: new Set(), blameOpen: new Set(), blameCache: new Map() };
  const MD = /\.(md|markdown|mdx|mdown)$/i;

  function ago(t) {
    if (!t) return "";
    const s = Math.max(0, Math.round(Date.now() / 1000 - t));
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return Math.floor(s / 86400) + "d ago";
  }
  function btn(text, cls, title) {
    const b = el("button", cls || "btn ghost small", text);
    b.type = "button";
    if (title) b.title = title;
    return b;
  }
  const DOT = { green: "●", red: "●", unrun: "○", untested: "!" };

  // =========================================================================== Tests by AC
  let host = null;
  // The host is Proof's (the caller owns where it sits); mounting twice into the same host is a no-op.
  function mountTests(into) {
    if (!into || (host && into.contains(host))) return;
    host = el("section", "rv-tests");
    host.id = "rv-tests";
    host.setAttribute("aria-label", "Tests by acceptance criterion");
    into.append(host);
  }

  async function loadTests() {
    if (!host) return;
    if (!rv.run) {
      const runs = await MK.api("/api/runs");
      if (runs && runs.state === "not-initialized") { rv.data = runs; paintTests(); return; }
      rv.runs = (runs && runs.runs) || [];
      rv.run = (runs && runs.default) || (rv.runs[0] && rv.runs[0].id) || null;
      if (!rv.run) { rv.data = { state: "no-run" }; paintTests(); return; }
    }
    if (!rv.data) { host.replaceChildren(); MK.regionSay && MK.regionSay(host, "loading", "loading the run's tests…"); }
    const q = new URLSearchParams({ run: rv.run, page: String(rv.page) });
    if (rv.status) q.set("status", rv.status);
    rv.data = await MK.api("/api/review/tests?" + q.toString());
    paintTests();
  }
  MK.testsByAC = { mount: mountTests, load: loadTests };

  function paintTests() {
    const d = rv.data || {};
    host.replaceChildren();
    if (d.error) {
      host.append(el("p", "warn", `${d.error}: ${d.note || ""}`));
      const r = btn("Retry"); r.onclick = loadTests; host.append(r);
      return;
    }
    if (d.state === "not-initialized") {
      host.append(el("p", "empty", "This repository is not set up for mokata, so there is no run, spec or AC → test map to show."),
                  el("p", "small dim", d.command ? "Set it up with: " + d.command : ""));
      return;
    }
    if (d.state === "no-run") { host.append(el("p", "empty", "No run has state in this repository yet — tests appear here once a run emits a spec (/mokata:spec).")); return; }
    // ---- the head: run · approval · totals · command
    const head = el("div", "rv-head");
    const runBox = el("div", "combo");
    runBox.id = "rv-run"; runBox.dataset.label = "run"; runBox.dataset.placeholder = "run…";
    head.append(el("span", "dim small", "run"), runBox);
    if (d.sample) head.append(el("span", "pill-static", "SAMPLE"));
    host.append(head);
    if (MK.combo) {
      const c = MK.combo(runBox);
      c.setOptions((rv.runs || [{ id: d.run, title: "" }]).map((r) => ({ value: r.id, label: (r.short || r.id.slice(0, 8)) + (r.title ? " · " + r.title : "") })));
      runBox.value = rv.run || d.run;
      runBox.addEventListener("change", () => { rv.run = runBox.value; rv.page = 1; rv.data = null; loadTests(); });
    }
    const ap = d.approval || {};
    const why = el("p", "rv-approval small");
    if (ap.state === "ok") {
      why.append("Let in by ", el("b", "", ap.approver || "the human"), ` approving the plan “${ap.approach || "?"}”`,
                 ap.approved_at ? ` (${String(ap.approved_at).slice(0, 16).replace("T", " ")})` : "", ` · spec v${ap.spec_version || "?"}`);
    } else why.append(el("span", "warn", "No approved plan on this run — nothing records who let this change in."));
    host.append(why);
    if (d.state !== "ok") { host.append(el("p", "warn", d.note || d.state)); return; }
    const specBtn = btn("Rendered spec diff", "pill-link", "this run's spec, two versions rendered side by side, with the ACs that changed");
    specBtn.id = "rv-spec-diff";
    specBtn.onclick = () => openSpecDiff(d.run);
    why.append(" ", specBtn);
    const t = d.totals || {};
    const sum = el("div", "rv-totals");
    sum.append(el("span", "", `${t.acs} AC${t.acs === 1 ? "" : "s"}`),
               el("span", "rv-st-green", `${t.green} green`), el("span", "rv-st-red", `${t.red} red`),
               el("span", "rv-st-unrun", `${t.unrun} not run`), el("span", "rv-st-untested", `${t.untested} untested`),
               el("span", "dim", `· ${t.tests} test${t.tests === 1 ? "" : "s"}`));
    if (t.never_red) sum.append(el("span", "rv-st-untested", `· ${t.never_red} never seen red`));
    host.append(sum);
    // ---- command + run controls
    const cmd = d.command || {};
    const bar = el("div", "rv-cmdbar");
    bar.append(el("code", "", cmd.text || "no test command"));
    const failBtn = btn("Re-run failing", "btn small"), allBtn = btn("Re-run all", "btn ghost small");
    failBtn.id = "rv-rerun-failing"; allBtn.id = "rv-rerun-all";
    failBtn.disabled = !cmd.runnable || !t.red; allBtn.disabled = !cmd.runnable || !t.tests;
    failBtn.onclick = () => rerun({ failing: true });
    allBtn.onclick = () => rerun({ all: true });
    bar.append(failBtn, allBtn);
    host.append(bar);
    if (cmd.note && !cmd.runnable) host.append(el("p", "warn small rv-cmd-note", cmd.note));
    host.append(el("p", "small dim", d.rerun_note || ""));
    const jobBox = el("div", "rv-job");
    jobBox.id = "rv-job";
    host.append(jobBox);
    paintJob();
    // ---- filters
    const pills = el("div", "filters");
    for (const [k, label] of [["", "All"], ["untested", "Untested"], ["red", "Red"], ["unrun", "Not run"], ["green", "Green"]]) {
      const p = btn(label, "pill" + (rv.status === k ? " active" : ""));
      p.dataset.status = k;
      p.onclick = () => { rv.status = k; rv.page = 1; loadTests(); };
      pills.append(p);
    }
    host.append(pills);
    // ---- the ACs (one page)
    const list = el("ul", "rv-acs");
    list.id = "rv-acs";
    for (const c of d.criteria || []) list.append(acRow(c, cmd));
    if (!(d.criteria || []).length) list.append(el("li", "empty", rv.status ? `No AC is ${rv.status}.` : "This spec has no criteria."));
    host.append(list);
    if (d.pages > 1) {
      const pg = el("div", "rv-pager");
      const prev = btn("‹ previous"), next = btn("next ›");
      prev.disabled = d.page <= 1; next.disabled = d.page >= d.pages;
      prev.onclick = () => { rv.page = d.page - 1; loadTests(); };
      next.onclick = () => { rv.page = d.page + 1; loadTests(); };
      pg.append(prev, el("span", "dim small", `page ${d.page} of ${d.pages} · ${d.matching} AC${d.matching === 1 ? "" : "s"}`), next);
      host.append(pg);
    }
    const um = d.unmapped || {};
    if (um.total) {
      const box = el("details", "rv-unmapped");
      box.append(el("summary", "", `${um.total} test${um.total === 1 ? "" : "s"} on record that no AC claims`));
      const ul = el("ul", "rv-tests-list");
      for (const id of um.tests || []) ul.append(el("li", "mono small", id));
      let shown = (um.tests || []).length, upage = 1;
      if (um.total > shown) {
        const li = el("li", "rv-more");
        const more = btn(`${um.total - shown} more…`, "link small");
        more.onclick = async () => {
          more.disabled = true;
          const r = await MK.api("/api/review/tests?" + new URLSearchParams({ run: rv.run, tpage: String(++upage), per: "1" }));
          const next = (r && r.unmapped && r.unmapped.tests) || [];
          for (const id of next) li.before(el("li", "mono small", id));
          shown += next.length;
          more.disabled = false;
          more.textContent = `${um.total - shown} more…`;
          if (!next.length || shown >= um.total) li.remove();
        };
        li.append(more);
        ul.append(li);
      }
      box.append(ul);
      host.append(box);
    }
  }

  function historyChips(h) {
    const box = el("span", "rv-hist");
    for (const x of h) {
      const c = el("span", "rv-h rv-h-" + x.state, x.state === "green" ? "G" : x.state === "red" ? "R" : x.state === "not-run" ? "N" : "E");
      c.title = `${x.state} · ${x.basis}${x.at ? " · " + ago(x.at) : ""}`;
      box.append(c);
    }
    if (!h.length) box.append(el("span", "dim small", "no run on record"));
    return box;
  }

  function acRow(c, cmd) {
    const li = el("li", "rv-ac rv-ac-" + c.status);
    li.dataset.ac = c.id;
    const head = el("div", "rv-ac-head");
    const dot = el("span", "rv-dot rv-st-" + c.status, DOT[c.status] || "○");
    dot.title = c.status;
    const tog = btn("", "link rv-ac-toggle");
    tog.append(el("b", "mono", c.id), " ", el("span", "", c.text));
    tog.setAttribute("aria-expanded", String(rv.open.has(c.id) || c.status === "red"));
    head.append(dot, tog, el("span", "spacer"));
    if (c.untested) head.append(el("span", "rv-flag", "untested"));
    else {
      head.append(el("span", "dim small", `${c.tests_total} test${c.tests_total === 1 ? "" : "s"}`));
      const r = btn("Re-run AC", "btn ghost small rv-rerun-ac");
      r.disabled = !cmd.runnable;
      r.onclick = () => rerun({ ac: c.id });
      head.append(r);
    }
    li.append(head);
    const body = el("ul", "rv-tests-list");
    body.hidden = tog.getAttribute("aria-expanded") !== "true";
    tog.onclick = () => {
      body.hidden = !body.hidden;
      tog.setAttribute("aria-expanded", String(!body.hidden));
      if (body.hidden) rv.open.delete(c.id); else rv.open.add(c.id);
    };
    for (const t of c.tests) body.append(testRow(t, cmd));
    if (c.tests_total > c.tests.length) {
      const more = btn(`${c.tests_total - c.tests.length} more…`, "link small");
      more.onclick = () => moreTests(c, body, more, cmd);
      const mli = el("li", "rv-more");
      mli.append(more);
      body.append(mli);
    }
    li.append(body);
    return li;
  }

  async function moreTests(c, body, more, cmd) {
    const next = (rv.tpages[c.id] || 1) + 1;
    more.disabled = true;
    const r = await MK.api("/api/review/tests?" + new URLSearchParams({ run: rv.run, ac: c.id, tpage: String(next) }));
    const row = r && r.criteria && r.criteria[0];
    if (!row) { more.textContent = (r && r.note) || "could not load"; return; }
    rv.tpages[c.id] = next;
    more.parentElement.remove();
    for (const t of row.tests) body.append(testRow(t, cmd));
    if (next < row.tests_pages) {
      const m = btn("more…", "link small");
      m.onclick = () => moreTests(c, body, m, cmd);
      const mli = el("li", "rv-more");
      mli.append(m);
      body.append(mli);
    }
  }

  function testRow(t, cmd) {
    const li = el("li", "rv-test");
    li.dataset.test = t.id;
    const st = el("span", "rv-chip rv-st-" + t.status, t.status === "unrun" ? "not run" : t.status);
    li.append(st, el("span", "mono rv-tid", t.id), historyChips(t.history || []));
    if (t.never_red) {
      const f = el("span", "rv-flag", "never seen red");
      f.title = "recorded GREEN without a RED on record — this test was never seen failing";
      li.append(f);
    }
    li.append(el("span", "spacer"));
    const r = btn("Re-run", "btn ghost small rv-rerun-one");
    r.disabled = !cmd.runnable;
    r.onclick = () => rerun({ tests: [t.id] });
    li.append(r);
    return li;
  }

  // ---- re-run: the project's own command, as a job (progress, Cancel, output)
  let strip = null;
  async function rerun(what) {
    const r = await MK.api("/api/review/tests/run", Object.assign({ run: rv.run }, what));
    if (!r || r.error) { rv.job = { error: (r && r.note) || "could not start" }; paintJob(); return; }
    rv.job = { id: r.id, snap: r.job, output: "", tests: [], result: null, ref: -1 };
    paintJob();
    pollJob(r.id);
  }

  async function pollJob(id) {
    const j = rv.job;
    if (!j || j.id !== id) return;
    const r = await MK.api("/api/jobs/" + encodeURIComponent(id)).catch(() => null);
    if (!rv.job || rv.job.id !== id) return;
    if (r && r.job) {
      j.snap = r.job;
      const p = r.result || r.partial;
      if (p) { j.output = p.output || ""; j.tests = p.tests || []; j.bytes = p.output_bytes || 0; j.env = p.env || ""; }
      if (r.result) j.result = r.result;
    }
    paintJob();
    if (j.snap && ["done", "failed", "cancelled"].includes(j.snap.state)) {
      if (j.snap.state === "done") { rv.data = rv.data && Object.assign({}, rv.data); loadTests(); }
      return;
    }
    setTimeout(() => pollJob(id), 400);
  }
  if (MK.jobs && MK.jobs.onUpdate) MK.jobs.onUpdate((snap) => {
    if (rv.job && snap && snap.id === rv.job.id) { rv.job.snap = Object.assign({}, rv.job.snap, snap); if (strip) strip.set(rv.job.snap); }
  });

  function paintJob() {
    const box = MK.$("rv-job");
    if (!box) return;
    box.replaceChildren();
    strip = null;
    const j = rv.job;
    if (!j) return;
    if (j.error) { box.append(el("p", "warn rv-job-error", j.error)); return; }
    if (MK.jobs && MK.jobs.strip) { strip = MK.jobs.strip(box, "rv-strip"); if (j.snap) strip.set(j.snap); }
    const res = el("ul", "rv-job-tests");
    for (const t of j.tests || []) {
      const li = el("li", "", "");
      li.append(el("span", "rv-chip rv-st-" + (t.state === "not-run" ? "unrun" : t.state), t.state === "not-run" ? "not run" : t.state), el("span", "mono", t.id),
                el("span", "dim small", t.state === "not-run" ? ` ${t.note || "could not start"}` : ""),
                el("span", "dim small", t.seconds !== undefined ? ` ${t.seconds}s` : ""));
      res.append(li);
    }
    box.append(res);
    if (j.snap && j.snap.state === "done" && j.result) {
      box.append(el("p", "small rv-job-sum", `${j.result.passed} passed · ${j.result.failed} failed · ${j.result.not_run || 0} not run · ran in ${j.result.env || "the project env"}`));
    }
    if (j.output) {
      const det = el("details", "rv-output");
      det.open = !!(j.snap && !["done"].includes(j.snap.state)) || (j.result && (j.result.failed > 0 || j.result.not_run > 0));
      det.append(el("summary", "small", "output" + (j.bytes > j.output.length ? ` (last ${Math.round(j.output.length / 1024)} KB of ${Math.round(j.bytes / 1024)} KB)` : "")));
      const pre = el("pre", "mono rv-out", j.output);
      det.append(pre);
      box.append(det);
      pre.scrollTop = pre.scrollHeight;
    }
  }

  // =========================================================================== rendered spec diff
  let overlay = null;
  function dialog(title) {
    if (!overlay) {
      overlay = el("div", "qo-overlay rv-overlay");
      overlay.id = "rv-overlay";
      overlay.addEventListener("click", (ev) => { if (ev.target === overlay) overlay.hidden = true; });
      document.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape" && overlay && !overlay.hidden) { overlay.hidden = true; ev.stopPropagation(); }
      }, true);
      document.body.append(overlay);
    }
    overlay.replaceChildren();
    overlay.hidden = false;
    const box = el("div", "qo-dialog rv-dialog");
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", title);
    const head = el("div", "qo-input-row");
    const close = btn("Close", "btn ghost");
    close.onclick = () => { overlay.hidden = true; };
    head.append(el("span", "qo-label", title), el("span", "spacer"), close);
    const body = el("div", "rv-dialog-body");
    box.append(head, body);
    overlay.append(box);
    return { body, head };
  }

  async function openSpecDiff(run, a, b) {
    const dlg = dialog("Spec — rendered diff");
    const q = new URLSearchParams({ run: run || rv.run || "" });
    if (a) q.set("a", String(a));
    if (b) q.set("b", String(b));
    const s = await MK.api("/api/review/spec-diff?" + q.toString());
    if (!s || s.error || s.state !== "ok") { dlg.body.append(el("p", "warn", (s && (s.note || s.error)) || "no spec")); return; }
    const pick = el("div", "rv-spec-pick small");
    pick.append(el("span", "dim", "compare"));
    for (const side of ["old", "new"]) {
      const sel = el("span", "rv-vers");
      for (const v of s.versions) {
        const b2 = btn("v" + v, "pill" + (s[side].version === v ? " active" : ""));
        b2.onclick = () => openSpecDiff(run, side === "old" ? v : s.old.version, side === "new" ? v : s.new.version);
        sel.append(b2);
      }
      pick.append(sel, side === "old" ? el("span", "dim", "→") : "");
    }
    dlg.body.append(pick);
    const ch = s.criteria;
    const sum = el("ul", "rv-ac-changes small");
    sum.id = "rv-ac-changes";
    for (const x of ch.added) sum.append(el("li", "n-add", `+ ${x.id} ${x.text}`));
    for (const x of ch.removed) sum.append(el("li", "n-del", `− ${x.id} ${x.text}`));
    for (const x of ch.changed) sum.append(el("li", "rv-chg", `~ ${x.id}: “${x.old}” → “${x.new}”`));
    sum.append(el("li", "dim", `${ch.same} AC${ch.same === 1 ? "" : "s"} unchanged`));
    dlg.body.append(sum);
    const view = el("div", "rv-spec-render");
    view.id = "rv-spec-render";
    dlg.body.append(view);
    MK.render.diff(view, s.old.markdown, s.new.markdown, { oldLabel: `v${s.old.version}${s.old.state !== "ok" ? " (" + s.old.state + ")" : ""}`, newLabel: `v${s.new.version}` });
  }
  MK.reviewSpecDiff = openSpecDiff;

  // =========================================================================== hunks: blame + rendered .md
  function sideRevs() {
    const v = MK.viewing ? MK.viewing() : { kind: "live" };
    if (v.kind === "live") return { base: "HEAD", head: "", label: "HEAD → working tree" };
    if (v.kind === "compare") {
      const b = MK.$("base") && MK.$("base").value, h = MK.$("head") && MK.$("head").value;
      if (b && h) return { base: b, head: h, label: `${b} → ${h}` };
    }
    return null;
  }

  // Blame is the first item of every hunk's ⋯ menu (MK.hunkMenu, features/diffview.js).
  function blameItem(f, h, sec) {
    const revs = sideRevs();
    return { id: "blame", label: rvBlameOpen(sec) ? "Hide blame" : "Blame",
             title: revs ? "who last wrote these lines, and their history (this hunk only)" : "blame needs a git revision: Uncommitted changes or Compare branches",
             disabled: !revs, run: () => toggleBlame(sec, f, h, revs) };
  }
  function rvBlameOpen(sec) { return !!sec.querySelector(".rv-blame"); }

  function decorate() {
    const diff = MK.$("diff");
    if (!diff) return;
    const f = MK.ui && MK.ui.files ? MK.ui.files[MK.ui.sel] : null;
    if (!f) return;
    const revs = sideRevs();
    // a live refresh redraws the hunks: a blame that was open stays open (from its cache)
    if (revs) {
      for (const sec of diff.querySelectorAll("section.hunk")) {
        if (sec.dataset.rvSeen) continue;
        sec.dataset.rvSeen = "1";
        const h = (f.hunks || [])[+sec.dataset.k];
        if (h && rv.blameOpen.has(blameKey(f, h, revs))) toggleBlame(sec, f, h, revs, true);
      }
    }
    const body = diff.querySelector(".file-body");
    if (body && MD.test(f.path) && !body.querySelector(".rv-md-bar") && !f.binary && !f.too_large) {
      const bar = el("div", "rv-md-bar small");
      const b = btn("Rendered diff", "pill-link rv-md-btn", revs ? "the document before and after, rendered side by side" : "a rendered diff needs Uncommitted changes or Compare branches");
      b.disabled = !revs;
      const out = el("div", "rv-md-out");
      out.hidden = true;
      b.onclick = () => renderedMd(f, revs, out, b, body);
      bar.append(b, el("span", "dim", revs ? revs.label : ""));
      body.prepend(bar);
      bar.after(out);
      if (revs && rv.mdOn.has(f.path)) renderedMd(f, revs, out, b, body);   // kept across a refresh
    }
  }
  function blameKey(f, h, revs) { return [f.path, h.header, revs.base, revs.head].join("\u0000"); }

  async function renderedMd(f, revs, out, b, body) {
    if (!out.hidden) {
      out.hidden = true; b.textContent = "Rendered diff"; rv.mdOn.delete(f.path);
      for (const s of body.querySelectorAll("section.hunk")) s.hidden = false;
      return;
    }
    rv.mdOn.add(f.path);
    out.hidden = false;
    b.textContent = "Show the line diff";
    for (const s of body.querySelectorAll("section.hunk")) s.hidden = true;
    out.replaceChildren(el("p", "dim small", "rendering…"));
    const oldPath = f.old_path || f.path;
    const [a, c] = await Promise.all([
      f.status === "added" ? Promise.resolve({ text: "", state: "absent" }) : MK.api("/api/review/doc?" + new URLSearchParams({ path: oldPath, rev: revs.base })),
      f.status === "deleted" ? Promise.resolve({ text: "", state: "absent" }) : MK.api("/api/review/doc?" + new URLSearchParams({ path: f.path, rev: revs.head })),
    ]);
    if ((a && a.error) || (c && c.error)) { out.replaceChildren(el("p", "warn", (a.note || c.note || "could not read the document"))); return; }
    out.replaceChildren();
    MK.render.diff(out, a.text || "", c.text || "", { basePath: f.path, oldLabel: revs.base + (a.state !== "ok" ? " (none)" : ""), newLabel: revs.head || "working tree" });
  }

  async function toggleBlame(sec, f, h, revs, restore) {
    const key = blameKey(f, h, revs);
    let box = sec.querySelector(".rv-blame");
    if (box) { box.remove(); rv.blameOpen.delete(key); return; }
    rv.blameOpen.add(key);
    box = el("div", "rv-blame small");
    sec.querySelector(".hunk-head").after(box);
    await blameInto(box, f, h, revs, restore);
  }

  // Ask (or reuse) the blame of one hunk and draw it in `box`: who last wrote its lines on each
  // side, and the hunk's line history. The ⋯ menu's Blame and the inspector's History tab share it.
  async function blameInto(box, f, h, revs, cached) {
    const key = blameKey(f, h, revs);
    let r = cached ? rv.blameCache.get(key) : null;
    if (!r) {
      const s = MK.jobs && MK.jobs.strip ? MK.jobs.strip(box) : null;
      if (s) s.say("blaming this hunk…");
      const q = new URLSearchParams({ path: f.path, hunk: h.header, base: revs.base });
      if (revs.head) q.set("head", revs.head);
      r = await MK.api("/api/review/blame?" + q.toString(), undefined, { onJob: (j) => s && s.set(j) });
      if (s) s.done();
      if (r && r.state === "ok") rv.blameCache.set(key, r);
    }
    if (!box.isConnected) return;
    if (!r || r.error || r.state !== "ok") { box.append(el("p", "warn", (r && (r.note || r.error)) || "no blame")); return; }
    const cols = el("div", "rv-blame-cols");
    for (const [side, label] of [["old", `before (${r.base})`], ["new", `after (${r.head || "working tree"})`]]) {
      const part = r[side];
      const col = el("div", "rv-blame-col");
      col.append(el("div", "dim", `${label} · lines ${part.start}–${part.end}`));
      const ul = el("ul", "rv-commits");
      for (const c of part.commits) {
        const li = el("li", "");
        li.append(el("code", "", c.uncommitted ? "uncommitted" : c.commit.slice(0, 8)), " ",
                  el("b", "", c.author || ""), el("span", "dim", ` ${ago(c.time)} · ${c.lines} line${c.lines === 1 ? "" : "s"} `), el("span", "", c.summary || ""));
        ul.append(li);
      }
      if (!part.commits.length) ul.append(el("li", "dim", side === "old" ? "no old lines in this hunk" : "no new lines in this hunk"));
      col.append(ul);
      cols.append(col);
    }
    box.append(cols);
    const hist = el("div", "rv-history");
    hist.append(el("div", "dim", `line history of this hunk · ${r.history.length} commit${r.history.length === 1 ? "" : "s"}`));
    const ul = el("ul", "rv-commits");
    for (const c of r.history) {
      const li = el("li", "");
      li.append(el("code", "", c.commit.slice(0, 8)), " ", el("b", "", c.author), el("span", "dim", ` ${ago(c.time)} `), el("span", "", c.summary));
      ul.append(li);
    }
    hist.append(ul);
    for (const n of r.notes || []) hist.append(el("p", "dim", n));
    box.append(hist);
  }

  // The Review inspector's History tab (UX-SIMPLIFY §4): the blame of the hunk in view.
  MK.reviewHistory = (host) => {
    host.replaceChildren();
    const ui = MK.ui;
    const f = ui.files[ui.sel];
    const h = f && (f.hunks || [])[Math.max(0, ui.hunkIdx)];
    const revs = sideRevs();
    if (!f || !h) { host.append(el("p", "dim small", "No hunk in view.")); return; }
    if (!revs) { host.append(el("p", "dim small", "History needs a git revision: Uncommitted changes or Compare branches (Viewing).")); return; }
    host.append(el("p", "mono small", `${f.path} · ${h.header}`));
    const box = el("div", "rv-blame small");
    host.append(box);
    blameInto(box, f, h, revs, true);
  };

  // =========================================================================== Code view: preview .md
  let prevBtn = null, preview = null;
  function mountPreview() {
    const bar = document.querySelector("#view-navigate .bar");
    if (!bar || prevBtn) return;
    prevBtn = btn("Preview", "pill-link rv-preview-btn", "render this Markdown file (Mermaid included)");
    prevBtn.id = "rv-preview";
    prevBtn.hidden = true;
    prevBtn.onclick = () => togglePreview();
    bar.append(prevBtn);
    preview = el("div", "rv-md-preview");
    preview.id = "rv-md-preview";
    preview.hidden = true;
    MK.$("code-scroll").append(preview);
    const code = MK.$("code");
    if (code) new MutationObserver(syncPreview).observe(code, { childList: true });
  }
  function syncPreview() {
    const path = MK.nav && MK.nav.path;
    const isMd = !!path && MD.test(path);
    prevBtn.hidden = !isMd;
    if (!preview.hidden && preview.dataset.path !== path) {
      if (isMd) renderPreview(path); else togglePreview(false);
    }
  }
  async function renderPreview(path) {
    preview.dataset.path = path;
    preview.replaceChildren(el("p", "dim small", "rendering…"));
    const d = await MK.api("/api/review/doc?" + new URLSearchParams({ path }));
    if (preview.dataset.path !== path) return;
    if (!d || d.error || d.state !== "ok") { preview.replaceChildren(el("p", "warn", (d && (d.note || d.error || d.state)) || "could not read")); return; }
    preview.replaceChildren();
    MK.render.markdown(preview, d.text, { basePath: path });
  }
  function togglePreview(on) {
    const want = on === undefined ? preview.hidden : on;
    preview.hidden = !want;
    MK.$("code").hidden = want;
    prevBtn.classList.toggle("active", want);
    prevBtn.textContent = want ? "Source" : "Preview";
    if (want) renderPreview(MK.nav.path);
    else preview.dataset.path = "";
  }

  // =========================================================================== wiring
  function mount() {
    if (!MK.$ || !MK.el || !document.getElementById("review-scroll")) return;  // not the IDE page
    mountPreview();
    if (MK.hunkMenu) MK.hunkMenu.add(blameItem, 10);
    const diff = MK.$("diff");
    if (diff) new MutationObserver(decorate).observe(diff, { childList: true, subtree: true });
    if (MK.onViewing) MK.onViewing.push(() => { const d = MK.$("diff"); if (d) { for (const b of d.querySelectorAll(".rv-md-bar")) b.remove(); for (const s of d.querySelectorAll("section.hunk")) delete s.dataset.rvSeen; } decorate(); });
  }
  window.mokataIDE.register({
    id: "review",
    commands: [
      { id: "review.specdiff", title: "Review: rendered spec diff of the run", run: async () => { if (!rv.run) { const r = await MK.api("/api/runs"); rv.run = r && r.default; } openSpecDiff(rv.run); } },
      { id: "review.preview", title: "Markdown: preview the open file", run: () => { if (prevBtn && !prevBtn.hidden) togglePreview(); } },
    ],
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();
})();
