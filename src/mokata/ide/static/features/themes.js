// mokata ide — the theme commands (0.0.27 P6, ID-THEMES-FULL). The palette's way to pick a theme:
// Ctrl/⌘+K, type ">theme", ↵ — no new chord, no new pane. The mechanism is static/theme.js.
"use strict";

(function () {
  const MK = window.MK;
  const T = MK.theme;
  const commands = T.CHOICES.map((c) => ({
    id: "theme." + c,
    title: "Theme: " + T.LABEL[c],
    run: () => {
      T.set(c);
      const shown = c === "system" ? `${T.LABEL[c]} (now ${T.LABEL[T.effective()]})` : T.LABEL[c];
      if (MK.toast) MK.toast("Theme: " + shown);
    },
  }));
  window.mokataIDE.register({ id: "themes", commands });
})();
