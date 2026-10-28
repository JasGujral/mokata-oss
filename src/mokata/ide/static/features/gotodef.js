// mokata ide — go to definition (0.0.26 P35, G12/R21). F12 asks the graph for the definition of
// the name under the caret (or the selected symbol) and jumps to it; ⌘/Ctrl-click in the code does
// the same (wired in nav.js, where the code view lives). The answer is `defs` from the code graph's
// node table in every language it indexes — several definitions open the Defs tab to pick from.
// SAFETY: no DOM text is written here.
"use strict";

(function () {
  if (typeof window === "undefined" || !window.mokataIDE) return;
  window.mokataIDE.register({
    id: "gotodef",
    commands: [
      { id: "nav.goToDefinition", title: "Go to definition", keys: "F12",
        run: () => { if (window.MK && window.MK.goToDefinition) window.MK.goToDefinition(); } },
    ],
  });
})();
