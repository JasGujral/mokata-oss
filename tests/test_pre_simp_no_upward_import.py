"""PRE-SIMP (0.0.15) — LAYER-LINT's seed: no upward (surface) import at the module top of the
extracted L2 domain modules.

The stage kills `memory/store.py`'s module-top `from ..prompt import read_yes_no` (UI in the domain
layer) — the confirm callable resolves `read_yes_no` LAZILY now — and extracts the backend
selection into `memory/selection.py`. This pins that neither module imports a SURFACE-layer module
(`prompt`/`cli`/`mcp`/`hooks`) at the MODULE TOP. A function-local (lazy) import inside a composition
method is allowed and is the house pattern; this guards only the module-level import list, which is
what the 0.1.1 LAYER-LINT will generalise across the tree.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import os
import unittest

import _support  # noqa: F401 - puts src/ on the path

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "mokata")

# The L4 SURFACE-layer modules an L2 domain module must not import at its top.
SURFACE = {"prompt", "cli", "mcp", "hooks", "hook_cli", "cli_commands"}


def _module_top_imported_modules(path):
    """The set of module names imported at MODULE TOP (not inside any function/class) of `path`.

    For `from X import a, b` -> X's last component; `from . import prompt` -> the imported name
    `prompt`; `import a.b.c` -> the dotted components. Lazy (function-local) imports are ignored,
    which is the whole point: this guards the module-level import list only."""
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    names = set()
    for node in tree.body:                      # module body ONLY — not recursive into defs
        if isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[-1])
            else:
                # `from .. import prompt` — the names ARE (potentially) submodules
                for alias in node.names:
                    names.add(alias.name.split(".")[-1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                for part in alias.name.split("."):
                    names.add(part)
    return names


class TestNoUpwardImportAtModuleTop(unittest.TestCase):
    def test_store_has_no_module_top_surface_import(self):
        top = _module_top_imported_modules(os.path.join(SRC, "memory", "store.py"))
        self.assertEqual(top & SURFACE, set(),
                         f"memory/store.py imports surface module(s) at its top: {top & SURFACE}")

    def test_store_does_not_module_top_import_read_yes_no(self):
        # the exact leak the stage killed (`from ..prompt import read_yes_no` at module top).
        src = open(os.path.join(SRC, "memory", "store.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        top_from_prompt = [n for n in tree.body
                           if isinstance(n, ast.ImportFrom) and (n.module or "").endswith("prompt")]
        self.assertEqual(top_from_prompt, [],
                         "memory/store.py still imports from `prompt` at the module top")

    def test_selection_has_no_module_top_surface_import(self):
        top = _module_top_imported_modules(os.path.join(SRC, "memory", "selection.py"))
        self.assertEqual(top & SURFACE, set(),
                         f"memory/selection.py imports surface module(s) at its top: {top & SURFACE}")


if __name__ == "__main__":
    unittest.main()
