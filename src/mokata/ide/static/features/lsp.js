// mokata ide — read-only LSP behind workspace trust (0.0.27 P24, ID-LSP-READONLY-EARLIER / R3).
//
// Three parts, placed where they are used (0.0.27 UX-SIMPLIFY §5 #9 — no screen of its own):
//   * TRUST — the workspace's trust state. A language server runs the repo's code, so nothing
//     starts until you trust it: the Trust button asks the server to print a ONE-TIME CODE to the
//     terminal running `mokata ide --foreground` (the P38 bar; the pairing token alone never
//     trusts) and you type it back; otherwise the panel shows `mokata ide --trust`. Revoke needs
//     no code and stops every server. In the Health dialog's "Language servers" section.
//   * SERVERS — one per language: found / not found (with the install line — never installed by
//     mokata), Start / Stop, state (indexing with the server's own progress, ready, crashed with
//     restarts), the count of edits the server asked for and was refused. In Health, under trust.
//   * ANSWERS — the palette's "Language server: definition / hover / symbols / diagnostics" for
//     the name under the caret, through /api/lsp/*, answered in the Code inspector's References
//     tab. References, callers and callees have ONE surface: the Find Usages box (usages.js —
//     0.0.28 G-Q3 deleted this panel's duplicate references read). The answer says its SOURCE: the
//     language server, or the graph layer while untrusted / indexing / crashed (labelled). Every
//     answer carries the mokata edge: results split into tests vs code, the symbol's callers,
//     blast radius and covering tests from the graph with its honesty basis, and the ACs whose
//     spec anchors name this file; a diagnostic names the symbol and ACs it sits under.
// SAFETY: every piece of repo or server text goes in through textContent.
"use strict";

(function () {
  if (typeof window === "undefined" || !window.mokataIDE || typeof document === "undefined") return;
  const MK = window.MK;
  const el = MK.el;
  const S = { status: null, trustBox: null, serversBox: null, answerBox: null, phase: "idle", last: null };

  function btn(label, cls, fn) { const b = el("button", cls || "btn ghost small", label); b.type = "button"; b.onclick = fn; return b; }
  function say(box, kind, text) { box.replaceChildren(el("div", "region-msg " + kind, text)); }

  // ------------------------------------------------------------------ the caret in the code view
  // (line, col, word): the selection's anchor inside a `div.cl` row (nav.js renders one per line)
  function caret() {
    const sel = window.getSelection ? window.getSelection() : null;
    const nav = MK.nav || {};
    if (!nav.path) return null;
    let line = nav.line || 1, col = 1, word = "";
    if (sel && sel.anchorNode) {
      const node = sel.anchorNode;
      const row = (node.nodeType === 3 ? node.parentNode : node).closest ? (node.nodeType === 3 ? node.parentNode : node).closest("div.cl") : null;
      if (row) {
        line = +row.dataset.n || line;
        const lt = row.querySelector(".lt");
        if (lt && lt.contains(node)) {
          const range = document.createRange();
          range.setStart(lt, 0);
          range.setEnd(node, sel.anchorOffset);
          const before = range.toString();
          col = before.length + 1;
          const text = lt.textContent || "";
          let a = col - 1, b = col - 1;
          while (a > 0 && /[\w$]/.test(text[a - 1])) a--;
          while (b < text.length && /[\w$]/.test(text[b])) b++;
          word = text.slice(a, b);
          if (word) col = a + 1;
        }
      }
    }
    return { path: nav.path, line, col, word };
  }

  // ------------------------------------------------------------------ trust
  function renderTrust() {
    const box = S.trustBox, st = S.status;
    if (!box || !st) return;
    box.replaceChildren();
    if (st.sample) { say(box, "empty", "SAMPLE data — there is no workspace to trust and no language server."); return; }
    const t = st.trust;
    const head = el("div", "row");
    head.append(el("b", "", t.trusted ? "Workspace trusted" : "Workspace not trusted"),
                el("span", "chip small " + (t.trusted ? "ok" : "warn"), t.trusted ? "language servers may run" : "graph layer only"));
    box.append(head);
    // verify2 N3: a revoke whose ledger row failed is still a revoke — and it says so, loudly
    if (S.trustWarn) box.append(el("p", "small error", S.trustWarn));
    box.append(el("p", "small dim", t.why + "."));
    if (t.note) box.append(el("p", "small dim lsp-trust-note", "Note: " + t.note + "."));
    const runs = t.will_run || [];
    box.append(el("div", "small", runs.length ? "Trusting allows exactly these commands to run:" : "No language server found yet — trusting allows the built-in ones once installed (never installed by mokata)."));
    for (const r of runs) box.append(el("div", "small mono lsp-cmd", `${r.language}: ${r.argv.join(" ")}`));
    if (t.trusted) {
      box.append(el("p", "small", `trusted ${t.at || ""} by ${t.by || "?"} · ledgered`));
      box.append(btn("Revoke trust", "btn ghost small", async () => {
        const r = await MK.api("/api/lsp/trust", { decision: "revoke" });
        if (r.error) { say(box, "error", r.note || r.error); return; }
        S.trustWarn = (r.trust && r.trust.ledger_error) || "";
        refresh();
      }));
      return;
    }
    const acts = el("div", "row");
    if (t.approve_here && t.approve_here.available) {
      if (S.phase === "idle") {
        acts.append(btn("Trust this workspace…", "btn small", async () => {
          const r = await MK.api("/api/lsp/trust/code", { decision: "trust" });
          if (r.error) { say(box, "error", r.note || r.error); return; }
          S.phase = "code"; renderTrust();
        }));
      } else {
        const inp = document.createElement("input");
        inp.type = "text"; inp.placeholder = "code from your terminal (ABCD-EFGH)"; inp.className = "small mono";
        inp.setAttribute("aria-label", "one-time code");
        const go = btn("Trust", "btn small", async () => {
          const r = await MK.api("/api/lsp/trust", { decision: "trust", code: inp.value });
          if (r.error) { say(box, "error", r.note || r.error); S.phase = "idle"; return; }
          S.phase = "idle"; S.trustWarn = ""; refresh();
        });
        inp.onkeydown = (ev) => { if (ev.key === "Enter") go.click(); };
        acts.append(el("span", "small", "A one-time code was printed in the terminal running mokata ide --foreground. "), inp, go,
                    btn("Cancel", "btn ghost small", () => { S.phase = "idle"; renderTrust(); }));
      }
    } else {
      acts.append(el("span", "small", (t.approve_here && t.approve_here.reason) || ""), document.createTextNode(" "), el("code", "", t.command));
    }
    box.append(acts);
  }

  // ------------------------------------------------------------------ servers
  function renderServers() {
    const box = S.serversBox, st = S.status;
    if (!box || !st) return;
    box.replaceChildren();
    if (st.sample) return;
    const running = new Map((st.servers || []).map((s) => [s.language, s]));
    const ul = el("ul", "lsp-servers");
    for (const l of st.languages || []) {
      const s = running.get(l.language);
      const li = el("li", "lsp-server");
      const head = el("div", "row");
      head.append(el("b", "", l.language), el("span", "dim small mono", " " + (l.exts || []).join(" ")));
      if (s) {
        head.append(el("span", "chip small st-" + s.state, s.state + (s.restarts ? ` · restarted ${s.restarts}×` : "")));
        if (s.indexing) head.append(el("span", "small dim", ` ${s.indexing.title || ""} ${s.indexing.message || ""}` + (s.indexing.percentage != null ? ` ${s.indexing.percentage}%` : "")));
        if (s.refused_edits) head.append(el("span", "chip small warn", `${s.refused_edits} edit${s.refused_edits === 1 ? "" : "s"} refused`));
        if (s.held) head.append(el("span", "small warn", " " + s.held));
        head.append(btn("Stop", "btn ghost small", async () => { await MK.api("/api/lsp/stop", { language: l.language }); refresh(); }));
        if (s.state === "crashed") head.append(btn("Start again", "btn ghost small", () => start(l.language)));
        li.append(head);
        li.append(el("div", "small dim mono", `${s.server} · pid ${s.pid || "-"} · ${s.answered} answers · ${s.open_documents} open`));
      } else {
        if (l.found) {
          head.append(el("span", "chip small", l.server + " found"));
          if (st.trust && st.trust.trusted) head.append(btn("Start", "btn small", () => start(l.language)));
          else head.append(el("span", "small dim", " trust the workspace to start it"));
        } else {
          head.append(el("span", "chip small warn", "no server found"));
        }
        li.append(head);
        if (!l.found) {
          for (const c of l.candidates || []) li.append(el("div", "small dim mono", `${c.name} — install yourself: ${c.install}`));
        }
      }
      ul.append(li);
    }
    box.append(ul);
    box.append(el("p", "small dim", st.read_only + ": the bridge sends only hover, definition, references, symbols and the call hierarchy; it never sends a rename, a code action, formatting or a command."));
  }

  async function start(language) {
    const r = await MK.api("/api/lsp/start", { language });
    if (r.error) say(S.serversBox, "error", r.note || r.error);
    refresh();
  }

  // ------------------------------------------------------------------ answers
  function edgeLines(e) {
    const out = [];
    if (!e) return out;
    if (e.tests != null) out.push(`${e.tests} from tests · ${e.code != null ? e.code : "?"} from code`);
    if (e.callers != null) out.push(`graph: ${e.callers} callers · blast radius ${e.blast_radius}${e.blast_truncated ? "+" : ""} (${e.blast_basis}) · ${e.verdict}${e.tests_reaching && e.tests_reaching.length ? ` · ${e.tests_reaching.length} tests reach it` : ""}`);
    if (e.ac && e.ac.length) out.push("ACs anchored here: " + e.ac.map((a) => a.ac + (a.symbol ? ` (${a.symbol})` : "")).join(", "));
    if (e.note) out.push(e.note);
    return out;
  }

  async function ask(kind) {
    const c = caret();
    const box = S.answerBox;
    if (!box) return;
    if (MK.showCodeAnswer) MK.showCodeAnswer();
    if (!c) { say(box, "empty", "Open a file in Code and put the caret on a name."); return; }
    say(box, "loading", `${kind} of ${c.word || "the position"} in ${c.path}:${c.line}:${c.col}…`);
    const r = await MK.api(`/api/lsp/${kind}?path=${encodeURIComponent(c.path)}&line=${c.line}&col=${c.col}`);
    S.last = r;
    renderAnswer(kind, r);
    refresh();
  }

  function renderAnswer(kind, r) {
    const box = S.answerBox;
    box.replaceChildren();
    if (!r || r.error) { say(box, "error", (r && (r.note || r.error)) || "no answer"); return; }
    const head = el("div", "row");
    head.append(el("b", "", `${kind} · ${r.symbol || ""}`),
                el("span", "chip small " + (r.source === "lsp" ? "ok" : "warn"),
                   r.source === "lsp" ? `language server (${r.lsp.server || ""})` : r.source === "graph" ? "graph layer — labelled, not the language server" : "no source"));
    if (r.lsp && r.lsp.state && r.source !== "lsp") head.append(el("span", "small dim", ` ${r.lsp.state}: ${r.lsp.note || ""}`));
    if (r.lsp && r.lsp.job) head.append(el("span", "small dim mono", ` job ${r.lsp.job} (jobs chip)`));
    if (r.basis && MK.basisBadge) head.append(MK.basisBadge(r.basis));
    box.append(head);
    for (const line of edgeLines(r.edge)) box.append(el("div", "small lsp-edge", line));
    if (kind === "hover") {
      box.append(r.hover ? el("pre", "mono small lsp-hover", r.hover) : el("p", "dim small", r.note || "no hover text"));
      return;
    }
    const ul = el("ul", "lsp-results");
    for (const x of r.results || []) {
      const li = el("li", "ref-card" + (x.test ? " is-test" : ""));
      if (kind === "symbols") {
        const b = el("button", "ref-head mono"); b.type = "button";
        b.append(el("span", "grow", `${"  ".repeat(x.depth || 0)}${x.name}`), el("span", "dim", `${x.kind} · L${x.line}`));
        b.onclick = () => MK.openFile && MK.openFile(r.path, x.line, x.name);
        li.append(b);
      } else if (kind === "diagnostics") {
        const b = el("button", "ref-head mono"); b.type = "button";
        b.append(el("span", "chip small sev-" + x.severity, x.severity), el("span", "grow", ` L${x.line}:${x.col} ${x.message}`));
        if (x.symbol) b.append(el("span", "dim", ` in ${x.symbol}`));
        if (x.ac && x.ac.length) b.append(el("span", "chip small", " " + x.ac.join(" ")));
        b.onclick = () => MK.openFile && MK.openFile(r.path, x.line, null);
        li.append(b);
      } else {
        const b = el("button", "ref-head mono"); b.type = "button";
        b.append(el("span", "grow", `${x.path}:${x.line}:${x.col}`));
        if (x.test) b.append(el("span", "chip small", "test"));
        if (x.outside) b.append(el("span", "chip small warn", "outside the repo"));
        b.disabled = !!x.outside;
        b.onclick = () => MK.openFile && MK.openFile(x.path, x.line, r.symbol || null);
        li.append(b);
        if (x.snippet) li.append(el("div", "ref-snip mono", x.snippet));
      }
      ul.append(li);
    }
    if (!(r.results || []).length) ul.append(el("li", "dim small", r.note || "none"));
    box.append(ul);
    if (r.note && (r.results || []).length) box.append(el("p", "dim small", r.note));
  }

  // ------------------------------------------------------------------ status + events
  async function refresh() {
    const r = await MK.api("/api/lsp");
    if (r && !r.error) { S.status = r; renderTrust(); renderServers(); }
    else if (S.serversBox) say(S.serversBox, "error", (r && (r.note || r.error)) || "no answer");
  }
  (MK.liveHooks = MK.liveHooks || []).push((es) => {
    es.addEventListener("bus", (ev) => {
      let e; try { e = JSON.parse(ev.data); } catch (_x) { return; }
      if (!e || e.kind !== "lsp") return;
      if (e.what === "crashed" && S.serversBox) say(S.serversBox, "error", `${e.server} (${e.language}) crashed (exit ${e.exit_code}) — it restarts at the next ask`);
      if (S.status) refresh();
    });
  });

  // Health opens → trust and servers are read into its "Language servers" section.
  MK.lspLoad = () => {
    S.trustBox = document.getElementById("hp-lsp-trust");
    S.serversBox = document.getElementById("hp-lsp-servers");
    return refresh();
  };
  // The Code inspector's answer box (nav.js owns the inspector; this fills its #insp-answer).
  MK.wireLsp = () => { S.answerBox = document.getElementById("insp-answer"); };

  window.mokataIDE.register({
    id: "lsp",
    commands: [
      { id: "lsp.open", title: "Language servers: trust and servers (Health)", run: () => { if (MK.openHealth) MK.openHealth("lsp"); } },
      { id: "lsp.definition", title: "Language server: definition of the name at the caret", run: () => ask("definition") },
      { id: "lsp.hover", title: "Language server: hover (type) of the name at the caret", run: () => ask("hover") },
      { id: "lsp.symbols", title: "Language server: document symbols", run: () => ask("symbols") },
      { id: "lsp.diagnostics", title: "Language server: diagnostics for this file", run: () => ask("diagnostics") },
    ],
  });
})();
