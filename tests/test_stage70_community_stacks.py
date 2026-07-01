"""Stage 70 — community stacks & skill marketplace.

HONEST SCOPE: mokata runs NO hosted marketplace. "Marketplace" = git/vault PUBLISH + a curated,
reviewable INDEX + a one-command, human-gated INSTALL, reusing existing primitives (export_manifest,
apply_manifest, the secret scan, the vault, the Stage-69 gated adopt pattern). These tests prove:

  * the curated versioned index parses + lists the starter stacks (in-code AND the committed mirror);
  * the starter stacks are VALID governed manifests (schema-valid; recommended skills exist);
  * `stacks list/search/show` are read-only over the curated index (bundled + a file-backed source);
  * `stacks install` runs the GATED ADOPT path — decline → nothing wired; a secret in a stack is
    hard-blocked (untrusted community content); a fresh install writes the governed config;
  * the MCP surfaces mirror this (reads read-only; install propose-only, secret hard-blocked even
    when approved);
  * the copy is HONEST (no "hosted marketplace"/"hosted registry" claimed as real);
  * the package-manager docs don't claim unpublished artifacts (Homebrew) as live;
  * a discoverable skill catalog (`skills search`) works; and 54e parity stays green.
"""

import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

from mokata import stacks as ST
from mokata import mcp_server as M
from mokata import parity
from mokata.config import Surface
from mokata.schema import validate_manifest
from mokata.skills import SKILL_NAMES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STACKS_DIR = os.path.join(ROOT, "src", "mokata", "stacks")
COMMANDS_DIR = os.path.join(ROOT, "templates", "commands")
DOCS = os.path.join(ROOT, "docs")

# A fake DSN VALUE assembled from fragments so no literal credential lives in this file (mokata's
# own secret-guard would otherwise block writing it). The point: a stack carrying this must be
# BLOCKED on install — community content is untrusted.
_FAKE_DSN = "postgres://u" + ":" + "pw" + "@h:5432/db"
_DENY = lambda _t: False
_ALLOW = lambda _t: True

STARTER = ("python-web", "node-ts", "go-service")


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _manifest_text(root):
    from mokata import MANIFEST_FILENAME, MOKATA_DIR
    with open(os.path.join(root, MOKATA_DIR, MANIFEST_FILENAME), encoding="utf-8") as fh:
        return fh.read()


def _poisoned_catalog(d, name="python-web"):
    """A file-backed catalog dir whose stack manifest carries a planted credential (untrusted)."""
    os.makedirs(d, exist_ok=True)
    data = ST.build_stack_manifest(name)
    data["leak"] = _FAKE_DSN                     # plant a secret in the community content
    with open(os.path.join(d, f"{name}.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=2))
    index = {"schema_version": ST.STACK_INDEX_VERSION, "kind": ST.INDEX_KIND,
             "stacks": [{"name": name, "framework": "x", "summary": "y", "manifest": f"{name}.json",
                         "tags": [], "skills": []}]}
    with open(os.path.join(d, ST.INDEX_FILENAME), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(index, indent=2))
    return d


# ============================================================ the curated index + starter stacks
class TestCuratedIndex(unittest.TestCase):
    def test_index_parses_and_lists_the_starter_stacks(self):
        idx = ST.build_index()
        self.assertEqual(idx["kind"], ST.INDEX_KIND)
        self.assertEqual(idx["schema_version"], ST.STACK_INDEX_VERSION)
        names = {s["name"] for s in idx["stacks"]}
        self.assertEqual(names, set(STARTER))

    def test_committed_index_mirror_parses_and_matches_code(self):
        with open(os.path.join(STACKS_DIR, ST.INDEX_FILENAME), encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["kind"], ST.INDEX_KIND)
        # no drift: the committed mirror equals what the code generates
        self.assertEqual(on_disk, ST.build_index())

    def test_committed_index_is_loadable_as_a_file_backed_catalog(self):
        # the git-org/vault convention: point --source at a dir holding index.json + stack files
        idx = ST.load_index(STACKS_DIR)
        self.assertEqual({s["name"] for s in idx["stacks"]}, set(STARTER))
        raw, data = ST.resolve_stack_manifest("python-web", source=STACKS_DIR)
        self.assertEqual(data["profile"], "standard")

    def test_load_index_degrades_clean_on_a_missing_source(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ST.StackError) as cm:
                ST.load_index(os.path.join(d, "nope"))
            self.assertIn("no stack index", str(cm.exception).lower())


class TestStarterStacksAreGovernedManifests(unittest.TestCase):
    def test_each_starter_stack_is_a_valid_governed_manifest(self):
        for name in STARTER:
            with self.subTest(stack=name):
                data = ST.build_stack_manifest(name)
                self.assertEqual(validate_manifest(data), [])
                stack = data["settings"]["stack"]
                self.assertEqual(stack["name"], name)
                self.assertTrue(stack["guardrails"], "a governed stack ships curated guardrails")
                # every recommended skill is a REAL catalog skill (no dangling references)
                for sk in stack["skills"]:
                    self.assertIn(sk, SKILL_NAMES, f"{name} recommends unknown skill {sk!r}")

    def test_committed_stack_files_match_the_builder(self):
        for name in STARTER:
            with self.subTest(stack=name):
                with open(os.path.join(STACKS_DIR, f"{name}.json"), encoding="utf-8") as fh:
                    on_disk = json.load(fh)
                self.assertEqual(validate_manifest(on_disk), [])
                self.assertEqual(on_disk, ST.build_stack_manifest(name))  # no drift

    def test_unknown_stack_is_a_clear_error(self):
        with self.assertRaises(ST.StackError):
            ST.build_stack_manifest("does-not-exist")


# ============================================================ list / search / show (read-only)
class TestReadOnlyCatalog(unittest.TestCase):
    def test_list_is_sorted_and_complete(self):
        entries = ST.list_stacks()
        self.assertEqual([e["name"] for e in entries], sorted(STARTER))

    def test_search_matches_by_tag_and_framework(self):
        self.assertEqual([h.entry["name"] for h in ST.search_stacks("typescript")], ["node-ts"])
        self.assertEqual([h.entry["name"] for h in ST.search_stacks("golang")], ["go-service"])
        self.assertEqual(ST.search_stacks(""), [])              # empty query → no matches

    def test_show_returns_entry_or_none(self):
        self.assertEqual(ST.show_stack("python-web")["name"], "python-web")
        self.assertIsNone(ST.show_stack("nope"))

    def test_reads_write_nothing_to_disk(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            before = sorted(os.listdir(surface.state.root)) if os.path.isdir(surface.state.root) else []
            M.stacks_list(path=d)
            M.stacks_search(path=d, query="python")
            M.stacks_show(path=d, name="node-ts")
            after = sorted(os.listdir(surface.state.root)) if os.path.isdir(surface.state.root) else []
            self.assertEqual(before, after)


# ============================================================ install = the gated adopt path
class TestGatedInstall(unittest.TestCase):
    def test_install_into_a_fresh_repo_writes_the_governed_config(self):
        with tempfile.TemporaryDirectory() as d:
            res = ST.install_stack(d, "python-web", assume_yes=True, out=lambda _m: None)
            self.assertTrue(res.installed)
            data = json.loads(_manifest_text(d))
            self.assertEqual(data["settings"]["stack"]["name"], "python-web")
            self.assertTrue(data["settings"]["stack"]["guardrails"])

    def test_decline_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            from mokata import MANIFEST_FILENAME, MOKATA_DIR
            res = ST.install_stack(d, "python-web", confirm=_DENY, out=lambda _m: None)
            self.assertFalse(res.installed)
            self.assertTrue(res.aborted)
            self.assertFalse(os.path.exists(os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)))

    def test_secret_in_a_stack_is_hard_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            src = _poisoned_catalog(os.path.join(d, "cat"))
            target = os.path.join(d, "target")
            os.makedirs(target)
            msgs = []
            res = ST.install_stack(target, "python-web", source=src, assume_yes=True,
                                   out=msgs.append)
            self.assertTrue(res.blocked)
            self.assertFalse(res.installed)
            self.assertTrue(res.findings)
            from mokata import MANIFEST_FILENAME, MOKATA_DIR
            self.assertFalse(os.path.exists(os.path.join(target, MOKATA_DIR, MANIFEST_FILENAME)))
            self.assertIn("secret", " ".join(msgs).lower())

    def test_unknown_stack_degrades_clean(self):
        with tempfile.TemporaryDirectory() as d:
            res = ST.install_stack(d, "nope", assume_yes=True, out=lambda _m: None)
            self.assertFalse(res.installed)
            self.assertIn("unknown stack", res.message.lower())


# ============================================================ MCP surfaces
class TestMcpSurfaces(unittest.TestCase):
    def _poison_dir(self, d):
        return _poisoned_catalog(os.path.join(d, "cat"))

    def test_read_tools_registered_read(self):
        for t in ("stacks_list", "stacks_search", "stacks_show"):
            self.assertIn(t, M.read_tool_names())
        self.assertIn("stacks_install", M.write_tool_names())

    def test_stacks_list_read(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            res = M.stacks_list(path=d)
            self.assertEqual(res["hosted"], False)
            self.assertEqual({s["name"] for s in res["stacks"]}, set(STARTER))

    def test_stacks_install_propose_only_without_approval(self):
        with tempfile.TemporaryDirectory() as d:
            res = M.stacks_install(path=d, name="python-web")
            self.assertEqual(res["status"], "proposed")
            from mokata import MANIFEST_FILENAME, MOKATA_DIR
            self.assertFalse(os.path.exists(os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)))

    def test_stacks_install_approve_commits(self):
        with tempfile.TemporaryDirectory() as d:
            res = M.stacks_install(path=d, name="python-web", approve=True)
            self.assertTrue(res["committed"])
            data = json.loads(_manifest_text(d))
            self.assertEqual(data["settings"]["stack"]["name"], "python-web")

    def test_stacks_install_secret_hard_blocked_even_when_approved(self):
        with tempfile.TemporaryDirectory() as d:
            src = self._poison_dir(d)
            target = os.path.join(d, "target")
            os.makedirs(target)
            res = M.stacks_install(path=target, name="python-web", source=src, approve=True)
            self.assertFalse(res.get("committed", False))
            self.assertEqual(res["status"], "blocked")
            self.assertTrue(res["findings"])
            from mokata import MANIFEST_FILENAME, MOKATA_DIR
            self.assertFalse(os.path.exists(os.path.join(target, MOKATA_DIR, MANIFEST_FILENAME)))


# ============================================================ CLI surface
class TestCliSurface(unittest.TestCase):
    def _run(self, **kw):
        from mokata.cli import cmd_stacks

        class A:
            pass
        a = A()
        a.path = kw.pop("path", ".")
        a.action = kw.pop("action", "list")
        a.target = kw.pop("target", None)
        a.source = kw.pop("source", None)
        a.yes = kw.pop("yes", False)
        a.force = kw.pop("force", False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_stacks(a)
        return rc, buf.getvalue()

    def test_list_show_search(self):
        rc, out = self._run(action="list")
        self.assertEqual(rc, 0)
        self.assertIn("python-web", out)
        rc, out = self._run(action="show", target="node-ts")
        self.assertEqual(rc, 0)
        self.assertIn("TypeScript", out)
        rc, out = self._run(action="search", target="golang")
        self.assertEqual(rc, 0)
        self.assertIn("go-service", out)

    def test_install_via_cli_into_fresh_repo(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._run(action="install", target="python-web", path=d, yes=True)
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(_manifest_text(d))["settings"]["stack"]["name"],
                             "python-web")

    def test_skills_search_is_discoverable(self):
        from mokata.cli import _search_skills
        hits = _search_skills("test")
        self.assertTrue(hits)
        self.assertTrue(all("test" in n.lower() or "test" in s.lower() for n, s in hits))
        # the MCP skills catalog is filterable too
        res = M.skills(query="typescript")
        self.assertEqual(res["skills"], [])            # no skill mentions typescript
        res = M.skills(query="test")
        self.assertTrue(res["skills"])


# ============================================================ honest copy + parity + pkg mgr
class TestHonestyAndParity(unittest.TestCase):
    def test_honest_note_never_claims_a_hosted_marketplace(self):
        note = ST.HONEST_NOTE.lower()
        self.assertIn("no hosted marketplace", note)
        for bad in ("hosted registry", "we host", "our marketplace", "our registry"):
            self.assertNotIn(bad, note)

    def test_docs_state_no_hosted_marketplace(self):
        for rel in ("how-to/community-stacks.md",):
            with open(os.path.join(DOCS, rel), encoding="utf-8") as fh:
                text = fh.read().lower()
            self.assertIn("no hosted marketplace", text)
            for bad in ("hosted registry", "we host a", "our hosted marketplace"):
                self.assertNotIn(bad, text)
        # the slash template is honest too
        with open(os.path.join(COMMANDS_DIR, "stacks.md"), encoding="utf-8") as fh:
            self.assertIn("no hosted marketplace", fh.read().lower())

    def test_install_docs_do_not_claim_unpublished_artifacts_as_live(self):
        with open(os.path.join(DOCS, "how-to", "install-mokata.md"), encoding="utf-8") as fh:
            text = fh.read()
        low = text.lower()
        # pipx/pip ARE live (mokata is on PyPI) — present them
        self.assertIn("pipx install mokata", low)
        # Homebrew is NOT published — the doc must say so, and must NOT mark it Live
        self.assertIn("homebrew", low)
        self.assertIn("pending publication", low)
        self.assertRegex(text, r"(?i)homebrew[^\n|]*\|[^\n]*pending")
        # there is no npm package — the doc must be explicit, not imply npx works
        self.assertIn("not applicable", low)

    def test_homebrew_formula_is_present_and_marked_pending(self):
        formula = os.path.join(ROOT, "packaging", "homebrew", "mokata.rb")
        self.assertTrue(os.path.exists(formula))
        with open(formula, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("NOT YET PUBLISHED", text)
        # it must NOT carry a real 64-hex checksum (that would imply a published artifact)
        self.assertIsNone(re.search(r'sha256 "[0-9a-f]{64}"', text),
                          "the pending formula must not claim a real sdist checksum")

    def test_stacks_in_surface_matrix_and_parity_green(self):
        self.assertIn("stacks", parity.SURFACE_MATRIX)
        s = parity.SURFACE_MATRIX["stacks"]
        self.assertIn("stacks", s.slash)
        self.assertTrue(s.in_harness)
        report = parity.verify_parity()
        self.assertTrue(report.ok, report.render())

    def test_slash_template_exists_namespaced_and_marker_prefixed(self):
        path = os.path.join(COMMANDS_DIR, "stacks.md")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn("name: stacks", md)
        self.assertIn("description: mokata ·", md)


if __name__ == "__main__":
    unittest.main()
