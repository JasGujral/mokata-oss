// mokata ide — Proof (0.0.27 P13, ID-VIS-PROOF; ruling U15: the launch demo). The AC → test → code
// trace of a change set, drawn as a three-column flow in hand-rolled SVG (no library, no CDN):
//   left   every acceptance criterion of the run's spec
//   middle the tests recorded for it, each with its RED-then-GREEN proof
//   right  the changed definitions those tests exercise, with their blast radius
// Edges carry their basis (recorded / planned from the run's record; structural / lexical from the
// graph; anchor from the spec). The RISK overlay (toggles) tints what is not proven: an AC with no
// test, a test that never went red ("not TDD-proven"), code no test reaches, gates red or skipped,
// and fan-in. Clicking a test or a definition opens it in the Code view (MK.openFile).
// Data: GET /api/proof (a job on a wide change set — MK.api follows it) and /api/proof/targets.
// Scale: the server pages rows (page.next) and never caps; when it reduced the picture
// (draw.reduced) the view SAYS so and offers "show all" — the same paged data as a table.
// SAFETY: like every view, repo text reaches the DOM through textContent only.
"use strict";

(function () {
  const MK = window.MK;

  // ------------------------------------------------------------------------ pure layout
  // layout(trace, width, height) -> {nodes: [{id, col, x, y, w, h, kind, row}], edges: [{source,
  // target, kind, basis, x1, y1, x2, y2}], hidden} — three columns, nodes stacked by the server's
  // order (riskiest first), only `draw.shown` nodes placed; the rest counted in `hidden`.
  function layout(t, width, height) {
    const shown = new Set((t.draw && t.draw.shown) || []);
    const cols = [t.nodes.acs || [], t.nodes.tests || [], t.nodes.code || []];
    const kinds = ["ac", "test", "code"];
    const pad = 16, colW = Math.max(120, Math.floor((width - pad * 4) / 3)), rowH = 26, gap = 6;
    const nodes = [], pos = {};
    let hidden = 0;
    cols.forEach((list, ci) => {
      let y = pad;
      for (const n of list) {
        if (!shown.has(n.id)) { hidden++; continue; }
        const node = { id: n.id, col: ci, kind: kinds[ci], x: pad + ci * (colW + pad), y, w: colW,
                       h: rowH - gap, row: n };
        nodes.push(node);
        pos[n.id] = node;
        y += rowH;
      }
    });
    const edges = [];
    for (const e of t.edges || []) {
      const a = pos[e.source], b = pos[e.target];
      if (!a || !b) continue;
      const left = a.col <= b.col ? a : b, right = left === a ? b : a;
      edges.push({ source: e.source, target: e.target, kind: e.kind, basis: e.basis,
                   x1: left.x + left.w, y1: left.y + left.h / 2, x2: right.x, y2: right.y + right.h / 2 });
    }
    const tall = Math.max(height, ...nodes.map((n) => n.y + n.h + pad));
    return { nodes, edges, hidden, width, height: tall };
  }

  // The risk class a node wears when the overlay is on (empty = proven / covered).
  function riskClass(n, over) {
    if (n.kind === "ac") {
      if (over.untested && (n.row.status === "untested" || n.row.status === "ambiguous")) return "pf-risk-untested";
      if (over.untested && n.row.status === "red") return "pf-risk-red";
      if (over.tdd && n.row.status === "green-not-tdd") return "pf-risk-tdd";
      if (over.tdd && n.row.status === "unrun") return "pf-risk-tdd";
    } else if (n.kind === "test") {
      if (over.untested && n.row.state === "deleted") return "pf-risk-untested";
      if (over.untested && n.row.state !== "present") return "pf-risk-unknown";
      if (over.tdd && (n.row.tdd === "green-only" || n.row.tdd === "unrecorded")) return "pf-risk-tdd";
      if (over.tdd && n.row.tdd === "red") return "pf-risk-red";
    } else if (n.kind === "code") {
      if (over.untested && n.row.coverage === "untested") return "pf-risk-untested";
      if (over.untested && (n.row.coverage === "unknown" || n.row.coverage === "pending")) return "pf-risk-unknown";
    }
    return "";
  }

  function tddWord(t) {
    const w = { proven: "RED → GREEN (TDD-proven; latest run green)", "green-only": "GREEN only — not TDD-proven",
                red: "RED (latest run failing)", unrecorded: "no run recorded — not TDD-proven",
                deleted: "DELETED — the record names a test the tree no longer holds; proves nothing",
                ambiguous: "AMBIGUOUS — proves nothing", unlocated: "not located in this tree; proves nothing",
                unparsed: "UNPARSED — its file has no outline mokata can read; proves nothing",
                ignored: "IGNORED — its file is gitignored / not tracked in the tree judged; proves nothing" }[t.tdd] || t.tdd;
    const why = t.reason && t.state !== "present" ? ` (${t.reason})` : "";
    const params = t.params && t.params.length ? ` · params: ${t.params.join(", ")}` : "";
    const note = t.tdd_note ? ` — ${t.tdd_note}` : "";
    const from = t.qualified_from && t.qualified_from.length ? ` · recorded as ${t.qualified_from.join(", ")} (qualified against the run's own test files)` : "";
    return w + why + note + params + from + (t.basis === "sets" ? " · from the phase sets (recorded before timed history; order not known)" : "");
  }
  function ambiguousWord(t) {
    return t.ambiguous && t.ambiguous.length
      ? `AMBIGUOUS — ${t.ambiguous.length} definitions answer to this id: ${t.ambiguous.map((id) => id.slice(5)).join(", ")}; record the full id path::Class::test to disambiguate`
      : "";
  }
  function acWord(a) {
    return ({ proven: "proven", "green-not-tdd": "green, not TDD-proven", red: "red (a test's latest run failed)",
             unrun: "mapped, never run", untested: "untested — no usable test",
             ambiguous: "only an ambiguous test id — not proven" }[a.status] || a.status) + (a.note ? ` (${a.note})` : "");
  }
  function covWord(c) {
    return { covered: "covered", untested: "untested — no test reaches it",
             unknown: "coverage unknown (lexical answer or a cut radius)",
             pending: "blast radius on request", test: "a test" }[c.coverage] || c.coverage;
  }
  function when(ts) {
    if (!ts) return "—";
    try { return new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 19); } catch (_e) { return String(ts); }
  }

  MK.proof = { layout, riskClass, tddWord, acWord, covWord, ambiguousWord };
  if (typeof document === "undefined" || typeof document.createElementNS !== "function") return;

  // ------------------------------------------------------------------------ the view
  const el = MK.el;
  // The SVG namespace comes from the core (sessions.js exports it) — a feature file carries no URL.
  const SVG = MK.svgNS || new DOMParser().parseFromString("<svg/>", "image/svg+xml").documentElement.namespaceURI;
  let viewEl = null, noteEl = null, pickEl = null, svgHost = null, tableHost = null,
      legendEl = null, riskEl = null, summaryEl = null, pagerEl = null, testsHost = null;
  let targets = null, trace = null, query = null, abort = null;
  const over = { untested: true, tdd: true, blast: true, gates: true };
  let showAll = false;

  function svg(tag, attrs) {
    const n = document.createElementNS(SVG, tag);
    for (const k in attrs) n.setAttribute(k, String(attrs[k]));
    return n;
  }
  function toast(text) { if (noteEl) noteEl.textContent = text || ""; }
  function button(label, title, fn, cls) {
    const b = el("button", cls || "pf-btn", label);
    b.type = "button"; b.title = title || label;
    b.addEventListener("click", fn);
    return b;
  }

  async function loadTargets() {
    try { targets = await MK.api("/api/proof/targets"); }
    catch (e) { targets = { error: "fetch", note: String(e) }; }
    MK.markSample(targets);
    renderPicker();
    if (!query && targets && targets.default) { query = Object.assign({}, targets.default); load(); }
    else if (!query) {
      MK.regionSay(viewEl, "empty", (targets && targets.note) || "nothing to trace yet",
                   { cli: "mokata ide" });
    }
  }

  function qs(q) {
    return Object.keys(q).filter((k) => q[k] !== undefined && q[k] !== null && q[k] !== "")
      .map((k) => encodeURIComponent(k) + "=" + encodeURIComponent(q[k])).join("&");
  }

  async function load() {
    if (!query) return;
    if (abort) abort.abort();
    abort = new AbortController();
    toast("tracing…");
    let d;
    try { d = await MK.api("/api/proof?" + qs(query), undefined, { signal: abort.signal }); }
    catch (e) { if (e && e.name === "AbortError") return; d = { error: "fetch", note: String(e) }; }
    MK.markSample(d);
    if (!d || d.error) {
      toast("");
      svgHost.replaceChildren(); tableHost.replaceChildren();
      MK.regionSay(svgHost, "error", MK.whyFailed(d, "build the trace"), { retry: load, cli: "mokata ide" });
      return;
    }
    trace = d;
    showAll = false;
    toast("");
    render();
  }

  // --- the picker (the "What to prove" disclosure at the top of the view): runs with their AC count, sessions, a commit range -------------
  function renderPicker() {
    if (!pickEl) return;
    pickEl.replaceChildren();
    if (!targets || targets.error) { pickEl.append(el("p", "pf-note", MK.whyFailed(targets, "list the targets"))); return; }
    const runs = el("ul", "pf-list");
    for (const r of targets.runs || []) {
      const li = el("li", "pf-li", `run ${r.run} · ${r.acs} AC${r.acs === 1 ? "" : "s"}`);
      li.title = r.note || `trace run ${r.run}`;
      li.addEventListener("click", () => { query = { run: r.run }; renderPicker(); load(); });
      if (query && query.run === r.run && !query.session) li.classList.add("active");
      runs.append(li);
    }
    pickEl.append(el("h3", "", "Runs"), runs.childElementCount ? runs : el("p", "pf-note", "no run has a spec here yet"));
    const sess = el("ul", "pf-list");
    for (const s of (targets.sessions || []).slice(0, 50)) {
      if (!s.files) continue;
      const li = el("li", "pf-li", `${s.session.slice(0, 8)} · ${s.files} file(s)` + (s.run ? ` · run ${String(s.run).slice(0, 10)}` : ""));
      li.title = `trace what session ${s.session} changed`;
      li.addEventListener("click", () => { query = { session: s.session }; renderPicker(); load(); });
      if (query && query.session === s.session) li.classList.add("active");
      sess.append(li);
    }
    if (sess.childElementCount) pickEl.append(el("h3", "", "Sessions"), sess);
    if (targets.git) {
      const live = el("ul", "pf-list");
      const li = el("li", "pf-li", "working tree · uncommitted changes");
      li.title = "trace what is changed in the working tree against HEAD";
      li.addEventListener("click", () => { query = { live: "1" }; if (targets.runs && targets.runs.length) query.run = targets.runs[0].run; renderPicker(); load(); });
      if (query && query.live) li.classList.add("active");
      live.append(li);
      pickEl.append(el("h3", "", "Working tree"), live);
      const form = el("div", "pf-range");
      const base = el("input", "pf-in"); base.placeholder = "base (e.g. main)"; base.value = (query && query.base) || "";
      const head = el("input", "pf-in"); head.placeholder = "head (e.g. HEAD)"; head.value = (query && query.head) || "";
      form.append(el("h3", "", "Commit range"), base, head,
        button("Trace range", "Trace base...head (what head changed since it left base)", () => {
          if (!base.value.trim() || !head.value.trim()) { toast("name both base and head"); return; }
          query = { base: base.value.trim(), head: head.value.trim() };
          if (targets.runs && targets.runs.length) query.run = targets.runs[0].run;
          renderPicker(); load();
        }));
      pickEl.append(form);
    }
    if (targets.note) pickEl.append(el("p", "pf-note", targets.note));
  }

  // --- the centre: summary, toggles, the flow, the table -----------------------------------------
  function render() {
    if (!trace) return;
    const t = trace, r = t.risk;
    summaryEl.replaceChildren();
    const title = el("h2", "", `Proof · ${t.target.title}`);
    summaryEl.append(title);
    const facts = el("p", "pf-facts",
      `${t.page.total_acs} AC(s) · ${t.page.total_tests} test(s) · ${t.page.total_code} changed definition(s) · ` +
      `${t.files.length} file(s) · graph: ${t.graph.basis}${t.graph.backend ? " (" + t.graph.backend + ")" : ""}` +
      (t.record.last_suite ? ` · ${t.record.last_suite.said || "the last test run cannot be read — re-run the tests"} (${t.record.last_suite.command || "?"})` : " · no suite run recorded"));
    summaryEl.append(facts);
    const chips = el("div", "pf-chips");
    chip(chips, `${r.untested_acs.length} untested AC`, r.untested_acs.length ? "bad" : "ok");
    chip(chips, `${r.red_acs.length} red AC`, r.red_acs.length ? "bad" : "ok");
    chip(chips, `${r.tests_not_tdd_proven.length} not TDD-proven`, r.tests_not_tdd_proven.length ? "warn" : "ok");
    if ((r.tests_ambiguous || []).length) chip(chips, `${r.tests_ambiguous.length} ambiguous test id(s)`, "warn");
    if ((r.tests_deleted || []).length) chip(chips, `${r.tests_deleted.length} deleted test(s) on record`, "bad");
    if ((r.tests_non_proving || []).length) chip(chips, `${r.tests_non_proving.length} recorded test(s) that cannot prove (ambiguous / unlocated / unparsed / ignored / deleted)`, "warn");
    if ((r.gates_unknown || []).length) chip(chips, `${r.gates_unknown.length} gate row(s) with no verdict`, "warn");
    chip(chips, `${r.code_without_test.length} changed without a test`, r.code_without_test.length ? "bad" : "ok");
    chip(chips, `${r.code_coverage_unknown.length} coverage unknown`, r.code_coverage_unknown.length ? "warn" : "ok");
    chip(chips, `${r.gates_red.length} gate(s) red`, r.gates_red.length ? "bad" : "ok");
    chip(chips, `${r.gates_skipped.length} gate(s) skipped`, r.gates_skipped.length ? "warn" : "ok");
    chip(chips, `blast fan-in ${r.blast.fan_in_total}` + (r.blast.cut ? ` (${r.blast.cut} cut)` : ""), "info");
    chip(chips, `risk score ${r.score}`, r.score ? "warn" : "ok");
    summaryEl.append(chips);
    if (t.note) summaryEl.append(el("p", "pf-note", t.note));

    riskEl.replaceChildren(el("span", "pf-lbl", "risk overlay:"));
    for (const [k, label] of [["untested", "untested"], ["tdd", "not TDD-proven"], ["blast", "blast radius"], ["gates", "gates"]]) {
      const lab = el("label", "pf-toggle");
      const cb = el("input"); cb.type = "checkbox"; cb.checked = over[k];
      cb.addEventListener("change", () => { over[k] = cb.checked; drawFlow(); drawTable(); });
      lab.append(cb, el("span", "", label));
      riskEl.append(lab);
    }
    drawFlow();
    drawTable();
    drawPager();
  }

  function chip(host, text, cls) { host.append(el("span", "pf-chip pf-" + cls, text)); }

  function openNode(n) {
    if (!MK.openFile) return;
    if (n.kind === "test" && n.row.path) MK.openFile(n.row.path, n.row.line || 1, n.row.symbol || null);
    else if (n.kind === "code" && n.row.path) MK.openFile(n.row.path, n.row.line || 1, n.row.symbol || null);
  }

  function nodeLabel(n) {
    if (n.kind === "ac") return `${n.row.ac} · ${n.row.text}`;
    if (n.kind === "test") return `${n.row.symbol || n.row.label}${n.row.state !== "present" ? " · " + n.row.state.toUpperCase() : n.row.path ? " · " + n.row.path : ""}`;
    return `${n.row.symbol} · ${n.row.path}` + (over.blast && n.row.fan_in ? ` · ${n.row.fan_in} caller(s)` : "");
  }
  function nodeTitle(n) {
    if (n.kind === "ac") return `${n.row.ac}: ${n.row.text}\n${acWord(n.row)} · ${n.row.tests.length} test(s) · ${n.row.code.length} definition(s)`;
    if (n.kind === "test") return `${n.row.label}\n${tddWord(n.row)}\nred ${when(n.row.red_at)} · green ${when(n.row.green_at)} (${n.row.basis})` + (ambiguousWord(n.row) ? "\n" + ambiguousWord(n.row) : "\nclick to open");
    const ap = (n.row.approvals || []).map((a) => `${a.decision} by ${a.actor || "?"}`).join(", ");
    return `${n.row.label} (${n.row.kind}, line ${n.row.line})\n${covWord(n.row)}\n` +
      `${n.row.fan_in} caller(s) · blast ${n.row.blast}${n.row.blast_cut ? " (cut)" : ""} · basis ${n.row.basis || "?"}` +
      (ap ? `\napprovals: ${ap}` : "\nno approval row names this file") + (n.row.note ? `\n${n.row.note}` : "") + "\nclick to open";
  }

  function drawFlow() {
    svgHost.replaceChildren();
    const width = Math.max(600, svgHost.clientWidth || 900);
    const L = layout(trace, width, 200);
    const s = svg("svg", { width, height: L.height, viewBox: `0 0 ${width} ${L.height}`, class: "pf-svg", role: "img" });
    s.setAttribute("aria-label", "AC to test to code trace");
    const heads = ["acceptance criteria", "tests", "changed code"];
    const colW = L.nodes.length ? L.nodes[0].w : Math.floor((width - 64) / 3);
    heads.forEach((h, i) => {
      const tx = svg("text", { x: 16 + i * (colW + 16), y: 12, class: "pf-head" });
      tx.textContent = h; s.append(tx);
    });
    for (const e of L.edges) {
      const mx = (e.x1 + e.x2) / 2;
      const p = svg("path", { d: `M${e.x1},${e.y1} C${mx},${e.y1} ${mx},${e.y2} ${e.x2},${e.y2}`,
                              class: `pf-edge pf-edge-${e.kind} pf-basis-${e.basis}` });
      const tt = svg("title", {}); tt.textContent = `${e.kind} · ${e.basis}`; p.append(tt);
      s.append(p);
    }
    for (const n of L.nodes) {
      const g = svg("g", { class: `pf-node pf-${n.kind} ${riskClass(n, over)}`, transform: `translate(${n.x},${n.y + 16})` });
      g.setAttribute("tabindex", "0");
      const rect = svg("rect", { width: n.w, height: n.h, rx: 4 });
      const tx = svg("text", { x: 6, y: n.h - 6, class: "pf-txt" });
      tx.textContent = nodeLabel(n).slice(0, Math.max(8, Math.floor(n.w / 7)));
      const tt = svg("title", {}); tt.textContent = nodeTitle(n);
      g.append(rect, tx, tt);
      if (n.kind !== "ac") {
        g.classList.add("pf-clickable");
        g.addEventListener("click", () => openNode(n));
        g.addEventListener("keydown", (ev) => { if (ev.key === "Enter") openNode(n); });
      }
      s.append(g);
    }
    svgHost.append(s);
    legendEl.replaceChildren();
    // §6 P2 plain words up front; the basis word stays in the tooltip
    const PLAIN = { recorded: "recorded by a run", planned: "planned in the spec", structural: "from the call graph",
                    lexical: "name match only", anchor: "the spec names this code" };
    const leg = Object.keys(PLAIN).map((b) => { const x = el("span", `pf-leg pf-basis-${b}`, PLAIN[b]); x.title = "basis: " + b; return x; });
    legendEl.append(el("span", "pf-lbl", "links:"), ...leg);
    if (trace.draw.reduced || L.hidden) {
      const said = el("p", "pf-reduced", trace.draw.note || `drawing ${L.nodes.length} of ${trace.draw.total} nodes`);
      said.append(" ", button(showAll ? "hide the full list" : "show all (paged table)", "Every node on this page, as a table", () => { showAll = !showAll; drawTable(); render(); }, "pf-link"));
      legendEl.append(said);
    }
  }

  function row(tr, text, cls) { const td = el("td", cls || "", text); tr.append(td); return td; }

  function drawTable() {
    tableHost.replaceChildren();
    const t = trace;
    if (!t) return;
    const shown = new Set(t.draw.shown || []);
    const all = showAll || !t.draw.reduced;
    const table = el("table", "pf-table");
    const hr = el("tr");
    for (const h of ["", "what", "status", "proof", "links", ""]) hr.append(el("th", "", h));
    table.append(hr);
    const add = (kind, n, status, proof, links, cls) => {
      if (!all && !shown.has(n.id)) return;
      const tr = el("tr", "pf-row " + (cls || ""));
      row(tr, kind, "pf-kind");
      row(tr, n.label || n.ac && `${n.ac} · ${n.text}` || n.id);
      row(tr, status);
      row(tr, proof);
      row(tr, links);
      const acts = row(tr, "", "pf-acts");
      if (kind !== "AC" && n.path) acts.append(button("open", "Open in the Code view", () => MK.openFile && MK.openFile(n.path, n.line || 1, n.symbol || null)));
      table.append(tr);
    };
    for (const a of t.nodes.acs) add("AC", a, acWord(a), a.tests.length ? `${a.tests.length} test(s)` : "—",
      `${a.code.length} definition(s)`, over.untested && a.status === "untested" ? "pf-risk-untested" : over.tdd && a.status === "green-not-tdd" ? "pf-risk-tdd" : "");
    for (const x of t.nodes.tests) add("test", x, ambiguousWord(x) || tddWord(x), `red ${when(x.red_at)} · green ${when(x.green_at)} (${x.basis})`,
      `${x.acs.length} AC(s) · ${x.code.length} definition(s)`, over.tdd && x.tdd !== "proven" ? "pf-risk-tdd" : "");
    for (const c of t.nodes.code) add("code", c, covWord(c),
      (over.blast ? `${c.fan_in} caller(s) · blast ${c.blast}${c.blast_cut ? " (cut)" : ""}` : "") +
      ((c.approvals || []).length ? ` · ${c.approvals.map((a) => a.decision + " by " + (a.actor || "?")).join(", ")}` : ""),
      `${c.acs.length} AC(s) · ${c.tests.length} test(s)`, over.untested && c.coverage === "untested" ? "pf-risk-untested" : "");
    tableHost.append(el("h3", "", all ? `every node on this page (${t.nodes.acs.length + t.nodes.tests.length + t.nodes.code.length})` : "drawn nodes"), table);
    if (over.gates && (t.gates || []).length) {
      const gt = el("table", "pf-table pf-gates");
      const ghr = el("tr");
      for (const h of ["gate", "verdict", "when", "path", "detail", "source", "attached"]) ghr.append(el("th", "", h));
      gt.append(ghr);
      for (const g of t.gates) {
        const tr = el("tr", "pf-row pf-gate-" + g.verdict);
        row(tr, g.gate); row(tr, g.verdict); row(tr, g.at); row(tr, g.path); row(tr, g.detail); row(tr, g.source);
        row(tr, g.attached === "time-window" ? "by time window (row names no run)" : "this run");
        gt.append(tr);
      }
      tableHost.append(el("h3", "", `gate verdicts (${t.gates.length})`), gt);
    }
    if ((t.approvals || []).length) {
      const at = el("table", "pf-table");
      const ahr = el("tr");
      for (const h of ["decision", "target", "actor", "when", "proposal"]) ahr.append(el("th", "", h));
      at.append(ahr);
      for (const a of t.approvals) {
        const tr = el("tr", "pf-row");
        row(tr, a.decision); row(tr, a.target); row(tr, a.actor); row(tr, a.at); row(tr, a.proposal);
        at.append(tr);
      }
      tableHost.append(el("h3", "", `approvals (${t.approvals.length})`), at);
    }
  }

  function drawPager() {
    pagerEl.replaceChildren();
    const p = trace.page;
    pagerEl.append(el("span", "pf-lbl", `page ${p.n} · rows ${p.rows} of ${p.total_rows}`));
    if (p.n > 1) pagerEl.append(button("← previous", "The previous page of rows", () => { query.page = p.n - 1; load(); }));
    if (p.next) pagerEl.append(button("next →", "Continue: the next page of rows", () => { query.page = p.next; load(); }));
    pagerEl.append(button("Refresh", "Re-build the trace", load));
  }

  window.mokataIDE.register({
    id: "proof",
    commands: [{ id: "proof.open", title: "Proof: AC → test → code trace", run: () => window.mokataIDE.show("proof"),
                 keys: "Alt+Shift+V" }],
    // UX-SIMPLIFY §5 #10: Proof is a FULL-WIDTH view of Review (U15, the launch demo) — the picker is
    // a disclosure at its top, Tests by AC (§5 #35, review.js) sits under the trace.
    panels: typeof document.createElement !== "function" ? [] : [{
      id: "proof", title: "Proof", tab: "review", full: true, order: 10,
      mount(_sec, view) {
        viewEl = view;
        const pick = document.createElement("details");
        pick.className = "pf-pickbox";
        const sum = el("summary", "small", "What to prove — a run, a session, the working tree or a commit range");
        noteEl = el("p", "pf-note", "");
        noteEl.setAttribute("role", "status");
        pickEl = el("div", "pf-pick");
        // 0.0.28 E2: what "proven" is bound to, and what it cannot see (note E §3f)
        const help = el("p", "pf-note pf-help",
          "A proof is bound to the file it ran against: its red, its green and its content stamp must be of one " +
          "real file in this repository. A re-pointed link, a file swapped for a link, or a link that leaves the " +
          "repository never proves, and the test says which files. Not seen: a hard link or a bind mount (the same " +
          "bytes under two names), and anything that changed while the test ran.");
        pick.append(sum, help, noteEl, pickEl);
        view.append(pick);
        summaryEl = el("div", "pf-summary");
        riskEl = el("div", "pf-risk");
        legendEl = el("div", "pf-legend");
        svgHost = el("div", "pf-flow");
        tableHost = el("div", "pf-tables");
        pagerEl = el("div", "pf-pager");
        const tests = el("section", "pf-tests");
        tests.setAttribute("aria-labelledby", "pf-tests-h");
        const th = el("h2", "", "Tests by acceptance criterion");
        th.id = "pf-tests-h";
        testsHost = el("div", "pf-tests-host");
        tests.append(th, testsHost);
        view.append(summaryEl, riskEl, svgHost, legendEl, pagerEl, tableHost, tests);
        if (MK.testsByAC) MK.testsByAC.mount(testsHost);
      },
      onShow() {
        if (!targets) loadTargets(); else if (trace) render();
        // review.js may load after this file: mount (idempotent) on first show, then read
        if (MK.testsByAC) { MK.testsByAC.mount(testsHost); MK.testsByAC.load(); }
      },
    }],
  });
})();
