"""0.0.29 F (matrix #2) — the macOS process reader is what `ps` said, read in-process.

What must be true for the user:
  * On a Mac the hooks read their parent chain without spawning `ps`, and every hop reads exactly
    what `ps` gave before: the same parent pid, the same start token BYTE FOR BYTE (rows recorded
    before this release still match, so a live session is never read as ended), and the same name.
  * The name is the LAUNCHED path, as `ps -o comm=` prints it — never the resolved image. The native
    Claude Code CLI is `~/.local/bin/claude -> …/versions/2.1.311`: read by its image it is
    `2.1.311`, not a Claude, and an MCP server more than three launchers below it silently lost its
    session binding (SESSION-BIND-BY-ANCESTOR). A chain 40 launchers deep through such a `claude`
    still binds.
  * A dead pid or another user's process reads as absent (fail-closed), never as a guess.

macOS only: the reader exists only there (Linux reads `/proc`, Windows Toolhelp — unchanged).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import session_ancestry as SA

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
DARWIN = sys.platform == "darwin"


def _ps_table():
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,lstart=,comm="], capture_output=True,
                         text=True, env=dict(os.environ, LC_ALL="C", LANG="C"),
                         stdin=subprocess.DEVNULL, check=True).stdout
    table = {}
    for line in out.splitlines():
        parts = line.split(None, 7)
        if len(parts) >= 7:
            table[int(parts[0])] = (int(parts[1]), " ".join(parts[2:7]),
                                    parts[7] if len(parts) > 7 else None)
    return table


@unittest.skipUnless(DARWIN, "the libproc reader is macOS's")
class EveryHopIsWhatPsSaid(unittest.TestCase):
    """force — `_libproc_lookup` does not exist before 0.0.29 F; each assertion is a hop of this
    test's own real chain compared with `ps` read at the same moment."""

    def test_ppid_token_and_name_equal_ps_for_every_hop(self):
        """The chain is BUILT by the test, so it never depends on how the suite was launched: a
        detached runner (nohup, a parent that is gone: ppid 1) has no ancestors to read. A child
        starts a grandchild; the grandchild prints its pid and blocks on the shared stdin (alive
        until the test closes it — an event, no clock). The reader walks from the grandchild:
        grandchild -> child -> this process -> …, every hop compared with `ps` read at the moment."""
        lookup = SA._libproc_lookup()
        self.assertIsNotNone(lookup)
        inner = "import os, sys; print(os.getpid(), flush=True); sys.stdin.read()"
        outer = ("import subprocess, sys; "
                 f"sys.exit(subprocess.call([sys.executable, '-c', {inner!r}]))")
        p = subprocess.Popen([sys.executable, "-c", outer], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
        try:
            grandchild = int(p.stdout.readline())
            own = lookup(grandchild)
            chain = SA.ancestors(lookup, pid=grandchild)
            ps = _ps_table()
        finally:
            p.stdin.close()
            p.wait()
            p.stdout.close()
        hops = [pid for pid, _ in chain]
        self.assertGreaterEqual(len(chain), 2, chain)
        self.assertEqual([p.pid, os.getpid()], hops[:2], "the built chain, nearest first")
        self.assertIsNotNone(own)
        for pid, info in [(grandchild, own)] + chain:
            with self.subTest(pid=pid):
                ppid, token, comm = ps[pid]
                self.assertEqual(ppid, info.ppid)
                self.assertEqual(token, info.token, "the start token must be byte-identical")
                self.assertEqual(comm, info.name, "the name must be `ps -o comm=`'s")
                self.assertEqual(SA._short_name(comm), SA._short_name(info.name))

    def test_the_token_is_ps_lstart_for_single_digit_days_too(self):
        """force — `ps` pads the day (`Nov  2`), `split()` drops the padding; the token rebuilt from
        an epoch must do the same, in every month, whatever this process's locale."""
        for month in range(1, 13):
            t = time.mktime((2026, month, 2, 9, 5, 7, 0, 0, -1))
            tok = SA.lstart_token(int(t))
            want = " ".join(time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(t)).split())
            self.assertEqual(want.replace(" 02 ", " 2 "), tok)
            self.assertNotIn("  ", tok)

    def test_a_dead_pid_and_another_users_process_are_absent(self):
        lookup = SA._libproc_lookup()
        p = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL)
        p.wait()
        self.assertIsNone(lookup(p.pid), "a reaped pid")
        self.assertIsNone(lookup(1), "launchd: another user's process (EPERM) is absent")
        self.assertIsNone(lookup(0))
        self.assertIsNone(lookup(-5))


_SIM = r'''
import os, shlex, subprocess, sys
root, depth, py, src = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]

HOP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hop.sh")
with open(HOP, "w") as fh:
    # one `sh` per hop, each waiting on the next (never exec'd: every sh stays a hop). A script
    # recursing on a counter — not 40 nested `sh -c` quotings, which grow exponentially.
    fh.write('n=$1; shift\nif [ "$n" -gt 0 ]; then /bin/sh "$0" $((n - 1)) "$@"; exit $?; fi\n'
             'exec "$@"\n')

def chain(code):
    env = dict(os.environ, PYTHONPATH=src)
    return subprocess.run(["/bin/sh", HOP, str(depth), py, "-c", code], env=env,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()

print("WRITE", chain("from mokata.session_ancestry import record_session as r; "
                     "print(r(%r, 'S-deep'))" % root))
print("READ", chain("from mokata.session_ancestry import session_for_process as s; "
                    "print(s(%r))" % root))
'''


def _interpreter_image() -> str:
    """The binary this interpreter RUNS AS, named by `ps` from inside a child of it — not
    `sys.executable`. A framework build's `bin/python3` is a stub that re-execs
    `Python.app/Contents/MacOS/Python` (same pid, new image): a stand-in `claude` made from the stub
    would be replaced by that image the moment it starts, and the hop would read as the framework
    path whatever the reader does (the macOS CI runner's Python is such a build; a uv or venv
    interpreter is not). Launch-independent: whatever `sys.executable` is, this is what stays."""
    p = subprocess.run([sys.executable, "-c",
                        "import os,subprocess; print(subprocess.run(['ps','-o','comm=','-p',"
                        "str(os.getpid())],capture_output=True,text=True).stdout.strip())"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    image = p.stdout.strip()
    return image if os.path.isabs(image) and os.path.isfile(image) \
        else os.path.realpath(sys.executable)


def _stand_in(case: unittest.TestCase, dest: str) -> None:
    """`dest` := the interpreter's real image (`_interpreter_image`), hard-linked, else copied (a
    framework image on another volume); a host that allows neither skips, saying so."""
    import shutil
    image = _interpreter_image()
    try:
        os.link(image, dest)
    except OSError:
        try:
            shutil.copy2(image, dest)
        except OSError as exc:
            case.skipTest(f"cannot link or copy the interpreter image {image} here ({exc})")
    # an image that finds its library by a path relative to itself (`@executable_path`, Apple's
    # Command Line Tools build) cannot start from another directory: say so, never a false red
    p = subprocess.run([dest, "-c", "pass"], capture_output=True, text=True,
                       env=dict(os.environ, PYTHONHOME=sys.base_prefix), stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        case.skipTest(f"the interpreter image {image} cannot start relocated: {p.stderr[-300:]}")


@unittest.skipUnless(DARWIN, "the libproc reader is macOS's")
class TheSymlinkedNativeCliStillBinds(unittest.TestCase):
    """force — a `claude` launched through a symlink whose target is named `2.1.311` (the native
    CLI's shape: the target here is the interpreter's real image, `_stand_in`), with its hooks and
    its MCP server each 40 `sh` launchers below it. The writer records the session against its Claude, the
    reader finds it: SESSION-BIND-BY-ANCESTOR end to end. Read by `pbi_name` the hop is `2.1.311`,
    outside `CLAUDE_NAMES`, and the reader answers None."""

    def test_a_chain_of_40_launchers_through_a_symlinked_claude_binds(self):
        from mokata.init import init_repo
        with tempfile.TemporaryDirectory() as t:
            t = os.path.realpath(t)
            versions = os.path.join(t, "versions")
            os.makedirs(versions)
            image = os.path.join(versions, "2.1.311")
            _stand_in(self, image)
            bindir = os.path.join(t, "bin")
            os.makedirs(bindir)
            claude = os.path.join(bindir, "claude")
            os.symlink(image, claude)
            root = os.path.join(t, "repo")
            os.makedirs(root)
            subprocess.run(["git", "init", "-q", root], check=True, capture_output=True,
                           stdin=subprocess.DEVNULL)
            init_repo(root=root, profile="standard", assume_yes=True, out=lambda *_: None)
            sim = os.path.join(t, "sim.py")
            with open(sim, "w", encoding="utf-8") as fh:
                fh.write(_SIM)
            env = dict(os.environ, PYTHONPATH=SRC, PYTHONHOME=sys.base_prefix)
            p = subprocess.run([claude, sim, root, "40", sys.executable, SRC], env=env,
                               capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertEqual(0, p.returncode, p.stderr[-2000:])
            out = dict(line.split(" ", 1) for line in p.stdout.splitlines() if " " in line)
            self.assertEqual("True", out.get("WRITE"), p.stdout + p.stderr[-2000:])
            self.assertEqual("S-deep", out.get("READ"), p.stdout + p.stderr[-2000:])

    def test_the_symlinked_hop_reads_claude_not_its_image(self):
        with tempfile.TemporaryDirectory() as t:
            t = os.path.realpath(t)
            image = os.path.join(t, "2.1.311")
            _stand_in(self, image)
            claude = os.path.join(t, "claude")
            os.symlink(image, claude)
            code = ("import os,sys; sys.path.insert(0, %r)\n"
                    "from mokata import session_ancestry as SA\n"
                    "print(SA._libproc_lookup()(os.getpid()).name)" % SRC)
            p = subprocess.run([claude, "-c", code], capture_output=True, text=True,
                               env=dict(os.environ, PYTHONHOME=sys.base_prefix),
                               stdin=subprocess.DEVNULL)
            self.assertEqual(0, p.returncode, p.stderr[-2000:])
            self.assertEqual(claude, p.stdout.strip())
            self.assertEqual("claude", SA._short_name(p.stdout.strip()))


if __name__ == "__main__":
    unittest.main()
