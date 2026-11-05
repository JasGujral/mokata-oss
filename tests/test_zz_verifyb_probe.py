"""VERIFIER PROBE (lane B) - scratch, probe branch only. Prints evidence; asserts at the end."""
import ctypes
import faulthandler
faulthandler.dump_traceback_later(420, exit=True)
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import _support  # noqa: F401

from mokata.ide import lsp as L
from mokata.quiet_spawn import detached

NT = os.name == "nt"


def _total_processes(job):
    class Acct(ctypes.Structure):
        _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("c", ctypes.c_int64),
                    ("d", ctypes.c_int64), ("TotalPageFaultCount", ctypes.c_uint32),
                    ("TotalProcesses", ctypes.c_uint32), ("ActiveProcesses", ctypes.c_uint32),
                    ("TotalTerminatedProcesses", ctypes.c_uint32)]
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                              ctypes.c_uint32, ctypes.c_void_p]
    a = Acct()
    ok = k32.QueryInformationJobObject(job.handle, 1, ctypes.byref(a), ctypes.sizeof(a), None)
    return (a.TotalProcesses, a.ActiveProcesses) if ok else (None, ctypes.get_last_error())


def _alive(pid):
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tasklist.exe")
    out = subprocess.run([exe, "/FI", f"PID eq {pid}", "/NH"], stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, errors="replace").stdout
    return f" {pid} " in f" {out} "


@unittest.skipUnless(NT, "windows probe")
class Race(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", os.path.join(cls.d, "v")],
                       check=True)
        cls.vpy = os.path.join(cls.d, "v", "Scripts", "python.exe")
        print("VENV PY", cls.vpy, "base", sys.executable, flush=True)

    def test_job_assignment_race_with_a_venv_launcher(self):
        """The product's sequence: Popen(detached) then WinJob(proc). Does the launcher's child
        (the real python) land in our job, and does a grandchild it starts die on terminate?"""
        code = ("import subprocess,sys,time,os;"
                "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
                "print(os.getpid(),p.pid,flush=True);time.sleep(30)")
        escaped, leaks, n = 0, 0, 40
        for i in range(n):
            proc = subprocess.Popen([self.vpy, "-c", code], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **detached())
            job = L.WinJob(proc)
            self.assertTrue(job.ok)
            line = proc.stdout.readline().decode().split()
            real, gc = int(line[0]), int(line[1])
            tot, act = _total_processes(job)
            if tot is not None and tot < 4:      # launcher + real + gc-launcher + gc-real
                escaped += 1
            job.terminate()
            time.sleep(0.5)
            alive = [p for p in (real, gc) if _alive(p)]
            if alive:
                leaks += 1
                for p in alive:
                    subprocess.run(["taskkill", "/F", "/PID", str(p)], capture_output=True)
            print(f"RACE {i}: launcher={proc.pid} real={real} gc={gc} total={tot} active={act} "
                  f"alive_after_terminate={alive}", flush=True)
            proc.stdout.close()
        print(f"RACE SUMMARY: escaped={escaped}/{n} leaks={leaks}/{n}", flush=True)
        self.assertEqual(0, leaks)


@unittest.skipUnless(NT, "windows probe")
class RealInstalls(unittest.TestCase):
    def test_pip_console_script_in_a_venv_is_found_and_runs(self):
        d = tempfile.mkdtemp()
        subprocess.run([sys.executable, "-m", "venv", os.path.join(d, ".venv")], check=True)
        hit = L.find_binary(d, "pip")
        print("PIP HIT", hit, flush=True)
        self.assertIsNotNone(hit)
        out = subprocess.run([hit, "--version"], capture_output=True, text=True, **detached())
        print("PIP RUN", out.returncode, out.stdout.strip(), flush=True)
        self.assertEqual(0, out.returncode)

    def _npm(self, *a, cwd):
        npm = shutil.which("npm")
        r = subprocess.run([npm, *a], cwd=cwd, capture_output=True, text=True, shell=False)
        print("NPM", a, r.returncode, r.stdout[-400:], r.stderr[-400:], flush=True)
        return r

    def _start(self, root, argv):
        srv = L.Server(root, "typescript", "typescript-language-server", argv)
        try:
            srv.start()
            print("TS START OK caps:", sorted(srv.capabilities)[:5], "job.ok", srv.job and srv.job.ok,
                  flush=True)
            return True
        except Exception as exc:  # noqa
            print("TS START FAIL", type(exc).__name__, exc, flush=True)
            return False
        finally:
            srv.stop("probe")

    def test_npm_local_install(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "package.json"), "w") as fh:
            json.dump({"name": "p", "version": "1.0.0"}, fh)
        self._npm("install", "--no-audit", "--no-fund", "typescript", "typescript-language-server", cwd=d)
        f = L.find_server(d, "typescript")
        print("NPM LOCAL FIND", json.dumps(f), flush=True)
        self.assertTrue(f["found"])
        self.assertFalse(any(a.lower().endswith((".cmd", ".bat", ".ps1")) for a in f["argv"]))
        self.assertTrue(self._start(d, f["argv"]))

    def test_pnpm_local_install_junctions(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "package.json"), "w") as fh:
            json.dump({"name": "p", "version": "1.0.0"}, fh)
        npx = shutil.which("npx")
        r = subprocess.run([npx, "-y", "pnpm@9.15.0", "add", "typescript", "typescript-language-server"],
                           cwd=d, capture_output=True, text=True)
        print("PNPM", r.returncode, r.stdout[-400:], r.stderr[-400:], flush=True)
        pkg = os.path.join(d, "node_modules", "typescript-language-server")
        print("PNPM pkg islink", os.path.islink(pkg), "isjunction",
              getattr(os.path, "isjunction", lambda p: None)(pkg), "real", os.path.realpath(pkg), flush=True)
        f = L.find_server(d, "typescript")
        print("PNPM LOCAL FIND", json.dumps(f), flush=True)
        self.assertTrue(f["found"])
        self.assertTrue(self._start(d, f["argv"]))

    def test_npm_global_install(self):
        self._npm("install", "-g", "--no-audit", "--no-fund", "typescript", "typescript-language-server", cwd=tempfile.gettempdir())
        prefix = self._npm("prefix", "-g", cwd=tempfile.gettempdir()).stdout.strip()
        print("NPM PREFIX", prefix, "on PATH:", any(os.path.normcase(p.strip('"')) == os.path.normcase(prefix)
                                                    for p in os.environ["PATH"].split(os.pathsep)), flush=True)
        empty = tempfile.mkdtemp()
        f = L.find_server(empty, "typescript")
        print("NPM GLOBAL FIND", json.dumps(f), flush=True)
        self.assertTrue(f["found"])
        self.assertTrue(self._start(empty, f["argv"]))


if __name__ == "__main__":
    unittest.main()
