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
    const st = { c, phase: "idle", decision: null };

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
      box.append(acts);
      if (!A.tty.available) box.append(el("p", "small dim ap-why", A.tty.reason || "Approve in your terminal:"),
        el("code", "ap-cmd", cc.command));
      if (st.phase === "code") box.append(codeRow());
      if (st.note) box.append(el("p", "small ap-note" + (st.bad ? " bad" : ""), st.note));
    }

    function btn(label, cls, fn) { const b = el("button", cls + " small", label); b.type = "button"; b.onclick = fn; return b; }

    async function ask(decision) {
      st.decision = decision; st.note = "asking for a code…"; st.bad = false; render();
      const r = await MK.api("/api/approvals/code", { id: st.c.proposal_id, decision }).catch((e) => ({ error: "unreachable", note: String(e) }));
      if (!r || r.error) { st.phase = "idle"; st.note = (r && r.note) || "could not ask for a code"; st.bad = true; if (r && r.error === "NoTerminal") A.tty = { available: false, reason: r.note }; render(); return; }
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
      const cancel = btn("Cancel", "btn ghost", () => { st.phase = "idle"; st.note = ""; render(); });
      inp.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); send(inp.value); } });
      row.append(inp, go, cancel);
      return row;
    }

    async function send(code) {
      const r = await MK.api("/api/approvals/decide", { id: st.c.proposal_id, decision: st.decision, code })
        .catch((e) => ({ error: "unreachable", note: String(e) }));
      if (!r || r.error) {
        st.note = (r && r.note) || "refused"; st.bad = true;
        if (/void|expired|no code/.test(st.note)) st.phase = "idle";
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

    box.update = (c2) => { st.c = Object.assign({}, st.c, c2); render(); };
    if (!c.render) {
      // A gated action handed us only an id: fetch the card in full.
      MK.api("/api/approvals?id=" + encodeURIComponent(c.proposal_id)).then((d) => { if (d && d.one) box.update(d.one); }).catch(() => null);
    }
    render();
    return box;
  }
  MK.approvalCard = approvalCard;

  // ---------------------------------------------------------------------------- the lists
  function hosts() { return [document.getElementById("approvals-drawer"), document.getElementById("approvals-review")].filter(Boolean); }

  function paint() {
    for (const host of hosts()) {
      const keep = new Map([...host.querySelectorAll(".ap-card")].map((n) => [n.dataset.pid, n]));
      host.replaceChildren();
      const shown = A.pending.concat([...A.decided.values()].filter((c) => !A.pending.some((p) => p.proposal_id === c.proposal_id)).slice(-3));
      if (host.id === "approvals-review") host.hidden = !shown.length;
      if (!shown.length) {
        if (host.id === "approvals-drawer") host.append(el("p", "small dim", "No durable write is waiting on you."));
        continue;
      }
      host.append(el("div", "ap-title small", `${A.pending.length} durable write(s) waiting on you`));
      for (const c of shown) {
        const old = keep.get(c.proposal_id);
        // A card mid-decision (a code being typed) is kept as it is; others are redrawn.
        if (old && old.querySelector("input.ap-code")) { host.append(old); continue; }
        host.append(approvalCard(c));
      }
    }
  }

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

  MK.wireApprovals = function wireApprovals() { reload(); };

  window.mokataIDE.register({
    id: "approvals",
    commands: [{ id: "approvals.open", title: "Approvals: what is waiting on you",
                 run: () => { if (MK.jobs) MK.jobs.open(); reload(); } }],
  });
})();
