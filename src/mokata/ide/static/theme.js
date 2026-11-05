// mokata ide — the theme (0.0.27 P6, ID-THEMES-FULL). Loaded in <head>, before the first paint, so
// a "follow the system" window never flashes the wrong colours. It owns ONE thing: which theme is
// in force, written as <html data-theme="dark|light|hc-dark|hc-light"> (+ data-scheme="dark|light"
// for the two logos). The colours themselves are static/themes.css; nothing here holds a colour.
//
//   MK.theme.choice()     what the person picked: system | dark | light | hc-dark | hc-light
//   MK.theme.effective()  the theme in force (system resolved against the OS, live)
//   MK.theme.set(c)       pick one — no reload, no re-render of the page; saved with the workspace
//   MK.theme.onChange(f)  f(effective, choice) when either changes (the graph, Mermaid, the terminal
//                         read their colours again; everything else is plain CSS and follows by itself)
//
// "System" follows prefers-color-scheme AND prefers-contrast: more, and re-resolves the moment the
// OS flips either one. The choice is stored in the SERVER's workspace (tabs.js carries it in the
// same save as the tabs; ide/workspace.py THEMES is the same list).
"use strict";

(function (root) {
  const MK = (root.MK = root.MK || {});
  const CHOICES = ["system", "dark", "light", "hc-dark", "hc-light"];
  const LABEL = { system: "Follow the system", dark: "Dark", light: "Light",
                  "hc-dark": "High contrast (dark)", "hc-light": "High contrast (light)" };
  const doc = root.document;

  function resolve(choice, env) {
    if (choice === "dark" || choice === "light" || choice === "hc-dark" || choice === "hc-light") return choice;
    const light = !!(env && env.light), more = !!(env && env.more);
    return more ? (light ? "hc-light" : "hc-dark") : (light ? "light" : "dark");
  }

  const query = (q) => (root.matchMedia ? root.matchMedia(q) : null);
  const mqLight = query("(prefers-color-scheme: light)");
  const mqMore = query("(prefers-contrast: more)");
  const env = () => ({ light: !!(mqLight && mqLight.matches), more: !!(mqMore && mqMore.matches) });

  let choice = "system";
  let userSet = false;               // the person picked: a later workspace load must not undo it
  let effective = resolve(choice, env());
  const listeners = [];

  function paint() {
    if (!doc || !doc.documentElement) return;
    const h = doc.documentElement;
    h.dataset.theme = effective;
    h.dataset.scheme = effective.endsWith("light") ? "light" : "dark";
    h.dataset.themeChoice = choice;
  }
  function emit() {
    for (const f of listeners.slice()) { try { f(effective, choice); } catch (e) { /* a view's own repaint must not stop the rest */ } }
    if (doc && root.CustomEvent) doc.dispatchEvent(new root.CustomEvent("mokata:theme", { detail: { effective, choice } }));
  }
  function apply(changedChoice) {
    const next = resolve(choice, env());
    const moved = next !== effective;
    effective = next;
    paint();
    if (moved || changedChoice) emit();
  }
  for (const mq of [mqLight, mqMore]) {
    if (mq && mq.addEventListener) mq.addEventListener("change", () => { if (choice === "system") apply(false); });
  }

  MK.theme = {
    CHOICES, LABEL, resolve,
    choice: () => choice,
    effective: () => effective,
    // `fromServer`: the saved workspace speaking — it never overrides what the person already picked.
    set(c, opts) {
      if (CHOICES.indexOf(c) < 0) return false;
      const fromServer = !!(opts && opts.fromServer);
      if (fromServer && userSet) return false;
      if (!fromServer) userSet = true;
      const changed = c !== choice;
      choice = c;
      apply(changed);
      if (!fromServer && changed && MK.saveView) MK.saveView();
      return true;
    },
    onChange(f) { listeners.push(f); },
  };
  paint();
})(typeof window !== "undefined" ? window : globalThis);
