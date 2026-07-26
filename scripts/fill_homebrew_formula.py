#!/usr/bin/env python3
"""BREW-15 — deterministic filler for the Homebrew formula (stdlib only; no dependency).

`packaging/homebrew/mokata.rb` carries THREE pieces that must track a release exactly and that
were previously hand-maintained — which is why the committed formula drifted three releases
stale (it pinned a 0.0.5 URL and a literal `sha256 "pending-publication-..."`):

  1. `url`       — the released sdist on PyPI,
  2. `sha256`    — that sdist's real checksum,
  3. `resource`  — the vendored dependency tree.

(3) is not optional. Homebrew installs a Python formula with `--no-deps` (see
`Formula#std_pip_args`), so `virtualenv_install_with_resources` installs ONLY what the formula
declares as `resource` stanzas. mokata depends unconditionally on `mcp>=1.2` (pyproject.toml),
so a formula with no resources yields a venv WITHOUT the MCP SDK — `mokata --version` still
passes (the SDK is lazily imported) while `mokata-mcp`, mokata's primary in-harness surface,
is broken. Silent breakage is the worst kind, so the resources are generated here and the
formula's `test do` block imports the SDK to make a missing one fail LOUD.

All three are generated, never hand-written. The resource set comes from a committed lockfile
(`packaging/homebrew/resources.lock.json`) so rendering is DETERMINISTIC: the same lockfile
always produces a byte-identical formula. A dependency bump is `refresh-lock` + `fill`, never
an edit.

WHEN THIS RUNS (grounded, and deliberately NOT at release-prep). release.yml derives
`SOURCE_DATE_EPOCH` from the commit being tagged, and `normalize_sdist.py` stamps every tar
member's mtime to it — so the published sdist's bytes are a function of the tag commit. That
commit is the squash-merge created on the mirror partway through `release.sh`; it does not
exist at release-prep, so a locally-built sdist provably CANNOT match what PyPI serves. The
fill therefore reads the PUBLISHED artifact, after the `pypi` job succeeds and before the tap
push (the maintainer's tap runbook sequences it).

    # after the release tag's PyPI publish has succeeded:
    scripts/fill_homebrew_formula.py fill --version 0.0.15 --from-pypi
    scripts/fill_homebrew_formula.py check --version 0.0.15

    # only when the dependency tree changes:
    scripts/fill_homebrew_formula.py refresh-lock

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORMULA = os.path.join(ROOT, "packaging", "homebrew", "mokata.rb")
LOCK = os.path.join(ROOT, "packaging", "homebrew", "resources.lock.json")

# The template tokens the committed formula carries between releases. They are deliberately
# not a plausible-looking stale value: an unfilled formula must be obviously unfilled.
URL_TOKEN = "FILL-ME-SDIST-URL"
SHA_TOKEN = "FILL-ME-SDIST-SHA256"

BEGIN_MARK = "  # BEGIN GENERATED RESOURCES"
END_MARK = "  # END GENERATED RESOURCES"

# Packages that are never resources: mokata itself (it is the formula's `url`) and the build
# scaffolding pip puts in every venv.
_NOT_A_RESOURCE = {"mokata", "pip", "setuptools", "wheel"}

PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"


class FormulaError(Exception):
    """The formula or its inputs are not in a shape we are willing to write."""


# --- pure helpers (unit-tested; no network, no filesystem beyond the path handed in) --------


def sha256_file(path: str) -> str:
    """sha256 of a file, streamed so a large sdist doesn't land in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sdist_version(filename: str) -> str:
    """Extract the version from an sdist filename: `mokata-0.0.15.tar.gz` -> `0.0.15`.

    Raises FormulaError on anything that is not a mokata sdist, so a wrong file can never be
    silently checksummed into the formula.
    """
    base = os.path.basename(filename)
    match = re.fullmatch(r"mokata-(.+)\.tar\.gz", base)
    if not match:
        raise FormulaError(
            f"not a mokata sdist filename: {base!r} (expected 'mokata-<version>.tar.gz')"
        )
    return match.group(1)


def render_resources(entries) -> str:
    """Render the `resource` stanzas, sorted by name, as deterministic Ruby.

    Sorting is on the lowercased name (Homebrew's own convention, and what
    `brew update-python-resources` emits) so the ordering never depends on how the lockfile
    happened to be written.
    """
    lines = []
    for entry in sorted(entries, key=lambda e: e["name"].lower()):
        for field in ("name", "url", "sha256"):
            if not entry.get(field):
                raise FormulaError(f"lockfile entry missing {field!r}: {entry!r}")
        lines.append(f'  resource "{entry["name"]}" do')
        lines.append(f'    url "{entry["url"]}"')
        lines.append(f'    sha256 "{entry["sha256"]}"')
        lines.append("  end")
        lines.append("")
    return "\n".join(lines).rstrip("\n")


def fill_formula(text: str, *, version: str, url: str, sha256: str, resources: str) -> str:
    """Return `text` with url + sha256 + the generated resource block replaced.

    Idempotent: filling an already-filled formula with the same inputs returns identical text.
    Refuses (FormulaError) on a formula that has lost any of the three regions it owns, rather
    than writing a half-filled formula.
    """
    if BEGIN_MARK not in text or END_MARK not in text:
        raise FormulaError(
            "formula is missing the generated-resources markers "
            f"({BEGIN_MARK.strip()} / {END_MARK.strip()}) — refusing to write"
        )
    head, rest = text.split(BEGIN_MARK, 1)
    _, tail = rest.split(END_MARK, 1)

    # url/sha256 are replaced ONLY in the head — the resource stanzas carry their own url and
    # sha256 lines, and must never be touched by the top-level fill.
    head, n_url = re.subn(r'^(\s*)url\s+".*?"$', rf'\g<1>url "{url}"', head, count=1, flags=re.M)
    if n_url != 1:
        raise FormulaError("could not find the formula's top-level `url` line — refusing to write")
    head, n_sha = re.subn(
        r'^(\s*)sha256\s+".*?"$', rf'\g<1>sha256 "{sha256}"', head, count=1, flags=re.M
    )
    if n_sha != 1:
        raise FormulaError(
            "could not find the formula's top-level `sha256` line — refusing to write"
        )

    body = f"\n{resources}\n" if resources else "\n"
    return f"{head}{BEGIN_MARK}\n{body}{END_MARK}{tail}"


def load_lock(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["resources"]


# --- PyPI access (the one impure edge; injected in tests) -----------------------------------


def _fetch_json(url: str):
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - fixed https host
        return json.load(resp)


def pypi_sdist(name: str, version: str, fetch=_fetch_json):
    """Return (canonical_name, url, sha256) for a package version's sdist, from the PyPI API.

    The API's digest IS the checksum of the bytes PyPI serves, which is exactly what Homebrew
    verifies — so taking it here makes the formula's sha256 correct by construction rather
    than by a local rebuild that has no reason to match.

    The name comes from the API too, not from `pip list`: pip reports the *distribution*
    spelling (`pydantic_core`, `typing_extensions`) while `brew audit --strict` requires the
    resource name to match the PyPI package name (`pydantic-core`, `typing-extensions`).
    """
    data = fetch(PYPI_JSON.format(name=name, version=version))
    # PEP 503 treats `_` and `-` as equivalent and PyPI's API echoes whichever the project
    # used (`typing-extensions` but `pydantic_core`), while brew audit wants the hyphen form
    # for both. Case is left alone — audit accepts `PyJWT` as-is.
    canonical = data.get("info", {}).get("name", name).replace("_", "-")
    for entry in data.get("urls", []):
        if entry.get("packagetype") == "sdist":
            return canonical, entry["url"], entry["digests"]["sha256"]
    raise FormulaError(f"{name} {version} publishes no sdist on PyPI (needed: Homebrew builds from source)")


# --- commands -------------------------------------------------------------------------------


def cmd_fill(args) -> int:
    if args.from_pypi:
        _name, url, sha = pypi_sdist("mokata", args.version)
        # The API answered for the version we asked about, but assert the artifact it handed
        # back really is that version — a mismatch means we would checksum the wrong release.
        got = sdist_version(url)
        if got != args.version:
            raise FormulaError(
                f"PyPI returned an sdist for {got}, but --version says {args.version}"
            )
    else:
        got = sdist_version(args.sdist)
        if got != args.version:
            raise FormulaError(
                f"sdist is {os.path.basename(args.sdist)} (version {got}), "
                f"but --version says {args.version} — refusing to fill"
            )
        sha = sha256_file(args.sdist)
        url = pypi_sdist("mokata", args.version)[1]

    with open(args.formula, encoding="utf-8") as fh:
        text = fh.read()
    filled = fill_formula(
        text,
        version=args.version,
        url=url,
        sha256=sha,
        resources=render_resources(load_lock(args.lock)),
    )
    if filled == text:
        print(f"formula already filled for {args.version} — no change")
        return 0
    with open(args.formula, "w", encoding="utf-8") as fh:
        fh.write(filled)
    print(f"filled {os.path.relpath(args.formula, ROOT)} for mokata {args.version}")
    print(f"  url    {url}")
    print(f"  sha256 {sha}")
    return 0


def cmd_check(args) -> int:
    """Fail-closed: is the formula actually filled for this version?

    The tap-push runbook gates on this so a template-state formula can never be pushed.
    """
    with open(args.formula, encoding="utf-8") as fh:
        text = fh.read()
    head = text.split(BEGIN_MARK, 1)[0]
    problems = []
    if URL_TOKEN in text or SHA_TOKEN in text:
        problems.append("formula still carries the FILL-ME template tokens (never filled)")
    if f"mokata-{args.version}.tar.gz" not in head:
        problems.append(f"formula's url is not the mokata {args.version} sdist")
    if re.search(r'^\s*sha256\s+"[0-9a-f]{64}"$', head, flags=re.M) is None:
        problems.append("formula's top-level sha256 is not a 64-hex-digit checksum")
    if BEGIN_MARK not in text or "resource " not in text:
        problems.append("formula declares no resources — `mokata-mcp` would be broken")
    if problems:
        for p in problems:
            print(f"NOT READY: {p}", file=sys.stderr)
        return 1
    print(f"formula is filled and ready for mokata {args.version}")
    return 0


def cmd_refresh_lock(args) -> int:
    """Re-resolve mokata's runtime dependency tree and rewrite the lockfile.

    Resolution runs in a throwaway venv so the dev environment is never mutated (the same
    posture release.sh takes for its preflight legs).
    """
    with tempfile.TemporaryDirectory() as tmp:
        venv = os.path.join(tmp, "venv")
        subprocess.run([args.python, "-m", "venv", venv], check=True)
        py = os.path.join(venv, "bin", "python")
        subprocess.run([py, "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
        # Resolve the SAME requirement pyproject declares, so the lock can't drift from it.
        subprocess.run([py, "-m", "pip", "install", "-q", args.requirement], check=True)
        frozen = subprocess.run(
            [py, "-m", "pip", "list", "--format=freeze"],
            check=True, capture_output=True, text=True,
        ).stdout

    resources = []
    for line in frozen.splitlines():
        if "==" not in line:
            continue
        name, version = line.split("==", 1)
        if name.lower() in _NOT_A_RESOURCE:
            continue
        canonical, url, sha = pypi_sdist(name, version)
        resources.append({"name": canonical, "version": version, "url": url, "sha256": sha})
        print(f"  resolved {canonical} {version}")

    resources.sort(key=lambda e: e["name"].lower())
    payload = {
        "_comment": (
            "GENERATED by scripts/fill_homebrew_formula.py refresh-lock — do not hand-edit. "
            "mokata's runtime dependency tree, vendored into the Homebrew formula because "
            "Homebrew installs Python formulae with --no-deps."
        ),
        "requirement": args.requirement,
        "python": args.python,
        "resources": resources,
    }
    with open(args.lock, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
        fh.write("\n")
    print(f"wrote {os.path.relpath(args.lock, ROOT)} ({len(resources)} resources)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    # --formula/--lock hang off every subcommand (not the top level) so the runbook's commands
    # read naturally and can't fail on flag ORDER — `fill --formula X` is what a maintainer
    # will type, and argparse would otherwise reject it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--formula", default=FORMULA, help="path to mokata.rb")
    common.add_argument("--lock", default=LOCK, help="path to resources.lock.json")

    p_fill = sub.add_parser("fill", parents=[common], help="fill url + sha256 + resources")
    p_fill.add_argument("--version", required=True)
    source = p_fill.add_mutually_exclusive_group(required=True)
    source.add_argument("--from-pypi", action="store_true", help="take url + sha256 from PyPI")
    source.add_argument("--sdist", help="local sdist to checksum (see the module docstring's "
                                        "warning about local-vs-published bytes)")
    p_fill.set_defaults(func=cmd_fill)

    p_check = sub.add_parser("check", parents=[common], help="fail-closed: is the formula filled for --version?")
    p_check.add_argument("--version", required=True)
    p_check.set_defaults(func=cmd_check)

    p_lock = sub.add_parser("refresh-lock", parents=[common], help="re-resolve the dependency tree")
    p_lock.add_argument("--python", default="python3.12")
    p_lock.add_argument("--requirement", default="mcp>=1.2")
    p_lock.set_defaults(func=cmd_refresh_lock)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FormulaError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
