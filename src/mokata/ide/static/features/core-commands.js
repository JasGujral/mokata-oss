// mokata ide — the core's commands, registered like any feature (0.0.26 P0). The palette lists
// these; later stages add their own from their own static/features/<name>.js.
// 0.0.27 UX-SIMPLIFY: one command per SCREEN ("Go to: …") — the top tabs, `g` + a letter and the
// palette all call the same MK.setMode. A feature's view gets its own command from its own file.
"use strict";

(function () {
  const MK = window.MK;
  const commands = [
    // inFields: ⌘P opens even from a field, as it always has.
    { id: "quickopen.open", title: "Go to file…", keys: "Mod+P", inFields: true,
      run: () => MK.openQuick() },
    { id: "viewing.open", title: "Viewing: change what Review, the gutter and search show…",
      run: () => MK.openViewing(true) },
    { id: "explorer.expand", title: "Explorer: expand all listed folders", run: () => { MK.setMode("navigate"); MK.explorer.expandAll(); } },
    { id: "explorer.collapse", title: "Explorer: collapse all folders", run: () => { MK.setMode("navigate"); MK.explorer.collapseAll(); } },
    { id: "explorer.copy-rel", title: "Explorer: copy the open file's relative path", run: () => MK.explorer.copyOpenPath(false) },
    { id: "explorer.copy-abs", title: "Explorer: copy the open file's absolute path", run: () => MK.explorer.copyOpenPath(true) },
    // each single-key action is also one palette command, so it stays reachable with single keys off
    { id: "changes.next", title: "Changes: next change or hunk (j)", run: () => { if (MK.ui.mode === "navigate" && MK.navChange) MK.navChange(1); else MK.moveHunk(1); } },
    { id: "changes.prev", title: "Changes: previous change or hunk (k)", run: () => { if (MK.ui.mode === "navigate" && MK.navChange) MK.navChange(-1); else MK.moveHunk(-1); } },
    { id: "review.unseen", title: "Review: next unseen file (u)", run: () => { MK.setMode("review"); MK.nextUnseen(); } },
    { id: "review.open-code", title: "Review: open this file in Code (e)", run: () => MK.openCurrentInNavigate() },
    { id: "keys.help", title: "Keys: show every key (?)", run: () => MK.keysHelp(true) },
    { id: "keys.single", title: "Keys: turn single-key shortcuts on or off",
      run: () => MK.setSingleKeys(!MK.singleKeysOn()) },
    { id: "code.search", title: "Code: search the repo", keys: "Mod+Shift+F", inFields: true,
      run: () => MK.openSearch() },
  ];
  for (const s of MK.TABS) {
    commands.push({ id: "screen." + s.id, title: `Go to: ${s.title} (g ${s.chord})`, run: () => MK.showTab(s.id) });
  }
  window.mokataIDE.register({ id: "core", commands });
})();
