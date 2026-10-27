// mokata ide — the embedded terminal (ID.S3, doc 109 §5): a pty on the server running the
// UNMODIFIED `claude` (or your shell), drawn here by a small ANSI/VT screen model.
//
// Why not xterm.js (doc 109 §3.2 names it): its DOM renderer injects <style> elements, which the
// page's strict CSP (style-src 'self', no inline) refuses, and nothing here is browser-tested in
// CI. This model covers what an Ink TUI and a shell emit — cursor addressing, erase, scroll
// regions, the alternate screen, SGR colours (16/256/truecolor), bracketed paste, application
// cursor keys and OSC 8 links — and renders ONLY through textContent and CSSOM (no markup).
//
// The pty stream is never stored: it lives in this page's memory and the server's bounded ring.
"use strict";

(function () {
  const MK = window.MK;
  const $ = MK.$, el = MK.el;
  const SB_MAX = 3000;

  // ------------------------------------------------------------------ the screen model
  function blank(st) { return { ch: " ", st }; }
  const PLAIN = Object.freeze({});

  function Screen(cols, rows) {
    this.cols = cols; this.rows = rows; this.st = PLAIN;
    this.main = this.grid(rows); this.alt = null; this.g = this.main;
    this.x = 0; this.y = 0; this.saved = { x: 0, y: 0 };
    this.top = 0; this.bot = rows - 1;
    this.st = PLAIN; this.link = null;
    this.appCursor = false; this.paste = false; this.showCursor = true; this.wrapNext = false;
    this.sb = [];           // scrolled-off lines (main screen only)
    this.sbNew = [];        // not yet drawn
    this.p = { mode: 0, buf: "" };
  }
  Screen.prototype.grid = function (rows) {
    const g = [];
    for (let i = 0; i < rows; i++) g.push(this.line());
    return g;
  };
  Screen.prototype.line = function () {
    const l = [];
    for (let i = 0; i < this.cols; i++) l.push(blank(this.st.bg ? { bg: this.st.bg } : PLAIN));
    return l;
  };
  Screen.prototype.resize = function (cols, rows) {
    const fit = (g) => {
      for (const l of g) { while (l.length < cols) l.push(blank(PLAIN)); l.length = cols; }
      while (g.length < rows) g.push(this.line());
      while (g.length > rows) {
        const gone = g.shift();
        if (g === this.main) { this.sb.push(gone); this.sbNew.push(gone); }
      }
    };
    this.cols = cols;
    fit(this.main); if (this.alt) fit(this.alt);
    this.rows = rows; this.top = 0; this.bot = rows - 1;
    this.x = Math.min(this.x, cols - 1); this.y = Math.min(this.y, rows - 1);
  };
  Screen.prototype.scrollUp = function (n) {
    for (let i = 0; i < n; i++) {
      const gone = this.g.splice(this.top, 1)[0];
      this.g.splice(this.bot, 0, this.line());
      if (this.g === this.main && this.top === 0) {
        this.sb.push(gone); this.sbNew.push(gone);
        if (this.sb.length > SB_MAX) this.sb.shift();
      }
    }
  };
  Screen.prototype.scrollDown = function (n) {
    for (let i = 0; i < n; i++) { this.g.splice(this.bot, 1); this.g.splice(this.top, 0, this.line()); }
  };
  Screen.prototype.lf = function () {
    if (this.y === this.bot) this.scrollUp(1);
    else if (this.y < this.rows - 1) this.y++;
  };
  Screen.prototype.put = function (ch) {
    if (this.wrapNext) { this.wrapNext = false; this.x = 0; this.lf(); }
    const cell = { ch, st: this.st };
    if (this.link) cell.link = this.link;
    this.g[this.y][this.x] = cell;
    if (this.x === this.cols - 1) this.wrapNext = true; else this.x++;
  };
  Screen.prototype.erase = function (y, from, to) {
    const l = this.g[y];
    for (let i = Math.max(0, from); i < Math.min(this.cols, to); i++) l[i] = blank(this.st.bg ? { bg: this.st.bg } : PLAIN);
  };
  Screen.prototype.clampCursor = function () {
    this.x = Math.max(0, Math.min(this.cols - 1, this.x));
    this.y = Math.max(0, Math.min(this.rows - 1, this.y));
    this.wrapNext = false;
  };

  const C16 = ["#1b1f1d", "#ff6b6b", "#3df58c", "#ffb454", "#8ab4ff", "#d78bff", "#5fd7d7", "#d8e2dc",
    "#5f6b64", "#ff9b9b", "#8ff5bb", "#ffd28a", "#b4ceff", "#e6b4ff", "#9af0f0", "#ffffff"];
  function c256(n) {
    if (n < 16) return C16[n];
    if (n >= 232) { const v = 8 + (n - 232) * 10; return `rgb(${v},${v},${v})`; }
    n -= 16;
    const s = [0, 95, 135, 175, 215, 255];
    return `rgb(${s[Math.floor(n / 36)]},${s[Math.floor(n / 6) % 6]},${s[n % 6]})`;
  }
  Screen.prototype.sgr = function (ps) {
    if (!ps.length) ps = [0];
    let st = Object.assign({}, this.st);
    for (let i = 0; i < ps.length; i++) {
      const p = ps[i];
      if (p === 0) st = {};
      else if (p === 1) st.b = 1;
      else if (p === 2) st.dim = 1;
      else if (p === 3) st.i = 1;
      else if (p === 4) st.u = 1;
      else if (p === 7) st.inv = 1;
      else if (p === 22) { delete st.b; delete st.dim; }
      else if (p === 23) delete st.i;
      else if (p === 24) delete st.u;
      else if (p === 27) delete st.inv;
      else if (p >= 30 && p <= 37) st.fg = C16[p - 30];
      else if (p >= 90 && p <= 97) st.fg = C16[p - 90 + 8];
      else if (p === 39) delete st.fg;
      else if (p >= 40 && p <= 47) st.bg = C16[p - 40];
      else if (p >= 100 && p <= 107) st.bg = C16[p - 100 + 8];
      else if (p === 49) delete st.bg;
      else if (p === 38 || p === 48) {
        const key = p === 38 ? "fg" : "bg";
        if (ps[i + 1] === 5) { st[key] = c256(ps[i + 2] || 0); i += 2; }
        else if (ps[i + 1] === 2) { st[key] = `rgb(${ps[i + 2] || 0},${ps[i + 3] || 0},${ps[i + 4] || 0})`; i += 4; }
      }
    }
    this.st = Object.freeze(st);
  };
  Screen.prototype.csi = function (raw, fin) {
    const priv = /^[?>=<]/.test(raw) ? raw[0] : "";
    const ps = (priv ? raw.slice(1) : raw).split(";").map((v) => (v === "" ? NaN : parseInt(v, 10)));
    const n = (i, d) => (Number.isFinite(ps[i]) && ps[i] > 0 ? ps[i] : d);
    switch (fin) {
      case "A": this.y = Math.max(this.top > this.y ? 0 : this.top, this.y - n(0, 1)); this.wrapNext = false; break;
      case "B": this.y = Math.min(this.bot < this.y ? this.rows - 1 : this.bot, this.y + n(0, 1)); this.wrapNext = false; break;
      case "C": this.x += n(0, 1); this.clampCursor(); break;
      case "D": this.x -= n(0, 1); this.clampCursor(); break;
      case "E": this.y += n(0, 1); this.x = 0; this.clampCursor(); break;
      case "F": this.y -= n(0, 1); this.x = 0; this.clampCursor(); break;
      case "G": case "`": this.x = n(0, 1) - 1; this.clampCursor(); break;
      case "d": this.y = n(0, 1) - 1; this.clampCursor(); break;
      case "H": case "f": this.y = n(0, 1) - 1; this.x = n(1, 1) - 1; this.clampCursor(); break;
      case "J": {
        const m = Number.isFinite(ps[0]) ? ps[0] : 0;
        if (m === 0) { this.erase(this.y, this.x, this.cols); for (let y = this.y + 1; y < this.rows; y++) this.erase(y, 0, this.cols); }
        else if (m === 1) { this.erase(this.y, 0, this.x + 1); for (let y = 0; y < this.y; y++) this.erase(y, 0, this.cols); }
        else { for (let y = 0; y < this.rows; y++) this.erase(y, 0, this.cols); if (m === 3) this.sb.length = 0; }
        break;
      }
      case "K": {
        const m = Number.isFinite(ps[0]) ? ps[0] : 0;
        if (m === 0) this.erase(this.y, this.x, this.cols);
        else if (m === 1) this.erase(this.y, 0, this.x + 1);
        else this.erase(this.y, 0, this.cols);
        break;
      }
      case "X": this.erase(this.y, this.x, this.x + n(0, 1)); break;
      case "P": { const l = this.g[this.y]; l.splice(this.x, n(0, 1)); while (l.length < this.cols) l.push(blank(PLAIN)); break; }
      case "@": { const l = this.g[this.y]; for (let i = 0; i < n(0, 1); i++) l.splice(this.x, 0, blank(PLAIN)); l.length = this.cols; break; }
      case "L": if (this.y >= this.top && this.y <= this.bot) { const t = this.top; this.top = this.y; this.scrollDown(n(0, 1)); this.top = t; } break;
      case "M": if (this.y >= this.top && this.y <= this.bot) { const t = this.top; this.top = this.y; for (let i = 0; i < n(0, 1); i++) { this.g.splice(this.top, 1); this.g.splice(this.bot, 0, this.line()); } this.top = t; } break;
      case "S": this.scrollUp(n(0, 1)); break;
      case "T": this.scrollDown(n(0, 1)); break;
      case "r": this.top = n(0, 1) - 1; this.bot = Math.min(this.rows, n(1, this.rows)) - 1;
        if (this.top >= this.bot) { this.top = 0; this.bot = this.rows - 1; }
        this.x = 0; this.y = 0; break;
      case "s": this.saved = { x: this.x, y: this.y }; break;
      case "u": this.x = this.saved.x; this.y = this.saved.y; this.clampCursor(); break;
      case "m": if (!priv) this.sgr(ps.map((v) => (Number.isFinite(v) ? v : 0))); break;
      case "h": case "l": if (priv === "?") this.mode(ps, fin === "h"); break;
      default:
    }
  };
  Screen.prototype.mode = function (ps, on) {
    for (const p of ps) {
      if (p === 1) this.appCursor = on;
      else if (p === 25) this.showCursor = on;
      else if (p === 2004) this.paste = on;
      else if (p === 1049 || p === 1047 || p === 47) {
        if (on && !this.alt) { this.saved = { x: this.x, y: this.y }; this.alt = this.grid(this.rows); this.g = this.alt; }
        else if (!on && this.alt) { this.alt = null; this.g = this.main; this.x = this.saved.x; this.y = this.saved.y; this.clampCursor(); }
        this.top = 0; this.bot = this.rows - 1;
      }
    }
  };
  Screen.prototype.osc = function (s) {
    if (s.startsWith("8;")) {
      const url = s.slice(2).replace(/^[^;]*;/, "");
      this.link = url || null;
    }
  };
  Screen.prototype.feed = function (text) {
    const p = this.p;
    for (const ch of text) {
      const c = ch.codePointAt(0);
      if (p.mode === 0) {
        if (c === 0x1b) { p.mode = 1; continue; }
        if (c >= 0x20 && c !== 0x7f) { this.put(ch); continue; }
        if (c === 0x0d) { this.x = 0; this.wrapNext = false; }
        else if (c === 0x0a || c === 0x0b || c === 0x0c) { this.wrapNext = false; this.lf(); }
        else if (c === 0x08) { this.x = Math.max(0, this.x - 1); this.wrapNext = false; }
        else if (c === 0x09) { this.x = Math.min(this.cols - 1, (this.x + 8) & ~7); }
        continue;
      }
      if (p.mode === 1) {                       // after ESC
        p.mode = 0;
        if (ch === "[") { p.mode = 2; p.buf = ""; }
        else if (ch === "]") { p.mode = 3; p.buf = ""; }
        else if (ch === "(" || ch === ")" || ch === "*" || ch === "+") p.mode = 4;
        else if (ch === "7") this.saved = { x: this.x, y: this.y };
        else if (ch === "8") { this.x = this.saved.x; this.y = this.saved.y; this.clampCursor(); }
        else if (ch === "D") this.lf();
        else if (ch === "E") { this.x = 0; this.lf(); }
        else if (ch === "M") { if (this.y === this.top) this.scrollDown(1); else if (this.y > 0) this.y--; }
        else if (ch === "c") { this.st = PLAIN; this.g = this.main; this.alt = null; this.main = this.grid(this.rows); this.g = this.main; this.x = this.y = 0; }
        continue;
      }
      if (p.mode === 2) {                       // CSI params … final
        if (c >= 0x40 && c <= 0x7e) { p.mode = 0; this.csi(p.buf, ch); }
        else if (p.buf.length < 64) p.buf += ch;
        continue;
      }
      if (p.mode === 3) {                       // OSC … BEL | ESC \
        if (c === 0x07) { p.mode = 0; this.osc(p.buf); }
        else if (c === 0x1b) p.mode = 5;
        else if (p.buf.length < 2048) p.buf += ch;
        continue;
      }
      if (p.mode === 5) { p.mode = 0; this.osc(p.buf); continue; }   // the "\" of ESC \
      if (p.mode === 4) { p.mode = 0; continue; }                     // charset designator
    }
  };

  MK.TermScreen = Screen;            // the model alone (tests drive it under node)

  // ------------------------------------------------------------------ drawing
  function sameOriginOpen(url) {
    try {
      const u = new URL(url, location.href);
      return u.origin === location.origin && u.pathname === "/open" ? u : null;
    } catch (_) { return null; }
  }
  function drawLine(cells, cursorX) {
    const row = el("div", "trow");
    let run = null, key = null;
    const flush = () => { if (run) row.append(run); run = null; };
    cells.forEach((cell, i) => {
      const cur = i === cursorX;
      const k = cell.st;
      if (!run || k !== key || cur || cell.link !== run._link || run._cur) {
        flush();
        const u = cell.link ? sameOriginOpen(cell.link) : null;
        run = el(u ? "a" : "span", cur ? "tcur" : "");
        run._link = cell.link; run._cur = cur;
        if (u) {
          run.href = u.pathname + u.search;
          run.className += " tlink";
          run.onclick = (ev) => {
            ev.preventDefault();
            const line = parseInt(u.searchParams.get("line") || "1", 10) || 1;
            if (MK.openFile) MK.openFile(u.searchParams.get("path") || "", line, null);
          };
        }
        let fg = k.fg, bg = k.bg;
        if (k.inv) { const t = fg || "var(--text)"; fg = bg || "var(--panel2)"; bg = t; }
        if (fg) run.style.color = fg;
        if (bg) run.style.backgroundColor = bg;
        if (k.b) run.style.fontWeight = "700";
        if (k.dim) run.style.opacity = "0.65";
        if (k.i) run.style.fontStyle = "italic";
        if (k.u) run.style.textDecoration = "underline";
        run.textContent = "";
        key = k;
      }
      run.textContent += cell.ch;
    });
    flush();
    return row;
  }

  // ------------------------------------------------------------------ the session
  const T = { id: null, scr: null, after: 0, polling: false, sending: Promise.resolve(),
              cw: 8, chH: 16, dec: null, frame: 0, caps: null };

  function setState(text, cls) {
    const s = $("term-state");
    s.textContent = text;
    s.className = "term-state small " + (cls || "");
  }
  function render() {
    T.frame = 0;
    const box = $("term-screen");
    const sb = $("term-sb");
    for (const l of T.scr.sbNew.splice(0)) sb.append(drawLine(l, -1));
    while (sb.childNodes.length > SB_MAX) sb.firstChild.remove();
    const scr = $("term-scr");
    scr.replaceChildren(...T.scr.g.map((l, y) =>
      drawLine(l, T.scr.showCursor && y === T.scr.y && document.activeElement === box ? T.scr.x : -1)));
    box.scrollTop = box.scrollHeight;
  }
  function schedule() { if (!T.frame) T.frame = requestAnimationFrame(render); }

  function measure() {
    const box = $("term-screen");
    const probe = el("span", "tprobe", "MMMMMMMMMM");
    box.append(probe);
    const r = probe.getBoundingClientRect();
    probe.remove();
    T.cw = r.width / 10 || 8; T.chH = r.height || 16;
    const cols = Math.max(20, Math.floor((box.clientWidth - 12) / T.cw));
    const rows = Math.max(5, Math.floor((box.clientHeight - 8) / T.chH));
    return { cols, rows };
  }
  async function resize() {
    if (!T.id || !T.scr) return;
    const { cols, rows } = measure();
    if (cols === T.scr.cols && rows === T.scr.rows) return;
    T.scr.resize(cols, rows);
    schedule();
    await MK.api("/api/term/resize", { id: T.id, cols, rows }).catch(() => null);
  }

  function send(data) {
    if (!T.id) return;
    const id = T.id;
    T.sending = T.sending.then(() => MK.api("/api/term/input", { id, data }).catch(() => null));
  }
  async function poll() {
    if (T.polling) return;
    T.polling = true;
    const id = T.id;
    while (T.id === id) {
      let r;
      try {
        r = await MK.api(`/api/term/out?id=${encodeURIComponent(id)}&after=${T.after}&wait=15`);
      } catch (_) { await new Promise((ok) => setTimeout(ok, 1000)); continue; }
      if (T.id !== id) break;
      if (!r || r.error) { ended(r && r.note); break; }
      if (r.data) {
        const bin = atob(r.data);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        T.scr.feed(T.dec.decode(bytes, { stream: true }));
        schedule();
      }
      T.after = r.next;
      if (!r.alive) { ended(r.exit === null || r.exit === undefined ? "" : `exit ${r.exit}`); break; }
    }
    T.polling = false;
  }
  function ended(why) {
    setState("session ended" + (why ? ` (${why})` : "") + " — start again, or resume a session from Join", "warn");
    T.id = null;
    $("term-close").hidden = true;
  }
  async function start(program, resume) {
    if (T.id) await stop();
    $("term-drawer").hidden = false;
    $("term-sb").replaceChildren();
    const { cols, rows } = measure();
    const body = { program, cols, rows };
    if (resume) body.resume = resume;
    const r = await MK.api("/api/term/open", body).catch(() => null);
    if (!r || r.error) { setState((r && r.note) || "could not start the terminal", "warn"); return; }
    T.id = r.id; T.after = 0; T.scr = new Screen(cols, rows); T.dec = new TextDecoder("utf-8");
    setState(`${program}${resume ? " --resume " + resume.slice(0, 8) : ""} · running`, "ok");
    $("term-close").hidden = false;
    $("term-screen").focus();
    schedule();
    poll();
  }
  async function stop() {
    const id = T.id;
    T.id = null;
    if (id) await MK.api("/api/term/close", { id }).catch(() => null);
    setState("closed");
    $("term-close").hidden = true;
  }
  MK.termStart = start;

  // ------------------------------------------------------------------ keys
  function keyData(ev) {
    const s = T.scr;
    const cur = (c) => (s && s.appCursor ? "\x1bO" : "\x1b[") + c;
    switch (ev.key) {
      case "Enter": return "\r";
      case "Backspace": return ev.altKey ? "\x1b\x7f" : "\x7f";
      case "Tab": return ev.shiftKey ? "\x1b[Z" : "\t";
      case "Escape": return "\x1b";
      case "ArrowUp": return cur("A");
      case "ArrowDown": return cur("B");
      case "ArrowRight": return cur("C");
      case "ArrowLeft": return cur("D");
      case "Home": return "\x1b[H";
      case "End": return "\x1b[F";
      case "Delete": return "\x1b[3~";
      case "PageUp": return "\x1b[5~";
      case "PageDown": return "\x1b[6~";
      default:
    }
    if (ev.key.length !== 1) return null;
    if (ev.ctrlKey && !ev.altKey) {
      const c = ev.key.toUpperCase().charCodeAt(0);
      if (c >= 64 && c <= 95) return String.fromCharCode(c - 64);
      if (ev.key === " ") return "\x00";
      return null;
    }
    return (ev.altKey ? "\x1b" : "") + ev.key;
  }
  function onKey(ev) {
    if (!T.id) return;
    if (ev.metaKey) return;                                  // ⌘C / ⌘V stay the browser's
    if (ev.ctrlKey && ev.shiftKey && (ev.key === "C" || ev.key === "V")) return;
    const d = keyData(ev);
    if (d === null) return;
    ev.preventDefault();
    ev.stopPropagation();
    send(d);
  }
  function onPaste(ev) {
    if (!T.id) return;
    const t = (ev.clipboardData && ev.clipboardData.getData("text")) || "";
    ev.preventDefault();
    if (!t) return;
    const body = t.replace(/\r?\n/g, "\r");
    send(T.scr && T.scr.paste ? "\x1b[200~" + body + "\x1b[201~" : body);
  }

  // ------------------------------------------------------------------ wiring
  async function caps() {
    T.caps = await MK.api("/api/term/caps").catch(() => null);
    const c = T.caps;
    const ok = !!(c && c.available);
    // G22 (B-F15): no TTY → the Terminal button is HIDDEN and the reason is said in its place
    // (it used to open a drawer of disabled buttons with the reason only in /api/term/caps).
    const why = $("term-why");
    if ($("term-toggle")) $("term-toggle").hidden = !ok;
    if (why) { why.hidden = ok; why.textContent = ok ? "" : "Terminal: " + ((c && (c.reason || c.note)) || "unavailable here"); }
    $("term-claude").disabled = !ok || !(c.programs || {}).claude;
    $("term-shell").disabled = !ok || !(c.programs || {}).shell;
    if (!ok) setState((c && c.reason) || "the terminal is unavailable", "warn");
    else if (!(c.programs || {}).claude) setState("`claude` is not on PATH — your shell is available", "");
    else if (!T.id) setState("start claude or your shell — it runs here, signed in your own way", "");
    return ok;
  }
  MK.termCaps = caps;
  MK.wireTerminal = function wireTerminal() {
    const box = $("term-screen");
    if (!box) return;
    $("term-toggle").onclick = async () => {
      const d = $("term-drawer");
      d.hidden = !d.hidden;
      $("term-toggle").setAttribute("aria-pressed", String(!d.hidden));
      if (!d.hidden) { await caps(); if (T.id) { box.focus(); resize(); } }
    };
    $("term-claude").onclick = () => start("claude");
    $("term-shell").onclick = () => start("shell");
    $("term-close").onclick = stop;
    box.addEventListener("keydown", onKey);
    box.addEventListener("paste", onPaste);
    box.addEventListener("focus", schedule);
    box.addEventListener("blur", schedule);
    let rt = 0;
    window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(resize, 150); });
    // Page closed → the terminal is killed now (the server's idle reaper is the backstop).
    window.addEventListener("pagehide", () => {
      if (!T.id) return;
      fetch("/api/term/close", { method: "POST", keepalive: true,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: T.id }) });
    });
  };
})();
