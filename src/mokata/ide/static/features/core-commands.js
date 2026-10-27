// mokata ide — the core's commands, registered like any feature (0.0.26 P0). The palette lists
// these; later stages add their own from their own static/features/<name>.js.
// 0.0.26 P34: one command per SCREEN ("Go to: …") — the rail, `g` + a letter and the palette all
// call the same MK.setMode. A feature's screen gets its own command from its own file.
"use strict";

(function () {
  const MK = window.MK;
  const commands = [
    // inFields: ⌘P opens even from a field, as it always has.
    { id: "quickopen.open", title: "Go to file…", keys: "Mod+P", inFields: true,
      run: () => MK.openQuick() },
    { id: "viewing.open", title: "Viewing: change what Review, the gutter and search show…",
      run: () => MK.openViewing(true) },
    { id: "keys.help", title: "Keys: show every key (?)", run: () => MK.keysHelp(true) },
  ];
  for (const s of MK.SCREENS) {              // the core screens only — a feature names its own
    commands.push({ id: "screen." + s.id, title: `Go to: ${s.title}${s.chord ? ` (g ${s.chord})` : ""}`,
                    run: s.id === "search" ? () => MK.openSearch() : () => MK.setMode(s.id), ...(s.id === "search" ? { keys: "Mod+Shift+F", inFields: true } : {}) });
  }
  window.mokataIDE.register({ id: "core", commands });
})();
