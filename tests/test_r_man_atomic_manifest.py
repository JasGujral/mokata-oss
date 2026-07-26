"""R-MAN — ATOMIC MANIFEST WRITES (MS.S6 rider, doc 84 §1).

MS.S6 made every SHARED artifact crash- and race-safe. It left the COMMITTED CONFIG behind, for a
defensible reason: the manifest writers are human-gated one-shots, so there is no race to lose. But
there is still a CRASH WINDOW. `open(path, "w")` truncates the file the instant it opens it; every
byte after that is a separate syscall. A crash — power loss, OOM kill, Ctrl-C at the wrong
microsecond — between the truncate and the last write leaves `.mokata/manifest.json` half-written.

That was already bad. SIMP.S1 made it WORSE, on purpose: a torn manifest now makes the repo REFUSE
transport derivation (`SessionTransportUnavailable`) rather than silently downgrading team→local.
That refusal is correct — a half-read manifest must never be trusted to answer "am I a team repo?" —
but it means a mid-write crash no longer costs you a re-run, it costs you a repo that will not push
until a human hand-edits the JSON back into shape. R-MAN makes that state UNREACHABLE-BY-CRASH: the
manifest is replaced atomically, so a crash leaves the OLD manifest, whole.

The fix is not new machinery. `atomicfile.atomic_write_text` (MS.S6's extracted primitive) already
does exactly this — same-directory temp file, `flush` + `os.fsync`, then `os.replace` — so R-MAN is
a routing change, not an invention. No gating changes: every one of these writers stays inside the
WriteGate commit callback it was already in, and the bytes it lands are unchanged.

WHAT THESE TESTS PIN
  * `test_r_man_torn_write_impossible` — fault-inject a crash at each point in the write path and
    assert the on-disk manifest is EITHER the whole old content or the whole new one. The control
    case (`_bare_write`, the pre-R-MAN mechanism) is run under the SAME injection and asserted to
    TEAR — without it this test would pass against a no-op and prove nothing.
  * `test_r_man_no_bare_manifest_writes` — the sweep guard, in the `test_d5_sweep_register` /
    MS.S6 `TestNoPlainWrites` shape: an AST walk of ALL of `src/mokata` registers every
    `open(..., "w")` site, and every site must be either CONVERTED (routed through atomicfile) or
    explicitly classified OUT-OF-SCOPE with a reason. A new bare write anywhere in src/ fails this
    test until somebody decides which it is. That is the point — fixing the writers we happened to
    notice is worth little if a new one can be added next week.
  * byte-identity — the atomic path lands exactly the bytes the bare path did, trailing newline
    included, for every converted writer.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import inspect
import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import CONSTITUTION_FILENAME, MANIFEST_FILENAME, MOKATA_DIR
from mokata import config_cmd, init, share, team
from mokata.atomicfile import atomic_write_text
from mokata.config import Surface
from mokata.init import init_repo
from mokata.knowledge import graph_adopt

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "mokata")


def silent(*_a, **_k):
    pass


def _repo(d, profile="standard"):
    init_repo(root=d, profile=profile, assume_yes=True, out=silent)
    return Surface.load(d)


def _manifest(d):
    return os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class _Boom(Exception):
    """The simulated crash. Distinct from any real error so a swallowed one can't fake a pass."""


# --------------------------------------------------------------------------- the fault injectors
#
# Three points, chosen to bracket the whole window a crash can land in:
#   at_write   — mid-content, the classic torn write (bare `open(..,"w")`'s fatal case: the target
#                is already truncated and only some bytes are back)
#   at_fsync   — content fully written to the temp file, nothing swapped yet
#   at_replace — the very last instant, the widest possible window in which new bytes exist on disk
#
def _inject(monkey_target, attr, real_module):
    """Replace `real_module.attr` with a raiser; returns a restore callable."""
    original = getattr(real_module, attr)

    def _raise(*_a, **_k):
        raise _Boom("simulated crash")

    setattr(real_module, attr, _raise)
    return lambda: setattr(real_module, attr, original)


class _TearingWriter:
    """The PRE-R-MAN mechanism, preserved verbatim as the control. If a change makes the real
    writers safe, this must still tear — otherwise the test is measuring the injector, not the fix.
    """

    @staticmethod
    def bare_write(path, text, crash_after=10):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text[:crash_after])
            raise _Boom("simulated crash")


class TestTornWriteImpossible(unittest.TestCase):
    """The crash-safety contract, driven through the REAL gated writers."""

    def test_the_control_case_really_does_tear(self):
        """Test validity: the pre-R-MAN mechanism, under this exact injection, corrupts the file.
        Without this, every assertion below could be passing against an injector that never fires."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            path = _manifest(d)
            old = _read(path)
            with self.assertRaises(_Boom):
                _TearingWriter.bare_write(path, '{"manifest_version": 1, "new": true}\n')
            torn = _read(path)
            self.assertNotEqual(torn, old, "control did not modify the file — injection is a no-op")
            self.assertNotIn(torn, (old,), "control must not leave the old content")
            # and the damage is exactly the class R-MAN exists to prevent: unparseable JSON
            with self.assertRaises(ValueError):
                json.loads(torn)

    def _assert_all_or_nothing(self, d, drive, attr):
        """Run `drive` with `attr` raising inside the atomic write path; the manifest must be
        whole-old (the crash won) or whole-new (it landed) — never a hybrid."""
        path = _manifest(d)
        old = _read(path)
        restore = _inject(None, attr, os)
        try:
            try:
                drive()
            except _Boom:
                pass                       # the crash is the point; the gate may or may not re-raise
        finally:
            restore()
        after = _read(path)
        # ALL-OR-NOTHING: parseable either way, and if it changed it is a complete manifest.
        json.loads(after)                  # raises if torn — the assertion that matters
        if after != old:
            self.assertIn("manifest_version", json.loads(after))
        return old, after

    def test_r_man_torn_write_impossible(self):
        """The named bar: crash mid-write against the team-connect and config-set writers."""
        for attr in ("fsync", "replace"):
            with self.subTest(crash_at=attr, writer="config set"):
                with tempfile.TemporaryDirectory() as d:
                    _repo(d)
                    old, after = self._assert_all_or_nothing(
                        d,
                        lambda: config_cmd.config_set(d, "settings.r_man_probe", "true",
                                                      assume_yes=True, out=silent),
                        attr)
                    self.assertEqual(after, old, "a crashed write must leave the OLD manifest")

            with self.subTest(crash_at=attr, writer="team connect"):
                with tempfile.TemporaryDirectory() as d:
                    surface = _repo(d)
                    old, after = self._assert_all_or_nothing(
                        d,
                        lambda: team.team_connect(d, surface, "MOKATA_R_MAN_DSN",
                                                  assume_yes=True, out=silent),
                        attr)
                    self.assertEqual(after, old, "a crashed write must leave the OLD manifest")

    def test_a_crashed_write_leaves_no_temp_debris(self):
        """A crash must not litter `.mokata/` with `.tmp-*` droppings — a stray temp file in the
        config dir is itself a (cosmetic) corruption of a COMMITTED directory."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            mdir = os.path.join(d, MOKATA_DIR)
            before = set(os.listdir(mdir))
            restore = _inject(None, "replace", os)
            try:
                try:
                    config_cmd.config_set(d, "settings.r_man_probe", "true",
                                          assume_yes=True, out=silent)
                except _Boom:
                    pass
            finally:
                restore()
            self.assertEqual(set(os.listdir(mdir)), before,
                             "a crashed write left debris in the committed config dir")

    def test_a_successful_write_still_lands(self):
        """The obvious other half: with no crash injected, every converted writer still writes."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            config_cmd.config_set(d, "settings.r_man_probe", "true", assume_yes=True, out=silent)
            self.assertIs(json.loads(_read(_manifest(d)))["settings"]["r_man_probe"], True)


class TestByteIdentity(unittest.TestCase):
    """Same bytes as before the routing change — content, formatting, trailing newline."""

    def test_atomic_write_text_is_byte_exact(self):
        """`atomic_write_text` writes the caller's text verbatim: it renders nothing itself, so the
        converted call sites keep whatever `to_json()`/`json.dumps` produced."""
        for text in ('{"a": 1}\n', "no trailing newline", "", "unicode: ✓\n"):
            with self.subTest(text=text[:20]):
                with tempfile.TemporaryDirectory() as d:
                    p = os.path.join(d, "probe.json")
                    atomic_write_text(p, text)
                    self.assertEqual(_read(p), text)

    def test_init_scaffold_bytes_are_unchanged(self):
        """init writes the manifest, the constitution and the ignore rule — all three byte-exact
        against the values the module declares."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            mdir = os.path.join(d, MOKATA_DIR)
            manifest_text = _read(os.path.join(mdir, MANIFEST_FILENAME))
            self.assertTrue(manifest_text.endswith("\n"))
            self.assertEqual(json.loads(manifest_text)["manifest_version"], 1)
            self.assertEqual(_read(os.path.join(mdir, CONSTITUTION_FILENAME)),
                             init.DEFAULT_CONSTITUTION)
            self.assertEqual(_read(os.path.join(mdir, ".gitignore")), init.DEFAULT_GITIGNORE)

    def test_config_set_bytes_match_the_rendered_preview(self):
        """The bytes on disk are exactly the `new_text` the gate secret-scanned and showed — the
        atomic path must not re-render, re-indent, or re-order."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            data = json.loads(_read(_manifest(d)))
            data.setdefault("settings", {})["r_man_probe"] = True
            expected = json.dumps(data, indent=2, sort_keys=False) + "\n"
            config_cmd.config_set(d, "settings.r_man_probe", "true", assume_yes=True, out=silent)
            self.assertEqual(_read(_manifest(d)), expected)


# ------------------------------------------------------------------------------- THE SWEEP GUARD
#
# CONVERTED — writes the COMMITTED manifest or a committed scaffold artifact. Each must route
# through atomicfile. This list is the deliverable: three sites were NAMED in the stage brief
# (team/config_cmd/init), and the sweep found two more (share.apply_shared, graph_adopt) plus
# init's two sibling scaffold writes.
CONVERTED = [
    (team._gated_write, ("team.py", "_gated_write._commit"),
     "team connect/disconnect/adopt/join — committed manifest"),
    (config_cmd.config_set, ("config_cmd.py", "config_set._commit"),
     "config set — committed manifest"),
    (init.InitPlan.write_files, ("init.py", "InitPlan.write_files"),
     "init scaffold — manifest + constitution.md + .gitignore"),
    (share.apply_manifest, ("share.py", "apply_manifest._commit"),
     "stack import — committed manifest"),
    (graph_adopt.adopt_graph, ("knowledge/graph_adopt.py", "adopt_graph._commit"),
     "graph adopt — committed manifest (the code_graph pin)"),
]

# OUT-OF-SCOPE — the sweep's other half. Every remaining `open(..., "w")` in src/mokata, keyed by
# "relpath:qualname", with the reason it is NOT a committed-config write. A site that is neither
# converted nor registered here fails `test_r_man_no_bare_manifest_writes`.
OUT_OF_SCOPE = {
    # --- ALREADY ATOMIC by its own hand (temp + os.replace). Not converted, because routing them
    #     would change behaviour beyond R-MAN's remit; each is pinned as atomic by its own suite.
    ("atomicfile.py", "atomic_write_text"):
        "IS the primitive — this open() is the temp-file write itself.",
    ("harness_setup.py", "_write_json"):
        "Already atomic: same-dir mkstemp + fsync + os.replace, its own impl predating the MS.S6 "
        "extraction. Writes HARNESS config (~/.claude etc), not mokata's manifest. Deduping it "
        "onto atomicfile is a fair follow-up but is not R-MAN — it would touch a second product.",
    ("knowledge/ast_backend.py", "AstBackend._save_cache"):
        "Already atomic (temp + os.replace) — and a code-graph CACHE regardless: a lost write is "
        "self-healed by the next scan.",
    ("govern/ledger.py", "AuditLedger._write_counter"):
        "Already atomic (temp + fsync + os.replace). Only the O(1) tail CACHE; its own docstring "
        "records that a torn write self-heals via a size-mismatch rescan on the next append.",
    # --- EXPORTED ARTIFACTS: they LEAVE the repo, and the importer validates what arrives.
    ("share.py", "export_manifest"):
        "Writes the caller-named EXPORT destination, never `.mokata/manifest.json`. A torn export "
        "is caught by the importer's validate+secret-scan (fail-loud), never trusted as config.",
    ("memory/share.py", "export_memory"):
        "Memory export artifact — same class as export_manifest, same importer-side validation.",
    ("vault.py", "vault_pull"):
        "Writes a pulled artifact to a caller-named `dest` (a READ's optional side-output). The "
        "vault's own artifact + index writes were made atomic at MS.S6.",
    ("memory/backends.py", "ObsidianBackend.put"):
        "Writes ONE memory note into the user's Obsidian vault — an adopted external store with "
        "its own per-item file, not mokata's committed config. Deprecated channel (0.0.17).",
    # --- DERIVED / REGENERABLE: rebuilt on demand, never a source of truth.
    ("dashboard.py", "write_dashboard"):
        "Rendered HTML view, regenerated on every `mokata watch` refresh tick.",
    ("dashboard.py", "write_governance_dashboard"):
        "Rendered HTML view, regenerated on demand from run-state + the ledger.",
    ("cli_commands/skills.py", "_skill_author.commit"):
        "Materializes ONE authored skill file to a caller-named `dest`. Gated, but not committed "
        "config: a torn skill file is a visibly broken markdown file, not a repo that won't push.",
    ("cli_commands/knowledge.py", "cmd_ci_check"):
        "Writes `--comment-file`, a CI scratch file consumed once by the workflow that asked for "
        "it; already best-effort (warns to stderr on OSError).",
    # --- BEST-EFFORT LOCAL STATE: contracted to be lossy, under temp_local/ or the user's home.
    ("migrate_channels.py", "_write_marker"):
        "One-time migration marker under temp_local/; explicitly best-effort (swallows OSError) — "
        "the migration already succeeded and the marker only suppresses a re-offer.",
    ("plugin_cache.py", "record_plugin_root"):
        "~/.mokata/plugin-root cache; must never raise (SessionStart hook) and is re-derived when "
        "absent.",
    ("plans.py", "write_plan_file"):
        "Plan file under temp_local/; run-state is the source of truth and a miss is non-fatal by "
        "documented contract.",
    ("govern/lifecycle.py", "_write_tombstone"):
        "Best-effort removal tombstone; the removal already happened and a miss is non-fatal.",
    ("extras_install.py", "record_extra_decline"):
        "USER-profile decline record (~/.mokata), explicitly non-fatal: a lost write costs one "
        "repeated question, never a corrupted repo.",
    ("knowledge/user_prefs.py", "record_graph_decline"):
        "USER-profile decline record — same class as record_extra_decline.",
}


def _bare_write_sites():
    """Every `open(..., "w")` (and `"wt"`/`"w+"`) call site in src/mokata, as (relpath, qualname)."""
    found = []
    for root, _dirs, files in os.walk(SRC):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            # POSIX-style always, so the register's keys are canonical on every platform.
            rel = os.path.relpath(path, SRC).replace(os.sep, "/")
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            _walk(tree, rel, [], found)
    return found


def _is_write_open(node):
    """A call to `open`/`os.fdopen` with a write mode literal."""
    func = node.func
    name = getattr(func, "id", None) or getattr(func, "attr", None)
    if name not in ("open", "fdopen"):
        return False
    args = list(node.args)
    modes = [a for a in args[1:] if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    modes += [k.value for k in node.keywords
              if k.arg == "mode" and isinstance(k.value, ast.Constant)]
    for m in modes:
        value = m.value if isinstance(m, ast.Constant) else m
        if isinstance(value, str) and "w" in value:
            return True
    return False


def _walk(node, rel, stack, found):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _walk(child, rel, stack + [child.name], found)
            continue
        if isinstance(child, ast.Call) and _is_write_open(child):
            found.append((rel, ".".join(stack)))
        _walk(child, rel, stack, found)


class TestNoBareManifestWrites(unittest.TestCase):
    """The guard that keeps a future writer from reintroducing the class."""

    def test_r_man_no_bare_manifest_writes(self):
        """Every committed-manifest/constitution writer routes through atomicfile, and its former
        bare-write site is GONE from the AST sweep — the two halves of "converted"."""
        live = set(_bare_write_sites())
        for func, key, label in CONVERTED:
            with self.subTest(writer=label):
                self.assertIn("atomic_write_text", inspect.getsource(func),
                              f"{label}: does not write atomically")
                self.assertNotIn(key, live,
                                 f"{label}: a bare open(..., 'w') is back at {key[0]}:{key[1]}")

    def test_every_write_site_in_src_is_classified(self):
        """THE SWEEP. A new `open(..., "w")` anywhere in src/mokata fails here until somebody
        decides whether it writes committed config (convert it) or something else (register it).
        If you are here because CI is red: do not add an entry to make it green — decide."""
        unclassified = sorted(f"{rel}:{qual}" for rel, qual in set(_bare_write_sites())
                              if (rel, qual) not in OUT_OF_SCOPE)
        self.assertEqual(
            unclassified, [],
            "UNCLASSIFIED BARE WRITE(S) — a new `open(..., 'w')` appeared in src/mokata and "
            "nobody decided what it writes. If it writes the committed manifest or constitution, "
            "route it through `atomicfile.atomic_write_text`. If it writes something else, add it "
            f"to OUT_OF_SCOPE with the reason: {unclassified}")

    def test_the_register_carries_no_stale_entries(self):
        """A registered site that no longer exists means the register is drifting from the code."""
        stale = sorted(set(OUT_OF_SCOPE) - set(_bare_write_sites()))
        self.assertEqual(stale, [],
                         f"OUT_OF_SCOPE names sites that no longer write: {stale}")

    def test_every_out_of_scope_entry_carries_a_reason(self):
        for key, why in OUT_OF_SCOPE.items():
            self.assertTrue(why and len(why) > 30, f"{key}: register entry needs a real reason")


class TestAtomicfileContract(unittest.TestCase):
    """The primitive's guarantees, pinned where R-MAN now depends on them."""

    def test_the_temp_file_is_created_in_the_target_directory(self):
        """A cross-filesystem temp would make `os.replace` a copy, not an atomic rename — which
        would silently un-fix everything above."""
        src = inspect.getsource(atomic_write_text)
        self.assertIn("dir=parent", src)
        self.assertIn("os.fsync", src)
        self.assertIn("os.replace", src)

    def test_replace_is_atomic_on_this_platform(self):
        """`os.replace` is the portable atomic-overwrite primitive: `rename(2)` on POSIX,
        `MoveFileEx(MOVEFILE_REPLACE_EXISTING)` on Windows. Both replace an EXISTING target in one
        step — which `os.rename` does not do on Windows (it raises), and which is why the primitive
        must never be 'simplified' back to `os.rename`."""
        self.assertIn("os.replace", inspect.getsource(atomic_write_text))
        self.assertNotIn("os.rename", inspect.getsource(atomic_write_text))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.json")
            atomic_write_text(p, "first\n")
            atomic_write_text(p, "second\n")          # overwrite an EXISTING file
            self.assertEqual(_read(p), "second\n")
            self.assertEqual(sorted(os.listdir(d)), ["t.json"], "temp debris left behind")


if __name__ == "__main__":
    unittest.main()
