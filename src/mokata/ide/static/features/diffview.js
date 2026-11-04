// mokata ide — how a diff is SHOWN (0.0.27 P5, ID-DIFF-SPLIT + ID-DIFF-INTRALINE). app.js's
// renderFile draws each hunk through MK.dv.table, so every diff surface (Review, the session index)
// gets the same view:
//   * Unified ↔ Split, remembered per browser. Split keeps each side's own line numbers; a click on
//     a line number copies `path:line` for THAT side (the old path on the left of a rename), and a
//     selection stays on the side it started in (the other side is not selectable while you drag);
//   * intra-line marks: the changed tokens of each changed line pair, asked from the server ONLY
//     for the rows in view (/api/diff/intraline). A batch the server measures as costly comes back
//     as a job — its progress and Cancel show above the hunk; a run it could not align is marked
//     as one span and says so on hover, never as exact;
//   * ignore whitespace (the diff routes' `ws=ignore`): the page says how many whitespace-only
//     line changes were hidden and how many indentation changes were KEPT (indentation is code);
//   * expand context: the unchanged lines between hunks, in pages from the server
//     (/api/diff/context), read from where the file's new side lives (`f.ctx`).
// Rows are built in blocks of BLOCK; past EAGER_ROWS a block is built when it nears the viewport
// (a 100,000-line hunk never builds 100,000 rows at once) and the file says so.
// SAFETY: line text goes into the page through textContent only — a <script> in a diff is text.
"use strict";

(function () {
  const MK = window.MK;
  if (typeof document === "undefined" || !MK) return;
  const el = MK.el;
  const BLOCK = 200;            // rows per block (the unit of lazy building and of intra-line asks)
  const EAGER_ROWS = 3000;      // a file with more rows builds the rest as it scrolls into view
  const POST_CHARS = 1 << 20;   // one intra-line request carries about this much line text
  const STEP = 20;              // context lines per "show more"
  const PAGE_MAX = 500;         // the server's page bound (diffview.PAGE_MAX)
  const CONTEXT = 3;            // context lines every diff source asks for (git -U3, difflib n=3)

  function load(key, dflt) { try { return window.localStorage.getItem("mokata-ide:diff-" + key) || dflt; } catch (_e) { return dflt; } }
  function save(key, v) { try { window.localStorage.setItem("mokata-ide:diff-" + key, v); } catch (_e) { /* no storage */ } }

  const dv = (MK.dv = { mode: load("mode", "unified") === "split" ? "split" : "unified",
                        ws: load("ws", "show") === "ignore" ? "ignore" : "show" });
  const fpMemo = new WeakMap();
  // The file's diff fingerprint, once per file object (MK.fileFp hashes every hunk — never per cell).
  function fpOf(f) {
    if (!fpMemo.has(f)) fpMemo.set(f, MK.fileFp ? MK.fileFp(f) : f.path);
    return fpMemo.get(f);
  }
  const ixCache = new Map();    // file fingerprint -> Map("k:i" -> answer for the pair whose OLD line is i)
  const inflight = new Set();

  // =========================================================================== pure row logic
  // One hunk's rows. A change block (a run of -/+ lines) pairs its i-th removed line with its i-th
  // added line: the pairs get intra-line marks, and in Split they sit side by side.
  function rowsOf(h, mode) {
    const lines = h.lines || [];
    const rows = [], pairs = [];
    let k = 0;
    while (k < lines.length) {
      if (lines[k][0] === " ") { rows.push(mode === "split" ? { l: k, r: k } : { u: k }); k++; continue; }
      let j = k;
      while (j < lines.length && lines[j][0] !== " ") j++;
      const del = [], add = [];
      for (let x = k; x < j; x++) (lines[x][0] === "-" ? del : add).push(x);
      for (let p = 0; p < Math.min(del.length, add.length); p++) pairs.push([del[p], add[p]]);
      if (mode === "split") {
        for (let p = 0; p < Math.max(del.length, add.length); p++) rows.push({ l: p < del.length ? del[p] : null, r: p < add.length ? add[p] : null, chg: true });
      } else for (let x = k; x < j; x++) rows.push({ u: x });
      k = j;
    }
    return { rows, pairs };
  }
  dv.rowsOf = rowsOf;

  // The new-side line span of a hunk header, and how old numbers relate to new ones around it.
  function parseHeader(header) {
    const m = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(header || "");
    if (!m) return null;
    const a = +m[1], b = m[2] === undefined ? 1 : +m[2], c = +m[3], d = m[4] === undefined ? 1 : +m[4];
    return { oldStart: b ? a : a + 1, oldEnd: b ? a + b - 1 : a, newStart: d ? c : c + 1, newEnd: d ? c + d - 1 : c };
  }
  // The unchanged gaps of a file: before each hunk, and after the last (to the end of the file).
  function gapsOf(f) {
    const out = [];
    let prevNew = 0, prevDelta = 0;
    (f.hunks || []).forEach((h, k) => {
      const r = parseHeader(h.header);
      if (!r) return;
      const delta = r.oldStart - r.newStart;
      if (r.newStart - 1 > prevNew) out.push({ before: k, from: prevNew + 1, to: r.newStart - 1, delta });
      prevNew = r.newEnd; prevDelta = r.oldEnd - r.newEnd;
    });
    // after the last hunk: only when it ends in a full run of context (-U3) — a hunk that ends in
    // fewer context lines (or on a line with no newline) already reaches the end of the file
    const last = (f.hunks || [])[(f.hunks || []).length - 1];
    if (last) {
      let tail = 0;
      for (let i = last.lines.length - 1; i >= 0 && last.lines[i][0] === " "; i--) tail++;
      const atEof = last.noeol && last.noeol[1] !== null && last.noeol[1] !== undefined;
      if (tail >= CONTEXT && !atEof) out.push({ before: -1, from: prevNew + 1, to: null, delta: prevDelta });
    }
    return out;
  }
  dv.gapsOf = gapsOf;
  dv.parseHeader = parseHeader;

  // Cut `text` into segments at syntax-span and intra-line boundaries: [{t, cls, ix}].
  function segments(text, syn, ix) {
    const cuts = new Set([0, text.length]);
    const spans = [];
    if (syn && syn.length) {
      let at = 0;
      for (const [cls, t] of syn) { spans.push([at, at + t.length, cls]); at += t.length; cuts.add(at); }
      if (at !== text.length) { spans.length = 0; cuts.clear(); cuts.add(0); cuts.add(text.length); }
    }
    for (const [s, e] of ix || []) { cuts.add(Math.max(0, Math.min(text.length, s))); cuts.add(Math.max(0, Math.min(text.length, e))); }
    const pts = [...cuts].sort((a, b) => a - b);
    const out = [];
    for (let q = 0; q < pts.length - 1; q++) {
      const s = pts[q], e = pts[q + 1];
      if (e <= s) continue;
      const sp = spans.find((x) => x[0] <= s && e <= x[1]);
      const inIx = (ix || []).some(([a, b]) => a <= s && e <= b);
      out.push({ t: text.slice(s, e), cls: sp ? sp[2] : "", ix: inIx });
    }
    return out;
  }
  dv.segments = segments;

  // =========================================================================== painting a cell
  function cellText(f, k, i) { const ln = f.hunks[k].lines[i]; return ln ? ln[3] : ""; }

  // One line's text: syntax spans (highlight.js sets td._syn), intra-line marks (td._ix, td._basis),
  // a visible ␍ only where a changed range holds the carriage return (a CRLF<->LF change), and the
  // "no newline at end of file" marker. Unified cells keep their +/-/space prefix as text.
  function paintCell(td) {
    const f = td._f, k = td._k, i = td._i;
    if (!f || !f.hunks[k]) return;
    const ln = f.hunks[k].lines[i];
    if (!ln) return;
    const text = ln[3];
    const nodes = [];
    if (td.dataset.side) { if (ln[0] !== " ") nodes.push(el("span", "sr-only dv-sr", ln[0] === "-" ? "removed: " : "added: ")); }
    else nodes.push(document.createTextNode(ln[0] === " " ? " " : ln[0]));
    for (const s of segments(text, td._syn, td._ix)) {
      let t = s.t;
      let cr = false;
      if (t.endsWith("\r") && s.ix) { t = t.slice(0, -1); cr = true; }
      if (t) {
        if (!s.cls && !s.ix) nodes.push(document.createTextNode(t));
        else {
          const sp = el("span", (s.cls ? "tk-" + s.cls : "") + (s.ix ? " ix" + (td._basis === "span" ? " ix-span" : "") : ""), t);
          nodes.push(sp);
        }
      }
      if (cr) {
        const c = el("span", "ix dv-cr", "CR");
        c.title = "carriage return — this line ends in CRLF on this side only";
        nodes.push(c);
      }
    }
    const ne = f.hunks[k].noeol;
    if (ne && ((ln[0] !== "+" && ln[1] !== null && ln[1] === ne[0]) || (ln[0] !== "-" && ln[2] !== null && ln[2] === ne[1]))) {
      nodes.push(el("span", "dv-noeol", "no newline at end of file"));
    }
    td.replaceChildren(...nodes);
    if (td._basis === "span") td.title = "marked as one span: this changed run had no token in common to align on"; else td.removeAttribute("title");
  }
  dv.paintCell = paintCell;

  // =========================================================================== the table
  function textCell(f, k, i, side, cls) {
    const td = el("td", "tx" + (cls ? " " + cls : ""));
    td.dataset.i = String(i);
    if (side) td.dataset.side = side;
    td._f = f; td._k = k; td._i = i;
    const fp = fpOf(f);
    const c = ixCache.get(fp);
    const got = c && c.get(k + ":" + i);
    if (got) { td._ix = got.ranges; td._basis = got.basis; }
    const syn = MK.hl && MK.hl.diffRow ? MK.hl.diffRow(f, k, i) : null;
    if (syn) td._syn = syn;
    paintCell(td);
    return td;
  }
  function noCell(f, side, n) {
    const td = el("td", "no", n ?? "");
    if (n !== null && n !== undefined) {
      td.dataset.side = side; td.dataset.n = String(n);
      td.title = `copy ${citePath(f, side)}:${n}`;
    }
    return td;
  }
  function citePath(f, side) { return side === "old" ? (f.old_path || f.path) : f.path; }

  function buildRows(f, k, rows, from, to, mode) {
    const h = f.hunks[k];
    const out = [];
    for (let q = from; q < to; q++) {
      const r = rows[q];
      if (mode === "split") {
        const L = r.l === null ? null : h.lines[r.l], R = r.r === null ? null : h.lines[r.r];
        const tr = el("tr", r.chg ? "chg" : "ctx");
        tr.append(noCell(f, "old", L ? L[1] : null),
                  L ? textCell(f, k, r.l, "old", r.chg ? "del" : "") : el("td", "tx empty"),
                  noCell(f, "new", R ? R[2] : null),
                  R ? textCell(f, k, r.r, "new", r.chg ? "add" : "") : el("td", "tx empty"));
        out.push(tr);
      } else {
        const ln = h.lines[r.u];
        const tr = el("tr", ln[0] === "+" ? "add" : ln[0] === "-" ? "del" : "");
        tr.append(noCell(f, "old", ln[1]), noCell(f, "new", ln[2]), textCell(f, k, r.u, "", ""));
        out.push(tr);
      }
    }
    return out;
  }

  let seen = null;
  function observe(node) {
    if (typeof IntersectionObserver === "undefined") { onSeen(node); return; }
    if (!seen) seen = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) onSeen(e.target);
    }, { rootMargin: "600px 0px" });
    seen.observe(node);
  }
  function onSeen(tb) {
    if (tb._fill) { const f = tb._fill; tb._fill = null; f(); }
    if (seen && tb._done) seen.unobserve(tb);
    askIntra(tb);
  }

  // One hunk's table: blocks of BLOCK rows; past the file's eager budget a block is a sized
  // placeholder built when it nears the viewport.
  function table(f, h, k, budget) {
    const mode = dv.mode;
    const { rows, pairs } = rowsOf(h, mode);
    const t = el("table", "d" + (mode === "split" ? " split" : ""));
    t.dataset.k = String(k);
    t._f = f;
    const head = el("thead", "sr-only");
    const hr = el("tr");
    const cols = mode === "split" ? ["line before", "before", "line after", "after"] : ["line before", "line after", "change"];
    for (const c of cols) { const th = el("th", "", c); th.scope = "col"; hr.append(th); }
    head.append(hr);
    // fixed layout takes its widths from <col>s (the header row is for screen readers only)
    const cg = el("colgroup");
    for (const c of mode === "split" ? ["no", "tx", "no", "tx"] : ["no", "no", "tx"]) cg.append(el("col", "dv-col-" + c));
    t.append(cg, head);
    t._pairs = pairs;
    for (let from = 0; from < rows.length; from += BLOCK) {
      const to = Math.min(rows.length, from + BLOCK);
      const tb = el("tbody");
      tb._f = f; tb._k = k; tb._from = from; tb._to = to; tb._rows = rows;
      if (budget.left > 0) {
        tb.append(...buildRows(f, k, rows, from, to, mode));
        budget.left -= to - from;
        tb._done = true;
      } else {
        budget.lazy += to - from;
        const ph = el("tr", "dv-ph");
        const td = el("td", "dim small", `${to - from} rows — built as you scroll`);
        td.colSpan = mode === "split" ? 4 : 3;
        td.style.height = `${(to - from) * 20}px`;
        ph.append(td);
        tb.append(ph);
        tb._fill = () => { tb.replaceChildren(...buildRows(f, k, rows, from, to, mode)); tb._done = true; };
      }
      t.append(tb);
      observe(tb);
    }
    return t;
  }
  dv.table = (f, h, k) => {
    const b = dv._budget && dv._budget.f === f ? dv._budget : (dv._budget = { f, left: EAGER_ROWS, lazy: 0 });
    return table(f, h, k, b);
  };

  // =========================================================================== intra-line asks
  function askIntra(tb) {
    if (!tb._done || tb._asked) return;
    const f = tb._f, k = tb._k;
    const t = tb.closest("table");
    if (!t || !t._pairs) return;
    const fp = fpOf(f);
    let cache = ixCache.get(fp);
    if (!cache) { cache = new Map(); ixCache.set(fp, cache); while (ixCache.size > 30) ixCache.delete(ixCache.keys().next().value); }
    // the pairs whose cells this block shows
    const idx = new Set();
    for (let q = tb._from; q < tb._to; q++) { const r = tb._rows[q]; for (const x of [r.u, r.l, r.r]) if (x !== undefined && x !== null) idx.add(x); }
    const want = t._pairs.filter(([a, b]) => (idx.has(a) || idx.has(b)) && !cache.has(k + ":" + a) && !inflight.has(fp + "|" + k + ":" + a));
    tb._asked = true;
    if (!want.length) return;
    let batch = [], size = 0;
    const flush = () => { if (batch.length) send(f, fp, k, batch, t); batch = []; size = 0; };
    for (const p of want) {
      const n = cellText(f, k, p[0]).length + cellText(f, k, p[1]).length;
      if (size && size + n > POST_CHARS) flush();
      batch.push(p); size += n;
    }
    flush();
  }

  // Where a hunk's intra-line job shows its progress / Cancel, or says it failed: above its table.
  function jobsBox(t) {
    const prev = t.previousElementSibling;
    if (prev && prev.classList.contains("dv-jobs")) return prev;
    const box = el("div", "dv-jobs small");
    t.before(box);
    return box;
  }

  async function send(f, fp, k, pairs, t) {
    for (const [a] of pairs) inflight.add(fp + "|" + k + ":" + a);
    let strip = null;
    const onJob = (j) => {
      if (!strip && MK.jobs && MK.jobs.strip && t.isConnected) strip = MK.jobs.strip(jobsBox(t), "dv-strip");
      if (strip) strip.set(j);
    };
    const r = await MK.api("/api/diff/intraline", { pairs: pairs.map(([a, b]) => [cellText(f, k, a), cellText(f, k, b)]) }, { onJob }).catch(() => null);
    for (const [a] of pairs) inflight.delete(fp + "|" + k + ":" + a);
    if (strip && r && !r.error) strip.done();
    if (!r || r.error || !Array.isArray(r.pairs)) {
      if (strip && r && r.cancelled) strip.done();
      if (r && r.error && !r.cancelled && t.isConnected) {
        MK.regionSay(jobsBox(t), "error", MK.whyFailed(r, "mark the changed words"), { replace: true });
      }
      return;
    }
    const cache = ixCache.get(fp) || new Map();
    ixCache.set(fp, cache);
    // paint every table showing this file's hunk NOW — the view may have been re-drawn while
    // the answer was on its way (an outline or impact answer re-renders the file)
    const tables = [...document.querySelectorAll(`table.d[data-k="${k}"]`)].filter((x) => x._f && fpOf(x._f) === fp);
    pairs.forEach(([a, b], n) => {
      const got = r.pairs[n];
      if (!got) return;
      cache.set(k + ":" + a, { ranges: got.old, basis: got.basis });
      cache.set(k + ":" + b, { ranges: got.new, basis: got.basis });
      for (const tt of tables) {
        for (const [i, side] of [[a, "old"], [b, "new"]]) {
          const td = tt.querySelector(`td.tx[data-i="${i}"]`);
          if (!td) continue;
          td._ix = side === "old" ? got.old : got.new;
          td._basis = got.basis;
          paintCell(td);
        }
      }
    });
  }

  // =========================================================================== expand context
  function ctxQuery(f, start, count) {
    const q = new URLSearchParams({ path: f.path, start: String(start), count: String(count) });
    for (const key of ["rev", "snap", "session"]) if (f.ctx && f.ctx[key]) q.set(key, f.ctx[key]);
    return "/api/diff/context?" + q.toString();
  }

  // `cut`: [[index, full bytes]] — a line the server cut at LINE_BYTES is shown cut, and SAYS so
  function ctxRows(f, g, start, lines, cut) {
    const cuts = new Map((cut || []).map(([i, n]) => [i, n]));
    const out = [];
    lines.forEach((text, n) => {
      const nn = start + n, on = nn + g.delta, full = cuts.get(n);
      const tr = el("tr", "dv-ctx");
      if (dv.mode === "split") {
        tr.append(noCell(f, "old", on), plainCell(text, "old", full), noCell(f, "new", nn), plainCell(text, "new", full));
      } else tr.append(noCell(f, "old", on), noCell(f, "new", nn), plainCell(" " + text, "", full));
      out.push(tr);
    });
    return out;
  }
  function plainCell(text, side, full) {
    const td = el("td", "tx", text.endsWith("\r") ? text.slice(0, -1) : text);
    if (side) td.dataset.side = side;
    if (full !== undefined) {
      td.classList.add("dv-cut-line");
      const m = el("span", "dv-cut", `line cut — ${full.toLocaleString()} bytes in all; open the file to read it whole`);
      td.append(m);
    }
    return td;
  }
  function ctxTable(split) {
    const t = el("table", "d dv-ctx-t" + (split ? " split" : ""));
    const cg = el("colgroup");
    for (const c of split ? ["no", "tx", "no", "tx"] : ["no", "no", "tx"]) cg.append(el("col", "dv-col-" + c));
    t.append(cg);
    return t;
  }

  // What has been expanded, per file diff and gap — kept across a re-draw of the file (an impact or
  // outline answer, a live refresh), so expanded lines never vanish under the reader.
  const ctxState = new Map();   // fingerprint -> Map(gap key -> state)
  function gapState(f, g) {
    const fp = fpOf(f);
    let m = ctxState.get(fp);
    if (!m) { m = new Map(); ctxState.set(fp, m); while (ctxState.size > 30) ctxState.delete(ctxState.keys().next().value); }
    const key = g.before + ":" + g.from;
    if (!m.has(key)) m.set(key, { lo: g.from, hi: g.to, eof: false, busy: false, top: [], bottom: [], error: "" });
    return { st: m.get(key), key, fp };
  }

  function gapBar(f, g) {
    const { st, key, fp } = gapState(f, g);
    const wrap = el("div", "dv-gap-wrap");
    wrap.tabIndex = -1;
    wrap.dataset.gkey = key;
    wrap._fp = fp;
    const can = f.ctx && Object.keys(f.ctx).length > 0;
    function left() { return st.hi === null ? null : st.hi - st.lo + 1; }
    function mk(text, title, act) {
      const b = el("button", "btn ghost small dv-more", text);
      b.type = "button"; b.title = title; b.dataset.act = act;
      b.onclick = () => run(act);
      return b;
    }
    function repaint() {
      const split = dv.mode === "split";
      const top = ctxTable(split), bottom = ctxTable(split);
      for (const [s0, lines, cut] of st.top) top.append(...ctxRows(f, g, s0, lines, cut));
      for (const [s0, lines, cut] of st.bottom) bottom.append(...ctxRows(f, g, s0, lines, cut));
      const parts = [top];
      const n = left();
      if (!(n !== null && n <= 0) && !(st.hi === null && st.eof)) {
        const bar = el("div", "dv-gap");
        bar.setAttribute("role", "group");
        const label = el("span", "dim small dv-gap-n", n === null ? "unchanged lines to the end of the file" : `${n.toLocaleString()} unchanged line${n === 1 ? "" : "s"}`);
        bar.setAttribute("aria-label", label.textContent);
        const btns = el("span", "dv-gap-btns");
        bar.append(label, btns);
        if (st.error) btns.append(el("span", "warn small", st.error));
        if (!can) label.title = "these lines are not shown: this view has no side to read them from (a SAMPLE, or a deleted file)";
        else if (st.hi === null) btns.append(mk(`Show ${STEP} more`, "the next unchanged lines, read from the file", "down"));
        else {
          if (g.before !== 0 || st.lo > 1) btns.append(mk(`Show ${Math.min(STEP, n)} below`, "unchanged lines after the hunk above", "down"));
          btns.append(mk(`Show ${Math.min(STEP, n)} above`, "unchanged lines before the hunk below", "up"));
          if (n > STEP) btns.append(mk(n <= PAGE_MAX ? `Show all ${n.toLocaleString()}` : `Show next ${PAGE_MAX}`, "in pages from the server", "all"));
        }
        for (const b of btns.querySelectorAll("button")) b.disabled = st.busy;
        parts.push(bar);
      }
      parts.push(bottom);
      wrap.replaceChildren(...parts);
    }
    wrap._repaint = repaint;
    async function fetchPage(start, count) {
      const r = await MK.api(ctxQuery(f, start, count)).catch((e) => ({ error: "unreachable", note: String(e && e.message || e) }));
      if (!r || r.error || r.state !== "ok") { st.error = MK.whyFailed(r && r.state === "unavailable" ? { note: r.note } : r, "read the unchanged lines"); return null; }
      st.error = "";
      return r;
    }
    async function down(count) {
      const r = await fetchPage(st.lo, count);
      if (!r) return;
      st.top.push([st.lo, r.lines, r.cut]);
      st.lo += r.lines.length;
      if (r.eof) { st.eof = true; if (st.hi !== null) st.hi = st.lo - 1; }
    }
    // A page may come back SHORT (the server stops a page at its byte budget): "above" keeps
    // asking from where the last page stopped until the run is whole, so no line is skipped.
    async function up(count) {
      const s0 = Math.max(st.lo, st.hi - count + 1);
      const got = [];
      let at = s0;
      while (at <= st.hi) {
        const r = await fetchPage(at, st.hi - at + 1);
        if (!r) return;
        if (!r.lines.length) break;
        got.push([at, r.lines, r.cut]);
        at += r.lines.length;
        if (r.eof) break;
      }
      st.bottom.unshift(...got);
      st.hi = s0 - 1;
    }
    // The wraps showing this gap NOW (the one clicked may have been re-drawn meanwhile).
    function live() { return [...document.querySelectorAll(`.dv-gap-wrap[data-gkey="${key}"]`)].filter((w) => w._fp === fp); }
    async function run(act) {
      if (st.busy) return;
      st.busy = true;
      for (const w of live()) w._repaint();
      const n = left();
      if (act === "up") await up(Math.min(STEP, n));
      else if (act === "all") await down(Math.min(n, PAGE_MAX));
      else await down(n === null ? STEP : Math.min(STEP, n));
      st.busy = false;
      const ws = live();
      for (const w of ws) w._repaint();
      // focus never falls to <body>: the same action in the bar, or the gap itself once it closed
      const w = ws[0];
      if (!w) return;
      const next = w.querySelector(`.dv-gap button[data-act="${act}"]`) || w.querySelector(".dv-gap button") || w;
      next.focus();
    }
    repaint();
    return wrap;
  }

  // Each gap goes INSIDE the hunk section below it (the trailing gap inside the last one), so a
  // view that hides the hunks (rendered Markdown / table diff) hides the gaps with them.
  dv.gaps = (wrap, f) => {
    const secs = wrap.querySelectorAll("section.hunk");
    for (const g of gapsOf(f)) {
      const sec = g.before >= 0 ? secs[g.before] : secs[secs.length - 1];
      if (!sec) continue;
      const bar = gapBar(f, g);
      if (g.before >= 0) sec.prepend(bar); else sec.append(bar);
    }
  };

  // =========================================================================== notes on the file
  dv.fileNote = (wrap, f) => {
    const b = dv._budget && dv._budget.f === f ? dv._budget : null;
    const notes = [];
    if (f.ws_hidden) notes.push(`${f.ws_hidden.toLocaleString()} whitespace-only line change${f.ws_hidden === 1 ? "" : "s"} hidden`);
    if (f.ws_kept) notes.push(`${f.ws_kept.toLocaleString()} indentation change${f.ws_kept === 1 ? "" : "s"} kept — indentation is code in this file`);
    if (b && b.lazy) notes.push(`${b.lazy.toLocaleString()} of ${(b.lazy + EAGER_ROWS).toLocaleString()} rows are built as you scroll to them`);
    if (!notes.length) return;
    const p = el("p", "dv-note small");
    p.textContent = notes.join(" · ");
    wrap.prepend(p);
  };
  dv.emptyWhy = (f) => f.ws_only
    ? `Only whitespace changed — ${f.ws_hidden} line${f.ws_hidden === 1 ? "" : "s"} hidden by “Ignore whitespace”.`
    : f.status === "renamed" ? "Renamed — the content is unchanged."
    : "No line changes (a file mode or metadata change).";
  dv.startFile = (f) => { dv._budget = { f, left: EAGER_ROWS, lazy: 0 }; };

  // The whole set: what "Ignore whitespace" hid, said where the diff's title is.
  dv.onDiff = (d) => {
    const head = MK.$("diff-stats");
    let p = MK.$("diff-ws");
    if (!p && head) {
      p = el("p", "small dim pad-x dv-ws-note");
      p.id = "diff-ws";
      const at = MK.$("diff-note");
      if (at) at.after(p);
    }
    if (!p) return;
    if (!d || d.ws !== "ignore") { p.hidden = true; return; }
    p.hidden = false;
    const files = (d.files || []).filter((x) => x.ws_hidden).length;
    p.replaceChildren(document.createTextNode(d.ws_hidden
      ? `Whitespace ignored: ${d.ws_hidden.toLocaleString()} whitespace-only line change${d.ws_hidden === 1 ? "" : "s"} hidden in ${files} file${files === 1 ? "" : "s"}.`
      : "Whitespace ignored: no change in this view was whitespace-only."));
    if (d.ws_kept) p.append(` ${d.ws_kept.toLocaleString()} indentation change${d.ws_kept === 1 ? " was" : "s were"} kept — indentation is code there.`);
    const b = el("button", "link small", "show whitespace");
    b.type = "button";
    b.onclick = () => setWs(false, b);
    p.append(" ", b);
  };
  dv.wsParam = () => (dv.ws === "ignore" ? "ws=ignore" : "");

  // =========================================================================== cites + selection
  function announce(text) {
    let live = MK.$("dv-live");
    if (!live) { live = el("div", "sr-only"); live.id = "dv-live"; live.setAttribute("role", "status"); document.body.append(live); }
    live.textContent = text;
  }
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (_e) { return false; }
  }
  function curFile(node) {
    const ui = MK.ui;
    if (node && node.closest && node.closest("#diff")) return ui && ui.files ? ui.files[ui.sel] : null;
    const t = node && node.closest && node.closest("table.d");
    const td = t && t.querySelector("td.tx[data-i]");
    return td ? td._f : null;
  }
  async function cite(td) {
    const f = curFile(td);
    if (!f) return;
    const side = td.dataset.side, n = td.dataset.n;
    const text = `${citePath(f, side)}:${n}`;
    const ok = await copy(text);
    announce(ok ? `Copied ${text} (${side === "old" ? "before" : "after"} the change)` : `Could not copy ${text}`);
    dv.lastCite = text;
  }
  // The selection's cite: its side, and the first..last line numbers of that side it covers.
  function selectionCite() {
    const s = window.getSelection && window.getSelection();
    if (!s || !s.rangeCount || s.isCollapsed) return null;
    const node = s.anchorNode && (s.anchorNode.nodeType === 1 ? s.anchorNode : s.anchorNode.parentElement);
    const td0 = node && node.closest && node.closest("td.tx");
    const t = td0 && td0.closest("table.d");
    if (!t) return null;
    const f = curFile(t);
    const side = td0.dataset.side || (td0.closest("tr").classList.contains("del") ? "old" : "new");
    const nums = [];
    for (const tr of t.querySelectorAll("tr")) {
      if (!s.containsNode(tr, true)) continue;
      const no = tr.querySelector(`td.no[data-side="${side}"]`);
      if (no && no.dataset.n) nums.push(+no.dataset.n);
    }
    if (!f || !nums.length) return null;
    const a = Math.min(...nums), b = Math.max(...nums);
    return `${citePath(f, side)}:${a}${b > a ? "-" + b : ""}`;
  }
  dv.selectionCite = selectionCite;

  // A re-draw of the file (an impact / outline answer, a live refresh) replaces #diff's children:
  // the button the reader was on is gone, so focus would fall to <body>. Put it back on the same
  // control in the new drawing (a gap's action), or on the file's diff itself.
  let lastFocus = null;
  function wireFocusKeeper() {
    const box = MK.$("diff");
    if (!box || typeof MutationObserver === "undefined") return;
    box.addEventListener("focusin", (ev) => {
      const b = ev.target;
      const w = b.closest && b.closest(".dv-gap-wrap");
      lastFocus = w ? { gkey: w.dataset.gkey, act: b.dataset ? b.dataset.act : "" } : null;
    });
    new MutationObserver(() => {
      if (!lastFocus) return;
      const a = document.activeElement;
      if (a && a !== document.body && a.isConnected) return;
      const w = box.querySelector(`.dv-gap-wrap[data-gkey="${lastFocus.gkey}"]`);
      if (!w) return;
      const t = (lastFocus.act && w.querySelector(`button[data-act="${lastFocus.act}"]`)) || w;
      t.focus();
    }).observe(box, { childList: true });
  }

  function wireDiffHost(host) {
    if (!host || host._dvWired) return;
    host._dvWired = true;
    host.addEventListener("click", (ev) => {
      const td = ev.target.closest && ev.target.closest("table.d td.no[data-n]");
      if (td) cite(td);
    });
    // a drag that starts on one side of a Split view selects that side only
    host.addEventListener("mousedown", (ev) => {
      const td = ev.target.closest && ev.target.closest("table.split td.tx");
      for (const t of host.querySelectorAll("table.split[data-sel]")) t.removeAttribute("data-sel");
      if (td && td.dataset.side) td.closest("table").dataset.sel = td.dataset.side;
    });
    // What a person COPIES from a Split selection is that side's code and nothing else. `user-select:
    // none` on the other side's cells is not enough: Chromium 141 still serialises the other side's
    // cells and the screen-reader "removed:/added:" spans into the copy. So the copy is built here.
    host.addEventListener("copy", (ev) => {
      const text = splitSideText();
      if (text === null || !ev.clipboardData) return;
      ev.clipboardData.setData("text/plain", text);
      ev.preventDefault();
    });
  }
  // The selected text of ONE side of a Split table (the side the drag started in), cell by cell and
  // clipped to the range at its ends; null when the selection is not inside a Split table.
  function splitSideText() {
    const s = window.getSelection && window.getSelection();
    if (!s || !s.rangeCount || s.isCollapsed) return null;
    const node = s.anchorNode && (s.anchorNode.nodeType === 1 ? s.anchorNode : s.anchorNode.parentElement);
    const t = node && node.closest && node.closest("table.split");
    const side = t && t.dataset.sel;
    if (!side) return null;
    const range = s.getRangeAt(0);
    const lines = [];
    for (const td of t.querySelectorAll(`td.tx[data-side="${side}"]`)) {
      if (!range.intersectsNode(td)) continue;
      const part = document.createRange();
      part.selectNodeContents(td);
      if (range.compareBoundaryPoints(Range.START_TO_START, part) > 0) part.setStart(range.startContainer, range.startOffset);
      if (range.compareBoundaryPoints(Range.END_TO_END, part) < 0) part.setEnd(range.endContainer, range.endOffset);
      const box = document.createElement("div");
      box.append(part.cloneContents());
      for (const x of box.querySelectorAll(".dv-sr, .dv-noeol, .dv-cr")) x.remove();
      lines.push(box.textContent);
    }
    return lines.join("\n");
  }
  dv.splitSideText = splitSideText;

  // =========================================================================== the toggles
  let tools = null;
  function rerender() {
    const ui = MK.ui;
    if (ui && ui.files && ui.files.length && MK.showFile) MK.showFile(ui.sel, { keepScroll: true });
    if (MK.onDiffViewChanged) MK.onDiffViewChanged();
  }
  // the toggles sit in the review bar, outside #diff: a re-draw never takes the focused one away
  function setMode(mode) {
    dv.mode = mode === "split" ? "split" : "unified";
    save("mode", dv.mode);
    paintTools();
    rerender();
  }
  async function setWs(on, from) {
    dv.ws = on ? "ignore" : "show";
    save("ws", dv.ws);
    paintTools();
    if (MK.refresh) await MK.refresh();
    const back = from && from.isConnected ? from : tools && tools.querySelector(".dv-ws");
    if (back) back.focus();
  }
  dv.setMode = setMode;
  dv.setWs = setWs;
  function paintTools() {
    if (!tools) return;
    for (const b of tools.querySelectorAll("[data-mode]")) b.setAttribute("aria-pressed", String(b.dataset.mode === dv.mode));
    const w = tools.querySelector(".dv-ws");
    w.setAttribute("aria-pressed", String(dv.ws === "ignore"));
  }
  function mountTools() {
    const bar = document.querySelector("#view-review .bar");
    if (!bar || tools) return;
    tools = el("div", "dv-tools");
    tools.setAttribute("role", "group");
    tools.setAttribute("aria-label", "Diff view");
    for (const [mode, label, title] of [["unified", "Unified", "one column: removed and added lines in order"], ["split", "Split", "two columns: before on the left, after on the right"]]) {
      const b = el("button", "seg", label);
      b.type = "button"; b.dataset.mode = mode; b.title = title;
      b.onclick = () => setMode(mode);
      tools.append(b);
    }
    const w = el("button", "seg dv-ws", "Ignore whitespace");
    w.type = "button";
    w.title = "show whitespace-only line changes as unchanged — the count of what is hidden is shown";
    w.onclick = () => setWs(dv.ws !== "ignore", w);
    tools.append(w);
    const at = MK.$("hunk-count");
    bar.insertBefore(tools, at || null);
    paintTools();
  }

  // ---------------------------------------------------------------- the hunk ⋯ menu
  // 0.0.27 UX-SIMPLIFY #36: three pills on every hunk head (Blame · Copy Ref · Copy with Context)
  // were the noisiest thing on the main screen. Each hunk head now carries ONE ⋯ button; the
  // features add their items (MK.hunkMenu.add(provider, order)); a provider is
  // (file, hunk, section) -> {id, label, title, disabled, run(anchorRect)} or null. Keyboard: Enter /
  // Space / ArrowDown open it on the first enabled item, ArrowUp/Down/Home/End move, Esc or Tab
  // close it and hand focus back to the ⋯ button. Items are built when it opens (labels are current).
  const hk = { providers: [], open: null };
  function hunkNewStart(h) {
    const m = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/.exec((h && h.header) || "");
    return m ? { line: Math.max(1, +m[1]), deletion: m[2] === "0" } : null;
  }
  function openInCodeItem(f) {
    return (h) => {
      const at = hunkNewStart(h);
      const gone = f.status === "deleted";
      return { id: "open", label: "Open in Code", disabled: gone || !MK.openFile,
               title: gone ? "the file was deleted — there is nothing to open" : "open the file in Code at this hunk",
               run: () => MK.openFile(f.path, at ? at.line : 1, null) };
    };
  }
  function closeHunkMenu(restore) {
    const o = hk.open;
    if (!o) return;
    hk.open = null;
    o.menu.remove();
    o.btn.setAttribute("aria-expanded", "false");
    if (restore !== false && o.btn.isConnected) o.btn.focus();
  }
  function hunkItems(f, h, sec) {
    const out = [];
    for (const p of hk.providers) {
      let it = null;
      try { it = p.fn(f, h, sec); } catch (_e) { it = null; }     // one feature never breaks the menu
      if (it) out.push(it);
    }
    out.push(openInCodeItem(f)(h));
    return out;
  }
  function openHunkMenu(btn, sec, focusLast) {
    closeHunkMenu(false);
    const f = MK.ui && MK.ui.files ? MK.ui.files[MK.ui.sel] : null;
    const h = f ? (f.hunks || [])[+sec.dataset.k] : null;
    if (!f || !h) return;
    const menu = el("div", "tab-menu hk-menu");
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", "hunk actions");
    menu.id = "hk-menu";
    for (const it of hunkItems(f, h, sec)) {
      const b = el("button", "tab-menu-item hk-item", it.label);
      b.type = "button";
      b.setAttribute("role", "menuitem");
      b.dataset.item = it.id;
      if (it.title) b.title = it.title;
      b.disabled = !!it.disabled;
      b.onclick = (ev) => {
        ev.stopPropagation();
        const r = btn.getBoundingClientRect();
        closeHunkMenu();
        it.run({ x: r.left, y: r.bottom + 4 });
      };
      menu.append(b);
    }
    menu.addEventListener("keydown", (ev) => {
      const all = [...menu.querySelectorAll("button:not([disabled])")];
      const i = all.indexOf(document.activeElement);
      if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); closeHunkMenu(); }
      else if (ev.key === "Tab") { closeHunkMenu(false); }
      else if (!all.length) return;
      else if (ev.key === "ArrowDown") { ev.preventDefault(); all[(i + 1) % all.length].focus(); }
      else if (ev.key === "ArrowUp") { ev.preventDefault(); all[(i - 1 + all.length) % all.length].focus(); }
      else if (ev.key === "Home") { ev.preventDefault(); all[0].focus(); }
      else if (ev.key === "End") { ev.preventDefault(); all[all.length - 1].focus(); }
    });
    document.body.append(menu);
    const r = btn.getBoundingClientRect();
    menu.style.left = `${Math.max(4, Math.min(r.left, window.innerWidth - 220))}px`;
    menu.style.top = `${Math.max(4, Math.min(r.bottom + 2, window.innerHeight - 160))}px`;
    btn.setAttribute("aria-expanded", "true");
    hk.open = { menu, btn };
    const en = [...menu.querySelectorAll("button:not([disabled])")];
    const first = focusLast ? en[en.length - 1] : en[0];
    (first || menu.querySelector("button") || menu).focus();
  }
  function decorateHunks() {
    const diff = MK.$("diff");
    if (!diff) return;
    for (const sec of diff.querySelectorAll("section.hunk")) {
      const head = sec.querySelector(".hunk-head");
      if (!head || head.querySelector(".hk-btn")) continue;
      const b = el("button", "hk-btn", "⋯");
      b.type = "button";
      b.setAttribute("aria-haspopup", "menu");
      b.setAttribute("aria-expanded", "false");
      b.setAttribute("aria-label", "hunk actions");
      b.title = "Blame · Copy with Context · Open in Code";
      b.onclick = (ev) => { ev.stopPropagation(); if (hk.open && hk.open.btn === b) closeHunkMenu(); else openHunkMenu(b, sec); };
      b.addEventListener("keydown", (ev) => {
        if (ev.key === "ArrowDown" || ev.key === "ArrowUp") { ev.preventDefault(); ev.stopPropagation(); openHunkMenu(b, sec, ev.key === "ArrowUp"); }
      });
      head.append(b);
    }
  }
  MK.hunkMenu = {
    add(fn, order) { hk.providers.push({ fn, order: order || 50 }); hk.providers.sort((a, b) => a.order - b.order); },
    close: closeHunkMenu,
    isOpen: () => !!hk.open,
  };
  function wireHunkMenu() {
    const diff = MK.$("diff");
    if (diff && typeof MutationObserver !== "undefined") new MutationObserver(decorateHunks).observe(diff, { childList: true, subtree: true });
    document.addEventListener("mousedown", (ev) => { if (hk.open && !hk.open.menu.contains(ev.target) && ev.target !== hk.open.btn) closeHunkMenu(false); });
    decorateHunks();
  }

  function mount() {
    if (!MK.$ || !document.getElementById("view-review")) return;
    mountTools();
    wireDiffHost(document.body);
    wireFocusKeeper();
    wireHunkMenu();
  }
  window.mokataIDE.register({
    id: "diffview",
    commands: [
      { id: "diff.split", title: "Diff: show side by side (split)", run: () => setMode("split") },
      { id: "diff.unified", title: "Diff: show unified", run: () => setMode("unified") },
      { id: "diff.whitespace", title: "Diff: ignore whitespace (on / off)", run: () => setWs(dv.ws !== "ignore") },
      { id: "diff.cite", title: "Diff: copy path:line of the selection", run: async () => {
        const c = selectionCite();
        if (!c) { announce("Select some lines of a diff first"); return; }
        const ok = await copy(c);
        dv.lastCite = c;
        announce(ok ? `Copied ${c}` : `Could not copy ${c}`);
      } },
    ],
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();
})();
