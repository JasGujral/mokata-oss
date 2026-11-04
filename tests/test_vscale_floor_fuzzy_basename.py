"""0.0.27 V-SCALE — ⌘P lists the file whose exact name you typed, on the pure-Python floor.

Found on kubernetes v1.37.2 (31k files): typing `kubelet.go` never listed `pkg/kubelet/kubelet.go`,
THE kubelet. The floor scored the leftmost subsequence match only, so the `k` of `pkg` broke the
run and twenty other paths — `test/e2e/dra/test-driver/app/kubeletplugin.go` among them — outranked
an exact basename match; ⌘P shows 12. The floor is what every install without rapidfuzz runs.

The candidates below are the twenty that outranked it there, verbatim, plus the file itself.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import sys
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.ide import nav

OUTRANKED_IT = [
    "test/e2e/node/kubelet.go", "test/e2e/common/node/kubelet.go",
    "test/e2e_node/services/kubelet.go", "test/e2e_node_windows/services/kubelet.go",
    "test/e2e/windows/gmsa_kubelet.go", "cmd/kubelet/kubelet.go",
    "cmd/kubeadm/app/cmd/phases/init/kubelet.go", "cmd/kubeadm/app/cmd/phases/join/kubelet.go",
    "cmd/kubeadm/app/componentconfigs/kubelet.go", "test/e2e/node/kubelet_perf.go",
    "test/e2e/node/kubelet_authz.go", "test/e2e/windows/kubelet_stats.go",
    "test/e2e_node/kubelet_tls_test.go", "test/e2e/node/kubelet_in_userns.go",
    "test/e2e_node/util_kubeletconfig.go", "test/e2e_node/kubelet_config_dir_test.go",
    "test/e2e_node/kubelet_server_tls_test.go", "test/e2e/common/node/kubelet_etc_hosts.go",
    "test/e2e_node_windows/util_kubeletconfig.go", "test/e2e/dra/test-driver/app/kubeletplugin.go",
]
THE_FILE = "pkg/kubelet/kubelet.go"


class FloorListsTheExactName(unittest.TestCase):
    def floor(self, q, cands):
        with mock.patch.dict(sys.modules, {"rapidfuzz": None}):
            rows, matcher = nav.fuzzy(q, cands)
        self.assertEqual("python-floor", matcher)
        return [p for p, _s in rows]

    def test_typing_the_exact_file_name_lists_it_among_the_shown_rows(self):
        shown = self.floor("kubelet.go", OUTRANKED_IT + [THE_FILE])
        self.assertIn(THE_FILE, shown)

    def test_an_exact_basename_outranks_a_name_that_only_contains_the_letters(self):
        shown = self.floor("kubelet.go", OUTRANKED_IT + [THE_FILE])
        self.assertLess(shown.index(THE_FILE),
                        shown.index("test/e2e/dra/test-driver/app/kubeletplugin.go")
                        if "test/e2e/dra/test-driver/app/kubeletplugin.go" in shown else len(shown))
        self.assertLess(shown.index(THE_FILE), shown.index("test/e2e/windows/gmsa_kubelet.go"))

    def test_which_files_match_is_unchanged(self):
        """Scoring moved; the match rule (a subsequence anywhere in the path) did not."""
        self.assertIsNone(nav._floor_score("zq", "pkg/kubelet/kubelet.go"))
        self.assertIsNotNone(nav._floor_score("pkgkg", "pkg/kubelet/kubelet.go"))
        self.assertIsNotNone(nav._floor_score("kubelet.go", THE_FILE))


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
