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
                     * the LAST SUCCESSFULLY READ protection document for this repo@branch is in
                       the local cache, is younger than `PROTECTION_CACHE_MAX_AGE_HOURS`, and
                       itself satisfies `evaluate_protection_payload`.
                   With all of that it is DEGRADED, not green: `ok` is True, `degraded` is True,
                   and the render is a loud dated notice naming the four assurances as verified
                   only AS OF the cached read. Without it it REFUSES — and a cache MISS, a cached
                   document that DISAGREES, one that is TOO OLD and one that cannot be USED are
                   four different refusals with four different exit codes (doc-free: see
                   `CACHE_OUTCOMES`). Every refusal's remedy is retry/check-status — never a write.

THE LAST-READ CACHE (option (b) of BRANCH-PROTECTION-DEGRADED-PASS). The boolean-only pass that
stood here passed on `protected: true` alone, which says protection EXISTS and nothing about what
it forbids. The cache turns that into "we know what the full document said at <time>, and the live
boolean says protection is still on". It is written ONLY after a successful full read of the
document, and it is dated, so its age is a fact the verdict prints rather than an assumption.

FAIL-CLOSED is still the posture everywhere except the one corroborated path above, and that path
announces itself. It reads via the `gh` CLI (which supplies its own auth: a maintainer's login
locally, `GH_TOKEN` in CI); NO token is ever hard-coded here or accepted as an argument. The `gh`
layer is injectable so every state is exercised offline in tests.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

from .atomicfile import atomic_write_text

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
# The degraded path's four ways NOT to corroborate, each its own exit so a caller can tell them
# apart without parsing prose (doc 85 §7g). All four refuse; none of them suggests a write.
EXIT_CACHE_MISS = 4
EXIT_CACHE_DISAGREES = 5
EXIT_CACHE_TOO_OLD = 6
EXIT_CACHE_UNUSABLE = 7

# ------------------------------------------------------------------------------ THE CACHE OUTCOMES
# What the degraded path found when it consulted the last-read cache. `""` means the cache was never
# consulted (states 1 and 2, and a state-3 refusal that failed before reaching it).
CACHE_HIT_AGREES = "CACHE_HIT_AGREES"          # fresh, and the cached document is safe  -> exit 3
CACHE_MISS = "CACHE_MISS"                      # nothing cached for this repo@branch     -> exit 4
CACHE_HIT_DISAGREES = "CACHE_HIT_DISAGREES"    # fresh, but the cached document is NOT safe -> 5
CACHE_HIT_TOO_OLD = "CACHE_HIT_TOO_OLD"        # cached, but older than the age limit    -> exit 6
CACHE_UNUSABLE = "CACHE_UNUSABLE"              # a file is there and cannot be trusted   -> exit 7
CACHE_OUTCOMES = (CACHE_HIT_AGREES, CACHE_MISS, CACHE_HIT_DISAGREES, CACHE_HIT_TOO_OLD,
                  CACHE_UNUSABLE)
_CACHE_REFUSAL_EXIT = {
    CACHE_MISS: EXIT_CACHE_MISS,
    CACHE_HIT_DISAGREES: EXIT_CACHE_DISAGREES,
    CACHE_HIT_TOO_OLD: EXIT_CACHE_TOO_OLD,
    CACHE_UNUSABLE: EXIT_CACHE_UNUSABLE,
}

# ⭐ THE AGE LIMIT, AND WHY IT IS 24 HOURS. The cached document is the protection detail AS OF its
# read; the live `protected: true` says protection still exists NOW. What the pair cannot see is a
# detail weakened in between — force-push or deletion switched on while protection stays on — and
# only a repository admin can do that. The limit bounds how old that blind spot may be:
#   * LONG ENOUGH FOR THE CASE IT EXISTS FOR. The measured outage (2026-08-17) kept the protection
#     endpoint at 503 for over an hour, and `release.sh` reads protection at the cheap gates of
#     EVERY run, so during a cut the cache is refreshed by every attempt that gets an answer. A
#     same-day retry loop after an outage begins is inside the limit.
#   * SHORT ENOUGH TO STAY A SAME-DAY FACT. Beyond a day the document describes an earlier working
#     session, not this cut, and an outage that has outlasted a day is not a transient to release
#     through — it is a reason to wait. The verdict prints the read time and the age either way.
# A cut that spans days still works: any run on which the endpoint answers refreshes the cache.
PROTECTION_CACHE_MAX_AGE_HOURS = 24

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
RESTORE_ROW = "BRANCH-PROTECTION-DEGRADED-PASS — filed 2026-08-17; a 24h last-read cache replaces the boolean-only pass, lands in 0.0.22"

# A gh runner is (api_path) -> (returncode, stdout, stderr). Injected in tests; the default below
# shells out to `gh api`. It takes a PATH rather than (repo, branch) because state 3 has to read
# two further endpoints, and a runner that can only spell one URL cannot be used to test the
# corroboration it is supposed to prove.
GhRunner = Callable[[str], Tuple[int, str, str]]


def _default_gh_runner(path: str) -> Tuple[int, str, str]:
    """Read `gh api <path>` — gh supplies auth (login locally / GH_TOKEN in CI).
    Returns (returncode, stdout, stderr); raises FileNotFoundError when `gh` is not installed."""
    proc = subprocess.run(["gh", "api", path], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
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


def default_cache_dir() -> str:
    """Where `mokata branch-protection-check` keeps the last-read documents.

    `$XDG_CACHE_HOME/mokata/branch-protection`, else `~/.cache/mokata/branch-protection`. A cache
    is a fact about THIS machine's reads, so it lives with the user's caches and never in a repo."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "mokata", "branch-protection")


def _utc(value: _dt.datetime) -> _dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=_dt.timezone.utc)


def _now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


@dataclass
class CacheLookupResult:
    """What the last-read cache said for one repo@branch — ONE of `CACHE_OUTCOMES`, with its dates."""

    outcome: str
    path: str = ""
    read_at: str = ""
    age_hours: float = 0.0
    # Why the cached document is not safe (HIT-disagrees) or why the file is unusable.
    failures: List[str] = field(default_factory=list)


class ProtectionCache:
    """The last SUCCESSFULLY READ protection document per repo@branch, with its read time.

    One JSON file per repo@branch: `{"repo", "branch", "read_at", "document"}`. Writes go through
    `atomicfile.atomic_write_text`, so a reader never sees half a document. Nothing here raises to the
    caller: a cache that cannot be written is a later MISS, and a MISS refuses — the safe way round.
    """

    def __init__(self, directory: str):
        self.directory = directory

    def path_for(self, repo: str, branch: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", f"{repo}@{branch}")
        return os.path.join(self.directory, safe + ".json")

    def store(self, repo: str, branch: str, document: Any, read_at: _dt.datetime) -> str:
        """Record a document that was just READ IN FULL. Returns "" or why the write failed."""
        path = self.path_for(repo, branch)
        record = {"repo": repo, "branch": branch, "read_at": _utc(read_at).isoformat(),
                  "document": document}
        try:
            atomic_write_text(path, json.dumps(record, sort_keys=True))
        except (OSError, TypeError, ValueError) as exc:
            return f"the protection cache could not be written at {path}: {exc}"
        return ""

    def forget(self, repo: str, branch: str) -> str:
        """Drop the entry. Called when a SUCCESSFUL read said the branch has NO protection at all:
        that 404 carries no document to cache, and leaving an older safe document behind would let a
        later outage corroborate against protection that is known to be gone."""
        path = self.path_for(repo, branch)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            return f"the stale protection cache at {path} could not be removed: {exc}"
        return ""

    def lookup(self, repo: str, branch: str, now: _dt.datetime) -> CacheLookupResult:
        path = self.path_for(repo, branch)
        if not os.path.exists(path):
            return CacheLookupResult(CACHE_MISS, path=path)

        def unusable(why: str) -> CacheLookupResult:
            return CacheLookupResult(CACHE_UNUSABLE, path=path, failures=[why])

        try:
            with open(path, encoding="utf-8") as fh:
                record = json.load(fh)
        except (OSError, ValueError) as exc:
            return unusable(f"it could not be read as JSON ({exc})")
        if not isinstance(record, dict) or "document" not in record:
            return unusable("it is not a cache record (no `document`)")
        if record.get("repo") != repo or record.get("branch") != branch:
            return unusable(f"it records {record.get('repo')!r}@{record.get('branch')!r}, not "
                            f"{repo!r}@{branch!r}")
        try:
            read_at = _utc(_dt.datetime.fromisoformat(str(record.get("read_at"))))
        except (TypeError, ValueError):
            return unusable(f"its read time {record.get('read_at')!r} is not a timestamp")
        age = _utc(now) - read_at
        read_iso = read_at.isoformat()
        if age.total_seconds() < 0:
            return unusable(f"its read time {read_iso} is in the FUTURE of this clock, so its age "
                            "cannot be known")
        hours = age.total_seconds() / 3600.0
        if hours > PROTECTION_CACHE_MAX_AGE_HOURS:
            return CacheLookupResult(CACHE_HIT_TOO_OLD, path=path, read_at=read_iso,
                                     age_hours=hours)
        safe, failures = evaluate_protection_payload(record["document"])
        if not safe:
            return CacheLookupResult(CACHE_HIT_DISAGREES, path=path, read_at=read_iso,
                                     age_hours=hours, failures=failures)
        return CacheLookupResult(CACHE_HIT_AGREES, path=path, read_at=read_iso, age_hours=hours)


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
    # State 3 only: which of `CACHE_OUTCOMES` the degraded path found ("" = never consulted).
    cache_outcome: str = ""
    # State 3 only, on a HIT: when the cached document was read and how old it was.
    cache_read_at: str = ""
    cache_age_hours: float = 0.0
    # Set when the cache could not be written or cleared. Not part of any render: it never changes
    # the verdict (a failed write is a later MISS, which refuses), and the CLI prints it to stderr.
    cache_note: str = ""

    @property
    def exit_code(self) -> int:
        if self.state == STATE_PROTECTED:
            return EXIT_PROTECTED
        if self.state == STATE_NOT_PROTECTED:
            return EXIT_NOT_PROTECTED
        if not self.ok and self.cache_outcome in _CACHE_REFUSAL_EXIT:
            return _CACHE_REFUSAL_EXIT[self.cache_outcome]
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
        why = (f" — last-read cache: {self.cache_outcome}" if self.cache_outcome else "")
        lines = [f"branch-protection FAIL — {self.repo}@{self.branch}: the protection detail is "
                 f"UNREADABLE and could NOT be corroborated ({self.observed_on}){why}:"]
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
            "THIS IS NOT A GREEN. The protection document could not be read LIVE, so the check",
            "passed on the LAST SUCCESSFULLY READ DOCUMENT plus live corroborating reads.",
            "",
            f"  unreadable: {self.unreadable_because}",
            f"  last read:  {self.cache_read_at} ({self.cache_age_hours:.1f}h ago; the limit is "
            f"{PROTECTION_CACHE_MAX_AGE_HOURS}h)",
            "",
            "  corroborated (every read SUCCEEDED — a failed read would have refused):",
        ]
        for c in self.corroboration:
            lines.append(f"    ✓ {c}")
        lines.append("")
        lines.append(f"  NOT OBTAINED LIVE — these four held at {self.cache_read_at}, not verified now:")
        for a in UNOBTAINED_ASSURANCES:
            lines.append(f"    ✗ {a}")
        lines.append("")
        lines.append("  It proceeded on the cached document and the live boolean. Record this against")
        lines.append("  the release it let through, with today's date, wherever that release is recorded.")
        lines.append(f"  Bounded by the cache age limit ({PROTECTION_CACHE_MAX_AGE_HOURS}h): {RESTORE_ROW}")
        lines.append("=" * 78)
        return "\n".join(lines)

    def render(self) -> str:
        if self.state == STATE_PROTECTED:
            return self._render_pass()
        if self.state == STATE_NOT_PROTECTED:
            return self._render_not_protected()
        return self._render_degraded() if self.ok else self._render_unreadable_refusal()


def _corroborate(run: GhRunner, repo: str, branch: str, unreadable_because: str,
                 cache: Optional[ProtectionCache] = None,
                 now: Optional[Callable[[], _dt.datetime]] = None) -> ProtectionVerdict:
    """STATE 3. The protection detail is unreadable — can it be positively corroborated?

    It passes ONLY on evidence that was actually READ. Every arm below that returns a refusal is
    the same rule stated once more: a read that did not succeed proves nothing, and must not be
    allowed to mean "fine" merely because it happened one level below the read that failed."""

    def refuse(extra: List[str], cache_outcome: str = "") -> ProtectionVerdict:
        return ProtectionVerdict(
            repo=repo, branch=branch, ok=False, state=STATE_UNREADABLE,
            failures=[f"could not read the protection detail: {unreadable_because}"] + extra,
            unreadable_because=unreadable_because, cache_outcome=cache_outcome)

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

    # ⭐ THE LAST-READ CACHE — what turns "protection exists" into "and here is what it said".
    # Four ways not to corroborate, four refusals, four exits; only a fresh, safe document passes.
    clock = now if now is not None else _now_utc
    if cache is None:
        hit = CacheLookupResult(CACHE_MISS, path="<no cache was supplied to this check>")
    else:
        hit = cache.lookup(repo, branch, clock())
    limit = f"{PROTECTION_CACHE_MAX_AGE_HOURS}h"
    if hit.outcome == CACHE_MISS:
        return refuse([f"no protection document has been read successfully for {repo}@{branch} "
                       f"on this machine ({hit.path}), so there is nothing to corroborate the "
                       "boolean against — `protected: true` alone is not a pass"], CACHE_MISS)
    if hit.outcome == CACHE_UNUSABLE:
        return refuse([f"the last-read cache at {hit.path} cannot be used: "
                       + "; ".join(hit.failures)], CACHE_UNUSABLE)
    if hit.outcome == CACHE_HIT_TOO_OLD:
        return refuse([f"the last successfully read protection document is {hit.age_hours:.1f}h "
                       f"old (read {hit.read_at}); the limit is {limit}, so it no longer "
                       "describes this cut"], CACHE_HIT_TOO_OLD)
    if hit.outcome == CACHE_HIT_DISAGREES:
        return refuse([f"the last successfully read protection document (read {hit.read_at}) "
                       "does NOT show safe protection — " + "; ".join(hit.failures)
                       + " — while the live boolean says protected: the two disagree"],
                      CACHE_HIT_DISAGREES)

    count = len(r_payload)
    return ProtectionVerdict(
        repo=repo, branch=branch, ok=True, state=STATE_UNREADABLE, degraded=True,
        unreadable_because=unreadable_because,
        cache_outcome=CACHE_HIT_AGREES, cache_read_at=hit.read_at,
        cache_age_hours=hit.age_hours,
        corroboration=[
            f"{branch_path} → protected: true",
            f"{rulesets_path} → {count} ruleset(s), all consistent"
            + (" (empty — classic protection only)" if count == 0 else ""),
            f"the last-read document (read {hit.read_at}, {hit.age_hours:.1f}h ago, limit "
            f"{limit}) → force-push and deletion disabled, required status checks configured",
        ])


def check_branch_protection(
    repo: str = DEFAULT_REPO,
    branch: str = DEFAULT_BRANCH,
    runner: Optional[GhRunner] = None,
    cache: Optional[ProtectionCache] = None,
    now: Optional[Callable[[], _dt.datetime]] = None,
) -> ProtectionVerdict:
    """Classify `repo`@`branch` into one of the THREE states. NEVER raises.

    A release proceeds only on `ok` — which is state 1, or state 3 corroborated by live reads AND a
    fresh, safe last-read document, with a loud dated notice. Everything else refuses. No token is
    hard-coded or accepted. With `cache=None` nothing is cached and state 3 is always a MISS."""
    run = runner if runner is not None else _default_gh_runner
    clock = now if now is not None else _now_utc

    detail_path = f"repos/{repo}/branches/{branch}/protection"
    read_ok, payload, error = _read_json(run, detail_path)

    if read_ok:
        # ⭐ THE ONLY CACHE WRITE: after a SUCCESSFUL FULL READ of the document, whatever it says.
        # An unsafe document is cached too, so a later outage corroborates against what was really
        # last seen (HIT-disagrees) instead of an older safe one.
        note = cache.store(repo, branch, payload, clock()) if cache is not None else ""
        ok, failures = evaluate_protection_payload(payload)
        if ok:
            return ProtectionVerdict(repo=repo, branch=branch, ok=True, state=STATE_PROTECTED,
                                     cache_note=note)
        return ProtectionVerdict(repo=repo, branch=branch, ok=False,
                                 state=STATE_NOT_PROTECTED, failures=failures, cache_note=note)

    if reads_as_unprotected(error):
        note = cache.forget(repo, branch) if cache is not None else ""
        return ProtectionVerdict(
            repo=repo, branch=branch, ok=False, state=STATE_NOT_PROTECTED,
            failures=[f"the branch has NO protection at all (GitHub answered: {error})"],
            cache_note=note)

    return _corroborate(run, repo, branch, error, cache=cache, now=clock)


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
    "EXIT_CACHE_MISS",
    "EXIT_CACHE_DISAGREES",
    "EXIT_CACHE_TOO_OLD",
    "EXIT_CACHE_UNUSABLE",
    "CACHE_HIT_AGREES",
    "CACHE_MISS",
    "CACHE_HIT_DISAGREES",
    "CACHE_HIT_TOO_OLD",
    "CACHE_UNUSABLE",
    "CACHE_OUTCOMES",
    "PROTECTION_CACHE_MAX_AGE_HOURS",
    "CacheLookupResult",
    "ProtectionCache",
    "default_cache_dir",
    "UNOBTAINED_ASSURANCES",
    "RESTORE_ROW",
    "ProtectionVerdict",
    "check_branch_protection",
    "evaluate_protection_payload",
    "evaluate_rulesets_payload",
    "reads_as_unprotected",
]
