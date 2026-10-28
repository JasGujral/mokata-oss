// mokata ide — Activity (0.0.26 P32, IDE-JOBS; PRD §5.3/§5.4). Long work is a visible JOB: the
// chip right of the ⌘K box says what is running (idle / N jobs + the latest label / partial /
// failed / done), and its drawer lists every job — label, scope, n/N or %, elapsed, ETA, Cancel,
// Retry, Open result. MK.jobs is what the views use: `follow` waits on a job a route answered 202
// with (progress + partial answers to the caller, the final answer at the end, a superseded call
// aborted and its job left), `line` says a job in one line, `strip` is the in-place progress strip
// a view shows IN its own region.
// Nothing here polls: job state arrives on the page's event stream (`job` events, <= 1 s apart
// while something runs, none at idle). SAFETY: textContent only.
"use strict";

(function () {
  const MK = window.MK;
  const ACTIVE = new Set(["queued", "running", "partial"]);
  const A = { jobs: new Map(), followers: new Map(), retries: new Map(), whenDone: [], listeners: [],
              seenFailed: new Set(), flash: 0, open: false, chip: null, drawer: null,
              sessions: { waiting: 0, live: 0, top: null } };

  // ------------------------------------------------------------------------- one job, in words
  function secs(s) {
    if (s === null || s === undefined) return "";
    const n = Math.round(s);
    return n >= 3600 ? `${Math.floor(n / 3600)}h${String(Math.floor(n % 3600 / 60)).padStart(2, "0")}m`
      : n >= 60 ? `${Math.floor(n / 60)}m${String(n % 60).padStart(2, "0")}s` : `${n}s`;
  }
  function progress(j) {
    if (j.total) {
      const pct = Math.floor(100 * (j.done || 0) / j.total);
      return `${j.done || 0}/${j.total} ${j.unit || ""} (${pct}%)`.replace(" (", " (").trim();
    }
    if (j.done) return `${j.done} ${j.unit || ""}`.trim();
    return "";
  }
  function line(j) {
    const parts = [j.label || j.kind];
    const st = j.state === "partial" ? "showing partial results" : j.state;
    if (j.state !== "running") parts.push(st);
    const p = progress(j);
    if (p) parts.push(p);
    if (j.elapsed) parts.push(secs(j.elapsed));
    if (j.eta) parts.push(`~${secs(j.eta)} left`);
    if (j.note && ACTIVE.has(j.state)) parts.push(j.note);
    if (j.error && !ACTIVE.has(j.state)) parts.push(j.error);
    return parts.join(" · ");
  }
  function norm(h) {          // a 202 handle and a `job` event both become a snapshot
    return h.id ? h : Object.assign({}, h, { id: h.job });
  }

  // ------------------------------------------------------------------------- the event stream
  function onEvent(ev) {
    let j;
    try { j = JSON.parse(ev.data); } catch (_e) { return; }
    if (!j || !j.id) return;
    update(j);
  }
  // A job id is one run (the server numbers every new job), so a finished job never runs again. A
  // snapshot that says otherwise is STALE: a fast job's `done` event can arrive before the POST that
  // started it answers `queued` — and that answer used to overwrite `done`, leaving the drawer
  // showing "queued" forever (found by the O9 browser e2e).
  const FINAL = new Set(["done", "failed", "cancelled"]);
  function update(j) {
    const prev = A.jobs.get(j.id);
    if (prev && FINAL.has(prev.state) && !FINAL.has(j.state)) return;
    A.jobs.set(j.id, Object.assign({}, prev || {}, j));
    for (const fn of A.listeners) fn(A.jobs.get(j.id));
    for (const f of A.followers.get(j.id) || []) deliver(f, A.jobs.get(j.id));
    if (j.state === "done" && (!prev || prev.state !== "done")) {
      A.flash = Date.now();
      setTimeout(paint, 5200);                   // the green tick goes back into history
      const keep = [];
      for (const w of A.whenDone) { if (w.kind === j.kind) w.fn(j); else keep.push(w); }
      A.whenDone = keep;
    }
    paint();
  }

  // ------------------------------------------------------------------------- following one job
  function follow(handle, path, body, opts) {
    const o = opts || {};
    const snap = norm(handle);
    update(snap);
    return new Promise((resolve, reject) => {
      const f = { id: snap.id, path, body, o, resolve, reject, partialRef: -1, tries: (o._tries || 0) };
      const list = A.followers.get(f.id) || [];
      list.push(f);
      A.followers.set(f.id, list);
      A.retries.set(f.id, () => MK.api(path, body));
      if (o.signal) {
        if (o.signal.aborted) { drop(f, true); reject(abortError()); return; }
        o.signal.addEventListener("abort", () => { if (drop(f, true)) reject(abortError()); }, { once: true });
      }
      if (handle.partial && o.onPartial) { f.partialRef = handle.partial_ref || 0; o.onPartial(handle.partial, snap); }
      // A job that finished before this page's stream saw it: ask once, then while waiting.
      f.safety = setTimeout(function check() {
        if (!alive(f)) return;
        MK.api("/api/jobs/" + encodeURIComponent(f.id)).then((r) => {
          if (r && r.job) update(r.job);
          else if (r && r.error && alive(f)) { drop(f, false); resolve({ error: "UnknownJob", note: r.note || "the job is gone" }); }
          if (alive(f)) f.safety = setTimeout(check, 5000);
        }).catch(() => { if (alive(f)) f.safety = setTimeout(check, 5000); });
      }, 2000);
      deliver(f, A.jobs.get(f.id));
    });
  }
  function abortError() { const e = new Error("superseded"); e.name = "AbortError"; return e; }
  function alive(f) { return (A.followers.get(f.id) || []).includes(f); }
  function drop(f, leave) {
    const list = (A.followers.get(f.id) || []).filter((x) => x !== f);
    const was = alive(f);
    if (list.length) A.followers.set(f.id, list); else A.followers.delete(f.id);
    clearTimeout(f.safety);
    // A superseded request: this page no longer wants the job (a query nobody wants stops).
    if (leave && was) MK.api(`/api/jobs/${encodeURIComponent(f.id)}/leave`, { client: MK.clientId }).catch(() => null);
    return was;
  }
  function deliver(f, j) {
    if (!j || !alive(f)) return;
    if (f.o.onJob) f.o.onJob(j);
    if (f.o.onPartial && j.state === "partial" && (j.partial_ref || 0) > f.partialRef && !f.fetching) {
      f.fetching = true;
      const ref = j.partial_ref;
      MK.api("/api/jobs/" + encodeURIComponent(f.id)).then((r) => {
        f.fetching = false;
        if (alive(f) && r && r.partial && ref > f.partialRef) { f.partialRef = ref; f.o.onPartial(r.partial, j); }
      }).catch(() => { f.fetching = false; });
    }
    if (j.state === "done") {
      drop(f, false);
      // The finished answer is fetched by naming the job this page waited for (a NEW ask would
      // compute afresh); if it has gone from the server's history, the question is asked again.
      if (f.tries >= 3) { f.resolve({ error: "Changed", note: "the answer could not be fetched — ask again" }); return; }
      const named = f.body === undefined
        ? f.path + (f.path.includes("?") ? "&" : "?") + "job=" + encodeURIComponent(f.id)
        : f.path;
      const body = f.body === undefined ? undefined : Object.assign({}, f.body, { job: f.id });
      MK.api(named, body, Object.assign({}, f.o, { _tries: f.tries + 1 })).then(f.resolve, f.reject);
    } else if (j.state === "failed") {
      drop(f, false);
      f.resolve({ error: "JobFailed", note: j.error || "failed", failed: true, job: j });
    } else if (j.state === "cancelled") {
      drop(f, false);
      f.resolve({ error: "Cancelled", note: j.error || "cancelled", cancelled: true, job: j });
    }
  }

  async function cancel(id) {
    const r = await MK.api(`/api/jobs/${encodeURIComponent(id)}/cancel`, {}).catch(() => null);
    if (r && r.job) update(r.job);
    return r;
  }
  async function start(kind, args) {
    const r = await MK.api("/api/jobs", { kind, args: args || {} }).catch(() => null);
    if (r && r.job) update(r.job);
    return r;
  }
  function whenDone(kind, fn) { A.whenDone.push({ kind, fn }); }

  // ------------------------------------------------------------------------- in-place strip
  // A view puts this IN the region the user is looking at (the graph canvas, a result column):
  // spinner + what it is doing + n/N + elapsed + Cancel. `set(j)` follows a job; `say(text)` is a
  // plain "loading" line (before the first job event); `done()` hides it.
  function strip(host, cls) {
    const box = MK.el("div", "job-strip" + (cls ? " " + cls : ""));
    box.setAttribute("role", "status");
    box.hidden = true;
    const spin = MK.el("span", "job-spin");
    const text = MK.el("span", "job-text");
    const btn = MK.el("button", "btn ghost small job-cancel", "Cancel");
    btn.type = "button";
    box.append(spin, text, btn);
    host.append(box);
    let cur = null;
    btn.onclick = () => { if (cur) cancel(cur.id); };
    return {
      el: box,
      say(t) { cur = null; box.hidden = false; box.classList.remove("bad"); btn.hidden = true; spin.hidden = false; text.textContent = t; },
      set(j) {
        cur = j; box.hidden = false;
        const live = ACTIVE.has(j.state);
        spin.hidden = !live; btn.hidden = !live || j.cancellable === false;
        box.classList.toggle("bad", j.state === "failed");
        text.textContent = line(j);
      },
      done() { cur = null; box.hidden = true; },
    };
  }

  // ------------------------------------------------------------------------- the chip
  function active() { return [...A.jobs.values()].filter((j) => ACTIVE.has(j.state)); }
  function failedUnseen() {
    return [...A.jobs.values()].filter((j) => j.state === "failed" && !A.seenFailed.has(j.id));
  }
  function paint() {
    const c = A.chip;
    if (!c) return;
    const act = active().sort((a, b) => (b.created || 0) - (a.created || 0));
    const bad = failedUnseen();
    c.className = "activity-chip";
    let label = "idle", count = "";
    if (act.length) {
      const partial = act.find((j) => j.state === "partial");
      c.classList.add("busy");
      if (partial) { c.classList.add("partial"); label = `${short(partial.label)} · ${progress(partial) || "partial"}`; }
      else label = `${act.length} job${act.length > 1 ? "s" : ""} · ${short(act[0].label)}`;
      count = String(act.length);
    } else if (bad.length) {
      c.classList.add("failed");
      label = `${bad.length} failed`; count = String(bad.length);
    } else if (Date.now() - A.flash < 5000) {
      c.classList.add("done");
      label = "done";
    }
    // P34 (§5.3): live sessions and "waiting on you" ride on the same chip (the top bar has no
    // separate live dot / needs-you button any more).
    const ss = A.sessions;
    const extra = [ss.live ? `${ss.live} live` : "", ss.waiting ? `${ss.waiting} waiting` : ""].filter(Boolean);
    if (extra.length) label = label === "idle" ? extra.join(" · ") : `${label} · ${extra.join(" · ")}`;
    c.classList.toggle("waiting", ss.waiting > 0);
    if (!count && ss.waiting) count = String(ss.waiting);
    A.chipText.textContent = label;
    A.chipCount.textContent = count;
    const why = ss.waiting && ss.top ? `\n${ss.waiting} waiting on you — most urgent: ${ss.top.text || ss.top.reason}` : "";
    c.title = (act.length ? act.map(line).join("\n") : bad.length ? bad.map(line).join("\n") : "nothing running") + why + "\n. opens the Activity drawer";
    if (A.open) renderDrawer();
  }
  function short(s) { s = String(s || ""); return s.length > 28 ? s.slice(0, 27) + "…" : s; }

  // ------------------------------------------------------------------------- the drawer
  function openResult(j) {
    const a = j.args || {};
    if (j.kind === "graph.view" && MK.openGraph) MK.openGraph(a.view || "ripple", a.symbol || "", a.to || "");
    else if (j.kind === "graph.query" && MK.openGraph) MK.openGraph("focus", a.target || "");
    else if ((j.kind === "search.text" || j.kind === "search.meaning") && MK.openSearch) MK.openSearch(a.q || "");
    else if (j.kind === "impact" && MK.openFile && a.path) MK.openFile(a.path, 1, null);
    else if (j.kind.startsWith("index.") && MK.openHealth) MK.openHealth();
  }
  function retry(j) {
    const again = A.retries.get(j.id);
    if (again) { again(); return; }
    if (["index.crg", "index.ast", "index.meaning", "index.symbols"].includes(j.kind)) { start(j.kind, {}); return; }
    openResult(j);
  }
  function row(j) {
    const li = MK.el("li", "act-row " + j.state);
    const head = MK.el("div", "act-head");
    head.append(MK.el("span", "act-state", j.state === "partial" ? "partial" : j.state), MK.el("b", "", j.label || j.kind));
    if (j.scope) head.append(MK.el("span", "dim small", j.scope));
    li.append(head);
    const meta = [progress(j), j.elapsed ? secs(j.elapsed) + " elapsed" : "", j.eta ? "~" + secs(j.eta) + " left" : ""].filter(Boolean).join(" · ");
    if (meta) li.append(MK.el("div", "small mono", meta));
    if (j.total) {
      const bar = MK.el("div", "act-bar");
      const fill = MK.el("span", "");
      fill.style.width = Math.min(100, Math.floor(100 * (j.done || 0) / j.total)) + "%";
      bar.append(fill);
      li.append(bar);
    }
    const why = ACTIVE.has(j.state) ? j.note : j.error || j.note;
    if (why) li.append(MK.el("div", "small dim act-note", why));
    if (j.state === "partial") li.append(MK.el("div", "small", "showing partial results"));
    const acts = MK.el("div", "act-acts");
    const btn = (t, fn, cls) => { const b = MK.el("button", "btn small " + (cls || "ghost"), t); b.type = "button"; b.onclick = fn; acts.append(b); };
    if (ACTIVE.has(j.state) && j.cancellable !== false) btn("Cancel", () => cancel(j.id));
    if (ACTIVE.has(j.state) && j.cancellable === false) acts.append(MK.el("span", "small dim", "runs in another process — not stopped from here"));
    if (j.state === "failed" || j.state === "cancelled") btn("Retry", () => retry(j));
    if (j.state === "done" || j.state === "partial") btn("Open result", () => { closeDrawer(); openResult(j); });
    if (acts.childNodes.length) li.append(acts);
    return li;
  }
  function renderDrawer() {
    const d = A.drawer;
    const list = d.querySelector(".act-list");
    list.replaceChildren();
    const all = [...A.jobs.values()].sort((a, b) => (b.created || 0) - (a.created || 0));
    const act = all.filter((j) => ACTIVE.has(j.state));
    const rest = all.filter((j) => !ACTIVE.has(j.state)).slice(0, 20);
    if (!act.length && !rest.length) list.append(MK.el("li", "dim small act-empty", "Nothing has run yet. Graph views, searches and index builds that take longer than a moment show here, with their progress and a Cancel."));
    if (act.length) list.append(MK.el("li", "act-sec small dim", "Running"));
    for (const j of act) list.append(row(j));
    if (rest.length) list.append(MK.el("li", "act-sec small dim", "Recent"));
    for (const j of rest) list.append(row(j));
  }
  async function openDrawer() {
    A.open = true;
    A.drawer.hidden = false;
    if (MK.termCaps) MK.termCaps();          // G22: the Terminal button says why it is not there
    for (const j of A.jobs.values()) if (j.state === "failed") A.seenFailed.add(j.id);
    renderDrawer();
    const r = await MK.api("/api/jobs").catch(() => null);
    if (r && r.active) { for (const j of r.active.concat(r.recent || [])) A.jobs.set(j.id, Object.assign({}, A.jobs.get(j.id) || {}, j)); }
    if (A.open) { for (const j of A.jobs.values()) if (j.state === "failed") A.seenFailed.add(j.id); paint(); }
  }
  function closeDrawer() { A.open = false; if (A.drawer) A.drawer.hidden = true; paint(); }
  function toggle() { if (A.open) closeDrawer(); else openDrawer(); }

  function mount() {
    const anchor = document.getElementById("qo-open");
    if (!anchor || A.chip) return;
    const c = MK.el("button", "activity-chip");
    c.type = "button"; c.id = "activity-chip";
    c.setAttribute("aria-haspopup", "true");
    A.chipDot = MK.el("span", "act-dot");
    A.chipText = MK.el("span", "act-label", "idle");
    A.chipCount = MK.el("b", "act-count", "");
    c.append(A.chipDot, A.chipText, A.chipCount);
    c.onclick = toggle;
    anchor.after(c);
    A.chip = c;
    const d = MK.el("div", "act-drawer");
    d.id = "activity-drawer"; d.hidden = true;
    d.setAttribute("role", "dialog"); d.setAttribute("aria-label", "Activity");
    const head = MK.el("div", "act-dhead");
    head.append(MK.el("h2", "", "Activity"));
    const x = MK.el("button", "link small", "close"); x.type = "button"; x.onclick = closeDrawer;
    head.append(x);
    // The live half (Follow · Join · Terminal · waiting on you · live), static in index.html.
    const live = document.getElementById("act-live");
    if (live) { live.hidden = false; d.append(head, live, MK.el("ul", "act-list")); } else d.append(head, MK.el("ul", "act-list"));
    document.body.append(d);
    A.drawer = d;
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && A.open) closeDrawer(); });
    // What already runs (a build started before this page opened): one read at load, then events.
    MK.api("/api/jobs").then((r) => {
      for (const j of ((r && r.active) || [])) A.jobs.set(j.id, j);
      // P37 — work handed to a background worker when the IDE last closed: still running, or
      // its outcome (done / failed), listed here once (the chip flashes a failure).
      for (const j of ((r && r.background) || [])) A.jobs.set(j.id, j);
      paint();
    }).catch(() => null);
    paint();
  }

  // P37 (Jas: "while closing, it should confirm") — closing or reloading the page while work that
  // outlives it is running asks first. The browser shows its own wording; the jobs are named in
  // the event's text for browsers that still show it, and in the Activity chip either way.
  function keptRunning() {
    return [...A.jobs.values()].filter((j) => j.keep && !j.background && j.cancellable !== false && ACTIVE.has(j.state));
  }
  if (typeof window.addEventListener === "function") window.addEventListener("beforeunload", (ev) => {
    const run = keptRunning();
    if (!run.length) return undefined;
    const say = `${run.length} job(s) running: ${run.map((j) => j.label || j.kind).join(", ")} — they keep running in the IDE server.`;
    ev.preventDefault();
    ev.returnValue = say;
    return say;
  });

  (MK.liveHooks = MK.liveHooks || []).push((es) => { es.addEventListener("job", onEvent); });
  MK.jobs = { follow, line, progress, cancel, start, strip, whenDone, onUpdate: (fn) => A.listeners.push(fn),
              get: (id) => A.jobs.get(id),
              all: () => [...A.jobs.values()], keptRunning, open: openDrawer, close: closeDrawer,
              setSessions: (s) => { A.sessions = Object.assign({ waiting: 0, live: 0, top: null }, s || {}); paint(); } };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();

  window.mokataIDE.register({
    id: "activity",
    commands: [{ id: "activity.open", title: "Activity: show running jobs", run: toggle }],
    keybindings: [{ keys: ".", command: "activity.open" }],
  });
})();
