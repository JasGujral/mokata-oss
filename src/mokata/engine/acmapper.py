"""D3 — AC-mapper: statically map each acceptance criterion to a test.

Traceability backbone. Mapping is static (no test execution): a test covers an AC if it
declares that AC's id. `scan_tests` discovers `TestRef`s by reading test files and
finding which AC ids each test function mentions (a marker in the name/comment/body).
An AC with no mapped test is flagged — that's what the completeness gate blocks on.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List

from .spec import Spec, TestRef


@dataclass
class ACMapping:
    ac: object                       # AcceptanceCriterion
    tests: List[TestRef] = field(default_factory=list)

    @property
    def mapped(self) -> bool:
        return bool(self.tests)


@dataclass
class MapResult:
    mappings: List[ACMapping] = field(default_factory=list)

    @property
    def unmapped(self) -> List[object]:
        return [m.ac for m in self.mappings if not m.mapped]

    @property
    def unmapped_ids(self) -> List[str]:
        return [m.ac.id for m in self.mappings if not m.mapped]

    @property
    def fully_mapped(self) -> bool:
        return bool(self.mappings) and not self.unmapped

    @property
    def coverage(self) -> float:
        if not self.mappings:
            return 0.0
        mapped = sum(1 for m in self.mappings if m.mapped)
        return mapped / len(self.mappings)


def map_acceptance_criteria(spec: Spec, tests: List[TestRef]) -> MapResult:
    """Map every AC in `spec` to the tests that declare it."""
    mappings: List[ACMapping] = []
    for ac in spec.criteria:
        covering = [t for t in tests if ac.id in t.ac_ids]
        mappings.append(ACMapping(ac=ac, tests=covering))
    return MapResult(mappings=mappings)


def _mentions(text: str, ac_id: str) -> bool:
    # word-ish boundary so 'AC-1' does not match inside 'AC-10'
    pattern = r"(?<![0-9A-Za-z])" + re.escape(ac_id) + r"(?![0-9A-Za-z])"
    return re.search(pattern, text) is not None


def scan_tests(root: str, ac_ids: List[str]) -> List[TestRef]:
    """Discover TestRefs by reading test files: for each `def test_...`, record which of
    `ac_ids` appear within that function. Dependency-free, static."""
    refs: List[TestRef] = []
    def_re = re.compile(r"^(\s*)def\s+(test_\w+)\s*\(")
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().splitlines()
            except OSError:
                continue
            i = 0
            while i < len(lines):
                m = def_re.match(lines[i])
                if not m:
                    i += 1
                    continue
                base, name, start = len(m.group(1)), m.group(2), i
                block = [lines[i]]
                j = i + 1
                while j < len(lines):
                    ln = lines[j]
                    if ln.strip() and (len(ln) - len(ln.lstrip())) <= base:
                        break
                    block.append(ln)
                    j += 1
                body = "\n".join(block)
                found = [a for a in ac_ids if _mentions(body, a)]
                if found:
                    refs.append(TestRef(name=name, ac_ids=found,
                                        path=os.path.relpath(path, root),
                                        line=start + 1))
                i = j
    return refs
