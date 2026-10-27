"""O22 (0.0.26 cut, hooks-execute · windows · git bash) — the plugin shims find their own folder
when Git Bash hands them a Windows path.

CI: `D:\\a\\mokata-oss\\mokata-oss\\src\\mokata\\hooks\\mokata-hook-launch: line 96:
./mokata-plugin-env.sh: No such file or directory`. `$0` held only backslashes, the `/`-only
dirname answered `.`, and every hook on the plugin route under Git Bash failed (exit 1). The same
code sits in `mokata-launch` (the plugin's CLI launcher; seven l8 tests red on Windows).

Linux has no backslash separator, so the shims' own dirname code is run here on planted values.
"""

import os
import re
import shutil
import subprocess
import unittest

import _support  # noqa: F401

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "mokata", "hooks")
CASES = [
    ("D:\\a\\repo x\\src\\mokata\\hooks\\mokata-hook-launch", "D:\\a\\repo x\\src\\mokata\\hooks"),
    ("C:/Users/me/plugin/src/mokata/hooks/mokata-hook-launch", "C:/Users/me/plugin/src/mokata/hooks"),
    ("D:\\a/b\\c", "D:\\a/b"),
    ("/opt/p/hooks/mokata-hook-launch", "/opt/p/hooks"),
    ("mokata-hook-launch", "."),
    ("/mokata-hook-launch", "/"),
]


def _sh():
    return shutil.which("sh") or shutil.which("bash")


@unittest.skipUnless(_sh(), "no POSIX shell here")
class TheShimsSplitOnBothSeparators(unittest.TestCase):

    def test_the_hook_shim_dirname(self):
        with open(os.path.join(HOOKS, "mokata-hook-launch"), encoding="utf-8") as fh:
            src = fh.read()
        fn = re.search(r"^_dirname\(\) \{.*?^\}", src, re.S | re.M).group(0)
        for given, want in CASES:
            p = subprocess.run([_sh(), "-c", fn + '\n_dirname "$1"; printf %s "$_dirname_out"',
                                "_", given], capture_output=True, text=True, timeout=30)
            self.assertEqual(want, p.stdout, given)

    def test_the_cli_launcher_dirname(self):
        with open(os.path.join(HOOKS, "mokata-launch"), encoding="utf-8") as fh:
            src = fh.read()
        block = re.search(r"^_dn_s=\$\{0%/\*\}.*?^\[ -n \"\$_here\" \] \|\| _here=/$", src,
                          re.S | re.M).group(0).replace("$0", "$1").replace("${0%", "${1%")
        for given, want in CASES:
            p = subprocess.run([_sh(), "-c", block + '\nprintf %s "$_here"', "_", given],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(want, p.stdout, given)


if __name__ == "__main__":
    unittest.main()
