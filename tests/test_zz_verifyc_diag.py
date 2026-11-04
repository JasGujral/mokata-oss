"""VERIFIER probe (lane C round 2) - Windows only. Not product, not shipped: lives on a probe branch.

Attack 1: every alias spelling of the private store that Windows can actually OPEN must be refused
by rel_path / private_path; and every name os.stat rejects with winerror 123 must also be
unopenable (else the new 123 branch is a bypass).
Attack 2: who can read a spill file on Windows (icacls)."""
import os
import subprocess
import sys
import tempfile
import unittest

import _support  # noqa: F401

SECRET = b"VERIFYC-SECRET-7731"


def _short(p):
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(p, buf, 1024)
    return buf.value if n else None


@unittest.skipUnless(os.name == "nt", "windows probe")
class VerifyC(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = os.path.realpath(self._t.name)
        subprocess.run(["git", "init", "-q", self.d], check=True)
        with open(os.path.join(self.d, ".git", "config"), "ab") as fh:
            fh.write(b"# " + SECRET + b"\n")
        os.makedirs(os.path.join(self.d, ".mokata"))
        with open(os.path.join(self.d, ".mokata", "x.txt"), "wb") as fh:
            fh.write(SECRET + b"\n")
        os.makedirs(os.path.join(self.d, "src"))
        with open(os.path.join(self.d, "src", "core.py"), "wb") as fh:
            fh.write(b"a = 1\nb = 2\nc = 3\n")

    def test_every_openable_alias_is_refused(self):
        from mokata.ide import snapshots as S
        from mokata_emit import private_path
        import _winapi
        d = self.d
        gshort = _short(os.path.join(d, ".git"))
        mshort = _short(os.path.join(d, ".mokata"))
        print("\nSHORT NAMES:", gshort, mshort)
        g8 = os.path.basename(gshort) if gshort else "GIT~1"
        m8 = os.path.basename(mshort) if mshort else "MOKATA~1"
        try:
            _winapi.CreateJunction(os.path.join(d, ".mokata"), os.path.join(d, "J"))
            print("JUNCTION J made")
        except OSError as exc:
            print("JUNCTION failed", exc)
        try:
            os.link(os.path.join(d, ".mokata", "x.txt"), os.path.join(d, "src", "hl.txt"))
        except OSError as exc:
            print("HARDLINK failed", exc)
        rels = [
            ".git/config", ".GIT/config", ".git./config", ".GIT /config", ".git../config",
            ".git. ./config",
            ".git::$INDEX_ALLOCATION/config", ".git:$I30:$INDEX_ALLOCATION/config",
            ".mokata::$INDEX_ALLOCATION/x.txt", ".mokata/x.txt::$DATA", ".mokata/x.txt:",
            ".mokata/x.txt.", ".mokata/X.TXT ",
            f"{g8}/config", f"{m8}/x.txt", f"{g8}::$INDEX_ALLOCATION/config",
            f"{m8}::$INDEX_ALLOCATION/x.txt", f"{m8}:$I30:$INDEX_ALLOCATION/x.txt",
            f"{m8}./x.txt", f"{m8} /x.txt", f"{m8}/x.txt::$DATA",
            "J/x.txt", "J::$INDEX_ALLOCATION/x.txt", "J./x.txt", "J:$I30:$INDEX_ALLOCATION/x.txt",
            "src/hl.txt", "src/hl.txt::$DATA", "src/hl.txt.", "src/HL.TXT ",
            "junk:a:b/../.git/config", f"junk:a:b/../{m8}/x.txt", "junk:a:b/../J/x.txt",
            f"nonexist/../{m8}/x.txt", f"src/core.py:3:x/../../{m8}/x.txt",
            "src/core.py:3:x", "src/core.py:3:9999999999", "src/core.py:3",
            "src/core.py::$DATA", "src/core.py:$DATA",
        ]
        abses = [os.path.join(d, r) for r in rels] + [
            "\\\\?\\" + os.path.join(d, ".git", "config"),
            "\\\\?\\" + os.path.join(d, g8, "config"),
            "\\\\?\\" + os.path.join(d, "J", "x.txt"),
            "\\\\?\\" + os.path.join(d, ".mokata", "x.txt."),
            "\\\\.\\" + os.path.join(d, ".mokata", "x.txt"),
            "\\\\?\\" + os.path.join(d, m8, "x.txt"),
        ]
        bypass, bad123 = [], []
        for ab in abses:
            try:
                with open(ab, "rb") as fh:
                    got = fh.read()
                opened = SECRET in got and "secret" or "opened"
            except OSError as exc:
                opened = f"ERR{getattr(exc, 'winerror', exc.errno)}"
            try:
                os.stat(ab)
                st = "ok"
            except OSError as exc:
                st = f"ERR{getattr(exc, 'winerror', exc.errno)}"
            try:
                rp = S.rel_path(d, ab)
            except Exception as exc:          # noqa: BLE001
                rp = f"RAISE {exc!r}"
            try:
                pp = private_path(ab)
            except Exception as exc:          # noqa: BLE001
                pp = f"RAISE {exc!r}"
            served = None
            if isinstance(rp, str) and not rp.startswith("RAISE"):
                from mokata.ide import diffs as D
                got = D.open_contained(d, rp)
                if got:
                    fh, _st = got
                    with fh:
                        served = SECRET in fh.read()
            print(f"ROW {ab[len(d):] if ab.startswith(d) else ab!r:60} open={opened:8} stat={st:7} "
                  f"rel_path={rp!r} private_path={pp!r} served_secret={served}")
            if opened == "secret" and (rp is not None or pp is not True):
                bypass.append(ab)
            if served:
                bypass.append(ab + " (served)")
            if st == "ERR123" and not opened.startswith("ERR"):
                bad123.append(ab)
        self.assertEqual([], bypass, "a store alias that opens was not refused")
        self.assertEqual([], bad123, "a name stat calls malformed still opened")

    def test_who_can_read_a_spill(self):
        from mokata.ide import usages as U
        for base in (self.d, os.getcwd()):
            sd = os.path.join(base, "verifyc-spill")
            w = U.SpillWriter(sd, "explain")
            w.write("secret\n")
            w.close()
            print("\nICACLS dir", base)
            print(subprocess.run(["icacls", base], capture_output=True, text=True).stdout)
            print("ICACLS spill", w.path)
            print(subprocess.run(["icacls", w.path], capture_output=True, text=True).stdout)
            os.remove(w.path)


if __name__ == "__main__":
    unittest.main()
