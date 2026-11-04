"""VERIFIER PROBE B3 (scratch, probe branch only). Verifier's OWN attack code against 7b924e6.
Instruments are independent of the product's: liveness via OpenProcess+GetExitCodeProcess, job
membership via QueryInformationJobObject, handle counts via GetProcessHandleCount."""
import faulthandler
faulthandler.dump_traceback_later(480, exit=True)
import ctypes
import gc
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import _support  # noqa: F401

from mokata.ide import lsp as L
from mokata.ide import review_context as RC

NT = os.name == "nt"
HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = os.path.join(HERE, "_fake_lsp_server.py")


def P(*a):
    print("VB3", *a, flush=True)


if NT:
    from ctypes import wintypes
    K32 = ctypes.WinDLL("kernel32", use_last_error=True)
    K32.OpenProcess.restype = wintypes.HANDLE
    K32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    K32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    K32.CloseHandle.argtypes = [wintypes.HANDLE]
    K32.GetCurrentProcess.restype = wintypes.HANDLE
    K32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    K32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                              wintypes.DWORD, ctypes.c_void_p]
    K32.GetConsoleWindow.restype = wintypes.HWND


def alive(pid):
    """OpenProcess-based: True while the process has not exited."""
    h = K32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        err = ctypes.get_last_error()
        return err != 87                           # 87 = invalid parameter = no such pid
    try:
        code = wintypes.DWORD()
        if not K32.GetExitCodeProcess(h, ctypes.byref(code)):
            return True
        return code.value == 259                   # STILL_ACTIVE
    finally:
        K32.CloseHandle(h)


def wait_dead(pid, within=60.0):
    end = time.monotonic() + within
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return False


def handles():
    n = wintypes.DWORD()
    K32.GetProcessHandleCount(K32.GetCurrentProcess(), ctypes.byref(n))
    return n.value


def job_counts(job):
    class Acct(ctypes.Structure):
        _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("c", ctypes.c_int64),
                    ("d", ctypes.c_int64), ("TotalPageFaultCount", ctypes.c_uint32),
                    ("TotalProcesses", ctypes.c_uint32), ("ActiveProcesses", ctypes.c_uint32),
                    ("TotalTerminatedProcesses", ctypes.c_uint32)]
    a = Acct()
    ok = K32.QueryInformationJobObject(job.handle, 1, ctypes.byref(a), ctypes.sizeof(a), None)
    return (a.TotalProcesses, a.ActiveProcesses) if ok else (None, ctypes.get_last_error())


def kill(pid):
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)


@unittest.skipUnless(NT, "windows probe")
class A_LauncherEscape(unittest.TestCase):
    """Round-2 race probe, re-run against spawn_jobbed. Chain: venv launcher -> real python ->
    grandchild launcher (sys.executable inside the venv IS the launcher) -> grandchild real.
    The real python also TRIES a CREATE_BREAKAWAY_FROM_JOB child: it must be refused."""

    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="vb3-")
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", os.path.join(cls.d, "v")],
                       check=True)
        cls.vpy = os.path.join(cls.d, "v", "Scripts", "python.exe")
        P("VENV", cls.vpy, "base", sys.executable)

    def test_50_runs_nothing_escapes_and_breakaway_is_refused(self):
        code = ("import subprocess,sys,time,os\n"
                "gc=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])\n"
                "try:\n"
                "    b=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],creationflags=0x01000000)\n"
                "    bp=b.pid\n"
                "except OSError as e:\n"
                "    bp=-(e.winerror or 1)\n"
                "print(os.getpid(),gc.pid,bp,flush=True)\n"
                "line=sys.stdin.readline()\n"
                "print('echo:'+line.strip(),flush=True)\n"
                "time.sleep(120)\n")
        leaks, escaped, broke, n = [], 0, 0, 50
        for i in range(n):
            proc, job = L.spawn_jobbed([self.vpy, "-c", code], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.assertTrue(job.ok)
            line = proc.stdout.readline().decode().split()
            real, gcpid, bp = int(line[0]), int(line[1]), int(line[2])
            proc.stdin.write(b"ping\n"); proc.stdin.flush()
            echo = proc.stdout.readline().decode().strip()
            tot, act = job_counts(job)
            if tot is None or tot < 4:
                escaped += 1
            if bp > 0:
                broke += 1
            job.terminate()
            proc.wait(timeout=60)
            pids = [proc.pid, real, gcpid] + ([bp] if bp > 0 else [])
            left = [p for p in pids if not wait_dead(p, 30)]
            if left:
                leaks.append((i, left))
                for p in left:
                    kill(p)
            if i < 3 or left or tot is None or tot < 4:
                P(f"ESCAPE {i}: launcher={proc.pid} real={real} gc={gcpid} breakaway={bp} "
                  f"echo={echo!r} job total={tot} active={act} left={left}")
            self.assertEqual("echo:ping", echo, "stdin/stdout pipes do not work through spawn_jobbed")
            proc.stdin.close(); proc.stdout.close()
        P(f"ESCAPE SUMMARY runs={n} escaped={escaped} breakaway_succeeded={broke} leaks={leaks}")
        self.assertEqual([], leaks)
        self.assertEqual(0, escaped)
        self.assertEqual(0, broke)

    def test_no_console_window_and_own_group(self):
        code = ("import ctypes,sys;print(ctypes.windll.kernel32.GetConsoleWindow() or 0,flush=True)")
        proc, job = L.spawn_jobbed([self.vpy, "-c", code], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out = proc.stdout.read().decode().strip()
        proc.wait(timeout=60)
        job.terminate()
        P("CONSOLE WINDOW of a spawn_jobbed child:", out)
        self.assertEqual("0", out)


@unittest.skipUnless(NT, "windows probe")
class B_Lifetimes(unittest.TestCase):
    def test_handles_do_not_grow_over_many_spawns(self):
        def one():
            proc, job = L.spawn_jobbed([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            proc.wait(timeout=60)
            job.terminate()
            del proc
        for _ in range(10):
            one()
        gc.collect()
        h0 = handles()
        for _ in range(300):
            one()
        gc.collect()
        h1 = handles()
        P(f"HANDLES spawn_jobbed x300: before={h0} after={h1} delta={h1 - h0}")
        self.assertLess(h1 - h0, 30)

    def test_handles_over_run_tests_and_server_cycles(self):
        root = tempfile.mkdtemp(prefix="vb3-rt-")
        script = os.path.join(root, "t.py")
        with open(script, "w") as fh:
            fh.write("import sys; sys.exit(0)\n")
        RC.run_tests(root, [sys.executable, script], ["t1"], environ=dict(os.environ))
        gc.collect()
        h0 = handles()
        for _ in range(60):
            RC.run_tests(root, [sys.executable, script], ["t1"], environ=dict(os.environ))
        gc.collect()
        h1 = handles()
        P(f"HANDLES run_tests x60: before={h0} after={h1} delta={h1 - h0}")
        srv_root = tempfile.mkdtemp(prefix="vb3-srv-")
        def cycle():
            s = L.Server(srv_root, "python", "fake", [sys.executable, "-I", FAKE])
            s.start()
            s.stop("probe")
            s.stopped.wait(30)
        cycle()
        time.sleep(1)
        gc.collect()
        h2 = handles()
        for _ in range(30):
            cycle()
        time.sleep(2)
        gc.collect()
        h3 = handles()
        P(f"HANDLES Server start/stop x30: before={h2} after={h3} delta={h3 - h2}")
        self.assertLess(h1 - h0, 30)
        self.assertLess(h3 - h2, 60)

    def test_a_live_server_is_not_killed_by_its_job_early(self):
        srv_root = tempfile.mkdtemp(prefix="vb3-live-")
        s = L.Server(srv_root, "python", "fake", [sys.executable, "-I", FAKE])
        s.start()
        gc.collect()
        time.sleep(5)
        gc.collect()
        a = s.alive
        P("LIVE SERVER after 5 s + gc:", a, "job.ok", s.job.ok, "counts", job_counts(s.job))
        pid = s.proc.pid
        s.stop("probe")
        self.assertTrue(a)
        self.assertTrue(wait_dead(pid, 30))

    def test_resume_failure_leaves_no_suspended_process(self):
        made = []
        real_popen = subprocess.Popen

        def popen(*a, **k):
            p = real_popen(*a, **k)
            made.append(p)
            return p
        with mock.patch("mokata.ide.lsp.subprocess.Popen", side_effect=popen), \
                mock.patch("mokata.ide.lsp._resume_process", side_effect=OSError("forced")):
            with self.assertRaises(OSError):
                L.spawn_jobbed([sys.executable, "-c", "import time;time.sleep(120)"],
                               stdin=subprocess.DEVNULL)
        pid = made[0].pid
        dead = wait_dead(pid, 30)
        P("RESUME-FAIL pid", pid, "dead:", dead)
        self.assertTrue(dead)

    def test_run_tests_leftover_dies_40_runs(self):
        d = tempfile.mkdtemp(prefix="vb3-left-")
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", os.path.join(d, "v")], check=True)
        vpy = os.path.join(d, "v", "Scripts", "python.exe")
        script = os.path.join(d, "leaver.py")
        with open(script, "w") as fh:
            fh.write("import subprocess,sys,os\n"
                     "g=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])\n"
                     "open(os.path.join(os.path.dirname(__file__),'pid_'+sys.argv[1]),'w').write(str(g.pid))\n")
        left = []
        for i in range(40):
            tid = f"t{i}"
            got = RC.run_tests(d, [vpy, script], [tid], environ=dict(os.environ))
            pid = int(open(os.path.join(d, "pid_" + tid)).read())
            if not wait_dead(pid, 30):
                left.append((i, pid))
                kill(pid)
            if i == 0:
                P("RUN_TESTS first:", got["tests"])
        P("RUN_TESTS LEFTOVER SUMMARY 40 runs, survivors:", left)
        self.assertEqual([], left)


@unittest.skipUnless(NT, "windows probe")
class C_Uris(unittest.TestCase):
    def test_real_windows_uri_forms(self):
        root = tempfile.mkdtemp(prefix="vb3-uri-")
        os.makedirs(os.path.join(root, "src"))
        with open(os.path.join(root, "src", "a.py"), "w") as fh:
            fh.write("def parse(t):\n    return t\n")
        with open(os.path.join(os.path.dirname(root), "vb3_outside.py"), "w") as fh:
            fh.write("SECRET = 1\n")
        long_root = os.path.realpath(root)
        P("ROOT given", root, "real", long_root)
        p = long_root.replace("\\", "/")          # C:/Users/.../vb3-uri-x
        drive, rest = p[0], p[2:]
        rest_q = rest.replace(" ", "%20")
        cases = {
            "vscode lower %3A": f"file:///{drive.lower()}%3A{rest_q}/src/a.py",
            "vscode lower %3a": f"file:///{drive.lower()}%3a{rest_q}/src/a.py",
            "upper plain": f"file:///{drive.upper()}:{rest_q}/src/a.py",
            "localhost": f"file://localhost/{drive.lower()}:{rest_q}/src/a.py",
            "LOCALHOST": f"file://LOCALHOST/{drive.lower()}:{rest_q}/src/a.py",
            "%2e%2e escape": f"file:///{drive}%3A{rest_q}/src/%2e%2e/%2e%2e/vb3_outside.py",
            "%2F escape": f"file:///{drive}%3A{rest_q}/src%2F..%2F..%2Fvb3_outside.py",
            "%5C escape": f"file:///{drive}%3A{rest_q}/src%5C..%5C..%5Cvb3_outside.py",
            "%00": f"file:///{drive}%3A{rest_q}/src/a.py%00.txt",
            "unc 4 slash": "file:////127.0.0.1/c$/x.py",
            "%5C%5C unc": "file:///%5C%5C127.0.0.1%5Cc$%5Cx.py",
            "\\\\?\\ long": f"file:///%5C%5C%3F%5C{drive}:{rest_q.replace('/', '%5C')}%5Csrc%5Ca.py",
            "drive-relative": f"file:///{drive}:src/a.py",
            "8.3 root?": "",
            "CON device": f"file:///{drive}%3A{rest_q}/src/CON",
            "ADS": f"file:///{drive}%3A{rest_q}/src/a.py%3A%3A%24DATA",
        }
        results = {}
        for name, uri in cases.items():
            if not uri:
                continue
            fp = L.from_uri(uri)
            loc = L.location(root, {"uri": uri, "range": {"start": {"line": 0, "character": 4},
                                                         "end": {"line": 0, "character": 9}}}, L._Lines())
            results[name] = (fp, loc["path"], loc["outside"], loc["snippet"])
            P(f"URI {name}: {uri} -> from_uri={fp!r} path={loc['path']!r} outside={loc['outside']} snippet={loc['snippet']!r}")
        # a root given with a lower-case drive (as a user may type it)
        lc_root = long_root[0].lower() + long_root[1:]
        loc = L.location(lc_root, {"uri": cases["vscode lower %3A"], "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}}}, L._Lines())
        P("LOWER-CASE ROOT:", lc_root, "->", loc["path"], loc["outside"], repr(loc["snippet"]))
        short = tempfile.gettempdir()
        P("TEMPDIR (8.3?)", short)
        for name in ("vscode lower %3A", "vscode lower %3a", "upper plain", "localhost"):
            self.assertEqual(("src/a.py", False, "def parse(t):"), results[name][1:], name)
        for name in ("%2e%2e escape", "%2F escape", "%5C escape"):
            self.assertTrue(results[name][2], f"{name} reads as inside")
            self.assertEqual("", results[name][3], f"{name} was READ")
        for name in ("%00", "unc 4 slash", "%5C%5C unc", "\\\\?\\ long", "drive-relative", "LOCALHOST"):
            self.assertIsNone(results[name][0], name)
        self.assertFalse(loc["outside"])


@unittest.skipUnless(NT, "windows probe")
class D_Runners(unittest.TestCase):
    def test_js_runner_on_windows(self):
        d = tempfile.mkdtemp(prefix="vb3-js-")
        with open(os.path.join(d, "package.json"), "w") as fh:
            json.dump({"name": "p", "version": "1.0.0",
                       "scripts": {"test": "node -e \"console.log('ran', process.argv.slice(1))\""}}, fh)
        for argv in (["npm", "test", "--"], ["npx", "--version"], ["yarn", "test"], ["pnpm", "test"],
                     ["mvn", "test"]):
            got = RC.run_tests(d, argv, ["t1"], environ=dict(os.environ))
            P("RUNNER", argv, "->", got["tests"][0])
        npm_cmd = shutil.which("npm")
        P("shutil.which npm =", npm_cmd)
        got = RC.run_tests(d, [npm_cmd, "test", "--"], ["t1"], environ=dict(os.environ))
        P("RUNNER explicit", npm_cmd, "->", got["tests"][0], "output tail:", got["output"][-300:])
        node = shutil.which("node")
        cli = os.path.join(os.path.dirname(node), "node_modules", "npm", "bin", "npm-cli.js")
        P("node", node, "npm-cli.js exists:", os.path.isfile(cli), cli)
        got = RC.run_tests(d, ["node", cli, "test", "--"], ["t1"], environ=dict(os.environ))
        P("RUNNER node npm-cli.js ->", got["tests"][0], "output tail:", got["output"][-300:])

    def test_a_user_server_installed_only_as_a_cmd_shim(self):
        d = tempfile.mkdtemp(prefix="vb3-shim-")
        for n in ("myls.cmd", "myls.bat", "myls.ps1"):
            open(os.path.join(d, n), "w").close()
        root = tempfile.mkdtemp(prefix="vb3-shimroot-")
        langs = {"mylang": {"exts": [".my"], "id": "mylang",
                            "servers": [["myls", [], "install myls"]], "user": True}}
        with mock.patch.dict(os.environ, {"PATH": d + os.pathsep + os.environ["PATH"]}), \
                mock.patch("mokata.ide.lsp.languages", return_value=langs):
            f = L.find_server(root, "mylang")
        P("CMD-ONLY USER SERVER:", json.dumps(f))


if __name__ == "__main__":
    unittest.main()
