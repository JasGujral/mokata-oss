"""0.0.28 lane I2 — the MCP tool reference is DERIVED, never hand-kept.

`docs/reference/mcp-tools.md` must equal `mokata mcp docs` (`mcp_admin.render_tool_docs`), which
renders the live server's descriptions and input schemas plus each tool's CLI twin from
`parity.SURFACE_MATRIX`. A tool, parameter or description change that is not regenerated reds
here. FIRING CONTROL: hand-edit one line of the page.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import unittest

import _support  # noqa: F401  (puts src/ on the path)
import _mcp_sdk

from mokata import mcp_admin  # noqa: E402
from mokata.mcp.registry import TOOLS  # noqa: E402

PAGE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "docs", "reference",
                                    "mcp-tools.md"))


@_mcp_sdk.requires_mcp_sdk
class TestI2_McpToolDocsAreDerived(unittest.TestCase):

    _rendered = None

    def setUp(self):
        # Rendered in setUp, not setUpClass: `requires_mcp_sdk` gates `setUp`, so a class fixture
        # would build the server BEFORE the gate and error on a leg where the SDK is broken (the
        # `jsonschema=absent` cell) instead of skipping it, test by test, as the gate rules.
        cls = type(self)
        if cls._rendered is None:
            cls._rendered = mcp_admin.render_tool_docs()
        self.render = cls._rendered

    def test_I2_the_page_is_the_render(self):
        with open(PAGE, encoding="utf-8") as fh:
            page = fh.read()
        self.assertEqual(page, self.render,
                         "regenerate: `mokata mcp docs > docs/reference/mcp-tools.md`")

    def test_I2_every_tool_has_a_section_with_an_example(self):
        for spec in TOOLS:
            self.assertIn(f"\n### {spec.name.replace('_', '-')}\n", self.render, spec.name)
            self.assertIn(f"Example: `{spec.name}(", self.render, spec.name)

    def test_I2_the_page_covers_consent_not_initialized_and_first_start(self):
        for heading in ("## Gated writes: the consent flow", "\"not-initialized\"",
                        "## First start under the plugin", "`mokata init`"):
            self.assertIn(heading, self.render)

    def test_I2_the_command_reference_links_it(self):
        with open(os.path.join(os.path.dirname(PAGE), "command-surfaces.md"),
                  encoding="utf-8") as fh:
            self.assertIn("(mcp-tools.md)", fh.read())


if __name__ == "__main__":
    unittest.main()
