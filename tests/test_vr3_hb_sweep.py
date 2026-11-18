"""Round-3 verifier: fresh variants of each must-red class in the M-r2-2 ruling (door_violations must flag each),
and benign lookalikes that must stay quiet (kept few: only ones the ruling names as not-findings)."""
import unittest
import _support  # noqa: F401
from _door_sweep import door_violations

H = "import os\n"
MUST = {
    # slice comparator
    "slice tuple(parts)": "def f(child, parent):\n    return tuple(child.parts)[:len(parent.parts)] == tuple(parent.parts)\n",
    "slice list": "def f(a, b):\n    return list(a)[:len(b)] == list(b)\n",
    "slice str neg": "def f(child, parent):\n    return str(child)[:len(str(parent))] == str(parent)\n",
    "slice `!=` in assert-style": "def f(child, parent):\n    return not (child[:len(parent)] != parent)\n",
    # walk/scandir/iterdir/glob roots
    "walk root startswith": H + "def f(b, p):\n    for r, d, fl in os.walk(b):\n        if p.startswith(r):\n            return r\n",
    "walk root in compare reversed": H + "def f(b):\n    for root, _, _ in os.walk(b):\n        if '/etc' == root:\n            return 1\n",
    "scandir entry path ==": H + "def f(b, x):\n    with os.scandir(b) as it:\n        return [e for e in it if e.path == x]\n",
    "scandir path endswith": H + "def f(b, x):\n    return [e for e in os.scandir(b) if e.path.endswith(x)]\n",
    "iterdir compare": "from pathlib import Path\ndef f(b, x):\n    return [c for c in Path(b).iterdir() if str(c).startswith(x)]\n",
    "rglob in": "from pathlib import Path\ndef f(b, x):\n    return [c for c in Path(b).rglob('*') if x in str(c)]\n",
    "iglob startswith": "import glob\ndef f(b, x):\n    return [g for g in glob.iglob(b + '/**') if g.startswith(x)]\n",
    "glob.glob compare": "import glob\ndef f(b, x):\n    return [g for g in glob.glob(b) if g == x]\n",
    "pathlib glob relative_to": "from pathlib import Path\ndef f(b, x):\n    return [c for c in Path(b).glob('*') if c.parent == x]\n",
    # fnmatch / glob / regex
    "fnmatch.filter": H + "import fnmatch\ndef f(p, names):\n    return fnmatch.fnmatch(os.path.abspath(p), names)\n",
    "from fnmatch import fnmatch": H + "from fnmatch import fnmatch\ndef f(p, pat):\n    return fnmatch(os.path.realpath(p), pat)\n",
    "fnmatch alias module": H + "import fnmatch as fm\ndef f(p, pat):\n    return fm.fnmatch(os.path.realpath(p), pat)\n",
    "re.match direct": H + "import re\ndef f(p, b):\n    return re.match(re.escape(b), os.path.realpath(p))\n",
    "re.fullmatch": H + "import re\ndef f(p, pat):\n    return re.fullmatch(pat, os.path.normpath(p))\n",
    "re.search compiled var": H + "import re\nRX = re.compile('^/x')\ndef f(p):\n    return RX.search(os.path.realpath(p))\n",
    "re.findall": H + "import re\ndef f(p, pat):\n    return re.findall(pat, os.path.realpath(p))\n",
    "PurePath.match": "from pathlib import PurePath\ndef f(p, pat):\n    return PurePath(p).match(pat)\n",
    "Path.full_match-like via match": "from pathlib import Path\ndef f(p, q):\n    return Path(p).resolve().match(q)\n",
    # match on parts
    "match parts star": "from pathlib import Path\ndef f(p):\n    match Path(p).resolve().parts:\n        case ('/', 'etc', *rest):\n            return rest\n",
    "match split": H + "def f(p):\n    match os.path.realpath(p).split(os.sep):\n        case ['', 'etc', *_]:\n            return 1\n",
    "match tainted str guard": H + "def f(p, K):\n    match os.path.abspath(p):\n        case x if x.startswith(K):\n            return 1\n",
    "match tuple of parts": "from pathlib import Path\ndef f(p):\n    match tuple(Path(p).resolve().parts):\n        case ('/', 'a'):\n            return 1\n",
}


# FINDINGS (round 3 verifier): shapes the ruling's slice-comparator class names, that stay quiet. Each is a
# real way to write the same containment test; the sweep only reds a comparison that is DIRECTLY a `return`.
MISSED = {
    "slice in an if": "def f(child, parent):\n    if child[:len(parent)] == parent:\n        return True\n    return False\n",
    "slice behind a length guard (and)": "def f(child, parent):\n    return len(child) >= len(parent) and child[:len(parent)] == parent\n",
    "slice in a ternary": "def f(child, parent):\n    return True if child[:len(parent)] == parent else False\n",
    "slice, length pre-bound": "def f(child, parent):\n    n = len(parent)\n    return child[:n] == parent\n",
    "slice assigned then returned": "def f(child, parent):\n    ok = child[:len(parent)] == parent\n    return ok\n",
    "os.fwalk root compare": H + "def f(b, x):\n    for r, d, fl, fd in os.fwalk(b):\n        if r == x:\n            return 1\n",
    "fnmatch.filter": H + "import fnmatch\ndef f(b, pat):\n    return fnmatch.filter([os.path.realpath(b)], pat)\n",
}


class R3Sweep(unittest.TestCase):
    def test_fresh_variants_red(self):
        missed = [k for k, src in MUST.items() if not door_violations({"brand/new.py": src})]
        self.assertEqual([], missed)

    def test_missed_variants_red(self):
        """Round-3 verifier M-r3-1 (expectedFailure removed in the round-3 close-out: the slice Compare is matched
        wherever it sits, and `os.fwalk` / `fnmatch.filter` are sinks)."""
        missed = [k for k, src in MISSED.items() if not door_violations({"brand/new.py": src})]
        self.assertEqual([], missed)


if __name__ == "__main__":
    unittest.main()
