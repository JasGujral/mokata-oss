// mokata ide — syntax highlighting (0.0.26 P1, ID-SYNTAX-HIGHLIGHT).
// The SERVER lexes (Pygments, ~600 lexers) and answers token spans: per line, [[cls, text], ...].
// This file only paints them: each span is a <span class="tk-<cls>"> with its text set through
// textContent — no markup ever comes from the server, and the colours are theme tokens
// (static/features/highlight.css), so light / dark / high-contrast are CSS's choice.
//   * the file view (nav.js, virtualised): MK.hl.want() fetches the 1,000-line chunks the visible
//     rows touch; MK.hl.row() gives a line's spans; a bounded client cache keeps 40 chunks.
//   * every diff: the rendered hunks of the selected file are re-painted with spans from
//     /api/highlight/diff — each line coloured in its OWN side (old for "-", new for "+").
//   * spec / AC code blocks (plan.js mdLite fences): MK.hl.snippet(pre, lang).
"use strict";

(function () {
  const MK = window.MK;
  const CHUNK = 1000;
  const MAX_CHUNKS = 40;
  const chunks = new Map();          // `${gen}|${path}|${k}` -> {rows, lang, plain_reason}
  const inflight = new Set();
  const meta = {};                   // path -> {lang, detected_by, reasons:Set}
  let gen = 0;                       // bumped on every (re)open: a reloaded file never mixes chunks

  function key(path, k) { return `${gen}|${path}|${k}`; }

  function paint(target, spans) {
    const out = [];
    for (const [cls, text] of spans) {
      if (!cls) { out.push(document.createTextNode(text)); continue; }
      const s = document.createElement("span");
      s.className = "tk-" + cls;
      s.textContent = text;
      out.push(s);
    }
    if (!out.length) out.push(document.createTextNode(" "));
    target.replaceChildren(...out);
  }

  function open(path) {
    gen++;
    for (const k of [...chunks.keys()]) if (!k.startsWith(gen + "|")) chunks.delete(k);
    delete meta[path];
    showLang(path);
  }

  function row(path, n) {
    const c = chunks.get(key(path, Math.floor((n - 1) / CHUNK)));
    if (!c) return null;
    return c.rows[(n - 1) % CHUNK] || null;
  }

  function remember(k, v) {
    chunks.delete(k);
    chunks.set(k, v);
    while (chunks.size > MAX_CHUNKS) chunks.delete(chunks.keys().next().value);
  }

  function want(path, first, last, done) {
    const g = gen;
    for (let k = Math.floor((first - 1) / CHUNK); k <= Math.floor((last - 1) / CHUNK); k++) {
      const kk = key(path, k);
      if (chunks.has(kk)) { remember(kk, chunks.get(kk)); continue; }
      if (inflight.has(kk)) continue;
      inflight.add(kk);
      const q = `/api/highlight?path=${encodeURIComponent(path)}&start=${k * CHUNK + 1}&end=${(k + 1) * CHUNK}`;
      MK.api(q).then((res) => {
        inflight.delete(kk);
        if (g !== gen || !res || res.error || res.not_served) {
          if (res && res.not_served && g === gen) noteReason(path, res.plain_reason);
          return;
        }
        remember(kk, { rows: res.rows || [] });
        const m = (meta[path] = meta[path] || { reasons: new Set() });
        m.lang = res.lang; m.by = res.detected_by;
        if (res.plain_reason) m.reasons.add(res.plain_reason);
        showLang(path);
        done();
      }).catch(() => inflight.delete(kk));
    }
  }

  function noteReason(path, reason) {
    const m = (meta[path] = meta[path] || { reasons: new Set() });
    if (reason) m.reasons.add(reason);
    showLang(path);
  }

  // The language + any "why plain" reason, beside the breadcrumbs — never silently plain.
  function showLang(path) {
    const crumbs = MK.$("crumbs");
    if (!crumbs || !crumbs.parentNode) return;
    let tag = MK.$("hl-lang");
    if (!tag) {
      tag = MK.el("span", "hl-lang small");
      tag.id = "hl-lang";
      crumbs.parentNode.insertBefore(tag, crumbs.nextSibling);
    }
    const m = meta[path];
    if (!m || !MK.nav || MK.nav.path !== path) { tag.textContent = ""; tag.title = ""; return; }
    const why = [...m.reasons].join("; ");
    tag.textContent = (m.lang || "") + (why ? " · plain" : "");
    tag.title = why || (m.by === "content" ? "language detected from the file's content" : m.by === "filename" ? "language from the file name" : "");
  }

  // ------------------------------------------------------------------------------- diffs
  const diffCache = new Map();       // fingerprint -> answer (bounded)
  function fp(f) {
    let h = 2166136261;
    const s = f.path + "|" + JSON.stringify(f.hunks || []);
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return f.path + "#" + (h >>> 0).toString(36) + ":" + s.length;
  }

  function paintDiff(box, f, res) {
    const secs = box.querySelectorAll("section.hunk");
    secs.forEach((sec) => {
      const k = +sec.dataset.k;
      const rows = (res.hunks || [])[k];
      const h = (f.hunks || [])[k];
      if (!rows || !h) return;
      const trs = sec.querySelectorAll("tr");
      trs.forEach((tr, i) => {
        const td = tr.querySelector("td.tx");
        const line = h.lines[i];
        if (!td || !line || !rows[i]) return;
        const op = line[0] === " " ? " " : line[0];
        paint(td, [["", op], ...rows[i]]);
      });
    });
    box.dataset.hl = res.lang || "";
    if (res.plain_reason) box.title = res.plain_reason;
  }

  async function decorateDiff() {
    const box = typeof document.getElementById === "function" ? document.getElementById("diff") : null;
    const ui = MK.ui;
    const f = ui && ui.files ? ui.files[ui.sel] : null;
    if (!box || !f || f.binary || !(f.hunks || []).length || !box.querySelector("section.hunk")) return;
    const id = fp(f);
    let res = diffCache.get(id);
    if (!res) {
      res = await MK.api("/api/highlight/diff", { path: f.path, hunks: f.hunks }).catch(() => null);
      if (!res || res.error) return;
      diffCache.set(id, res);
      while (diffCache.size > 30) diffCache.delete(diffCache.keys().next().value);
    }
    const now = ui.files[ui.sel];
    if (now && fp(now) === id) paintDiff(MK.$("diff"), now, res);
  }

  // ------------------------------------------------------------------------------- snippets
  async function snippet(pre, lang) {
    const text = pre.textContent || "";
    const res = await MK.api("/api/highlight/snippet", { text, lang: lang || "" }).catch(() => null);
    if (!res || res.error || !pre.isConnected) return;
    const lines = [];
    (res.rows || []).forEach((r, i) => {
      if (i) lines.push(["", "\n"]);
      for (const s of r) lines.push(s);
    });
    if (lines.length) paint(pre, lines);
    if (res.lang) pre.dataset.lang = res.lang;
    if (res.plain_reason) pre.title = res.plain_reason;
  }

  MK.hl = { open, row, want, paint, snippet, chunks, CHUNK };

  // The diff view is app.js's; it is re-painted after each render, without editing app.js:
  // a MutationObserver on #diff's own children (renderFile replaces them).
  function watchDiff() {
    const box = typeof document.getElementById === "function" ? document.getElementById("diff") : null;
    if (!box || typeof MutationObserver === "undefined") return;
    new MutationObserver(() => { decorateDiff(); }).observe(box, { childList: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watchDiff);
  else watchDiff();
})();
