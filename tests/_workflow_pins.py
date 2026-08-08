"""The action-pinning sweep — a PURE FUNCTION over a SUPPLIED CORPUS of workflow files.

Stage 10 (PIN-SUBSTRING-COMMENT-HOLE breadth). Before this, exactly one test in the suite made a
generic SHA-pinning assertion — `test_stage68_supply_chain.py::test_pypi_actions_are_sha_pinned`
— and it iterated `yaml.safe_load(release.yml)["jobs"]["pypi"]["steps"]`: ONE job of ONE of the
nine workflows. Everything else was pinned bespokely or not at all.

TWO DESIGN CONSTRAINTS, both from things that already went wrong here:

1. IT TAKES A CORPUS DIRECTORY, it does not go and find the repo. Every reference in the real
   tree is already correctly pinned, so a sweep hard-wired to `.github/workflows/` would run
   green forever while grading NOTHING — the exact shape doc 85 §7i convicted. Because the corpus
   is a parameter, the tests can hand it a synthetic offender and watch it go red. A sweep that
   has only ever seen a healthy tree is a comment.

2. IT PARSES, IT DOES NOT GREP. Doc 84's PIN-SUBSTRING-COMMENT-HOLE lesson, in its own words:
   "a pin whose scanned region contains prose about itself is not a pin; prefer PARSING the
   structure (YAML/array) over grepping the file." A `grep 'uses:.*@[0-9a-f]\\{40\\}'` is
   satisfied by a COMMENT showing a correctly-pinned example, which is precisely what a workflow
   documenting its own pinning policy would contain.

AND IT FAILS LOUD WITHOUT PyYAML. The 17 other PyYAML call sites in this suite `skipTest` when
the parser is absent, so on any runner without it the property is simply not checked and the run
still reads OK — an absent answer wearing a pass (§7g). `MissingParser` is raised instead, and
`test_s10_workflow_pins.py` pins that the missing-parser path RAISES rather than skips.
"""

import os
import re

SHA40 = re.compile(r"^[0-9a-f]{40}$")

# What a `uses:` value can be, and what each shape must carry to count as pinned.
KIND_REMOTE = "remote"    # owner/repo[/subpath]@ref     -> ref must be a 40-hex commit SHA
KIND_LOCAL = "local"      # ./path/to/action             -> lives in this repo, nothing to pin
KIND_DOCKER = "docker"    # docker://image[:tag|@digest] -> must carry an immutable @sha256: digest


class MissingParser(RuntimeError):
    """PyYAML is absent, so the sweep CANNOT ANSWER — distinct from 'found no offenders'.

    Raised rather than skipped on purpose. A skip here means the property goes unchecked while
    the run still reports OK, which is how a workflow could lose its pins on a runner missing a
    test-only dependency and nothing would say so.
    """


class ActionRef:
    """One `uses:` reference, with enough provenance to name it in a failure message."""

    __slots__ = ("workflow", "where", "uses", "kind", "ref")

    def __init__(self, workflow, where, uses):
        self.workflow = workflow
        self.where = where          # e.g. "jobs.build.steps[2]"
        self.uses = uses
        if uses.startswith("./") or uses.startswith("../"):
            self.kind, self.ref = KIND_LOCAL, ""
        elif uses.startswith("docker://"):
            self.kind, self.ref = KIND_DOCKER, uses.split("@", 1)[1] if "@" in uses else ""
        else:
            self.kind, self.ref = KIND_REMOTE, uses.split("@", 1)[1] if "@" in uses else ""

    @property
    def pinned(self):
        """Whether this reference is immutable. A LOCAL action has nothing to pin — it ships in
        this repo and moves only when the repo does — so it is vacuously pinned."""
        if self.kind == KIND_LOCAL:
            return True
        if self.kind == KIND_DOCKER:
            return self.ref.startswith("sha256:")
        return bool(SHA40.match(self.ref))

    def __repr__(self):
        return "%s %s -> %s" % (self.workflow, self.where, self.uses)


def safe_load(text):
    """Parse YAML, or raise MissingParser. Imported INSIDE the call so a test can make the
    import fail and prove this path raises instead of skipping."""
    try:
        import yaml
    except ImportError as exc:                                  # pragma: no cover - proven by test
        raise MissingParser(
            "PyYAML is required to sweep workflow action pins and is not installed. This is a "
            "HARD FAILURE, not a skip: skipping would report OK while every action pin in "
            ".github/workflows/ went unchecked. Install it (requirements/ci.txt)."
        ) from exc
    return yaml.safe_load(text)


def workflow_files(corpus_dir):
    """Every workflow file in the corpus, sorted. Name-only, so callers can print them."""
    return tuple(sorted(
        n for n in os.listdir(corpus_dir) if n.endswith((".yml", ".yaml"))))


def action_refs(corpus_dir):
    """EVERY `uses:` in the corpus, wherever it appears.

    Both places one can occur are walked, because covering only steps is how a gap like the
    original single-job sweep survives:

      * `jobs.<id>.steps[<i>].uses`  — an action inside a job;
      * `jobs.<id>.uses`             — a REUSABLE WORKFLOW call, which takes a ref just like an
                                       action does and is just as mutable when tag-pinned.
    """
    found = []
    for name in workflow_files(corpus_dir):
        with open(os.path.join(corpus_dir, name), encoding="utf-8") as fh:
            doc = safe_load(fh.read())
        if not isinstance(doc, dict):
            continue
        jobs = doc.get("jobs")
        if not isinstance(jobs, dict):
            continue
        for job_id, job in jobs.items():
            if not isinstance(job, dict):
                continue
            if isinstance(job.get("uses"), str):
                found.append(ActionRef(name, "jobs.%s" % job_id, job["uses"]))
            steps = job.get("steps")
            if not isinstance(steps, list):
                continue
            for i, step in enumerate(steps):
                if isinstance(step, dict) and isinstance(step.get("uses"), str):
                    found.append(ActionRef(name, "jobs.%s.steps[%d]" % (job_id, i), step["uses"]))
    return tuple(found)


def unpinned(corpus_dir):
    """The offenders: every reference in the corpus that is not immutably pinned."""
    return tuple(r for r in action_refs(corpus_dir) if not r.pinned)


def describe(refs):
    """A failure message that names WHICH reference in WHICH workflow, not just a count."""
    return "\n".join(
        "  %s  %s\n      uses: %s\n      ref : %s"
        % (r.workflow, r.where, r.uses, r.ref or "<none — no @ref at all>")
        for r in refs)
