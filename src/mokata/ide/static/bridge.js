// mokata ide — the IDE → session half of the bridge (ID.S3, doc 109 §4):
//   * Join: attach to a live session from the registry `mokata windows` reads — focus + follow it;
//   * Send to session: the human's explicit "send this to Claude" (selection / file / diff). The
//     page sends NAMES only (path + lines); the server reads, bounds and redacts the text, and the
//     session's next turn carries it through mokata's existing per-turn injection;
//   * /open?path=&line= (an OSC-8 link printed by the CLI) lands here and opens the file.
"use strict";

(function () {
  const MK = window.MK;
  const $ = MK.$, el = MK.el;
  const B = { joined: null };

  function toast(text, warn) {
    const t = $("bridge-toast");
    t.textContent = text;
    t.className = "bridge-toast small" + (warn ? " warn" : "");
    t.hidden = false;
    clearTimeout(B.tt);
    B.tt = setTimeout(() => { t.hidden = true; }, 4000);
  }

  // ------------------------------------------------------------------ join
  function setJoined(sid) {
    B.joined = sid;
    $("join-btn").textContent = sid ? `joined ${sid.slice(0, 8)}` : "Join";
    $("join-btn").setAttribute("aria-pressed", String(!!sid));
    if (sid && MK.setFollow) MK.setFollow(true);
  }
  async function loadJoin() {
    const r = await MK.api("/api/join").catch(() => null);
    const ul = $("join-list");
    ul.replaceChildren();
    if (!r || r.error) { ul.append(el("li", "dim small", "could not list sessions")); return; }
    setJoined(r.joined || null);
    if (!r.sessions.length) {
      ul.append(el("li", "dim small", "No session is known yet — one appears when a Claude Code window starts in this repo (or writes a file)."));
      return;
    }
    for (const s of r.sessions) {
      const li = el("li", "join-row");
      const dot = el("span", "jdot " + (s.alive === true ? "on" : s.alive === false ? "off" : ""), "●");
      dot.title = s.alive === true ? "live (its process is running)" : s.alive === false ? "not running" : "seen in snapshots";
      const b = el("button", "link mono", s.short);
      b.type = "button";
      b.title = "join: focus and follow this session; context you send goes to it";
      b.onclick = () => join(s.id);
      const meta = el("span", "dim small",
        [s.phase, s.scope, s.branch, s.writes ? `${s.writes} write(s)` : "", s.last_seen].filter(Boolean).join(" · "));
      li.append(dot, b, meta);
      if (s.alive !== true) {
        const rb = el("button", "pill-link small", "resume in terminal");
        rb.type = "button";
        rb.title = "claude --resume " + s.id;
        rb.onclick = () => { $("join-pop").hidden = true; if (MK.termStart) MK.termStart("claude", s.id); };
        li.append(rb);
      }
      if (s.id === B.joined) li.classList.add("active");
      ul.append(li);
    }
  }
  async function join(sid) {
    const r = await MK.api("/api/join", { session: sid }).catch(() => null);
    if (!r || r.error) { toast((r && r.note) || "could not join", true); return; }
    $("join-pop").hidden = true;
    setJoined(r.joined);
    toast(r.joined ? `joined ${r.joined.slice(0, 8)} — following its edits` : "left the session");
  }
  function onJoinEvent(ev) {
    let e;
    try { e = JSON.parse(ev.data); } catch (_) { return; }
    if (e && e.kind === "join") setJoined(e.session || null);
  }

  // ------------------------------------------------------------------ send to session
  function selectedLines() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
    const row = (n) => (n && (n.nodeType === 1 ? n : n.parentElement));
    const a = row(sel.anchorNode) && row(sel.anchorNode).closest(".cl");
    const b = row(sel.focusNode) && row(sel.focusNode).closest(".cl");
    if (!a || !b || !$("code").contains(a) || !$("code").contains(b)) return null;
    const x = parseInt(a.dataset.n, 10), y = parseInt(b.dataset.n, 10);
    return `${Math.min(x, y)}-${Math.max(x, y)}`;
  }
  async function sendNav() {
    const path = MK.nav && MK.nav.path;
    if (!path) { toast("open a file first", true); return; }
    const lines = selectedLines();
    await sendItem(lines ? { kind: "selection", path, lines } : { kind: "file", path });
  }
  async function sendDiff() {
    const f = MK.ui.files[MK.ui.sel];
    if (!f) { toast("no changed file selected", true); return; }
    await sendItem({ kind: "diff", path: f.path });
  }
  async function sendItem(body) {
    const r = await MK.api("/api/context", body).catch(() => null);
    if (!r || r.error) { toast((r && r.note) || "not sent", true); return; }
    const q = r.queued;
    const who = q.target ? `session ${q.target.slice(0, 8)}` : "the next session turn";
    toast(`queued for ${who}: ${q.kind} ${q.path}${q.lines ? ":" + q.lines.join("-") : ""}`
      + (q.redacted ? ` · ${q.redacted} line(s) redacted` : "") + " — it arrives with your next prompt");
  }

  // ------------------------------------------------------------------ wiring
  MK.wireBridge = function wireBridge() {
    if (!$("join-btn")) return;
    $("join-btn").onclick = async () => {
      const p = $("join-pop");
      p.hidden = !p.hidden;
      if (!p.hidden) await loadJoin();
    };
    $("join-leave").onclick = () => join(null);
    $("send-ctx").onclick = sendNav;
    $("send-diff").onclick = sendDiff;
    MK.onBusExtra = onJoinEvent;
    MK.api("/api/join").then((r) => { if (r && !r.error) setJoined(r.joined || null); }).catch(() => {});
  };

  // An OSC-8 link from the CLI: /open → /?open=<path>&line=<n>. Opened once the page is up.
  MK.openFromLink = function openFromLink() {
    const u = new URL(location.href);
    const path = u.searchParams.get("open");
    if (!path || !MK.openFile) return false;
    const line = parseInt(u.searchParams.get("line") || "1", 10) || 1;
    history.replaceState(null, "", "/");
    MK.openFile(path, line, null);
    return true;
  };
})();
