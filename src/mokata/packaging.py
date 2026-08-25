"""J1 — Claude Code plugin packaging: manifest paths + dependency-free validators.

mokata ships as a Claude Code plugin under the MoStack marketplace. Two committed,
reviewable manifests describe it:
  - .claude-plugin/plugin.json      — the plugin (name/version/commands/hooks/license)
  - .claude-plugin/marketplace.json — the marketplace listing (install/update metadata)

These validators mirror the spine's own validation style (structural, no dependency).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import repo_paths
from .disclosure import DisclosureReport, check_disclosure_resolves

PLUGIN_MANIFEST_PATH = ".claude-plugin/plugin.json"
MARKETPLACE_PATH = ".claude-plugin/marketplace.json"
PYPROJECT_PATH = "pyproject.toml"
PACKAGE_INIT_PATH = "src/mokata/__init__.py"
CHANGELOG_PATH = "CHANGELOG.md"
RELEASE_NOTES_PATH = "RELEASE_NOTES.md"


def _req_str(data: Dict[str, Any], key: str, where: str, errors: List[str]) -> None:
    if not isinstance(data.get(key), str) or not data.get(key):
        errors.append(f"{where}.{key} must be a non-empty string")


def validate_plugin(data: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["plugin manifest must be a JSON object"]
    _req_str(data, "name", "plugin", errors)
    _req_str(data, "version", "plugin", errors)
    # Optional-but-typed fields.
    if "license" in data and not isinstance(data["license"], str):
        errors.append("plugin.license must be a string")
    if "keywords" in data and not isinstance(data["keywords"], list):
        errors.append("plugin.keywords must be an array")
    return errors


def validate_marketplace(data: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["marketplace manifest must be a JSON object"]
    _req_str(data, "name", "marketplace", errors)
    plugins = data.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        errors.append("marketplace.plugins must be a non-empty array")
        return errors
    for i, entry in enumerate(plugins):
        where = f"marketplace.plugins[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{where} must be an object")
            continue
        _req_str(entry, "name", where, errors)
        _req_str(entry, "source", where, errors)
    return errors


# =====================================================================================
# Stage 61b — release version-consistency check (PURE / OFFLINE).
# =====================================================================================
# The 0.0.4 cut tagged a commit whose version fields lagged the tag, so the
# tag-triggered CI went red. This is the single source of truth for "do all version
# fields equal the tag we're about to push?" — read by `mokata release-check`, the
# `release.sh` preflight (which verifies AT THE EXACT COMMIT being tagged, on the dev
# checkout AND the public mirror), and the ship-artifact test. No network, never raises:
# a missing/unreadable field is reported as a named mismatch (None), not a crash.

# PIN-DRIFT (0.0.15) — the published `mokata-check@vX.Y.Z` action pins. They used to be
# bumped BY HAND at each doc gate (0.0.14: `916c65f`) and one gate missed them; they are now
# guarded fields like any other version-bearing location. A new pin must be added HERE;
# `tests/test_pin_drift.py` sweeps the tree and fails CI if a published pin isn't covered.
ACTION_PIN_RE = re.compile(r"mokata-check@v(\d+\.\d+\.\d+)")
ACTION_PIN_PATHS = (
    "docs/how-to/mokata-as-a-pr-check.md",
    ".github/actions/mokata-check/example-pr-check.yml",
)

# field name (`path:selector`) -> how to read it. The four version-bearing FILES + the
# package __version__ + the action pins — the set a tag must match before it can be pushed.
# VERIFY-ONLY: nothing here bumps; `release.sh` REFUSES to tag on a mismatch.
_VERSION_FIELDS = (
    "pyproject.toml:version",
    "plugin.json:version",
    "marketplace.json:metadata.version",
    "marketplace.json:plugins[0].version",
    "src/mokata/__init__.py:__version__",
) + tuple(f"{rel}:action-pin" for rel in ACTION_PIN_PATHS)


def _read_text(root: str, rel: str) -> Optional[str]:
    try:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _toml_version(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else None


def _dunder_version(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = re.search(r'(?m)^\s*__version__\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else None


def _action_pin(text: Optional[str]) -> Optional[str]:
    """The `@vX.Y.Z` action pin inside arbitrary text (the `action-pin` selector kind).
    Fail-closed: no pin -> None (a named mismatch); several DISAGREEING pins in one file ->
    the joined values, which can never equal a tag, so the file is named as an offender."""
    if not text:
        return None
    found = ACTION_PIN_RE.findall(text)
    if not found:
        return None
    uniq = sorted(set(found))
    return uniq[0] if len(uniq) == 1 else ",".join(uniq)


def _json_get(root: str, rel: str, *path: Any) -> Optional[str]:
    text = _read_text(root, rel)
    if text is None:
        return None
    try:
        node: Any = json.loads(text)
        for key in path:
            node = node[key]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    return node if isinstance(node, str) else None


def read_version_fields(root: str = ".") -> "Dict[str, Optional[str]]":
    """The version string at each guarded location — the five canonical version fields plus
    the `action-pin` entries (None when the file or field is missing/unreadable). Keyed by
    `_VERSION_FIELDS`. Pure/offline; never raises."""
    fields: "Dict[str, Optional[str]]" = {
        "pyproject.toml:version": _toml_version(_read_text(root, PYPROJECT_PATH)),
        "plugin.json:version": _json_get(root, PLUGIN_MANIFEST_PATH, "version"),
        "marketplace.json:metadata.version":
            _json_get(root, MARKETPLACE_PATH, "metadata", "version"),
        "marketplace.json:plugins[0].version":
            _json_get(root, MARKETPLACE_PATH, "plugins", 0, "version"),
        "src/mokata/__init__.py:__version__":
            _dunder_version(_read_text(root, PACKAGE_INIT_PATH)),
    }
    for rel in ACTION_PIN_PATHS:
        fields[f"{rel}:action-pin"] = _action_pin(_read_text(root, rel))
    return fields


def _normalize_tag(target: str) -> str:
    """`v0.0.4` / `0.0.4` -> `0.0.4` (the comparable version string)."""
    return (target or "").strip().lstrip("vV")


# =====================================================================================
# 0.0.18 stage 7 — WHICH mokata answered? (`RELEASE-CHECK-BARE-COMMAND-READS-SITE-PACKAGES`)
# =====================================================================================
# `python3 -m mokata release-check 0.0.17` run bare from the repo root imported mokata 0.0.9
# out of `~/Library/Python/3.9/.../site-packages` — EIGHT tagged releases behind — read the
# version fields out of THIS checkout, compared them against 0.0.9's five-entry
# `_VERSION_FIELDS`, and printed PASS. The two `action-pin` fields did not exist in 0.0.9, so
# they were not checked and nothing said so. A five-field PASS and a seven-field PASS were the
# same sentence.
#
# THE TAG-TIME PATH WAS NEVER AFFECTED and is not changed here: `scripts/release.sh` runs this
# command with `PYTHONPATH="${root}/src"`, so it imports from the checkout it is asked about.
# The residual was the HUMAN path — every doc, help string and runbook line showing the bare
# command handed a reader a green computed by stale code.
#
# THE FIX IS §7g: an answer that carries its own provenance cannot be mistaken for a different
# answer. `answering_package()` splits three states that used to share one representation —
#   IN-ROOT      the package answering lives inside the checkout being asked about
#   OUT-OF-ROOT  it lives somewhere else (site-packages, another checkout, a venv)
#   UNRESOLVABLE the package has no `__file__` at all (namespace/frozen import)
# — and the CLI REFUSES on the last two rather than answering. The remedy it names,
# `PYTHONPATH=<root>/src`, is the one `release.sh` already uses, so it exists before it is
# advertised (§7g's corollary: a refusal pointing at a remedy nobody built is a new defect).

ANSWERED_IN_ROOT = "in-root"
ANSWERED_OUT_OF_ROOT = "out-of-root"
ANSWERED_UNRESOLVABLE = "unresolvable"


@dataclass
class PackageProvenance:
    """WHICH installed mokata is answering, and whether it is the one being asked about."""

    state: str
    root: str                                  # the checkout the caller asked about (realpath)
    package_dir: Optional[str]                 # where the answering package lives (realpath)
    version: str                               # the answering package's own __version__
    field_count: int                           # how many guarded fields THIS code knows about

    @property
    def trustworthy(self) -> bool:
        return self.state == ANSWERED_IN_ROOT

    @property
    def remedy(self) -> str:
        # ⚠ `os.path.join`, NOT `"%s/src"`. This string is SHIPPED, USER-FACING OUTPUT — it is the
        # command a refused release-check tells a human to run — and welding a `/` onto a native
        # path produced `D:\a\mokata-oss\mokata-oss/src` on the 0.0.18 Windows legs: a path with
        # both separators, in the one line whose entire job is to be copy-pasteable.
        #
        # Found by the WSL repair, not by a new test. This module asserted `returncode != 0`, and
        # WSL's launcher also exits non-zero, so the assertion was satisfied by a shell that never
        # read the step — a vacuous green. Fixing cause A made the module actually run, and it
        # surfaced this the same hour. That is the argument for repairing a false green even when
        # the suite was already "passing": the green was hiding a real product defect, not merely
        # failing to add confidence.
        return 'PYTHONPATH="%s" python3 -m mokata release-check <version> --root "%s"' % (
            os.path.join(self.root, "src"), self.root)

    def render(self) -> str:
        where = self.package_dir if self.package_dir is not None else "(no __file__)"
        return "answered by mokata %s from %s — %d guarded fields; checking %s" % (
            self.version, where, self.field_count, self.root)

    def refusal(self) -> str:
        if self.state == ANSWERED_UNRESOLVABLE:
            why = ("this mokata has no importable location, so there is no way to tell whether "
                   "it is the code that belongs to the checkout being verified")
        else:
            why = ("this mokata was imported from OUTSIDE the checkout it was asked about, so a "
                   "PASS here is that package's opinion of this tree — including which fields it "
                   "knows to guard, which is exactly what changes between releases")
        return "\n".join([
            "release-check REFUSED — %s" % why,
            "  answering package: %s" % (self.package_dir or "(no __file__)"),
            "  checkout asked about: %s" % self.root,
            "  run it against the checkout's own code instead:",
            "    %s" % self.remedy,
        ])


def _within(child: str, parent: str) -> bool:
    """Whether `child` is `parent` or lives under it. BOTH MUST ALREADY BE REALPATHS.

    ⚠ IT USED TO RESOLVE THEM ITSELF, AND THAT SECOND RESOLUTION MADE THE FIRST UNGRADABLE
    (doc 85 §7f, found by mutation at 0.0.18 stage 7). `answering_package` resolves both paths for
    its own reasons — the rendered line must show a reader the path that actually answered — so
    the calls here were a duplicate defence: mutating either one alone left the other covering,
    and the symlink mutant came back GREEN. Deleted rather than split, because the second bought
    nothing: there is exactly one caller and it resolves first.

    The prefix test keeps the separator on purpose. `/…/mokata-oss` starts with `/…/mokata`, and
    those are the two checkouts this whole stage exists to keep apart.
    """
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def answering_package(root: str, package_file: Optional[str], version: str,
                      field_count: Optional[int] = None) -> PackageProvenance:
    """Where the mokata that is about to answer came from, relative to `root`.

    PURE: the caller supplies the package's `__file__` rather than this reading its own, so the
    OUT-OF-ROOT and UNRESOLVABLE states are reachable from a test on a healthy tree (doc 85 §7i
    — a guard whose offender only exists on a broken machine grades nothing on this one).
    """
    resolved_root = os.path.realpath(root or ".")
    if field_count is None:
        field_count = len(_VERSION_FIELDS)
    if not package_file:
        return PackageProvenance(ANSWERED_UNRESOLVABLE, resolved_root, None, version, field_count)
    package_dir = os.path.realpath(os.path.dirname(package_file))
    state = ANSWERED_IN_ROOT if _within(package_dir, resolved_root) else ANSWERED_OUT_OF_ROOT
    return PackageProvenance(state, resolved_root, package_dir, version, field_count)


@dataclass
class ReleaseConsistency:
    """Whether every version field equals the intended tag — fail-closed."""

    target: str                                          # the intended version (no 'v')
    fields: "Dict[str, Optional[str]]" = field(default_factory=dict)
    mismatches: List[Tuple[str, Optional[str]]] = field(default_factory=list)

    @property
    def consistent(self) -> bool:
        # an empty target is itself a failure (nothing to release against)
        return bool(self.target) and not self.mismatches

    def render(self) -> str:
        head = ("release-check PASS" if self.consistent else "release-check FAIL")
        lines = [f"{head} — intended tag {self.target or '(none given)'}"]
        for name, val in self.fields.items():
            ok = (val == self.target)
            mark = "  " if ok else "✗ "
            lines.append(f"  {mark}{name}: {val if val is not None else '(missing)'}")
        if not self.target:
            lines.append("  offenders: no intended version supplied")
        elif self.mismatches:
            offenders = ", ".join(f"{n}={v if v is not None else '(missing)'}"
                                  for n, v in self.mismatches)
            lines.append(f"  offenders (≠ {self.target}): {offenders}")
        return "\n".join(lines)


def check_release_consistency(target: str, root: str = ".") -> ReleaseConsistency:
    """Verify that all five version fields under `root` equal `target` (the intended tag).
    PURE/OFFLINE and fail-closed: a missing field or any value ≠ the tag is a named
    mismatch (so a caller — release.sh — REFUSES to tag). Never raises."""
    norm = _normalize_tag(target)
    fields = read_version_fields(root)
    mismatches = [(name, val) for name, val in fields.items() if val != norm]
    return ReleaseConsistency(target=norm, fields=fields, mismatches=mismatches)


# =====================================================================================
# DG-7 — release-notes disclosure check (PURE / OFFLINE).
# =====================================================================================
# The 0.0.16 pre-release audit (DB.S10) returned NO-GO on exactly one thing, and it was
# not a missing disclosure — it was the ABSENCE OF ANYTHING THAT WOULD NOTICE one.
# `CHANGELOG.md` carried a measured, quantitative `### Known limitations` entry (the FTS
# lexical tier loses recall at 100k); `RELEASE_NOTES.md` — the file GitHub Releases and
# the PR body are built from, and the only one most readers ever see — was still the
# previous release's text. Nothing in `tests/`, `scripts/` or `.github/` bound the two,
# so the notes could be rewritten at the cut with the disclosure silently dropped and
# NOTHING would go red. This project already gates markdown table SHAPE
# (`scripts/check-tracker-tables.py`); a disclosure it deliberately chose to ship is
# worth at least as much.
#
# The contract, deliberately narrow so it cannot be satisfied by watering the text down:
#   1. every `### Known limitations` entry in the CHANGELOG's section for the target
#      version must have its SIGNATURE carried into the notes — the measured numbers, the
#      versions it names, and the code identifiers it blames. Prose may be rewritten
#      freely (the notes are not the changelog); the FACTS may not be dropped. "Some
#      ranking issues exist" loses every number and goes RED.
#   2. the notes must actually FRAME it as a known limitation (the phrase must appear),
#      so the numbers cannot be scattered into a performance brag.
#   3. `RELEASE_NOTES.md`'s declared version must equal the intended tag — which is what
#      `release.sh` reads out of `pyproject.toml`. This is what catches the 0.0.16 case
#      literally: notes announcing 0.0.14 at a 0.0.16 cut.
#
# Rule 3 is a CUT-TIME check by construction: writing the notes ahead of the version bump
# is the normal pre-cut state (and is what the DB.S10 remediation deliberately did), so
# it is enforced by `release.sh`'s fail-closed preflight rather than by a unit test that
# would be red for the whole development window. Rules 1-2 are version-independent and
# ARE asserted on every push by `tests/test_dg7_release_notes_disclosure.py`.

_LIMITATIONS_HEADING = "### Known limitations"
_CODE_SPAN_RE = re.compile(r"`([^`]+)`")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SEMVER_RE = re.compile(r"\d+\.\d+\.\d+")
# A MEASUREMENT is a number a reader could act on: one carrying a unit (`5.6pp`, `21×`,
# `4533 ms`), a precise decimal (`0.4444` — 3+ fraction digits, i.e. a measured score not
# a version fragment), or a grouped integer (`100,000`). Bare small integers are NOT
# measurements: requiring `10` from "MRR@10" would make the gate noise.
_MEASUREMENT_RE = re.compile(
    r"(?<![\w.])(?:"
    r"\d[\d,]*(?:\.\d+)?\s*(?:pp|%|×|ms\b|s\b|x\b)"   # a number with a unit
    r"|\d+\.\d{3,}"                                    # a precise decimal
    r"|\d{1,3}(?:,\d{3})+"                             # a grouped integer
    r")"
)


def _normalize_prose(text: str) -> str:
    """Fold the differences that are pure presentation, so a fact stated in the notes
    counts even when it is worded, emphasised or punctuated differently.

    Lowercases; drops markdown emphasis/code markers; maps the Unicode minus, en- and
    em-dash onto ASCII `-`; strips thousands separators INSIDE numbers (so `100,000` and
    `100000` are the same fact); collapses whitespace."""
    out = (text or "").lower()
    for ch in "*_`":
        out = out.replace(ch, "")
    out = out.replace("−", "-").replace("–", "-").replace("—", "-")
    out = re.sub(r"(?<=\d),(?=\d{3}\b)", "", out)
    return re.sub(r"\s+", " ", out).strip()


def changelog_section(text: Optional[str], version: str) -> Optional[str]:
    """The body of the `## …` CHANGELOG section for `version` — matching both the
    released form (`## [0.0.15] — 2026-07-22`) and the pre-cut form
    (`## [Unreleased] — 0.0.16`). None when the file or the section is absent."""
    if not text:
        return None
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## ") and version in line:
            start = i + 1
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return "\n".join(lines[start:end])


def known_limitations(section: Optional[str]) -> List[str]:
    """The `### Known limitations` bullets of one CHANGELOG section, each returned whole
    (lead line + its indented continuation). Empty when the section has no such heading —
    a release that declares no limitation has nothing to disclose."""
    if not section:
        return []
    lines = section.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == _LIMITATIONS_HEADING:
            start = i + 1
            break
    if start is None:
        return []
    entries: List[str] = []
    current: List[str] = []
    for line in lines[start:]:
        if line.startswith("###") or line.startswith("## "):
            break
        if line.startswith("- "):
            if current:
                entries.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        entries.append("\n".join(current))
    return [e for e in entries if e.strip()]


def signature_tokens(entry: str) -> List[str]:
    """The facts of one limitation entry that the release notes must carry: every code
    identifier it names, every `x.y.z` version it references, and every measurement.

    Deliberately fact-shaped rather than prose-shaped — the notes are expected to be
    rewritten, so matching the wording would be a false gate; matching the NUMBERS is
    what makes "shipped as-is, here is exactly how much it costs you" unfalsifiable."""
    tokens: List[str] = []
    for span in _CODE_SPAN_RE.findall(entry):
        span = span.strip()
        if _IDENTIFIER_RE.match(span):
            tokens.append(span)
    stripped = _CODE_SPAN_RE.sub(" ", entry)
    tokens.extend(_SEMVER_RE.findall(entry))
    tokens.extend(m.strip() for m in _MEASUREMENT_RE.findall(stripped))
    # dedupe, keeping first-seen order so the failure message reads in document order
    seen, ordered = set(), []
    for tok in tokens:
        key = _normalize_prose(tok)
        if key and key not in seen:
            seen.add(key)
            ordered.append(tok)
    return ordered


def read_release_notes_version(text: Optional[str]) -> Optional[str]:
    """The version `RELEASE_NOTES.md` announces — the first `x.y.z` in the document
    (the notes open `mokata **0.0.16 — "…"**`). None when absent/unreadable, which is a
    named mismatch rather than a crash."""
    if not text:
        return None
    m = _SEMVER_RE.search(text)
    return m.group(0) if m else None


@dataclass
class ReleaseNotesCheck:
    """Whether RELEASE_NOTES.md announces the intended tag AND discloses everything the
    CHANGELOG calls a known limitation — fail-closed."""

    target: str                                                # intended version, no 'v'
    notes_version: Optional[str] = None
    section_found: bool = False
    limitation_count: int = 0
    missing_tokens: List[Tuple[int, str]] = field(default_factory=list)   # (entry #, fact)
    framing_missing: bool = False
    #: B5 (0.0.19) — whether every published SCHEDULE still resolves. A SEPARATE axis from
    #: `disclosure_ok` on purpose: that one asks whether a limitation is still PRINTED, this one
    #: whether a promise is still TRUE, and the row exists because the first passed while the second
    #: was false. `ok` below is untouched, so no verdict on anything that is not a schedule moves.
    claims: Optional["DisclosureReport"] = None

    @property
    def version_ok(self) -> bool:
        return bool(self.target) and self.notes_version == self.target

    @property
    def disclosure_ok(self) -> bool:
        return self.section_found and not self.missing_tokens and not self.framing_missing

    @property
    def ok(self) -> bool:
        return self.version_ok and self.disclosure_ok

    @property
    def claims_failed(self) -> bool:
        """A published schedule no longer resolves. Red, and it fails the cut."""
        return bool(self.claims is not None and self.claims.failed)

    @property
    def claims_undecided(self) -> bool:
        """A schedule this leg could NOT check — the third state. Not red, and NOT a pass."""
        return bool(self.claims is not None and self.claims.undecided)

    def render(self) -> str:
        head = ("release-notes-check PASS" if self.ok else "release-notes-check FAIL")
        lines = [f"{head} — intended tag {self.target or '(none given)'}"]
        vmark = "  " if self.version_ok else "✗ "
        lines.append(f"  {vmark}RELEASE_NOTES.md announces: "
                     f"{self.notes_version if self.notes_version else '(no version found)'}")
        if not self.section_found:
            lines.append(f"  ✗ CHANGELOG.md has no `## …` section for {self.target or '(none)'}"
                         " — cannot tell what must be disclosed, so this fails closed")
        else:
            dmark = "  " if self.disclosure_ok else "✗ "
            lines.append(f"  {dmark}CHANGELOG known limitations to disclose: {self.limitation_count}")
        if self.framing_missing:
            lines.append("  ✗ RELEASE_NOTES.md never says \"known limitation\" — a limitation the "
                         "CHANGELOG declares must be FRAMED as one, not buried in the prose")
        for idx, tok in self.missing_tokens:
            lines.append(f"  ✗ limitation #{idx}: RELEASE_NOTES.md does not carry `{tok}`")
        if not self.ok:
            lines.append("  remedy: rewrite RELEASE_NOTES.md for this version carrying every fact "
                         "above. DG-7 exists because an auditor must not be the one who discovers "
                         "a measured regression.")
        if self.claims is not None:
            lines.append(self.claims.render())
        return "\n".join(lines)


#: The tagged prose a release promise can be published in. `README.md` is here because it ships and
#: is read more than either of the other two; it carries no claims today and the population says so
#: rather than the corpus quietly not asking.
CLAIM_PROSE = ("CHANGELOG.md", "RELEASE_NOTES.md", "README.md")


def shipped_claim_sources(root: str = ".") -> Dict[str, Optional[str]]:
    """`{repo-relative POSIX name: text}` for every shipped surface a schedule can be PRINTED from.

    Scope, declared rather than discovered (doc 85 §7j): the tagged prose above plus every shipped
    `src/**/*.py`. `tests/` is deliberately absent — a promise is something said to a user, and a
    string planted in a test fixture is by construction not one; including it convicts three files
    whose strings are mutant sources and DG-7 fixtures. `scripts/` carries no printable constant and
    is not shipped uniformly, so it is out too. `disclosure.DECLARATION_MODULE` is skipped by the
    grader, not here, so this builder stays a plain statement of what ships.

    Missing files come back as None rather than raising, which is what lets the mirror and a partial
    checkout be graded at all — `unparseable` then reports them instead of a clean sweep hiding them.
    """
    sources: Dict[str, Optional[str]] = {}
    for rel in CLAIM_PROSE:
        sources[rel] = _read_text(root, rel)
    src_root = os.path.join(root, "src")
    for dirpath, _dirs, files in os.walk(src_root):
        if "__pycache__" in dirpath:
            continue
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            # ⚠ `name_of`, NOT a bare `relpath`. These keys are NAMES — they are compared against
            # `disclosure.DECLARATION_MODULE` and against declarations a human spelled with `/`, so
            # an OS-separated key stops matching every one of them on Windows and only on Windows.
            # The static sweep in `test_repo_paths_invariant` convicts the bare form, and the
            # choke point is the reason "relativised but not yet spelled" is not a state.
            rel = str(repo_paths.name_of(os.path.join(dirpath, name), root))
            sources[rel] = _read_text(root, rel)
    return sources


def check_release_notes(target: str, root: str = ".") -> ReleaseNotesCheck:
    """DG-7, PURE/OFFLINE and fail-closed: `RELEASE_NOTES.md` under `root` must announce
    `target` AND carry every fact from the CHANGELOG's `### Known limitations` entries for
    that version. A missing file, a missing section or an undisclosed fact is a named
    failure (so `release.sh` REFUSES to tag). Never raises."""
    norm = _normalize_tag(target)
    changelog = _read_text(root, CHANGELOG_PATH)
    notes = _read_text(root, RELEASE_NOTES_PATH)
    section = changelog_section(changelog, norm) if norm else None
    entries = known_limitations(section)
    haystack = _normalize_prose(notes or "")
    missing: List[Tuple[int, str]] = []
    for idx, entry in enumerate(entries, start=1):
        for tok in signature_tokens(entry):
            if _normalize_prose(tok) not in haystack:
                missing.append((idx, tok))
    return ReleaseNotesCheck(
        target=norm,
        notes_version=read_release_notes_version(notes),
        section_found=section is not None,
        limitation_count=len(entries),
        missing_tokens=missing,
        framing_missing=bool(entries) and "known limitation" not in haystack,
        # ⭐ `plan_sources` IS NOT PASSED, AND THAT IS THE BOUNDARY, NOT AN OVERSIGHT. This leg
        # SHIPS; the planning documents do not. So every keyed claim comes back NOT_CHECKABLE here
        # — honestly, in every tree — and the resolving verdict is made by the internal gate that
        # has the documents. Passing `{}` instead would say "consulted, found nothing", which reds
        # the mirror on every claim; passing a path would be SHIPPED-TEST-READS-INTERNAL-FILE.
        claims=check_disclosure_resolves(shipped_claim_sources(root), None, cutting=norm or None),
    )
