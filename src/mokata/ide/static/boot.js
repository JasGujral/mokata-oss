// mokata ide — start-up: wire every view, then load. Kept last so each view's script has
// registered its hooks before the first refresh.
"use strict";

(function () {
  const MK = window.MK;
  const hooks = (name) => Object.keys(MK).filter((k) => k.startsWith(name) && k !== name)
    .map((k) => MK[k]).filter((f) => typeof f === "function");
  MK.onMode = (mode) => { for (const f of hooks("onMode")) f(mode); };
  MK.onRefresh = () => { for (const f of hooks("onRefresh")) f(); };
  for (const w of hooks("wire")) w();
  MK.setMode("review");
  MK.setPanel("changes", true);
  MK.api("/api/caps").then((c) => {
    if (!c || c.error) return;
    MK.$("st-graph").textContent = c.graph || "—";
    MK.$("st-graph").classList.toggle("degraded", !!c.graph_degraded);
    MK.$("st-matcher").textContent = c.matcher || "—";
    const extra = MK.$("st-extra");
    extra.replaceChildren();
    for (const [k, v] of Object.entries(c.more || {})) {
      const s = MK.el("span", "", `${k}: `); s.append(MK.el("b", "", v)); extra.append(s, " ");
    }
  }).catch(() => {});
  MK.refresh().then(() => {
    if (!(MK.openFromLink && MK.openFromLink())) MK.restoreView();   // ID.S3: an OSC-8 /open link
    MK.live();
  });
})();
