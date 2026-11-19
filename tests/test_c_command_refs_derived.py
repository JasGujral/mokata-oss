"""0.0.29 C2 — every command reference mokata emits names a REAL command, rendered for the ACTIVE route.

Three derivations from three real registries, never a typed list:

1. SLASH NAMES — registry = the stems of ``templates/commands/*.md`` (what ``skills.py`` / ``menu.py``
   read). Every ``/mokata:<name>`` in any ``src`` string constant (``ast``; docstrings and comments are
   not emitted text), ``.md``, ``.js`` (comment lines excluded) and ``.json`` must name a registered
   command. ``/mokata:<placeholder>``, ``/mokata:{field}`` and ``/mokata:*`` are not names.
   Limit, stated: the BARE form (``/spec``) has no token that is unambiguous in prose and paths, so it
   is not scanned; it is produced only by ``plugin_runtime.slash_command``, which is exercised below.
2. ROUTING RATCHET — outside the allowlist, no ``.py`` / ``.js`` string literal carries a
   ``/mokata:<name>``: a command a human reads is built by ``plugin_runtime.slash_command`` or rendered
   by ``plugin_runtime.localize_commands`` (a literal INSIDE a ``localize_commands(...)`` call, or in a module constant that is
   passed to one, is the one source form, rendered at the emit boundary). A new literal anywhere is red the day it is
   written. An allowlist entry that matches nothing fails, so the list cannot rot into an exemption.
3. CLI NAMES — registry = ``skill_lints._cli_tree()`` (the real parser). ``cli_parity_findings`` runs
   over every ``src`` string constant, restricted to code spans (backticks) so ``mokata cannot`` prose
   and ``mokata review:`` output lines are skipped. First level and, where a group has subparsers,
   second level must resolve. Limit, stated: ``team`` / ``vault`` take positional actions with no
   subparser, so their second token is not parser-checkable and is not pretended to be.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Iterator, List, Set, Tuple

import _support  # noqa: F401  (puts src/ on the path)

from mokata import plugin_runtime as PR
from mokata import skill_lints

SRC = Path(__file__).resolve().parents[1] / "src" / "mokata"

#: A command NAME after the namespace. `<x>`, `{x}`, `*` and a bare `/mokata:` are not names.
NAME_TOKEN = re.compile(r"/mokata:([a-z][a-z0-9-]*)")
#: The same in an f-string, where the name is a field: `f"/mokata:{name}"`.
FIELD_TOKEN = re.compile(r"/mokata:\{\}")

#: (posix path under src/mokata) -> why this file is allowed to keep a literal `/mokata:<name>`.
#: By design, left as the plugin form. Each entry is asserted to still match something.
ALLOWLIST: Dict[str, str] = {
    "plugin_runtime.py": "the door itself: builds `/mokata:<name>` and the token regex localize_commands "
                         "rewrites from; its docstrings and error text quote the namespaced form",
    "docsync.py": "the docs checker RECOGNISES `/mokata:<name>` and the bare form in pages it audits, and "
                  "names the form the page used in its finding; it renders for a PAGE, not for a route",
    "axi_out.py": "a recogniser (`_CMD`) for a backticked command in a model's text, both forms",
    "explain/bundle.py": "a recogniser: the command-prefix set that tells a PR link from a typed command "
                         "(`/explain` and `/mokata:explain`, both)",
    "ide/sessions.py": "a recogniser: a prompt starting with either typed form is a brainstorm; "
                       "it reads the user's text, it does not emit one",
    "ide/features/palette.py": "the palette's `/mokata:*` filter vocabulary (catalog entry names), and "
                               "the page copies the namespaced form by design",
    "ide/static/features/palette.js": "the `/mokata:*` filter vocabulary of the command palette",
    "skills.py": "the skill PROMPT tables carry ONE command form and are rendered once, in bulk, at the "
                 "emit boundary by `render_skill` (`mokata run`); the same text is written verbatim "
                 "into the plugin's command templates (MUST_RENDER below asserts the call exists)",
    "parity.py": "the surface matrix's notes are data built once at import; `CommandSurface.described()` "
                 "renders a note at the read (MUST_RENDER asserts the call exists)",
    "skill_anatomy.py": "rationalization text rendered into SKILL.md files; those are written by "
                        "`harness_setup`/`agent_skills`, which localize at write (the plugin ships "
                        "them verbatim, where `/mokata:x` is right)",
    "skill_contracts.py": "contract text rendered into SKILL.md files, localized at write like "
                          "skill_anatomy.py",
}

#: an allowlisted file whose literals are rendered in bulk must actually contain the rendering call.
MUST_RENDER = ("skills.py", "parity.py")

_PY = "py"


def _rel(p: Path) -> str:
    return p.relative_to(SRC).as_posix()


def _registry() -> Set[str]:
    return {p.stem for p in (SRC / "templates" / "commands").glob("*.md")}


def _docstring_ids(tree: ast.AST) -> Set[int]:
    out: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def _call_name(call: ast.Call) -> str:
    f = call.func
    return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")


def _literals(path: Path) -> Iterator[Tuple[int, str, bool, bool]]:
    """(line, text, rendered, recogniser) for every emitted-text string constant of one module.
    f-strings are one text with `{}` for each field. `rendered` = inside a `localize_commands(...)`
    call; `recogniser` = inside `re.compile(...)`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = _docstring_ids(tree)
    parents: Dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node

    #: names that reach a `localize_commands(...)` call: a module/class constant holding the ONE
    #: source form is rendered where it is emitted, which is the design (C-commands-setup §3b).
    fed: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) == "localize_commands":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name):
                    fed.add(sub.id)
                elif isinstance(sub, ast.Attribute):
                    fed.add(sub.attr)

    def ctx(node: ast.AST) -> Tuple[bool, bool]:
        rendered = recogniser = False
        cur = parents.get(id(node))
        while cur is not None:
            if isinstance(cur, (ast.Assign, ast.AnnAssign)) and isinstance(
                    parents.get(id(cur)), (ast.Module, ast.ClassDef)):
                targets = cur.targets if isinstance(cur, ast.Assign) else [cur.target]
                rendered = rendered or any(isinstance(t, ast.Name) and t.id in fed for t in targets)
            if isinstance(cur, ast.Call):
                name = _call_name(cur)
                rendered = rendered or name == "localize_commands"
                recogniser = recogniser or (name == "compile"
                                            and isinstance(cur.func, ast.Attribute)
                                            and getattr(cur.func.value, "id", "") == "re")
            cur = parents.get(id(cur))
        return rendered, recogniser

    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            text = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
            r, c = ctx(node)
            yield node.lineno, text, r, c
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            if isinstance(parents.get(id(node)), ast.JoinedStr):
                continue
            r, c = ctx(node)
            yield node.lineno, node.value, r, c


def _has_ref(text: str) -> bool:
    return bool(NAME_TOKEN.search(text) or FIELD_TOKEN.search(text))


def _js_lines(path: Path) -> Iterator[Tuple[int, str]]:
    in_block = False
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if in_block:
            in_block = "*/" not in s
            continue
        if s.startswith("/*"):
            in_block = "*/" not in s
            continue
        if s.startswith("//") or s.startswith("*"):
            continue
        yield n, line


def census() -> Dict[str, object]:
    """Every command reference on the tree: slash tokens by surface, and the routed/allowlisted split."""
    py_unrouted: List[Tuple[str, int, str]] = []
    py_rendered = py_allowed = 0
    for p in sorted(SRC.rglob("*.py")):
        rel = _rel(p)
        for line, text, rendered, recogniser in _literals(p):
            if not _has_ref(text):
                continue
            if recogniser:
                py_allowed += 1
            elif rel in ALLOWLIST:
                py_allowed += 1
            elif rendered:
                py_rendered += 1
            else:
                py_unrouted.append((rel, line, text.strip()[:90]))
    js_unrouted: List[Tuple[str, int, str]] = []
    js_allowed = 0
    for p in sorted((SRC / "ide" / "static").rglob("*.js")):
        rel = _rel(p)
        for line, text in _js_lines(p):
            if NAME_TOKEN.search(text):
                if rel in ALLOWLIST:
                    js_allowed += 1
                else:
                    js_unrouted.append((rel, line, text.strip()[:90]))
    return {"py_unrouted": py_unrouted, "py_rendered": py_rendered, "py_allowed": py_allowed,
            "js_unrouted": js_unrouted, "js_allowed": js_allowed}


class TheSlashNamesAreRegistered(unittest.TestCase):
    """Derivation 1: a `/mokata:<name>` anywhere a human reads names a command that exists."""

    def _tokens(self) -> Iterator[Tuple[str, int, str]]:
        for p in sorted(SRC.rglob("*.py")):
            for line, text, _r, recogniser in _literals(p):
                if recogniser:
                    continue
                for m in NAME_TOKEN.finditer(text):
                    yield _rel(p), line, m.group(1)
        for p in sorted(SRC.rglob("*.md")):
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                for m in NAME_TOKEN.finditer(line):
                    yield _rel(p), n, m.group(1)
        for p in sorted((SRC / "ide" / "static").rglob("*.js")):
            for n, line in _js_lines(p):
                for m in NAME_TOKEN.finditer(line):
                    yield _rel(p), n, m.group(1)
        for p in sorted(SRC.rglob("*.json")):
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                for m in NAME_TOKEN.finditer(line):
                    yield _rel(p), n, m.group(1)

    def test_the_registry_is_derived_and_not_empty(self):
        reg = _registry()
        self.assertGreaterEqual(len(reg), 20)
        self.assertIn("spec", reg)

    def test_every_slash_token_names_a_registered_command(self):
        reg = _registry()
        bad = [f"{rel}:{line}  /mokata:{name}" for rel, line, name in self._tokens()
               if name not in reg]
        self.assertEqual(bad, [], "a command reference names no registered command:\n" + "\n".join(bad))


class TheRouteIsDecidedByTheDoorNotByALiteral(unittest.TestCase):
    """Derivation 2: no emitted `/mokata:<name>` literal outside the allowlist."""

    def test_no_python_or_js_literal_bypasses_the_door(self):
        c = census()
        offenders = [f"{r}:{n}  {t}" for r, n, t in c["py_unrouted"] + c["js_unrouted"]]  # type: ignore
        self.assertEqual(offenders, [],
                         f"{len(offenders)} command reference(s) are literals; build them with "
                         "plugin_runtime.slash_command or render with localize_commands:\n"
                         + "\n".join(offenders))

    def test_every_allowlist_entry_has_a_reason_and_still_matches(self):
        for rel, why in ALLOWLIST.items():
            self.assertTrue(why.strip(), f"{rel}: an allowlist entry states WHY")
            p = SRC / rel
            self.assertTrue(p.is_file(), f"{rel}: allowlisted but the file is gone")
            if rel.endswith(".py"):
                hit = any(_has_ref(t) or t == "/mokata:" for _l, t, _r, _c in _literals(p))
                if not hit:
                    hit = any("/mokata:" in t for _l, t, _r, _c in _literals(p))
            else:
                hit = any("/mokata:" in line for _n, line in _js_lines(p))
            self.assertTrue(hit, f"{rel}: allowlisted but carries no `/mokata:` literal any more — "
                                 "delete the entry, the list may not rot into an exemption")

    def test_a_bulk_rendered_file_calls_the_renderer(self):
        for rel in MUST_RENDER:
            tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
            calls = [n for n in ast.walk(tree)
                     if isinstance(n, ast.Call) and _call_name(n) == "localize_commands"]
            self.assertTrue(calls, f"{rel}: its literals are rendered in bulk, but it never calls "
                                   "localize_commands")

    def test_the_allowlist_is_small(self):
        self.assertLessEqual(len(ALLOWLIST), 12)


class TheDoorRendersTheActiveRoute(unittest.TestCase):
    """The behaviour the swaps rely on, once per route (the surfaces' own tests cover their text)."""

    def test_plugin_env_is_namespaced_and_bare_files_are_bare(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(PR.slash_command("spec", d, env={}), "/mokata:spec")
            cmds = Path(d, ".claude", "commands")
            cmds.mkdir(parents=True)
            (cmds / "spec.md").write_text("---\ndescription: mokata · spec\n---\nbody\n", encoding="utf-8")
            self.assertEqual(PR.slash_command("spec", d, env={}, home=d), "/spec")
            self.assertEqual(PR.slash_command("spec", d, env={"CLAUDE_PLUGIN_ROOT": d}),
                             "/mokata:spec")
            self.assertEqual(PR.localize_commands("run /mokata:spec then /mokata:ship", d,
                                                  env={}, home=d),
                             "run /spec then /mokata:ship")


class TheCliNamesAreRealCommands(unittest.TestCase):
    """Derivation 3: every backticked `mokata <cmd> [<sub>]` in a string constant resolves."""

    #: (file, finding text) -> why a prose false positive is not a broken reference.
    PROSE = {
        ("crg_resolve.py", "`mokata code-review-graph` — no such CLI command"):
            "`mokata[full]` / `pipx inject mokata code-review-graph` — a package name inside a pipx command",
    }

    def test_every_cli_reference_resolves_against_the_parser(self):
        tree = skill_lints._cli_tree()
        bad: List[str] = []
        for p in sorted(SRC.rglob("*.py")):
            rel = _rel(p)
            for line, text, _r, _c in _literals(p):
                if "mokata " not in text:
                    continue
                for f in skill_lints.cli_parity_findings(rel, text, tree):
                    if (rel, f) in self.PROSE:
                        continue
                    bad.append(f"{rel}:{line}  {f}")
        self.assertEqual(bad, [], "a CLI reference names no real command:\n" + "\n".join(bad))

    def test_every_prose_exemption_still_matches(self):
        tree = skill_lints._cli_tree()
        seen: Set[Tuple[str, str]] = set()
        for (rel, _f) in self.PROSE:
            for _l, text, _r, _c in _literals(SRC / rel):
                for f in skill_lints.cli_parity_findings(rel, text, tree):
                    seen.add((rel, f))
        self.assertEqual(set(self.PROSE) - seen, set(), "a prose exemption matches nothing now")


def _bare_command(root: str, name: str, marked: bool = True) -> None:
    """Install `<name>.md` the way the CLI route does (bare, with mokata's description mark) — or a
    FOREIGN one of the same name (no mark), which must never make mokata say `/<name>`."""
    d = Path(root, ".claude", "commands")
    d.mkdir(parents=True, exist_ok=True)
    desc = "mokata · " + name if marked else "someone else's " + name
    (d / f"{name}.md").write_text(f"---\ndescription: {desc}\n---\nbody\n", encoding="utf-8")


class EverySurfaceNamesTheRoutesCommand(unittest.TestCase):
    """One test per kind of swapped surface: bare mokata files -> the bare form; the plugin env -> the
    namespaced form; a foreign file of the same name -> the namespaced form."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.realpath(self._tmp.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_PLUGIN")}

    def _under(self, plugin: bool = False):
        from unittest import mock
        env = dict(self.env)
        if plugin:
            env["CLAUDE_PLUGIN_ROOT"] = self.root
        return mock.patch.dict(os.environ, env, clear=True)

    def test_an_unblock_hint_and_the_setup_offer_and_a_skill_follow_the_route(self):
        from mokata.bootstrap import build_setup_offer
        from mokata.legibility import unblock_hint
        from mokata.skills import get_skill, render_skill
        _bare_command(self.root, "spec")
        _bare_command(self.root, "onboard")
        _bare_command(self.root, "init")                  # a Claude Code built-in: written mokata-init
        with self._under():
            self.assertIn("`/spec`", unblock_hint("spec-persisted", self.root))
            self.assertIn("/onboard", build_setup_offer(root=self.root).text)
            self.assertIn("/mokata:init", build_setup_offer(root=self.root).text)   # bare init is foreign
            self.assertNotIn("`/mokata:spec`", render_skill(get_skill("develop"), None, self.root))
        with self._under(plugin=True):
            self.assertIn("`/mokata:spec`", unblock_hint("spec-persisted", self.root))
            self.assertIn("/mokata:onboard", build_setup_offer(root=self.root).text)

    def test_a_foreign_file_of_the_same_name_is_not_ours(self):
        from mokata.legibility import unblock_hint
        _bare_command(self.root, "spec", marked=False)
        with self._under():
            self.assertIn("`/mokata:spec`", unblock_hint("spec-persisted", self.root))

    def test_the_ide_state_payload_carries_the_commands_the_pages_name(self):
        import test_p0_ide_api_contract as P0
        from mokata.ide.server import PAGE_COMMANDS
        _bare_command(self.root, "spec")
        for plugin, want in ((False, {"brainstorm": "/mokata:brainstorm", "spec": "/spec"}),
                             (True, {"brainstorm": "/mokata:brainstorm", "spec": "/mokata:spec"})):
            with self._under(plugin=plugin):
                c, _app, _state = P0.client(self.root, demo=True)
                got = c.get("/api/state").json()["commands"]
            self.assertEqual(set(PAGE_COMMANDS), set(got))
            self.assertEqual(want, got)

    def test_the_init_plan_names_the_setup_command_of_the_active_route(self):
        """C1 Low: on the plugin route the plan must not send the user to a `mokata` CLI they lack."""
        from unittest import mock
        from mokata import init as INIT
        from mokata.adoption_modes import plugin_setup_command
        plan = INIT.plan_init(self.root, "full") if hasattr(INIT, "plan_init") else None
        self.assertIsNotNone(plan, "init exposes a plan")
        with self._under():
            self.assertIn("`mokata setup claude --mode full`", INIT.render_plan(plan))
        with self._under(plugin=True):
            text = INIT.render_plan(plan)
        self.assertIn(plugin_setup_command("full"), text)
        self.assertNotIn("`mokata setup claude --mode full`", text)

    def test_the_pages_read_the_payload_and_do_not_name_a_command_themselves(self):
        static = SRC / "ide" / "static"
        for rel in ("sessions.js", "plan.js", "features/review.js"):
            text = (static / rel).read_text(encoding="utf-8")
            self.assertIn('MK.cmd("', text, f"{rel}: the page reads its command from the payload")


def _fed_constants() -> Dict[str, Set[str]]:
    """module-level constants whose literal carries a command AND that reach a `localize_commands(...)`
    call in their own file: name -> files that define them."""
    out: Dict[str, Set[str]] = {}
    for p in sorted(SRC.rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        fed: Set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) == "localize_commands":
                fed.update(n.id for n in ast.walk(node) if isinstance(n, ast.Name))
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and t.id in fed and node.value is not None and any(
                            isinstance(c, ast.Constant) and isinstance(c.value, str) and "/mokata:" in c.value
                            for c in ast.walk(node.value)):
                        out.setdefault(t.id, set()).add(_rel(p))
    return out


class ARenderedConstantIsNeverUsedRaw(unittest.TestCase):
    """The ratchet counts a constant as routed because it is passed to `localize_commands`; that is
    only true if EVERY use of it is. A raw use anywhere in `src` reds."""

    def test_every_use_of_a_fed_constant_is_inside_the_renderer(self):
        consts = _fed_constants()
        self.assertGreaterEqual(len(consts), 8)
        bad: List[str] = []
        for p in sorted(SRC.rglob("*.py")):
            tree = ast.parse(p.read_text(encoding="utf-8"))
            inside: Set[int] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _call_name(node) == "localize_commands":
                    inside.update(id(n) for n in ast.walk(node))
            here = {c for c, files in consts.items() if _rel(p) in files} | {
                a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                for a in n.names if a.name in consts}
            for node in ast.walk(tree):
                name = node.id if isinstance(node, ast.Name) else (
                    node.attr if isinstance(node, ast.Attribute) else None)
                if name in here and isinstance(getattr(node, "ctx", None), ast.Load) \
                        and id(node) not in inside:
                    bad.append(f"{_rel(p)}:{node.lineno}  {name}")
        self.assertEqual(bad, [], "a rendered constant is used raw (it names a command in the plugin "
                                  "form whatever the route):\n" + "\n".join(bad))


class TheRootlessDoorsFindTheRepoFromCwd(unittest.TestCase):
    """A caller with no repo root in hand (a table, a pure function) must still get the route right:
    on a CLI-route repo (a project-scope bare mokata command file) every entry point emits the bare
    form, never a plugin-form command that does not exist there."""

    def setUp(self):
        from unittest import mock
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.realpath(self._tmp.name)
        for name in _registry():
            _bare_command(self.root, PR.bare_command_name(name))
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_PLUGIN")}
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self._cwd)

    def _no_plugin_form(self, text: str, what: str) -> None:
        self.assertNotRegex(text, r"/mokata:[a-z]", f"{what}: names a command that does not exist on "
                                                    f"this route: {text[:160]!r}")

    def test_the_door_itself(self):
        self.assertEqual(PR.slash_command("spec"), "/spec")
        self.assertEqual(PR.localize_commands("run /mokata:spec then /mokata:brainstorm"),
                         "run /spec then /brainstorm")

    def test_every_rootless_entry_point_emits_the_bare_form(self):
        import types
        from mokata import (bootstrap, legibility, loop_guard, onboarding, parity, product_docs,
                            progress, team)
        from mokata.engine import completeness, spec_gate
        out: List[str] = []
        cases = {
            "spec_gate": spec_gate.check_spec_persisted(None, run_id="r").reason,
            "completeness.standalone_note": completeness.standalone_note(types.SimpleNamespace(standalone=True)),
            "legibility.next_command": legibility.next_command(legibility.STAGE_BADGE_STAGES[0]) or "",
            "legibility.unblock_hint": legibility.unblock_hint("spec-persisted") or "",
            "loop_guard": loop_guard.breaker_message([("sig", 3)]),
            "product_docs": product_docs.render_topic_list(),
            "onboarding.next": onboarding._wizard_next_step(),
            "setup_offer": bootstrap.build_setup_offer().text,
            "progress.no_run": progress.render_lanes(progress.RunLanes(active=False, mode="none")),
        }
        team._join_onboard(out.append)
        cases["team"] = "\n".join(out)
        cases["parity notes"] = "\n".join(s.described() for s in parity.SURFACE_MATRIX.values())
        for what, text in cases.items():
            self._no_plugin_form(text, what)
        self.assertIn("/spec", cases["spec_gate"])
        self.assertIn("/brainstorm", cases["progress.no_run"])


if __name__ == "__main__":
    unittest.main()
