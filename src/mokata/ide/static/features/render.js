// mokata ide — the Markdown + Mermaid renderer (0.0.26 P7, ID-REVIEW-CONTEXT). One interface,
// shared by the review view, the Code view and (P46) the change explainer:
//
//   MK.render.markdown(host, text, {basePath})        render `text` into `host`, lazily
//   MK.render.diff(host, oldText, newText, {basePath}) old | new rendered side by side, each
//                                                       top-level block marked same/add/del/chg
//   MK.render.sanitize(html) -> DocumentFragment       the allowlist pass, for a caller with HTML
//   MK.render.ready() -> Promise                        the vendored markdown-it, loaded once
//   both renders return {blocks, destroy()}
//
// SAFETY — three walls, each enough alone for a <script> in a .md file or a diff to stay inert:
//   1. markdown-it runs with `html: false`: raw HTML in Markdown is TEXT, never markup;
//   2. its output is parsed in an inert DOMParser document and rebuilt node by node from an
//      ALLOWLIST (tags, attributes, URL schemes) — no markup string is ever assigned into the
//      page; style attributes become classes;
//   3. the page's CSP (script-src 'self', no inline) refuses any script or handler that got past.
// Mermaid runs in a hidden same-origin frame with its own CSP (server.FRAME_CSP), at
// securityLevel "strict", and its SVG comes back as an <img> — an image document runs nothing.
// Libraries are VENDORED (/static/vendor/, pinned in VERSIONS.json); nothing loads from a CDN.
// SCALE: a document is split into top-level blocks; blocks are rendered in chunks as they near
// the viewport, and each diagram only when it is about to be seen. No size cap.
"use strict";

(function () {
  const MK = window.MK;
  const R = (MK.render = MK.render || {});
  const CHUNK = 40;                    // top-level blocks per lazily rendered chunk
  let md = null, loading = null;

  function ready() {
    if (md) return Promise.resolve(md);
    if (loading) return loading;
    loading = new Promise((ok, bad) => {
      const s = document.createElement("script");
      s.src = "/static/vendor/markdown-it.min.js";
      s.onload = () => {
        md = window.markdownit({ html: false, linkify: true, typographer: false });
        const fence = md.renderer.rules.fence;
        md.renderer.rules.fence = (tokens, idx, options, env, self) => {
          const t = tokens[idx];
          if ((t.info || "").trim().split(/\s+/)[0].toLowerCase() === "mermaid") {
            env.mermaid = env.mermaid || [];
            env.mermaid.push(t.content);
            return `<div class="mk-mermaid" data-m="${env.mermaid.length - 1}"></div>\n`;
          }
          return fence(tokens, idx, options, env, self);
        };
        ok(md);
      };
      s.onerror = () => { loading = null; bad(new Error("could not load the Markdown renderer")); };
      document.head.append(s);
    });
    return loading;
  }
  R.ready = ready;

  // ------------------------------------------------------------------------ the allowlist
  const TAGS = new Set(["P", "H1", "H2", "H3", "H4", "H5", "H6", "UL", "OL", "LI", "BLOCKQUOTE",
    "PRE", "CODE", "EM", "STRONG", "S", "DEL", "A", "IMG", "TABLE", "THEAD", "TBODY", "TR", "TH",
    "TD", "HR", "BR", "SUP", "SUB", "DIV"]);
  const DROP = new Set(["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "TEMPLATE", "NOSCRIPT",
    "SVG", "MATH", "FORM", "INPUT", "TEXTAREA", "BUTTON", "SELECT", "LINK", "META", "BASE"]);
  const IMG_OK = /^data:image\/(png|gif|jpeg|webp);base64,[a-z0-9+/=\s]+$/i;

  function safeHref(href) {
    const h = (href || "").trim();
    if (/^(https?:|mailto:)/i.test(h)) return { kind: "external", href: h };
    if (h.startsWith("#")) return { kind: "anchor", href: h };
    if (!h || /^[a-z][a-z0-9+.-]*:/i.test(h) || h.startsWith("//")) return null;  // any other scheme
    return { kind: "repo", href: h };
  }

  function resolve(base, rel) {
    const parts = (base || "").split("/").slice(0, -1);
    for (const p of rel.split("#")[0].split("/")) {
      if (!p || p === ".") continue;
      if (p === "..") parts.pop(); else parts.push(p);
    }
    return parts.join("/");
  }

  function clean(src, out, opts) {
    for (const n of src.childNodes) {
      if (n.nodeType === 3) { out.append(document.createTextNode(n.nodeValue)); continue; }
      if (n.nodeType !== 1) continue;
      const tag = n.tagName;
      if (DROP.has(tag)) continue;
      if (!TAGS.has(tag)) { clean(n, out, opts); continue; }       // unknown wrapper: keep text
      if (tag === "DIV") {
        if (n.className === "mk-mermaid" && /^\d+$/.test(n.getAttribute("data-m") || "")) {
          const d = document.createElement("div");
          d.className = "mk-mermaid";
          d.dataset.m = n.getAttribute("data-m");
          out.append(d);
        } else clean(n, out, opts);
        continue;
      }
      if (tag === "IMG") {
        const s = n.getAttribute("src") || "", alt = n.getAttribute("alt") || "";
        if (IMG_OK.test(s)) {
          const i = document.createElement("img");
          i.src = s; i.alt = alt;
          out.append(i);
        } else {
          out.append(Object.assign(document.createElement("span"), {
            className: "mk-img-off", textContent: `[image: ${alt || "untitled"} — not loaded]` }));
        }
        continue;
      }
      const e = document.createElement(tag);
      if (tag === "A") {
        const h = safeHref(n.getAttribute("href"));
        if (h && h.kind === "external") { e.href = h.href; e.target = "_blank"; e.rel = "noopener noreferrer"; }
        else if (h && h.kind === "anchor") e.href = h.href;
        else if (h && h.kind === "repo") {
          const path = resolve(opts.basePath || "", h.href);
          e.href = "#"; e.dataset.path = path; e.title = path;
          e.addEventListener("click", (ev) => { ev.preventDefault(); if (MK.openFile) MK.openFile(path, 1, null); });
        }
      } else if (tag === "OL" && /^\d{1,9}$/.test(n.getAttribute("start") || "")) {
        e.start = +n.getAttribute("start");
      } else if (tag === "CODE" && /^language-[\w+#.-]{1,40}$/.test(n.className || "")) {
        e.className = n.className;
      } else if (tag === "TH" || tag === "TD") {
        const m = /text-align:\s*(left|center|right)/.exec(n.getAttribute("style") || "");
        if (m) e.className = "mk-al-" + m[1];
      }
      clean(n, e, opts);
      out.append(e);
    }
    return out;
  }

  function sanitize(html, opts) {
    // A DOMParser document has no browsing context: parsing runs no script and loads nothing.
    const doc = new DOMParser().parseFromString("<!doctype html><body>" + html, "text/html");
    return clean(doc.body, document.createDocumentFragment(), opts || {});
  }
  R.sanitize = sanitize;

  // ------------------------------------------------------------------------ Mermaid (lazy)
  const MM = { frame: null, ready: null, waiting: new Map(), seq: 0 };
  // P6: the diagram is drawn in the page's own theme tokens (themes.css), read now — the frame
  // only ever receives plain hex colours, and a theme switch redraws the diagrams already shown.
  function theme() {
    const cs = getComputedStyle(document.documentElement);
    const g = (n) => cs.getPropertyValue(n).trim();
    return { dark: document.documentElement.dataset.scheme !== "light", background: g("--panel"), text: g("--text"),
      line: g("--text2"), node: g("--panel2"), border: g("--line2"), note: g("--sel"), accent: g("--accent") };
  }
  function frame() {
    if (MM.ready) return MM.ready;
    MM.ready = new Promise((ok) => {
      window.addEventListener("message", (ev) => {
        if (!MM.frame || ev.source !== MM.frame.contentWindow || ev.origin !== window.location.origin) return;
        const m = ev.data || {};
        if (m.ready) { ok(); return; }
        const w = MM.waiting.get(m.id);
        if (!w) return;
        MM.waiting.delete(m.id);
        if (typeof m.svg === "string") w.ok(m.svg); else w.bad(new Error(m.error || "diagram failed"));
      });
      const f = document.createElement("iframe");
      f.className = "mk-mermaid-frame";
      f.setAttribute("aria-hidden", "true");
      f.tabIndex = -1;
      f.title = "diagram layout";
      f.setAttribute("sandbox", "allow-scripts allow-same-origin");
      f.src = "/static/mermaid-frame.html";
      MM.frame = f;
      document.body.append(f);
    });
    return MM.ready;
  }
  function diagram(text) {
    return frame().then(() => new Promise((ok, bad) => {
      const id = "d" + (++MM.seq);
      MM.waiting.set(id, { ok, bad });
      MM.frame.contentWindow.postMessage({ id, text, theme: theme() }, window.location.origin);
    }));
  }
  R.diagram = diagram;

  let seen = null;                       // made on first use (a page without the API: draw now)
  function whenSeen(node, fn) {
    if (typeof IntersectionObserver === "undefined") { fn(); return; }
    if (!seen) seen = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        seen.unobserve(e.target);
        const f = e.target._mkShow;
        if (f) { e.target._mkShow = null; f(); }
      }
    }, { rootMargin: "800px 0px" });
    node._mkShow = fn;
    seen.observe(node);
  }

  // A theme switch: every diagram on screen is laid out again in the new colours (same node, same
  // place in the page — only the picture changes; one that is still drawing picks the theme up itself).
  MK.theme.onChange(() => {
    for (const d of document.querySelectorAll('.mk-mermaid[data-state="drawn"]')) {
      const src = d._mkSrc, img = d.querySelector("img.mk-m-img");
      if (typeof src !== "string" || !img) continue;
      diagram(src).then((svg) => { img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg); }, () => {});
    }
  });

  function paintDiagrams(root, sources) {
    for (const d of root.querySelectorAll(".mk-mermaid")) {
      const src = sources[+d.dataset.m];
      if (typeof src !== "string") continue;
      d.append(Object.assign(document.createElement("span"), { className: "mk-m-wait", textContent: "drawing diagram…" }));
      whenSeen(d, () => diagram(src).then((svg) => {
        const img = document.createElement("img");
        img.className = "mk-m-img";
        img.alt = "diagram: " + src.split("\n")[0].slice(0, 120);
        img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
        d.replaceChildren(img);
        d._mkSrc = src;
        d.dataset.state = "drawn";
      }, (err) => {
        const pre = document.createElement("pre");
        pre.textContent = src;
        d.replaceChildren(Object.assign(document.createElement("p"), {
          className: "mk-m-err", textContent: "diagram could not be drawn — " + err.message }), pre);
        d.dataset.state = "error";
      }));
    }
  }

  // ------------------------------------------------------------------------ blocks
  // A document's top-level blocks: [{a, b, src}] — token slice [a, b) and the source lines it
  // came from (what a diff compares).
  function blocks(tokens, lines) {
    const out = [];
    let start = -1;
    for (let i = 0; i < tokens.length; i++) {
      const t = tokens[i];
      if (t.level !== 0) continue;
      if (t.nesting === 1) { start = i; continue; }
      const a = t.nesting === 0 ? i : start;
      const open = tokens[a];
      const map = open.map || [0, 0];
      out.push({ a, b: i + 1, src: lines.slice(map[0], map[1]).join("\n") });
      start = -1;
    }
    return out;
  }

  function renderBlocks(mdi, tokens, list, env, opts) {
    const frag = document.createDocumentFragment();
    for (const b of list) {
      const box = document.createElement("div");
      box.className = "mk-block";
      box.append(sanitize(mdi.renderer.render(tokens.slice(b.a, b.b), mdi.options, env), opts));
      frag.append(box);
    }
    return frag;
  }

  function parse(mdi, text) {
    const env = {};
    const src = String(text || "").replace(/\r\n?/g, "\n");
    const tokens = mdi.parse(src, env);
    return { env, tokens, list: blocks(tokens, src.split("\n")) };
  }

  // Render `list` into `host` in chunks; the first chunk now, the rest as each nears the viewport.
  function lazy(host, count, paint) {
    for (let k = 0; k < count; k += CHUNK) {
      const chunk = document.createElement("div");
      chunk.className = "mk-chunk";
      chunk.dataset.from = String(k);
      host.append(chunk);
      const fill = () => { chunk.classList.add("mk-filled"); paint(chunk, k, Math.min(count, k + CHUNK)); };
      if (k === 0) fill(); else { chunk.classList.add("mk-pending"); whenSeen(chunk, () => { chunk.classList.remove("mk-pending"); fill(); }); }
    }
  }

  R.markdown = function markdown(host, text, opts) {
    const o = opts || {};
    host.replaceChildren();
    host.classList.add("mk-doc");
    const handle = { blocks: 0, destroy() { host.replaceChildren(); } };
    handle.done = ready().then((mdi) => {
      const p = parse(mdi, text);
      handle.blocks = p.list.length;
      if (!p.list.length) { host.append(Object.assign(document.createElement("p"), { className: "dim", textContent: "This document is empty." })); return handle; }
      lazy(host, p.list.length, (chunk, a, b) => {
        chunk.append(renderBlocks(mdi, p.tokens, p.list.slice(a, b), p.env, o));
        paintDiagrams(chunk, p.env.mermaid || []);
      });
      return handle;
    }, (err) => { host.append(Object.assign(document.createElement("p"), { className: "warn", textContent: err.message })); return handle; });
    return handle;
  };

  // A block-level alignment of two documents: common head and tail, then an LCS over the middle
  // when it is small enough to align exactly; past that, the middle is shown as one change (every
  // block still rendered, nothing dropped).
  const ALIGN_CELLS = 4e6;
  function align(a, b) {
    let s = 0;
    while (s < a.length && s < b.length && a[s] === b[s]) s++;
    let e = 0;
    while (e < a.length - s && e < b.length - s && a[a.length - 1 - e] === b[b.length - 1 - e]) e++;
    const rows = [];
    for (let i = 0; i < s; i++) rows.push(["same", i, i]);
    const A = a.slice(s, a.length - e), B = b.slice(s, b.length - e);
    if (A.length * B.length <= ALIGN_CELLS) {
      const n = A.length, m = B.length;
      const L = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
      for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
        L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
      let i = 0, j = 0;
      while (i < n || j < m) {
        if (i < n && j < m && A[i] === B[j]) { rows.push(["same", s + i, s + j]); i++; j++; }
        // on a tie the removal comes first, so a replaced block pairs up as one change below
        else if (j < m && (i >= n || L[i][j + 1] > L[i + 1][j])) { rows.push(["add", null, s + j]); j++; }
        else { rows.push(["del", s + i, null]); i++; }
      }
    } else {
      for (let i = 0; i < A.length; i++) rows.push(["del", s + i, null]);
      for (let j = 0; j < B.length; j++) rows.push(["add", null, s + j]);
    }
    for (let i = 0; i < e; i++) rows.push(["same", a.length - e + i, b.length - e + i]);
    // a removal followed by an addition is one CHANGED block, shown side by side
    const out = [];
    for (let k = 0; k < rows.length; k++) {
      const r = rows[k];
      if (r[0] === "del" && rows[k + 1] && rows[k + 1][0] === "add") { out.push(["chg", r[1], rows[k + 1][2]]); k++; }
      else out.push(r);
    }
    return out;
  }
  R.align = align;

  R.diff = function diff(host, oldText, newText, opts) {
    const o = opts || {};
    host.replaceChildren();
    host.classList.add("mk-diff");
    const handle = { blocks: 0, changed: 0, destroy() { host.replaceChildren(); } };
    handle.done = ready().then((mdi) => {
      const pa = parse(mdi, oldText), pb = parse(mdi, newText);
      const rows = align(pa.list.map((x) => x.src), pb.list.map((x) => x.src));
      handle.blocks = rows.length;
      handle.changed = rows.filter((r) => r[0] !== "same").length;
      const head = document.createElement("div");
      head.className = "mk-diff-head";
      head.append(Object.assign(document.createElement("span"), { textContent: o.oldLabel || "before" }),
                  Object.assign(document.createElement("span"), { textContent: o.newLabel || "after" }));
      host.append(head);
      if (!handle.changed) host.append(Object.assign(document.createElement("p"), { className: "dim mk-nochange", textContent: "The rendered documents are the same." }));
      lazy(host, rows.length, (chunk, a, b) => {
        for (const [kind, i, j] of rows.slice(a, b)) {
          const row = document.createElement("div");
          row.className = "mk-row mk-" + kind;
          const left = document.createElement("div"), right = document.createElement("div");
          left.className = "mk-side mk-old"; right.className = "mk-side mk-new";
          if (i !== null) left.append(renderBlocks(mdi, pa.tokens, [pa.list[i]], pa.env, o));
          if (j !== null) right.append(renderBlocks(mdi, pb.tokens, [pb.list[j]], pb.env, o));
          row.append(left, right);
          chunk.append(row);
        }
        // diagram ids are per document, so each side paints from its own sources
        for (const side of [["mk-old", pa], ["mk-new", pb]]) {
          for (const s of chunk.querySelectorAll("." + side[0])) paintDiagrams(s, side[1].env.mermaid || []);
        }
      });
      return handle;
    }, (err) => { host.append(Object.assign(document.createElement("p"), { className: "warn", textContent: err.message })); return handle; });
    return handle;
  };
})();
