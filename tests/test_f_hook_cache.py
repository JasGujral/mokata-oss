"""0.0.29 F — mokata's ONE user-cache rule, graded in every language it is written in, and the
bytecode cache's first run and prune (matrix #3, #4, #6, #7, #8, #9).

What must be true for the user:
  * A RELATIVE `XDG_CACHE_HOME` (or home) is ignored by every cache rule mokata has — the Python one
    (`pycache.user_cache_base`, used by `branch_protection`) and the shell one every launcher runs
    (`_mk_bytecode_cache` in `mokata-plugin-env.sh`). Taken as given it put the hooks' bytecode
    under whatever directory the hook ran in: the user's project (USER-CACHE-DIR-RELATIVE-XDG).
    "Absolute" is the platform's own rule: on macOS/Linux `C:\\x` is a relative name.
  * With no absolute base, the launcher leaves Claude Code's `PYTHONDONTWRITEBYTECODE` in place and
    writes nothing anywhere; with one, the hook's Python caches under `<base>/mokata/pycache`.
  * The first hook after an install (empty cache) answers inside `perf.FIRST_RUN_BUDGET_MS`; the
    second inside its measured ceiling.
  * Bytecode whose source is gone is deleted — only what can be proved, only inside a resolved
    `…/mokata/pycache`, never through a link, and never on Windows (a named no-op).
  * No test writes the developer's real cache.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import importlib.util
import json
import os
import py_compile
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import branch_protection, perf
from mokata import pycache as PC
from mokata.quiet_spawn import hidden

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
TESTS = os.path.dirname(os.path.abspath(__file__))
ENV_SH = os.path.join(SRC, "mokata", "hooks", "mokata-plugin-env.sh")
POSIX = os.name != "nt"

#: One input list; the expected column per platform (matrix #3).
XDG_INPUTS = [None, "", "/abs/xdg", "rel", "./x", "~/x", "C:\\x", "C:/x", "\\\\srv\\s", "\\x",
              "C:x", "//srv/s"]
HOME_INPUTS = [None, "/home/u", "relhome"]
#: Accepted as an absolute XDG_CACHE_HOME on POSIX (a leading `/`), and on Windows (ntpath).
POSIX_ABS = {"/abs/xdg", "//srv/s"}
NT_ABS = {"C:\\x", "C:/x", "\\\\srv\\s", "//srv/s"}


def _env(xdg, home, *, nt=False):
    env = {}
    if xdg is not None:
        env["XDG_CACHE_HOME"] = xdg
    if home is not None:
        env["USERPROFILE" if nt else "HOME"] = home
    return env


def _sh_rule(env, msys=False):
    """What `_mk_bytecode_cache` sets, run in a clean `sh` with exactly `env`."""
    script = ('. "$1"; PYTHONDONTWRITEBYTECODE=1; export PYTHONDONTWRITEBYTECODE; '
              '_mk_bytecode_cache; printf "%s|%s" "${PYTHONPYCACHEPREFIX-<unset>}" '
              '"${PYTHONDONTWRITEBYTECODE-<unset>}"')
    full = {"PATH": os.environ.get("PATH", "")}
    full.update(env)
    if msys:
        full["MSYSTEM"] = "MINGW64"
    p = subprocess.run(_support.bash_argv("-c", script, "sh", ENV_SH), env=full,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, **hidden())
    assert p.returncode == 0, p.stderr
    prefix, dont = p.stdout.split("|")
    return (None if prefix == "<unset>" else prefix), dont


class TheOneRule(unittest.TestCase):
    """#3 — force: before 0.0.29 F both rules took any non-empty value as given."""

    def test_the_python_rule_expected_column_per_platform(self):
        for nt, accepted in ((False, POSIX_ABS), (True, NT_ABS)):
            for xdg in XDG_INPUTS:
                with self.subTest(nt=nt, xdg=xdg):
                    got = PC.user_cache_base(_env(xdg, None, nt=nt), nt=nt)
                    self.assertEqual(xdg if xdg in accepted else None, got)
            for home, ok in (("/home/u", not nt), ("C:\\Users\\u", nt), ("relhome", False),
                             (None, False)):
                with self.subTest(nt=nt, home=home):
                    got = PC.user_cache_base(_env(None, home, nt=nt), nt=nt)
                    self.assertEqual(ok, got is not None, got)

    @unittest.skipUnless(_support.BASH, _support.NO_BASH)
    @unittest.skipIf(os.name == "nt", "Windows' bash is Git Bash: it synthesizes HOME and "
                     "converts POSIX paths, so the POSIX column is the next test's, under MSYSTEM")
    def test_sh_and_python_name_the_same_prefix_posix(self):
        for xdg in XDG_INPUTS:
            for home in HOME_INPUTS:
                with self.subTest(xdg=xdg, home=home):
                    env = _env(xdg, home)
                    want = PC.bytecode_prefix(env, nt=False)
                    prefix, dont = _sh_rule(env)
                    self.assertEqual(want, prefix)
                    self.assertEqual("<unset>" if want else "1", dont,
                                     "PYTHONDONTWRITEBYTECODE kept exactly when nothing is named")

    @unittest.skipUnless(_support.BASH, _support.NO_BASH)
    def test_sh_under_git_bash_accepts_what_windows_python_accepts(self):
        """Git Bash (`MSYSTEM`): drive and UNC forms are absolute; Git Bash's own `/c/…` spelling
        of a drive path is absolute too (Python sees it as `C:\\…`). Both rules must name the SAME
        directory for every XDG value. HOME is passed, absolute, never left out or relative: Git
        Bash's runtime synthesizes a HOME from USERPROFILE when it is unset and resolves a relative
        one against the working directory (both seen on the windows-latest runner), so an absent or
        relative HOME is a different input on Windows than on the host running this — and the home
        it would name is the one Windows Python falls back to anyway."""
        import ntpath

        def same_dir(p):
            if p is None:
                return None
            if p[:1] == "/" and p[2:3] == "/" and p[1:2].isalpha():    # Git Bash `/c/…`
                p = p[1].upper() + ":\\" + p[3:]
            return ntpath.normcase(ntpath.normpath(p))
        for xdg in XDG_INPUTS + ["/c/Users/u/cache"]:
            if xdg is None or (xdg.startswith("/") and not xdg.startswith(("/c/", "//"))):
                continue                     # an MSYS-only path: no Windows-Python counterpart
            with self.subTest(xdg=xdg):
                as_nt = "C:\\" + xdg[3:].replace("/", "\\") if xdg.startswith("/c/") else xdg
                py = PC.bytecode_prefix({"XDG_CACHE_HOME": as_nt, "USERPROFILE": "C:\\hm"},
                                        nt=True)
                prefix, dont = _sh_rule({"XDG_CACHE_HOME": xdg, "HOME": "/c/hm"}, msys=True)
                self.assertEqual(same_dir(py), same_dir(prefix), (py, prefix))
                self.assertEqual("<unset>", dont)
                if PC.user_cache_base({"XDG_CACHE_HOME": as_nt}, nt=True) is None:
                    self.assertEqual("/c/hm/.cache/mokata/pycache", prefix, "the HOME fallback")
                else:
                    self.assertEqual(f"{xdg}/mokata/pycache", prefix, "the XDG value as given")

    def test_branch_protection_uses_the_rule(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": "rel", "HOME": "relhome", "USERPROFILE": "relhome"}):
            self.assertTrue(os.path.isabs(branch_protection.default_cache_dir()))
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": "C:\\x"}):
            got = branch_protection.default_cache_dir()
            self.assertEqual(not POSIX, got.startswith("C:\\x"), got)


class _Plugin(unittest.TestCase):
    """A fresh copy of the plugin's sources (no bytecode), as test_w5 builds it."""

    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        cls.t = os.path.realpath(cls._t.name)
        cls.src = os.path.join(cls.t, "plugin", "src")
        for pkg in ("mokata", "mokata_emit"):
            shutil.copytree(os.path.join(SRC, pkg), os.path.join(cls.src, pkg),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        cls.launcher = os.path.join(cls.src, "mokata", "hooks", "mokata-hook-launch")
        os.chmod(cls.launcher, os.stat(cls.launcher).st_mode | stat.S_IXUSR)

    @classmethod
    def tearDownClass(cls):
        cls._t.cleanup()

    def _base_env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if k not in ("MOKATA_HOOK", "MOKATA_PYTHON", "CLAUDE_PLUGIN_DATA", "PYTHONPATH",
                            "PYTHONPYCACHEPREFIX", "XDG_CACHE_HOME", "HOME")}
        env.update(PYTHONDONTWRITEBYTECODE="1", MOKATA_PYTHON=sys.executable)
        env.update(extra)
        return env

    def _pycs(self, top):
        return [os.path.join(d, n) for d, _s, ns in os.walk(top) for n in ns
                if n.endswith(".pyc")]


@unittest.skipUnless(_support.BASH and POSIX, "the POSIX reproduction (Windows: probe)")
class RelativeXdgWritesNothingHere(_Plugin):
    """#4 — force: the row's reproduction, from a temp project as the hook's cwd."""

    def test_a_relative_or_foreign_xdg_puts_nothing_under_the_project(self):
        for xdg in ("rel", "C:\\x", "./x"):
            with self.subTest(xdg=xdg):
                proj = tempfile.mkdtemp(dir=self.t, prefix="proj-")
                home = tempfile.mkdtemp(dir=self.t, prefix="home-")
                env = self._base_env(XDG_CACHE_HOME=xdg, HOME=home, USERPROFILE=home)
                p = subprocess.run(_support.bash_argv(self.launcher, "secret-guard", "--text",
                                                      "print(1)", "--path", "ok.py"),
                                   env=env, cwd=proj, capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, **hidden())
                self.assertEqual(0, p.returncode, p.stderr)
                self.assertEqual([], os.listdir(proj), "the hook wrote under the project")
                self.assertEqual([], self._pycs(self.src), "bytecode beside the sources")
                self.assertTrue(self._pycs(os.path.join(home, ".cache", "mokata", "pycache")),
                                "the home rule was used instead")


@unittest.skipUnless(_support.BASH and POSIX, "the POSIX state cell (Windows: probe)")
class NoCacheIsGradedByState(_Plugin):
    """#9 — guard: what the launcher hands the hook's Python, read from the child itself
    (`MOKATA_HOOK` override: the launcher's rung 0), and where `.pyc` files land."""

    def _child_env(self, **env):
        probe = os.path.join(self.t, "probe.sh")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write('#!/bin/sh\nprintf "%s|%s" "${PYTHONPYCACHEPREFIX-<unset>}" '
                     '"${PYTHONDONTWRITEBYTECODE-<unset>}"\n')
        os.chmod(probe, 0o755)
        p = subprocess.run(_support.bash_argv(self.launcher, "stop"),
                           env=self._base_env(MOKATA_HOOK=probe, **env), cwd=self.t,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, **hidden())
        self.assertEqual(0, p.returncode, p.stderr)
        return p.stdout.split("|")

    def test_no_nameable_base_keeps_the_variable_and_sets_no_prefix(self):
        self.assertEqual(["<unset>", "1"], self._child_env(HOME="relhome", USERPROFILE="relhome"))
        self.assertEqual(["<unset>", "1"], self._child_env())

    def test_a_base_sets_the_prefix_and_drops_the_variable(self):
        base = os.path.join(self.t, "xdg")
        self.assertEqual([os.path.join(base, "mokata", "pycache"), "<unset>"],
                         self._child_env(XDG_CACHE_HOME=base))

    def test_no_base_writes_no_bytecode_anywhere(self):
        proj = tempfile.mkdtemp(dir=self.t, prefix="nobase-")
        p = subprocess.run(_support.bash_argv(self.launcher, "secret-guard", "--text", "print(1)",
                                              "--path", "ok.py"),
                           env=self._base_env(HOME="relhome", USERPROFILE="relhome"), cwd=proj, capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, **hidden())
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertEqual([], self._pycs(self.src))
        self.assertEqual([], os.listdir(proj))


@unittest.skipUnless(_support.BASH, _support.NO_BASH)
class TheFirstRunIsGraded(_Plugin):
    """#6 — force: the empty-cache hook had no budget at all (measured 242 ms on the prompt hook,
    graded by nothing). Normalised by the bare interpreter measured beside it."""

    FLOOR = [sys.executable, "-c", "pass"]

    def _timed(self, argv, env, cwd):
        t0 = time.perf_counter()
        p = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, stdin=subprocess.DEVNULL,
                           **hidden())
        ms = (time.perf_counter() - t0) * 1000.0
        self.assertEqual(0, p.returncode, p.stderr[-2000:])
        t0 = time.perf_counter()
        subprocess.run(self.FLOOR, capture_output=True, stdin=subprocess.DEVNULL, **hidden())
        return ms, (time.perf_counter() - t0) * 1000.0

    def test_call_one_inside_the_first_run_budget_call_two_inside_its_ceiling(self):
        sub = "secret-guard"
        argv = _support.bash_argv(self.launcher, sub, "--text", "print(1)", "--path", "ok.py")
        ceiling, _ = perf.hook_budget_ms(sub, "outside")
        seen = []
        for attempt in range(3):              # best of three fresh caches: load moves a cold run
            base = os.path.join(self.t, f"cold-{attempt}")
            env = self._base_env(XDG_CACHE_HOME=base)
            first, f1 = self._timed(argv, env, self.t)
            second, f2 = self._timed(argv, env, self.t)
            self.assertTrue(self._pycs(base), "fixture: the first call filled the cache")
            seen.append((first / perf.interpreter_factor(f1),
                         second / perf.interpreter_factor(f2)))
            if seen[-1][0] <= perf.FIRST_RUN_BUDGET_MS and seen[-1][1] <= ceiling:
                break
        sys.stderr.write(f"\n[F-FIRST-RUN] {sub}: (first, second) normalised ms {seen}\n")
        self.assertLessEqual(min(s[0] for s in seen), perf.FIRST_RUN_BUDGET_MS, seen)
        self.assertLessEqual(min(s[1] for s in seen), ceiling, seen)


def _compile_tree(prefix_root, src_root, files):
    """Real bytecode for `files` under `src_root`, written where CPython writes it with
    `PYTHONPYCACHEPREFIX=prefix_root` (via `importlib.util.cache_from_source`)."""
    saved = sys.pycache_prefix
    sys.pycache_prefix = prefix_root
    try:
        out = []
        for rel in files:
            src = os.path.join(src_root, rel)
            os.makedirs(os.path.dirname(src), exist_ok=True)
            with open(src, "w", encoding="utf-8") as fh:
                fh.write("x = 1\n")
            out.append(py_compile.compile(src, cfile=importlib.util.cache_from_source(src),
                                          doraise=True))
        return out
    finally:
        sys.pycache_prefix = saved


@unittest.skipUnless(POSIX, "prune is POSIX-only (Windows: the no-op cell below)")
class PruneIsNarrow(unittest.TestCase):
    """#7 — force: nothing pruned the prefix (17 MB, trees for four deleted checkouts)."""

    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="f-prune-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.prefix = os.path.join(self.t, "cache", "mokata", "pycache")
        os.makedirs(self.prefix)

    def test_a_deleted_source_root_goes_a_live_one_stays(self):
        live = _compile_tree(self.prefix, os.path.join(self.t, "live"), ["pkg/a.py", "pkg/b.py"])
        dead = _compile_tree(self.prefix, os.path.join(self.t, "gone"), ["pkg/a.py", "m.py"])
        shutil.rmtree(os.path.join(self.t, "gone"))
        self.assertEqual(2, PC.prune(self.prefix))
        self.assertTrue(all(os.path.exists(p) for p in live))
        self.assertFalse(any(os.path.exists(p) for p in dead))
        gone_tree = self.prefix + os.path.join(self.t, "gone")
        self.assertFalse(os.path.exists(gone_tree), "emptied directories are removed")
        self.assertEqual(0, PC.prune(self.prefix), "idempotent")

    def test_a_root_that_is_not_mokata_pycache_is_never_touched(self):
        other = os.path.join(self.t, "cache", "other")
        os.makedirs(other)
        dead = _compile_tree(other, os.path.join(self.t, "gone"), ["m.py"])
        shutil.rmtree(os.path.join(self.t, "gone"))
        self.assertEqual(0, PC.prune(other))
        self.assertTrue(os.path.exists(dead[0]))

    def test_a_symlinked_root_is_resolved_once_and_links_below_are_not_followed(self):
        real_cache = os.path.join(self.t, "real-cache")
        os.makedirs(os.path.join(real_cache, "mokata", "pycache"))
        link_cache = os.path.join(self.t, "home-cache")
        os.symlink(real_cache, link_cache)
        prefix = os.path.join(link_cache, "mokata", "pycache")
        dead = _compile_tree(prefix, os.path.join(self.t, "gone"), ["m.py"])
        outside = os.path.join(self.t, "outside")
        dead_out = _compile_tree(outside, os.path.join(self.t, "gone2"), ["n.py"])
        os.symlink(outside, os.path.join(real_cache, "mokata", "pycache", "lnk"))
        # A link that sits exactly where a STALE `.pyc` would (its name decodes to a deleted
        # source): a link is a leaf the prune never acts on — not followed, not removed.
        named = _compile_tree(prefix, os.path.join(self.t, "gone3"), ["k.py"])[0]
        os.remove(named)
        os.symlink(dead_out[0], named)
        shutil.rmtree(os.path.join(self.t, "gone"))
        shutil.rmtree(os.path.join(self.t, "gone2"))
        shutil.rmtree(os.path.join(self.t, "gone3"))
        self.assertEqual(1, PC.prune(prefix))
        self.assertFalse(os.path.exists(dead[0]))
        self.assertTrue(os.path.exists(dead_out[0]), "a link below the root was followed")
        self.assertTrue(os.path.islink(named), "a link below the root was deleted")

    def test_a_pyc_whose_decode_does_not_round_trip_is_kept(self):
        keep = []
        tag = sys.implementation.cache_tag
        d = self.prefix + os.path.join(self.t, "gone")
        os.makedirs(d)
        for name in ("m.cpython-311.pyc" if tag != "cpython-311" else "m.cpython-310.pyc",
                     f"m.{tag}.opt-7.pyc", f".{tag}.pyc", "m.pyc"):
            p = os.path.join(d, name)
            with open(p, "wb") as fh:
                fh.write(b"x")
            keep.append(p)
        self.assertEqual(0, PC.prune(self.prefix))
        self.assertTrue(all(os.path.exists(p) for p in keep))

    def test_scale_1e5_stale_pyc_in_1e3_directories(self):
        tag = sys.implementation.cache_tag
        base = self.prefix + os.path.join(self.t, "gone")
        for i in range(1000):
            d = os.path.join(base, f"d{i}")
            os.makedirs(d)
            for j in range(100):
                with open(os.path.join(d, f"m{j}.{tag}.pyc"), "wb"):
                    pass
        self.assertEqual(100_000, PC.prune(self.prefix))
        self.assertEqual([], os.listdir(self.prefix))


@unittest.skipUnless(POSIX, "prune is POSIX-only")
class PruneHoldsItsRootUnderASwap(unittest.TestCase):
    """force — verifier M1 (0.0.29 F round 2): a directory under the root (or the root itself)
    replaced by a link to a directory OUTSIDE the root, at EVERY step of the walk (each call the
    prune makes to `os.open` / `os.scandir` is a step; the swap happens just before that call),
    never makes the prune delete anything outside the root. The victim is shaped to be deleted if
    reached: `<stem>.<tag>.pyc`, whose decoded source does not exist. Deterministic: no thread,
    no clock — the seam is the call sequence itself. The old path-based walk deleted the victim."""

    TARGETS = ("", "a", os.path.join("a", "b"), os.path.join("a", "b", "c"))

    def _tree(self):
        t = os.path.realpath(tempfile.mkdtemp(prefix="mk-prune-swap-"))
        self.addCleanup(shutil.rmtree, t, True)
        root = os.path.join(t, "cache", "mokata", "pycache")
        tag = sys.implementation.cache_tag
        for d in self.TARGETS[1:]:
            os.makedirs(os.path.join(root, d), exist_ok=True)
            with open(os.path.join(root, d, f"s.{tag}.pyc"), "wb") as fh:
                fh.write(b"x")
        victim = os.path.join(t, "victim")
        os.makedirs(victim)
        bait = os.path.join(victim, f"m.{tag}.pyc")
        with open(bait, "wb") as fh:
            fh.write(b"x")
        return root, victim, bait

    def _run(self, root, on_step):
        from unittest import mock
        real_open, real_scandir = os.open, os.scandir
        steps = [0]

        def hook():
            steps[0] += 1
            on_step(steps[0])

        def w_open(*a, **k):
            hook()
            return real_open(*a, **k)

        def w_scandir(*a, **k):
            hook()
            return real_scandir(*a, **k)
        with mock.patch.object(os, "open", w_open), mock.patch.object(os, "scandir", w_scandir), \
                mock.patch.object(os, "supports_fd", os.supports_fd | {w_scandir}), \
                mock.patch.object(os, "supports_dir_fd", os.supports_dir_fd | {w_open}):
            n = PC.prune(root)
        return n, steps[0]

    def test_unswapped_the_walk_prunes_and_counts_its_steps(self):
        root, _victim, bait = self._tree()
        n, steps = self._run(root, lambda _i: None)
        self.assertEqual(3, n, "the seam must not make the prune vacuous")
        self.assertGreaterEqual(steps, 4)
        self.assertTrue(os.path.exists(bait))

    def test_a_swap_at_every_step_never_deletes_outside_the_root(self):
        root, _v, _b = self._tree()
        _n, total = self._run(root, lambda _i: None)
        swapped = 0
        for target in self.TARGETS:
            for k in range(1, total + 2):
                root, victim, bait = self._tree()
                path = os.path.join(root, target) if target else root

                def on_step(i, path=path, victim=victim, k=k):
                    nonlocal swapped
                    if i == k and os.path.isdir(path) and not os.path.islink(path):
                        os.rename(path, path + ".was")
                        os.symlink(victim, path)
                        swapped += 1
                with self.subTest(target=target or "<root>", step=k):
                    self._run(root, on_step)
                    self.assertTrue(os.path.exists(bait), "the prune deleted outside its root")
                    self.assertEqual([os.path.basename(bait)], os.listdir(victim))
        self.assertGreater(swapped, len(self.TARGETS), "the seam never swapped anything")


@unittest.skipUnless(POSIX, "prune is POSIX-only")
class PruneReachesItsRootByDescriptor(unittest.TestCase):
    """force — verifier r2 H1 (0.0.29 F round 3): the ROOT is reached from `/` one component at a
    time by descriptor (`O_NOFOLLOW | O_DIRECTORY`), then held only if it is the very directory the
    resolve named (`st_dev`/`st_ino`). Three cells, each owned by one guard:

      * an ANCESTOR (`home`, `.cache`, `mokata`, `pycache`) swapped for a link to a look-alike tree
        OUTSIDE the cache, at EVERY step the prune takes (each `os.open` / `os.stat` / `os.scandir`
        call; the swap lands just before it) -> nothing outside is deleted;
      * the path re-pointed through a link to the SAME directory (identity equal) -> nothing is
        pruned: a link on the path is refused, not followed (the no-follow guard's own cell);
      * the root replaced by a NEW real directory (no link anywhere; the no-follow guard cannot
        see it) -> nothing is pruned: the held directory is not the resolved one (identity's)."""

    ANCESTORS = ("home", ".cache", "mokata", "pycache")

    def _tree(self):
        base = os.path.realpath(tempfile.mkdtemp(prefix="mk-prune-anc-"))
        self.addCleanup(shutil.rmtree, base, True)
        tag = sys.implementation.cache_tag
        rel = ("home", ".cache", "mokata", "pycache")
        root = os.path.join(base, *rel)
        stale = os.path.join(root, "gone", f"m.{tag}.pyc")
        os.makedirs(os.path.dirname(stale))
        with open(stale, "wb") as fh:
            fh.write(b"x")
        # a look-alike of every suffix of the path, outside the cache, each holding the same bait
        baits = {}
        for i, anc in enumerate(rel):
            other = os.path.join(base, f"elsewhere-{anc}")
            bait = os.path.join(other, *rel[i + 1:], "gone", f"m.{tag}.pyc")
            os.makedirs(os.path.dirname(bait))
            with open(bait, "wb") as fh:
                fh.write(b"x")
            baits[anc] = (os.path.join(base, *rel[:i + 1]), other, bait)
        return root, stale, baits

    def _run(self, root, on_step):
        from unittest import mock
        real = {n: getattr(os, n) for n in ("open", "stat", "scandir")}
        steps = [0]

        def wrap(name):
            def w(*a, **k):
                steps[0] += 1
                on_step(steps[0])
                return real[name](*a, **k)
            return w
        w = {n: wrap(n) for n in real}
        with mock.patch.object(os, "open", w["open"]), mock.patch.object(os, "stat", w["stat"]), \
                mock.patch.object(os, "scandir", w["scandir"]), \
                mock.patch.object(os, "supports_fd", os.supports_fd | {w["scandir"]}), \
                mock.patch.object(os, "supports_dir_fd", os.supports_dir_fd | {w["open"]}):
            n = PC.prune(root)
        return n, steps[0]

    def test_unswapped_the_root_is_reached_and_pruned(self):
        root, stale, _b = self._tree()
        n, steps = self._run(root, lambda _i: None)
        self.assertEqual(1, n)
        self.assertFalse(os.path.exists(stale))
        self.assertGreater(steps, 5)

    def test_an_ancestor_swapped_at_every_step_never_reaches_outside(self):
        root, _s, _b = self._tree()
        _n, total = self._run(root, lambda _i: None)
        fired = 0
        for anc in self.ANCESTORS:
            for k in range(1, total + 2):
                root, _stale, baits = self._tree()
                path, other, bait = baits[anc]

                def on_step(i, path=path, other=other, k=k):
                    nonlocal fired
                    if i == k and not os.path.islink(path):
                        os.rename(path, path + ".was")
                        os.symlink(other, path)
                        fired += 1
                with self.subTest(ancestor=anc, step=k):
                    self._run(root, on_step)
                    self.assertTrue(os.path.exists(bait), "the prune deleted outside the cache")
        self.assertGreaterEqual(fired, len(self.ANCESTORS) * 5, "the seam rarely swapped")

    def test_a_link_on_the_path_to_the_same_root_is_refused(self):
        root, stale, baits = self._tree()
        cache = baits[".cache"][0]
        calls = []

        def at_first_open(i, cache=cache):
            calls.append(i)
            if len(calls) == 3 and not os.path.islink(cache):   # 3: the walk opens `/`
                os.rename(cache, cache + ".real")
                os.symlink(cache + ".real", cache)
        n, _ = self._run(root, at_first_open)
        self.assertTrue(os.path.islink(cache), "fixture: the swap fired")
        self.assertEqual(0, n, "a link on the root's path was followed")
        self.assertTrue(os.path.exists(os.path.join(cache + ".real", "mokata", "pycache", "gone",
                                                    os.path.basename(stale))))

    def test_a_root_replaced_by_another_real_directory_is_refused(self):
        root, stale, _b = self._tree()
        calls = []
        planted = os.path.join(root, "gone", os.path.basename(stale))

        def at_first_open(i):
            calls.append(i)
            if len(calls) == 3:                 # 1 isdir, 2 the resolve's stat, 3 the walk's `/`
                os.rename(root, root + ".was")
                os.makedirs(os.path.dirname(planted))
                with open(planted, "wb") as fh:
                    fh.write(b"x")
        n, _ = self._run(root, at_first_open)
        self.assertTrue(os.path.isdir(root + ".was"), "fixture: the swap fired")
        self.assertEqual(0, n, "the held root is not the one resolved, and it was pruned")
        self.assertTrue(os.path.exists(planted))


class PruneOnWindowsIsANoOp(unittest.TestCase):
    """#7 Windows column — guard: the drive is stripped from the prefixed path, so nothing can be
    proved; the prune touches nothing. (Built here from the POSIX shape; the probe's cell builds
    the two-drive case from `cache_from_source` on Windows itself — not run, see the report.)"""

    def test_nt_prune_touches_nothing(self):
        t = os.path.realpath(tempfile.mkdtemp(prefix="f-prune-nt-"))
        self.addCleanup(shutil.rmtree, t, True)
        prefix = os.path.join(t, "mokata", "pycache")
        dead = _compile_tree(prefix, os.path.join(t, "gone"), ["m.py"])
        shutil.rmtree(os.path.join(t, "gone"))
        self.assertEqual(0, PC.prune(prefix, nt=True))
        self.assertTrue(os.path.exists(dead[0]))


class TheRefresherPrunes(unittest.TestCase):
    """guard — the prune runs in SessionStart's detached refresher (off every hook's clock), on the
    prefix the launcher handed down."""

    def test_refresh_main_prunes_sys_pycache_prefix(self):
        from unittest import mock
        from mokata import briefing_cache as BC
        with mock.patch.object(sys, "pycache_prefix", "/x/mokata/pycache"), \
                mock.patch("mokata.pycache.prune", return_value=3) as pr:
            self.assertEqual(3, BC.prune_bytecode())
        pr.assert_called_once_with("/x/mokata/pycache")
        with open(BC.__file__, encoding="utf-8") as fh:
            src = fh.read()
        body = src[src.index("def refresh_main"):]
        end = body.find("\ndef ", 1)
        self.assertIn("prune_bytecode()", body if end < 0 else body[:end])


class NoTestWritesTheRealCache(unittest.TestCase):
    """#8 — guard (HOOK-PYCACHE-TESTS-WRITE-REAL-CACHE)."""

    def test_support_points_xdg_at_a_temp_dir(self):
        xdg = os.environ.get("XDG_CACHE_HOME", "")
        self.assertTrue(os.path.isabs(xdg))
        self.assertEqual(os.path.realpath(_support.TEST_CACHE_HOME), os.path.realpath(xdg))
        self.assertNotEqual(os.path.realpath(_support._USER_CACHE), os.path.realpath(xdg))

    def test_every_module_running_a_launcher_names_its_cache(self):
        # CORPUS: THE WORKING TREE. The question is "can any test module in THIS checkout run a
        # hook launcher against the developer's real cache?" — an uncommitted module would run
        # too, and `sync-public.sh` rsyncs tests/ from the disk, not the index.
        bad = []
        for n in sorted(os.listdir(TESTS)):
            if not (n.startswith("test_") and n.endswith(".py")):
                continue
            with open(os.path.join(TESTS, n), encoding="utf-8") as fh:
                src = fh.read()
            if ("mokata-hook-launch" in src or "mokata-launch" in src or "HOOK_LAUNCHER" in src) \
                    and "import _support" not in src and "from _support import" not in src:
                bad.append(n)
        self.assertEqual([], bad, "a module runs a launcher without _support's temp cache")


if __name__ == "__main__":
    unittest.main()
