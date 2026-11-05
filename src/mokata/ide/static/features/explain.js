// mokata ide — Explain (0.0.27 P46 CHANGE-EXPLAINER): the cited review pages Claude Code writes
// for a change set, and the "Explain changes" button that asks for one.
//
// mokata never calls a model. "Explain changes" builds the FACT bundle as a visible job (Cancel),
// then hands the request to Claude Code on the user's own login: the session's next turn when that
// session is live, otherwise `claude` in the embedded terminal. The page arrives here when Claude
// saves it through `explain_save`.
//
// SAFETY — the page is model-authored, so it is shown in a frame that cannot act:
//   * the frame is `sandbox="allow-same-origin"` with NO allow-scripts, and the page is served with
//     `script-src 'none'`, no network, framable by this origin only (features/explain.py);
//   * the page itself has zero scripts and passed a positive allowlist when it was saved;
//   * its cite buttons are resolved HERE, only through the sidecar's closed kept-cite list — a
//     click or key on anything else in the frame is swallowed; nothing in the frame can navigate;
//   * diagrams are Mermaid SOURCE in the page; this page draws them through the P7 frame and puts
//     the SVG back as an <img> (an image document runs nothing; `img-src data:` only).
// Repo text reaches this page's own DOM through textContent only.
"use strict";

(function () {
  const MK = window.MK;
  const el = (t, c, x) => MK.el(t, c, x);
  const S = { side: null, view: null, list: null, form: null, strip: null, reqs: null, jobs: null,
              size: null, sel: null, sidecar: null, frame: null, data: null, timer: 0, pending: null };

  function btn(text, cls, fn, title) {
    const b = el("button", "xp-btn" + (cls ? " " + cls : ""), text);
    b.type = "button";
    if (title) b.title = title;
    b.onclick = fn;
    return b;
  }
  const short = (t) => String(t || "").replace(/\b([0-9a-f]{12})[0-9a-f]{28}\b/g, "$1");
  function kb(n) { return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.ceil(n / 1024) + " KB"; }

  // ----------------------------------------------------------------------------- asking
  function targetText() {
    const f = S.form;
    const kind = f.kind.value;
    if (kind === "session") return MK.ui.focus ? "session " + MK.ui.focus : "";
    if (kind === "range") {
      const a = f.base.value.trim(), b = f.head.value.trim();
      return a && b ? `range ${a} ${b}` : "";
    }
    return "live";
  }

  async function ask(target) {
    const t = target || targetText();
    if (!t) { MK.regionSay(S.note, "error", "pick a session first, or give both ends of the range"); return; }
    S.note.textContent = "";
    S.strip.say("building the fact bundle for " + t + "…");
    const r = await MK.api("/api/explain/request", { target: t }).catch((e) => ({ error: "unreachable", note: String(e) }));
    if (r.error) { S.strip.done(); MK.regionSay(S.note, "error", MK.whyFailed(r, "ask for an explanation"), { retry: () => ask(t) }); return; }
    MK.markSample(r);
    S.strip.set(r.job);
    S.pending = r.id;
  }

  function onJob(j) {
    if (!j || j.id !== S.pending) return;
    S.strip.set(j);
    if (j.state === "done") {
      S.pending = null;
      S.strip.done();
      S.note.textContent = "asked Claude Code — the request below says where it runs and when the page arrives";
      load();
    } else if (j.state === "failed" || j.state === "cancelled") {
      S.pending = null;
      MK.regionSay(S.note, "error", j.state === "cancelled" ? "cancelled — nothing was asked of Claude Code"
        : "could not build the fact bundle: " + (j.error || "failed"));
    }
  }

  async function cancel(body) {
    const r = await MK.api("/api/explain/cancel", body).catch((e) => ({ error: "unreachable", note: String(e) }));
    if (r.error) MK.regionSay(S.note, "error", MK.whyFailed(r, "cancel"));
    else S.note.textContent = r.note || "cancelled";
    load();
  }

  // ----------------------------------------------------------------------------- the lists
  async function load() {
    const q = "";                     // every session's pages, newest first (each row names its own)
    if (!S.data) MK.regionSay(S.list, "loading", "reading the explainer pages…");
    const d = await MK.api("/api/explain/list" + q).catch((e) => ({ error: "unreachable", note: String(e) }));
    if (d.error) { MK.regionSay(S.list, "error", MK.whyFailed(d, "list the explainer pages"), { retry: load, cli: "mokata explain" }); return; }
    MK.markSample(d);
    S.data = d;
    paint(d);
    clearTimeout(S.timer);
    if ((d.requests || []).some((r) => r.state === "waiting" || r.state === "queued")
        || (d.jobs || []).some((j) => j.state === "running")) S.timer = setTimeout(load, 4000);
  }

  function paint(d) {
    S.reqs.replaceChildren();
    for (const r of (d.requests || []).slice(0, 6)) {
      const li = el("li", "xp-req xp-" + r.state.replace(/\s+/g, "-"));
      li.append(el("span", "xp-state", r.state), el("span", "xp-spec", short(r.spec)), el("p", "xp-why", r.text));
      if (r.state === "queued" || r.state === "waiting")
        li.append(btn("Cancel", "", () => cancel({ request: r.request_id }), "stop waiting for this page"));
      if (r.state === "arrived" && r.review) li.append(btn("Open", "", () => open(r.review)));
      S.reqs.append(li);
    }
    S.jobs.replaceChildren();
    for (const j of (d.jobs || []).filter((x) => x.state === "running")) {
      const li = el("li", "xp-req");
      li.append(el("span", "xp-state", "building"), el("span", "xp-spec", j.spec || ""),
                el("p", "xp-why", `${j.done || 0}/${j.total || "?"} ${j.unit || ""} — ${j.note || ""} (asked from Claude Code)`),
                btn("Cancel", "", () => cancel({ mcp_job: j.id })));
      S.jobs.append(li);
    }
    S.list.replaceChildren();
    const rows = d.reviews || [];
    if (!rows.length) {
      MK.regionSay(S.list, "empty", MK.ui.focus ? "no explainer page for this session yet — Explain changes asks Claude Code for one"
        : "no explainer page yet — pick a session, or explain the working tree");
    }
    for (const r of rows) {
      const li = el("li", "xp-row" + (r.id === S.sel ? " on" : ""));
      li.tabIndex = 0;
      li.setAttribute("role", "button");
      const when = (r.generated_at || "").replace("T", " ").replace("Z", "");
      li.append(el("span", "xp-badge xp-" + r.state, r.state), el("span", "xp-when", when || r.id),
                el("span", "xp-spec", short((r.target && r.target.spec) || r.sid)),
                el("span", "xp-kept", r.state === "unreadable" ? "" : `${r.kept || 0} kept · ${r.dropped || 0} dropped`));
      if (r.why && r.state !== "fresh") li.append(el("p", "xp-why", r.why));
      const go = () => { if (r.state !== "unreadable") open(r.id); };
      li.onclick = go;
      li.onkeydown = (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(); } };
      if ((r.state === "stale" || r.state === "unreadable") && r.target && r.target.spec)
        li.append(btn("Regenerate", "", (ev) => { ev.stopPropagation(); ask(r.target.spec); }, "ask Claude Code for a fresh page"));
      S.list.append(li);
    }
    const sz = d.size || { pages: 0, bundles: 0 };
    S.size.textContent = `store: pages ${kb(sz.pages)} · fact bundles ${kb(sz.bundles)} · the newest ${d.keep} pages and ${d.keep_bundles} bundles per session are kept`;
  }

  // ----------------------------------------------------------------------------- one page
  async function open(id) {
    S.sel = id;
    if (MK.setMode && MK.ui.mode !== "explain") MK.setMode("explain");
    const v = S.view;
    v.replaceChildren();
    const head = el("div", "xp-head");
    const title = el("h2", "", "Change explained");
    const meta = el("p", "xp-meta", id);
    const acts = el("div", "xp-acts");
    head.append(title, meta, acts);
    const note = el("p", "xp-note", "");
    const baseBox = el("section", "xp-base");
    baseBox.hidden = true;
    baseBox.tabIndex = -1;
    baseBox.setAttribute("aria-label", "the base side of a cite (read-only)");
    const dropped = el("details", "xp-dropped");
    const frame = document.createElement("iframe");
    frame.className = "xp-frame";
    frame.title = "explainer page " + id;
    frame.setAttribute("sandbox", "allow-same-origin");
    frame.setAttribute("referrerpolicy", "no-referrer");
    v.append(head, note, baseBox, frame, dropped);
    S.frame = frame;
    const sc = await MK.api("/api/explain/sidecar?id=" + encodeURIComponent(id)).catch((e) => ({ error: "unreachable", note: String(e) }));
    if (sc.error) { MK.regionSay(note, "error", MK.whyFailed(sc, "read the page's sidecar"), { retry: () => open(id) }); return; }
    S.sidecar = sc;
    meta.textContent = `${(sc.target && sc.target.spec) || ""} · ${sc.state}${sc.why && sc.state !== "fresh" ? " — " + sc.why : ""} · Claude Code ${(sc.claude_code && sc.claude_code.version) || "unknown"}`;
    meta.className = "xp-meta xp-" + sc.state;
    acts.append(btn("Export", "", () => exportPage(id), "download the page with its diagrams (no scripts)"));
    acts.append(btn("Commit to repo…", "", () => commitForm(acts, id), "propose writing this page into the repository (you approve it)"));
    if (sc.state !== "fresh" && sc.target && sc.target.spec) acts.append(btn("Regenerate", "", () => ask(sc.target.spec)));
    const dl = sc.dropped || [];
    dropped.append(el("summary", "", `${dl.length} claim(s) dropped as uncited or unresolvable`));
    const ul = el("ul", "xp-dl");
    for (const x of dl) {
      const li = el("li", "");
      li.append(el("span", "xp-dl-where", `${x.section}[${x.block}]`), el("span", "xp-dl-why", x.reason));
      if (x.text) li.append(el("p", "xp-dl-text", x.text));
      ul.append(li);
    }
    dropped.append(ul);
    frame.addEventListener("load", () => wire(frame, sc), { once: true });
    frame.src = "/api/explain/page?id=" + encodeURIComponent(id);
  }

  function wire(frame, sc) {
    const doc = frame.contentDocument;
    if (!doc || !doc.body) return;
    const swallow = (ev) => {
      const t = ev.target && ev.target.closest ? ev.target.closest("button[data-cite]") : null;
      if (ev.type === "submit" || (ev.target && ev.target.closest && ev.target.closest("a,area,form"))) { ev.preventDefault(); return; }
      if (!t) return;
      if (ev.type === "keydown" && ev.key !== "Enter" && ev.key !== " ") return;
      ev.preventDefault();
      follow(sc, t.getAttribute("data-cite"));
    };
    for (const type of ["click", "auxclick", "keydown", "submit"]) doc.addEventListener(type, swallow, true);
    drawDiagrams(doc);
    fit(frame);
  }

  function follow(sc, k) {
    if (!/^\d+$/.test(k || "")) return;
    const c = (sc.cites || [])[+k];
    if (!c || !c.path) { MK.regionSay(S.view.querySelector(".xp-note"), "error", "that cite has no location to open"); return; }
    if (c.tree === "base") { baseView(c); return; }   // never the Code view: that is head
    if (MK.openFile) MK.openFile(c.path, c.line || 1, null);
  }

  // A base cite is the OTHER side of the change: it is read at the base commit the server named
  // (`c.rev`, from the stored target — never chosen here) through P5's read-only
  // `/api/diff/context?rev=`, shown in place and labelled `base @ <sha>`. A file the change added
  // has no base side, and a target with no base commit has none either: both are SAID, and the
  // file at head is never opened in their place (P46 ruling 2).
  async function baseView(c) {
    const host = S.view.querySelector(".xp-base");
    if (!host) return;
    host.replaceChildren();
    host.hidden = false;
    const sha = String(c.rev || ""), sh = sha.slice(0, 12);
    const lo = Math.max(1, c.line || 1), hi = Math.max(lo, c.end || lo);
    const bar = el("div", "xp-base-head");
    bar.append(el("strong", "xp-base-label", sha ? "base @ " + sh : "base"),
               el("span", "xp-base-where", `${c.path}:${lo}${hi > lo ? "-" + hi : ""} · read-only`),
               btn("Close", "", () => { host.hidden = true; host.replaceChildren(); }, "close the base view"));
    const body = el("div", "xp-base-body");
    host.append(bar, body);
    host.focus();
    if (!sha || c.base === "none") {
      MK.regionSay(body, "empty", `${c.cite}: this page's target has no base commit (a session read from its recorded writes), so there is no base side to show — the file at head is not shown in its place`);
      return;
    }
    // `c.base` is the server's explicit object check (commit + blob), never a guess from an error
    if (c.base === "unavailable") {
      MK.regionSay(body, "error", `base commit ${sh} is not in this repository (pruned, or past a shallow clone's boundary) — fetch it (e.g. git fetch --unshallow) to see the base side. The file at head is not shown in its place.`);
      return;
    }
    if (c.base === "added") {
      MK.regionSay(body, "empty", `${c.path} did not exist at base @ ${sh} — the change added it, so there is no base side to show. The file at head is not opened in its place.`);
      return;
    }
    MK.regionSay(body, "loading", `reading ${c.path} at base @ ${sh}…`);
    const from = Math.max(1, lo - 3);
    const q = new URLSearchParams({ path: c.path, rev: sha, start: String(from), count: String(Math.min(500, hi - from + 4)) });
    const r = await MK.api("/api/diff/context?" + q.toString()).catch((e) => ({ error: "unreachable", note: String(e) }));
    body.replaceChildren();
    if (r.error) { MK.regionSay(body, "error", MK.whyFailed(r, "read the base side"), { retry: () => baseView(c) }); return; }
    if (r.state !== "ok") {
      MK.regionSay(body, "error", `could not read ${c.path} at base @ ${sh}: ${r.note || r.state} — the file at head is not shown in its place`);
      return;
    }
    const lines = r.lines || [];
    if (!lines.length) { MK.regionSay(body, "empty", `${c.path} at base @ ${sh} has no line ${lo}`); return; }
    const t = el("table", "xp-base-t");
    t.setAttribute("aria-label", `${c.path} at base @ ${sh}, lines ${r.start}-${r.start + lines.length - 1}`);
    lines.forEach((text, i) => {
      const n = r.start + i;
      const tr = el("tr", n >= lo && n <= hi ? "hit" : "");
      tr.append(el("td", "no", String(n)), el("td", "tx", text.endsWith("\r") ? text.slice(0, -1) : text));
      t.append(tr);
    });
    body.append(t);
  }

  function drawDiagrams(doc) {
    if (!MK.render || !MK.render.diagram) return;
    for (const fig of doc.querySelectorAll("figure[data-diagram]")) {
      const pre = fig.querySelector("pre.mermaid-src");
      if (!pre) continue;
      const src = pre.textContent;
      MK.render.diagram(src).then((svg) => {
        const img = doc.createElement("img");
        img.className = "diagram-img";
        img.alt = "diagram: " + ((fig.querySelector("figcaption") || {}).textContent || "").slice(0, 160);
        img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
        img.setAttribute("style", "max-width:100%;height:auto;display:block");
        pre.hidden = true;
        pre.after(img);
        fit(S.frame);
      }, () => { /* the escaped source stays visible: still readable, never executed */ });
    }
  }

  function fit(frame) {
    try {
      const h = frame.contentDocument.documentElement.scrollHeight;
      frame.style.height = Math.max(240, h + 24) + "px";
    } catch (_e) { /* a frame not of this origin is left at its default size */ }
  }

  function exportPage(id) {
    const doc = S.frame && S.frame.contentDocument;
    if (!doc) return;
    const copy = doc.documentElement.cloneNode(true);
    for (const n of copy.querySelectorAll("script,iframe,object,embed,link,base,meta[http-equiv]")) n.remove();
    for (const p of copy.querySelectorAll("pre.mermaid-src[hidden]")) p.removeAttribute("hidden");
    const head = copy.querySelector("head");
    const meta = doc.createElement("meta");
    meta.setAttribute("http-equiv", "Content-Security-Policy");
    meta.setAttribute("content", "default-src 'none'; img-src data:; style-src 'unsafe-inline'");
    head.prepend(meta);
    const html = "<!doctype html>\n" + new XMLSerializer().serializeToString(copy);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    a.download = "mokata-explain-" + id.replace(/[^A-Za-z0-9_.-]/g, "_") + ".html";
    document.body.append(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  function commitForm(host, id) {
    if (host.querySelector(".xp-commit")) return;
    const box = el("div", "xp-commit");
    const inp = el("input", "xp-input");
    inp.value = "docs/reviews/" + id.replace("/", "-") + ".html";
    inp.setAttribute("aria-label", "repository path for the page");
    const out = el("p", "xp-why", "");
    box.append(inp, btn("Propose", "", async () => {
      const r = await MK.api("/api/explain/commit", { review: id, dest: inp.value.trim() }).catch((e) => ({ error: "unreachable", note: String(e) }));
      if (r.error) { MK.regionSay(out, "error", MK.whyFailed(r, "propose the commit")); return; }
      out.textContent = r.status === "committed" ? "committed" : `proposed — approve it in Approvals (${r.proposal_id || ""}); nothing is written until you do`;
    }), out);
    host.append(box);
    inp.focus();
  }

  // ----------------------------------------------------------------------------- mount
  function mount(side, view) {
    S.side = side; S.view = view;
    side.append(el("h2", "", "Explain"));
    const f = el("form", "xp-form");
    f.setAttribute("aria-label", "explain a change set");
    const kind = el("select", "xp-input");
    kind.name = "kind";
    kind.setAttribute("aria-label", "what to explain");
    for (const [v, t] of [["session", "the session in view"], ["live", "the working tree"], ["range", "a range of commits"]]) {
      const o = el("option", "", t); o.value = v; kind.append(o);
    }
    const base = el("input", "xp-input"); base.name = "base"; base.placeholder = "base (e.g. main)"; base.setAttribute("aria-label", "range base");
    const headI = el("input", "xp-input"); headI.name = "head"; headI.placeholder = "head (e.g. my-branch)"; headI.setAttribute("aria-label", "range head");
    const range = el("div", "xp-range"); range.append(base, headI); range.hidden = true;
    kind.onchange = () => { range.hidden = kind.value !== "range"; };
    const go = el("button", "xp-btn xp-go", "Explain changes");
    go.type = "submit";
    f.append(kind, range, go, el("p", "xp-fine", "Claude Code writes the page on your own login; mokata calls no model and never blocks a merge."));
    f.onsubmit = (ev) => { ev.preventDefault(); ask(); };
    S.form = { kind, base, head: headI };
    S.note = el("p", "xp-note", "");
    const stripHost = el("div", "");
    S.strip = MK.jobs ? MK.jobs.strip(stripHost) : { say() {}, set() {}, done() {} };
    S.reqs = el("ul", "xp-reqs"); S.reqs.setAttribute("aria-label", "requests");
    S.jobs = el("ul", "xp-reqs"); S.jobs.setAttribute("aria-label", "bundle jobs Claude Code is running");
    S.list = el("ul", "xp-list"); S.list.setAttribute("aria-label", "explainer pages");
    S.size = el("p", "xp-fine", "");
    side.append(f, stripHost, S.note, S.reqs, S.jobs, el("h3", "", "Pages"), S.list, S.size);
    view.append(el("p", "xp-empty", "Pick a page on the left, or Explain changes to ask Claude Code for one."));
    if (MK.jobs && MK.jobs.onUpdate) MK.jobs.onUpdate(onJob);
  }

  MK.explain = { ask, open, load };

  window.mokataIDE.register({
    id: "explain",
    commands: [
      { id: "explain.open", title: "Explain: cited review pages of a change", keys: "Alt+Shift+E",
        run: () => window.mokataIDE.show("explain") },
      { id: "explain.ask", title: "Explain changes: ask Claude Code to explain the session in view",
        run: () => { window.mokataIDE.show("explain"); ask(MK.ui.focus ? "session " + MK.ui.focus : "live"); } },
    ],
    panels: typeof document.createElement !== "function" ? [] : [{
      id: "explain", title: "Explain", tab: "review", side: true, order: 20, mount, onShow: load,
    }],
  });
})();
