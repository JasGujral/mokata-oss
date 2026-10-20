// mokata ide — health + updates (0.0.25).
// HIGHLIGHT what is missing, degraded or out of sync (never install a dependency); FORCE a re-sync
// of what is derived (code graph, AST index, search index); UPDATE mokata on a confirmed click.
// One cheap pass at page load (no spawn, no network) fills the chip; opening the panel adds every
// `mokata doctor` verdict. "Check for update" is the only network call, and only on the click.
// Every text goes in through textContent.
"use strict";

(function () {
  const MK = window.MK;
  const { $, el, api } = MK;
  const H = { data: null, poll: 0, update: null };
  const STATE_CLS = { ok: "ok", missing: "bad", broken: "bad", degraded: "warn", "out of sync": "warn" };

  function chip(data) {
    const n = (data && data.problems) || 0;
    const b = $("health-chip");
    $("health-n").textContent = data ? String(n) : "?";
    b.classList.toggle("has-problems", n > 0);
    b.title = n ? `${n} thing(s) missing, degraded or out of sync — open for details`
      : "nothing missing or degraded — open for updates and re-sync";
  }

  async function load(full) {
    const res = await api("/api/health" + (full ? "?full=1" : "")).catch(() => null);
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
    $("hp-sub").textContent = res.problems ? `${res.problems} thing(s) need attention` : "everything answers";
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
      if (r.action) {
        const b = el("button", "btn ghost small", r.action_label || r.action);
        b.type = "button";
        b.onclick = () => act(r.action, "");
        acts.append(b);
      }
      if (acts.childNodes.length) li.append(acts);
      ul.append(li);
      if (r.key === "mokata") renderUpdate(r);
    }
    renderJob(res.job);
  }

  function renderUpdate(r) {
    H.update = r.update || null;
    $("hp-version").textContent = r.detail;
    const u = H.update;
    if (!u) return;
    $("hp-cmd").textContent = u.command || "";
    $("hp-cmd-why").textContent = u.runnable ? "" : (u.reason || "") + " — run it yourself in a terminal.";
    $("hp-run").hidden = !u.runnable;
  }

  function renderJob(job) {
    const box = $("hp-job");
    if (!job || !job.action || job.state === "idle") { box.hidden = true; return; }
    box.hidden = false;
    box.className = "hp-job " + (job.state === "failed" ? "bad" : job.state === "done" ? "ok" : "warn");
    box.replaceChildren(el("b", "", `${job.action}: ${job.state}`));
    if (job.state === "running") {
      const secs = Math.round(Date.now() / 1000 - (job.started || Date.now() / 1000));
      box.append(document.createTextNode(` — ${secs}s (a code-graph rebuild on a large repo takes minutes)`));
    }
    if (job.message) box.append(el("div", "small", job.message));
    if (job.output) box.append(el("pre", "mono small hp-out", job.output));
    if (job.refused) box.append(el("div", "small", job.refused));
  }

  async function pollJob() {
    clearTimeout(H.poll);
    const job = await api("/api/health/job").catch(() => null);
    renderJob(job);
    if (job && job.state === "running") { H.poll = setTimeout(pollJob, 2000); return; }
    load(true);                                   // the job changed what is true: re-check once
  }

  async function act(action, confirm) {
    const res = await api("/api/health/action", { action, confirm }).catch((e) => ({ error: "fetch", note: String(e) }));
    if (res && res.error) { renderJob({ action, state: "failed", message: res.note || res.error }); return; }
    renderJob(res);
    pollJob();
  }

  async function checkUpdate() {
    $("hp-check").disabled = true;
    $("hp-check-note").textContent = "checking…";
    const res = await api("/api/health/check-update", {}).catch(() => null);
    $("hp-check").disabled = false;
    $("hp-check-note").textContent = res ? res.message : "could not check";
    $("hp-upd").hidden = !(res && res.ok && !res.up_to_date && H.update);
  }

  function open() {
    $("hp").hidden = false;
    $("hp-sub").textContent = "checking…";
    load(true);
    pollJob();
  }
  function close() { $("hp").hidden = true; clearTimeout(H.poll); }

  MK.wireHealth = function wireHealth() {
    $("health-chip").onclick = open;
    $("hp-close").onclick = close;
    $("hp").onclick = (ev) => { if (ev.target === $("hp")) close(); };
    $("hp-check").onclick = checkUpdate;
    $("hp-run").onclick = () => {
      const cmd = H.update && H.update.command;
      if (!cmd) return;
      // The confirmation IS the command shown; the server refuses anything else.
      if (!window.confirm(`Run this now?\n\n${cmd}\n\nmokata keeps running the old version until you restart the IDE.`)) return;
      act("update", cmd);
    };
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !$("hp").hidden) close(); });
    load(false);                                 // the cheap pass: fills the chip, no spawn, no network
  };
  MK.openHealth = open;
})();
