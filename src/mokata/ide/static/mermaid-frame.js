// mokata ide — the Mermaid layout frame (0.0.26 P7). Talks to its PARENT only (same origin, the
// page that framed it): {id, text, theme: {colours}} in, {id, svg} or {id, error} out. Mermaid runs at
// securityLevel "strict" (labels are sanitised, no click handlers, no HTML labels), so what comes
// back is a plain diagram; the page shows it as an image, which executes nothing.
"use strict";

(function () {
  const M = window.mermaid;
  let theme = null;
  let seq = 0;

  // Keys a diagram's own `%%{init: …}%%` directive or front-matter `config:` may NOT set (Mermaid
  // deletes them at any depth before applying a directive). Mermaid's defaults, plus everything
  // that writes into the diagram's CSS (a `url(...)` there would be a request) or turns labels
  // into HTML (P7 verification F1). The frame's CSP refuses any load anyway; this keeps the
  // diagram from even asking.
  const SECURE = ["secure", "securityLevel", "startOnLoad", "maxTextSize", "maxEdges",
    "suppressErrorRendering", "dompurifyConfig", "htmlLabels", "themeCSS", "themeVariables",
    "fontFamily", "altFontFamily", "fontSize", "messageFontFamily", "noteFontFamily",
    "actorFontFamily", "titleFontFamily", "look", "handDrawnSeed", "deterministicIds",
    "deterministicIDSeed", "elk", "layout"];

  // The page hands over its theme's colours (themes.css) as plain hex; anything else is refused and
  // the diagram falls back to Mermaid's own dark/light, so a value can never become CSS here.
  const HEX = /^#[0-9a-fA-F]{3,8}$/;
  function vars(t) {
    const k = ["background", "text", "line", "node", "border", "note", "accent"];
    if (!t || typeof t !== "object" || !k.every((n) => HEX.test(String(t[n] || "")))) return null;
    return { darkMode: !!t.dark, background: t.background, mainBkg: t.node, primaryColor: t.node,
      primaryTextColor: t.text, primaryBorderColor: t.border, secondaryColor: t.note, tertiaryColor: t.background,
      lineColor: t.line, textColor: t.text, nodeTextColor: t.text, titleColor: t.text,
      edgeLabelBackground: t.background, clusterBkg: t.background, clusterBorder: t.border,
      noteBkgColor: t.note, noteTextColor: t.text, noteBorderColor: t.border, actorBkg: t.node,
      actorBorder: t.border, actorTextColor: t.text, signalColor: t.line, signalTextColor: t.text,
      labelBoxBkgColor: t.node, labelBoxBorderColor: t.border, labelTextColor: t.text, loopTextColor: t.text };
  }
  function setup(t) {
    const key = JSON.stringify(t);
    if (key === theme) return;
    theme = key;
    const v = vars(t);
    M.initialize({
      startOnLoad: false, securityLevel: "strict",
      ...(v ? { theme: "base", themeVariables: v } : { theme: t && t.dark === false ? "default" : "dark" }),
      htmlLabels: false, flowchart: { htmlLabels: false }, deterministicIds: true,
      fontFamily: "ui-sans-serif, system-ui, sans-serif", secure: SECURE,
    });
  }

  window.addEventListener("message", async (ev) => {
    if (ev.source !== window.parent || ev.origin !== window.location.origin) return;
    const m = ev.data || {};
    if (typeof m.id !== "string" || typeof m.text !== "string") return;
    try {
      setup(m.theme);
      const out = await M.render("mk-m" + (++seq), m.text);
      window.parent.postMessage({ id: m.id, svg: out.svg }, window.location.origin);
    } catch (e) {
      window.parent.postMessage({ id: m.id, error: String((e && e.message) || e).slice(0, 500) },
                                window.location.origin);
    }
  });
  window.parent.postMessage({ ready: true }, window.location.origin);
})();
