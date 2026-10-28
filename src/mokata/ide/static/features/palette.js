// mokata ide — ONE palette, five prefixes (0.0.26 P3, ID-PALETTE-FULL). Moved here from app.js's
// ⌘P and widened:
//
//   (none)  files — changed-in-this-session first, then the repo, then symbols (/api/quickopen)
//   @       symbols in the current file (its outline)
//   #       symbols in the whole workspace (the graph layer's symbol index — /api/quickopen "@")
//   :       a line in the current file
//   >       commands — every IDE command in the registry AND every /mokata:* command and skill
//   /       only the /mokata:* commands ("/spe", "/mokata:spe")
//
// Keys (all through the registry): ⌘P files (again: changed only) · ⌘K the palette · ⌘⇧P commands
// · ⌘G go to line. Every command row shows its shortcut (which-key). A /mokata:* command is COPIED
// for the human to paste into their Claude Code session — the IDE never types into a session by
// itself (P2); the server's /api/context route carries references, never instructions.
// SAFETY: repo text and command descriptions enter the page through textContent only.
"use strict";

(function () {
  const MK = window.MK;
  const $ = (id) => (MK.$ ? MK.$(id) : null);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  };

  // --------------------------------------------------------------------------- pure logic
  const PREFIX = { "@": "symbol", "#": "workspace", ":": "line", ">": "command", "/": "slash" };
  const LABEL = { files: "Go to", symbol: "Symbol", workspace: "Workspace", line: "Line",
                  command: "Command", slash: "mokata" };

  function parse(q) {
    const s = String(q || "");
    const mode = PREFIX[s.charAt(0)];
    if (!mode) return { mode: "files", needle: s };
    let needle = s.slice(1);
    if (mode === "slash") needle = needle.replace(/^mokata:/i, "");
    return { mode, needle: needle.trim() };
  }

  // One rule, the server's: a case-insensitive subsequence. A prefix beats a substring beats a
  // scattered match; shorter wins a tie. null = no match.
  function score(needle, text) {
    const n = String(needle || "").toLowerCase(), t = String(text || "").toLowerCase();
    if (!n) return 0;
    const at = t.indexOf(n);
    if (at === 0) return 1000 - t.length * 0.1;
    if (at > 0) return (/[\s/:._-]/.test(t.charAt(at - 1)) ? 900 : 800) - at - t.length * 0.1;
    let j = 0, gaps = 0, last = -1;
    for (let i = 0; i < t.length && j < n.length; i++) {
      if (t.charAt(i) === n.charAt(j)) { if (last >= 0 && i !== last + 1) gaps++; last = i; j++; }
    }
    return j === n.length ? 400 - gaps * 5 - t.length * 0.1 : null;
  }

  // The `>` list: IDE commands (by title) and /mokata:* entries (by name, then description).
  // Ties go to what the human most likely wants: a mokata COMMAND, then an IDE command, then a skill.
  function rankCommands(needle, ide, slash) {
    const rows = [];
    for (const c of ide || []) {
      const s = score(needle, c.title);
      if (s !== null) rows.push({ type: "ide", s, tie: 1, c });
    }
    for (const c of slash || []) {
      let s = score(needle, c.name);
      if (s === null) s = score(needle, c.slash);
      if (s === null) { const d = score(needle, c.description); s = d === null ? null : d - 500; }
      if (s !== null) rows.push({ type: "slash", s, tie: c.kind === "command" ? 0 : 2, c });
    }
    rows.sort((a, b) => (b.s - a.s) || (a.tie - b.tie) ||
      String(a.c.title || a.c.name).localeCompare(String(b.c.title || b.c.name)));
    return rows;
  }

  function lineTarget(needle, total) {
    const m = /^(\d+)$/.exec(String(needle || "").trim());
    if (!m) return null;
    const n = Math.max(1, parseInt(m[1], 10));
    return total ? Math.min(n, total) : n;
  }

  // "Mod+Shift+P" -> "⌘⇧P" on a Mac, "Ctrl+Shift+P" elsewhere.
  function keyLabel(combo, mac) {
    if (!mac) return combo.replace(/^Mod\b/, "Ctrl");
    const sym = { Mod: "⌘", Alt: "⌥", Shift: "⇧" };
    return combo.split("+").map((p) => sym[p] || p).join("");
  }

  MK.palette = { parse, score, rankCommands, lineTarget, keyLabel };

  if (typeof document === "undefined" || !document.getElementById) return;   // node tests

  // --------------------------------------------------------------------------- state
  const pal = { open: false, changedOnly: false, rows: [], idx: 0, gen: 0, timer: 0,
                slash: null, outlines: {} };
  const mac = typeof navigator !== "undefined" && /Mac|iP(hone|ad)/.test(navigator.platform || "");
  const ui = () => MK.ui || {};

  function currentPath() {
    const u = ui();
    if (u.mode === "navigate" && MK.nav && MK.nav.path) return MK.nav.path;
    const f = (u.files || [])[u.sel];
    if (u.mode === "review" && f && f.status !== "deleted") return f.path;
    return (MK.nav && MK.nav.path) || null;
  }

  function openPalette(prefix) {
    const inp = $("qo-input");
    if (!inp) return;
    if (!pal.open) { pal.open = true; pal.changedOnly = false; $("qo").hidden = false; }
    inp.value = prefix || "";
    inp.focus();
    inp.setSelectionRange(inp.value.length, inp.value.length);
    run();
  }
  // ⌘P: files; pressed again while the file list is open, the CHANGED files only.
  MK.openQuick = function openQuick() {
    if (pal.open && parse($("qo-input").value).mode === "files") {
      pal.changedOnly = !pal.changedOnly; run(); return;
    }
    openPalette("");
  };
  MK.openPalette = openPalette;
  function close() { pal.open = false; $("qo").hidden = true; }

  // --------------------------------------------------------------------------- rows
  function hl(text, needle) {
    const frag = document.createDocumentFragment();
    const ql = String(needle || "").toLowerCase();
    let j = 0;
    for (const ch of String(text)) {
      if (j < ql.length && ch.toLowerCase() === ql[j]) { frag.append(el("b", "m", ch)); j++; }
      else frag.append(document.createTextNode(ch));
    }
    return frag;
  }
  function section(box, title, note) {
    const h = el("div", "qo-sec"); h.append(el("h2", "", title));
    if (note) h.append(el("span", "dim small", note));
    box.append(h);
    const ul = el("ul", "qo-list"); box.append(ul); return ul;
  }
  function addRow(ul, li, act) {
    const k = pal.rows.length;
    li.onclick = (ev) => act(ev.altKey);
    li.onmousemove = () => { if (pal.idx !== k) { pal.idx = k; paint(); } };
    pal.rows.push({ li, act });
    ul.append(li);
  }
  function paint() {
    pal.rows.forEach((r, k) => r.li.classList.toggle("active", k === pal.idx));
    const cur = pal.rows[pal.idx];
    if (cur) cur.li.scrollIntoView({ block: "nearest" });
  }
  function fileRow(path, needle) {
    const [dir, name] = MK.splitPath(path);
    const li = el("li", "qo-row"); const nm = el("span", "nm mono");
    nm.append(hl(name, needle), el("span", "dim small", "  " + dir)); li.append(nm);
    return li;
  }
  function symRow(s, needle, where) {
    const li = el("li", "qo-row");
    li.append(el("span", "kind", s.kind === "class" ? "cls" : s.kind === "method" ? "m" : "fn"));
    const nm = el("span", "nm mono"); nm.append(hl(s.name, needle)); li.append(nm);
    li.append(el("span", "dim small mono", where));
    if (s.basis && MK.basisBadge) li.append(MK.basisBadge(s.basis));
    return li;
  }

  const HINT = {
    files: "↵ open (diff if changed, else file) · ⌥↵ the file, not the diff · ⌘P again: changed only",
    symbol: "↵ jump to the symbol in this file", workspace: "↵ open the definition",
    line: "↵ go to the line", command: "↵ run · a /mokata: command is copied for your session",
    slash: "↵ copy the command for your Claude Code session",
  };
  function paintChrome(mode) {
    $("qo-label").textContent = LABEL[mode];
    const legend = $("qo-prefixes"); legend.replaceChildren();
    const parts = [["", "files", "files"], ["@", "symbol", "symbol"], ["#", "workspace", "workspace"],
                   [":", "line", "line"], [">", "command", "command"]];
    parts.forEach(([p, m, word], i) => {
      if (i) legend.append(" · ");
      const s = el(m === mode ? "b" : "span", m === mode ? "on" : "", p ? p + " " + word : word);
      legend.append(s);
    });
    const hint = $("qo-hint"); hint.replaceChildren();
    hint.append(el("span", "", HINT[mode]));
    $("qo-matcher").textContent = "";
  }
  function empty(box, text) { box.append(el("p", "empty", text)); }

  // --------------------------------------------------------------------------- modes
  async function run() {
    const gen = ++pal.gen;
    const q = $("qo-input").value;
    const { mode, needle } = parse(q);
    paintChrome(mode);
    const box = $("qo-results");
    const fresh = () => { if (gen !== pal.gen) return false; box.replaceChildren(); pal.rows = []; pal.idx = 0; return true; };
    if (mode === "files") return runFiles(gen, needle, fresh);
    if (mode === "workspace") return runWorkspace(gen, needle, fresh);
    if (mode === "symbol") return runSymbol(gen, needle, fresh);
    if (mode === "line") { fresh(); runLine(box, needle); paint(); return; }
    return runCommands(gen, needle, mode === "slash", fresh);
  }

  async function runFiles(gen, needle, fresh) {
    const u = ui();
    const changed = (u.files || []).filter((f) => f.status !== "deleted").map((f) => f.path);
    const res = await MK.api("/api/quickopen", { q: needle, changed }).catch(() => null);
    if (!res || !fresh()) return;
    const box = $("qo-results");
    if (pal.changedOnly) { res.files = []; res.symbols = []; }
    if ((res.changed || []).length) {
      const ul = section(box, u.focus ? "Changed in this session" : "Changed", "always ranked first");
      for (const r of res.changed) {
        const f = (u.files || []).find((x) => x.path === r.path) || { status: "modified", added: 0, removed: 0 };
        const li = fileRow(r.path, needle);
        const letter = (MK.ST_LETTER || {})[f.status] || "M";
        li.prepend(el("span", "seen-dot" + (MK.isSeen && MK.isSeen(f) ? " seen" : "")), el("span", "st st-" + letter, letter));
        const im = (u.impact || {})[r.path];
        if (im && im.status === "untested") li.append(el("span", "risk", "untested"));
        li.append(el("span", "n-add mono small", `+${f.added}`), el("span", "n-del mono small", `−${f.removed}`), el("span", "dim small", "↵ diff"));
        addRow(ul, li, (alt) => {
          close();
          if (alt && MK.openFile) { MK.openFile(r.path, 1, null); return; }
          const i = (u.files || []).findIndex((x) => x.path === r.path);
          MK.setMode("review"); if (i >= 0) MK.showFile(i);
        });
      }
    }
    if ((res.files || []).length) {
      const ul = section(box, "Files");
      for (const r of res.files) addRow(ul, fileRow(r.path, needle), () => { close(); MK.openFile(r.path, 1, null); });
    }
    if ((res.symbols || []).length) {
      const ul = section(box, "Symbols", "# for symbols only");
      for (const s of res.symbols) addRow(ul, symRow(s, needle, `${s.path}:${s.line}`), () => { close(); MK.openFile(s.path, s.line, s.name); });
    }
    if (res.symbols_note && !pal.changedOnly) section(box, "Symbols", res.symbols_note);
    if (needle.trim() && !pal.changedOnly) {
      const ul = section(box, "Text");
      const li = el("li", "qo-row"); li.append(el("span", "", `Search the repo for “${needle}”`));
      addRow(ul, li, () => { close(); if (MK.openSearch) MK.openSearch(needle); });
    }
    if (!pal.rows.length) empty(box, res.error ? `could not search: ${res.note || res.error}` : "No match.");
    paint();
    $("qo-matcher").textContent = res.matcher ? `matcher: ${res.matcher}${res.matcher === "python-floor" ? " (pure-Python floor — pip install rapidfuzz)" : ""}${pal.changedOnly ? " · changed only" : ""}` : "";
    if (res.symbols_state === "building" && !pal.changedOnly) whenSymbols(gen);
  }

  // P32 — the symbol index builds as an Activity job (n/N files there): the palette asks again when
  // that job is DONE, from the event stream, instead of polling every 1.5 s while it is open.
  function whenSymbols(gen) {
    if (!MK.jobs || pal.waitingSymbols === gen) return;
    pal.waitingSymbols = gen;
    const again = () => { if (gen === pal.gen && pal.open) run(); };
    const running = MK.jobs.all().some((j) => j.kind === "index.symbols" && ["queued", "running", "partial"].includes(j.state));
    // Not seen running yet (its first event is on its way): one more ask shortly, never a loop.
    if (running) MK.jobs.whenDone("index.symbols", again); else setTimeout(again, 1500);
  }

  // `#`: the WORKSPACE symbols. There is no public graph `definitions()` accessor in this build,
  // so this asks the ⌘P symbol index (the graph layer's, AST floor beneath) through /api/quickopen
  // with its symbols-only form "@x" — every row still carries its basis.
  async function runWorkspace(gen, needle, fresh) {
    if (!needle) { fresh(); empty($("qo-results"), "Type a symbol name — every definition in the workspace."); return; }
    const res = await MK.api("/api/quickopen", { q: "@" + needle, changed: [] }).catch(() => null);
    if (!res || !fresh()) return;
    const box = $("qo-results");
    if ((res.symbols || []).length) {
      const ul = section(box, "Workspace symbols");
      for (const s of res.symbols) addRow(ul, symRow(s, needle, `${s.path}:${s.line}`), () => { close(); MK.openFile(s.path, s.line, s.name); });
    }
    if (res.symbols_note) section(box, "Symbols", res.symbols_note);
    if (!pal.rows.length && !res.symbols_note) empty(box, res.error ? `could not search: ${res.note || res.error}` : "No symbol matches.");
    paint();
    if (res.symbols_state === "building") whenSymbols(gen);
  }

  async function runSymbol(gen, needle, fresh) {
    const path = currentPath();
    if (!path) { fresh(); empty($("qo-results"), "Open a file first — or type # to search every file's symbols."); return; }
    let ol = MK.nav && MK.nav.path === path && MK.nav.outline ? MK.nav.outline : pal.outlines[path];
    if (!ol) {
      ol = await MK.api("/api/outline?path=" + encodeURIComponent(path)).catch(() => null);
      if (ol && !ol.error) pal.outlines[path] = ol;
    }
    if (!fresh()) return;
    const box = $("qo-results");
    const syms = ((ol && ol.symbols) || [])
      .map((s) => ({ s, sc: score(needle, s.name) })).filter((r) => r.sc !== null);
    if (needle) syms.sort((a, b) => b.sc - a.sc || a.s.line - b.s.line);
    if (syms.length) {
      const ul = section(box, "In " + path);
      for (const { s } of syms) addRow(ul, symRow({ ...s, basis: ol.basis }, needle, ":" + s.line), () => { close(); MK.openFile(path, s.line, s.name); });
    }
    if (!pal.rows.length) empty(box, ol && ol.note ? ol.note : "No symbol matches in this file.");
    paint();
  }

  function runLine(box, needle) {
    const path = currentPath();
    if (!path) { empty(box, "Open a file first, then : and a line number."); return; }
    const text = MK.nav && MK.nav.path === path && MK.nav.file && typeof MK.nav.file.text === "string" ? MK.nav.file.text : null;
    const total = text === null ? 0 : text.split("\n").length - (text.endsWith("\n") ? 1 : 0);
    const n = lineTarget(needle, total);
    if (n === null) { empty(box, `Type a line number${total ? ` (1–${total})` : ""} in ${path}.`); return; }
    const ul = section(box, "Line");
    const li = el("li", "qo-row"); li.append(el("span", "nm mono", `Go to line ${n}`), el("span", "dim small mono", path));
    addRow(ul, li, () => { close(); MK.openFile(path, n, null); });
  }

  async function loadSlash() {
    if (pal.slash) return pal.slash;
    const res = await MK.api("/api/palette/commands").catch(() => null);
    if (res && Array.isArray(res.commands)) pal.slash = res;
    return pal.slash || { commands: [], how: "", joined: null };
  }

  function keysOf(c) { return (c.keys || []).map((k) => keyLabel(k, mac)); }

  async function runCommands(gen, needle, slashOnly, fresh) {
    const sl = await loadSlash();
    if (!fresh()) return;
    const box = $("qo-results");
    const ide = slashOnly || !window.mokataIDE ? [] : window.mokataIDE.commands();
    const rows = rankCommands(needle, ide, sl.commands);
    const ulIde = rows.some((r) => r.type === "ide") && !needle ? section(box, "IDE") : null;
    const ulMk = rows.some((r) => r.type === "slash") && !needle ? section(box, "mokata", "paste into your Claude Code session") : null;
    const ulAll = needle ? section(box, "Commands") : null;
    for (const r of rows) {
      const li = el("li", "qo-row");
      if (r.type === "ide") {
        const nm = el("span", "nm"); nm.append(hl(r.c.title, needle)); li.append(nm);
        li.append(el("span", "dim small", r.c.feature));
        for (const k of keysOf(r.c)) li.append(el("kbd", "", k));
        addRow(ulAll || ulIde, li, () => { close(); window.mokataIDE.run(r.c.id); });
      } else {
        const nm = el("span", "nm mono"); nm.append(hl(r.c.slash, needle)); li.append(nm);
        li.append(el("span", "dim small desc", r.c.description));
        if (r.c.kind === "skill") li.append(el("span", "tag", "skill"));
        if (r.c.gated) { const g = el("span", "tag gate", "gate"); g.title = "this command stops at a human gate"; li.append(g); }
        addRow(ulAll || ulMk, li, () => { close(); copySlash(r.c.slash, sl); });
      }
    }
    if (!pal.rows.length) empty(box, "No command matches.");
    paint();
  }

  // --------------------------------------------------------------------------- copy + toast
  function copyText(text) {
    if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(() => true, () => legacyCopy(text));
    }
    return Promise.resolve(legacyCopy(text));
  }
  function legacyCopy(text) {
    const ta = el("textarea"); ta.value = text; ta.setAttribute("readonly", "");
    ta.style.position = "fixed"; ta.style.opacity = "0"; document.body.append(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_e) { ok = false; }
    ta.remove();
    return ok;
  }
  function copySlash(slash, sl) {
    copyText(slash).then((ok) => {
      const who = sl.joined ? ` (joined: ${String(sl.joined).slice(0, 8)})` : "";
      toast(ok ? `Copied ${slash} — paste it into your Claude Code session${who}.`
               : `Run ${slash} in your Claude Code session${who} — the copy was blocked by the browser.`);
    });
  }
  let toastTimer = 0;
  function toast(text) {
    let t = $("pal-toast");
    if (!t) { t = el("div", "pal-toast"); t.id = "pal-toast"; t.setAttribute("role", "status"); document.body.append(t); }
    t.textContent = text; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 6000);
  }
  MK.toast = toast;

  // --------------------------------------------------------------------------- wiring
  MK.wirePalette = function wirePalette() {
    $("qo-open").onclick = () => MK.openQuick();
    $("qo-input").oninput = () => { clearTimeout(pal.timer); pal.timer = setTimeout(run, 70); };
    $("qo").onclick = (ev) => { if (ev.target === $("qo")) close(); };
    // The palette's own keys, on its input: handled (and preventDefault'ed) here before the
    // registry and app.js see them.
    $("qo-input").addEventListener("keydown", (ev) => {
      if (!pal.open) return;
      if (ev.key === "Escape") { ev.preventDefault(); close(); }
      else if (ev.key === "ArrowDown") { ev.preventDefault(); pal.idx = Math.min(pal.rows.length - 1, pal.idx + 1); paint(); }
      else if (ev.key === "ArrowUp") { ev.preventDefault(); pal.idx = Math.max(0, pal.idx - 1); paint(); }
      else if (ev.key === "Enter") { ev.preventDefault(); const r = pal.rows[pal.idx]; if (r) r.act(ev.altKey); }
    });
  };

  window.mokataIDE.register({
    id: "palette",
    commands: [
      { id: "palette.open", title: "Palette: files, @symbols, #workspace, :line, >commands",
        keys: "Mod+K", inFields: true, run: () => openPalette("") },
      { id: "palette.commands", title: "Palette: commands", keys: "Mod+Shift+P", inFields: true,
        run: () => openPalette(">") },
      { id: "palette.line", title: "Go to line…", keys: "Mod+G", inFields: true, run: () => openPalette(":") },
      { id: "palette.symbol", title: "Go to symbol in this file…", run: () => openPalette("@") },
      { id: "palette.workspace", title: "Go to symbol in the workspace…", run: () => openPalette("#") },
      { id: "palette.mokata", title: "mokata: run a /mokata: command…", run: () => openPalette("/") },
    ],
  });
})();
