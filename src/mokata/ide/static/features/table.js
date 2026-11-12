// mokata ide — delimited text as a TABLE (0.0.27 P8, ID-CSV-VIEW).
//   * Code view: a .csv/.tsv/.tab/.psv file opens as a table (sticky header, resizable columns,
//     virtualised rows paged from the server by byte offset); the "Raw text" button is one click
//     away. Other delimiters are sniffed by the server.
//   * Sort and Filter are SERVER jobs over the WHOLE file (external sort, spooled to disk):
//     progress, Cancel, and a view that exists only when it is complete — never a sort of the rows
//     that happen to be loaded.
//   * Review: a changed table file gets "Table diff" — added/removed rows and changed cells,
//     matched by a key column or by row position; "Show the line diff" is one click back.
//   MK.table.exportText(rows, fmt) -> Promise<string>  the copy/export text (server-guarded).
// SAFETY: every cell goes into the page through textContent, never as markup; copy and export go
// through /api/table/export, which neutralises spreadsheet formulas (one guard, server side).
"use strict";

(function () {
  const MK = window.MK;
  if (typeof document === "undefined") return;
  const el = MK.el;
  const AUTO = /\.(csv|tsv|tab|psv)$/i, OFFER = /\.(txt|dat|dsv)$/i;
  const ROW_H = 26, PAGE = 200, MAX_PX = 8000000, KEEP_PAGES = 100;
  const T = (MK.table = MK.table || {});

  function btn(text, cls, title) {
    const b = el("button", cls || "btn ghost small", text);
    b.type = "button";
    if (title) b.title = title;
    return b;
  }
  const show = (c) => String(c == null ? "" : c).replace(/\r\n|\n|\r/g, "↵");
  const n = (x) => Number(x).toLocaleString();

  T.exportText = async function (rows, fmt) {
    const r = await MK.api("/api/table/export", { rows, format: fmt || "csv" });
    if (!r || r.error) throw new Error((r && (r.note || r.error)) || "could not prepare the text");
    return r.text;
  };
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (_e) { /* fall through */ }
    const ta = el("textarea");
    ta.value = text; document.body.append(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_e) { ok = false; }
    ta.remove();
    return ok;
  }
  function download(text, name) {
    const a = el("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
    a.download = name;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  function widthsFor(header, rows) {
    return header.map((h, i) => {
      let m = String(h).length;
      for (const r of rows.slice(0, 50)) m = Math.max(m, show(r[i]).length);
      return Math.max(80, Math.min(320, m * 8 + 28));
    });
  }

  // =============================================================================== the table
  function mountTable(host, path) {
    const st = { info: null, pages: new Map(), pending: new Set(), widths: [], userSized: false,
                 spec: {}, view: null, building: false, sel: new Set(), last: -1, dead: false,
                 counting: false, uncounted: "", retry: 0, job: null };
    host.replaceChildren();
    const root = el("div", "tb"); host.append(root);
    const bar = el("div", "tb-bar");
    const meta = el("span", "tb-meta"), chips = el("span", "tb-chips");
    const bCopy = btn("Copy", "btn ghost small", "copy the selected rows (or the rows in view) as CSV — formulas are neutralised");
    const bExp = btn("Export loaded", "btn ghost small", "download the loaded rows as CSV — formulas are neutralised with a leading apostrophe");
    bar.append(meta, chips, el("span", "spacer"), bCopy, bExp);
    const fbar = el("form", "tb-filter small"); fbar.setAttribute("aria-label", "filter rows");
    const fCol = el("select", "tb-key"); fCol.setAttribute("aria-label", "filter column");
    const fOp = el("select", "tb-key"); fOp.setAttribute("aria-label", "filter operator");
    for (const [v, t] of [["contains", "contains"], ["equals", "equals"], ["starts", "starts with"], ["empty", "is empty"], ["notempty", "is not empty"], ["gt", "> (number)"], ["gte", "≥ (number)"], ["lt", "< (number)"], ["lte", "≤ (number)"]]) fOp.append(new Option(t, v));
    const fVal = el("input", "tb-fval"); fVal.type = "text"; fVal.placeholder = "value"; fVal.setAttribute("aria-label", "filter value");
    const bFilter = btn("Filter", "btn ghost small", "scan the whole file for matching rows (a job you can cancel)");
    bFilter.type = "submit";
    const bClear = btn("Clear", "btn ghost small", "back to the file as it is, in file order"); bClear.hidden = true;
    fbar.append(fCol, fOp, fVal, bFilter, bClear);
    const jobBox = el("div", "tb-job");
    const note = el("div", "tb-note small"); note.hidden = true;
    const scroll = el("div", "tb-scroll"); scroll.tabIndex = 0;
    scroll.setAttribute("role", "table"); scroll.setAttribute("aria-label", "table of " + path);
    const head = el("div", "tb-head"); head.setAttribute("role", "row");
    const body = el("div", "tb-body");
    const rowsEl = el("div", "tb-rows");
    body.append(rowsEl); scroll.append(head, body);
    root.append(bar, fbar, jobBox, note, scroll);
    const strip = MK.jobs && MK.jobs.strip ? MK.jobs.strip(jobBox) : { say() {}, set() {}, done() {} };
    const q = (u, extra) => u + "?" + new URLSearchParams({ path, ...extra }).toString();

    const total = () => st.view ? st.view.matched
      : st.info.rows != null ? st.info.rows : Math.max(st.info.estimate || 0, st.info.indexed || 0);
    const cols = () => st.info.header.length || st.info.width;
    const rowAt = (i) => {
      const p = st.pages.get(Math.floor(i / PAGE));
      return p ? { i, n: p.nums ? p.nums[i - p.row] : i, cells: p.rows[i - p.row] || [], flags: p.flags[String(i)] || [] } : null;
    };
    function paintMeta() {
      const i = st.info, parts = [];
      if (st.view) parts.push(`${n(st.view.matched)} of ${n(st.view.scanned)} rows`);
      else parts.push(i.rows != null ? `${n(i.rows)} rows`
        : st.counting ? `≈${n(i.estimate)} rows · counting…`
        : `≈${n(i.estimate)} rows · not counted: ${st.uncounted}`);
      parts.push(`${cols()} columns`, `${i.delimiter_name} (${i.delimiter_by})`);
      meta.textContent = parts.join(" · ");
      chips.replaceChildren();
      const chip = (t, ti) => { const c = el("span", "tb-chip", t); c.title = ti; chips.append(c); };
      if (i.ragged) chip(`${n(i.ragged)} ragged`, "rows whose cell count differs from the header's — shown, flagged ⚠");
      if (i.bad_utf8) chip("not UTF-8", "some bytes are not UTF-8 and show as � (flagged on their rows)");
      if (i.cut) chip("cells cut", `cells over ${n(i.cell_max)} characters are cut for display`);
      if (i.encoding !== "utf-8") chip(i.encoding === "utf-16-le" ? "UTF-16 LE" : "UTF-16 BE", "decoded from UTF-16 (its byte-order mark was read, not shown)");
      else if (i.bom) chip("BOM", "a UTF-8 byte-order mark was skipped");
    }
    function paintHead() {
      const ac = document.activeElement;
      const hadFocus = ac && ac.classList && head.contains(ac) ? (ac.dataset.col != null ? "col" + ac.dataset.col : "") : null;
      head.replaceChildren();
      const g = el("div", "tb-gut tb-th", "#"); head.append(g);
      st.info.header.forEach((h, c) => {
        const th = el("div", "tb-th"); th.setAttribute("role", "columnheader");
        th.style.width = st.widths[c] + "px";
        const b = el("button", "tb-sort", show(h) || `column ${c + 1}`);
        b.type = "button"; b.dataset.col = c; b.title = "sort the whole file by this column (a job you can cancel)";
        const dd = dirOf(c);
        th.setAttribute("aria-sort", dd ? (dd > 0 ? "ascending" : "descending") : "none");
        if (dd) b.append(el("span", "tb-arrow", dd > 0 ? " ▲" : " ▼"));
        b.onclick = () => sortBy(c);
        const rz = el("span", "tb-rz"); rz.tabIndex = 0;
        rz.setAttribute("role", "separator"); rz.setAttribute("aria-label", "resize column " + (c + 1));
        const setW = (w) => { st.widths[c] = Math.max(40, w); st.userSized = true; th.style.width = st.widths[c] + "px"; draw(); };
        rz.onpointerdown = (ev) => {
          ev.preventDefault(); rz.setPointerCapture(ev.pointerId);
          const x0 = ev.clientX, w0 = st.widths[c];
          rz.onpointermove = (e) => setW(w0 + e.clientX - x0);
          rz.onpointerup = () => { rz.onpointermove = null; rz.onpointerup = null; };
        };
        rz.onkeydown = (ev) => { if (ev.key === "ArrowRight") setW(st.widths[c] + 12); else if (ev.key === "ArrowLeft") setW(st.widths[c] - 12); };
        th.append(b, rz); head.append(th);
      });
      head.style.width = (44 + st.widths.reduce((a, b2) => a + b2, 0)) + "px";
      if (hadFocus && hadFocus.startsWith("col")) {                // a redraw must not drop keyboard focus to <body>
        const again = head.querySelector(`.tb-sort[data-col="${hadFocus.slice(3)}"]`);
        if (again) again.focus();
      }
    }
    let first = 0, vis = 30, scheduled = false;
    function geometry() {
      const N = total(), px = Math.min(N * ROW_H, MAX_PX);
      const viewH = Math.max(scroll.clientHeight - head.offsetHeight, ROW_H);
      vis = Math.ceil(viewH / ROW_H) + 2;
      const top = scroll.scrollTop;
      const scaled = N * ROW_H > MAX_PX;
      first = scaled ? Math.round(top / Math.max(1, px - viewH) * Math.max(0, N - vis)) : Math.floor(top / ROW_H);
      first = Math.max(0, Math.min(first, Math.max(0, N - 1)));
      return { N, px, scaled, top };
    }
    function draw() {
      scheduled = false;
      if (st.dead || !st.info) return;
      const g = geometry();
      body.style.height = g.px + "px";
      body.style.width = head.style.width;
      rowsEl.style.top = (g.scaled ? g.top : first * ROW_H) + "px";
      const frag = document.createDocumentFragment();
      const want = new Set();
      for (let i = first; i < Math.min(g.N, first + vis); i++) {
        const r = rowAt(i);
        const row = el("div", "tb-row"); row.setAttribute("role", "row");
        row.dataset.i = i;
        if (!r) { want.add(Math.floor(i / PAGE)); row.classList.add("tb-wait"); row.append(el("div", "tb-gut", i + 1), el("div", "tb-td dim", st.counting && i >= (st.info.indexed || 0) ? "counting…" : "loading…")); frag.append(row); continue; }
        const fl = r.flags || [];
        const gut = el("div", "tb-gut", (r.n + 1) + (fl.includes("ragged") ? " ⚠" : "") + (fl.includes("bad") ? " �" : "") + (fl.includes("cut") ? " ✂" : ""));
        if (fl.length) gut.title = [fl.includes("ragged") ? `${r.cells.length} cells, header has ${cols()}` : "", fl.includes("bad") ? "bytes that are not UTF-8 were replaced" : "", fl.includes("cut") ? "a long cell is cut for display" : ""].filter(Boolean).join("; ");
        row.append(gut);
        if (fl.includes("ragged")) row.classList.add("tb-ragged");
        if (fl.includes("bad")) row.classList.add("tb-bad");
        if (st.sel.has(r.i)) row.classList.add("tb-sel");
        for (let c = 0; c < Math.max(cols(), r.cells.length); c++) {
          const td = el("div", "tb-td", show(r.cells[c]));
          td.setAttribute("role", "cell");
          td.style.width = (st.widths[c] || 140) + "px";
          if (c >= cols()) td.classList.add("tb-extra");
          if (r.cells[c] != null && r.cells[c].length > 24) td.title = r.cells[c].slice(0, 600);
          row.append(td);
        }
        gut.onclick = (ev) => select(r.i, ev.shiftKey);
        frag.append(row);
      }
      rowsEl.replaceChildren(frag);
      for (const p of want) need(p);
    }
    const later = () => { if (!scheduled) { scheduled = true; requestAnimationFrame(draw); } };
    scroll.addEventListener("scroll", later);
    function select(i, range) {
      if (range && st.last >= 0) for (let k = Math.min(i, st.last); k <= Math.max(i, st.last); k++) st.sel.add(k);
      else if (st.sel.has(i)) st.sel.delete(i); else st.sel.add(i);
      st.last = i; draw();
    }
    async function need(p) {
      if (st.pending.has(p) || st.pages.has(p) || st.dead) return;
      st.pending.add(p);
      const view = st.view;
      const d = await MK.api(view ? "/api/table/view/rows?" + new URLSearchParams({ id: view.id, from: p * PAGE, count: PAGE })
        : q("/api/table/rows", { row: p * PAGE, count: PAGE }));
      st.pending.delete(p);
      if (st.dead) return;
      // A newer view replaced this one: drop the page, and draw again — the new view's page was
      // skipped while this one was pending (same page number), so nothing else would ask for it.
      if (st.view !== view) { draw(); return; }
      if (!d || d.error || d.state !== "ok") { note.hidden = false; note.textContent = (d && (d.note || d.error)) || "could not read rows"; return; }
      if (d.indexed === false) {   // the visible count job has not got that far yet: look again soon
        st.info.indexed = d.indexed_rows; st.info.estimate = d.estimate;
        clearTimeout(st.retry); st.retry = setTimeout(draw, 1000);
        return;
      }
      if (view) d.row = d.from;
      st.pages.set(p, d);
      if (!view && d.total != null) st.info.rows = d.total;
      if (!st.userSized && p === 0) { st.widths = widthsFor(st.info.header, d.rows); paintHead(); }
      if (st.pages.size > KEEP_PAGES) {
        const far = [...st.pages.keys()].sort((a, b) => Math.abs(b - p) - Math.abs(a - p))[0];
        st.pages.delete(far);
      }
      paintMeta(); draw();
    }
    // ---- sort and filter: a SERVER job over the whole file; the view exists only when complete
    function loaded() {
      const out = [];
      for (const p of [...st.pages.keys()].sort((a, b) => a - b)) {
        const pg = st.pages.get(p);
        pg.rows.forEach((cells, k) => out.push({ n: pg.nums ? pg.nums[k] : pg.row + k, cells }));
      }
      return out;
    }
    function dirOf(c) { return st.spec.scol === c ? st.spec.sdir : 0; }
    function sortBy(c) {
      const d = dirOf(c);
      const next = Object.assign({}, st.spec);
      if (d === 0) { next.scol = c; next.sdir = 1; } else if (d > 0) { next.sdir = -1; } else { delete next.scol; delete next.sdir; }
      return buildView(next);
    }
    function paintFilterBar() {
      bClear.hidden = !(st.spec.fop || st.spec.scol != null);
      fbar.setAttribute("aria-busy", st.building ? "true" : "false");     // controls stay enabled: focus must not fall to <body>
    }
    async function buildView(spec) {
      if (st.building) return;
      const want = Object.assign({}, spec);
      if (want.scol == null && !want.fop) { st.spec = {}; st.view = null; st.pages.clear(); st.sel.clear(); note.hidden = true; paintHead(); paintFilterBar(); paintMeta(); scroll.scrollTop = 0; draw(); return; }
      st.building = true; note.hidden = true; paintFilterBar();
      const p = {};
      if (want.scol != null) { p.scol = want.scol; p.sdir = want.sdir; }
      if (want.fop) { p.fcol = want.fcol; p.fop = want.fop; p.fval = want.fval || ""; }
      strip.say(want.scol != null ? "sorting the whole file…" : "filtering the whole file…");
      const r = await MK.api(q("/api/table/view", p), undefined, { onJob: (j) => strip.set(j) });
      st.building = false;
      if (st.dead) return;
      if (!r || r.cancelled || r.failed) { paintFilterBar(); return; }          // the strip keeps saying so; nothing changes
      strip.done();
      if (r.error || r.state !== "ok") { note.hidden = false; note.textContent = (r && (r.note || r.error)) || "could not build the view"; paintFilterBar(); return; }
      st.spec = want; st.view = r; st.pages.clear(); st.sel.clear();
      note.hidden = false; note.replaceChildren();
      const bits = [];
      if (want.scol != null) bits.push(`Sorted by ${show(st.info.header[want.scol]) || "column " + (want.scol + 1)} ${want.sdir > 0 ? "ascending" : "descending"} over the whole file (${n(r.scanned)} rows; empty cells last)`);
      if (want.fop) bits.push(`${n(r.matched)} of ${n(r.scanned)} rows match`);
      if (r.not_numeric) bits.push(`${n(r.not_numeric)} cells were not numbers and did not match`);
      note.append(bits.join(" · ") + ". ");
      paintHead(); paintFilterBar(); paintMeta(); scroll.scrollTop = 0; draw();
    }
    fbar.onsubmit = (ev) => {
      ev.preventDefault();
      const op = fOp.value;
      const spec = Object.assign({}, st.spec, { fcol: Number(fCol.value), fop: op, fval: fVal.value });
      if (!["empty", "notempty"].includes(op) && fVal.value === "") { note.hidden = false; note.textContent = "Type a value to filter by."; return; }
      buildView(spec);
    };
    bClear.onclick = () => { fVal.value = ""; fVal.focus(); buildView({}); };
    // ---- copy / export
    function header() { return st.info.header; }
    bCopy.onclick = async () => {
      const rows = [];
      if (st.sel.size) { for (const i of [...st.sel].sort((a, b) => a - b)) { const r = rowAt(i); if (r) rows.push(r.cells); } }
      else for (let i = first; i < Math.min(total(), first + vis); i++) { const r = rowAt(i); if (r) rows.push(r.cells); }
      const text = await T.exportText([header(), ...rows], "tsv").catch((e) => { note.hidden = false; note.textContent = String(e.message); return null; });
      if (text != null) { await copy(text); bCopy.textContent = "Copied"; setTimeout(() => { bCopy.textContent = "Copy"; }, 1500); }
    };
    bExp.onclick = async () => {
      const rows = loaded().map((r) => r.cells);
      const text = await T.exportText([header(), ...rows], "csv").catch((e) => { note.hidden = false; note.textContent = String(e.message); return null; });
      if (text != null) download(text, (path.split("/").pop() || "table") + ".export.csv");
    };
    // ---- open
    (async function open() {
      const info = await MK.api(q("/api/table/info"));
      if (st.dead) return;
      if (!info || info.error || info.state !== "ok") { root.replaceChildren(el("p", "warn", (info && (info.note || info.error || info.state)) || "could not read the file")); return; }
      st.info = info; st.info.indexed = 0;
      st.widths = info.header.map(() => 140);
      fCol.append(new Option("any column", "-1"));
      info.header.forEach((h, c) => fCol.append(new Option(show(h) || "column " + (c + 1), String(c))));
      // a count about to start is "counting" from the FIRST paint: the meta line never shows an
      // outcome ("not counted") before the count has had one (0.0.28 D — the Linux runner read it)
      st.counting = !info.complete;
      paintMeta(); paintHead(); paintFilterBar(); draw();
      if (!info.complete) {
        strip.say("counting rows…");
        // what a count's answer does to the table - the first one, and the one the Jobs drawer's Retry
        // brings back (`onRetry`): a retried count updates THIS table, no reopen, no new button
        const counted = (r) => {
          st.counting = false;
          if (st.dead) return;
          if (r && r.state === "ok") { strip.done(); Object.assign(st.info, r); st.info.estimate = r.rows; } else {
            // Each way a count can end without a number says its OWN word (0.0.28 D) — never "reload",
            // which fails the same way: cancelled -> the Jobs drawer's Retry (the existing control);
            // failed -> the job's error; cannot be counted (a record over the size bound) -> the server's reason.
            strip.done();
            st.uncounted = r && r.cancelled ? "the count was cancelled — Retry in the Jobs drawer"
              : r && r.failed ? `the count failed: ${r.note || "no reason given"}`
              : (r && (r.note || r.error)) || "the count could not finish";
          }
          paintMeta(); draw();
        };
        const onJob = (j) => {
          if (st.dead) return;
          strip.set(j);
          if (!st.counting && ["queued", "running", "partial"].includes(j.state)) { st.counting = true; paintMeta(); }
        };
        counted(await MK.api(q("/api/table/count"), undefined, { onJob, onRetry: counted }));
      }
    })();
    return { destroy() { st.dead = true; clearTimeout(st.retry); host.replaceChildren(); }, state: st, redraw: draw };
  }
  T.mount = mountTable;

  // =============================================================================== Code view
  let toggle = null, tbHost = null, cur = null;
  const raw = new Set();
  function mountCode() {
    const bar = document.querySelector("#view-navigate .bar"), scroll = MK.$("code-scroll");
    if (!bar || toggle || !scroll) return;
    toggle = btn("Raw text", "pill-link tb-toggle", "show this file as plain text");
    toggle.id = "tb-toggle"; toggle.hidden = true;
    toggle.onclick = () => {
      const p = MK.nav.path;
      if (cur && cur.path === p) { raw.add(p); hideTable(); } else { raw.delete(p); showTable(p); }
      sync();
    };
    bar.append(toggle);
    tbHost = el("div", "tb-code"); tbHost.id = "tb-code"; tbHost.hidden = true;
    scroll.append(tbHost);
    const code = MK.$("code");
    if (code) new MutationObserver(sync).observe(code, { childList: true });
    // A file the Code view will not show as text (UTF-16 has NULs) leaves #code untouched, but
    // every open rewrites the breadcrumbs: watch those too, so the table follows the open file.
    const crumbs = MK.$("crumbs");
    if (crumbs) new MutationObserver(sync).observe(crumbs, { childList: true });
  }
  function showTable(path) {
    if (cur && cur.path === path) return;
    hideTable();
    tbHost.hidden = false; MK.$("code-scroll").classList.add("tb-on"); MK.$("code").hidden = true;
    cur = { path, view: mountTable(tbHost, path) };
  }
  function hideTable() {
    if (cur) { cur.view.destroy(); cur = null; }
    if (!tbHost) return;
    tbHost.hidden = true; MK.$("code-scroll").classList.remove("tb-on");
    const c = MK.$("code"); if (c) c.hidden = false;
  }
  function sync() {
    const path = MK.nav && MK.nav.path;
    if (!path || !toggle) { return; }
    const auto = AUTO.test(path), offer = auto || OFFER.test(path);
    toggle.hidden = !offer;
    if (!offer) { if (cur) hideTable(); return; }
    if (auto && !raw.has(path)) showTable(path); else if (cur && cur.path !== path) hideTable();
    toggle.textContent = cur && cur.path === path ? "Raw text" : "Table";
    toggle.title = cur ? "show this file as plain text" : "show this file as a table";
  }

  // =============================================================================== table diff
  const on = new Set();
  function sideRevs() {
    const v = MK.viewing ? MK.viewing() : { kind: "live" };
    if (v.kind === "live") return { base: "HEAD", head: "", label: "HEAD → working tree" };
    if (v.kind === "compare") {
      const b = MK.$("base") && MK.$("base").value, h = MK.$("head") && MK.$("head").value;
      if (b && h) return { base: b, head: h, label: `${b} → ${h}` };
    }
    return null;
  }
  function decorate() {
    const diff = MK.$("diff");
    const f = MK.ui && MK.ui.files ? MK.ui.files[MK.ui.sel] : null;
    if (!diff || !f || !AUTO.test(f.path) || f.binary || f.too_large) return;
    const bodyEl = diff.querySelector(".file-body");
    if (!bodyEl || bodyEl.querySelector(".tb-dbar")) return;
    const revs = sideRevs();
    const barEl = el("div", "tb-dbar small");
    const b = btn("Table diff", "pill-link tb-dbtn", revs ? "cell-level diff: added and removed rows, changed cells" : "a table diff needs Uncommitted changes or Compare branches");
    b.disabled = !revs;
    const keySel = el("select", "tb-key"); keySel.setAttribute("aria-label", "match rows by"); keySel.hidden = true;
    const out = el("div", "tb-dout"); out.hidden = true;
    barEl.append(b, keySel, el("span", "dim", revs ? revs.label : ""));
    bodyEl.prepend(barEl); barEl.after(out);
    b.onclick = () => {
      if (!out.hidden) { out.hidden = true; keySel.hidden = true; b.textContent = "Table diff"; on.delete(f.path); for (const s of bodyEl.querySelectorAll("section.hunk")) s.hidden = false; return; }
      on.add(f.path); out.hidden = false; b.textContent = "Show the line diff";
      for (const s of bodyEl.querySelectorAll("section.hunk")) s.hidden = true;
      runDiff(f, revs, "", out, keySel);
    };
    keySel.onchange = () => runDiff(f, revs, keySel.value, out, keySel);
    if (revs && on.has(f.path)) b.onclick(), (b.textContent = "Show the line diff");
  }
  async function runDiff(f, revs, key, out, keySel) {
    out.replaceChildren();
    const s = MK.jobs && MK.jobs.strip ? MK.jobs.strip(out) : { say() {}, set() {}, done() {} };
    s.say("comparing the tables…");
    const p = new URLSearchParams({ path: f.path, base: revs.base, key });
    if (revs.head) p.set("head", revs.head);
    if (f.old_path) p.set("old_path", f.old_path);
    const d = await MK.api("/api/table/diff?" + p.toString(), undefined, { onJob: (j) => s.set(j) });
    if (!d || d.cancelled || d.failed) return;
    s.done();
    if (d.error || d.state !== "ok") { out.append(el("p", "warn", (d && (d.note || d.error)) || "could not compare")); return; }
    const sm = d.summary, cols = sm.columns;
    if (!keySel.options.length) {
      keySel.append(new Option("match rows by position", ""));
      for (const c of cols) keySel.append(new Option("match rows by key: " + c, c));
    }
    keySel.value = key; keySel.hidden = false;
    const chips = el("div", "tb-dsum");
    chips.append(el("span", "tb-d-add", `+${n(sm.added)} added`), el("span", "tb-d-del", `−${n(sm.removed)} removed`), el("span", "tb-d-chg", `~${n(sm.changed)} changed`), el("span", "dim", `${n(sm.same)} unchanged`));
    if (sm.columns_added.length) chips.append(el("span", "tb-d-add", "columns added: " + sm.columns_added.join(", ")));
    if (sm.columns_removed.length) chips.append(el("span", "tb-d-del", "columns removed: " + sm.columns_removed.join(", ")));
    if (sm.duplicate_keys) chips.append(el("span", "warn", `${n(sm.duplicate_keys)} repeated keys (paired in order)`));
    out.append(chips);
    if (sm.memory) out.append(el("p", "tb-dmem dim small", sm.memory));
    if (!sm.changes) { out.append(el("p", "dim", "No cell differs.")); return; }
    const wrap = el("div", "tb-dwrap"), grid = el("div", "tb-dgrid");
    const hd = el("div", "tb-drow tb-dhead"); hd.append(el("div", "tb-gut", ""));
    for (const c of cols) { const th = el("div", "tb-td", c); th.style.width = "150px"; hd.append(th); }
    grid.append(hd); wrap.append(grid); out.append(wrap);
    let shown = 0;
    const more = btn("", "link small");
    const add = (chs) => {
      for (const c of chs) {
        const row = el("div", "tb-drow tb-d-" + c.op);
        row.append(el("div", "tb-gut", (c.op === "add" ? "+" : c.op === "del" ? "−" : "~") + " " + ((c.new_row != null ? c.new_row : c.old_row) + 1)));
        const cells = c.new || c.old;
        cols.forEach((_c, i) => {
          const td = el("div", "tb-td"); td.style.width = "150px";
          if (c.op === "chg" && c.changed.includes(i)) {
            td.classList.add("tb-cellchg");
            td.append(el("span", "tb-old", show(c.old[i])), el("span", "tb-new", show(c.new[i])));
            td.title = `${c.old[i] == null ? "" : c.old[i]} → ${c.new[i] == null ? "" : c.new[i]}`.slice(0, 600);
          } else td.textContent = show(cells[i]);
          row.append(td);
        });
        grid.append(row);
      }
      shown += chs.length;
      more.hidden = shown >= sm.changes;
      more.textContent = `Show the next changes (${n(shown)} of ${n(sm.changes)})`;
    };
    add(d.changes);
    more.onclick = async () => {
      const r = await MK.api("/api/table/diff/rows?" + new URLSearchParams({ id: d.id, from: shown, count: PAGE }));
      if (r && r.state === "ok") add(r.changes); else { more.hidden = true; out.append(el("p", "warn", (r && r.note) || "could not load")); }
    };
    out.append(more);
  }

  function mount() {
    if (!MK.$ || !document.getElementById("review-scroll")) return;
    mountCode();
    const diff = MK.$("diff");
    if (diff) new MutationObserver(decorate).observe(diff, { childList: true, subtree: true });
    if (MK.onViewing) MK.onViewing.push(() => { const d = MK.$("diff"); if (d) for (const b of d.querySelectorAll(".tb-dbar,.tb-dout")) b.remove(); decorate(); });
  }
  window.mokataIDE.register({ id: "table", commands: [
    { id: "table.raw", title: "Table: toggle raw text for the open file", run: () => { if (toggle && !toggle.hidden) toggle.click(); } },
  ] });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();
})();
