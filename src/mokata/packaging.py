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

    @property
    def version_ok(self) -> bool:
        return bool(self.target) and self.notes_version == self.target

    @property
    def disclosure_ok(self) -> bool:
        return self.section_found and not self.missing_tokens and not self.framing_missing

    @property
    def ok(self) -> bool:
        return self.version_ok and self.disclosure_ok

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
        return "\n".join(lines)


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
    )
