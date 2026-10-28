// mokata ide — which tree the code views read (0.0.26 P41, MCP-FOLLOWS-WORKTREE). When the focused
// session works in its own git worktree, Code view / ⌘P / search / impact / the graph read THAT
// worktree; this chip, next to the focus chip, names it ("code: worktree .claude/worktrees/mk-…"),
// so a reader never mistakes the main checkout's code for the session's. It asks `/api/code-tree`
// only when the focus chip's text changes (no polling). SAFETY: textContent only.
"use strict";

(function () {
  const MK = window.MK;

  function chipText(t) {
    if (!t || t.sample) return "";
    if (t.worktree) return "code: worktree " + (t.label || "");
    return t.note ? "code: main checkout (" + t.note + ")" : "";
  }
  MK.codeTree = { chipText };

  if (typeof document === "undefined") return;   // node tests of the pure helper

  let chip = null, asked = 0;
  async function refresh() {
    const n = ++asked;
    let t = null;
    try { t = await MK.api("/api/code-tree"); } catch (_e) { t = null; }
    if (n !== asked || !chip) return;
    const text = chipText(t);
    chip.textContent = text;
    chip.title = t && t.tree ? t.tree : "";
    chip.hidden = !text;
  }

  function mount() {
    const focus = document.getElementById("focus-chip");
    if (!focus || chip) return;
    chip = document.createElement("span");
    chip.id = "code-tree-chip";
    chip.className = "chip code-tree-chip";
    chip.hidden = true;
    focus.insertAdjacentElement("afterend", chip);
    new MutationObserver(refresh).observe(focus, { childList: true, characterData: true,
                                                   subtree: true });
    refresh();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
