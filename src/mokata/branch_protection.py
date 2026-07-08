"""Branch-protection VERIFICATION for the public mirror's default branch — FAIL-CLOSED.

The public repo (`JasGujral/mokata-oss`) must ship from a protected `main`: no force-push, no
deletion, required status checks configured. This module is the standalone check the release
preflight runs (`mokata branch-protection-check`) so no release proceeds on an unprotected branch.

FAIL-CLOSED is the whole point: if protection is absent, `gh` is unavailable/unauthed, or the API
errors in ANY way, the verdict is FAIL — never a silent pass. It reads protection via the `gh` CLI
(which supplies its own auth: a maintainer's login locally, `GH_TOKEN` in CI); NO token is ever
hard-coded here or accepted as an argument. The `gh` layer is injectable so it can be exercised
offline in tests.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

DEFAULT_REPO = "JasGujral/mokata-oss"
DEFAULT_BRANCH = "main"

# A gh runner is (repo, branch) -> (returncode, stdout, stderr). Injected in tests; the default
# below shells out to `gh api`.
GhRunner = Callable[[str, str], Tuple[int, str, str]]


def _default_gh_runner(repo: str, branch: str) -> Tuple[int, str, str]:
    """Read branch protection via `gh api` — gh supplies auth (login locally / GH_TOKEN in CI).
    Returns (returncode, stdout, stderr); raises FileNotFoundError when `gh` is not installed."""
    proc = subprocess.run(
        ["gh", "api", f"repos/{repo}/branches/{branch}/protection"],
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


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


@dataclass
class ProtectionVerdict:
    """Whether the branch is safely protected — fail-closed."""

    repo: str
    branch: str
    ok: bool
    failures: List[str] = field(default_factory=list)

    def render(self) -> str:
        if self.ok:
            return (f"branch-protection PASS — {self.repo}@{self.branch} is protected "
                    "(no force-push, no deletion, required status checks).")
        lines = [f"branch-protection FAIL — {self.repo}@{self.branch} is NOT safely protected:"]
        for f in self.failures:
            lines.append(f"  ✗ {f}")
        lines.append(
            "  fix: apply protection (admin) —\n"
            f"       gh api -X PUT repos/{self.repo}/branches/{self.branch}/protection --input - <<'JSON'\n"
            '       { "required_status_checks": {"strict": true, "contexts": []}, "enforce_admins": false,\n'
            '         "required_pull_request_reviews": null, "restrictions": null,\n'
            '         "allow_force_pushes": false, "allow_deletions": false }\n'
            "       JSON\n"
            "       (and ensure `gh` is installed + authenticated — GH_TOKEN in CI). "
            "See docs/build/68-mokata-branch-protection-setup.md."
        )
        return "\n".join(lines)


def check_branch_protection(
    repo: str = DEFAULT_REPO,
    branch: str = DEFAULT_BRANCH,
    runner: Optional[GhRunner] = None,
) -> ProtectionVerdict:
    """Verify `repo`@`branch` is safely protected. NEVER raises — every inability to prove
    protection (gh missing, non-zero exit, non-JSON body, unsafe settings) is a FAIL verdict so the
    caller (release.sh) refuses to release. No token is hard-coded or accepted."""
    run = runner if runner is not None else _default_gh_runner

    def fail(msgs: List[str]) -> ProtectionVerdict:
        return ProtectionVerdict(repo=repo, branch=branch, ok=False, failures=msgs)

    try:
        code, out, err = run(repo, branch)
    except FileNotFoundError:
        return fail(["`gh` CLI not found — cannot verify branch protection"])
    except Exception as exc:  # any gh invocation error is fail-closed
        return fail([f"error invoking `gh` to read branch protection: {exc}"])

    if code != 0:
        detail = (err or out or "").strip().splitlines()
        msg = detail[0] if detail else f"`gh` exited {code}"
        return fail([f"could not read branch protection (gh exit {code}): {msg}"])

    try:
        payload = json.loads(out)
    except (ValueError, TypeError):
        return fail(["branch-protection response was not valid JSON"])

    ok, failures = evaluate_protection_payload(payload)
    return ProtectionVerdict(repo=repo, branch=branch, ok=ok, failures=failures)


__all__ = [
    "DEFAULT_REPO",
    "DEFAULT_BRANCH",
    "ProtectionVerdict",
    "check_branch_protection",
    "evaluate_protection_payload",
]
