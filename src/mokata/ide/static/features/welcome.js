// mokata ide — S0 Welcome (0.0.26 P39 FIRST-RUN; PRD F-1, §5 S0, R34/R36). A stranger runs
// `mokata ide` on their repo and is GUIDED: the repo (files counted, languages), then one row per
// capability — ready now, limited, not ready, building, failed, needs setup. A missing dependency
// is ONE fix line (doctor's: the same string as the search card and the Health row); a missing
// index or setup is a Build / Set up button that first shows a CONSENT — what it writes, where,
// the estimated time and disk — and only "Confirm" sends the consent's token back; the action
// runs as an Activity job (progress, Cancel). When it reaches done the row flips and what it
// unlocks (Impact, Search by meaning, Sessions) is enabled. The IDE never installs and never goes
// online. Shown first on an uninitialised repo, or when an index is missing (once per repo).
// SAFETY: textContent only.
"use strict";

(function () {
  const MK = window.MK;

  // Pure helpers (node-tested): which rows can be opened, and the one-line state.
  const STATE_WORD = { ready: "ready", limited: "works now (limited)", "not-ready": "not ready",
                       building: "building…", failed: "failed", "needs-setup": "needs setup" };
  // An unlock is openable when it works NOW: Impact always does (the floor answers until the code
  // graph is built, then it is structural); Search by meaning and Sessions only once ready.
  function openable(row, e) { return !!(e ? e.now : row && row.ready); }
  MK.welcome = { STATE_WORD, openable };

  if (typeof document === "undefined") return;   // node tests of the pure helpers

  const el = MK.el;
  const W = { view: null, data: null, consent: null, job: null, poll: 0, shownOnce: false, moved: false };

  function copyBtn(text) {
    const b = el("button", "btn ghost small", "Copy");
    b.type = "button";
    b.onclick = () => { try { navigator.clipboard.writeText(text); b.textContent = "Copied"; } catch (_e) { b.textContent = "select + copy"; } };
    return b;
  }

  function header(d) {
    const h = el("div", "wl-head");
    h.append(el("h2", "", d.initialized ? `Welcome back — ${d.repo.name}` : `Welcome to ${d.repo.name}`));
    const langs = (d.repo.languages || []).map((l) => `${l.ext} ${l.files.toLocaleString()}`).join(" · ");
    h.append(el("p", "wl-sub", `${d.repo.files.toLocaleString()} files${langs ? " · " + langs : ""}` +
      (d.initialized ? "" : " · mokata is not set up here yet — nothing has been written to this repo")));
    return h;
  }

  function row(r) {
    const li = el("li", "wl-row wl-" + r.state);
    li.dataset.key = r.key;
    li.dataset.state = r.state;
    const head = el("div", "wl-rhead");
    head.append(el("span", "wl-state", STATE_WORD[r.state] || r.state), el("b", "", r.label));
    li.append(head, el("div", "small wl-detail", r.detail || ""));
    if (r.cost && !r.ready) li.append(el("div", "small dim wl-cost", "cost: " + r.cost.text));
    const acts = el("div", "wl-acts");
    if (r.fix) {
      const c = el("code", "hp-fix wl-fix", r.fix);
      c.title = "run this in a terminal — the IDE never installs anything";
      acts.append(el("span", "small dim", "fix:"), c, copyBtn(r.fix));
    }
    if (r.action && r.state !== "building") {
      const b = el("button", "btn small wl-act", r.action_label || r.action);
      b.type = "button";
      b.dataset.action = r.action;
      b.onclick = () => askConsent(r.action);
      acts.append(b);
    }
    for (const e of r.enables || []) {
      const b = el("button", "btn ghost small wl-open", "Open " + e.label);
      b.type = "button";
      b.dataset.screen = e.screen;
      b.disabled = !openable(r, e);
      b.title = b.disabled ? `${r.label} is not ready yet` : "";
      b.onclick = () => MK.setMode(e.screen);
      acts.append(b);
    }
    if (acts.childNodes.length) li.append(acts);
    return li;
  }

  function render() {
    const v = W.view;
    if (!v) return;
    const d = W.data;
    v.replaceChildren();
    if (!d) return;
    if (d.error) {
      MK.regionSay(v, "error", MK.whyFailed(d, "read what this repo can do"), { retry: load, cli: "mokata doctor" });
      return;
    }
    if (d.sample) { v.append(el("p", "empty", "SAMPLE data — the Welcome screen describes a real repo.")); return; }
    v.append(header(d));
    const consentBox = el("div", "wl-consent");
    consentBox.id = "wl-consent";
    consentBox.hidden = !W.consent;
    v.append(consentBox);
    if (W.consent) paintConsent(consentBox);
    const strip = el("div", "wl-job");
    v.append(strip);
    W.strip = MK.jobs ? MK.jobs.strip(strip) : null;
    if (W.job && W.strip) W.strip.set(W.job);
    const ul = el("ul", "wl-rows");
    ul.dataset.rows = "1";
    for (const r of d.capabilities || []) ul.append(row(r));
    v.append(ul);
    v.append(el("p", "small dim wl-foot", "Costs are estimates from this repo's file count. Nothing runs until you confirm; every build is a job you can cancel in Activity."));
  }

  async function load() {
    if (!W.view) return;
    if (!W.data) MK.regionSay(W.view, "loading", "Reading what this repo can do…");
    const res = await MK.api("/api/welcome", undefined, { onJob: (j) => {
      if (!W.data) MK.regionSay(W.view, "loading", "Counting files — " + MK.jobs.line(j));
    } }).catch((e) => ({ error: "fetch", note: String(e) }));
    W.data = res;
    MK.markSample(res);
    render();
    return res;
  }

  // ------------------------------------------------------------------ consent -> job
  async function askConsent(action) {
    const c = await MK.api("/api/welcome/consent?action=" + encodeURIComponent(action)).catch((e) => ({ error: "fetch", note: String(e) }));
    W.consent = c;
    render();
  }

  function paintConsent(box) {
    const c = W.consent;
    box.replaceChildren();
    if (c.error || c.available === false) {
      MK.regionSay(box, "error", c.note || c.error || "cannot run now", { retry: () => askConsent(c.action) });
      return;
    }
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", c.title);
    box.append(el("h3", "", c.title));
    box.append(el("p", "small", "It will write:"));
    const ul = el("ul", "small wl-writes");
    for (const w of c.writes || []) ul.append(el("li", "", w));
    box.append(ul);
    box.append(el("p", "small wl-ccost", "Cost: " + ((c.cost && c.cost.text) || "?") + (c.cost && c.cost.basis ? ` (${c.cost.basis})` : "")));
    const pre = el("pre", "mono small wl-plan", c.text || "");
    box.append(pre);
    const go = el("button", "btn small wl-confirm", "Confirm — start");
    go.type = "button";
    go.onclick = () => run(c);
    const no = el("button", "btn ghost small wl-cancel", "Not now");
    no.type = "button";
    no.onclick = () => { W.consent = null; render(); };
    box.append(go, document.createTextNode(" "), no);
  }

  async function run(c) {
    const r = await MK.api("/api/welcome/run", { action: c.action, confirm: c.token }).catch((e) => ({ error: "fetch", note: String(e) }));
    W.consent = null;
    if (!r || r.error || !r.job) {
      W.consent = { error: (r && r.error) || "refused", note: MK.whyFailed(r, "start it"), action: c.action, available: false };
      render();
      return;
    }
    follow(r.job);
  }

  function follow(j) {
    W.job = j;
    render();
    clearTimeout(W.poll);
    const tick = async () => {
      const r = await MK.api("/api/jobs/" + encodeURIComponent(W.job.id)).catch(() => null);
      if (r && r.job) onJob(r.job);
      if (W.job && ["queued", "running", "partial"].includes(W.job.state)) W.poll = setTimeout(tick, 1500);
    };
    W.poll = setTimeout(tick, 800);       // a backstop for the event stream, only while it runs
  }

  async function onJob(j) {
    if (!W.job || j.id !== W.job.id) return;
    const was = W.job.state;
    W.job = Object.assign({}, W.job, j);
    if (W.strip) W.strip.set(W.job);
    if (["done", "failed", "cancelled"].includes(j.state) && was !== j.state) {
      clearTimeout(W.poll);
      const d = await load();                     // the job changed what is true: re-read once
      if (j.state === "done" && d && !d.error) {
        const unlocked = (d.capabilities || []).filter((r) => r.ready).flatMap((r) => r.enables || []).map((e) => e.label);
        if (W.strip) W.strip.set(Object.assign({}, W.job, { note: "" }));
        MK.welcome.lastDone = { kind: j.kind, unlocked };
      }
    }
  }
  if (MK.jobs && MK.jobs.onUpdate) MK.jobs.onUpdate(onJob);

  // ------------------------------------------------------------------ first run -> S0
  // launch(first run) -> S0 (PRD §2b): once the first refresh is in, if the user has not moved
  // yet, an uninitialised repo — or one whose graph / meaning index is missing (once per repo,
  // remembered in this browser) — opens on Welcome.
  // "Not moved" = no key or pointer input yet. Comparing the mode at refresh time missed a user
  // who moved BEFORE the first refresh landed (Alt+Shift+W, a rail click): Welcome then yanked
  // them off the screen they had picked (found by the O9 browser e2e).
  if (typeof document !== "undefined" && document.addEventListener) {
    const moved = () => { W.moved = true; };
    document.addEventListener("keydown", moved, { capture: true, once: true });
    document.addEventListener("pointerdown", moved, { capture: true, once: true });
  }
  function seenKey(d) { return "mokata.welcome.seen:" + ((d && d.repo && d.repo.path) || ""); }
  MK.onRefreshWelcome = async () => {
    if (W.shownOnce) return;
    W.shownOnce = true;
    const startMode = MK.ui.mode;
    const d = W.data || await MK.api("/api/welcome").catch(() => null);
    if (!d || d.error || d.sample) return;
    W.data = d;
    if (!d.first_run || W.moved || MK.ui.mode !== startMode) return;
    let seen = false;
    try { seen = !!localStorage.getItem(seenKey(d)); } catch (_e) { seen = false; }
    if (d.initialized && seen) return;
    try { localStorage.setItem(seenKey(d), "1"); } catch (_e) { /* private window: show again */ }
    MK.setMode("welcome");
  };

  window.mokataIDE.register({
    id: "welcome",
    commands: [{ id: "welcome.open", title: "Go to: Welcome — what this repo can do", run: () => window.mokataIDE.show("welcome") }],
    panels: typeof document.createElement !== "function" ? [] : [{
      id: "welcome", title: "Welcome", icon: "★", side: false,
      mount(_sec, view) { W.view = view; view.classList.add("wl-view"); },
      onShow() { load(); },
    }],
  });
})();
