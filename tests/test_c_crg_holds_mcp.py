"""0.0.29 C1 — D15 `CRG-PULLS-MCP-2`: no install seam can move `mcp` past mokata's ceiling.

code-review-graph 2.4.0 accepts `fastmcp<5`, and fastmcp 4.x needs mcp 2.x — so a resolver asked for
code-review-graph ALONE picks mcp 2.3 and breaks `mokata-mcp` (measured: the real `uv pip compile`
dry-runs are in `docs/build/handoff/0029-evidence/C1/d15-*.txt`). With mokata's own `mcp>=1.30,<2` in
the same request it resolves fastmcp 3.4.11 + mcp 1.30.0, and code-review-graph 2.4.0 answers real
queries on that set (same evidence). So:

  * the joint routes (`uv tool install "mokata[full]"`, `uvx --from`, `pipx install --force
    "mokata[full]"`, `pip install "mokata[full]"`) carry it as mokata's own base requirement;
  * every NON-joint seam — pip / uv pip / pipx inject of an extra's requirements, the doctor's
    `only=` line, `install_extra(...)` — appends mokata's own `mcp` requirement to the argv;
  * code-review-graph is pinned to the release proven on that set.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata import install_route as R

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
BASE_MCP = next(r for r in PYPROJECT["dependencies"] if r.replace(" ", "").startswith("mcp"))

#: mokata's Requires-Dist as installed metadata spells it (base + extras), built from pyproject.
REQUIRES = list(PYPROJECT["dependencies"]) + [
    f"{r.split(';')[0].strip()} ; extra == \"{extra}\""
    + (f" and ({r.split(';', 1)[1].strip()})" if ";" in r else "")
    for extra, reqs in PYPROJECT["optional-dependencies"].items() for r in reqs]


def _which(*names):
    return lambda n: f"/bin/{n}" if n in names else None


def _crg(argv):
    return any(a.startswith("code-review-graph") for a in argv)


class EverySeamHoldsMcp(unittest.TestCase):
    def assertHolds(self, argv, seam):
        self.assertIsNotNone(argv, seam)
        self.assertIn(BASE_MCP, argv, f"{seam}: mokata's own mcp requirement must ride along")

    def test_pip_extra_line(self):
        p = R.plan_extra_install("full", prefix="/nowhere", executable="/v/bin/python",
                                 which=_which(), has_pip=True, requires=REQUIRES, platform="linux")
        self.assertIn(BASE_MCP, p.display)

    def test_uv_pip_extra_line(self):
        p = R.plan_extra_install("full", prefix="/nowhere", executable="/v/bin/python",
                                 which=_which("uv"), has_pip=False, requires=REQUIRES,
                                 platform="linux")
        self.assertIn(BASE_MCP, p.display)

    def test_the_only_line_for_code_review_graph(self):
        p = R.plan_extra_install("full", prefix="/nowhere", executable="/v/bin/python",
                                 which=_which(), has_pip=True, requires=REQUIRES,
                                 platform="linux", only=["code-review-graph"])
        self.assertIn("code-review-graph", p.display)
        self.assertIn(BASE_MCP, p.display)

    def test_install_extra_pip_and_uv_pip(self):
        for has_pip, which in ((True, _which()), (False, _which("uv"))):
            p = R.plan_install(["code-review-graph"], prefix="/nowhere", executable="/v/bin/python",
                               which=which, has_pip=has_pip, requires=REQUIRES, platform="linux")
            self.assertHolds(p.argv, p.installer)

    def test_pipx_inject_every_command_that_can_pull_mcp(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            Path(d, "pipx_metadata.json").write_text("{}", encoding="utf-8")
            p = R.plan_install(["mokata[full]"], prefix=d, executable="/v/bin/python",
                               which=_which("pipx"), requires=REQUIRES, platform="linux")
        self.assertEqual(R.PIPX, p.installer)
        for argv in [p.argv, *p.then]:
            # `pipx inject` installs each named package on its own run (measured: a held `mcp`
            # positional was skipped AFTER code-review-graph pulled mcp 2.3), so the hold must
            # ride inside pip's own resolve: `--pip-args`.
            self.assertIn(f"--pip-args={BASE_MCP}", argv)
            self.assertNotIn(BASE_MCP, argv, "a positional hold does not hold under pipx")

    def test_a_request_that_does_not_touch_mcp_is_unchanged_in_kind(self):
        p = R.plan_install(["model2vec>=0.3"], prefix="/nowhere", executable="/v/bin/python",
                           which=_which(), has_pip=True, requires=REQUIRES, platform="linux")
        self.assertEqual("model2vec>=0.3", p.argv[4])


class TheCrgPin(unittest.TestCase):
    def test_full_extra_pins_the_release_proven_on_mcp_1(self):
        crg = [r for r in PYPROJECT["optional-dependencies"]["full"]
               if r.startswith("code-review-graph")]
        self.assertEqual(["code-review-graph==2.4.0"], crg)

    def test_the_adopt_offer_installs_the_same_pin(self):
        from mokata.knowledge.graph_adopt import CRG_SPEC
        crg = [r for r in PYPROJECT["optional-dependencies"]["full"]
               if r.startswith("code-review-graph")]
        self.assertEqual(crg, [CRG_SPEC])

    def test_mokata_still_caps_mcp_below_2(self):
        self.assertEqual("mcp>=1.30,<2", BASE_MCP.replace(" ", ""))


if __name__ == "__main__":
    unittest.main()
