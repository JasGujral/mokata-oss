"""The release-path DISARMED-CHECK sweep — PURE FUNCTIONS over a SUPPLIED CORPUS.

0.0.18 stage 6 (`NULLGLOB-DISARMS-THE-EXISTENCE-CHECK` + `SIGNED-RELEASES-UNSCORED` narrow,
doc 84 §1; doc 102 exit criterion 6, second half).

WHAT WENT WRONG, stated once so the functions below are not a style guide.

`v0.0.17` published a Release carrying the wheel, the sdist, the SBOM and one `.sigstore.json`
each — and NO `.sig`, NO `.pem`, both of which `release.yml` asked for in two places. Three
diagnoses were filed and all three were wrong (a stale Scorecard reading, a closure reasoned from
the workflow source, then a hunt through the upload/download round trip). The actual cause is two
checks that CANNOT FAIL sitting in the release path, plus a third-party behaviour change nobody
measured:

  ① THE BUILD JOB.  `echo "Signature assets:"; ls -l dist/*.sig dist/*.pem dist/*.sigstore.json`
     ran nine lines under `shopt -s nullglob`. An unmatched glob expands to NOTHING there rather
     than to itself, so `ls` received only the three bundles and exited 0. doc 84 then cited that
     exit 0 as PROOF the files existed — "the build job's own `ls` would have failed the step
     otherwise". A check that cannot fail, read as a check that passed.

  ② THE RELEASE JOB.  `softprops/action-gh-release`'s `fail_on_unmatched_files` defaults to FALSE.
     Its own source (v3.0.2 = commit 3d0d988…, `src/run.ts` lines 15-21) branches
     `if (config.input_fail_on_unmatched_files) { throw } else { console.warn }`. So it WARNED —
     it was not silent — and published the short set. The warnings are in the log:

         2026-08-08T18:36:16.1091175Z 🤔 Pattern 'dist/*.sig' does not match any files.
         2026-08-08T18:36:16.1092970Z 🤔 Pattern 'dist/*.pem' does not match any files.

     (run 31271824251, `github-release` job, log lines 24593-24594.) A warning inside a green job
     is not distinguishable from a pass by anyone who does not read the log — which is the same
     defect as ① wearing different clothes, and it is the one nearer the user.

  ③ WHY THE PAIR WAS NEVER WRITTEN.  Not a bug in ours. `sigstore/cosign-installer` v4.1.2 passes
     no `cosign-release`, so it installs its OWN DEFAULT — cosign **v3.0.6** (`action.yml`:
     `default: 'v3.0.6'`; the run log confirms `input_cosign_release: v3.0.6`). cosign v3 defaults
     `--new-bundle-format=true`, and then:

         Flag --output-signature has been deprecated, please use --bundle to provide the output
           bundle location, which will include the signature
         Flag --output-certificate has been deprecated, please use --bundle …
         WARNING: --output-signature is deprecated when using --new-bundle-format and will be ignored
         WARNING: --output-certificate is deprecated when using --new-bundle-format and will be ignored

     Both flags are ACCEPTED (so the step exits 0), WARNED ABOUT, and IGNORED. Measured 2026-08-13
     by running the real binary — `cosign-darwin-arm64` from the sigstore/cosign v3.0.6 release,
     `GitVersion: v3.0.6` — with the workflow's exact flag set, and the four lines above came back
     verbatim. The identical four lines appear three times in run 31271824251's signing step. THE
     BUNDLE ALREADY CARRIES THE SIGNATURE AND THE CERTIFICATE; there was never anything to attach.

DESIGN CONSTRAINTS, each from something that already went wrong in this repo:

1. **PURE FUNCTIONS OVER SUPPLIED TEXT, never a walk that both discovers and judges** (doc 85 §7i).
   Once `release.yml` is fixed the tree holds no offender, so a sweep wired to `.github/workflows/`
   would pass having graded NOTHING. Every function here takes its corpus as an argument and
   `test_release_asset_set.py` hands it synthetic offenders — including the real v0.0.17 text.

2. **THE CLASS, NOT THE INSTANCE** (doc 85 §7j). `nullglob_disarmed_checks` does not look for the
   string `dist/*.sig`; it looks for ANY existence-testing command whose arguments are globs while
   `nullglob` is in effect. `unarmed_uploads` ranges over every `action-gh-release` step in the
   document, not over the one we know about.

3. **THREE STATES, NEVER TWO** (doc 85 §7g). `nullglob` in effect + a glob check is an offender;
   a glob check WITHOUT `nullglob` is not (bare `ls` fails loudly on a literal that does not
   exist); and a check on an exact path is not. Collapsing those would make the fix unprovable.

⚠ WHAT THIS DOES NOT GRADE, declared rather than discovered:

  * **cosign's behaviour is a MEASUREMENT, not a derivation.** `ignored_cosign_flags` encodes the
    rule established by running v3.0.6 above. If a future stage pins a different cosign, that rule
    must be re-measured — it cannot be read off our own files, and reading it off the docs is what
    produced the 2026-08-07 closure this stage is undoing.
  * **The v0.0.17 record is a LITERAL.** History cannot be derived. `V0017_*` below are transcribed
    from the published Release and the published run, each carrying the command that produced it.
    `release.yml` at HEAD was byte-identical to the v0.0.17 cut before this stage
    (`git diff v0.0.17 -- .github/workflows/release.yml` was empty), so these fixtures are the
    workflow as it actually shipped, not a reconstruction.
  * **Glob matching here is `fnmatch` per path SEGMENT**, not `node-glob`. It is good enough for
    the `dir/pattern` shapes `files:` uses and it is not a general re-implementation of the
    action's matcher.
"""

import fnmatch
import re

from _workflow_pins import safe_load        # the ONE representation of "the parser is absent"

__all__ = [
    "MissingParserPassthrough", "run_blocks", "nullglob_disarmed_checks", "upload_steps",
    "unarmed_uploads", "declared_upload_patterns", "unmatched_patterns", "ignored_cosign_flags",
    "script_patterns", "safe_load",
    # 0.0.18 stage 8 — the provenance asset (SIGNED-RELEASES-PROVENANCE (ii))
    "PROVENANCE_ASSET", "SCORECARD_PROVENANCE_SUFFIX", "SCORECARD_SIGNATURE_SUFFIXES",
    "ATTEST_ACTION", "PYPI_PUBLISH_ACTION", "DISTRIBUTION_SUFFIXES",
    "job_steps", "action_steps", "output_references",
]

# ---- the provenance asset, and the EXTERNAL rule that decides its name --------------------------
#
# 0.0.18 stage 8. `actions/attest-build-provenance` has been in this workflow since stage 68 — the
# row's "(ii) is one job step" describes a step that already existed. What never happened is the
# bundle LEAVING THE RUNNER: `actions/attest` writes it to `$RUNNER_TEMP/<mkdtemp>/attestation.json`
# and publishes that path as `bundle-path`, and nothing consumed the output. The attestation landed
# in GitHub's attestation store, which Scorecard does not read.
#
# ⚠ THE NAME IS AN EXTERNAL RULE, NOT OURS, AND IT IS A MEASUREMENT-CLASS FACT like the cosign
# behaviour above. Scorecard's Signed-Releases check scores a release from the SUFFIXES OF ITS
# ASSET NAMES: a signature asset scores 8 and a provenance asset scores 10, averaged over the last
# five releases. Our live 6 is 4 signed × 8 ÷ 5 = 6.4, which is exactly the arithmetic behind the
# 2026-08-04 reason string "4 out of the last 5 releases have a total of 4 signed artifacts". The
# suffix list below is what that check looks for. **If Scorecard changes the rule, this must be
# re-read — it cannot be derived from our files, and reading it off our own docs is what produced
# the 2026-08-07 closure this area has been undoing ever since.**
PROVENANCE_ASSET = "provenance.intoto.jsonl"
SCORECARD_PROVENANCE_SUFFIX = ".intoto.jsonl"
SCORECARD_SIGNATURE_SUFFIXES = (".sig", ".asc", ".minisig", ".sign", ".sigstore.json")

ATTEST_ACTION = "actions/attest-build-provenance"
PYPI_PUBLISH_ACTION = "pypa/gh-action-pypi-publish"

#: What PyPI accepts. Everything else the Release carries must be stripped before the upload, and
#: that stripping is a THIRD copy of the asset set — derived by `test_release_provenance.py`.
DISTRIBUTION_SUFFIXES = (".whl", ".tar.gz")

# `safe_load` is re-exported rather than re-implemented: `tests/_pyyaml_sweep.py` sweeps for a
# second spelling of the missing-parser path and `tests/test_pyyaml_skip_cluster.py` fails the
# build on one. This alias exists only so the name is greppable as a deliberate re-export.
MissingParserPassthrough = safe_load


# ---- ① the build surface: a glob check under nullglob ------------------------------------------

# Commands whose whole job is to answer "does this exist". Under `nullglob` a glob argument that
# matches nothing simply vanishes, so each of these succeeds on an EMPTY argument list — which is
# how `ls -l dist/*.sig dist/*.pem dist/*.sigstore.json` exited 0 with two of the three absent.
EXISTENCE_COMMANDS = ("ls", "stat", "test", "[", "readlink", "file")

_GLOB_CHARS = "*?["
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_SPLIT = re.compile(r"(?:\|\||&&|[;|&])")
_NULLGLOB_ON = re.compile(r"^[ \t]*shopt[ \t]+-s[ \t]+(?:[-\w]+[ \t]+)*nullglob\b", re.M)
_NULLGLOB_OFF = re.compile(r"^[ \t]*shopt[ \t]+-u[ \t]+(?:[-\w]+[ \t]+)*nullglob\b", re.M)


def _strip_comment(fragment):
    """Drop a trailing `#` comment, but only when the `#` starts a word outside quotes."""
    out, quote = [], None
    for i, char in enumerate(fragment):
        if quote:
            out.append(char)
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
            out.append(char)
            continue
        if char == "#" and (i == 0 or fragment[i - 1] in " \t"):
            break
        out.append(char)
    return "".join(out)


def _has_unquoted_glob(text):
    """Whether a glob metacharacter survives quoting.

    `ls "dist/*.sig"` is a LITERAL filename and fails loudly when absent — not this defect.
    `[ -f "$want" ]` has no metacharacter at all once the quotes are removed.
    """
    return any(char in _QUOTED.sub("", text) for char in _GLOB_CHARS)


def nullglob_disarmed_checks(shell_text):
    """Existence checks that `shopt -s nullglob` disarms, as `(line_number, fragment)`.

    THE CLASS, not the instance. Any of `EXISTENCE_COMMANDS` invoked with a glob argument while
    nullglob is in effect cannot fail: the argument list can legally become empty and the command
    still exits 0. The three states of §7g are kept apart —

        nullglob on  + glob argument   -> OFFENDER (cannot fail)
        nullglob off + glob argument   -> fine     (fails loudly on the literal)
        nullglob on  + exact path      -> fine     (what the fix uses)
    """
    found = []
    armed = False
    for number, line in enumerate(shell_text.splitlines(), start=1):
        if _NULLGLOB_ON.match(line):
            armed = True
        elif _NULLGLOB_OFF.match(line):
            armed = False
        if not armed:
            continue
        for fragment in _SPLIT.split(_strip_comment(line)):
            words = fragment.strip().split()
            if not words or words[0] not in EXISTENCE_COMMANDS:
                continue
            if _has_unquoted_glob(" ".join(words[1:])):
                found.append((number, fragment.strip()))
    return tuple(found)


def run_blocks(doc):
    """`[(where, run_text)]` for every step `run:` in a parsed workflow document.

    Both the job-level and step-level shapes are walked for the same reason `_workflow_pins`
    walks `jobs.<id>.uses` as well as the steps: covering only the obvious one is how the gap
    this file exists for survived in the first place.
    """
    found = []
    if not isinstance(doc, dict):
        return tuple(found)
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return tuple(found)
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for index, step in enumerate(steps):
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                found.append(("jobs.%s.steps[%d]" % (job_id, index), step["run"]))
    return tuple(found)


def job_steps(doc):
    """`[(job_id, index, step)]` for every step in the document, in document order.

    `run_blocks` answers "what shell runs"; this answers "what is the step, and WHERE in its job" —
    which is the only way to grade ORDER, and stage 8's provenance copy has to sit strictly between
    the attestation that produces the bundle and the signing that consumes the file.
    """
    found = []
    if not isinstance(doc, dict):
        return tuple(found)
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return tuple(found)
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for index, step in enumerate(steps):
            if isinstance(step, dict):
                found.append((job_id, index, step))
    return tuple(found)


def action_steps(doc, action):
    """`[(job_id, index, step)]` for every step using `action`, at ANY ref."""
    return tuple(
        (job_id, index, step) for job_id, index, step in job_steps(doc)
        if isinstance(step.get("uses"), str) and step["uses"].split("@", 1)[0] == action)


_OUTPUT_REF = re.compile(r"steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)")


def output_references(text):
    """`{(step_id, output_name)}` referenced by a `${{ }}` expression in this text.

    A step that produces an output nobody reads is the shape this stage exists to close: the
    attestation was genuinely created on every release for months, and its `bundle-path` was
    referenced nowhere at all.
    """
    return frozenset(_OUTPUT_REF.findall(text if isinstance(text, str) else ""))


# ---- ② the release surface: an upload glob nothing arms ----------------------------------------

RELEASE_ACTION = "softprops/action-gh-release"
FAIL_ON_UNMATCHED = "fail_on_unmatched_files"


def upload_steps(doc, action=RELEASE_ACTION):
    """`[(where, with_mapping)]` for every step using the release-upload action, any ref."""
    found = []
    if not isinstance(doc, dict):
        return tuple(found)
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return tuple(found)
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            uses = step.get("uses")
            if isinstance(uses, str) and uses.split("@", 1)[0] == action:
                inputs = step.get("with")
                found.append(("jobs.%s.steps[%d]" % (job_id, index),
                              inputs if isinstance(inputs, dict) else {}))
    return tuple(found)


def declared_upload_patterns(with_mapping):
    """The `files:` globs one upload step declares, as a tuple, blank lines dropped."""
    files = with_mapping.get("files")
    if not isinstance(files, str):
        return ()
    return tuple(line.strip() for line in files.splitlines() if line.strip())


def _armed(with_mapping):
    """Whether this step's unmatched-glob check is ON. YAML may give a bool or a string."""
    value = with_mapping.get(FAIL_ON_UNMATCHED)
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() == "true"


def unarmed_uploads(doc, action=RELEASE_ACTION):
    """`[(where, patterns)]` for upload steps that declare globs with the check left OFF.

    The default is FALSE and the action then only warns, so a step that declares `files:` and says
    nothing about `fail_on_unmatched_files` is publishing whatever happens to be there.
    """
    return tuple(
        (where, declared_upload_patterns(inputs))
        for where, inputs in upload_steps(doc, action)
        if declared_upload_patterns(inputs) and not _armed(inputs))


def unmatched_patterns(patterns, listing):
    """The patterns matching nothing in a SUPPLIED listing — the action's own rule, offline.

    This is what `fail_on_unmatched_files: true` now performs in CI. Having it as a function is
    what lets the v0.0.17 asset set be graded against the v0.0.17 declaration without publishing
    anything (doc 00 step 6: verify from the artifact — here, from the artifact list).
    """
    paths = tuple(listing)
    found = []
    for pattern in patterns:
        parts = pattern.split("/")
        for path in paths:
            segments = path.split("/")
            if len(segments) == len(parts) and all(
                    fnmatch.fnmatchcase(seg, pat) for seg, pat in zip(segments, parts)):
                break
        else:
            found.append(pattern)
    return tuple(found)


# ---- ③ the measured cosign rule ----------------------------------------------------------------

# Accepted, warned about, and IGNORED by cosign v3 whenever the new bundle format is in use — see
# the module docstring for the transcript and the version it was measured on.
COSIGN_IGNORED_WITH_NEW_BUNDLE = ("--output-signature", "--output-certificate")

_SIGN_BLOB = re.compile(r"cosign[ \t]+sign-blob\b")
_OLD_BUNDLE_FORMAT = re.compile(r"--new-bundle-format[= \t]+(?:false|0)\b")


def _invocations(shell_text):
    """Each `cosign sign-blob …` command as one string, line continuations folded in."""
    joined = shell_text.replace("\\\n", " ")
    found = []
    for match in _SIGN_BLOB.finditer(joined):
        end = joined.find("\n", match.end())
        found.append(joined[match.start():end if end >= 0 else len(joined)])
    return tuple(found)


def ignored_cosign_flags(shell_text):
    """`[(flag, invocation)]` for output flags cosign v3 will silently ignore.

    A flag listed here is not an error and not a warning the build can see — cosign exits 0, so
    the step is green and the file it names is simply never created. That is the whole distance
    between "a step that creates a file" and "a Release that carries it".
    """
    found = []
    for invocation in _invocations(shell_text):
        if _OLD_BUNDLE_FORMAT.search(invocation):
            continue                      # legacy format explicitly requested: the flags are honoured
        for flag in COSIGN_IGNORED_WITH_NEW_BUNDLE:
            if re.search(r"(?<![\w-])%s\b" % re.escape(flag), invocation):
                found.append((flag, invocation.strip()))
    return tuple(found)


# ---- the single declaration of the asset set ---------------------------------------------------

_PATTERN_BLOCK = re.compile(r"^SIGNED_PATTERNS='([^']*)'", re.M)
_BUNDLE_SUFFIX = re.compile(r"^BUNDLE_SUFFIX='([^']*)'", re.M)


def script_patterns(script_text):
    """`(patterns, bundle_suffix)` read out of `scripts/check-release-assets.sh` itself.

    DERIVED, so the workflow's `files:` block is graded against the script that produces the files
    rather than against a second hand-typed list — which is what the six-glob block already was,
    and two of those six named things nothing could ever produce.
    """
    block = _PATTERN_BLOCK.search(script_text)
    suffix = _BUNDLE_SUFFIX.search(script_text)
    if block is None or suffix is None:
        return None, None
    patterns = tuple(line.strip() for line in block.group(1).splitlines() if line.strip())
    return patterns, suffix.group(1)


# ---- THE v0.0.17 RECORD — literal, dated, each with the command that produced it ----------------
#
# History cannot be derived (§7j: this is a DECLARED literal, not a typed scope). Every value below
# is transcribed from a published surface, not from our own tracker — doc 00 step 6.

# $ gh release view v0.0.17 --repo JasGujral/mokata-oss --json assets \
#       --jq '.assets[].name'                                             (read 2026-08-13)
# SIX uploaded assets. The Release web page shows eight because GitHub adds two source archives
# that no workflow controls; the API list below is what `files:` actually put there.
V0017_RELEASE_ASSETS = (
    "dist/mokata-0.0.17-py3-none-any.whl",
    "dist/mokata-0.0.17-py3-none-any.whl.sigstore.json",
    "dist/mokata-0.0.17.tar.gz",
    "dist/mokata-0.0.17.tar.gz.sigstore.json",
    "dist/sbom.cdx.json",
    "dist/sbom.cdx.json.sigstore.json",
)

# $ git show v0.0.17:.github/workflows/release.yml     — the `files:` block, verbatim.
V0017_DECLARED_FILES = (
    "dist/*.whl",
    "dist/*.tar.gz",
    "dist/sbom.cdx.json",
    "dist/*.sig",
    "dist/*.pem",
    "dist/*.sigstore.json",
)

# $ git show v0.0.17:.github/workflows/release.yml     — the signing step's `run:`, verbatim.
V0017_SIGNING_RUN = """shopt -s nullglob
for f in dist/*.whl dist/*.tar.gz dist/sbom.cdx.json; do
  echo "Signing $f"
  cosign sign-blob \\
    --output-signature   "$f.sig" \\
    --output-certificate "$f.pem" \\
    --bundle             "$f.sigstore.json" \\
    "$f"
done
echo "Signature assets:"; ls -l dist/*.sig dist/*.pem dist/*.sigstore.json
"""
