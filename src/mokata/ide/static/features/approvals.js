// mokata ide — the approval card (0.0.26 P38, APPROVE-IN-IDE; PRD G6 / R29).
//
// A durable write an agent proposed is waiting on you. The card shows it IN FULL (the same render
// `mokata approve <id>` prints), the exact command that decides it in a terminal (Copy), and —
// only when `mokata ide --foreground` runs in your own terminal — Approve / Reject here: the
// window asks the server to print a ONE-TIME CODE to that terminal, and you type it back. The
// pairing token alone can never approve (an agent can read it from disk); the code never leaves
// the terminal (ide/approve_codes.py). It updates live when the decision lands from anywhere (a
// terminal, another window) and then shows the audit-ledger row.
//
// REUSABLE: `MK.approvalCard(card, opts)` builds one card from a /api/approvals card (or from any
// {proposal_id, command} a gated action answered with — P31's worktree merge, P21's gated merge:
// pass `{ proposal_id }` and it fetches the rest). `opts.onDecided(result)` runs after a decision.
// SAFETY: repo text goes in through textContent only.
"use strict";

(function () {
  const MK = window.MK;
  if (!MK || typeof document === "undefined" || !document.getElementById) return;
  const el = MK.el;
  const A = { tty: { available: false, reason: "" }, pending: [], decided: new Map(), seq: 0 };

  function copyBtn(label, text) {
    const b = el("button", "btn ghost small", label);
    b.type = "button";
    b.title = text;
    b.onclick = () => {
      const done = () => { b.textContent = "Copied"; setTimeout(() => { b.textContent = label; }, 1800); };
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, () => { b.textContent = text; });
      else b.textContent = text;
    };
    return b;
  }

  function ago(secs) { return secs < 90 ? `${Math.round(secs)} s` : `${Math.round(secs / 60)} min`; }

  function ledgerLines(rows) {
    const ul = el("ul", "ap-ledger small mono");
    for (const r of rows || []) {
      ul.append(el("li", "", `#${r.seq} · ${r.at || ""} · ${r.decision} by ${r.actor || r.approved_by || "?"}`
        + (r.decision === "redeemed" ? (r.committed ? " · committed" : " · not committed") : "")
        + (r.entry_hash ? ` · ${String(r.entry_hash).slice(0, 12)}` : "")));
    }
    return ul;
  }

  // ---------------------------------------------------------------------------- one card
  function approvalCard(c, opts) {
    const o = opts || {};
    const box = el("div", "ap-card");
    box.dataset.pid = c.proposal_id;
    // a repaint hands a card's note over to its replacement while the proposal's status is the
    // same ("Cancelled - nothing was decided" must not vanish on the next live reload)
    const kept = o.note && o.note.status === c.status ? o.note : null;
    const st = { c, phase: "idle", decision: null, note: kept ? kept.text : "", bad: kept ? kept.bad : false };

    function render() {
      const cc = st.c;
      box.replaceChildren();
      box.className = "ap-card st-" + (cc.status || "proposed");
      const head = el("div", "ap-head");
      head.append(el("b", "", cc.tool || "write"), el("span", "mono small", " " + cc.proposal_id));
      const badge = el("span", "ap-badge", cc.status === "proposed" ? "waiting on you" : cc.status || "");
      head.append(badge);
      if (cc.decidable && cc.expires_in) head.append(el("span", "dim small", ` · expires in ${ago(cc.expires_in)}`));
      box.append(head);
      if (cc.summary) box.append(el("p", "small", cc.summary));
      if (cc.render) { const pre = el("pre", "ap-render mono small", cc.render); box.append(pre); }
      if (!cc.decidable) {
        box.append(el("p", "small", cc.status === "approved" ? "Approved — the agent may now commit it once."
          : cc.status === "used" ? "Committed — the approval was used."
          : cc.status === "rejected" ? "Rejected — it will not be written."
          : cc.status === "expired" ? "Expired — ask the agent to re-run the write to see it again." : ""));
        if (cc.ledger && cc.ledger.length) { box.append(el("div", "small dim", "audit ledger")); box.append(ledgerLines(cc.ledger)); }
        return;
      }
      if (cc.status === "approved") box.append(el("p", "small", "Approved — the agent may now commit it once. You can still reject it until then."));
      if (cc.ledger && cc.ledger.length) { box.append(el("div", "small dim", "audit ledger")); box.append(ledgerLines(cc.ledger)); }
      const acts = el("div", "row ap-acts");
      if (A.tty.available) {
        if (st.phase === "idle") {
          if (cc.status !== "approved") acts.append(btn("Approve", "btn", () => ask("approve")));
          acts.append(btn("Reject", "btn ghost", () => ask("reject")));
        }
      }
      acts.append(copyBtn("Copy approve command", cc.command), copyBtn("Copy reject command", cc.reject_command));
      // 0.0.27 P46 — before a gated MERGE: a one-click OFFER of a cited explanation. Never a gate:
      // it asks Claude Code for a page and changes nothing about this approval.
      if (/merge/.test(cc.tool || "") && MK.explain) acts.append(btn("Explain changes", "btn ghost",
        () => { window.mokataIDE.show("explain"); MK.explain.ask(MK.ui.focus ? "session " + MK.ui.focus : "live"); }));
      box.append(acts);
      if (!A.tty.available) box.append(el("p", "small dim ap-why", A.tty.reason || "Approve in your terminal:"),
        el("code", "ap-cmd", cc.command));
      // 0.0.27 SMALL-FIX (DEFLAKE L4): while the one-time code is being asked for, Approve/Reject are
      // hidden (no double ask). The way out is a visible Cancel that the USER presses - it does not
      // wait for a timer, so a request that never answers still leaves a card you can leave and retry.
      if (st.phase === "asking") box.append(askingRow());
      if (st.phase === "code") box.append(codeRow());
      if (st.note) box.append(el("p", "small ap-note" + (st.bad ? " bad" : ""), st.note));
    }

    function btn(label, cls, fn) { const b = el("button", cls + " small", label); b.type = "button"; b.onclick = fn; return b; }

    function askingRow() {
      const row = el("div", "row ap-askrow");
      row.append(btn("Cancel", "btn ghost ap-cancel-ask", cancelAsk));
      return row;
    }

    function cancelAsk() {
      const ctl = st.ctl;
      st.ctl = null;                                     // the late answer, if any, is now ignored
      if (ctl) ctl.abort();
      st.phase = "idle"; st.bad = false;
      st.note = "Cancelled - nothing was decided. Approve or Reject again to ask for a new code.";
      render();
      const again = box.querySelector(".ap-acts button");
      if (again) again.focus();                           // focus never falls to <body>
      reload();                                           // a decision made elsewhere meanwhile shows
    }

    // 0.0.27 PINS-UI (DEFLAKE L1): while a code was asked for or typed, live reloads kept this card
    // as it was (paint() skips a busy card), so leaving that state must catch up: a decision made in
    // a terminal meanwhile is shown at once, not at the next unrelated event.
    // Keyboard focus was on a control this render removes (Cancel, the code input, Confirm):
    // it goes to the card's first action, never to <body> (round 2: a void code or a failed ask
    // dropped it there).
    function backToIdle(note, bad) {
      const had = box.contains(document.activeElement);
      st.phase = "idle"; st.note = note; st.bad = !!bad;
      render();
      const again = had ? box.querySelector(".ap-acts button") : null;
      if (again) again.focus();
      reload();
    }

    async function ask(decision) {
      // "asking" is NOT idle: a live reload that lands while the code is in flight must keep this
      // card (paint() asks `box.busy()`), or the code row renders into a detached node.
      st.decision = decision; st.phase = "asking"; st.note = "asking for a code…"; st.bad = false;
      const ctl = typeof AbortController === "function" ? new AbortController() : null;
      st.ctl = ctl;
      render();                                          // Cancel is on screen before the request can hang
      const cancel = box.querySelector(".ap-cancel-ask");
      if (cancel) cancel.focus();
      const r = await MK.api("/api/approvals/code", { id: st.c.proposal_id, decision }, ctl ? { signal: ctl.signal } : undefined)
        .catch((e) => ({ error: "unreachable", note: String(e) }));
      if (st.ctl !== ctl) return;                        // cancelled while it was in flight
      st.ctl = null;
      if (!r || r.error) { if (r && r.error === "NoTerminal") A.tty = { available: false, reason: r.note }; backToIdle((r && r.note) || "could not ask for a code", true); return; }
      st.phase = "code";
      st.note = `${r.note} (${r.tries} tries, ${r.expires_in} s).`;
      render();
      const inp = box.querySelector("input.ap-code");
      if (inp) inp.focus();
    }

    function codeRow() {
      const row = el("div", "row ap-coderow");
      const inp = el("input", "ap-code mono");
      inp.setAttribute("aria-label", `one-time code to ${st.decision} ${st.c.proposal_id}`);
      inp.placeholder = "ABCD-EFGH";
      inp.autocomplete = "off";
      inp.spellcheck = false;
      const go = btn(st.decision === "approve" ? "Confirm approve" : "Confirm reject", "btn", () => send(inp.value));
      const cancel = btn("Cancel", "btn ghost ap-cancel-code", () => backToIdle("Cancelled - nothing was decided here."));
      inp.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); send(inp.value); } });
      row.append(inp, go, cancel);
      return row;
    }

    async function send(code) {
      const r = await MK.api("/api/approvals/decide", { id: st.c.proposal_id, decision: st.decision, code })
        .catch((e) => ({ error: "unreachable", note: String(e) }));
      if (!r || r.error) {
        st.note = (r && r.note) || "refused"; st.bad = true;
        if (/void|expired|no code/.test(st.note)) { backToIdle(st.note, true); return; }
        render();
        return;
      }
      st.phase = "idle"; st.bad = false; st.note = r.message;
      if (r.proposal) st.c = Object.assign({}, r.proposal, { ledger: r.ledger });
      A.decided.set(st.c.proposal_id, st.c);          // stays on screen with its ledger row
      render();
      if (o.onDecided) o.onDecided(r);
      reload();
    }

    box.busy = () => st.phase !== "idle";
    box.note = () => (st.note ? { text: st.note, bad: st.bad, status: st.c.status } : null);
    box.update = (c2) => { st.c = Object.assign({}, st.c, c2); render(); };
    if (!c.render) {
      // A gated action handed us only an id: fetch the card in full.
      MK.api("/api/approvals?id=" + encodeURIComponent(c.proposal_id)).then((d) => { if (d && d.one) box.update(d.one); }).catch(() => null);
    }
    render();
    return box;
  }
  MK.approvalCard = approvalCard;

  // ---------------------------------------------------------------------------- the waiting bar
  // 0.0.27 UX-SIMPLIFY (§5 #23): ONE place a decision waiting on you is shown — the bar under the
  // top bar (#waiting). It holds the approval cards (above) and the sessions' needs-you queue
  // (sessions.js hands it over, risk-sorted). A queue item that IS a pending approval (same
  // command) is shown once, as its card. The bar is hidden when nothing waits.
  const W = { needs: [], open: false };
  const $ = (id) => document.getElementById(id);

  function needsOnly() {
    const cmds = new Set(A.pending.map((c) => c.command).filter(Boolean));
    const ids = new Set(A.pending.map((c) => c.proposal_id));
    return W.needs.filter((q) => {
      const d = q.detail || {};
      return !(d.command && cmds.has(d.command)) && !(d.proposal_id && ids.has(d.proposal_id));
    });
  }

  function needsItem(q) {
    const li = el("li", "wait-item");
    const r = el("span", "ny-reason", q.reason === "awaiting-approval" ? "awaiting approval" : String(q.reason || "").replace(/-/g, " "));
    li.append(r, el("span", "", " " + (q.text || "")));
    if (q.session) {
      const b = el("button", "link", `session ${q.short || String(q.session).slice(0, 8)}`);
      b.type = "button";
      b.onclick = () => { toggle(false); if (MK.switchSession) MK.switchSession(q.session); };
      li.append(document.createTextNode(" · "), b);
    }
    const d = q.detail || {};
    if (d.command) li.append(el("code", "ny-cmd", d.command), copyBtn("Copy", d.command));
    else if (d.gate) li.append(el("span", "small mono", ` ${d.gate}: ${d.decision} ${d.subject || ""}`));
    return li;
  }

  function paint() {
    const bar = $("waiting");
    if (!bar) return;
    const extra = needsOnly();
    const n = A.pending.length + extra.length;
    const shown = A.pending.concat([...A.decided.values()].filter((c) => !A.pending.some((p) => p.proposal_id === c.proposal_id)).slice(-3));
    // an open bar stays up while a card it holds shows its outcome; it hides once it is closed
    bar.hidden = n === 0 && !(W.open && shown.length);
    $("wait-n").textContent = n ? String(n) : "";
    const top = A.pending[0] || null;
    const q = top ? null : extra[0];
    $("wait-top").textContent = top ? `${top.tool || "a durable write"} — ${top.summary || top.proposal_id}`
      : q ? q.text || q.reason : "";
    const cmd = top ? top.command : q && q.detail ? q.detail.command : "";
    $("wait-cmd").hidden = !cmd;
    $("wait-cmd").textContent = cmd || "";
    const cp = $("wait-copy");
    cp.hidden = !cmd;
    cp.onclick = () => {
      const done = () => { cp.textContent = "Copied"; setTimeout(() => { cp.textContent = "Copy"; }, 1800); };
      if (navigator.clipboard) navigator.clipboard.writeText(cmd).then(done, () => { cp.textContent = cmd; });
    };
    // the cards (a card mid-decision — a code asked for or being typed — is kept as it is)
    const host = $("approvals-bar");
    const keep = new Map([...host.querySelectorAll(".ap-card")].map((x) => [x.dataset.pid, x]));
    // keyboard focus inside a card this repaint replaces goes to the same control on its
    // replacement (or the card's first control) — never to <body>
    const act = document.activeElement;
    const fCard = act && act.closest ? act.closest(".ap-card") : null;
    const focusAt = fCard && host.contains(fCard) ? { pid: fCard.dataset.pid, label: act.textContent, el: act } : null;
    host.replaceChildren();
    for (const c of shown) {
      const old = keep.get(c.proposal_id);
      if (old && old.busy && old.busy()) { host.append(old); continue; }
      host.append(approvalCard(c, old && old.note ? { note: old.note() } : undefined));
    }
    const ul = $("wait-other");
    ul.replaceChildren(...extra.map(needsItem));
    if (focusAt && !host.contains(document.activeElement)) {
      const card = [...host.querySelectorAll(".ap-card")].find((x) => x.dataset.pid === focusAt.pid);
      const ctl = card ? [...card.querySelectorAll("button, input")] : [];
      const to = ctl.includes(focusAt.el) ? focusAt.el : ctl.find((x) => x.textContent === focusAt.label) || ctl[0];
      if (to) to.focus();
      else if (!bar.hidden) $("wait-toggle").focus();     // its card went (decided elsewhere)
      else { const tab = document.querySelector('#screen-tabs [aria-selected="true"]'); if (tab) tab.focus(); }
    }
    if (!n && !shown.length && W.open) toggle(false);
  }

  function toggle(open) {
    W.open = open === undefined ? !W.open : !!open;
    $("wait-list").hidden = !W.open;
    const t = $("wait-toggle");
    t.setAttribute("aria-expanded", String(W.open));
    t.textContent = W.open ? "Hide" : "Show";
    const bar = $("waiting");
    const inBar = !!document.activeElement && bar.contains(document.activeElement);
    if (!W.open && inBar) t.focus();
    // Closing can be what ends the bar: a decided card kept it up only while open. Repaint, so a
    // bar with nothing waiting goes instead of staying as an empty "Show" (round 2, M1).
    if (!W.open) paint();
    // …and focus never falls to <body> with it: the screen's selected tab takes it.
    if (inBar && bar.hidden) { const tab = document.querySelector('#screen-tabs [aria-selected="true"]'); if (tab) tab.focus(); }
  }

  MK.waiting = {
    setNeeds: (list) => { W.needs = Array.isArray(list) ? list : []; paint(); },
    toggle, count: () => A.pending.length + needsOnly().length,
  };

  async function reload() {
    const seq = ++A.seq;
    const d = await MK.api("/api/approvals").catch(() => null);
    if (seq !== A.seq || !d || d.error) return;
    A.tty = d.approve_here || A.tty;
    A.pending = d.pending || [];
    paint();
  }
  MK.reloadApprovals = reload;

  // live: the server's watcher publishes `approval` when a proposal record changes (a terminal
  // approve, a reject, a redemption, a new proposal) — no polling.
  (MK.liveHooks = MK.liveHooks || []).push((es) => {
    es.addEventListener("open", () => reload());       // (re)connected: catch up on what changed
    es.addEventListener("bus", (ev) => {
      let e; try { e = JSON.parse(ev.data); } catch (_e) { return; }
      if (e && e.kind === "approval") reload();
    });
  });

  MK.wireApprovals = function wireApprovals() {
    $("wait-toggle").onclick = () => toggle();
    reload();
  };

  window.mokataIDE.register({
    id: "approvals",
    commands: [{ id: "approvals.open", title: "Approvals: what is waiting on you",
                 run: () => { reload().then(() => { if (MK.waiting.count()) { toggle(true); $("wait-toggle").focus(); } }); } }],
  });
})();
