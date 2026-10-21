// mokata ide — the plan + spec of a run, and its lifecycle (0.0.23 C5).
// Plan = the run's approved approach (brainstorm hand-off: approach, decisions, answered questions).
// Spec = the emitted spec (criteria, anchors, per-criterion test record, scope, version history).
// Lifecycle = the run's stage arc from recorded evidence, with gate blocks and amendments on it.
// Read-only. Every piece of repo/run text goes in through textContent (mdLite builds elements, never
// markup). The team source is labelled as such, and an unreachable team store is said, not hidden.
"use strict";

(function () {
  const MK = window.MK;
  const $ = MK.$;
  const el = MK.el;
  const P = { source: "local", runs: null, run: null, pinned: false, version: 0, team: null,
              gen: 0, lcGen: 0, lc: null, plan: null, file: null, files: null };
  MK.plan = P;

  // --- mdLite ---
  // A deliberately tiny Markdown subset for run text: paragraphs, "- " bullets, "#" headings,
  // **bold** and `code`. It creates elements and text nodes only — there is no markup path.
  function mdInline(parent, text) {
    const re = /(\*\*([^*]+)\*\*|`([^`]+)`)/g;
    let last = 0;
    let m;
    while ((m = re.exec(text))) {
      if (m.index > last) parent.append(document.createTextNode(text.slice(last, m.index)));
      parent.append(m[2] !== undefined ? el("b", "", m[2]) : el("code", "", m[3]));
      last = re.lastIndex;
    }
    if (last < text.length) parent.append(document.createTextNode(text.slice(last)));
    return parent;
  }
  function mdLite(text) {
    const frag = document.createDocumentFragment();
    const blocks = String(text || "").replace(/\r\n/g, "\n").split(/\n{2,}/);
    for (const block of blocks) {
      const lines = block.split("\n").filter((l) => l.trim());
      let para = [];
      let list = null;
      const flush = () => {
        if (para.length) { frag.append(mdInline(el("p", "md-p"), para.join(" "))); para = []; }
      };
      for (const line of lines) {
        if (/^\s*[-*]\s+/.test(line)) {
          flush();
          if (!list) { list = el("ul", "md-ul"); frag.append(list); }
          list.append(mdInline(el("li", ""), line.replace(/^\s*[-*]\s+/, "")));
        } else if (/^#{1,6}\s/.test(line)) {
          flush(); list = null;
          frag.append(mdInline(el("h4", "md-h"), line.replace(/^#+\s*/, "")));
        } else {
          list = null;
          para.push(line.trim());
        }
      }
      flush();
    }
    return frag;
  }
  // --- end mdLite ---
  MK.mdLite = mdLite;

  const fmtT = (t) => (t ? new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "");
  const fmtIso = (s) => {
    if (!s) return "";
    const t = Date.parse(s);
    return Number.isNaN(t) ? String(s) : fmtT(t / 1000);
  };
  const chip = (text, cls) => el("span", "chip" + (cls ? " " + cls : ""), text);

  // ------------------------------------------------------------------------------- which run
  function focusRun() {
    const ids = ((P.runs && P.runs.runs) || []).map((r) => r.id);
    const ui = MK.ui;
    const row = ((ui.state && ui.state.sessions) || []).find((s) => s.id === ui.focus);
    if (row && row.run && ids.includes(row.run)) return row.run;
    if (P.runs && P.runs.default && ids.includes(P.runs.default)) return P.runs.default;
    return ids[0] || null;
  }

  async function loadRuns() {
    const res = await MK.api("/api/runs?source=" + encodeURIComponent(P.source)).catch(() => null);
    MK.markSample(res);
    P.runs = res && !res.error ? res : { runs: [], error: (res && (res.note || res.error)) || "unavailable" };
    if (res && res.team) P.team = res.team;
    if (!P.pinned || !(P.runs.runs || []).some((r) => r.id === P.run)) { P.run = focusRun(); P.pinned = false; P.version = 0; }
    renderRunChooser();
  }

  function renderRunChooser() {
    const rows = (P.runs && P.runs.runs) || [];
    MK.combo("plan-run").setOptions(rows.map((r) => ({
      value: r.id, label: r.title ? `${r.title}` : `run ${r.short}`,
      sub: [r.short, r.author ? "by " + r.author : "", `plan ${r.approach}`, `spec ${r.spec}`,
            (r.sessions || []).length ? `${r.sessions.length} session(s)` : ""].filter(Boolean).join(" · "),
    })), P.run || "");
    const c = P.runs && P.runs.counts;
    $("plan-run-note").textContent = c && c.total
      ? `${c.listed < c.total ? `Showing ${c.listed} of ${c.total} runs` : `${c.total} run(s)`} — ${c.with_spec} with a spec, ` +
        `${c.with_plan} with a plan. Runs with a spec are listed first, then runs with a plan.` : "";
    const teamBtn = $("plan-team-btn");
    teamBtn.hidden = !(P.team && P.team.configured) && P.source !== "team";
    for (const b of document.querySelectorAll("button.pill[data-psource]")) b.classList.toggle("active", b.dataset.psource === P.source);
    const note = $("plan-source-note");
    if (P.source === "team") note.textContent = "Read from the team store (shared session bundles).";
    else if (P.team && P.team.unknown) note.textContent = "Team mode unknown: " + (P.team.note || "");
    else note.textContent = P.team && P.team.configured ? "Local state of this checkout. Switch to Team for the shared store."
      : "Local state of this checkout (not team-connected).";
  }

  // ------------------------------------------------------------------------------- plan + spec
  async function loadPlan() {
    const gen = ++P.gen;
    showFile(false);
    const box = $("plan-state");
    if (P.runs && P.runs.state === "team-unreachable") { showState(P.runs.note, "unreachable"); clearPlan(); return; }
    if (!P.run) {
      showState(P.runs && P.runs.error ? `could not list runs: ${P.runs.error}`
        : "No run yet. A run appears here once a brainstorm registers it (/mokata:brainstorm); its plan once the approach is approved, its spec once it is emitted.", "");
      clearPlan();
      return;
    }
    const q = `/api/plan?run=${encodeURIComponent(P.run)}&source=${encodeURIComponent(P.source)}${P.version ? "&version=" + P.version : ""}`;
    const res = await MK.api(q).catch((e) => ({ error: "fetch", note: String(e) }));
    if (gen !== P.gen) return;
    MK.markSample(res);
    P.plan = res;
    box.hidden = true;
    if (res.error) { showState(`could not read this run: ${res.note || res.error}`, ""); clearPlan(); return; }
    if (res.state === "team-unreachable") { showState(res.note, "unreachable"); clearPlan(); return; }
    renderPlan(res);
  }

  function showState(text, kind) {
    const box = $("plan-state");
    box.hidden = false;
    box.className = "empty big" + (kind ? " " + kind : "");
    box.replaceChildren();
    if (kind === "unreachable") box.append(el("b", "", "Team store unreachable"), el("br"));
    box.append(document.createTextNode(text || ""));
  }
  function clearPlan() {
    $("plan-approach").replaceChildren();
    $("plan-spec").replaceChildren();
    $("plan-versions").replaceChildren();
    $("plan-source-badge").textContent = P.source === "team" ? "team store" : "local";
  }

  function section(box, title, meta) {
    const h = el("div", "plan-h");
    h.append(el("h2", "", title));
    if (meta) h.append(el("span", "dim small", meta));
    box.append(h);
  }

  function renderPlan(res) {
    $("plan-title").textContent = `run ${String(res.run).slice(0, 8)}`;
    $("plan-source-badge").textContent = res.sample ? "SAMPLE" : res.label || res.source;
    $("plan-source-badge").className = "pill-static" + (res.source === "team" ? " team" : "");
    renderApproach(res.approach || {});
    renderSpec(res.spec || {}, res);
    renderVersions(res);
  }

  function renderApproach(a) {
    const box = $("plan-approach");
    box.replaceChildren();
    section(box, "Plan — the approved approach", a.state === "ok"
      ? `approved by ${a.approver || "?"}${a.approved_at ? " · " + fmtIso(a.approved_at) : ""}` : "");
    if (a.state !== "ok") { box.append(el("p", "empty", a.note || "no approved approach")); return; }
    if (a.topic) box.append(el("p", "plan-topic", a.topic));
    const card = el("div", "plan-card");
    card.append(el("h3", "", a.name || "(unnamed approach)"));
    const sum = el("div", "md");
    sum.append(mdLite(a.summary));
    card.append(sum);
    if ((a.pros || []).length || (a.cons || []).length) {
      const pc = el("div", "pros-cons");
      for (const [k, items, cls] of [["Pros", a.pros, "pro"], ["Cons", a.cons, "con"]]) {
        if (!(items || []).length) continue;
        const col = el("div", "pc-col " + cls);
        col.append(el("h4", "", k));
        const ul = el("ul", "md-ul");
        for (const x of items) ul.append(mdInline(el("li", ""), x));
        col.append(ul);
        pc.append(col);
      }
      card.append(pc);
    }
    if ((a.targets || []).length) {
      const t = el("div", "chips");
      t.append(el("span", "dim small", "touches"));
      for (const x of a.targets) t.append(chip(x, "mono"));
      card.append(t);
    }
    if ((a.domains || []).length) {
      const t = el("div", "chips");
      t.append(el("span", "dim small", "domains"));
      for (const x of a.domains) t.append(chip(x));
      card.append(t);
    }
    box.append(card);
    const files = (P.plan && P.plan.plan_files) || [];
    if (files.length) {
      const t = el("div", "chips");
      t.append(el("span", "dim small", "saved as"));
      for (const f of files) {
        const b = el("button", "chip mono linkish", f.path);
        b.type = "button";
        b.title = "open the saved plan file";
        b.onclick = () => openFile(f.slug, f.where);
        t.append(b);
      }
      box.append(t);
    }
    if ((a.decisions || []).length) {
      box.append(el("h3", "plan-sub", `Decisions (${a.decisions.length})`));
      const ul = el("ul", "dec-list");
      for (const d of a.decisions) {
        const li = el("li", "dec");
        const head = el("div", "dec-head");
        head.append(el("span", "ac-id mono", d.id || "—"));
        head.append(mdInline(el("span", "dec-st"), d.statement || ""));
        li.append(head);
        const meta = el("div", "chips");
        if (d.rationale) meta.append(el("span", "dim small", "because " + d.rationale));
        for (const c of d.about_code || []) meta.append(chip(c, "mono"));
        for (const x of d.deferred || []) meta.append(chip("defers " + (x.item || x.id), "amber"));
        if (meta.childNodes.length) li.append(meta);
        ul.append(li);
      }
      box.append(ul);
    }
    if ((a.questions || []).length) {
      box.append(el("h3", "plan-sub", `Questions answered (${a.questions.length})`));
      const dl = el("dl", "qa");
      for (const q of a.questions) {
        dl.append(mdInline(el("dt", ""), q.text || ""));
        dl.append(mdInline(el("dd", ""), q.answer || "—"));
      }
      box.append(dl);
    }
  }

  const TEST_LABEL = { passing: "test passing", failing: "failing test on record", none: "no test yet" };

  function renderSpec(s, res) {
    const box = $("plan-spec");
    box.replaceChildren();
    const vline = s.state === "ok" ? (s.superseded ? `v${s.version} — superseded (current is v${s.current_version})` : `v${s.version} (current)`) : "";
    section(box, "Spec", vline);
    if (res.amendment && res.amendment.open) {
      box.append(el("p", "warn plan-banner", `An amendment is open (v${res.amendment.from_version} → v${res.amendment.to_version}${res.amendment.reason ? ": " + res.amendment.reason : ""}) — the run is back at SPEC and development writes are blocked.`));
    }
    if (s.state !== "ok") {
      const drafts = (res && res.pending_spec) || [];
      if (drafts.length) { renderDrafts(box, drafts); return; }
      box.append(el("p", s.state === "malformed" ? "note" : "empty", s.note || "no spec"));
      if (s.state !== "malformed" && res && res.source === "local") {
        box.append(el("p", "small dim",
          "A spec shows here once it is committed to this run. `spec_emit` first only PROPOSES it: " +
          "nothing is saved until you run `mokata approve <id>` and the agent re-sends it with that id. " +
          "A spec.json or spec file the agent wrote in the repo is a draft, not this run's spec."));
      }
      return;
    }
    if (s.superseded) box.append(el("p", "warn plan-banner", `You are reading v${s.version}, a superseded spec. The run's current spec is v${s.current_version}.`));
    const card = el("div", "plan-card");
    card.append(mdInline(el("h3", ""), s.title || "(untitled spec)"));
    const meta = el("div", "chips");
    if (s.approach) meta.append(el("span", "dim small", "approach"), chip(s.approach));
    for (const dm of s.domains || []) meta.append(chip(dm));
    if (meta.childNodes.length) card.append(meta);
    box.append(card);
    box.append(el("h3", "plan-sub", `Acceptance criteria (${(s.criteria || []).length})`));
    const ul = el("ul", "ac-list");
    for (const c of s.criteria || []) {
      const li = el("li", "ac");
      const head = el("div", "ac-head");
      head.append(el("span", "ac-id mono", c.id));
      const txt = el("div", "ac-text md");
      txt.append(mdLite(c.text));
      head.append(txt);
      if (c.test) head.append(el("span", "ac-test " + c.test, TEST_LABEL[c.test] || c.test));
      li.append(head);
      if ((c.anchors || []).length) {
        const an = el("ul", "anchors");
        for (const a of c.anchors) {
          const ai = el("li", "mono small");
          ai.append(el("span", "kind", a.kind || "anchor"), document.createTextNode(" " + (a.ref || a.path || a.symbol || JSON.stringify(a))));
          if (a.source) ai.append(el("span", "dim", "  · " + a.source));
          if (a.confidence) ai.append(el("span", "dim", " · " + a.confidence));
          an.append(ai);
        }
        li.append(an);
      }
      ul.append(li);
    }
    box.append(ul);
    if (s.tests === null || s.tests === undefined) {
      box.append(el("p", "small dim", res.source === "team" ? "Test records are not carried in the team bundle — they stay in the author's local state." : ""));
    } else {
      box.append(el("p", "small dim", `Test record (TDD): a failing test was recorded for ${s.tests.failing.length} criterion/criteria; ${s.tests.passing.length} now pass.`));
    }
    if (s.scope) {
      box.append(el("h3", "plan-sub", "Scope"));
      const sc = el("div", "plan-card");
      const auth = el("div", "chips");
      auth.append(el("span", "dim small", "authorized"));
      for (const g of s.scope.authorized || []) auth.append(chip(g, "mono"));
      if (!(s.scope.authorized || []).length) auth.append(el("span", "dim small", "— none"));
      sc.append(auth);
      if ((s.scope.deferred || []).length) {
        const d = el("ul", "md-ul");
        for (const x of s.scope.deferred) d.append(el("li", "", `deferred: ${x.item || x.id}${(x.paths || []).length ? " (" + x.paths.join(", ") + ")" : ""}`));
        sc.append(d);
      }
      box.append(sc);
    }
  }

  function renderDrafts(box, drafts) {
    for (const d of drafts) {
      const card = el("div", "plan-card");
      const head = el("p", "warn plan-banner");
      head.append(el("b", "", d.status === "approved" ? "Spec approved — waiting for the agent to commit it. "
        : "Spec drafted — waiting for your approval. "));
      head.append(document.createTextNode(d.status === "approved"
        ? "Ask the agent to re-send spec_emit with this proposal id."
        : "Run this in a terminal, then tell the agent:"));
      card.append(head);
      if (d.status !== "approved") card.append(el("pre", "mono cmd", d.approve));
      card.append(el("p", "small dim", `${d.tool} · proposal ${d.proposal_id} · expires in ${Math.max(1, Math.round(d.expires_in / 60))} min`
        + " — an unapproved draft expires and is never saved."));
      if (d.summary) card.append(mdInline(el("p", ""), d.summary));
      if (d.preview) card.append(el("pre", "mono small preview", d.preview));
      box.append(card);
    }
  }

  // ------------------------------------------------------------------------------- saved plan files
  async function loadFiles() {
    const res = await MK.api("/api/saved-plans").catch(() => null);
    P.files = res && !res.error ? res : { plans: [], error: (res && (res.note || res.error)) || "unavailable" };
    const ul = $("plan-files");
    ul.replaceChildren();
    for (const f of P.files.plans || []) {
      const b = el("button", "item" + (P.file && P.file.slug === f.slug && P.file.where === f.where ? " active" : ""));
      b.type = "button";
      b.append(el("span", "", f.slug),
               el("span", "sub", `${f.where === "exported" ? "in your repo" : "mokata's copy"} · ${f.path} · ${fmtT(f.updated)}`));
      b.onclick = () => openFile(f.slug, f.where);
      ul.append(MK.wrapLi(b));
    }
    const dirs = P.files.dirs || {};
    $("plan-files-note").textContent = P.files.error ? `could not list plan files: ${P.files.error}`
      : (P.files.plans || []).length ? "Saved at approval; `mokata plan export` copies one into your repo."
      : `No saved plan files yet (${dirs.internal || ".mokata/temp_local/plans"}, ${dirs.exported || "plans"}).`;
  }

  function showFile(on) {
    $("plan-file").hidden = !on;
    $("plan-approach").hidden = on;
    $("plan-spec").hidden = on;
    if (!on) P.file = null;
  }

  async function openFile(slug, where) {
    if (MK.ui.mode !== "plan") MK.setMode("plan");
    const res = await MK.api(`/api/saved-plan?plan=${encodeURIComponent(slug)}&where=${encodeURIComponent(where)}`)
      .catch((e) => ({ error: "fetch", note: String(e) }));
    MK.markSample(res);
    $("plan-state").hidden = true;
    const box = $("plan-file");
    box.replaceChildren();
    showFile(true);
    P.file = { slug, where };
    $("plan-title").textContent = res.path || slug;
    $("plan-source-badge").textContent = res.sample ? "SAMPLE" : where === "exported" ? "plan file · your repo" : "plan file · mokata";
    if (res.error) { box.append(el("p", "empty", `could not read this plan: ${res.note || res.error}`)); return; }
    section(box, "Saved plan", `${res.path} · ${fmtT(res.updated)}`);
    const back = el("button", "pill", "← back to the run");
    back.type = "button";
    back.onclick = () => loadPlan();
    box.append(back);
    const md = el("div", "md plan-card");
    md.append(mdLite(res.text));
    box.append(md);
    if (res.truncated) box.append(el("p", "small dim", "Long file — the first 256 KB is shown."));
    for (const b of document.querySelectorAll("#plan-files button.item")) b.classList.remove("active");
    loadFiles();
  }
  MK.openPlanFile = openFile;

  function renderVersions(res) {
    const rows = res.versions || [];
    MK.combo("plan-version").setOptions(rows.slice().reverse().map((v) => ({
      value: String(v.version), label: `v${v.version}${v.current ? " (current)" : ""}`,
      sub: v.malformed ? "MALFORMED — present but unreadable" : v.present ? `${v.title || ""} · ${v.ac_count} AC(s)` : "not archived",
    })), String((res.spec && res.spec.version) || ""));
    const ul = $("plan-versions");
    ul.replaceChildren();
    for (const v of rows.slice().reverse()) {
      const b = el("button", "item" + ((res.spec && res.spec.version) === v.version ? " active" : ""));
      b.type = "button";
      b.disabled = !v.present || v.malformed;
      b.append(el("span", "", `v${v.version}${v.current ? " · current" : ""}`),
               el("span", "sub", v.malformed ? "present but unreadable" : v.present ? `${v.title} · ${v.ac_count} AC(s)` : "not archived"));
      b.onclick = () => { P.version = v.current ? 0 : v.version; loadPlan(); };
      ul.append(MK.wrapLi(b));
    }
    $("plan-versions-note").textContent = res.versions_note || (rows.length ? "" : "no spec emitted on this run yet");
  }

  // ------------------------------------------------------------------------------- lifecycle
  const GLYPH = { done: "✓", current: "▶", pending: "○" };

  async function loadLifecycle() {
    const gen = ++P.lcGen;
    // The lifecycle is the LOCAL progress log's; while the team source is shown, the strip keeps
    // the last local run's arc rather than guess a team run's.
    if (P.source !== "local") return;
    const run = P.run;
    if (!run) { P.lc = null; renderStrip(); renderDetail(); return; }
    const res = await MK.api("/api/lifecycle?run=" + encodeURIComponent(run)).catch(() => null);
    if (gen !== P.lcGen) return;
    MK.markSample(res);
    P.lc = res && !res.error ? res : null;
    renderStrip();
    renderDetail();
  }

  function stripVisible() { return ["review", "navigate"].includes(MK.ui.mode); }

  function renderStrip() {
    const box = $("lifecycle");
    const lc = P.lc;
    box.hidden = !stripVisible() || !lc;
    if (!lc) return;
    const row = ((P.runs && P.runs.runs) || []).find((r) => r.id === lc.run);
    $("lc-run").textContent = `run ${String(lc.run).slice(0, 8)}${row && row.title ? " · " + row.title : ""}`;
    const ol = $("lc-stages");
    ol.replaceChildren();
    for (const s of lc.stages) {
      const li = el("li", "lc-stage " + s.status);
      li.append(el("span", "lc-glyph", GLYPH[s.status] || "○"), el("span", "lc-name", s.stage));
      if (s.blocks) { const b = el("span", "lc-flag block", `⛔${s.blocks}`); b.title = `${s.blocks} gate block(s)`; li.append(b); }
      if (s.amendments) { const b = el("span", "lc-flag amend", `↺${s.amendments}`); b.title = `${s.amendments} amendment(s)`; li.append(b); }
      li.title = `${s.stage}: ${s.status}${s.entered ? " · entered " + fmtIso(s.entered) : ""}${s.evidence === "implied" ? " · no record of its own (implied by a later stage)" : ""}`;
      li.setAttribute("aria-label", li.title);
      ol.append(li);
    }
    const blocks = lc.marks.filter((m) => m.kind === "gate_block").length;
    const parts = [];
    if (lc.started) parts.push(`started ${fmtT(lc.started)}${lc.started_basis ? " (" + lc.started_basis + ")" : ""}`);
    if (lc.finished) parts.push(`finished ${fmtIso(lc.finished_at)}`);
    else if (lc.regressed) parts.push("regressed to spec (amendment open)");
    else if (lc.current) parts.push(`now: ${lc.current}`);
    if (blocks) parts.push(`${blocks} block(s)`);
    $("lc-meta").textContent = parts.join(" · ");
  }

  function renderDetail() {
    const ol = $("lc-detail");
    ol.replaceChildren();
    const lc = P.lc;
    $("lc-detail-empty").hidden = !!lc;
    $("lc-detail-run").textContent = lc ? `run ${String(lc.run).slice(0, 8)}` : "";
    if (!lc) return;
    for (const s of lc.stages) {
      const li = el("li", "lcd " + s.status);
      const head = el("div", "lcd-head");
      head.append(el("span", "lc-glyph", GLYPH[s.status] || "○"), el("b", "", s.stage),
                  el("span", "dim small", s.status + (s.evidence === "implied" ? " (implied)" : "")));
      if (s.entered) head.append(el("span", "mono small dim", fmtIso(s.entered)));
      li.append(head);
      const marks = lc.marks.filter((m) => m.stage === s.stage).slice(-50);
      if (marks.length) {
        const ul = el("ul", "lcd-marks");
        for (const m of marks) {
          const mi = el("li", "lcd-mark " + m.kind);
          mi.append(el("span", "mono small dim", m.t ? fmtT(m.t) : (m.ts || "")), el("span", "", " " + m.label));
          if (m.reason) mi.append(el("span", "dim small", ` — ${m.reason}`));
          ul.append(mi);
        }
        li.append(ul);
      }
      ol.append(li);
    }
  }

  // ------------------------------------------------------------------------------- hooks
  async function refreshAll() {
    await loadRuns();
    loadFiles();
    await loadLifecycle();
    if (MK.ui.mode === "plan") await loadPlan();
  }
  MK.onRefreshPlan = () => { refreshAll(); };
  MK.onModePlan = (mode) => {
    renderStrip();
    if (mode === "plan") { loadFiles(); if (!P.file) loadPlan(); }
  };
  MK.openPlan = (run) => {
    if (run) { P.run = run; P.pinned = true; P.version = 0; }
    MK.setMode("plan");
  };

  MK.wirePlan = function wirePlan() {
    $("plan-run").onchange = () => {
      P.run = $("plan-run").value || null; P.pinned = true; P.version = 0;
      loadPlan(); loadLifecycle();
    };
    $("plan-version").onchange = () => {
      const v = +$("plan-version").value;
      const cur = ((P.plan && P.plan.versions) || []).find((x) => x.current);
      P.version = cur && cur.version === v ? 0 : v;
      loadPlan();
    };
    for (const b of document.querySelectorAll("button.pill[data-psource]")) {
      b.onclick = async () => {
        if (P.source === b.dataset.psource) return;
        P.source = b.dataset.psource; P.pinned = false; P.version = 0;
        await loadRuns(); loadPlan(); loadLifecycle();
      };
    }
    $("lc-run").onclick = () => MK.openPlan(P.lc && P.lc.run);
    $("lc-more").onclick = () => MK.setPanel("sessions", true);
  };
})();
