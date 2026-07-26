"""BREW-15 — the Homebrew formula's url + sha256 + resources are GENERATED, never hand-written.

The committed formula drifted three releases stale (a 0.0.5 url and a literal
`sha256 "pending-publication-..."`) because all three were hand-maintained. This suite pins the
generator that replaces that hand-editing:

  * a fixture sdist checksums correctly and rewrites BOTH the url and the sha256 line, with the
    byte-diff CONFINED to the regions the script owns (resource stanzas carry their own url and
    sha256 lines and must survive untouched);
  * re-running the fill is idempotent;
  * a version that disagrees with the sdist filename is REFUSED (never silently checksummed);
  * a formula that has lost the regions the script owns is REFUSED, not half-written;
  * rendering is deterministic — the same lockfile yields byte-identical output;
  * the lockfile + formula actually vendor the MCP SDK, because Homebrew installs Python
    formulae with `--no-deps` and a missing resource ships a broken `mokata-mcp`.

Offline: the one impure edge (the PyPI lookup) is injected, so nothing here touches the network.
"""

import os
import sys
import tarfile
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import fill_homebrew_formula as FF  # noqa: E402

FORMULA = os.path.join(ROOT, "packaging", "homebrew", "mokata.rb")
LOCK = os.path.join(ROOT, "packaging", "homebrew", "resources.lock.json")

# A formula skeleton with the same shape as the real one, small enough to diff by eye.
TEMPLATE = '''class Mokata < Formula
  desc "test"
  homepage "https://example.invalid"
  url "FILL-ME-SDIST-URL"
  sha256 "FILL-ME-SDIST-SHA256"
  license "Apache-2.0"

  # BEGIN GENERATED RESOURCES
  # END GENERATED RESOURCES

  def install
    virtualenv_install_with_resources
  end
end
'''

LOCK_ENTRIES = [
    {"name": "zebra", "url": "https://example.invalid/zebra-1.0.tar.gz", "sha256": "z" * 64},
    {"name": "Alpha", "url": "https://example.invalid/Alpha-2.0.tar.gz", "sha256": "a" * 64},
]


def _fixture_sdist(tmp, name="mokata-9.9.9.tar.gz"):
    """Build a real (tiny) sdist tarball so the checksum under test is a genuine one."""
    path = os.path.join(tmp, name)
    inner = os.path.join(tmp, "PKG-INFO")
    with open(inner, "w", encoding="utf-8") as fh:
        fh.write("Metadata-Version: 2.1\nName: mokata\nVersion: 9.9.9\n")
    with tarfile.open(path, "w:gz") as tar:
        tar.add(inner, arcname="mokata-9.9.9/PKG-INFO")
    return path


class TestFixtureSdistFillsTheFormula(unittest.TestCase):
    def test_fixture_sdist_checksum_is_the_real_sha256(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            path = _fixture_sdist(tmp)
            with open(path, "rb") as fh:
                expected = hashlib.sha256(fh.read()).hexdigest()
            self.assertEqual(FF.sha256_file(path), expected)

    def test_fill_rewrites_url_and_sha_lines(self):
        out = FF.fill_formula(
            TEMPLATE, version="9.9.9",
            url="https://example.invalid/mokata-9.9.9.tar.gz", sha256="f" * 64,
            resources=FF.render_resources(LOCK_ENTRIES),
        )
        self.assertIn('url "https://example.invalid/mokata-9.9.9.tar.gz"', out)
        self.assertIn(f'sha256 "{"f" * 64}"', out)
        self.assertNotIn(FF.URL_TOKEN, out)
        self.assertNotIn(FF.SHA_TOKEN, out)

    def test_byte_diff_is_confined_to_the_owned_regions(self):
        # Everything outside url / sha256 / the marker block must be untouched — in particular
        # the fill must NOT reach into the resource stanzas' own url and sha256 lines.
        filled = FF.fill_formula(
            TEMPLATE, version="9.9.9",
            url="https://example.invalid/mokata-9.9.9.tar.gz", sha256="f" * 64,
            resources=FF.render_resources(LOCK_ENTRIES),
        )
        # The markers themselves bracket the generated region; compare what surrounds them.
        before = [ln for ln in TEMPLATE.splitlines()
                  if "url " not in ln and "sha256 " not in ln
                  and ln not in (FF.BEGIN_MARK, FF.END_MARK)]
        after_head = filled.split(FF.BEGIN_MARK)[0].splitlines()
        after_tail = filled.split(FF.END_MARK)[1].splitlines()
        kept = [ln for ln in after_head + after_tail
                if "url " not in ln and "sha256 " not in ln]
        self.assertEqual([ln for ln in before if ln.strip() not in ("",)],
                         [ln for ln in kept if ln.strip() not in ("",)])

    def test_resource_stanza_urls_survive_a_refill(self):
        once = FF.fill_formula(
            TEMPLATE, version="9.9.9", url="https://example.invalid/mokata-9.9.9.tar.gz",
            sha256="f" * 64, resources=FF.render_resources(LOCK_ENTRIES),
        )
        twice = FF.fill_formula(
            once, version="9.9.10", url="https://example.invalid/mokata-9.9.10.tar.gz",
            sha256="e" * 64, resources=FF.render_resources(LOCK_ENTRIES),
        )
        # the new top-level values landed...
        self.assertIn('url "https://example.invalid/mokata-9.9.10.tar.gz"', twice)
        self.assertIn(f'sha256 "{"e" * 64}"', twice)
        # ...and the resources kept THEIR urls/checksums (not overwritten by the top-level fill)
        self.assertIn('url "https://example.invalid/Alpha-2.0.tar.gz"', twice)
        self.assertIn(f'sha256 "{"z" * 64}"', twice)


class TestIdempotence(unittest.TestCase):
    def test_refilling_with_the_same_inputs_changes_nothing(self):
        args = dict(version="9.9.9", url="https://example.invalid/mokata-9.9.9.tar.gz",
                    sha256="f" * 64, resources=FF.render_resources(LOCK_ENTRIES))
        once = FF.fill_formula(TEMPLATE, **args)
        twice = FF.fill_formula(once, **args)
        self.assertEqual(once, twice)

    def test_rendering_is_deterministic_and_sorted(self):
        # Same lockfile -> byte-identical output, regardless of the input ordering, so the
        # fill-script tests (and a release diff) can pin it.
        forward = FF.render_resources(LOCK_ENTRIES)
        backward = FF.render_resources(list(reversed(LOCK_ENTRIES)))
        self.assertEqual(forward, backward)
        self.assertLess(forward.index('resource "Alpha"'), forward.index('resource "zebra"'))


class TestRefusals(unittest.TestCase):
    def test_wrong_version_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _fixture_sdist(tmp)  # mokata-9.9.9.tar.gz
            formula = os.path.join(tmp, "mokata.rb")
            with open(formula, "w", encoding="utf-8") as fh:
                fh.write(TEMPLATE)
            args = FF.main.__globals__["argparse"].Namespace(
                formula=formula, lock=LOCK, version="1.2.3", from_pypi=False, sdist=path,
            )
            with self.assertRaises(FF.FormulaError) as ctx:
                FF.cmd_fill(args)
            self.assertIn("refusing to fill", str(ctx.exception))
            # and the formula on disk is untouched
            with open(formula, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), TEMPLATE)

    def test_non_mokata_sdist_filename_is_refused(self):
        with self.assertRaises(FF.FormulaError):
            FF.sdist_version("requests-2.0.0.tar.gz")

    def test_malformed_formula_missing_markers_is_refused(self):
        broken = TEMPLATE.replace(FF.BEGIN_MARK, "  # something else")
        with self.assertRaises(FF.FormulaError) as ctx:
            FF.fill_formula(broken, version="9.9.9", url="u", sha256="s", resources="")
        self.assertIn("markers", str(ctx.exception))

    def test_malformed_formula_missing_url_line_is_refused(self):
        broken = "\n".join(ln for ln in TEMPLATE.splitlines() if "url " not in ln)
        with self.assertRaises(FF.FormulaError) as ctx:
            FF.fill_formula(broken, version="9.9.9", url="u", sha256="s", resources="")
        self.assertIn("url", str(ctx.exception))

    def test_lockfile_entry_missing_a_field_is_refused(self):
        with self.assertRaises(FF.FormulaError):
            FF.render_resources([{"name": "x", "url": "https://example.invalid/x.tar.gz"}])


class TestTheRealFormulaAndLock(unittest.TestCase):
    """The shipped formula must actually be installable — the resources are not decoration."""

    def test_lock_vendors_the_mcp_sdk(self):
        names = {e["name"].lower() for e in FF.load_lock(LOCK)}
        # mokata's ONE unconditional runtime dependency, plus the transitive pieces that would
        # break `import mcp` if a future refresh dropped them. Names are the PyPI canonical
        # (hyphenated) form — `brew audit --strict` rejects the `pydantic_core` spelling that
        # `pip list` reports.
        for required in ("mcp", "pydantic", "pydantic-core", "anyio", "httpx", "starlette"):
            self.assertIn(required, names, f"lockfile lost the '{required}' resource")

    def test_every_lock_entry_has_an_sdist_url_and_real_checksum(self):
        for entry in FF.load_lock(LOCK):
            self.assertTrue(entry["url"].endswith((".tar.gz", ".zip")),
                            f"{entry['name']}: Homebrew builds from source, needs an sdist")
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")

    def test_resource_names_are_pypi_canonical(self):
        # `brew audit --strict` fails a resource whose name doesn't match the PyPI package
        # name, and `pip list` reports the underscore spelling for several of these. The
        # 0.0.15 audit run caught exactly this on pydantic-core and typing-extensions.
        for entry in FF.load_lock(LOCK):
            self.assertNotIn("_", entry["name"],
                             f"{entry['name']}: brew audit requires the hyphenated PyPI name")

    def test_formula_declares_the_resources_and_the_rust_build_dep(self):
        with open(FORMULA, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(FF.BEGIN_MARK, text)
        self.assertIn('depends_on "rust" => :build', text,
                      "pydantic-core/rpds-py/cryptography build from source under --no-binary")

    def test_formula_test_block_imports_the_mcp_sdk(self):
        # `mokata --version` passes even with the SDK absent (it is lazily imported), so the
        # import check is what makes a missing resource fail LOUD instead of silently.
        with open(FORMULA, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('"import mcp"', text,
                      "formula's test block must import the MCP SDK or a dropped resource "
                      "ships a broken `mokata-mcp` undetected")

    def test_formula_is_in_template_state_until_the_tap_push(self):
        # Doc 00 rule 2: the committed formula stays honestly unfilled between releases. The
        # fill happens in the tap runbook, post-tag.
        with open(FORMULA, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(FF.URL_TOKEN, text)
        self.assertIn(FF.SHA_TOKEN, text)


if __name__ == "__main__":
    unittest.main()
