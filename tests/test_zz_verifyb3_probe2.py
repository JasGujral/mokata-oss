"""VERIFIER PROBE B3 #2: does CREATE_BREAKAWAY_FROM_JOB take a child (and its tree) OUT of the
spawn_jobbed job? Own instruments: IsProcessInJob, OpenProcess/GetExitCodeProcess, the job's
basic limit flags as set."""
import faulthandler
faulthandler.dump_traceback_later(420, exit=True)
import ctypes
import os
import subprocess
import sys
import tempfile
import time
import unittest
from ctypes import wintypes

import _support  # noqa: F401
from mokata.ide import lsp as L

NT = os.name == "nt"


def P(*a):
    print("VB3B", *a, flush=True)


if NT:
    K32 = ctypes.WinDLL("kernel32", use_last_error=True)
    K32.OpenProcess.restype = wintypes.HANDLE
    K32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    K32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    K32.CloseHandle.argtypes = [wintypes.HANDLE]
    K32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    K32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                              wintypes.DWORD, ctypes.c_void_p]


def in_job(pid, job_handle):
    h = K32.OpenProcess(0x1000, False, pid)
    if not h:
        return f"open-failed {ctypes.get_last_error()}"
    try:
        b = wintypes.BOOL()
        if not K32.IsProcessInJob(h, job_handle, ctypes.byref(b)):
            return f"query-failed {ctypes.get_last_error()}"
        return bool(b.value)
    finally:
        K32.CloseHandle(h)


def in_any_job(pid):
    h = K32.OpenProcess(0x1000, False, pid)
    if not h:
        return "open-failed"
    try:
        b = wintypes.BOOL()
        K32.IsProcessInJob(h, None, ctypes.byref(b))
        return bool(b.value)
    finally:
        K32.CloseHandle(h)


def alive(pid):
    h = K32.OpenProcess(0x1000, False, pid)
    if not h:
        return ctypes.get_last_error() != 87
    try:
        c = wintypes.DWORD()
        K32.GetExitCodeProcess(h, ctypes.byref(c))
        return c.value == 259
    finally:
        K32.CloseHandle(h)


def wait_dead(pid, within=30.0):
    end = time.monotonic() + within
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return False


def limit_flags(job):
    buf = (ctypes.c_ubyte * 256)()
    ret = wintypes.DWORD()
    ok = K32.QueryInformationJobObject(job.handle, 9, buf, 144, ctypes.byref(ret))
    return int.from_bytes(bytes(buf[16:20]), "little") if ok else f"q-failed {ctypes.get_last_error()}"


DEEP = ("import subprocess,sys,os,time\n"
        "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])\n"
        "open(sys.argv[1]+'.t','w').write('%d %d'%(os.getpid(),c.pid));os.replace(sys.argv[1]+'.t',sys.argv[1])\n"
        "time.sleep(120)\n")


@unittest.skipUnless(NT, "windows probe")
class Breakaway(unittest.TestCase):
    def test_breakaway_from_the_spawn_jobbed_job(self):
        d = tempfile.mkdtemp(prefix="vb3b-")
        deep = os.path.join(d, "deep.py")
        open(deep, "w").write(DEEP)
        for interp_name, interp in (("base", sys.executable),):
            for flags_name, flags in (("BREAKAWAY", 0x01000000), ("BREAKAWAY|NEW_GROUP|NO_WINDOW", 0x01000000 | 0x200 | 0x08000000)):
                pf = os.path.join(d, f"pids-{flags_name.replace('|', '_')}")
                leader = ("import subprocess,sys,time\n"
                          f"b=subprocess.Popen([{interp!r},{deep!r},{pf!r}],creationflags={flags})\n"
                          "print(b.pid,flush=True);time.sleep(120)\n")
                proc, job = L.spawn_jobbed([interp, "-c", leader], stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                P(flags_name, "job.ok", job.ok, "job LimitFlags", hex(limit_flags(job)) if isinstance(limit_flags(job), int) else limit_flags(job))
                first = proc.stdout.readline().decode().strip()
                end = time.monotonic() + 60
                while not os.path.exists(pf) and time.monotonic() < end:
                    time.sleep(0.05)
                bp, bc = map(int, open(pf).read().split())
                P(flags_name, "leader", proc.pid, "in our job:", in_job(proc.pid, job.handle),
                  "| breakaway", bp, "(printed", first, ") in our job:", in_job(bp, job.handle),
                  "in any job:", in_any_job(bp), "| its child", bc, "in our job:", in_job(bc, job.handle))
                job.terminate()
                proc.wait(timeout=60)
                res = {pid: wait_dead(pid, 20) for pid in (proc.pid, bp, bc)}
                P(flags_name, "dead after terminate:", res)
                for pid, dead in res.items():
                    if not dead:
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
                proc.stdout.close()
                self.assertTrue(all(res.values()), f"{flags_name}: survivors {res}")

    def test_the_outer_job_of_this_runner(self):
        P("this test process in any job:", in_any_job(os.getpid()))


if __name__ == "__main__":
    unittest.main()
