"""0.0.26 Lane R — R7 (doc 85: no unverified performance claim; doc 113 §6 exit criterion 12 R7).

Launch copy may make NO performance claim — "N% fewer tokens", "2× faster", "30% higher pass rate" —
unless the A/B benchmark (`bench/ab`, scored by recorded test outcomes) backs it. A claim is backed
when the SAME paragraph cites a committed result under `bench/ab/results/`. Everything else is red.

The scope is the product copy a reader meets: README, the docs site, the landing page, llms.txt.
Dated records (the changelog, release notes) are claims about their own release and are not graded.
"""
import glob

import _internal_scope  # noqa: E402  (test helper, same directory)
import os
import re
import unittest

import _support  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CITATION = "bench/ab/results/"

_NUM = r"\d+(?:\.\d+)?\s*(?:%|x|×)\+?"
_PERF = (r"(?:fewer|less|more|faster|better|cheaper|higher|lower|reduction|reductions|"
         r"improvement|savings?|speed-?ups?|smaller|quicker)")
CLAIM = re.compile(
    rf"\b{_NUM}\s+(?:[\w-]+\s+){{0,2}}{_PERF}\b"            # "60%+ token savings", "2× faster"
    rf"|\b(?:cuts?|reduces?|saves?|improves?|lowers?|boosts?|increases?|speeds?\s+up|{_PERF})"
    rf"\s+(?:[\w-]+\s+){{0,3}}by\s+{_NUM}",                 # "cuts review time by 40%"
    re.IGNORECASE)


def product_copy(root=ROOT):
    """The copy a READER meets. ⚠ Scoped to what SHIPS, derived from `sync-public.sh`'s
    `--exclude` set (O16): a dev tree also carries `docs/build/`, `docs/talks/` and friends —
    internal records that quote a number precisely because it was once claimed — and grading
    those turns a release-blocking guard into noise about history. On the public mirror the
    script is absent and every path is shipped, so the whole glob stands."""
    files = [os.path.join(root, "README.md"), os.path.join(root, "llms.txt")]
    files += glob.glob(os.path.join(root, "docs", "**", "*.md"), recursive=True)
    files += glob.glob(os.path.join(root, "overrides", "**", "*.html"), recursive=True)
    dated = ("changelog", "release")
    return sorted(f for f in files if os.path.isfile(f)
                  and not any(d in os.path.basename(f).lower() for d in dated)
                  and _shipped(root, f))


def _shipped(root, path):
    """Is `path` part of the published tree? (see `_internal_scope`)"""
    return _internal_scope.is_shipped(root, path)


def unbacked_claims(text):
    """Every claim in `text` whose paragraph cites no benchmark result: [(line, claim)]."""
    out = []
    offset = 0
    for para in re.split(r"\n\s*\n", text):
        if CITATION not in para:
            for m in CLAIM.finditer(para):
                line = text.count("\n", 0, offset + m.start()) + 1
                out.append((line, m.group(0)))
        offset += len(para) + 2
    return out


class NoUnbackedPerformanceClaim(unittest.TestCase):

    def test_R7_product_copy_makes_no_perf_claim_without_a_benchmark_result(self):
        found = []
        for path in product_copy():
            with open(path, encoding="utf-8") as fh:
                for line, claim in unbacked_claims(fh.read()):
                    found.append(f"{os.path.relpath(path, ROOT)}:{line}: {claim!r}")
        self.assertEqual([], found,
                         "performance claim(s) with no A/B benchmark result cited in the same "
                         "paragraph (run `python -m bench.ab run --agent live --yes`, commit "
                         "bench/ab/results/<run>/, cite it — or say it without the number)")

    def test_R7_the_guard_fires_on_a_claim_and_accepts_a_cited_one(self):
        for text in ("mokata uses 60%+ fewer tokens.", "Agents ship 2× faster with mokata.",
                     "It cuts review time by 40%.", "a 30% higher pass rate"):
            with self.subTest(text=text):
                self.assertTrue(unbacked_claims(text), text)
        cited = "mokata solved 30% more tasks ([results](bench/ab/results/2026-11-01/RESULTS.md))."
        self.assertEqual([], unbacked_claims(cited))
        for honest in ("the hook answers in 50 ms", "Python 3.12 or newer", "100% local",
                       "reports its own saved_pct for your repo"):
            with self.subTest(honest=honest):
                self.assertEqual([], unbacked_claims(honest))

    def test_R7_the_scope_covers_the_launch_surfaces(self):
        from mokata.repo_paths import name_of
        rel = {name_of(p, ROOT) for p in product_copy()}
        for must in ("README.md", "docs/index.md", "overrides/home.html"):
            self.assertIn(must, rel)
        self.assertFalse(any("changelog" in r.lower() for r in rel))


if __name__ == "__main__":
    unittest.main()
