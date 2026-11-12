// mokata ide - a changed image, before against after (0.0.27 P9, ID-IMAGE-DIFF), inside the diff view.
//   A changed PNG / JPEG / GIF / WebP / SVG / ICO / BMP gets a panel above its diff with four modes:
//   Side by side, Swipe (a divider you drag: before on the left, after on the right), Onion skin (the
//   after image fading over the before), and Difference (every differing pixel in red, counted).
//   Both sides say their dimensions and byte sizes; an added or deleted image shows its one side,
//   labelled; a side too big to show says "too large to diff - N MB, open externally".
//   The Difference runs in a Worker (imagediff-worker.js, OffscreenCanvas) with true progress (rows
//   done of rows total) and Cancel - the page stays live, and a cancelled run shows NO result.
// SAFETY: an image is shown ONLY as an <img> whose src is /api/image/raw (served as an image with a
// CSP that allows nothing, nosniff, a sandbox). SVG is never put into the DOM as markup - this file
// has no markup-parsing sink and embeds nothing but <img> - so no script, handler, foreignObject
// or external href in a repo's SVG can run or load. Every label is textContent.
"use strict";

(function () {
  const MK = window.MK;
  if (!MK || typeof document === "undefined") return;
  const el = MK.el;
  const IMG = /\.(png|jpe?g|gif|webp|svg|ico|bmp)$/i;
  const MODES = [["side", "Side by side"], ["swipe", "Swipe"], ["onion", "Onion skin"], ["diff", "Difference"]];
  const TOLS = [[0, "exact"], [8, "within 8"], [32, "within 32"]];
  // what the reader chose survives a redraw (a refresh, the next image)
  const pref = { mode: "side", swipe: 50, onion: 50, actual: false, tol: 0 };
  const n = (x) => Number(x).toLocaleString();

  function fmtBytes(b) {
    if (b < 1024) return b + " B";
    let x = b, u = "B";
    for (const next of ["KB", "MB", "GB"]) { x /= 1024; u = next; if (x < 1024) break; }
    return x.toFixed(1) + " " + u;
  }
  function btn(text, cls, title) {
    const b = el("button", cls || "btn ghost small", text);
    b.type = "button";
    if (title) b.title = title;
    return b;
  }
  function sideRevs() {
    const v = MK.viewing ? MK.viewing() : { kind: "live" };
    if (v.kind === "live") return { base: "HEAD", head: "", label: "HEAD → working tree" };
    if (v.kind === "compare") {
      const b = MK.$("base") && MK.$("base").value, h = MK.$("head") && MK.$("head").value;
      if (b && h) return { base: b, head: h, label: `${b} → ${h}` };
    }
    return null;
  }
  const rawUrl = (path, rev) => "/api/image/raw?" + new URLSearchParams(rev ? { path, rev } : { path });

  // ============================================================================ one side
  // -> { fig, img }  img is null when the side has nothing to draw (the caption says why)
  function figure(side, which, name, rev, ctx) {
    const fig = el("figure", "imd-fig imd-" + which);
    const cap = el("figcaption", "imd-cap small");
    const label = which === "old" ? "Before" : "After";
    const where = side.rev || rev || "working tree";
    let img = null;
    if (side.state === "ok") {
      img = document.createElement("img");
      img.className = "imd-img";
      img.alt = `${label.toLowerCase()} version of ${name}`;
      img.decoding = "async";
      img.draggable = false;
      img.src = rawUrl(name, rev);
      const dim = el("span", "imd-dim", side.width ? `${side.width}×${side.height}` : "size set by the image");
      img.addEventListener("load", () => {
        if (ctx.dead()) return;
        const px = img.naturalWidth * img.naturalHeight;
        if (px > ctx.info.limits.view_pixels) {      // an SVG whose size the header did not give
          img.remove();
          fig.append(el("div", "imd-note", `too large to diff — ${img.naturalWidth}×${img.naturalHeight}, open externally`));
          ctx.bad(which);
          return;
        }
        if (!side.width) dim.textContent = `${img.naturalWidth}×${img.naturalHeight}`;
      });
      img.addEventListener("error", () => {
        if (ctx.dead()) return;
        img.remove();
        fig.append(el("div", "imd-note", `this ${label.toLowerCase()} version could not be loaded as an image`));
        ctx.bad(which);
      });
      cap.append(el("b", "", label), " · ", el("span", "dim2", where), " · ", dim, " · ", el("span", "", fmtBytes(side.size)));
      fig.append(el("div", "imd-frame"), cap);
      fig.firstChild.append(img);
    } else {
      cap.append(el("b", "", label), " · ", el("span", "dim2", where));
      const why = side.state === "missing"
        ? (which === "old" ? "Added — there is no earlier version" : "Deleted — there is no later version")
        : side.note;
      fig.append(el("div", "imd-note" + (side.state === "too_large" ? " imd-big" : ""), why), cap);
      if (side.state === "too_large") fig.append(el("code", "imd-path small", name));
    }
    return { fig, img, frame: img ? fig.firstChild : null };
  }

  // ============================================================================ the panel
  function build(f, revs, info) {
    const panel = el("section", "imd");
    panel.setAttribute("aria-label", "image diff of " + f.path);
    if (pref.mode === "diff") pref.mode = "side";    // a pixel run is always asked for, never started by moving to the next image
    const gone = { v: false };
    const ctx = { info, dead: () => gone.v || !panel.isConnected, bad: (which) => { ctx.badSide[which] = true; syncBar(); }, badSide: {} };
    const oldName = info.old_path || f.old_path || f.path, newName = f.path;
    const oldRev = info.base || revs.base, newRev = info.head || "";
    const A = figure(info.old, "old", oldName, oldRev, ctx), B = figure(info.new, "new", newName, newRev, ctx);
    const bothOk = () => A.img && B.img && !ctx.badSide.old && !ctx.badSide.new;
    const diffNote = info.diff && !info.diff.ok ? info.diff.note : "";

    const bar = el("div", "imd-bar");
    const tools = el("div", "dv-tools");
    tools.setAttribute("role", "group");
    tools.setAttribute("aria-label", "image diff mode");
    const buttons = {};
    for (const [id, text] of MODES) {
      const b = btn(text, "seg");
      b.dataset.mode = id;
      b.onclick = () => { if (!b.disabled) { pref.mode = id; render(); } };
      buttons[id] = b;
      tools.append(b);
    }
    const actual = btn("Actual size", "btn ghost small imd-actual");
    actual.setAttribute("aria-pressed", String(pref.actual));
    actual.title = "show pixels 1:1 instead of fitting the panel";
    actual.onclick = () => { pref.actual = !pref.actual; actual.setAttribute("aria-pressed", String(pref.actual)); stage.classList.toggle("imd-1to1", pref.actual); };
    bar.append(tools, actual, el("span", "dim small", revs.label));

    const meta = el("p", "imd-meta small dim2");
    const o = info.old, w = info.new;
    if (o.state === "ok" && w.state === "ok") {
      const d = w.size - o.size;
      const dims = o.width && w.width ? (o.width === w.width && o.height === w.height ? `${w.width}×${w.height}` : `${o.width}×${o.height} → ${w.width}×${w.height}`) : "";
      meta.textContent = [dims, `${fmtBytes(o.size)} → ${fmtBytes(w.size)} (${d >= 0 ? "+" : "−"}${fmtBytes(Math.abs(d))})`].filter(Boolean).join(" · ");
    } else meta.textContent = info.status === "added" ? "Added image" : info.status === "deleted" ? "Deleted image" : "";

    const stage = el("div", "imd-stage" + (pref.actual ? " imd-1to1" : ""));
    const live = el("div", "imd-live small");
    live.setAttribute("role", "status");
    live.setAttribute("aria-live", "polite");
    let job = null;

    function available(id) {
      if (id === "side") return true;
      if (!bothOk()) return false;
      return id !== "diff" || !diffNote;
    }
    function syncBar() {
      for (const [id] of MODES) {
        const b = buttons[id], ok = available(id);
        b.disabled = !ok;
        b.title = ok ? "" : id !== "side" && !bothOk() ? "needs both versions shown as images" : diffNote || "not available";
        b.setAttribute("aria-pressed", String(effective() === id));
      }
    }
    const effective = () => (available(pref.mode) ? pref.mode : "side");

    // a run is `job` from the moment it is asked for (while the images are being decoded too), so
    // Cancel works at every point, and a run stopped before its Worker exists never starts one
    function stop() {
      if (job) { job.cancelled = true; if (job.worker) job.worker.terminate(); job = null; }
    }
    // hiding a strip whose Cancel had the focus must not drop the focus on <body>
    function hideStrip(strip) {
      const had = strip.contains(document.activeElement);
      strip.hidden = true;
      if (had) buttons.diff.focus();
    }
    function slider(label, key, apply) {
      const row = el("label", "imd-slider small");
      const r = document.createElement("input");
      r.type = "range"; r.min = "0"; r.max = "100"; r.value = String(pref[key]);
      r.setAttribute("aria-label", label);
      r.oninput = () => { pref[key] = +r.value; apply(+r.value); };
      row.append(el("span", "dim2", label), r);
      return { row, r };
    }
    // swipe and onion lay the SAME two <img> elements over each other (moved, never re-fetched)
    function stack() {
      const s = el("div", "imd-stack");
      s.append(A.img, B.img);
      return { s, under: A.img, over: B.img };
    }

    function render(focusTol) {
      stop();
      live.textContent = "";
      stage.replaceChildren();
      const mode = effective();
      syncBar();
      for (const [S, k] of [[A, "old"], [B, "new"]]) {
        if (S.img && !ctx.badSide[k]) { S.img.style.clipPath = ""; S.img.style.opacity = ""; S.frame.append(S.img); }
      }
      if (mode === "side") {
        const grid = el("div", "imd-side" + (info.old.state === "missing" || info.new.state === "missing" ? " imd-one" : ""));
        grid.append(A.fig, B.fig);
        stage.append(grid);
        return;
      }
      if (mode === "swipe" || mode === "onion") {
        const { s, over } = stack();
        const frame = el("div", "imd-frame");
        frame.append(s);
        const apply = mode === "swipe"
          ? (v) => { over.style.clipPath = `inset(0 0 0 ${v}%)`; over.style.opacity = ""; }
          : (v) => { over.style.opacity = String(v / 100); over.style.clipPath = ""; };
        const sl = slider(mode === "swipe" ? "Swipe: before on the left, after on the right" : "Onion skin: before ← → after",
                          mode, apply);
        apply(pref[mode]);
        stage.append(sl.row, frame);
        return;
      }
      renderDiff(focusTol);
    }

    // ------------------------------------------------------------------ pixel difference
    function renderDiff(focusTol) {
      const tolRow = el("label", "imd-slider small");
      const sel = document.createElement("select");
      sel.setAttribute("aria-label", "how close counts as the same");
      for (const [v, t] of TOLS) sel.append(new Option(t, String(v)));
      sel.value = String(pref.tol);
      sel.onchange = () => { pref.tol = +sel.value; render(true); };
      tolRow.append(el("span", "dim2", "Same when each channel is"), sel);
      const strip = el("div", "imd-strip small");
      const prog = document.createElement("progress");
      const ptxt = el("span", "imd-ptxt");
      const cancel = btn("Cancel", "btn ghost small", "stop comparing; nothing is shown");
      strip.append(prog, ptxt, cancel);
      const result = el("div", "imd-result");
      stage.append(tolRow, strip, result);
      if (focusTol) sel.focus();
      const px = Math.max(A.img.naturalWidth, B.img.naturalWidth) * Math.max(A.img.naturalHeight, B.img.naturalHeight);
      if (px > info.limits.diff_pixels) {
        strip.hidden = true;
        result.append(el("div", "imd-note", `pixel difference decodes both images: ${(px / 1e6).toFixed(1)} MP is over the ${Math.round(info.limits.diff_pixels / 1e6)} MP budget. Side by side, swipe and onion skin still work`));
        return;
      }
      // 0.0.28 D: an SVG is never compared pixel by pixel, in ANY browser. What an SVG draws is the
      // browser's rendering of it (fonts, embedded HTML, references one browser refuses to load and
      // another draws blank), so a pixel count would describe this browser, not the file — and one
      // browser refusing to hand over the pixels (Chromium) while another compares (Firefox) made
      // the same file say two different things. Its source is compared in the line diff below.
      if (info.old.kind === "svg" || info.new.kind === "svg") {
        strip.hidden = true;
        result.append(el("div", "imd-note", "this image cannot be compared pixel by pixel: an SVG is drawn by the browser, so its pixels describe the browser, not the file (its source is compared in the line diff below). Side by side, swipe and onion skin still work"));
        return;
      }
      if (!window.Worker || typeof OffscreenCanvas === "undefined" || typeof createImageBitmap !== "function") {
        strip.hidden = true;
        result.append(el("div", "imd-note", "this browser cannot compare pixels off the page (no Worker + OffscreenCanvas); side by side, swipe and onion skin still work"));
        return;
      }
      ptxt.textContent = "preparing both images…";
      prog.removeAttribute("value");
      start(strip, prog, ptxt, cancel, result);
    }
    async function start(strip, prog, ptxt, cancel, result) {
      const mine = { cancelled: false, worker: null };
      job = mine;
      let rows = [0, 0];
      cancel.onclick = () => {
        if (job !== mine) return;
        stop();
        hideStrip(strip);
        live.textContent = rows[1] ? `Cancelled at row ${n(rows[0])} of ${n(rows[1])} — no result is shown.`
          : "Cancelled before the comparison began — no result is shown.";
        result.replaceChildren(el("div", "imd-note", live.textContent));
      };
      let a = null, b = null;
      try {
        await Promise.all([A.img.decode(), B.img.decode()]);
        [a, b] = await Promise.all([createImageBitmap(A.img), createImageBitmap(B.img)]);
      } catch (e) {
        if (!mine.cancelled && !ctx.dead()) { job = null; hideStrip(strip); result.append(el("div", "imd-note", "could not decode both images to compare them")); }
        return;
      }
      if (mine.cancelled || ctx.dead()) { a.close(); b.close(); return; }
      const worker = new Worker("/static/imagediff-worker.js");
      mine.worker = worker;
      worker.onmessage = (ev) => {
        const m = ev.data || {};
        if (job !== mine || ctx.dead()) return;
        if (m.type === "progress") {
          rows = [m.done, m.total];
          prog.max = m.total; prog.value = m.done;
          ptxt.textContent = `comparing rows · ${n(m.done)} of ${n(m.total)}`;
        } else if (m.type === "done") {
          job = null; worker.terminate(); hideStrip(strip);
          const cv = document.createElement("canvas");
          cv.className = "imd-canvas";
          cv.width = m.w; cv.height = m.h;
          cv.getContext("bitmaprenderer").transferFromImageBitmap(m.bitmap);
          const pct = m.total ? (100 * m.differing / m.total) : 0;
          const says = m.differing === 0
            ? `No pixel differs (${n(m.total)} compared${pref.tol ? `, within ${pref.tol}` : ""}).`
            : `${n(m.differing)} of ${n(m.total)} pixels differ (${pct < 0.01 ? "<0.01" : pct.toFixed(2)}%)${m.size_differs ? " — the images differ in size, and pixels outside the smaller one count as different" : ""}.`;
          cv.setAttribute("role", "img");
          cv.setAttribute("aria-label", "pixel difference: " + says);
          live.textContent = says;
          const frame = el("div", "imd-frame");
          frame.append(cv);
          result.replaceChildren(el("p", "imd-sum" + (m.differing ? " imd-diff" : " imd-same"), says), frame);
        } else if (m.type === "error") {
          job = null; worker.terminate(); hideStrip(strip);
          result.replaceChildren(el("div", "imd-note", "the comparison failed: " + String(m.note || "").slice(0, 200)));
        }
      };
      worker.onerror = () => {
        if (job !== mine) return;
        job = null; worker.terminate(); hideStrip(strip);
        result.replaceChildren(el("div", "imd-note", "the comparison could not run in this browser"));
      };
      try {
        worker.postMessage({ a, b, tol: pref.tol }, [a, b]);
      } catch (e) {
        // the browser refuses to hand over pixels it marks as not safe to read (an SVG with a
        // foreignObject or an external reference): said, and nothing is compared
        job = null; worker.terminate(); hideStrip(strip);
        result.replaceChildren(el("div", "imd-note", "this image cannot be compared pixel by pixel: the browser will not let the page read its pixels (an SVG can embed content from elsewhere). Side by side, swipe and onion skin still work"));
      }
    }

    panel.append(bar, meta, stage, live);
    panel._stop = () => { gone.v = true; stop(); };
    panel._mode = (id) => { if (!buttons[id].disabled) { pref.mode = id; render(); buttons[id].focus(); } };
    // the stage is drawn once the sides that have an image have one (so a mode can read its size)
    const waits = [A.img, B.img].filter(Boolean).map((i) => (i.complete ? Promise.resolve() : new Promise((r) => { i.addEventListener("load", r, { once: true }); i.addEventListener("error", r, { once: true }); })));
    // a mode that reads the images' sizes (swipe, onion, difference) is drawn once they have loaded
    if (effective() === "side" || !waits.length) render(); else stage.append(el("p", "dim small", "loading the images…"));
    Promise.all(waits).then(() => { if (!ctx.dead()) { syncBar(); if (pref.mode !== "side") render(); } });
    return panel;
  }

  // ============================================================================ in the diff view
  let token = 0, queued = false;
  // The page's ONE in-flight info fetch. Leaving the image it was asked for (another file, a view
  // change) ABORTS it: the browser drops the request and the server's watcher kills the git read,
  // instead of a slow answer running to its own end for a screen nobody is on.
  let infoCtl = null;
  function cancelInfo() { const c = infoCtl; infoCtl = null; if (c) c.abort(); }
  function decorate() {
    queued = false;
    const diff = MK.$("diff");
    const f = MK.ui && MK.ui.files ? MK.ui.files[MK.ui.sel] : null;
    if (!diff || !f || !IMG.test(f.path)) { cancelInfo(); return; }
    const body = diff.querySelector(".file-body");
    if (!body) { cancelInfo(); return; }
    if (body.querySelector(".imd, .imd-pending")) return;
    const revs = sideRevs();
    const bin = body.querySelector(":scope > .bin");
    const pending = el("section", "imd-pending small dim2", revs ? "loading the image diff…" : "an image diff needs Uncommitted changes or Compare branches");
    body.prepend(pending);
    if (!revs) return;
    if (bin) bin.hidden = true;
    const mine = ++token;
    cancelInfo();
    const ctl = typeof AbortController === "function" ? new AbortController() : null;
    infoCtl = ctl;
    const q = new URLSearchParams({ path: f.path, base: revs.base });
    if (revs.head) q.set("head", revs.head);
    if (f.old_path) q.set("old_path", f.old_path);
    MK.api("/api/image/info?" + q.toString(), undefined, ctl ? { signal: ctl.signal } : undefined).then((info) => {
      if (infoCtl === ctl) infoCtl = null;
      if (mine !== token || !pending.isConnected) return;
      if (!info || info.error || info.state !== "ok") {
        pending.textContent = (info && (info.note || info.error)) || "could not read the image";
        if (bin) bin.hidden = false;
        return;
      }
      const panel = build(f, revs, info);
      pending.replaceWith(panel);
    }).catch(() => {
      if (infoCtl === ctl) infoCtl = null;
      if (mine !== token || (ctl && ctl.signal.aborted) || !pending.isConnected) return;   // switched away: say nothing
      pending.textContent = "could not read the image";
      if (bin) bin.hidden = false;
    });
  }
  function schedule() { if (!queued) { queued = true; requestAnimationFrame(decorate); } }
  function mount() {
    const diff = MK.$ && MK.$("diff");
    if (!diff) return;
    new MutationObserver((muts) => {
      // a removed panel stops its worker (a redraw of the diff must not leave one running)
      for (const m of muts) for (const nd of m.removedNodes) {
        if (nd.nodeType === 1) for (const p of (nd.matches && nd.matches(".imd") ? [nd] : nd.querySelectorAll ? nd.querySelectorAll(".imd") : [])) if (p._stop) p._stop();
      }
      schedule();
    }).observe(diff, { childList: true, subtree: true });
    if (MK.onViewing) MK.onViewing.push(() => {
      token++;
      cancelInfo();
      const d = MK.$("diff");
      if (d) for (const x of d.querySelectorAll(".imd, .imd-pending")) x.remove();
      schedule();
    });
  }
  const command = (id) => () => { const p = document.querySelector("#diff .imd"); if (p && p._mode) p._mode(id); };
  window.mokataIDE.register({ id: "imagediff", commands: MODES.map(([id, text]) => (
    { id: "imagediff." + id, title: "Image diff: " + text.toLowerCase(), run: command(id) })) });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();
})();
