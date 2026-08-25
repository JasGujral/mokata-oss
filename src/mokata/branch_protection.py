"""Branch-protection VERIFICATION for the public mirror's default branch — THREE states.

The public repo (`JasGujral/mokata-oss`) must ship from a protected `main`: no force-push, no
deletion, required status checks configured. This module is the standalone check the release
preflight runs (`mokata branch-protection-check`) so no release proceeds on an unprotected branch.

WHY THIS FILE WAS REBUILT (0.0.18, 2026-08-17). It used to have TWO states — protected / not
protected — and it reported "I could not read the protection detail" as "the branch is NOT safely
protected". Those are different facts, and collapsing them is doc 85 §7g: the release script's own
instance of the defect the release exists to kill. It surfaced for real when GitHub returned 503 on
BOTH `/branches/<b>/protection` (REST) and `branchProtectionRules` (GraphQL) for over an hour while
every other endpoint answered 200 and the rate limit sat untouched at 5000/5000.

A SECOND defect rode the first: on a READ FAILURE the fix text printed an APPLY-PROTECTION remedy —
a destructive `PUT` that overwrites whatever protection exists with a template. Under time pressure
at a cut most operators would paste it, and a transient 503 would become real data loss. A read
error must never suggest a write, and no message this module produces on an unreadable read now
contains a `PUT`.

The three states:

  1. PROTECTED     `.../protection` READ and satisfies `evaluate_protection_payload`. PASS.
  2. NOT PROTECTED a read SUCCEEDED and shows protection absent or too weak (a `200` with unsafe
                   settings, or the `404 Branch not protected` GitHub answers when there is none).
                   REFUSE — and this is the ONE state that carries the apply-protection remedy,
                   because here we KNOW protection is missing.
  3. UNREADABLE    the detail could not be obtained at all (5xx, 403, gh missing, unparseable
                   body). This state must EARN its pass and it earns it from POSITIVE evidence:
                     * `repos/<repo>/branches/<branch>` READS and says `protected == true`, AND
                     * `repos/<repo>/rulesets` READS and is consistent, AND
                     * both of those reads actually SUCCEEDED — a failed corroborating read is not
                       a pass. Re-admitting "could not read ⇒ fine" one level down would be the
                       very bug being fixed, wearing a hat.
                   With corroboration it is DEGRADED, not green: `ok` is True, `degraded` is True,
                   and the render is a loud dated notice naming the four assurances that were NOT
                   obtained. Without corroboration it REFUSES, and its remedy is retry/check-status
                   — never a write.

FAIL-CLOSED is still the posture everywhere except the one corroborated path above, and that path
announces itself. It reads via the `gh` CLI (which supplies its own auth: a maintainer's login
locally, `GH_TOKEN` in CI); NO token is ever hard-coded here or accepted as an argument. The `gh`
layer is injectable so every state is exercised offline in tests.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import datetime as _dt
import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

DEFAULT_REPO = "JasGujral/mokata-oss"
DEFAULT_BRANCH = "main"

# ---------------------------------------------------------------------------------- THE STATES
# Named, because a state that only exists as a bool pair is a state nobody can assert on.
STATE_PROTECTED = "PROTECTED"
STATE_NOT_PROTECTED = "NOT_PROTECTED"
STATE_UNREADABLE = "UNREADABLE"

# Exit codes the CLI maps these onto. The DEGRADED pass is deliberately NON-ZERO: it is a pass only
# for a caller that has been taught what it means, and any caller that has not been taught reads it
# as a refusal — which is the safe way round. `release.sh` handles 3 explicitly.
EXIT_PROTECTED = 0
EXIT_NOT_PROTECTED = 1
EXIT_UNREADABLE = 2
EXIT_DEGRADED = 3

# The four assurances that live ONLY in the `.../protection` document. When that document is
# unreadable these are exactly what is not known — the degraded notice names them one by one rather
# than saying "some detail", because "some detail" is how an exemption stops being read.
UNOBTAINED_ASSURANCES = (
    "force-push is disabled          (allow_force_pushes.enabled == false)",
    "branch deletion is disabled     (allow_deletions.enabled == false)",
    "status checks are STRICT        (required_status_checks.strict)",
    "the required check CONTEXTS     (required_status_checks.contexts)",
)

# The dated row that must remove this exemption. An exemption with no expiry is
# RELEASE-SH-DEV-CI-WAIVER-OUTLIVED-ITS-SCOPE, which survived seven releases before stage 7 of
# 0.0.18 closed it; naming the row in the notice itself is the cheapest thing that stops a repeat.
#
# ⚠ SELF-CONTAINED ON PURPOSE — no internal doc number, no internal directory. This module SHIPS,
# and the mirror drops the maintainers' planning tree, so a notice pointing a reader into it would
# be a dangling pointer for every OSS user. `tests/test_footer2_src_boundary.py` reds on that and
# caught it in this very change — twice, the second time on the comment that explained the first,
# because it greps the file rather than the strings. The doc-NUMBER form it cannot see at all,
# which is why this paragraph exists rather than a one-word note. The maintainer-facing version of
# this text — which DOES name the exact files to update — lives in `release.sh`, which never ships.
#
# ⚠ IT SAID 0.0.19 AND THAT WAS FALSE, NOT STALE (0.0.19 row B5, decided by reading 2026-08-20).
# The two readings were not equally available: STALE would mean the restore had already happened and
# the sentence merely outlived it, and FALSE means it is still owed and named the wrong release. The
# 0.0.18 cut recorded `branch-protection-check` as a TRUE pass once the GitHub 503 cleared, which is
# what makes stale look plausible — but that pass says the exemption was never SPENT, not that full
# verification was RESTORED. The degrade path is still here, the four `UNOBTAINED_ASSURANCES` are
# still unobtainable when the endpoint 503s, and the row that owns the work is OPEN and assigns it to
# 0.0.20 with two candidate mechanisms and neither chosen. 0.0.19's scope carries none of it. So the
# promise was live and pointed at a release that would not keep it — which is exactly the defect B5
# exists to catch, in `src/` rather than in the notes, and it is now the row's own regression test:
# `mokata release-notes-check` reds on this constant if the release it names stops owning the item.
RESTORE_ROW = "BRANCH-PROTECTION-DEGRADED-PASS — filed 2026-08-17, restore full verification in 0.0.20"

# A gh runner is (api_path) -> (returncode, stdout, stderr). Injected in tests; the default below
# shells out to `gh api`. It takes a PATH rather than (repo, branch) because state 3 has to read
# two further endpoints, and a runner that can only spell one URL cannot be used to test the
# corroboration it is supposed to prove.
GhRunner = Callable[[str], Tuple[int, str, str]]


def _default_gh_runner(path: str) -> Tuple[int, str, str]:
    """Read `gh api <path>` — gh supplies auth (login locally / GH_TOKEN in CI).
    Returns (returncode, stdout, stderr); raises FileNotFoundError when `gh` is not installed."""
    proc = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _read_json(run: GhRunner, path: str) -> Tuple[bool, Any, str]:
    """Read one `gh api` document. Returns (read_ok, payload, error).

    `read_ok` is FALSE for every inability to OBTAIN the document — gh missing, non-zero exit,
    body that will not parse. It is never "the document said no": that distinction belongs to the
    caller, and conflating the two is the §7g defect this module was rebuilt to remove."""
    try:
        code, out, err = run(path)
    except FileNotFoundError:
        return False, None, "`gh` CLI not found — cannot read GitHub"
    except Exception as exc:  # any gh invocation error is an unreadable document, never a verdict
        return False, None, f"error invoking `gh` for {path}: {exc}"

    if code != 0:
        detail = (err or out or "").strip().splitlines()
        msg = detail[0] if detail else f"`gh` exited {code}"
        return False, None, f"gh exit {code} on {path}: {msg}"

    try:
        return True, json.loads(out), ""
    except (ValueError, TypeError):
        return False, None, f"the response to {path} was not valid JSON"


def reads_as_unprotected(error: str) -> bool:
    """Does a FAILED protection read nevertheless STATE that protection is absent?

    GitHub answers `/branches/<b>/protection` with `404 Branch not protected` when the branch has
    no protection at all. That is not an unreadable document — it is the document, and it says no.
    Classifying it as state 2 keeps the apply-protection remedy attached to the one case where it
    is the correct thing to do. Every OTHER non-zero exit (5xx, 403, DNS, gh missing) is state 3."""
    return "branch not protected" in (error or "").lower()


def evaluate_protection_payload(payload: Any) -> Tuple[bool, List[str]]:
    """PURE predicate over a parsed `.../protection` payload. Returns (ok, failures).

    Fail-closed: anything other than an object that explicitly disables force-push AND deletion AND
    configures required status checks is unsafe. A missing/ambiguous field is a failure, never a pass.
    """
    if not isinstance(payload, dict):
        return False, ["branch-protection response was not a JSON object"]

    failures: List[str] = []

    fp = payload.get("allow_force_pushes")
    if not isinstance(fp, dict) or fp.get("enabled") is not False:
        failures.append("force-push is not disabled")

    dl = payload.get("allow_deletions")
    if not isinstance(dl, dict) or dl.get("enabled") is not False:
        failures.append("branch deletion is not disabled")

    rsc = payload.get("required_status_checks")
    if not isinstance(rsc, dict):
        failures.append("required status checks are not configured")

    return (not failures), failures


def evaluate_rulesets_payload(payload: Any) -> Tuple[bool, List[str]]:
    """PURE predicate over a parsed `repos/<repo>/rulesets` payload. Returns (consistent, reasons).

    CONSISTENT means: the documented shape (a JSON array), and every ruleset in it is ACTIVELY
    enforced. An empty array is consistent — that is what a repo protected the CLASSIC way returns,
    and it is exactly the picture observed when this state was first hit.

    A ruleset in `evaluate` or `disabled` mode is protection that is CONFIGURED and NOT APPLIED.
    Standing beside a `protected: true` whose detail we cannot read, it makes the picture ambiguous
    — we cannot tell whether the protection we are trusting is the classic one or the one that is
    switched off. An ambiguous corroboration is not a corroboration, so it refuses."""
    if not isinstance(payload, list):
        return False, ["the rulesets response was not a JSON array"]

    reasons: List[str] = []
    for entry in payload:
        if not isinstance(entry, dict):
            reasons.append("a rulesets entry was not a JSON object")
            continue
        enforcement = entry.get("enforcement")
        if enforcement != "active":
            name = entry.get("name") or entry.get("id") or "<unnamed>"
            reasons.append(
                f"ruleset {name!r} is enforcement={enforcement!r}, not 'active' — configured "
                "but not applied, so it cannot corroborate anything")
    return (not reasons), reasons


@dataclass
class ProtectionVerdict:
    """Which of the THREE states the branch is in, and whether a release may proceed."""

    repo: str
    branch: str
    ok: bool
    state: str = STATE_PROTECTED
    failures: List[str] = field(default_factory=list)
    # State 3 only: True when the pass was bought with corroboration instead of the real document.
    degraded: bool = False
    # What the corroborating reads actually established, in the operator's words.
    corroboration: List[str] = field(default_factory=list)
    # Why the protection document could not be read (state 3 only).
    unreadable_because: str = ""
    # The DATE this verdict was formed. A degraded pass that is not dated cannot be aged out.
    observed_on: str = field(default_factory=lambda: _dt.date.today().isoformat())

    @property
    def exit_code(self) -> int:
        if self.state == STATE_PROTECTED:
            return EXIT_PROTECTED
        if self.state == STATE_NOT_PROTECTED:
            return EXIT_NOT_PROTECTED
        return EXIT_DEGRADED if self.ok else EXIT_UNREADABLE

    # -------------------------------------------------------------------------------- rendering
    def _render_pass(self) -> str:
        return (f"branch-protection PASS — {self.repo}@{self.branch} is protected "
                "(no force-push, no deletion, required status checks).")

    def _render_not_protected(self) -> str:
        lines = [f"branch-protection FAIL — {self.repo}@{self.branch} is NOT safely protected:"]
        for f in self.failures:
            lines.append(f"  ✗ {f}")
        # THE ONE PLACE A `PUT` MAY APPEAR. We got a readable answer and it says protection is
        # absent or too weak, so applying it destroys nothing that was there.
        lines.append(
            "  fix: apply protection (admin) —\n"
            f"       gh api -X PUT repos/{self.repo}/branches/{self.branch}/protection --input - <<'JSON'\n"
            '       { "required_status_checks": {"strict": true, "contexts": []}, "enforce_admins": false,\n'
            '         "required_pull_request_reviews": null, "restrictions": null,\n'
            '         "allow_force_pushes": false, "allow_deletions": false }\n'
            "       JSON\n"
            "       (and ensure `gh` is installed + authenticated — GH_TOKEN in CI). "
            "This restores the OSS-required branch-protection policy (no force-push/deletion "
            "+ required status checks)."
        )
        return "\n".join(lines)

    def _render_unreadable_refusal(self) -> str:
        lines = [f"branch-protection FAIL — {self.repo}@{self.branch}: the protection detail is "
                 f"UNREADABLE and could NOT be corroborated ({self.observed_on}):"]
        for f in self.failures:
            lines.append(f"  ✗ {f}")
        # NO `PUT` HERE, EVER. This is a READ fault; a write suggested against a read fault is how
        # a transient 503 becomes an overwrite of real protection with a template.
        lines.append(
            "  fix: this is a READ failure, not a protection failure — do NOT apply protection to\n"
            "       clear it. Retry once GitHub answers again:\n"
            "         check   https://www.githubstatus.com\n"
            f"         retry   gh api repos/{self.repo}/branches/{self.branch}/protection\n"
            "       If a readable response then shows protection genuinely ABSENT, THAT state\n"
            "       prints the apply-protection remedy. This one never will."
        )
        return "\n".join(lines)

    def _render_degraded(self) -> str:
        lines = [
            "=" * 78,
            f"⚠⚠ branch-protection DEGRADED — {self.repo}@{self.branch} — {self.observed_on}",
            "=" * 78,
            "THIS IS NOT A GREEN. The protection document could not be read, so the check passed on",
            "CORROBORATING BOOLEANS ALONE.",
            "",
            f"  unreadable: {self.unreadable_because}",
            "",
            "  corroborated (both reads SUCCEEDED — a failed read would have refused):",
        ]
        for c in self.corroboration:
            lines.append(f"    ✓ {c}")
        lines.append("")
        lines.append("  NOT OBTAINED — these four assurances were NOT verified for this release:")
        for a in UNOBTAINED_ASSURANCES:
            lines.append(f"    ✗ {a}")
        lines.append("")
        lines.append("  It proceeded on the corroborating boolean alone. Record this against the")
        lines.append("  release it let through, with today's date, wherever that release is recorded.")
        lines.append(f"  This exemption EXPIRES with: {RESTORE_ROW}")
        lines.append("=" * 78)
        return "\n".join(lines)

    def render(self) -> str:
        if self.state == STATE_PROTECTED:
            return self._render_pass()
        if self.state == STATE_NOT_PROTECTED:
            return self._render_not_protected()
        return self._render_degraded() if self.ok else self._render_unreadable_refusal()


def _corroborate(run: GhRunner, repo: str, branch: str, unreadable_because: str) -> ProtectionVerdict:
    """STATE 3. The protection detail is unreadable — can it be positively corroborated?

    It passes ONLY on evidence that was actually READ. Every arm below that returns a refusal is
    the same rule stated once more: a read that did not succeed proves nothing, and must not be
    allowed to mean "fine" merely because it happened one level below the read that failed."""

    def refuse(extra: List[str]) -> ProtectionVerdict:
        return ProtectionVerdict(
            repo=repo, branch=branch, ok=False, state=STATE_UNREADABLE,
            failures=[f"could not read the protection detail: {unreadable_because}"] + extra,
            unreadable_because=unreadable_because)

    branch_path = f"repos/{repo}/branches/{branch}"
    b_ok, b_payload, b_err = _read_json(run, branch_path)
    if not b_ok:
        return refuse([f"and the corroborating read of {branch_path} ALSO failed: {b_err} — a "
                       "failed read is not a pass"])
    if not isinstance(b_payload, dict):
        return refuse([f"the corroborating read of {branch_path} was not a JSON object"])
    protected = b_payload.get("protected")
    if protected is not True:
        return refuse([f"{branch_path} reads `protected: {protected!r}` — the corroborating read "
                       "SUCCEEDED and does not say the branch is protected"])

    rulesets_path = f"repos/{repo}/rulesets"
    r_ok, r_payload, r_err = _read_json(run, rulesets_path)
    if not r_ok:
        return refuse([f"and the corroborating read of {rulesets_path} ALSO failed: {r_err} — a "
                       "failed read is not a pass"])
    consistent, reasons = evaluate_rulesets_payload(r_payload)
    if not consistent:
        return refuse([f"{rulesets_path} read but is not consistent: " + "; ".join(reasons)])

    count = len(r_payload)
    return ProtectionVerdict(
        repo=repo, branch=branch, ok=True, state=STATE_UNREADABLE, degraded=True,
        unreadable_because=unreadable_because,
        corroboration=[
            f"{branch_path} → protected: true",
            f"{rulesets_path} → {count} ruleset(s), all consistent"
            + (" (empty — classic protection only)" if count == 0 else ""),
        ])


def check_branch_protection(
    repo: str = DEFAULT_REPO,
    branch: str = DEFAULT_BRANCH,
    runner: Optional[GhRunner] = None,
) -> ProtectionVerdict:
    """Classify `repo`@`branch` into one of the THREE states. NEVER raises.

    A release proceeds only on `ok` — which is state 1, or state 3 with positive corroboration and
    a loud dated notice. Everything else refuses. No token is hard-coded or accepted."""
    run = runner if runner is not None else _default_gh_runner

    detail_path = f"repos/{repo}/branches/{branch}/protection"
    read_ok, payload, error = _read_json(run, detail_path)

    if read_ok:
        ok, failures = evaluate_protection_payload(payload)
        if ok:
            return ProtectionVerdict(repo=repo, branch=branch, ok=True, state=STATE_PROTECTED)
        return ProtectionVerdict(repo=repo, branch=branch, ok=False,
                                 state=STATE_NOT_PROTECTED, failures=failures)

    if reads_as_unprotected(error):
        return ProtectionVerdict(
            repo=repo, branch=branch, ok=False, state=STATE_NOT_PROTECTED,
            failures=[f"the branch has NO protection at all (GitHub answered: {error})"])

    return _corroborate(run, repo, branch, error)


__all__ = [
    "DEFAULT_REPO",
    "DEFAULT_BRANCH",
    "STATE_PROTECTED",
    "STATE_NOT_PROTECTED",
    "STATE_UNREADABLE",
    "EXIT_PROTECTED",
    "EXIT_NOT_PROTECTED",
    "EXIT_UNREADABLE",
    "EXIT_DEGRADED",
    "UNOBTAINED_ASSURANCES",
    "RESTORE_ROW",
    "ProtectionVerdict",
    "check_branch_protection",
    "evaluate_protection_payload",
    "evaluate_rulesets_payload",
    "reads_as_unprotected",
]
