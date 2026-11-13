// mokata ide — Copy with Context and Find Usages (0.0.27 P4, ID-COPY-CONTEXT).
//
// No new pane. They are reached from:
//   * the command palette (Copy with Context Alt+Shift+X, Find Usages Shift+F12);
//   * a right-click menu on the Code view (the line, or the selected lines);
//   * the Review hunk's ⋯ menu (MK.hunkMenu; new side, live working-tree view only).
// 0.0.27 UX-SIMPLIFY #40: Copy Ref is no longer its own command — it is the "ref" shape of Copy
// with Context (path:line and symbol id, copied at once, no job): ONE copy, its shapes inside.
// Find Usages answers in the Code inspector's References tab. 0.0.28 G (ID-LSP-CALL-HIERARCHY): the box is
// three tabs — Usages · Callers · Callees — one tabpanel; Callers/Callees are a tree (the function
// at the root, one level per question, any row expands) answered by the project's language server
// through /api/lsp/calls, or by the call graph (callers only) while it cannot answer, labelled.
// No keybinding (G-Q4): the palette (Find Callers / Find Callees), the Code right-click menu (the
// same two items, after Find Usages), or the box's tabs. Copy with Context opens a small popover where the
// mouse was. The popover shows the job (progress and Cancel), then the size, token estimate and
// cut/lexical state of each shape. NOTHING is copied until the human picks a shape. A cancelled job
// copies nothing, and a click on Cancel stops any write that is still pending.
// SAFETY: every piece of repo or server text goes in through textContent.
"use strict";

(function () {
  if (typeof window === "undefined" || !window.mokataIDE || typeof document === "undefined") return;
  const MK = window.MK;
  const el = MK.el;
  const P = { view: null, box: null, head: null, cursors: [], page: null, pageSize: 50, pop: null,
              mode: "usages", target: null, tabs: null, live: new Set() };
  const MODES = [["usages", "Usages"], ["in", "Callers"], ["out", "Callees"]];

  function btn(label, cls, fn, title) {
    const b = el("button", cls || "btn ghost small", label);
    b.type = "button"; b.onclick = fn;
    if (title) b.title = title;
    return b;
  }
  function toast(t) { if (MK.toast) MK.toast(t); }
  function plural(n, one, many) { return `${n.toLocaleString()} ${n === 1 ? one : (many || one + "s")}`; }
  function kb(n) { return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${(n / 1024).toFixed(1)} KB` : `${n} B`; }

  // A copy never leaves keyboard focus on <body> (the legacy path selects a hidden textarea).
  async function copy(text) {
    const back = document.activeElement;
    const ok = MK.copyText ? await MK.copyText(text) : false;
    if (back && back !== document.body && document.contains(back) && back.focus) back.focus();
    return ok;
  }

  // ------------------------------------------------------------------ what the code view selects
  function rowOf(node) {
    if (!node) return null;
    const e = node.nodeType === 3 ? node.parentNode : node;
    const row = e && e.closest ? e.closest("div.cl") : null;
    return row && row.closest("#code") ? row : null;
  }
  function atRowStart(row, node, offset) {
    const lt = row.querySelector(".lt");
    if (!lt || !lt.contains(node)) return offset === 0;
    const r = document.createRange();
    r.setStart(lt, 0); r.setEnd(node, offset);
    return r.toString().length === 0;
  }
  // {path, lo, hi}: the selected lines. A drag that ends at column 0 of the next line does not
  // take that line. With no selection, the caret line.
  function selection() {
    const nav = MK.nav || {};
    if (!nav.path) return null;
    const s = window.getSelection ? window.getSelection() : null;
    let lo = nav.line || 1, hi = lo;
    if (s && s.anchorNode) {
      const a = rowOf(s.anchorNode), f = rowOf(s.focusNode);
      if (a && f) {
        const an = +a.dataset.n, fn = +f.dataset.n;
        let endRow = fn >= an ? f : a, endNode = fn >= an ? s.focusNode : s.anchorNode;
        let endOff = fn >= an ? s.focusOffset : s.anchorOffset;
        lo = Math.min(an, fn); hi = Math.max(an, fn);
        if (hi > lo && atRowStart(endRow, endNode, endOff)) hi -= 1;
      } else if (a) { lo = hi = +a.dataset.n; }
    }
    return { path: nav.path, lo, hi };
  }

  // ------------------------------------------------------------------ the "ref" shape
  // path:line and the enclosing symbol's id (/api/ref) — what Copy Ref copied. It needs no job, so
  // it is offered from the moment the popover opens, above the context job's progress.
  function refRow(p, sel) {
    const wrap = el("div", "p4-shape-row p4-ref-row");
    const b = btn("Copy ref", "btn small p4-shape", () => copyRefShape(p, sel, b, msg), "path:line and the symbol id — no context");
    b.dataset.shape = "ref";
    const msg = el("span", "small dim p4-ref-msg", "path:line and symbol id");
    wrap.append(b, msg);
    return wrap;
  }
  async function copyRefShape(p, sel, origin, msg) {
    if (p.closed) return;
    origin.disabled = true;
    const r = await MK.api(`/api/ref?path=${encodeURIComponent(sel.path)}&lo=${sel.lo}&hi=${sel.hi}`).catch(() => null);
    origin.disabled = false;
    if (p.closed) return;
    if (!r || r.error) { msg.textContent = `Not copied: ${(r && (r.note || r.error)) || "no answer"}`; origin.focus(); return; }
    const ok = await copy(r.ref);
    msg.textContent = ok ? `Copied ${r.ref}${r.note ? " (" + r.note + ")" : ""}` : `Could not copy (the browser blocked it): ${r.ref}`;
    origin.focus();
  }

  // ------------------------------------------------------------------ Copy with Context (popover)
  function closePop(restore) {
    const p = P.pop;
    if (!p) return;
    P.pop = null;
    p.closed = true;
    p.el.remove();
    if (p.job && p.live) MK.jobs.cancel(p.job);
    if (restore !== false && p.back && document.contains(p.back) && p.back.focus) p.back.focus();
  }
  function openPop(x, y, title) {
    closePop(false);
    const box = el("div", "tab-menu p4-pop");
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", title);
    box.append(el("div", "p4-pop-title", title));
    const body = el("div", "p4-pop-body");
    box.append(body);
    document.body.append(box);
    const w = Math.min(420, window.innerWidth - 16);
    box.style.width = `${w}px`;
    box.style.left = `${Math.max(8, Math.min(x, window.innerWidth - w - 8))}px`;
    box.style.top = `${Math.max(8, Math.min(y, window.innerHeight - 260))}px`;
    const p = { el: box, body, back: document.activeElement, closed: false, job: null, live: false, cancelled: false };
    box.addEventListener("keydown", (ev) => { if (ev.key === "Escape") { ev.stopPropagation(); closePop(); } });
    P.pop = p;
    return p;
  }

  async function copyWithContext(sel, at) {
    sel = sel || selection();
    if (!sel) { toast("Open a file in Code first."); return; }
    const pos = at || { x: window.innerWidth / 2 - 200, y: 120 };
    const p = openPop(pos.x, pos.y, `Copy with Context — ${sel.path}:${sel.lo}${sel.hi !== sel.lo ? "-" + sel.hi : ""}`);
    p.sel = sel;
    p.refRow = refRow(p, sel);            // ONE row for the popover's life: its message survives the job's answer
    p.body.append(p.refRow);
    const strip = MK.jobs.strip(p.body, "p4-strip");
    strip.say("starting…");
    const stop = btn("Cancel", "btn ghost small p4-cancel", () => {
      p.cancelled = true;                       // set at once: nothing after this may copy
      if (p.job) MK.jobs.cancel(p.job);
      closePop();
    });
    p.body.append(stop);
    stop.focus();
    const body = { path: sel.path, lo: sel.lo, hi: sel.hi };
    // MK.api follows the 202 itself; onJob is where this popover learns the job (Cancel reaches
    // it) and shows its progress.
    const onJob = (j) => {
      p.job = j.id; p.live = j.state === "queued" || j.state === "running" || j.state === "partial";
      if (!p.closed) strip.set(j);
    };
    const r = await MK.api("/api/copy-context", body, { onJob }).catch((e) => ({ error: "fetch", note: String(e) }));
    p.live = false;
    if (p.closed || p.cancelled) return;
    strip.done();
    stop.remove();
    if (!r || r.error) {
      p.body.append(el("p", "small error", r && r.cancelled ? "Cancelled — nothing was copied." : `Nothing copied: ${(r && (r.note || r.error)) || "no answer"}`));
      p.body.append(btn("Close", "btn ghost small", () => closePop())).focus && p.body.lastChild.focus();
      return;
    }
    renderChoice(p, r);
  }

  function shapeLine(r, shape) {
    const s = r.shapes[shape];
    const bits = [`${kb(s.bytes)} · ≈${s.tokens.toLocaleString()} tokens`];
    if (r.cut.length) bits.push(`CUT radius: ${r.cut.join(", ")}`);
    if (r.lexical.length) bits.push(`lexical: ${r.lexical.join(", ")}`);
    if (s.replaced) bits.push(`${plural(s.replaced, "line")} replaced by the secret scan`);
    if (s.sensitive) bits.push(`${plural(s.sensitive, "sensitive location")} hidden`);
    return bits.join(" · ");
  }

  function renderChoice(p, r) {
    const b = p.body;
    b.replaceChildren(p.refRow);
    const facts = el("div", "small p4-facts");
    facts.append(el("div", "", `Selection: ${kb(r.selection_bytes)} (lines ${r.lines[0]}–${r.lines[1]})`));
    facts.append(el("div", "", r.acs ? `${plural(r.acs, "acceptance criterion", "acceptance criteria")} tied to this file` : `ACs: none — ${r.acs_why}`));
    if (!r.radii.length) facts.append(el("div", "", "No definition encloses these lines — no blast radius."));
    for (const x of r.radii) {
      const t = x.truncated ? `${x.count.toLocaleString()} of ${x.total != null ? x.total.toLocaleString() : "unknown"} (CUT)` : x.count.toLocaleString();
      facts.append(el("div", "", `${x.name}: ${t} locations · ${x.basis} via ${x.backend || "the floor"}${x.asked_by === "name" && x.shared > 1 ? ` · ${x.shared} definitions share the name` : ""}`));
    }
    if (r.dropped && r.dropped.length) facts.append(el("div", "dim", `Enclosing ${r.dropped.join(", ")} not included (innermost definitions only).`));
    if (!r.complete) facts.append(el("div", "warn", "A blast radius was cut — both shapes say so; neither is the whole radius."));
    b.append(facts);
    const row = el("div", "p4-shapes");
    for (const shape of ["summary", "full"]) {
      const choice = btn(shape === "full" ? "Copy full" : "Copy summary", "btn small p4-shape", () => take(p, r, shape, choice));
      choice.dataset.shape = shape;
      const wrap = el("div", "p4-shape-row");
      wrap.append(choice, el("span", "small dim", shapeLine(r, shape)));
      row.append(wrap);
    }
    b.append(row);
    b.append(el("p", "small dim", "Summary: every test location, with a count of locations per file. Full: every location."));
    b.append(btn("Close", "btn ghost small", () => closePop()));
    row.querySelector("button").focus();
  }

  async function take(p, r, shape, origin) {
    if (p.closed || p.cancelled) return;
    origin.disabled = true;
    const msg = el("div", "small", `Fetching ${kb(r.shapes[shape].bytes)}…`);
    p.body.append(msg);
    let off = 0, text = "";
    for (;;) {
      const c = await MK.api(`/api/copy-context/text?id=${encodeURIComponent(r.id)}&shape=${shape}&offset=${off}`).catch(() => null);
      if (p.closed || p.cancelled) return;
      if (!c || c.error) { msg.textContent = `Nothing copied: ${(c && (c.note || c.error)) || "no answer"}`; origin.disabled = false; return; }
      text += c.text;
      if (c.next == null) break;
      off = c.next;
    }
    if (p.closed || p.cancelled) return;
    const ok = await copy(text);
    origin.disabled = false;
    if (ok) {
      msg.textContent = `Copied the ${shape} shape (${kb(r.shapes[shape].bytes)}).`;
      origin.focus();
      return;
    }
    msg.textContent = "The browser blocked the copy (it was not right after a click). The finished text is ready:";
    const again = btn("Copy", "btn small p4-copy-again", async () => {
      if (p.closed || p.cancelled) return;
      const ok2 = await copy(text);
      msg.textContent = ok2 ? `Copied the ${shape} shape.` : "Still blocked — allow clipboard access for this page.";
      again.focus();
    });
    p.body.append(again);
    again.focus();
  }

  // ------------------------------------------------------------------ Find Usages
  function mountBox(view) {
    if (P.box && view.contains(P.box)) return;
    P.view = view;
    P.box = el("section", "p4-usages");
    P.box.setAttribute("aria-label", "Find Usages");
    // ONE tablist, ONE tabpanel (WAI-ARIA tabs, manual activation): ←/→/Home/End move, Enter/Space
    // or a click selects; the selected tab is the one tab stop.
    P.tabs = el("div", "p4-tabs");
    P.tabs.setAttribute("role", "tablist");
    P.tabs.setAttribute("aria-label", "Find Usages: usages, callers, callees");
    for (const [mode, label] of MODES) {
      const t = el("button", "pill itab p4-tab", label);
      t.type = "button";
      t.id = "p4-tab-" + mode;
      t.dataset.mode = mode;
      t.setAttribute("role", "tab");
      t.setAttribute("aria-controls", "p4-usages-panel");
      t.onclick = () => pickTab(mode);
      t.onkeydown = (ev) => tabKey(ev, t);
      P.tabs.append(t);
    }
    P.box.append(P.tabs);
    P.body = el("div", "p4-usages-body");
    P.body.id = "p4-usages-panel";
    P.body.setAttribute("role", "tabpanel");
    P.body.append(el("p", "small dim", "Shift+F12 on a name in Code (or right-click → Find Usages): every usage, paged, with the backend that answered and what it covers. Callers / Callees: Find Callers / Find Callees in the palette or the right-click menu, or these tabs — the language server's answer, one level at a time."));
    P.box.append(P.body);
    view.append(P.box);
    paintTabs();
  }

  function paintTabs() {
    if (!P.tabs) return;
    for (const t of P.tabs.querySelectorAll("[role=tab]")) {
      const on = t.dataset.mode === P.mode;
      t.setAttribute("aria-selected", String(on));
      t.classList.toggle("active", on);
      t.tabIndex = on ? 0 : -1;
    }
    P.body.setAttribute("aria-labelledby", "p4-tab-" + P.mode);
  }

  function tabKey(ev, t) {
    const all = [...P.tabs.querySelectorAll("[role=tab]")];
    const i = all.indexOf(t);
    let j = -1;
    if (ev.key === "ArrowRight") j = (i + 1) % all.length;
    else if (ev.key === "ArrowLeft") j = (i - 1 + all.length) % all.length;
    else if (ev.key === "Home") j = 0;
    else if (ev.key === "End") j = all.length - 1;
    else if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pickTab(t.dataset.mode); return; }
    if (j < 0) return;
    ev.preventDefault();
    all.forEach((x, k) => { x.tabIndex = k === j ? 0 : -1; });
    all[j].focus();
  }

  // A tab asks the box's current question in its own way; with none yet, it says how to ask one.
  function pickTab(mode) {
    const back = document.activeElement;
    P.mode = mode;
    paintTabs();
    const t = P.target;
    if (!t) {
      supersede();
      P.body.replaceChildren(el("p", "small dim", "Put the caret in a function in Code, then pick a tab or use the palette (Find Callers / Find Callees)."));
    } else if (mode === "usages") findUsages(t);
    else findCalls(mode, t);
    if (back && back.getAttribute && back.getAttribute("role") === "tab" && document.contains(back)) {
      const sel = P.tabs.querySelector("[aria-selected=true]");
      if (sel) sel.focus();
    }
  }

  // A newer question CANCELS the older one's jobs (rule F11), not only drops its answer.
  function supersede() {
    const gen = (P.gen = (P.gen || 0) + 1);
    for (const id of P.live) MK.jobs.cancel(id);
    P.live.clear();
    return gen;
  }
  function track(gen) {
    let mine = null;
    return {
      onJob: (j) => {
        const active = j.state === "queued" || j.state === "running" || j.state === "partial";
        if (active && gen !== P.gen) {
          // superseded before its id was known (the 202 lost the race with the newer question):
          // cancel it the moment it names itself
          if (j.id && j.id !== mine) { mine = j.id; MK.jobs.cancel(j.id); }
        } else if (active) { mine = j.id; P.live.add(j.id); }
        else if (j.id) P.live.delete(j.id);
      },
      done: () => { if (mine) P.live.delete(mine); },
    };
  }

  function ensureBox() {
    if (MK.showCodeAnswer) MK.showCodeAnswer();
    const host = document.getElementById("insp-usages");
    if (host) mountBox(host);
    if (!P.box) { toast("The Code inspector is not available."); return false; }
    return true;
  }

  // Focus is never left on <body>: an answer that replaced what held focus hands it to the tree
  // (or the selected tab); focus the person moved elsewhere is left where it is.
  function keepFocus() {
    const a = document.activeElement;
    if (a && a !== document.body && document.contains(a)) return;
    const f = P.body.querySelector("[role=treeitem][tabindex='0']") || P.tabs.querySelector("[aria-selected=true]");
    if (f) f.focus();
  }

  async function findUsages(target, opts) {
    const o = opts || {};
    if (!ensureBox()) return;
    if (!target.ref) P.target = { path: target.path, line: target.line };
    P.mode = "usages";
    paintTabs();
    const body = target.ref ? { ref: target.ref } : { path: target.path, line: target.line };
    if (o.lexical) body.mode = "lexical";
    const gen = supersede();                    // a later question wins; an older answer is dropped
    P.body.replaceChildren();
    const strip = MK.jobs.strip(P.body, "p4-strip");
    strip.say(`finding usages of ${target.ref || target.path + ":" + target.line}…`);
    P.cursors = []; P.head = null;
    const tr = track(gen);
    const r = await MK.api("/api/usages", body, { onJob: (j) => { tr.onJob(j); if (gen === P.gen) strip.set(j); } }).catch((e) => ({ error: "fetch", note: String(e) }));
    tr.done();
    if (gen !== P.gen) return;
    strip.done();
    if (!r || r.error) {
      P.body.append(el("p", "region-msg error", r && r.cancelled ? "Cancelled — no list (a cancelled search keeps nothing)." : `No answer: ${(r && (r.note || r.error)) || "?"}`));
      return;
    }
    P.head = r;
    renderHead(r);
    await loadPage(null);
    keepFocus();
  }

  // ------------------------------------------------------------------ Callers / Callees (0.0.28 G)
  function callsUrl(dir, t) {
    return `/api/lsp/calls?path=${encodeURIComponent(t.path)}&line=${t.line}` + (t.col ? `&col=${t.col}` : "") + `&dir=${dir}`;
  }
  const WORD = { in: ["caller", "callers"], out: ["callee", "callees"] };

  async function findCalls(dir, target) {
    if (!ensureBox()) return;
    P.target = { path: target.path, line: target.line, col: target.col || null };
    P.mode = dir;
    paintTabs();
    const gen = supersede();
    P.body.replaceChildren();
    const strip = MK.jobs.strip(P.body, "p4-strip");
    strip.say(`finding the ${WORD[dir][1]} of ${target.path}:${target.line}…`);
    const tr = track(gen);
    const r = await MK.api(callsUrl(dir, P.target), undefined, { onJob: (j) => { tr.onJob(j); if (gen === P.gen) strip.set(j); } }).catch((e) => ({ error: "fetch", note: String(e) }));
    tr.done();
    if (gen !== P.gen) return;
    strip.done();
    P.body.replaceChildren();
    if (!r || r.error) {
      P.body.append(el("p", "region-msg error", r && r.cancelled ? "Cancelled — no list." : `No answer: ${(r && (r.note || r.error)) || "?"}`));
      keepFocus();
      return;
    }
    renderCalls(r, dir, gen);
    keepFocus();
  }

  function sourceChip(r) {
    if (r.source === "lsp") return el("span", "chip small ok", `language server (${r.lsp.server || r.backend || ""})`);
    if (r.source === "graph") return el("span", "chip small warn", "call graph" + (r.basis ? ` · ${r.basis}` : ""));
    return el("span", "chip small warn", "no answer");
  }

  // The facts an answer must say about itself (rule M-E/M-F/F9): who answered, how many, what was
  // cut, what was skipped, and why a side is empty.
  function callsFacts(r, dir) {
    const out = [];
    const shown = (r.groups || []).reduce((n, g) => n + g.rows.length, 0);
    if (r.source !== "lsp" && r.lsp) out.push(["dim", `language server: ${r.lsp.state}${r.lsp.note ? " — " + r.lsp.note : ""}`]);
    if (r.groups && r.groups.length) {
      out.push(["", r.truncated ? `showing ${shown.toLocaleString()} of ${r.total.toLocaleString()} ${WORD[dir][1]} — narrow by opening a ${WORD[dir][0]}`
                              : plural(r.total, WORD[dir][0], WORD[dir][1])]);
    }
    if (r.skipped) out.push(["warn", `${plural(r.skipped, "entry", "entries")} the server sent malformed ${r.skipped === 1 ? "was" : "were"} skipped`]);
    if (r.items_named > r.items_asked) out.push(["warn", `the server named ${r.items_named.toLocaleString()} functions here; the first ${r.items_asked} were asked`]);
    if (r.note) out.push(["dim", r.note]);
    return out;
  }

  function renderCalls(r, dir, gen) {
    const h = el("div", "p4-head p4-calls-head");
    const title = el("div", "row");
    title.append(el("b", "", `${dir === "in" ? "Callers" : "Callees"} of ${r.symbol || "?"}`), document.createTextNode(" "), sourceChip(r));
    h.append(title);
    for (const [cls, text] of callsFacts(r, dir)) h.append(el("div", "small " + cls, text));
    P.body.append(h);
    const tree = el("ul", "lsp-results p4-tree");
    tree.setAttribute("role", "tree");
    tree.setAttribute("aria-label", `${WORD[dir][1]} of ${r.symbol || "the function"}`);
    tree.onkeydown = (ev) => treeKey(ev, tree, dir, gen);
    const groups = r.groups || [];
    groups.forEach((g, i) => {
      const li = treeItem(g.item, 1, i + 1, groups.length, dir, r.source, gen, false);
      tree.append(li);
      addChildren(li, g.rows, g.truncated ? g.total : g.rows.length, 2, dir, r.source, gen);
    });
    if (!groups.length) tree.append(el("li", "dim small", r.callable === false ? (r.note || "no function here") : r.asked ? `no ${WORD[dir][1]} found by ${r.backend || "the server"}` : (r.note || `no ${WORD[dir][1]}`)));
    P.body.append(tree);
    const first = tree.querySelector("[role=treeitem]");
    if (first) first.tabIndex = 0;
  }

  function treeItem(x, level, pos, size, dir, source, gen, expandable) {
    const li = el("li", "ref-card p4-node" + (x.test ? " is-test" : ""));
    li.setAttribute("role", "treeitem");
    li.setAttribute("aria-level", String(level));
    li.setAttribute("aria-posinset", String(pos));
    li.setAttribute("aria-setsize", String(size));
    li.setAttribute("aria-label", `${x.name || "?"} — ${x.shown || x.path}:${x.line}${x.outside ? " (outside the repository)" : ""}${x.test ? " (test)" : ""}`);
    li.tabIndex = -1;
    li.dataset.name = x.name || "";
    li._row = x; li._dir = dir; li._gen = gen;
    if (x.outside) li.setAttribute("aria-disabled", "true");
    if (expandable && !x.outside) li.setAttribute("aria-expanded", "false");
    const row = el("div", "p4-call-row");
    const exp = el("span", "p4-exp", expandable && !x.outside ? "▸" : "");
    exp.setAttribute("aria-hidden", "true");
    exp.onclick = (ev) => { ev.stopPropagation(); li.focus(); toggle(li); };
    row.append(exp, el("b", "p4-name", x.name || "?"), el("span", "mono dim p4-where", ` ${x.shown || x.path}:${x.line}${x.col ? ":" + x.col : ""}`));
    if (x.test) row.append(el("span", "chip small", "test"));
    if (x.outside) row.append(el("span", "chip small warn", "outside the repository"));
    if (x.calls != null && level > 1) row.append(el("span", "chip small", `${x.calls} call${x.calls === 1 ? "" : "s"}`));
    if (level > 1 && source === "graph") row.append(el("span", "chip small warn", "call graph"));
    row.append(el("span", "small dim p4-status"));
    row.onclick = () => { li.focus(); openRow(li); };
    li.append(row);
    if (x.snippet) li.append(el("div", "ref-snip mono", x.snippet));
    return li;
  }

  function addChildren(li, rows, size, level, dir, source, gen) {
    const ul = el("ul", "p4-group");
    ul.setAttribute("role", "group");
    rows.forEach((x, i) => ul.append(treeItem(x, level, i + 1, size, dir, source, gen, true)));
    li.append(ul);
    li.setAttribute("aria-expanded", "true");
    const exp = li.querySelector(":scope > .p4-call-row > .p4-exp");
    if (exp) exp.textContent = rows.length ? "▾" : "";
    if (!rows.length && level > 2) {
      li.removeAttribute("aria-expanded");
      say(li, `no ${WORD[dir][1]} found`);
    }
  }

  function say(li, text) {
    const s = li.querySelector(":scope > .p4-call-row > .p4-status");
    if (s) s.textContent = text ? ` ${text}` : "";
  }

  function openRow(li) {
    const x = li._row;
    if (!x || x.outside) return;
    if (MK.openFile) MK.openFile(x.path, x.line, null, x.col ? { col: x.col } : undefined);
  }

  function collapse(li) {
    const g = li.querySelector(":scope > ul[role=group]");
    if (!g) return;
    const a = document.activeElement;
    const inside = a && a !== li && li.contains(a);
    g.remove();                                           // collapsed = gone; expanding asks again (fresh)
    li.setAttribute("aria-expanded", "false");
    const exp = li.querySelector(":scope > .p4-call-row > .p4-exp");
    if (exp) exp.textContent = "▸";
    if (inside) { li.tabIndex = 0; li.focus(); }          // focus inside a collapsed group moves up
  }

  // One more level for this row: a NEW question at the row's own position (stateless — a cycle
  // cannot loop). Its answer lands under the row; a tree that was replaced meanwhile drops it.
  async function expand(li) {
    if (li.getAttribute("aria-disabled") === "true" || !li.hasAttribute("aria-expanded")) return;
    if (li.querySelector(":scope > ul[role=group]") || li._busy) return;
    li._busy = true;
    li.setAttribute("aria-busy", "true");
    say(li, "loading…");
    const x = li._row, gen = li._gen, dir = li._dir;
    const tr = track(gen);
    const r = await MK.api(callsUrl(dir, { path: x.path, line: x.line, col: x.col }), undefined, { onJob: tr.onJob }).catch((e) => ({ error: "fetch", note: String(e) }));
    tr.done();
    li._busy = false;
    li.removeAttribute("aria-busy");
    if (gen !== P.gen || !document.contains(li)) return;
    if (!r || r.error) {
      say(li, r && r.cancelled ? "Cancelled — no list" : `no answer: ${(r && (r.note || r.error)) || "?"}`);
      return;
    }
    const rows = [];
    let size = 0;
    for (const grp of r.groups || []) { rows.push(...grp.rows); size += grp.truncated ? grp.total : grp.rows.length; }
    const facts = callsFacts(r, dir).filter(([cls]) => cls === "warn" || r.truncated).map(([, t]) => t);
    say(li, facts.join(" · "));
    const level = +li.getAttribute("aria-level") + 1;
    if (!rows.length) {
      li.removeAttribute("aria-expanded");
      const exp = li.querySelector(":scope > .p4-call-row > .p4-exp");
      if (exp) exp.textContent = "";
      say(li, r.source === "lsp" || r.source === "graph" ? `no ${WORD[dir][1]} found` : (r.note || "no answer"));
      return;
    }
    addChildren(li, rows, size, level, dir, r.source, gen);
  }

  function toggle(li) {
    if (li.getAttribute("aria-expanded") === "true") collapse(li); else expand(li);
  }

  function visibleItems(tree) {
    return [...tree.querySelectorAll("[role=treeitem]")];
  }

  function treeKey(ev, tree) {
    const li = ev.target.closest && ev.target.closest("[role=treeitem]");
    if (!li || !tree.contains(li)) return;
    const items = visibleItems(tree);
    const i = items.indexOf(li);
    let next = null;
    if (ev.key === "ArrowDown") next = items[Math.min(items.length - 1, i + 1)];
    else if (ev.key === "ArrowUp") next = items[Math.max(0, i - 1)];
    else if (ev.key === "Home") next = items[0];
    else if (ev.key === "End") next = items[items.length - 1];
    else if (ev.key === "ArrowRight") {
      ev.preventDefault();
      if (li.getAttribute("aria-disabled") === "true") return;
      if (li.getAttribute("aria-expanded") === "false") { expand(li); return; }
      if (li.getAttribute("aria-expanded") === "true") next = li.querySelector(":scope > ul[role=group] > [role=treeitem]");
    } else if (ev.key === "ArrowLeft") {
      ev.preventDefault();
      if (li.getAttribute("aria-expanded") === "true") { collapse(li); return; }
      const parent = li.parentElement && li.parentElement.closest("[role=treeitem]");
      if (parent) next = parent;
    } else if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      openRow(li);
      return;
    }
    if (!next) return;
    ev.preventDefault();
    for (const n of tree.querySelectorAll("[role=treeitem][tabindex='0']")) n.tabIndex = -1;
    next.tabIndex = 0;
    next.focus();
  }

  function callsHere(dir) {
    const s = selection();
    if (!s) { toast("Open a file in Code and put the caret in a function."); return; }
    findCalls(dir, { path: s.path, line: s.lo });
  }

  function renderHead(r) {
    const h = el("div", "p4-head");
    const sym = r.symbol || {};
    const title = el("div", "row");
    title.append(el("b", "", sym.qual || sym.name || "?"), el("span", "dim small mono", ` ${sym.path}:${sym.line}`),
                 el("span", "chip small " + (r.basis === "lexical" ? "warn" : "ok"), r.basis === "lsp" ? r.backend : r.basis === "lexical" ? "lexical" : "structural"));
    h.append(title);
    h.append(el("div", "small p4-label", r.label || ""));
    const total = r.truncated ? `${r.total.toLocaleString()} of ${r.graph_total != null ? r.graph_total.toLocaleString() : "unknown"} — CUT by the graph` : r.total.toLocaleString();
    h.append(el("div", "small", `${total} usages in ${plural(r.files, "file")} · ${r.tests.toLocaleString()} in tests · ${r.code.toLocaleString()} in code` + (r.outside ? ` · ${r.outside.toLocaleString()} outside the repository` : "")));
    if (r.lsp && r.basis !== "lsp") h.append(el("div", "small dim", `language server: ${r.lsp.state}${r.lsp.note ? " — " + r.lsp.note : ""}`));
    if (r.graph_note && r.basis !== "lsp") h.append(el("div", "small dim", `code graph: ${r.graph_note}`));
    if (r.ambiguity) h.append(el("div", "small warn", r.ambiguity));
    if (r.candidates && r.candidates.length) {
      const c = el("div", "row small p4-cands");
      c.append(el("span", "dim", "definitions: "));
      for (const d of r.candidates) c.append(btn(`${d.path}:${d.line}`, "chip small linkish", () => findUsages({ path: d.path, line: d.line })));
      h.append(c);
    }
    if (r.basis !== "lexical" && r.basis !== "lsp") h.append(btn("Lexical scan as well", "btn ghost small", () => findUsages({ path: sym.path, line: sym.line }, { lexical: true }), "word matches over every source file — finds values, annotations and strings the graph leaves out"));
    P.body.append(h);
    P.stale = el("div", "small warn p4-stale"); P.stale.hidden = true;
    P.body.append(P.stale);
    P.list = el("ul", "lsp-results p4-list");
    P.list.setAttribute("role", "listbox");
    P.list.setAttribute("aria-label", "usages");
    P.body.append(P.list);
    P.pager = el("div", "row p4-pager");
    P.body.append(P.pager);
  }

  function pageSize() {
    const h = (P.view && P.view.clientHeight) || 600;
    return Math.max(20, Math.min(500, Math.ceil(h / 30) * 2));
  }

  async function loadPage(after, focusFirst) {
    const r = P.head;
    if (!r) return;
    const q = `/api/usages/page?id=${encodeURIComponent(r.id)}&limit=${pageSize()}` + (after ? `&after=${encodeURIComponent(JSON.stringify(after))}` : "");
    const pg = await MK.api(q).catch(() => null);
    if (P.head !== r) return;                   // a newer Find Usages owns the list now
    if (!pg || pg.error) {
      P.list.replaceChildren(el("li", "region-msg error", pg && pg.error === "Expired" ? "Expired — run Find Usages again." : `No page: ${(pg && (pg.note || pg.error)) || "?"}`));
      return;
    }
    P.page = { after, pg };
    const st = pg.stale || {};
    P.stale.hidden = st.symbol === "same";
    P.stale.textContent = st.note || "";
    const changed = new Set(st.changed_rows || []);
    P.list.replaceChildren();
    pg.rows.forEach((x, i) => {
      const [path, line, col, symbol, snippet, test, outside] = x;
      const li = el("li", "ref-card" + (test ? " is-test" : ""));
      li.setAttribute("role", "option");
      li.setAttribute("aria-setsize", String(pg.total));
      li.setAttribute("aria-posinset", String((pg.first || 1) + i));
      const b = el("button", "ref-head mono p4-row");
      b.type = "button";
      b.tabIndex = i === 0 ? 0 : -1;
      b.append(el("span", "grow", `${path}:${line}:${col}`));
      if (symbol) b.append(el("span", "dim", ` in ${symbol}`));
      if (test) b.append(el("span", "chip small", "test"));
      if (outside) b.append(el("span", "chip small warn", "outside the repo"));
      if (changed.has(i)) b.append(el("span", "chip small warn", "changed since"));
      b.disabled = !!outside;
      b.onclick = () => MK.openFile && MK.openFile(path, line, null, { col });
      b.onkeydown = (ev) => rowKey(ev, b);
      li.append(b);
      if (snippet) li.append(el("div", "ref-snip mono", snippet));
      P.list.append(li);
    });
    if (!pg.rows.length) P.list.append(el("li", "dim small", r.total ? "no rows here" : "no usages found by this backend — read its label above for what it covers"));
    P.pager.replaceChildren();
    const a = pg.first || 0, z = a ? a + pg.rows.length - 1 : 0;
    P.pager.append(el("span", "small dim", pg.total ? `rows ${a.toLocaleString()}–${z.toLocaleString()} of ${pg.total.toLocaleString()}` : ""));
    const prev = btn("Previous", "btn ghost small p4-prev", () => { const back = P.cursors.pop(); loadPage(back === undefined ? null : back, true); });
    prev.disabled = !P.cursors.length;
    const next = btn("Next", "btn ghost small p4-next", () => { P.cursors.push(after); loadPage(pg.next, true); });
    next.disabled = !pg.next;
    P.pager.append(prev, next);
    if (focusFirst) {
      const f = P.list.querySelector("button.p4-row:not(:disabled)") || (pg.next ? next : prev);
      if (f) f.focus();
    }
  }

  function rowKey(ev, b) {
    const rows = [...P.list.querySelectorAll("button.p4-row")];
    const i = rows.indexOf(b);
    let j = -1;
    if (ev.key === "ArrowDown") j = Math.min(rows.length - 1, i + 1);
    else if (ev.key === "ArrowUp") j = Math.max(0, i - 1);
    else if (ev.key === "Home") j = 0;
    else if (ev.key === "End") j = rows.length - 1;
    if (j < 0) return;
    ev.preventDefault();
    rows.forEach((r, k) => { r.tabIndex = k === j ? 0 : -1; });
    rows[j].focus();
  }

  function usagesHere() {
    const s = selection();
    if (!s) { toast("Open a file in Code and put the caret in a definition or on a name."); return; }
    findUsages({ path: s.path, line: s.lo });
  }

  // ------------------------------------------------------------------ the Code view's menu
  let menu = null;
  function closeMenu(restore) {
    if (!menu) return;
    const m = menu; menu = null;
    m.el.remove();
    if (restore !== false && m.back && document.contains(m.back) && m.back.focus) m.back.focus();
  }
  function openMenu(ev) {
    const sel = selection();
    if (!sel) return;
    ev.preventDefault();
    closeMenu(false);
    const box = el("div", "tab-menu p4-menu");
    box.setAttribute("role", "menu");
    const back = document.activeElement !== document.body ? document.activeElement : document.getElementById("code-scroll");
    menu = { el: box, back };
    const at = { x: ev.clientX || 40, y: ev.clientY || 40 };
    const items = [
      ["Copy with Context…", () => copyWithContext(sel, at)],
      ["Find Usages", () => findUsages({ path: sel.path, line: sel.lo })],
      // 0.0.28 G (coordinator ruling, after lane D): the call hierarchy, asked about the
      // function enclosing the line — the same question as the palette's commands
      ["Find Callers", () => findCalls("in", { path: sel.path, line: sel.lo })],
      ["Find Callees", () => findCalls("out", { path: sel.path, line: sel.lo })],
    ];
    for (const [label, fn] of items) {
      const b = el("button", "tab-menu-item", label);
      b.type = "button";
      b.setAttribute("role", "menuitem");
      b.onclick = () => { closeMenu(label !== "Copy with Context…"); fn(); };
      box.append(b);
    }
    box.onkeydown = (k) => {
      const all = [...box.querySelectorAll("button")];
      const i = all.indexOf(document.activeElement);
      if (k.key === "Escape") { k.preventDefault(); closeMenu(); }
      else if (k.key === "ArrowDown") { k.preventDefault(); all[(i + 1) % all.length].focus(); }
      else if (k.key === "ArrowUp") { k.preventDefault(); all[(i - 1 + all.length) % all.length].focus(); }
    };
    document.body.append(box);
    // kept on screen by its MEASURED size (four items since 0.0.28 G; a fixed 120 px clipped them)
    box.style.left = `${Math.max(4, Math.min(at.x, window.innerWidth - Math.max(220, box.offsetWidth) - 4))}px`;
    box.style.top = `${Math.max(4, Math.min(at.y, window.innerHeight - box.offsetHeight - 4))}px`;
    box.querySelector("button").focus();
    setTimeout(() => document.addEventListener("click", (e) => { if (menu && !menu.el.contains(e.target)) closeMenu(false); }, { once: true }), 0);
  }

  // ------------------------------------------------------------------ the review hunk header
  // A hunk's ref is its NEW side. Only the live view's new side is the working tree, and a pure
  // deletion has no line there, so both cases are refused with the reason.
  function hunkRange(h) {
    const m = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/.exec(h.header || "");
    if (!m) return null;
    const start = +m[1], n = m[2] === undefined ? 1 : +m[2];
    return n === 0 ? { deletion: true } : { lo: start, hi: start + n - 1 };
  }
  // The Review hunk's ⋯ menu item (MK.hunkMenu, features/diffview.js).
  function contextItem(f, h) {
    const live = !MK.viewing || MK.viewing().kind === "live";
    const rg = hunkRange(h);
    const why = !live ? "the new side here is not the working tree — open Uncommitted changes"
      : !rg ? "this hunk has no line range" : rg.deletion ? "a pure deletion has no line in the working tree" : "";
    const sel = rg && !rg.deletion ? { path: f.path, lo: rg.lo, hi: rg.hi } : null;
    return { id: "context", label: "Copy with Context…", title: why || "this hunk's ref, or the hunk with its ACs and blast radius",
             disabled: !!why, run: (at) => copyWithContext(sel, at) };
  }

  // V4 — the page is leaving (reload, close, navigate away): its results and their spill files
  // are released now, not at IDE stop. keepalive lets the request outlive the page; the server
  // also releases a page whose event stream stays gone (the backstop).
  function release() {
    try {
      fetch("/api/usages/discard", {
        method: "POST", keepalive: true,
        headers: { "X-Mokata-Client": MK.clientId, "Content-Type": "application/json" },
        body: JSON.stringify({ all: true }),
      }).catch(() => null);
    } catch (_e) { /* the page is going either way */ }
  }

  function mount() {
    if (typeof window.addEventListener === "function") window.addEventListener("pagehide", release);
    const code = document.getElementById("code");
    if (code) code.addEventListener("contextmenu", (ev) => { if (rowOf(ev.target)) openMenu(ev); });
    if (MK.hunkMenu) MK.hunkMenu.add(contextItem, 20);
    const host = document.getElementById("insp-usages");
    if (host) mountBox(host);
  }

  MK.usages = { copyWithContext, findUsages, findCalls, selection, hunkRange };
  window.mokataIDE.register({
    id: "usages",
    commands: [
      { id: "usages.copyContext", title: "Copy with Context (selection, ACs, blast radius)", keys: "Alt+Shift+X", run: () => copyWithContext() },
      { id: "usages.find", title: "Find Usages (paged, every backend labelled)", keys: "Shift+F12", run: usagesHere },
      // G-Q4: palette only — no keybinding.
      { id: "usages.callers", title: "Find Callers (who calls the function at the caret)", run: () => callsHere("in") },
      { id: "usages.callees", title: "Find Callees (what the function at the caret calls)", run: () => callsHere("out") },
    ],
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();
})();
