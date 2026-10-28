// mokata ide — the feature registry (0.0.26 P0, ID-API-CONTRACT): commands, panels, keybindings.
// Loaded FIRST (before app.js). A feature is one file, static/features/<name>.js, that calls
//
//   window.mokataIDE.register({
//     id: "my-feature",
//     commands:    [{ id: "my.open", title: "My feature: open", run: () => ..., keys: "Mod+Shift+M" }],
//     keybindings: [{ keys: "Alt+3", command: "my.open", inFields: false }],
//     panels:      [{ id: "mine", title: "Mine", icon: "M", chord: "m", side: true,
//                     mount(side, view) {...}, onShow() {...} }],
//   });
//
// The server writes a <script> tag for every static/features/*.js (sorted) after the core views
// and before boot.js — no edit to index.html or app.js. register() is ATOMIC: everything is
// checked first, and a conflict (a command id, a key combo or a panel id another owner holds,
// including the core's reserved keys below) throws, naming both owners — nothing is half-added.
// The command list is what the command palette shows: commands() -> [{id, title, keys, feature}].
// SAFETY: like every view, a feature puts repo text in the DOM through textContent only.
"use strict";

(function (root) {
  const MK = (root.MK = root.MK || {});

  // ---------------------------------------------------------------------------- key combos
  // Canonical form: [Mod+][Alt+][Shift+]Key. Ctrl and Cmd are ONE modifier ("Mod"): the page
  // already treats ⌘P and Ctrl+P alike, so a feature binding Ctrl+K owns ⌘K too. A letter is
  // upper-case; a single non-letter, non-digit character ("]", ">") carries no Shift (the
  // character already says it).
  const MODS = { ctrl: "Mod", control: "Mod", cmd: "Mod", command: "Mod", meta: "Mod", mod: "Mod",
                 alt: "Alt", option: "Alt", opt: "Alt", shift: "Shift" };
  const NAMED = { esc: "Escape", escape: "Escape", enter: "Enter", return: "Enter", tab: "Tab",
                  space: "Space", " ": "Space", up: "ArrowUp", down: "ArrowDown", left: "ArrowLeft",
                  right: "ArrowRight", arrowup: "ArrowUp", arrowdown: "ArrowDown",
                  arrowleft: "ArrowLeft", arrowright: "ArrowRight", backspace: "Backspace",
                  delete: "Delete", home: "Home", end: "End", pageup: "PageUp", pagedown: "PageDown" };

  function keyName(k) {
    if (k.length === 1) return /[a-z]/i.test(k) ? k.toUpperCase() : k;
    const low = k.toLowerCase();
    if (NAMED[low]) return NAMED[low];
    if (/^f([1-9]|1[0-2])$/.test(low)) return low.toUpperCase();
    return null;
  }

  function canonical(mods, key) {
    const printable = key.length === 1 && !/[A-Z0-9]/.test(key);
    const out = [];
    if (mods.Mod) out.push("Mod");
    if (mods.Alt) out.push("Alt");
    if (mods.Shift && !printable) out.push("Shift");
    out.push(key);
    return out.join("+");
  }

  function normalize(combo) {
    if (typeof combo !== "string" || !combo.trim()) throw new Error("a key combo is a string");
    const parts = combo.trim().split("+");
    const raw = parts.pop() || "+";              // "Mod++" binds the plus key
    const mods = {};
    for (const p of parts) {
      const m = MODS[p.trim().toLowerCase()];
      if (!m) throw new Error(`unknown modifier "${p}" in "${combo}"`);
      mods[m] = true;
    }
    const key = keyName(raw.trim() || raw);
    if (!key) throw new Error(`unknown key "${raw}" in "${combo}"`);
    return canonical(mods, key);
  }

  // The combos one keydown can mean: by ev.code (layout- and Alt-proof: macOS turns Alt+1 into
  // "¡") and by ev.key (what a symbol binding like ">" means).
  function fromEvent(ev) {
    const mods = { Mod: !!(ev.ctrlKey || ev.metaKey), Alt: !!ev.altKey, Shift: !!ev.shiftKey };
    const out = [];
    const m = /^(?:Key([A-Z])|Digit([0-9]))$/.exec(ev.code || "");
    if (m) out.push(canonical(mods, m[1] || m[2]));
    const k = ev.key ? keyName(ev.key) : null;
    if (k) { const c = canonical(mods, k); if (!out.includes(c)) out.push(c); }
    return out;
  }

  // The keys the core views already handle with their own listeners (app.js onKey, the ⌘P
  // dialog, sessions.js, health.js). A feature may not bind them; to take one over, the stage
  // deletes the core handler AND its line here.
  const RESERVED = {
    "]": "app.js (next file)", "[": "app.js (previous file)", "J": "app.js (next change)",
    "K": "app.js (previous change)", "U": "app.js (next unseen)", "E": "app.js (open in Navigate)",
    "Alt+ArrowLeft": "app.js (back)", "Alt+ArrowRight": "app.js (forward)",
    "Escape": "app.js / health.js (close)", "Enter": "palette.js (pick)",
    "ArrowUp": "palette.js (move)", "ArrowDown": "palette.js (move)",
    ">": "sessions.js (next session)", "<": "sessions.js (previous session)",
    "!": "sessions.js (most urgent)",
    "G": "app.js (g + a letter: go to a screen)", "?": "app.js (every key)",
  };

  // 0.0.26 P34 (R30, B-F16) — chords a BROWSER keeps for itself: Ctrl/⌘+W closes the IDE's window,
  // Ctrl/⌘+Tab switches browser tabs, Ctrl/⌘+Shift+T reopens a browser tab, and so on. A page
  // never sees them reliably, so the registry REFUSES them — bind an Alt chord instead.
  const BROWSER_RESERVED = {
    "Mod+W": "closes the browser tab", "Mod+Shift+W": "closes the browser window",
    "Mod+T": "opens a browser tab", "Mod+Shift+T": "reopens a closed browser tab",
    "Mod+N": "opens a browser window", "Mod+Shift+N": "opens a private window",
    "Mod+Tab": "switches browser tabs", "Mod+Shift+Tab": "switches browser tabs",
    "Mod+Q": "quits the browser", "Mod+L": "focuses the address bar",
  };

  const commands = new Map();   // id -> {id, title, run, when, feature}
  const bindings = new Map();   // combo -> {combo, command, inFields, feature}
  const panels = new Map();     // id -> {id, title, mode, feature, onShow, el}
  const features = new Set();
  const conflicts = [];

  function owner(combo) {
    if (RESERVED[combo]) return "core " + RESERVED[combo];
    const b = bindings.get(combo);
    return b ? `${b.feature} (${b.command})` : null;
  }

  function fail(msg) {
    conflicts.push(msg);
    if (root.console) root.console.error("mokataIDE: " + msg);
    throw new Error(msg);
  }

  function register(spec) {
    if (!spec || typeof spec.id !== "string" || !spec.id) fail("register({id, ...}) needs an id");
    const feature = spec.id;
    if (features.has(feature)) fail(`feature "${feature}" registered twice`);
    const cmds = spec.commands || [], keys = [...(spec.keybindings || [])], pans = spec.panels || [];
    // 1. check everything (nothing is added if anything conflicts)
    const newCmd = new Map(), newKey = new Map(), newPan = new Map();
    for (const c of cmds) {
      if (!c || typeof c.id !== "string" || typeof c.title !== "string" || typeof c.run !== "function")
        fail(`${feature}: a command needs {id, title, run}`);
      if (commands.has(c.id) || newCmd.has(c.id))
        fail(`command "${c.id}" is registered twice: by ${(commands.get(c.id) || { feature }).feature} and by ${feature}`);
      newCmd.set(c.id, { id: c.id, title: c.title, run: c.run, when: c.when || null, feature });
      if (c.keys) keys.push({ keys: c.keys, command: c.id, inFields: !!c.inFields });
    }
    for (const k of keys) {
      let combo;
      try { combo = normalize(k.keys); } catch (e) { fail(`${feature}: ${e.message}`); }
      if (!commands.has(k.command) && !newCmd.has(k.command))
        fail(`${feature}: ${combo} binds unknown command "${k.command}"`);
      if (BROWSER_RESERVED[combo]) fail(`key ${combo} is the browser's (it ${BROWSER_RESERVED[combo]}) — ${feature} (${k.command}) must use an Alt chord`);
      const prior = owner(combo) || (newKey.has(combo) ? `${feature} (${newKey.get(combo).command})` : null);
      if (prior) fail(`key ${combo} is taken: by ${prior}, wanted by ${feature} (${k.command})`);
      newKey.set(combo, { combo, command: k.command, inFields: !!k.inFields, feature });
    }
    for (const p of pans) {
      if (!p || typeof p.id !== "string" || !/^[a-z][a-z0-9-]*$/.test(p.id) || typeof p.title !== "string")
        fail(`${feature}: a panel needs {id (lower-case), title}`);
      const core = (MK.SCREENS || []).some((s) => s.id === p.id);
      if (core || panels.has(p.id) || newPan.has(p.id))
        fail(`panel "${p.id}" is taken: by ${core ? "core" : (panels.get(p.id) || { feature }).feature}, wanted by ${feature}`);
      if (p.chord !== undefined && (typeof p.chord !== "string" || !/^[a-z]$/.test(p.chord)))
        fail(`${feature}: a screen's chord is one lower-case letter (g + it)`);
      const chordOwner = p.chord && ((MK.SCREENS || []).find((s) => s.chord === p.chord)
        || [...newPan.values()].find((q) => q.chord === p.chord));
      if (chordOwner) fail(`chord g ${p.chord} is taken: by ${chordOwner.id}, wanted by ${feature} (${p.id})`);
      newPan.set(p.id, p);
    }
    // 2. apply
    features.add(feature);
    for (const [id, c] of newCmd) commands.set(id, c);
    for (const [combo, b] of newKey) bindings.set(combo, b);
    for (const [id, p] of newPan) panels.set(id, mountPanel(p, feature));
    return { commands: [...newCmd.keys()], keys: [...newKey.keys()], panels: [...newPan.keys()] };
  }

  // ---------------------------------------------------------------------------- screens
  // 0.0.26 P34 (IA-ONE-NAV): a panel IS a screen — a rail button (`data-screen`) + a centre view
  // (#view-<id>) + with `side: true` a side panel (#panel-<id>) shown only with it. MK.setMode(id)
  // is the one way to show it (rail highlight == visible view, R26); `chord` is its `g` letter.
  // mount(side, view) builds DOM only (it runs at load — fetch nothing there); onShow() runs each
  // time the screen is shown, and is where a feature asks the server for data.
  function mountPanel(p, feature) {
    const rec = { id: p.id, title: p.title, mode: true, side: !!p.side, chord: p.chord || null, feature,
                  onShow: p.onShow || null };
    const doc = root.document;
    if (MK.SCREENS) MK.SCREENS.push({ id: p.id, title: p.title, chord: p.chord || null, feature });
    if (!doc) return rec;
    const rail = doc.querySelector("nav.rail"), left = doc.getElementById("left");
    const center = doc.querySelector("main.center");
    const btn = doc.createElement("button");
    btn.type = "button"; btn.className = "rail-btn"; btn.dataset.screen = p.id;
    btn.title = p.title + (p.chord ? ` (g ${p.chord})` : ""); btn.setAttribute("aria-label", p.title);
    btn.textContent = p.icon || p.title.slice(0, 1);
    if (rail) rail.append(btn);
    let sec = null;
    if (p.side) {
      sec = doc.createElement("section");
      sec.className = "lp"; sec.id = "panel-" + p.id; sec.hidden = true;
      sec.setAttribute("aria-label", p.title);
      if (left) left.append(sec);
    }
    const view = doc.createElement("section");
    view.className = "view-pane"; view.id = "view-" + p.id; view.hidden = true;
    if (center) center.append(view);
    if (typeof p.mount === "function") p.mount(sec, view);
    return rec;
  }

  function show(id) {
    if (panels.has(id) && MK.setMode) MK.setMode(id);      // onShow runs through MK.onModeFeatures
  }
  // boot.js calls every MK.onMode* on a screen change: a feature screen shown by restore, a chord
  // or the rail gets its onShow.
  MK.onModeFeatures = (mode) => { const p = panels.get(mode); if (p && p.onShow) p.onShow(); };

  // ---------------------------------------------------------------------------- dispatch
  function typing(ev) {
    const t = ev.target;
    if (!t || t === root.document.body) return false;
    const tag = (t.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || !!t.isContentEditable;
  }

  function run(id, ...args) {
    const c = commands.get(id);
    if (!c) throw new Error(`no command "${id}"`);
    if (c.when && !c.when()) return false;
    c.run(...args);
    return true;
  }

  function onKey(ev) {
    if (ev.defaultPrevented) return;              // a focused widget (the terminal) took it
    for (const combo of fromEvent(ev)) {
      const b = bindings.get(combo);
      if (!b) continue;
      if (!b.inFields && typing(ev)) return;
      const c = commands.get(b.command);
      if (c.when && !c.when()) return;
      ev.preventDefault();
      c.run();
      return;
    }
  }
  if (root.document && root.document.addEventListener) root.document.addEventListener("keydown", onKey);

  const api = {
    register, run,
    commands: () => [...commands.values()].map((c) => ({
      id: c.id, title: c.title, feature: c.feature,
      keys: [...bindings.values()].filter((b) => b.command === c.id).map((b) => b.combo) }))
      .sort((a, b) => a.title.localeCompare(b.title)),
    bindings: () => [...bindings.values()].map((b) => ({ ...b })),
    reserved: () => ({ ...RESERVED }),
    panels: () => [...panels.values()].map((p) => ({ id: p.id, title: p.title, side: p.side, chord: p.chord, feature: p.feature })),
    browserReserved: () => ({ ...BROWSER_RESERVED }),
    features: () => [...features],
    conflicts: () => conflicts.slice(),
    keys: { normalize, fromEvent, owner },
    show,
  };
  root.mokataIDE = api;
  MK.ide = api;
})(typeof window !== "undefined" ? window : globalThis);
