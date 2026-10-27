"""bootstrap / validate / release-check / route / detect / status / version / update — the spine's core read + lifecycle commands."""
from __future__ import annotations

import argparse
import sys

from ._common import (
    __version__,
    build_bootstrap,
    Detector,
    ManifestError,
    TOOL_CATALOG,
    _load_surface,
    _profile_for,
    _ledger_for,
)


def cmd_bootstrap(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    result = build_bootstrap(surface)
    sys.stdout.write(result.text)
    if args.show_tokens:
        status = "OK" if result.within_budget else "OVER BUDGET"
        print(
            f"\n[tokens ~{result.token_estimate} / budget {result.budget} — {status}]",
            file=sys.stderr,
        )
    return 0 if result.within_budget else 1


def cmd_validate(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    m = surface.manifest
    print(
        f"OK — manifest valid: profile '{m.profile}', "
        f"{len(m.capabilities)} capabilit{'y' if len(m.capabilities) == 1 else 'ies'}, "
        f"{len(m.tools)} tool(s)."
    )
    return 0


# `release-check`'s exit contract. Three outcomes, three codes (doc 85 §7g) — 1 has always meant
# "the versions do not agree"; collapsing "another package answered" into it would hand `release.sh`
# and a human the same number for two different facts.
RELEASE_CHECK_OK = 0
RELEASE_CHECK_MISMATCH = 1
RELEASE_CHECK_ANSWERED_ELSEWHERE = 3
RELEASE_CHECK_UNRESOLVABLE_PACKAGE = 4


def cmd_release_check(args: argparse.Namespace) -> int:
    """Stage 61b — PURE/OFFLINE: assert every version field == the intended tag. Exit 1
    (fail-closed) naming any mismatch so `release.sh` can REFUSE to tag a lagging commit.

    0.0.18 stage 7 (`RELEASE-CHECK-BARE-COMMAND-READS-SITE-PACKAGES`): the answer now names the
    package that produced it, and REFUSES when that package lives outside `--root`. Run bare from
    the repo root this used to import a 0.0.9 install from site-packages and print PASS against
    its own five-field set while the checkout had seven. The tag-time path is unaffected —
    `release.sh` already passes `PYTHONPATH="${root}/src"`, which resolves inside `--root`.
    """
    from .. import packaging
    from ..packaging import answering_package, check_release_consistency
    prov = answering_package(args.root, getattr(packaging, "__file__", None), __version__)
    print(prov.render())
    if not prov.trustworthy:
        print(prov.refusal())
        return (RELEASE_CHECK_UNRESOLVABLE_PACKAGE
                if prov.state == packaging.ANSWERED_UNRESOLVABLE
                else RELEASE_CHECK_ANSWERED_ELSEWHERE)
    target = args.version or __version__
    res = check_release_consistency(target, root=args.root)
    print(res.render())
    return RELEASE_CHECK_OK if res.consistent else RELEASE_CHECK_MISMATCH


def cmd_release_notes_check(args: argparse.Namespace) -> int:
    """DG-7 — PURE/OFFLINE: assert RELEASE_NOTES.md announces the intended tag AND discloses
    every `### Known limitations` fact the CHANGELOG records for it. Exit 1 (fail-closed)
    naming each undisclosed fact, so `release.sh` REFUSES to tag notes that quietly dropped a
    limitation the project decided to ship.

    B5 (0.0.19) — A SECOND AXIS AND A THIRD EXIT CODE. DG-7 above grades whether a limitation is
    still PRINTED. It never graded whether a PROMISE is still TRUE, and that is how five published
    commitments went to PyPI against a release whose scope had been replaced, with every check
    green. So this command now also grades whether every published schedule still RESOLVES:

        0  the notes are right AND every schedule resolved (or was accounted for, or is spent)
        1  the notes are wrong, OR a published schedule no longer resolves
        2  NOT CHECKABLE HERE — a schedule could not be resolved from this artefact at all

    ⭐ 2 IS NEITHER A PASS NOR A FAILURE, and it is non-zero for `branch_protection`'s reason: a
    caller that has not been taught what it means reads it as a refusal, which is the safe way
    round. It is the honest answer for a SHIPPED checker, because the planning documents it would
    have to resolve against are dropped by both mirror controls — a shipped reader of them is
    `SHIPPED-TEST-READS-INTERNAL-FILE`, filed four times. `release.sh` handles 2 explicitly and
    then requires the internal resolving gate to have answered; CI on the mirror handles 2
    explicitly and says which gate owns the verdict. Nothing treats it as green."""
    from ..packaging import check_release_notes
    target = args.version or __version__
    res = check_release_notes(target, root=args.root)
    print(res.render())
    if not res.ok or res.claims_failed:
        return 1
    return 2 if res.claims_undecided else 0


def cmd_branch_protection_check(args: argparse.Namespace) -> int:
    """TM.S12a — verify the public mirror's default branch is protected (no force-push, no
    deletion, required status checks). THREE states, three exit codes (0.0.18, 2026-08-17):

        0  PROTECTED      the detail was read and is safe
        1  NOT PROTECTED  a read SUCCEEDED and shows protection absent/too weak
        2  UNREADABLE     the detail could not be read AND the live reads could not corroborate
        3  DEGRADED       unreadable, corroborated live AND by a fresh, safe last-read document
        4  CACHE MISS     unreadable; nothing was ever read successfully on this machine
        5  CACHE DISAGREES  unreadable; the last-read document was NOT safe
        6  CACHE TOO OLD  unreadable; the last-read document is older than the age limit
        7  CACHE UNUSABLE unreadable; the cache file is there and cannot be trusted

    3 is deliberately non-zero: only a caller taught what it means may proceed on it, and every
    caller that has not been taught reads it as a refusal. `release.sh` handles it explicitly.
    The last-read cache lives under `branch_protection.default_cache_dir()` and is written only
    after a successful full read. No token hard-coded (gh supplies auth: login locally / GH_TOKEN
    in CI)."""
    from ..branch_protection import ProtectionCache, check_branch_protection, default_cache_dir
    from ..degrade import FAILURE_UNREACHABLE, note_degraded
    verdict = check_branch_protection(repo=args.repo, branch=args.branch,
                                      cache=ProtectionCache(default_cache_dir()))
    print(verdict.render())
    if verdict.cache_note:
        print(f"note: {verdict.cache_note}", file=sys.stderr)
    if verdict.degraded:
        # Also into the D5 register, so `mokata doctor` can answer "what degraded this session?"
        # after the block above has scrolled away.
        note_degraded(
            "branch-protection", FAILURE_UNREACHABLE,
            fallback=f"passed on the last-read document of {verdict.cache_read_at} and the live "
                     f"boolean ({verdict.observed_on}); force-push, deletions, strict and "
                     "contexts were NOT verified live",
            fix="Retry once GitHub's branch-protection endpoints answer; do NOT apply protection "
                "to clear a read failure",
            detail=verdict.unreadable_because)
    return verdict.exit_code


def cmd_route(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    try:
        targets = [args.need] if args.need else list(surface.manifest.capabilities)
        for need in targets:
            r = surface.router.resolve(need)
            chain = ", ".join(
                f"{t}{'+' if present else '-'}" for t, present in r.attempted
            )
            print(r.summary())
            print(f"    attempted: {chain}")
            print(f"    reason: {r.reason}")
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    detector = Detector()
    for tid in sorted(TOOL_CATALOG):
        present = detector.is_present(tid, TOOL_CATALOG[tid])
        mark = "present" if present else "absent "
        print(f"[{mark}] {tid}  ({TOOL_CATALOG[tid]['provides']})")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    surface = _load_surface(args.path)
    m = surface.manifest
    live = [r.summary() for r in surface.router.resolve_all()]
    from ..run_mode import read_mode
    print(f"mokata {m.mokata_version} · profile '{m.profile}' · mode: {read_mode(surface)}")
    for line in live:
        print(f"  {line}")
    # Stage 25 Part B — actionable code-graph hint (active queries, or how to wire one).
    from ..knowledge import graph_guidance
    print(graph_guidance(surface))
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    # 45b — OFFLINE by default: version + profile + install method + Python, zero egress.
    from ..version import check_for_update, version_info
    print(version_info(profile=_profile_for(args.path)).render())
    if args.check:
        # OPT-IN outbound check — netguard-accounted (logged) + degrade-clean offline.
        print(check_for_update(ledger=_ledger_for(args.path)).render())
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    # 0.0.26 L2 (`MOK-UPDATE`, Jas 2026-10-21: "whatever makes UX easy") — ONE action: the plan,
    # one "Update now? [Y/n]" (the P2 consent; `--yes` answers it), the installer's own command,
    # then `--finish` with the NEW mokata. `--check` / `--offline` print the plan and run nothing.
    import os
    from ..prompt import confirmed, decline_notice
    from ..update import (finish_update, launch_detached, plan_update,
                          render_result, render_update, run_update, take_result)
    pending = take_result()
    if pending:
        print(render_result(pending) + "\n")
    if args.finish:
        return finish_update(args.path)
    print_only = args.check or args.offline
    plan = plan_update(args.path, check=not args.offline,
                       ledger=None if args.offline else _ledger_for(args.path),
                       method="plugin" if args.method == "plugin" else None)
    print(render_update(plan, check=print_only))
    if print_only:
        return 0
    if plan.checked and not plan.online:
        print("\nNot updating without knowing what is newer — run the line above yourself, or "
              "retry when the release feed answers.")
        return 0
    if not plan.newer:
        if not plan.follow_up:
            return 0
        decision = confirmed(args.yes, "", "Refresh them now?", default_yes=True)
        if not decision:
            print(f"not refreshed — {decline_notice(decision)}.")
            return 0
        return finish_update(args.path)
    if plan.argv is None:
        print("\nmokata cannot run this one for you" + (f" ({plan.reason})" if plan.reason else "")
              + " — run it yourself, then `mokata update --finish`.")
        return 0
    decision = confirmed(args.yes, "", "Update now?", default_yes=True)
    if not decision:
        print(f"not updated — {decline_notice(decision)}.")
        return 0
    if plan.deferred:
        launch_detached(plan, args.path, wait_pids=[os.getpid()])
        print("\nUpdating as soon as this command exits (Windows keeps mokata.exe locked while it "
              "runs). It takes a minute; your next `mokata` command reports the result.")
        return 0
    return run_update(plan, args.path)


def register(sub, common):
    p_boot = sub.add_parser(
        "bootstrap", parents=[common], help="print the SessionStart briefing"
    )
    p_boot.add_argument(
        "--show-tokens",
        action="store_true",
        help="print the token estimate + budget check to stderr",
    )
    p_boot.set_defaults(func=cmd_bootstrap)

    p_val = sub.add_parser(
        "validate", parents=[common], help="validate the committed manifest"
    )
    p_val.set_defaults(func=cmd_validate)

    p_relchk = sub.add_parser(
        "release-check",
        help="verify all version fields == the intended tag (pure/offline; exit 1 on mismatch)",
    )
    p_relchk.add_argument(
        "version", nargs="?", default=None,
        help="the intended tag/version (e.g. 0.0.5 or v0.0.5); default: this package's version",
    )
    p_relchk.add_argument(
        "--root", default=".",
        help="the checkout to verify (e.g. the public mirror before tagging); default: cwd",
    )
    p_relchk.set_defaults(func=cmd_release_check)

    p_notes = sub.add_parser(
        "release-notes-check",
        help="verify RELEASE_NOTES.md announces the tag, discloses the CHANGELOG's known "
             "limitations, and that every published schedule still resolves (pure/offline; "
             "exit 1 on a gap or a broken promise, 2 when a schedule is NOT CHECKABLE here)",
    )
    p_notes.add_argument(
        "version", nargs="?", default=None,
        help="the intended tag/version (e.g. 0.0.16 or v0.0.16); default: this package's version",
    )
    p_notes.add_argument(
        "--root", default=".",
        help="the checkout to verify (e.g. the public mirror before tagging); default: cwd",
    )
    p_notes.set_defaults(func=cmd_release_notes_check)

    p_bpc = sub.add_parser(
        "branch-protection-check",
        help="verify the public mirror's default branch is protected (fail-closed; exit 0 "
             "protected, 1 not protected, 2 unreadable+uncorroborated, 3 degraded pass on a "
             "fresh last-read document, 4-7 unreadable and the last-read cache cannot "
             "corroborate: miss / disagrees / too old / unusable)",
    )
    p_bpc.add_argument(
        "--repo", default="JasGujral/mokata-oss",
        help="owner/repo to verify (default: the public mirror JasGujral/mokata-oss)",
    )
    p_bpc.add_argument(
        "--branch", default="main",
        help="the default branch to verify (default: main)",
    )
    p_bpc.set_defaults(func=cmd_branch_protection_check)

    p_route = sub.add_parser(
        "route", parents=[common], help="resolve a capability to its tool"
    )
    p_route.add_argument("need", nargs="?", help="capability name (default: all)")
    p_route.set_defaults(func=cmd_route)

    p_det = sub.add_parser(
        "detect", parents=[common], help="show tool presence for the catalog"
    )
    p_det.set_defaults(func=cmd_detect)

    p_stat = sub.add_parser(
        "status", parents=[common], help="one-line stack summary"
    )
    p_stat.set_defaults(func=cmd_status)

    p_ver = sub.add_parser(
        "version", parents=[common],
        help="show version + profile + install method + Python (offline by default)",
    )
    p_ver.add_argument("--check", action="store_true",
                       help="opt-in: check for a newer release (outbound; degrades clean "
                            "offline)")
    p_ver.set_defaults(func=cmd_version)

    p_up = sub.add_parser(
        "update", parents=[common],
        help="update mokata in one go: shows installer, latest version + release notes and the "
             "exact command, asks once, runs it (keeping your extras), then refreshes the "
             "vendored commands/skills and wiring",
    )
    p_up.add_argument("--check", action="store_true",
                      help="print the plan only; run nothing")
    p_up.add_argument("--offline", action="store_true",
                      help="print the plan only, without asking the release feed (no network)")
    p_up.add_argument("--yes", action="store_true",
                      help="answer the one \"Update now?\" question with yes")
    p_up.add_argument("--method", choices=("auto", "plugin"), default="auto",
                      help="override install-method detection (default: auto)")
    p_up.add_argument("--finish", action="store_true", help=argparse.SUPPRESS)
    p_up.set_defaults(func=cmd_update)


__all__ = [
    "cmd_bootstrap",
    "cmd_validate",
    "cmd_release_check",
    "cmd_branch_protection_check",
    "cmd_route",
    "cmd_detect",
    "cmd_status",
    "cmd_version",
    "cmd_update",
]
