// mokata ide — Sessions mode (0.0.25 ID3): the session browser, the needs-you queue, the
// session switcher, the time-lapse and the change treemap. The server derives and lays out
// everything (`ide/session_views.py`); this file only draws it. Repo text goes in through
// textContent / SVG attributes, never markup.
"use strict";

(function () {
  const MK = window.MK;
  const { $, el, ui, api } = MK;
  const SVG = "http://www.w3.org/2000/svg";
  const sv = { data: null, tl: null, step: 0, timer: 0, size: "loc" };

  const svgEl = (tag, attrs) => {
    const n = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, String(v));
    return n;
  };
  const ago = (secs) => secs < 90 ? `${secs}s` : secs < 5400 ? `${Math.round(secs / 60)}m` : `${Math.round(secs / 3600)}h`;

  // ----------------------------------------------------------------------------- browser
  async function loadBrowser() {
    const d = await api("/api/sessions").catch(() => null);
    if (!d || d.error) return;
    MK.markSample(d);
    sv.data = d;
    renderNeedsBadge(d);
    if (ui.mode === "sessions") renderBrowser(d);
  }
  MK.onRefreshSessions = loadBrowser;

  function renderNeedsBadge(d) {
    const b = $("needs-you");
    b.hidden = !d.waiting;
    $("needs-you-n").textContent = String(d.waiting || 0);
    const top = (d.needs_you || [])[0];
    b.title = top ? `${d.waiting} waiting on you — most urgent: ${top.text}` : "";
  }

  function needsLine(r) {
    const s = el("span", "ny-reason", r.reason === "awaiting-approval" ? "AWAITING APPROVAL" : r.reason);
    s.title = r.text;
    return s;
  }

  function renderBrowser(d) {
    const ul = $("sb-list");
    ul.replaceChildren();
    for (const s of d.sessions || []) {
      const b = el("button", "item sb-row" + (ui.focus === s.id ? " active" : "") + (s.needs_you.length ? " waiting" : ""));
      b.type = "button";
      const head = el("span", "sb-head");
      const dot = el("span", "st-dot st-" + s.status);
      dot.title = `${s.status} — ${s.status_basis}`;
      head.append(dot, el("span", "mono", `session ${s.short}`));
      if (s.run) head.append(el("span", "dim small mono", `run ${String(s.run).slice(0, 12)}`));
      for (const r of s.needs_you) head.append(needsLine(r));
      b.append(head);
      const t = s.tokens || {};
      const spend = (t.measured && t.measured.events ? `${t.measured.input + t.measured.output} tok measured` : "")
        + (t.estimated && t.estimated.events ? `${t.measured && t.measured.events ? " · " : ""}~${t.estimated.input + t.estimated.output} tok est.` : "");
      b.append(el("span", "sub", `${(s.files || []).length} file(s) · ${s.writes} write(s) · gates ${s.gates.fired}`
        + `${s.gates.blocked ? " (" + s.gates.blocked + " blocked)" : ""}${spend ? " · " + spend : ""} · ${s.last_iso || ""}`));
      b.onclick = () => switchTo(s.id);
      ul.append(MK.wrapLi(b));
    }
    $("sb-empty").hidden = (d.sessions || []).length > 0;
    const ny = $("ny-list");
    ny.replaceChildren();
    for (const q of d.needs_you || []) {
      const li = el("li", "ny-item");
      const b = el("button", "link", q.session ? `session ${q.short}` : "this repo");
      b.type = "button";
      b.onclick = () => { if (q.session) switchTo(q.session); };
      li.append(needsLine(q), b, el("span", "dim small", ` waiting ${ago(q.waiting_secs)} · risk ${q.risk}`));
      const det = q.detail || {};
      if (det.command) li.append(el("code", "ny-cmd", det.command));
      else if (det.gate) li.append(el("span", "small mono", ` ${det.gate}: ${det.decision} ${det.subject || ""}`));
      ny.append(li);
    }
    $("ny-empty").hidden = (d.needs_you || []).length > 0;
    $("ny-formula").textContent = d.risk_formula || "";
  }

  // ----------------------------------------------------------------------------- switcher
  // Focus another session quickly: from the browser, `>` / `<` to cycle, `!` for the most urgent.
  async function switchTo(sid) {
    if (!sid || sid === ui.focus) { if (ui.mode === "sessions") renderAll(); return; }
    ui.focus = sid; ui.picked = [];
    await api("/api/focus", { session: sid });
    ui.view = "session";
    await MK.refresh();
  }
  MK.switchSession = switchTo;

  function cycle(delta) {
    const rows = (sv.data && sv.data.sessions) || [];
    if (!rows.length) return;
    const i = rows.findIndex((s) => s.id === ui.focus);
    switchTo(rows[(i + delta + rows.length) % rows.length].id);
  }
  function urgent() {
    const q = ((sv.data && sv.data.needs_you) || []).find((x) => x.session);
    if (q) { MK.setMode("sessions"); switchTo(q.session); }
  }

  // ----------------------------------------------------------------------------- time-lapse
  async function loadTimelapse() {
    stop();
    $("tl-empty").hidden = !!ui.focus;
    $("tl-box").hidden = !ui.focus;
    if (!ui.focus) return;
    const d = await api("/api/timelapse?session=" + encodeURIComponent(ui.focus)).catch(() => null);
    if (!d || d.error) return;
    sv.tl = d;
    const r = $("tl-range");
    r.max = String(Math.max(0, d.steps.length - 1));
    sv.step = Math.max(0, d.steps.length - 1);
    r.value = String(sv.step);
    $("tl-note").textContent = `${d.count} step(s)${d.truncated ? " (newest shown)" : ""} · ${d.reads}`;
    drawStep();
  }

  function drawStep() {
    const d = sv.tl;
    if (!d) return;
    const st = d.steps[sv.step];
    const touched = {};
    for (const s of d.steps.slice(0, sv.step + 1)) {
      if (!s.path) continue;
      const t = touched[s.path] || (touched[s.path] = { writes: 0, lines: 0 });
      if (s.kind === "write") { t.writes += 1; t.lines += (s.added || 0) + (s.removed || 0); }
    }
    const ul = $("tl-files");
    ul.replaceChildren();
    for (const p of d.files) {
      const t = touched[p];
      const li = el("li", "tl-file mono" + (t ? " lit" : "") + (st && st.path === p ? " now" : ""));
      li.append(el("span", "", p), el("span", "dim small", t ? ` ${t.writes} write(s) · ${t.lines} line(s)` : " not yet"));
      ul.append(li);
    }
    const lab = $("tl-label");
    if (!st) { lab.textContent = "no steps recorded for this session"; return; }
    const when = new Date(st.t * 1000).toLocaleTimeString();
    lab.textContent = st.kind === "gate"
      ? `${sv.step + 1}/${d.steps.length} · ${when} · gate ${st.gate}: ${st.decision}${st.blocked ? " (BLOCKED)" : ""}`
      : `${sv.step + 1}/${d.steps.length} · ${when} · ${st.kind} ${st.path}`
        + (st.kind === "write" ? (st.measured ? ` (+${st.added} −${st.removed})` : " (delta not measured)") : "");
    $("tl-show").disabled = !st.snap;
  }

  function stop() { if (sv.timer) { clearInterval(sv.timer); sv.timer = 0; } $("tl-play").textContent = "Play"; }
  function play() {
    if (sv.timer) { stop(); return; }
    if (!sv.tl || !sv.tl.steps.length) return;
    if (sv.step >= sv.tl.steps.length - 1) sv.step = 0;
    $("tl-play").textContent = "Pause";
    sv.timer = setInterval(() => {
      if (!sv.tl || sv.step >= sv.tl.steps.length - 1) { stop(); return; }
      sv.step += 1; $("tl-range").value = String(sv.step); drawStep();
    }, 400);
  }

  // ----------------------------------------------------------------------------- treemap
  async function loadTreemap() {
    const qs = `?size=${sv.size}` + (ui.focus ? "&session=" + encodeURIComponent(ui.focus) : "");
    const d = await api("/api/treemap" + qs).catch(() => null);
    const svg = $("tm");
    svg.replaceChildren();
    if (!d || d.error) { $("tm-note").textContent = d ? `treemap unavailable: ${d.note || d.error}` : ""; return; }
    svg.setAttribute("viewBox", `0 0 ${d.width} ${d.height}`);
    for (const dr of d.dirs) {
      const r = svgEl("rect", { x: dr.x, y: dr.y, width: dr.w, height: dr.h, class: "tm-dir" });
      const t = svgEl("title"); t.textContent = dr.path + "/"; r.append(t);
      svg.append(r);
    }
    for (const f of d.files) {
      if (f.w <= 0 || f.h <= 0) continue;
      const r = svgEl("rect", { x: f.x, y: f.y, width: f.w, height: f.h, class: "tm-file tm-h" + f.heat });
      const t = svgEl("title");
      t.textContent = `${f.path} · ${f.loc ?? "?"} LOC` + (f.writes ? ` · ${f.writes} write(s) · +${f.added} −${f.removed}` : " · unchanged");
      r.append(t);
      r.addEventListener("click", () => MK.openFile && MK.openFile(f.path, 1, null));
      svg.append(r);
      if (f.w > 60 && f.h > 14) {
        const lab = svgEl("text", { x: f.x + 3, y: f.y + 11, class: "tm-label" });
        lab.textContent = f.path.slice(f.path.lastIndexOf("/") + 1);
        svg.append(lab);
      }
    }
    $("tm-note").textContent = `${d.count} file(s) sized by ${d.sized_by}; ${d.changed} changed`
      + `${ui.focus ? " by this session" : " by any session"}${d.omitted ? ` · ${d.omitted} small unchanged file(s) omitted` : ""}`;
  }

  // ----------------------------------------------------------------------------- wiring
  function renderAll() {
    if (sv.data) renderBrowser(sv.data);
    loadTimelapse();
    loadTreemap();
  }
  MK.onModeSessions = (mode) => { if (mode === "sessions") renderAll(); else stop(); };
  MK.onRefreshSessionViews = () => { if (ui.mode === "sessions") { loadTimelapse(); loadTreemap(); } };

  MK.wireSessions = function wireSessions() {
    $("needs-you").onclick = urgent;
    $("tl-range").oninput = () => { stop(); sv.step = +$("tl-range").value; drawStep(); };
    $("tl-play").onclick = play;
    $("tl-show").onclick = () => {
      const st = sv.tl && sv.tl.steps[sv.step];
      if (st && st.snap && MK.pickSnapshot) MK.pickSnapshot({ id: st.snap, path: st.path });
    };
    for (const b of document.querySelectorAll("button.pill[data-tmsize]")) {
      b.onclick = () => {
        sv.size = b.dataset.tmsize;
        for (const x of document.querySelectorAll("button.pill[data-tmsize]")) x.classList.toggle("active", x === b);
        loadTreemap();
      };
    }
    document.addEventListener("keydown", (ev) => {
      if (MK.typing(ev) || ev.metaKey || ev.ctrlKey || ev.altKey || !$("qo").hidden) return;
      if (ev.key === ">") { ev.preventDefault(); cycle(1); }
      else if (ev.key === "<") { ev.preventDefault(); cycle(-1); }
      else if (ev.key === "!") { ev.preventDefault(); urgent(); }
    });
  };
})();
