// mokata ide — health + updates (0.0.25).
// HIGHLIGHT what is missing, degraded or out of sync (never install a dependency); FORCE a re-sync
// of what is derived (code graph, AST index, search index); UPDATE mokata on one click — the same
// plan and executor as `mokata update` (0.0.26 L2). The click is the consent; it sends back the
// command it showed, and the output streams into the job box (P32: each action is an Activity job).
// One cheap pass at page load (no spawn, no network) fills the chip; opening the panel adds every
// `mokata doctor` verdict. "Check for update" is the only network call, and only on the click.
// Every text goes in through textContent.
"use strict";

(function () {
  const MK = window.MK;
  const { $, el, api } = MK;
  const H = { data: null, update: null, jobId: null, strip: null };
  const STATE_CLS = { checking: "", ok: "ok", missing: "bad", broken: "bad", degraded: "warn", "out of sync": "warn", "not set up": "warn" };

  function chip(data) {
    const n = (data && data.problems) || 0;
    const b = $("health-chip");
    $("health-n").textContent = data && !data.checking ? String(n) : (data ? "…" : "?");
    b.classList.toggle("has-problems", n > 0);
    b.title = data && data.initialized === false ? "mokata is not set up in this repo — open to see what `mokata setup claude` adds"
      : n ? `${n} thing(s) missing, degraded or out of sync — open for details`
      : "nothing missing or degraded — open for updates and re-sync";
  }

  async function load(full) {
    if (full && !$("hp").hidden) $("hp-sub").textContent = "checking…";
    const path = "/api/health" + (full ? "?full=1" : "");
    const opts = { onJob: (j) => { if (full && !$("hp").hidden) $("hp-sub").textContent = MK.jobs.line(j); } };
    let res = await api(path, undefined, opts).catch(() => null);
    // INT-2a: the checks still running answer 200 in the CHECKING shape with the job in `pending` —
    // show the checking rows now, then follow the job to its finished rows.
    if (res && res.checking && res.pending && MK.jobs) {
      H.data = res;
      chip(res);
      if (full) render(res);
      res = await MK.jobs.follow(res.pending, path, undefined, opts).catch(() => null);
    }
    if (!res || res.error) {
      if (full) $("hp-sub").textContent = res ? `checks failed: ${res.note || res.error}` : "checks failed";
      return;
    }
    H.data = res;
    chip(res);
    if (full) render(res);
  }

  function render(res) {
    const ul = $("hp-rows");
    ul.replaceChildren();
    $("hp-sub").textContent = res.checking ? "checking…"
      : res.problems ? `${res.problems} thing(s) need attention` : "everything answers";
    for (const r of res.rows || []) {
      const li = el("li", "hp-row " + (STATE_CLS[r.state] || ""));
      const head = el("div", "hp-head");
      head.append(el("span", "hp-state", r.state), el("b", "", r.label));
      li.append(head, el("div", "small hp-detail", r.detail || ""));
      const acts = el("div", "hp-acts");
      if (r.fix && r.state !== "ok") {
        const c = el("code", "hp-fix", r.fix);
        c.title = "run this in a terminal";
        acts.append(el("span", "small dim", "fix:"), c);
      }
      if (r.retry) {      // P39 (R35): failing AT USE — re-run the job that failed, same question
        const b = el("button", "btn ghost small hp-retry", "Retry");
        b.type = "button";
        b.title = `run ${r.retry.kind} again`;
        b.onclick = async () => {
          b.disabled = true;
          const s = await MK.jobs.start(r.retry.kind, r.retry.args || {});
          if (!(s && s.id)) { $("hp-sub").textContent = `retry refused: ${(s && (s.note || s.error)) || "no answer"}`; return; }
          H.jobId = s.id;
          // A backstop for the event stream, only while the retried job runs: re-check at its end.
          for (let i = 0; i < 2400; i++) {
            const j = await api("/api/jobs/" + encodeURIComponent(s.id)).catch(() => null);
            if (!j || !j.job || ["done", "failed", "cancelled"].includes(j.job.state)) break;
            await new Promise((ok) => setTimeout(ok, 700));
          }
          load(!$("hp").hidden);
        };
        acts.append(b);
      }
      if (r.action) {
        const b = el("button", "btn ghost small", r.action_label || r.action);
        b.type = "button";
        b.onclick = () => act(r.action);
        acts.append(b);
      }
      if (acts.childNodes.length) li.append(acts);
      if (r.lights_up && r.lights_up.length) {   // P29: what `mokata setup claude` adds here
        const lu = el("ul", "small hp-lights");
        for (const f of r.lights_up) lu.append(el("li", "", f.what));
        li.append(lu);
      }
      ul.append(li);
      if (r.key === "mokata") renderUpdate(r);
    }
    const mine = (res.jobs || []).find((j) => j.id === H.jobId) || (res.jobs || []).find((j) => ["queued", "running", "partial"].includes(j.state));
    if (mine) { H.jobId = mine.id; renderJob(mine); }
  }

  function renderUpdate(r) {
    H.update = r.update || null;
    $("hp-version").textContent = r.detail;
    paintUpdate(H.update);
  }

  // `u` is `update.UpdatePlan.to_dict()` — the same plan `mokata update` prints.
  function paintUpdate(u) {
    if (!u) return;
    $("hp-cmd").textContent = u.command || "";
    $("hp-keeps").textContent = u.keeps || "mokata";
    $("hp-cmd-why").textContent = u.reason || "";
    $("hp-run").hidden = !u.runnable;
    const delta = $("hp-delta");
    delta.replaceChildren();
    delta.hidden = !(u.delta && u.delta.length);
    if (!delta.hidden) {
      delta.append(el("b", "", `What changed ${u.current} → ${u.latest}`));
      for (const rel of u.delta.slice().reverse()) {
        delta.append(el("div", "", rel.version));
        delta.append(el("pre", "mono small hp-out", (rel.notes || []).join("\n") || "(no notes)"));
      }
    }
    const then = $("hp-then");
    then.replaceChildren();
    then.hidden = !(u.follow_up && u.follow_up.length);
    if (!then.hidden) {
      then.append(el("span", "dim", "then: "));
      for (const c of u.follow_up) then.append(el("code", "hp-fix", c), document.createTextNode(" "));
    }
  }

  // P32 — every action is a JOB (Activity lists it with Cancel; no time limit): the box below
  // follows it from the page's event stream — n/N, elapsed, ETA, its last line — and the panel
  // re-checks once when it ends. Nothing is polled.
  function renderJob(job) {
    const box = $("hp-job");
    if (!job) { box.hidden = true; return; }
    if (!H.strip && MK.jobs) H.strip = MK.jobs.strip(box);
    box.hidden = false;
    box.className = "hp-job " + (job.state === "failed" ? "bad" : job.state === "done" ? "ok" : "warn");
    if (H.strip) H.strip.set(job);
    let msg = box.querySelector(".hp-msg");
    if (!msg) { msg = el("div", "small hp-msg"); box.append(msg); }
    msg.textContent = job.state === "done" ? (job.note || "done") : job.state === "failed" ? (job.error || "failed")
      : job.state === "cancelled" ? "cancelled — the index in use was left as it was" : "";
  }

  async function showOutput(id) {
    const r = await api("/api/jobs/" + encodeURIComponent(id)).catch(() => null);
    const out = r && ((r.partial && r.partial.output) || (r.result && r.result.output));
    if (!out) return;
    let pre = $("hp-job").querySelector("pre");
    if (!pre) { pre = el("pre", "mono small hp-out"); $("hp-job").append(pre); }
    pre.textContent = out;
  }

  function onJob(j) {
    if (!H.jobId || j.id !== H.jobId) return;
    renderJob(j);
    if (j.kind === "mokata.update" && j.partial_ref) showOutput(j.id);
    if (["done", "failed", "cancelled"].includes(j.state)) {
      if (j.kind === "mokata.update") showOutput(j.id);
      load(!$("hp").hidden);                     // the job changed what is true: re-check once
    }
  }

  async function act(action, confirm) {
    const res = await api("/api/health/action", { action, confirm: confirm || "" }).catch((e) => ({ error: "fetch", note: String(e) }));
    if (res && res.error) { H.jobId = null; renderJob({ label: action, state: "failed", error: res.note || res.error }); return; }
    H.jobId = res.job.id;
    renderJob(res.job);
  }

  async function checkUpdate() {
    $("hp-check").disabled = true;
    $("hp-check-note").textContent = "checking…";
    const res = await api("/api/health/check-update", {}).catch(() => null);
    $("hp-check").disabled = false;
    $("hp-check-note").textContent = res ? res.message : "could not check";
    if (res && res.update) { H.update = res.update; paintUpdate(res.update); }
  }

  function open() {
    $("hp").hidden = false;
    $("hp-sub").textContent = "checking…";
    load(true);
  }
  function close() { $("hp").hidden = true; }

  MK.wireHealth = function wireHealth() {
    $("health-chip").onclick = open;
    $("hp-close").onclick = close;
    $("hp").onclick = (ev) => { if (ev.target === $("hp")) close(); };
    $("hp-check").onclick = checkUpdate;
    $("hp-run").onclick = () => {
      const cmd = H.update && H.update.command;
      if (cmd) act("update", cmd);               // the server refuses anything but the shown command
    };
    $("hp-copy").onclick = async () => {
      const cmd = H.update && H.update.command;
      if (!cmd) return;
      try { await navigator.clipboard.writeText(cmd); $("hp-copy").textContent = "Copied"; }
      catch (_e) { $("hp-copy").textContent = "Select the line above and copy it"; }
      setTimeout(() => { $("hp-copy").textContent = "Copy"; }, 2000);
    };
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !$("hp").hidden) close(); });
    if (MK.jobs) MK.jobs.onUpdate(onJob);
    load(false);                                 // the cheap pass: fills the chip, no spawn, no network
  };
  MK.openHealth = open;
})();
