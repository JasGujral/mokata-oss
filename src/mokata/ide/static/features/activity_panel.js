// mokata ide — Sessions › Timeline and the session header (0.0.27 P12, ID-ACTIVITY-PANEL; folded
// into Sessions by UX-SIMPLIFY §5 #7; doc 109 §4.1/§14, PRD §5.3).
//
// What the picked session is doing RIGHT NOW, and what it did:
//   * the session header's card (#s1-card) — its pipeline phase, what it last ran (tool, file),
//     files touched, the run's RED/GREEN test record, its gate verdicts, and its derived NEXT STEP
//     (P14). What waits on you is the waiting bar's (approvals.js), never a second copy here;
//   * the TIMELINE (Sessions › Timeline): every item of its history, newest first, PAGED from the
//     server (`next` cursor, never capped) and VIRTUALISED here (only the rows in view are in the
//     DOM), with live items prepended as they happen. A resumed session shows its predecessors'
//     history too. Click a write to see it; shift-click a second write to compare the two moments.
// The one read is `/api/activity` (+ `/api/activity/history` per page); every later change ARRIVES
// on the page's event stream (`bus` edit/tool/turn_end/event/approval, `job`) — nothing here polls.
// THE EDGE: gates, approvals and the test record sit beside every tool call — governed activity,
// not a raw log. SAFETY: repo text goes into the DOM through textContent only.
"use strict";

(function () {
  const MK = window.MK;
  if (!MK || typeof document === "undefined") return;   // node tests of the pure helpers
  const el = MK.el;
  const ROW = 26;                 // one timeline row, px (fixed: the scroller does the arithmetic)
  const OVERSCAN = 8;
  const STAGES = ["brainstorm", "spec", "test", "develop", "review", "ship"];
  const P = { data: null, sel: null, chain: [], items: [], next: null, loading: false, types: new Set(),
              total: null, el: {}, drawer: null, reloadTimer: 0, seq: 0, picked: null, loadingLive: false,
              // P14 — each card's derived next step: answers by session, the sessions being re-asked,
              // one request in flight (+ the sessions owed a trailing one), per-session answer tokens
              hints: {}, hintBusy: new Set(), hintFlight: false, hintOwed: new Set(), hintTok: 0,
              hintApplied: {}, hintReqs: 0, busDown: false, hintWhy: [] };

  // ------------------------------------------------------------------------- words
  function ago(ts, now) {
    if (!ts) return "—";
    const s = Math.max(0, Math.round((now || Date.now() / 1000) - ts));
    if (s < 5) return "just now";
    if (s < 60) return s + " s ago";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago";
    return Math.floor(s / 86400) + " d ago";
  }
  function clock(t) {
    if (!t) return "";
    const d = new Date(t * 1000);
    const two = (n) => String(n).padStart(2, "0");
    return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
  }
  function stateWord(a) {
    if (!a) return "unknown";
    if (a.state === "working") return a.last_tool ? `working · ${a.last_tool}` : "working";
    return a.state;
  }
  // The same item shape the server answers (ide/activity.py), built here for a bus event so a
  // live item slots into the loaded timeline. Content-free, like the server's.
  const BLOCK = new Set(["block", "blocked", "deny", "denied", "refused", "fail", "failed", "would-block", "no-handback"]);
  const PASS = new Set(["pass", "passed", "allow", "allowed", "ok", "approved", "handback"]);
  function verdictOf(d) { d = String(d || "").toLowerCase(); return BLOCK.has(d) ? "blocked" : PASS.has(d) ? "passed" : d === "advisory" ? "advisory" : ""; }
  function itemFromBus(e) {
    const t = e.ts || Date.now() / 1000;
    const base = { id: "bus:" + (e.id || P.seq++), t, session: e.session || "", run: e.run || "", source: "bus", ledger_seq: null, verdict: "" };
    if (e.kind === "edit" && typeof e.path === "string") return Object.assign(base, { type: "edit", title: "wrote " + e.path, detail: { path: e.path, tool: e.tool || "" } });
    if (e.kind === "tool") {
      if (typeof e.seq === "number") { base.id = "ev:" + e.seq; base.source = "events"; }   // stored: same id as history
      return Object.assign(base, { type: "tool", title: String(e.tool || "tool"), verdict: "ok", detail: { tool: e.tool || "", surface: "claude-code", durable: typeof e.seq === "number" } });
    }
    if (e.kind === "turn_end") return Object.assign(base, { type: "turn", title: "turn ended — your move", verdict: "waiting", detail: {} });
    if (e.kind !== "event") return null;
    // a typed-store row keeps the id the history gives it (`ev:<seq>`): a page loaded after the
    // push carries the same row and the two are ONE item
    if (typeof e.seq === "number") base.id = "ev:" + e.seq;
    const d = e.data || {};
    if (e.type === "gate_decision") {
      const g = String(d.gate || ""), dec = String(d.decision || "");
      if (g === "review") return Object.assign(base, { type: "review", title: "review " + dec, verdict: verdictOf(dec) || dec, detail: { decision: dec, stage: d.subject || "" } });
      if (g === "tdd") return Object.assign(base, { type: "test", title: "tdd · " + dec, verdict: verdictOf(dec), detail: { decision: dec } });
      if (g === "subagent-contract") return Object.assign(base, { type: "subagent", title: "subagent finished · " + dec, verdict: verdictOf(dec), detail: { decision: dec } });
      return Object.assign(base, { type: "gate", title: `${g || "gate"} · ${dec || "recorded"}` + (d.subject ? " · " + d.subject : ""), verdict: verdictOf(dec), detail: { gate: g, decision: dec, subject: d.subject || "" } });
    }
    if (e.type === "phase_transition") return Object.assign(base, { type: "phase", title: d.outcome === "finish" ? `${d.phase} finished` : `entered ${d.phase}`, detail: { phase: d.phase, outcome: d.outcome } });
    if (e.type === "approval_decision") return Object.assign(base, { type: "approval", title: "approval · " + (d.decision || ""), verdict: verdictOf(d.decision) === "passed" ? "approved" : verdictOf(d.decision) === "blocked" ? "rejected" : "", detail: { decision: d.decision, subject: d.subject || "", actor: d.actor || "" } });
    if (e.type === "secret_scan_hit") return Object.assign(base, { type: "secret", title: "secret caught · " + (d.kind || "") + (d.where ? " · " + d.where : ""), verdict: "hit", detail: { kind: d.kind, where: d.where } });
    if (e.type === "memory_op") return Object.assign(base, { type: "memory", title: "memory · " + (d.op || ""), detail: { op: d.op, mtype: d.mtype } });
    if (e.type === "token_spend") return Object.assign(base, { type: "tokens", title: `tokens · ${d.label || ""} · ${d.input_tokens || 0} in / ${d.output_tokens || 0} out` + (d.estimated === false ? " (measured)" : " (estimated)"), detail: { label: d.label, input: d.input_tokens, output: d.output_tokens, estimated: d.estimated !== false } });
    if (e.type === "tool_call") return Object.assign(base, { type: "tool", title: String(d.tool || "tool") + (d.ok === false ? " · failed" : ""), verdict: d.ok === false ? "failed" : "ok", detail: { tool: d.tool, surface: d.surface, ok: d.ok !== false } });
    return Object.assign(base, { type: "activity", title: String(e.type || "event"), detail: d });
  }

  // ------------------------------------------------------------------------- live cards
  function phaseBar(ph) {
    const bar = el("div", "ap-phase");
    const stages = (ph && ph.stages && ph.stages.length) ? ph.stages : STAGES.map((s) => ({ stage: s, status: "pending" }));
    for (const s of stages) {
      const seg = el("span", "ap-seg " + (s.status || "pending"), "");
      seg.title = `${s.stage}: ${s.status}` + (s.blocks ? ` · ${s.blocks} gate block(s)` : "");
      bar.append(seg);
    }
    const word = el("span", "small mono ap-phase-word", ph ? (ph.finished ? "shipped" : (ph.current || "no stage yet")) : "no run");
    bar.append(word);
    return bar;
  }
  // ------------------------------------------------------------------------- next step (P14)
  // Each card's "Next:" line is DERIVED on the server from what mokata recorded for the session's
  // run (`/api/hints`, `mokata.hints`) and names the fact it came from (hover). Nothing here guesses
  // a step, runs one, or turns "could not read" into "nothing to do". Dismissal is per hint KEY (its
  // rule + its own facts): unrelated activity never brings a dismissed hint back; its own fact
  // changing does, because the key moves with it.
  const DISMISS_KEY = "mk.hints.dismissed";
  function dismissed() {
    try { return new Set(JSON.parse(localStorage.getItem(DISMISS_KEY) || "[]")); } catch (_e) { return new Set(); }
  }
  function dismiss(key) {
    const d = dismissed();
    d.add(key);
    try { localStorage.setItem(DISMISS_KEY, JSON.stringify([...d].slice(-500))); } catch (_e) { /* private mode: this page only */ }
    P.dismissedNow = P.dismissedNow || new Set();
    P.dismissedNow.add(key);
  }
  function isDismissed(key) { return dismissed().has(key) || !!(P.dismissedNow && P.dismissedNow.has(key)); }
  const SIDE = new Set(["approve", "graph-unbuilt"]);
  function nextLine(s) {
    const box = el("div", "ap-next small");
    box.dataset.sid = s.id;
    const busy = P.hintBusy.has(s.id);
    box.setAttribute("aria-busy", busy ? "true" : "false");
    if (busy) box.classList.add("rechecking");
    const ans = P.hints[s.id];
    box.append(el("b", "", "Next: "));
    if (!ans) { box.append(el("span", "dim", busy ? "checking the recorded state…" : "—")); return box; }
    if (ans.status === "unknown") {
      box.classList.add("unknown");
      box.append(el("span", "", "unknown — " + (ans.reason || "a store could not be read")));
      box.append(asOf(ans));
      box.title = "derived from: recorded state — this is NOT \"nothing to do\"";
      return box;
    }
    const shown = (ans.hints || []).filter((h) => !isDismissed(h.key));
    const h = shown.find((x) => !SIDE.has(x.rule)) || shown[0];
    if (!h) {
      box.append(el("span", "dim", ans.status === "none" ? "nothing owed — " + (ans.reason || "") : "dismissed"));
      box.append(asOf(ans));
      return box;
    }
    const txt = el("span", "ap-next-text", h.text);
    box.append(txt);
    const cmd = el("code", "ap-cmd", h.command);
    cmd.title = "nothing runs from here — run it yourself; it asks for its own consent";
    box.append(cmd);
    const why = el("span", "dim ap-next-why", " from " + h.cites.map((c) => c.source + ": " + c.ref).join(" · "));
    box.append(why);
    if (shown.length > 1) box.append(el("span", "dim", ` · +${shown.length - 1} more`));
    box.append(asOf(ans));
    const x = el("button", "btn ghost small ap-next-x", "×");
    x.type = "button";
    x.setAttribute("aria-label", "Dismiss this hint");
    x.title = "dismiss — it comes back when its own fact changes";
    const off = () => { dismiss(h.key); renderCards(); const c = P.el.cards && P.el.cards.querySelector(`.ap-card[data-sid="${CSS.escape(s.id)}"]`); if (c) c.focus(); };
    x.onclick = off;
    box.addEventListener("keydown", (ev) => { if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); off(); } });
    box.append(x);
    return box;
  }
  // When the answer was derived (a clock time: true however long it sits on screen), and — with the
  // live stream down — that it may be out of date. A hint never reads as live when it is not.
  function asOf(ans) {
    const at = ans && ans.derived_at;
    const t = el("span", "dim ap-next-asof", at ? " · as of " + clock(at) : "");
    if (P.busDown) { t.classList.add("stale"); t.textContent += " — may be out of date (live updates off)"; }
    return t;
  }
  // Ask for `sids`' next steps: ONE request in flight; sessions named meanwhile wait for ONE
  // trailing request. An answer older than one already applied for a session is dropped.
  async function askHints(sids, why) {
    sids = [...new Set(sids)].filter(Boolean);
    if (!sids.length) return;
    P.hintWhy.push(why || "owed");            // what asked (newest last) — a test reads it
    if (P.hintWhy.length > 500) P.hintWhy.shift();
    for (const sid of sids) P.hintBusy.add(sid);
    if (P.hintFlight) { for (const sid of sids) P.hintOwed.add(sid); paintNext(); return; }
    P.hintFlight = true;
    const tok = ++P.hintTok;
    paintNext();
    P.hintReqs++;
    const d = await MK.api("/api/hints?session=" + encodeURIComponent(sids.slice(0, 50).join(","))).catch((e) => ({ error: "unreachable", note: String(e && e.message || e) }));
    P.hintFlight = false;
    for (const sid of sids) {
      if (P.hintOwed.has(sid)) continue;       // a newer ask is owed: stay "re-checking"
      P.hintBusy.delete(sid);
      if ((P.hintApplied[sid] || 0) > tok) continue;
      P.hintApplied[sid] = tok;
      if (d && !d.error && d.answers && d.answers[sid]) P.hints[sid] = d.answers[sid];
      else P.hints[sid] = { status: "unknown", reason: MK.whyFailed(d, "read the next step"), hints: [] };
    }
    if (P.hintOwed.size) { const owed = [...P.hintOwed]; P.hintOwed.clear(); askHints(owed, "trailing"); }
    paintNext();
  }
  function paintNext() {          // repaint only the "Next:" lines (the cards keep their focus)
    paintReviewNext();
    if (!P.el.cards) return;
    for (const old of P.el.cards.querySelectorAll(".ap-next")) {
      const s = P.data && P.data.sessions.find((x) => x.id === old.dataset.sid);
      if (s) old.replaceWith(nextLine(s));
    }
  }
  function askShown(why) {
    why = typeof why === "string" ? why : "focus";
    const s = focusRow();
    if (s && !P.loadingLive && visible()) askHints([s.id], why);
  }
  function hintSettled() { return !P.hintFlight && !P.hintOwed.size && !P.hintBusy.size; }

  function card(s) {
    const c = el("article", "ap-card " + s.status + (s.id === P.sel ? " selected" : ""));
    c.dataset.sid = s.id;
    const head = el("div", "ap-head");
    const dot = el("span", "ap-dot " + (s.activity && s.activity.state || "unknown"));
    dot.title = (s.activity && s.activity.text) || "";
    head.append(dot, el("b", "mono", s.short || s.id.slice(0, 8)), el("span", "ap-prompt", s.prompt || "(no prompt recorded)"));
    const st = el("span", "small dim ap-status", s.status + (s.branch ? " · " + s.branch : ""));
    st.title = s.status_basis || "";
    head.append(st);
    c.append(head, phaseBar(s.phase));
    const a = s.activity || {};
    const line = el("div", "ap-line small");
    line.append(el("span", "ap-state " + (a.state || "unknown"), stateWord(a)));
    if (a.last_path) line.append(el("span", "mono", " · " + a.last_path));
    if (a.at) line.append(el("span", "dim", " · " + ago(a.at)));
    line.title = (a.text || "") + (a.basis ? " — " + a.basis : "");
    c.append(line);
    const f = s.files || {}, g = s.gates || {}, tests = (s.phase && s.phase.tests) || null;
    const facts = el("div", "ap-facts small mono");
    facts.append(el("span", "", `${f.count || 0} file(s) · ${f.writes || 0} write(s)`));
    if (tests) facts.append(el("span", (tests.failing ? "red" : tests.passing ? "green" : ""), ` · tests ${tests.failing} red / ${tests.passing} green`));
    facts.append(el("span", g.blocked ? "red" : "", ` · gates ${g.fired || 0} fired${g.blocked ? ", " + g.blocked + " blocked" : ""}`));
    if (s.secret_hits) facts.append(el("span", "red", ` · ${s.secret_hits} secret hit(s)`));
    if ((s.chain || []).length > 1) facts.append(el("span", "dim", ` · resumed ×${s.chain.length - 1}`));
    c.append(facts);
    if ((s.needs_you || []).length) {
      const w = el("div", "ap-wait small", `waiting on you (${s.needs_you.length}) — `);
      const b = el("button", "link small", "show"); b.type = "button";
      b.onclick = () => { if (MK.waiting) MK.waiting.toggle(true); };
      w.append(b);
      c.append(w);
    }
    c.tabIndex = -1;                  // focus comes back here when its hint is dismissed
    c.append(nextLine(s));
    if (s.status === "live") {
      const acts = el("div", "ap-acts");
      const b = el("button", "btn ghost small", "Follow");
      b.type = "button";
      b.title = "open each file this session edits as it edits it";
      b.onclick = () => { if (MK.setFollow) MK.setFollow(true); };
      acts.append(b);
      c.append(acts);
    }
    return c;
  }
  // The session header's card: the PICKED session (the Viewing focus) — one card, never a list.
  function focusRow() {
    const d = P.data;
    if (!d) return null;
    const sid = MK.ui.focus;
    return (sid && d.sessions.find((x) => x.id === sid || (x.chain || []).includes(sid))) || null;
  }
  function renderCards() {
    const host = P.el.cards;
    if (!host) return;
    host.replaceChildren();
    const s = focusRow();
    if (s) host.append(card(s));
    paintReviewNext();
  }
  // P14 at the Review file header (§5 #44): the Viewing session's next step, where the decision is
  // made. Painted from the answer the session header asked for; Review asks once if none is known.
  function paintReviewNext() {
    const box = document.getElementById("rv-next");
    if (!box) return;
    const sid = MK.ui.focus;
    const ans = sid ? P.hints[sid] : null;
    const h = ans && ans.status !== "unknown" ? (ans.hints || []).filter((x) => !isDismissed(x.key)).find((x) => !SIDE.has(x.rule)) : null;
    box.hidden = !h;
    box.replaceChildren();
    if (!h) return;
    const cmd = el("code", "ap-cmd", h.command);
    cmd.title = "nothing runs from here — run it yourself; it asks for its own consent";
    box.append(el("b", "", "Next: "), el("span", "", h.text + " "), cmd);
    box.title = "derived from " + h.cites.map((c) => c.source + ": " + c.ref).join(" · ");
  }
  MK.onModeReviewNext = (mode) => {
    const sid = MK.ui.focus;
    if (mode === "review" && sid && !P.hints[sid] && !P.hintBusy.has(sid)) askHints([sid], "review");
    else paintReviewNext();
  };

  // ------------------------------------------------------------------------- the timeline
  function select(sid) {
    if (!sid) return;
    P.sel = sid; P.items = []; P.next = null; P.total = null; P.picked = null;
    const s = (P.data && P.data.sessions || []).find((x) => x.id === sid || (x.chain || []).includes(sid));
    P.chain = s ? (s.chain || [sid]) : [sid];
    P.el.tlTitle.textContent = `Timeline · session ${s ? s.short : sid.slice(0, 8)}`;
    P.el.detail.replaceChildren();
    renderRows(true);
    loadPage();
  }
  async function loadPage() {
    if (P.loading || !P.sel) return;
    P.loading = true;
    P.el.tlNote.textContent = P.items.length ? "loading older…" : "loading…";
    const types = [...P.types].join(",");
    const qs = `?session=${encodeURIComponent(P.sel)}` + (P.next ? `&before=${encodeURIComponent(P.next)}` : "") + (types ? `&types=${encodeURIComponent(types)}` : "");
    const want = P.sel;
    const d = await MK.api("/api/activity/history" + qs).catch(() => null);
    P.loading = false;
    if (want !== P.sel) return;
    if (!d || d.error) { P.el.tlNote.textContent = MK.whyFailed(d, "load the history"); return; }
    P.chain = d.joined || P.chain;
    if (d.total) P.total = d.total;                 // counted on the first page only (F1)
    const seen = new Set(P.items.map((i) => i.id));
    for (const i of d.items) if (!seen.has(i.id)) P.items.push(i);
    // a write the bus pushed before its page arrived: the snapshot item is the durable one
    const snaps = d.items.filter((i) => i.source === "snapshots");
    P.items = P.items.filter((i) => !(i.source === "bus" && (i.type === "edit" || i.type === "before")
      && snaps.some((s) => s.detail.path === i.detail.path && Math.abs(s.t - i.t) < 3)));
    P.items.sort((a, b) => b.t - a.t);
    P.next = d.next;
    const t = P.total || {};
    P.el.tlNote.textContent = `${P.items.length} shown · ${t.events || 0} event(s) · ${t.writes || 0} write(s)` + ((d.joined || []).length > 1 ? ` · joined ${d.joined.length} session(s)` : "") + (d.next ? " · scroll for older" : " · complete");
    if (d.sample) MK.markSample(d);
    renderRows();
  }
  function prepend(item) {
    if (!item || P.items.some((i) => i.id === item.id)) return;     // already loaded from a page
    P.items.unshift(item);
    const t = P.total || { events: 0, writes: 0 };
    if (item.type === "edit" || item.type === "before") t.writes = (t.writes || 0) + 1; else t.events = (t.events || 0) + 1;
    P.total = t;
    renderRows();
  }
  function badge(type, verdict) {
    const b = el("span", "ap-type t-" + type + (verdict ? " v-" + verdict : ""), type);
    return b;
  }
  function row(i) {
    const r = el("div", "ap-row" + (P.picked && P.picked.id === i.id ? " picked" : ""));
    r.style.height = ROW + "px";
    r.append(el("span", "mono dim ap-time", clock(i.t)), badge(i.type, i.verdict), el("span", "ap-title", i.title));
    if (i.verdict) r.append(el("span", "ap-verdict v-" + i.verdict, i.verdict));
    if ((P.chain || []).length > 1 && i.session) r.append(el("span", "mono dim small", i.session.slice(0, 8)));
    if (i.ledger_seq !== null && i.ledger_seq !== undefined) { const l = el("span", "mono dim small", "#" + i.ledger_seq); l.title = "audit-ledger row (hash-chained)"; r.append(l); }
    r.title = i.title + ((i.detail || {}).snap ? " — shift-click a second write to compare the two moments" : "");
    r.setAttribute("role", "listitem");
    r.tabIndex = 0;
    r.onclick = (ev) => {
      const a = P.picked && P.picked.detail, b = i.detail || {};
      if (ev.shiftKey && a && a.snap && b.snap && a.snap !== b.snap) { compare(P.picked, i); return; }
      pick(i);
    };
    r.onkeydown = (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); r.onclick(ev); } };
    return r;
  }
  function renderRows(reset) {
    const sc = P.el.scroll, inner = P.el.inner, rows = P.el.rows;
    if (!sc) return;
    if (reset) sc.scrollTop = 0;
    const n = P.items.length;
    inner.style.height = Math.max(n * ROW, ROW) + "px";
    const first = Math.max(0, Math.floor(sc.scrollTop / ROW) - OVERSCAN);
    const last = Math.min(n, Math.ceil((sc.scrollTop + sc.clientHeight) / ROW) + OVERSCAN);
    rows.style.transform = `translateY(${first * ROW}px)`;
    rows.replaceChildren();
    if (!n) rows.append(el("div", "dim small ap-empty", P.loading ? "" : P.sel ? "Nothing recorded for this session yet." : "Pick a session on the left to see its timeline."));
    for (let k = first; k < last; k++) rows.append(row(P.items[k]));
    // older pages: fetched as the user nears the end (server-paged, never capped)
    if (P.next && !P.loading && last >= n - OVERSCAN) loadPage();
  }
  function pick(i) {
    P.picked = i;
    const box = P.el.detail;
    box.replaceChildren();
    const h = el("div", "ap-dhead");
    h.append(badge(i.type, i.verdict), el("b", "", i.title), el("span", "dim small mono", ` ${clock(i.t)} · ${i.source}` + (i.session ? ` · ${i.session.slice(0, 8)}` : "") + (i.run ? ` · run ${i.run}` : "")));
    box.append(h);
    const dl = el("div", "ap-kv small mono");
    for (const [k, v] of Object.entries(i.detail || {})) {
      if (v === "" || v === null || v === undefined) continue;
      dl.append(el("span", "dim", k), el("span", "", typeof v === "object" ? JSON.stringify(v) : String(v)));
    }
    if (i.ledger_seq !== null && i.ledger_seq !== undefined) dl.append(el("span", "dim", "ledger"), el("span", "", "#" + i.ledger_seq + " (hash-chained audit row)"));
    box.append(dl);
    const d = i.detail || {};
    const acts = el("div", "ap-acts");
    if (d.path && MK.openFile) { const b = el("button", "btn ghost small", "Open file"); b.type = "button"; b.onclick = () => MK.openFile(d.path, 1, null); acts.append(b); }
    if (d.snap && d.path && MK.pickSnapshot) { const b = el("button", "btn ghost small", "Diff this write"); b.type = "button"; b.onclick = () => MK.pickSnapshot({ id: d.snap, path: d.path }); acts.append(b); }
    if (d.command) acts.append(el("code", "ap-cmd", d.command));
    if (acts.childNodes.length) box.append(acts);
    renderRows();
  }
  // Two moments compared (what the Sessions per-write list did): Review shows a → b.
  function compare(a, b) {
    const ui = MK.ui;
    ui.picked = [a.detail.snap, b.detail.snap];
    ui.pickedPaths[a.detail.snap] = a.detail.path; ui.pickedPaths[b.detail.snap] = b.detail.path;
    ui.view = "between";
    MK.setMode("review");
    MK.refresh();
  }
  function typeChips() {
    const host = P.el.chips;
    host.replaceChildren();
    const types = (P.data && P.data.types) || {};
    const all = el("button", "pill" + (P.types.size ? "" : " active"), "all"); all.type = "button";
    all.setAttribute("aria-pressed", String(!P.types.size));
    all.onclick = () => { P.types.clear(); typeChips(); if (P.sel) select(P.sel); };
    host.append(all);
    for (const [t, why] of Object.entries(types)) {
      if (t === "explainer") continue;                       // reserved for P46 — nothing produces it yet
      const b = el("button", "pill" + (P.types.has(t) ? " active" : ""), t); b.type = "button"; b.title = why;
      b.setAttribute("aria-pressed", String(P.types.has(t)));
      b.onclick = () => { if (P.types.has(t)) P.types.delete(t); else P.types.add(t); typeChips(); if (P.sel) select(P.sel); };
      host.append(b);
    }
  }

  // ------------------------------------------------------------------------- loading + live
  async function load() {
    P.loadingLive = true;
    const d = await MK.api("/api/activity").catch((e) => ({ error: "unreachable", note: String(e && e.message || e) }));
    P.loadingLive = false;
    if (!d || d.error) { P.el.tlNote.textContent = MK.whyFailed(d, "read the activity"); return; }
    P.data = d;
    MK.markSample(d);
    if (P.el.bridge) { P.el.bridge.hidden = !(d.bridge && d.bridge.note); P.el.bridge.textContent = (d.bridge && d.bridge.note) || ""; }
    renderCards();
    const f = focusRow();
    if (f) askHints([f.id], "load");
    typeChips();
    follow();
  }
  // The timeline follows the picked session (the Viewing focus); with none picked, the session the
  // server says is focused, else the newest live one.
  function follow() {
    const d = P.data;
    const want = MK.ui.focus || (d && ((d.sessions.find((s) => s.focused) || d.sessions[0] || {}).id));
    if (!want) return;
    if (want !== P.sel && !(P.chain || []).includes(want) || !P.sel) select(want);
    else if (!P.items.length && !P.loading) loadPage();
  }
  function reloadSoon() {           // a pushed change: ONE re-read, coalesced (never a timer at idle)
    clearTimeout(P.reloadTimer);
    P.reloadTimer = setTimeout(() => { P.reloadTimer = 0; if (visible()) load(); else P.data = null; }, 400);
  }
  // Nothing in flight: the live read answered, no coalesced re-read is pending, no history page
  // is loading. What a test (or a caller) waits on before counting requests.
  function settled() { return !!P.data && !P.loadingLive && !P.reloadTimer && !P.loading; }
  // The Sessions screen is in view (its header shows the card; Timeline the history).
  function visible() { const v = document.getElementById("sessions-head"); return !!v && !v.hidden; }
  function touchCard(sid, fn) {
    if (!P.data) return false;
    const s = P.data.sessions.find((x) => x.id === sid || (x.chain || []).includes(sid));
    if (!s) return false;
    fn(s);
    return true;
  }
  // A pushed change that can move a next step re-asks ONLY the cards it names (its session, or the
  // cards of its run); a change that names nobody (an approval) re-asks every card shown.
  // `runstate` is the watcher's word that a run-state record moved with no event behind it — a test
  // run, verdict, spec or finish recorded in a terminal (`runs` names them; [] means any run).
  const HINT_KINDS = new Set(["edit", "tool", "event", "approval", "turn_end", "session_end", "runstate"]);
  function hintsFor(e) {
    if (!HINT_KINDS.has(e.kind)) return;
    if (e.kind === "runstate") {
      const runs = new Set(e.runs || []);
      const hit = P.data.sessions.filter((s) => s === focusRow() && (!runs.size || runs.has(s.run)));
      if (hit.length && visible()) askHints(hit.map((s) => s.id), "runstate");
      return;
    }
    if (e.kind === "event" && typeof e.seq === "number" && e.seq <= P.data.store_seq) return;
    const rows = P.data.sessions.filter((s) => s === focusRow());
    let hit = rows.filter((s) => e.session && (s.id === e.session || (s.chain || []).includes(e.session)));
    if (!hit.length && e.run) hit = rows.filter((s) => s.run === e.run);
    if (!hit.length && !e.session && !e.run) hit = rows;
    if (hit.length && visible()) askHints(hit.map((s) => s.id), "bus:" + e.kind);
  }
  function onBus(ev) {
    let e; try { e = JSON.parse(ev.data); } catch (_e) { return; }
    if (!e || !e.kind) return;
    // Before the first live read answered (or while it is in flight) there is nothing to update:
    // the read that is coming carries the state these events changed. Acting on them here used to
    // schedule an extra re-read (a store-tail push landing before `load` set P.data — F3).
    if (!P.data || P.loadingLive) return;
    hintsFor(e);
    if (e.kind === "approval") { reloadSoon(); return; }
    const sid = e.session || "";
    const inChain = sid && (P.chain || []).includes(sid) || (e.kind === "event" && e.run && P.data && P.data.sessions.some((s) => s.id === P.sel && s.run === e.run));
    const now = e.ts || Date.now() / 1000;
    let known = false;
    if (e.kind === "edit" || e.kind === "tool") {
      known = touchCard(sid, (s) => {
        s.activity = Object.assign(s.activity || {}, { state: "working", at: now, last_tool: e.tool || (e.kind === "edit" ? "Write" : ""), tools: ((s.activity || {}).tools || 0) + 1, basis: "bus (live)" });
        if (e.kind === "edit") { s.activity.last_path = e.path; s.files = s.files || { count: 0, writes: 0 }; s.files.writes++; s.files.last = e.path; }
      });
    } else if (e.kind === "turn_end") {
      known = touchCard(sid, (s) => { s.activity = Object.assign(s.activity || {}, { state: "idle", at: now, text: "the agent ended its turn — it is your move" }); });
    } else if (e.kind === "session_end") {
      known = touchCard(sid, (s) => { s.status = "ended"; s.activity = Object.assign(s.activity || {}, { state: "ended", at: now }); });
    } else if (e.kind === "event" && P.data && typeof e.seq === "number" && e.seq <= P.data.store_seq) {
      known = true;                     // the tail caught up on a row the last read already counted
    } else if (e.kind === "event") {
      known = touchCard(sid, (s) => {
        if (e.type === "gate_decision") { s.gates = s.gates || { fired: 0, blocked: 0 }; s.gates.fired++; if (BLOCK.has(String((e.data || {}).decision || "").toLowerCase())) s.gates.blocked++; }
        if (e.type === "phase_transition" && s.phase && (e.data || {}).phase) { const ph = (e.data || {}).phase; s.phase.current = ph; for (const st of s.phase.stages) st.status = STAGES.indexOf(st.stage) < STAGES.indexOf(ph) ? "done" : st.stage === ph ? "current" : "pending"; }
        if (e.type === "secret_scan_hit") s.secret_hits = (s.secret_hits || 0) + 1;
      });
      if (!known && e.run && P.data) { const s = P.data.sessions.find((x) => x.run === e.run); if (s) { s.gates.fired++; known = true; } }
    }
    if (known && visible()) renderCards();
    if ((e.kind === "session_start" || e.kind === "session_end" || (!known && sid)) && visible()) reloadSoon();   // a session the panel did not know
    if (inChain && visible()) prepend(itemFromBus(e));
  }

  // ------------------------------------------------------------------------- mount
  function mount(_side, view) {
    P.el.cards = document.getElementById("s1-card");
    P.el.bridge = el("p", "small ap-bridge"); P.el.bridge.hidden = true;
    view.append(P.el.bridge);
    P.el.busNote = el("p", "small ap-busdown");
    P.el.busNote.setAttribute("role", "status");
    P.el.busNote.append(el("span", "", "live updates off — the next step shown may be out of date. "));
    const rb = el("button", "btn ghost small", "Refresh"); rb.type = "button"; rb.onclick = () => load();
    P.el.busNote.append(rb);
    P.el.busNote.hidden = !P.busDown;
    view.append(P.el.busNote);
    const tlHead = el("div", "ap-tlhead");
    P.el.tlTitle = el("h2", "", "Timeline");
    P.el.tlNote = el("span", "small dim", "");
    P.el.chips = el("div", "ap-chips");
    P.el.chips.setAttribute("role", "group");
    P.el.chips.setAttribute("aria-label", "show item types");
    const edge = el("span", "small dim ap-edge", "gates · approvals · tests beside every tool call");
    edge.title = "mokata's edge over a raw agent log: governed activity (doc 109 §14)";
    tlHead.append(P.el.tlTitle, P.el.tlNote, edge, P.el.chips);
    view.append(tlHead);
    const sc = (P.el.scroll = el("div", "ap-scroll"));
    sc.setAttribute("role", "list"); sc.setAttribute("aria-label", "session timeline");
    P.el.inner = el("div", "ap-inner");
    P.el.rows = el("div", "ap-rows");
    P.el.inner.append(P.el.rows);
    sc.append(P.el.inner);
    sc.addEventListener("scroll", () => renderRows());
    view.append(sc);
    P.el.detail = el("div", "ap-detail");
    view.append(P.el.detail);
    if (typeof ResizeObserver === "function") new ResizeObserver(() => renderRows()).observe(sc);
  }
  function onShow() {
    // events pushed while the view was hidden were not prepended: the first page is re-read
    if (P.sel) { P.items = []; P.next = null; P.total = null; }
    load();
  }
  // Any Sessions view (Plan & spec, Worktree) shows the header card: one read when it has none.
  MK.onModeSessionCard = (mode) => {
    if (MK.ui.tab === "sessions" && mode !== "activity" && !P.data && !P.loadingLive) load();
  };
  // The picked session changed (a row, < > !, the palette): the card and the timeline follow it.
  MK.onSessionPicked = () => {
    if (!visible()) return;
    if (!P.data) { load(); return; }
    renderCards();
    const f = focusRow();
    if (f && !P.hints[f.id]) askHints([f.id], "pick");
    if (MK.ui.mode === "activity") follow();
    else if (!focusRow()) load();
  };

  (MK.liveHooks = MK.liveHooks || []).push((es) => {
    es.addEventListener("bus", onBus);
    es.addEventListener("job", (ev) => {
      // an IDE test re-run that FINISHED recorded the facts a hint reads (other jobs — an index
      // build, a search — record none, and re-asking on them is noise)
      let j = null; try { j = JSON.parse(ev.data); } catch (_e) { /* not a job snapshot */ }
      if (j && JOB_RECORDS.has(j.kind) && JOB_ENDED.has(j.state)) askShown("job");
    });
    es.addEventListener("open", () => { setBusDown(false); if (visible()) load(); });    // (re)connected: catch up
    es.addEventListener("error", () => setBusDown(true));
  });
  const JOB_ENDED = new Set(["done", "failed", "cancelled"]);
  const JOB_RECORDS = new Set(["tests.run"]);
  // Coming back to the window or the tab: whatever was recorded meanwhile with no push (or while
  // the stream was down) is re-asked once — never a timer at idle.
  if (typeof window.addEventListener === "function") window.addEventListener("focus", () => askShown("focus"));
  if (typeof document.addEventListener === "function") {
    document.addEventListener("visibilitychange", () => { if (!document.hidden) askShown("visible"); });
  }
  function setBusDown(v) {
    const was = P.busDown;
    P.busDown = v;
    if (P.el.busNote) P.el.busNote.hidden = !v;
    if (was !== v) paintNext();
  }
  function reveal(index) {           // scroll the virtualised list so row `index` is in view
    if (!P.el.scroll || index < 0) return;
    P.el.scroll.scrollTop = Math.max(0, index * ROW - Math.floor(P.el.scroll.clientHeight / 2));
    renderRows();
  }
  MK.activityPanel = { select, items: () => P.items.slice(), itemFromBus, load, reveal, settled,
                       hintSettled, hintRequests: () => P.hintReqs, hintWhy: () => P.hintWhy.slice(), hints: () => Object.assign({}, P.hints),
                       setBusDown };

  window.mokataIDE.register({
    id: "activity-panel",
    commands: [{ id: "activity.panel", title: "Sessions: the picked session's timeline", run: () => MK.setMode("activity") }],
    panels: typeof document.createElement !== "function" ? [] : [{
      id: "activity", title: "Timeline", tab: "sessions", order: 10, mount, onShow }],
  });
})();
