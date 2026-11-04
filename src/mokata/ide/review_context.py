"""0.0.26 P7 `ID-REVIEW-CONTEXT` — what a reviewer needs beside the diff, from evidence that exists.

Nothing here is a new store. Every answer is a projection of a record mokata already keeps, and
the one write is a re-run's outcome into the run's existing test record:

  * TESTS BY AC — the run's AC → test map (`test_runs.read_test_record`: the map seeded at emit,
    `planned`, and the one test runs extended, `ac_tests`), each test's RED/GREEN record
    (`tdd_state.load`), the last suite run (`last_suite`), and the run's spec
    (`engine.spec_gate.read_emitted_spec`) and approval (`brainstorm.load_approved_approach`).
    A test's history is RED then GREEN in TDD order as the record holds them, then any later
    suite run that disagrees (with its `source`), then the re-runs started from this IDE window.
  * RE-RUN — the project's OWN test command (`settings.baseline.test_command`, the one
    `mokata baseline` runs), one subprocess per test, in the project's env (`project_env`), as
    a visible job: progress per test, Cancel ends the test's whole process tree, the output
    streams as the job's partial answer. Never through a shell; mokata runs no model. Its outcome
    is recorded in the run's test record with provenance (`record_rerun`; ruled 2026-10-28).
  * SPEC AS MARKDOWN + RENDERED SPEC DIFF — any two versions of the run's spec
    (`engine.spec_history`), rendered to Markdown here, plus the AC-level change list.
  * DOC TEXT — a Markdown file in the working tree or at a git revision, for the rendered view
    and the rendered diff of a changed `.md`.
  * BLAME ON THE HUNK — `git blame -L` over the hunk's own lines (both sides) and
    `git log -L` over the same range: the line history of the hunk under review, never the file's.

Long work (a test run, a `git log -L` on a deep history) runs as a job (`ide/jobs.py`); no
deadline anywhere. Paging is by the request (`page`, `per`), never a cap on what exists.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
import queue
import shlex
import subprocess
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from ..knowledge.cancel import cancelled, checkpoint, kill_on_cancel, report
from ..exe_path import path_dirs, which_in
from ..quiet_spawn import hidden
from .lsp import spawn_jobbed
from .server import BadRequest

#: ACs per page and tests per AC when the page does not say (paging, not a cap: `page` walks all).
PER_PAGE = 100
TESTS_PER_AC = 200
#: The re-run job keeps this much of the output's TAIL in memory for the page (the byte count of
#: the whole output is always reported, so a long log is said to be longer, never cut silently).
OUTPUT_TAIL = 512 * 1024
MD_EXT = (".md", ".markdown", ".mdx", ".mdown")
#: A test id the page may ask to run: what a runner takes as ONE argument (pytest node ids,
#: unittest dotted names, a path). Never a leading "-" (that would be an OPTION to the runner),
#: and none of the characters a shell or `cmd.exe` would read as syntax (`& | < > ^ % ! " ' $`,
#: backtick, space) — on Windows a `.cmd` runner is parsed by cmd.exe even without a shell.
_TEST_ID = re.compile(r"^[\w./\\:\[\]=+,@#~][\w./\\:\[\]=+,@#~-]{0,299}$")
#: A test command the IDE can split into argv without a shell. Anything a shell would interpret
#: (`&&`, pipes, redirects, `;`, backticks, `$(`) is refused with the remedy — never run through
#: `sh -c`.
_SHELLY = re.compile(r"(&&|\|\||[|;<>`]|\$\()")
#: What `cmd.exe` also reads as syntax and a POSIX split does not: a lone `&` (a second command),
#: `^` (its escape) and a line break. Refused on Windows even though no cmd.exe is ever started.
_SHELLY_NT = re.compile(r"[&^\r\n]")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

STATUS = ("green", "red", "unrun")
AC_STATUS = ("green", "red", "unrun", "untested")


# ============================================================================ tests by AC
def _criteria(spec: Any) -> List[Dict[str, Any]]:
    return [{"id": c.id, "text": c.text, "kind": c.kind} for c in spec.criteria]


def test_history(tid: str, red: set, green: set, suite: Optional[Dict[str, Any]],
                 reruns: Dict[str, List[Dict[str, Any]]],
                 suite_ids: Optional[frozenset] = None) -> Tuple[str, List[Dict[str, Any]], bool]:
    """(latest status, history oldest-first, never_red) for one test id from the run's evidence.

    The TDD record holds a SET of ids recorded RED and a set recorded GREEN — the order between
    them is the order TDD imposes (RED first; the gate refuses implementation without it). A test
    in GREEN that was never RED is `never_red`: it was never seen failing, which is said."""
    hist: List[Dict[str, Any]] = []
    if tid in red:
        hist.append({"state": "red", "basis": "tdd record"})
    if tid in green:
        hist.append({"state": "green", "basis": "tdd record"})
    latest = hist[-1]["state"] if hist else "unrun"
    if suite_ids is None and suite:
        suite_ids = frozenset(suite.get("tests") or ())
    if suite and tid in suite_ids:
        st = "green" if suite.get("passed") else "red"
        if st != latest:
            hist.append({"state": st, "basis": "last suite run", "at": suite.get("at"),
                         "source": suite.get("source") or "cli"})
            latest = st
        elif hist:
            hist[-1].update(at=suite.get("at"), source=suite.get("source") or "cli")
    for r in reruns.get(tid, ()):
        hist.append({"state": r["state"], "basis": "re-run here", "at": r["at"], "job": r["job"],
                     "source": "ide"})
        latest = r["state"] if r["state"] in ("green", "red") else latest
    return latest, hist, (tid in green and tid not in red)


def _ac_status(statuses: List[str]) -> str:
    if not statuses:
        return "untested"
    if "red" in statuses:
        return "red"
    if all(s == "green" for s in statuses):
        return "green"
    return "unrun"


def _page_arg(raw: Any, name: str, default: int) -> int:
    s = str(raw or "").strip()
    if not s:
        return default
    if not s.isdigit() or not 1 <= int(s) <= 10 ** 7:
        raise BadRequest(f"{name} must be a positive number")
    return int(s)


def tests_panel(root: str, run_id: str, *, page: Any = "", per: Any = "", ac: str = "",
                tpage: Any = "", status: str = "", reruns: Optional[Dict[str, List[Dict[str, Any]]]] = None,
                command: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The testing panel for `run_id`: the spec's ACs (paged), each with its mapped tests (paged
    per AC), each test's latest status and red/green history, the untested ACs flagged, the tests
    on record that no AC claims, and the approval that let the change in."""
    from ..engine.spec_gate import read_emitted_spec
    from ..tdd_state import load as load_tdd
    from ..test_runs import read_test_record
    from . import plans
    rid = plans.check_local_run(root, run_id)
    pg, pp = _page_arg(page, "page", 1), _page_arg(per, "per", PER_PAGE)
    tp = _page_arg(tpage, "tpage", 1)
    store = plans._run_store(root, rid)
    spec, malformed = read_emitted_spec(store)
    rec = read_test_record(root, rid)
    try:
        red, green = load_tdd(store, rid)
    except (OSError, ValueError, TypeError):
        red, green = set(), set()
    reruns = reruns or {}
    suite = rec["last_suite"]
    sids = frozenset((suite or {}).get("tests") or ())       # one set: membership is O(1)
    approval = approval_of(store, spec)
    base = {"sample": False, "run": rid, "approval": approval, "command": command or {},
            "last_suite": suite, "rerun_note": RERUN_NOTE}
    if malformed:
        from ..engine.spec_gate import SPEC_MALFORMED_MESSAGE
        return {**base, "state": "malformed", "note": SPEC_MALFORMED_MESSAGE}
    if spec is None:
        from ..cli_commands.spec import NO_SPEC_RECOVERY
        return {**base, "state": "no-spec", "note": NO_SPEC_RECOVERY}

    def tests_of(acid: str) -> List[Tuple[str, str]]:
        planned = set(rec["planned"].get(acid, ()))
        recorded = set(rec["ac_tests"].get(acid, ()))
        return [(t, "both" if t in planned and t in recorded else
                 "planned" if t in planned else "recorded") for t in sorted(planned | recorded)]

    if status and status not in AC_STATUS:
        raise BadRequest(f"status must be one of {', '.join(AC_STATUS)}")
    crit = _criteria(spec)
    totals = {"acs": len(spec.criteria), "untested": 0, "green": 0, "red": 0, "unrun": 0,
              "tests": 0, "never_red": 0}
    claimed: set = set()
    ac_status: Dict[str, str] = {}
    for c in crit:                               # totals over EVERY criterion (one pass)
        ts = tests_of(c["id"])
        claimed.update(t for t, _ in ts)
        sts = [test_history(t, red, green, suite, reruns, sids)[0] for t, _ in ts]
        ac_status[c["id"]] = _ac_status(sts)
        totals[ac_status[c["id"]]] += 1
    if ac:
        if ac not in ac_status:
            raise BadRequest(f"the spec has no criterion {ac[:80]!r}")
        crit = [c for c in crit if c["id"] == ac]
    elif status:
        crit = [c for c in crit if ac_status[c["id"]] == status]
    rows = []
    totals["tests"] = len(claimed)
    totals["never_red"] = sum(1 for t in claimed if t in green and t not in red)
    pages = max(1, -(-len(crit) // pp))
    for c in crit[(pg - 1) * pp: pg * pp]:
        ts = tests_of(c["id"])
        out_tests = []
        sts = []
        for t, where in ts:
            latest, hist, never_red = test_history(t, red, green, suite, reruns, sids)
            sts.append(latest)
            out_tests.append({"id": t, "status": latest, "history": hist, "never_red": never_red,
                              "mapped": where})
        tpp = TESTS_PER_AC
        t_pages = max(1, -(-len(out_tests) // tpp))
        shown = out_tests[(tp - 1) * tpp: tp * tpp] if ac else out_tests[:tpp]
        rows.append({**c, "status": _ac_status(sts), "untested": not ts,
                     "tests": shown, "tests_total": len(out_tests),
                     "tests_pages": t_pages, "tests_page": tp if ac else 1})
    unmapped = sorted((red | green) - claimed)
    return {**base, "state": "ok",
            "spec": {"title": spec.title, "version": _version(store)},
            "page": pg, "per": pp, "pages": pages, "status": status, "matching": len(crit),
            "totals": totals, "criteria": rows,
            "unmapped": {"total": len(unmapped), "tests": unmapped[(tp - 1) * TESTS_PER_AC:
                                                                    tp * TESTS_PER_AC]
                         if ac == "" else []}}


def run_tests_of(root: str, run_id: str,
                 reruns: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    """Every test the run's ACs map, unpaged: `{by_ac: {AC: [ids]}, latest: {id: status}}` — what
    a re-run request is checked against (the panel re-runs the run's own tests, nothing else)."""
    from ..engine.spec_gate import read_emitted_spec
    from ..tdd_state import load as load_tdd
    from ..test_runs import mapped_tests, read_test_record
    from . import plans
    rid = plans.check_local_run(root, run_id)
    store = plans._run_store(root, rid)
    spec, _malformed = read_emitted_spec(store)
    if spec is None:
        raise BadRequest("the run has no readable spec — there are no ACs to run tests for")
    rec = read_test_record(root, rid)
    try:
        red, green = load_tdd(store, rid)
    except (OSError, ValueError, TypeError):
        red, green = set(), set()
    by_ac = {c.id: mapped_tests(rec, c.id) for c in spec.criteria}
    ids = {t for v in by_ac.values() for t in v}
    suite = rec["last_suite"]
    sids = frozenset((suite or {}).get("tests") or ())
    latest = {t: test_history(t, red, green, suite, reruns or {}, sids)[0] for t in ids}
    return {"run": rid, "by_ac": by_ac, "latest": latest}


RERUN_NOTE = ("A re-run here is recorded in the run's test record as evidence the Stop gate "
              "reads, with its provenance: source ide, you as the actor, the command, the exit "
              "code and the time — never mistaken for a run the agent recorded.")


def _version(store: Any) -> int:
    from ..engine import spec_history as SH
    return SH.current_version(store) or 1


def approval_of(store: Any, spec: Any) -> Dict[str, Any]:
    """Who approved the plan this change was built from, and which spec version is in force — the
    approval that let the change in (`brainstorm.load_approved_approach`)."""
    from . import plans
    ap = plans._approach_view(store)
    return {"state": ap.get("state"), "approach": ap.get("name", ""),
            "approver": ap.get("approver", ""), "approved_at": ap.get("approved_at", ""),
            "spec_version": _version(store) if spec is not None else 0}


# ============================================================================ the test command
def _manifest(root: str) -> Tuple[Any, str]:
    """(the repo's manifest or None, why it could not be read — "" when absent or fine)."""
    from .. import MOKATA_DIR
    from ..manifest import Manifest, ManifestError
    path = os.path.join(root, MOKATA_DIR, "manifest.json")
    if not os.path.exists(path):
        return None, ""
    try:
        return Manifest.load(path), ""
    except (ManifestError, OSError) as exc:
        return None, str(exc)[:300]


#: A JS runner's own JavaScript entry: name -> the npm packages (global installs) whose `bin` has
#: it. On Windows these tools exist as `.cmd` shims only; the entry run by `node` is the same
#: program without cmd.exe. (corepack ships `pnpm` and `yarn` entries.)
JS_RUNNERS = {"npm": ("npm",), "npx": ("npm",), "pnpm": ("pnpm", "corepack"),
              "yarn": ("yarn", "corepack")}


def _path_of(env: Dict[str, str]) -> Optional[str]:
    return next((v for k, v in env.items() if k.upper() == "PATH"), None)     # Win: "Path"


def _shim_in(dirs: List[str], name: str) -> Optional[str]:
    """The `.cmd`/`.bat` that is the only thing named `name` on these directories, if any."""
    for d in dirs:
        for ext in (".cmd", ".bat"):
            cand = os.path.join(d, name + ext)
            if os.path.isfile(cand):
                return cand
    return None


def resolve_runner(root: str, name: str, path_var: Optional[str],
                   nt: Optional[bool] = None) -> Tuple[Optional[List[str]], Optional[str]]:
    """`(argv head, None)` when `name` can be started WITHOUT a shell, else `(None, why)` — a why
    that is true and says what to do. A bare name is found by the product's finder (absolute PATH
    entries, never the repo); on Windows, where npm/npx/pnpm/yarn are `.cmd` shims, the head is
    `[node.exe, <the tool's own JS entry>]` found as the LSP finder finds node servers. A `.cmd`/
    `.bat` is never run (cmd.exe would parse the test id and the repo's files). An explicit path is
    used as written, unless it is such a shim."""
    from .lsp import _global_node_package_bin
    nt = (os.name == "nt") if nt is None else nt
    bare = os.path.basename(name) == name and not os.path.isabs(name)
    if not bare:
        if nt and name.lower().endswith((".cmd", ".bat")):
            return None, _shim_why(name, name, None)
        return [name], None
    dirs = path_dirs({"PATH": path_var or ""}, cwd=root)
    exe = which_in(name, dirs, nt=nt)
    if exe:
        return [exe], None
    if not nt:
        return None, _not_found(name)
    shim = _shim_in(dirs, name)
    env = {"PATH": path_var or ""}
    tried = ""
    for pkg in JS_RUNNERS.get(name, ()):
        head, w = _global_node_package_bin(root, name, pkg, env)
        if head is not None:
            return head, None
        tried = tried or w
    if shim is None:
        return None, _not_found(name)
    return None, _shim_why(name, shim, name in JS_RUNNERS, tried)


def _not_found(name: str) -> str:
    return f"`{name}` is not an executable on the project's PATH"


def _shim_why(name: str, shim: str, js: Optional[bool], tried: str = "") -> str:
    """Why a Windows batch file is not started — the same words for the IDE re-run and the gated
    merge's full-suite run, so it says neither "test id" nor "IDE"."""
    base = os.path.basename(shim)
    return (f"`{name}` is a Windows batch file ({shim}) and mokata never runs a .cmd/.bat — "
            f"cmd.exe would re-parse the command line"
            + (f"; its own JavaScript entry (run through node) was not found either"
               + (f" ({tried})" if tried else "") if js else "")
            + f". Set `settings.baseline.test_command` to a real executable"
            + (" (for example `node <path to the runner's .js>`)" if js else "")
            + f" — {base} itself is never run, by the IDE re-run or by the gated merge's full-suite "
              f"run. Run these tests in your terminal; to merge without the runner's suite "
              f"evidence, a human records `mokata worktree merge-override`")


def test_command(root: str, nt: Optional[bool] = None) -> Dict[str, Any]:
    """`{configured, text, argv, runnable, note}` — the project's own test command, and whether
    the IDE can run it WITHOUT a shell."""
    from ..baseline import baseline_command
    man, problem = _manifest(root)
    if problem:
        return {"configured": False, "text": "", "argv": [], "runnable": False,
                "note": f"the manifest cannot be read, so the test command is unknown — {problem}"}
    cmd = baseline_command(man)
    if not cmd:
        return {"configured": False, "text": "", "argv": [], "runnable": False,
                "note": "no test command set — set `settings.baseline.test_command` "
                        "(`mokata config set baseline.test_command \"pytest -q\"`); mokata "
                        "never guesses one"}
    if _SHELLY.search(cmd) or ((os.name == "nt" if nt is None else nt) and _SHELLY_NT.search(cmd)):
        return {"configured": True, "text": cmd, "argv": [], "runnable": False,
                "note": "the test command uses shell syntax (&&, |, ;, redirects) — the IDE runs "
                        "a test without a shell; put the runner alone in "
                        "`settings.baseline.test_command` (e.g. `pytest -q`) to re-run here"}
    try:
        argv = split_command(cmd)
    except ValueError as exc:
        return {"configured": True, "text": cmd, "argv": [], "runnable": False,
                "note": f"the test command cannot be split into arguments ({exc})"}
    if not argv:
        return {"configured": True, "text": cmd, "argv": [], "runnable": False,
                "note": "the test command is empty"}
    from .. import project_env
    penv_env = dict(os.environ)
    # A READ never starts a program: no `poetry`/`pipenv` is asked where the env is (the panel
    # opening is not a click). So a runner missing from the base PATH is not "known missing" in a
    # project whose env a tool could still supply — only what is certain is refused.
    penv = project_env.resolve(root, project_env.settings_python(man), penv_env, ask_tools=False)
    run_env = project_env.activated(penv_env, penv)
    head, why = resolve_runner(root, argv[0], _path_of(run_env), nt)
    if head is None and why == _not_found(argv[0]) and penv is None and (
            os.path.exists(os.path.join(root, "pyproject.toml"))
            or os.path.exists(os.path.join(root, "Pipfile"))):
        return {"configured": True, "text": cmd, "argv": argv, "runnable": True,
                "note": f"`{argv[0]}` is not on the base PATH; it is looked up in the project's "
                        f"poetry/pipenv environment when a test runs (the panel starts no tool to "
                        f"ask). Each test runs as `{cmd} <test id>`"}
    if head is None:
        return {"configured": True, "text": cmd, "argv": argv, "runnable": False, "note": why}
    return {"configured": True, "text": cmd, "argv": argv, "runnable": True,
            "note": "each test runs as `" + cmd + " <test id>` in the project's env"}


def split_command(cmd: str) -> List[str]:
    """The command as argv, without a shell. POSIX rules everywhere but Windows; there,
    `shlex(posix=False)` keeps a quoted token's quotes (`"C:\\Program Files\\py.exe"`), which are
    stripped here so the path is one argument without them."""
    if os.name != "nt":
        return shlex.split(cmd)
    out = []
    for tok in shlex.split(cmd, posix=False):
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
            tok = tok[1:-1]
        out.append(tok)
    return out


def ide_actor() -> str:
    """The human at this IDE: the OS account the server runs as (the IDE is localhost-only and
    paired to that user's browser)."""
    import getpass
    try:
        return getpass.getuser()
    except (OSError, KeyError, ImportError):      # no account name: still an IDE run, said so
        return "unknown"


def record_rerun(root: str, run_id: str, command: str, results: List[Dict[str, Any]],
                 complete: bool) -> Dict[str, Any]:
    """Write an IDE re-run into the run's test record (`test_runs.record_test_run`, the record the
    Stop gate reads) with its provenance — ruled 2026-10-28: it counts as evidence and is always
    told apart from an agent run. Each test RED or GREEN by its own exit; the run "passed" only
    when every test passed AND the run completed (a cancelled run never reads as passing)."""
    from ..test_runs import record_test_run
    outcome = {r["id"]: r["state"] == "green" for r in results}
    codes = [r.get("rc") for r in results]
    bad = next((c for c in codes if c != 0), 0)
    return record_test_run(root, run_id, passed=complete and all(outcome.values()),
                           command=command, results=outcome, source="ide",
                           actor=ide_actor(), exit_code=bad if isinstance(bad, int) else -1)


def check_test_ids(ids: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for t in ids:
        if not isinstance(t, str) or not _TEST_ID.match(t):
            raise BadRequest("a test id is one argument with no spaces and no leading '-' "
                             f"(got {str(t)[:80]!r})")
        if t not in out:
            out.append(t)
    if not out:
        raise BadRequest("name at least one test to run")
    return out


#: How often the re-run loop looks at its pipe, its cancel flag and its runner.
_POLL_S = 0.1
#: After the runner has exited and its group was ended, output is drained until the pipe has been
#: quiet this long — a bound on draining a dead run's pipe, never on the run itself.
_DRAIN_IDLE_S = 1.0


def _kill_tree(proc: Any, leftovers_only: bool = False, job: Any = None) -> None:
    """End the runner's whole tree. POSIX: SIGKILL to its process group (its pid, as it leads a
    new session). Windows: the runner's Job Object (`lsp.spawn_jobbed`: the runner is created suspended, put in the job, then resumed) — it holds the
    tree whether or not the runner still lives, so what a test left behind after the runner
    exited is ended too (`taskkill /T` needs a LIVE root to walk from, so it reaches nothing then:
    the leftover held the project directory open and could not be deleted). Only when no job could
    be made does it fall back to `taskkill /F /T` (`crg_resolve._end_tree`) for a live runner.
    `leftovers_only`: the runner has already exited — end what it left in its group. Never
    raises."""
    if os.name == "nt":
        if job is not None and job.ok:
            job.terminate()
        elif not leftovers_only:
            from ..crg_resolve import _end_tree
            _end_tree(proc)
        return
    import signal
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    if not leftovers_only:
        try:
            proc.kill()
            proc.wait()
        except OSError:
            pass


def _pump(stream: Any, pipe: "queue.Queue[Optional[bytes]]") -> None:
    """The runner's output, line by line, onto `pipe`; None at EOF. Runs on its own thread so a
    Cancel never waits on a read."""
    try:
        for chunk in iter(stream.readline, b""):
            pipe.put(chunk)
    except (OSError, ValueError):
        pass
    pipe.put(None)


def run_tests(root: str, argv: List[str], ids: List[str],
              on_test: Optional[Callable[[str, str, float], None]] = None,
              environ: Optional[Dict[str, str]] = None,
              on_done: Optional[Callable[[List[Dict[str, Any]], bool], None]] = None
              ) -> Dict[str, Any]:
    """Run each test as `argv + [id]` in the project's env, one subprocess at a time — the work of
    a `tests.run` job. Progress per test; the output streams as the partial answer; a cancel ends
    the running test's whole process tree at once (`_kill_tree`) and the job stops. No timeout: a
    test that takes an hour is a job that shows it is running for an hour. `on_done(results,
    complete)` is called with what ran — also when the job was cancelled part-way."""
    from .. import project_env
    man, problem = _manifest(root)
    sp = project_env.settings_python(man)
    base_env = dict(os.environ if environ is None else environ)
    penv = project_env.resolve(root, sp, base_env)
    env_line = project_env.describe(penv) + (f" (manifest unreadable, `settings.python` not "
                                             f"applied: {problem})" if problem else "")
    run_env = project_env.activated(base_env, penv)
    # The runner as the PROJECT's env finds it (its bin/ first on PATH): `pytest` is the venv's.
    # The product's own finder (`exe_path`): absolute PATH entries only, never the cwd — which is
    # the repo — and on Windows only a real `.exe`/`.com`, never a `.cmd` shim (cmd.exe).
    path_var = _path_of(run_env)
    # An explicit path (`/abs/pytest`, `./gradlew`: the manifest's own words) is used as written;
    # a BARE name is found only by the finder — and when it finds nothing the run does not start
    # (handing the bare name to Popen would let the OS search `.` or a relative PATH entry,
    # which is the repo).
    head, not_found = resolve_runner(root, argv[0], path_var)
    argv = [*(head or [argv[0]]), *argv[1:]]
    out = {"tail": bytearray(), "bytes": 0}
    results: List[Dict[str, Any]] = []

    def emit(note: str) -> None:
        report(done=len(results), total=len(ids), unit="tests", note=note,
               partial={"tests": list(results), "output": out["tail"].decode("utf-8", "replace"),
                        "output_bytes": out["bytes"], "env": env_line})

    def take(chunk: bytes) -> None:
        out["bytes"] += len(chunk)
        out["tail"].extend(chunk)
        if len(out["tail"]) > OUTPUT_TAIL:
            del out["tail"][:len(out["tail"]) - OUTPUT_TAIL]

    complete = False
    try:
        for tid in ids:
            checkpoint()
            t0 = time.time()
            take(f"$ {' '.join(argv)} {tid}\n".encode("utf-8"))
            emit(f"running {tid}")
            try:
                # Windows: suspended, into the tree's Job Object, then resumed — a launcher's
                # first child (venv python.exe, pytest.exe) is in the job from its first instruction.
                if not_found:
                    raise OSError(not_found)
                proc, job = spawn_jobbed([*argv, tid], cwd=root, env=run_env,  # nosec B603 argv
                                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT)
            except OSError as exc:
                results.append({"id": tid, "state": "error", "rc": None,
                                "seconds": 0.0,
                                "note": (f"could not start: {exc}")[:300]})
                if on_test:
                    on_test(tid, "error", time.time())
                emit(f"{tid}: could not start")
                continue
            try:
                rc = _drive(proc, take, lambda: emit(f"running {tid}"), job)
            finally:
                if job is not None:
                    job.terminate()         # releases the handle; a no-op once the tree is ended
            st = "green" if rc == 0 else "red"
            results.append({"id": tid, "state": st, "rc": rc,
                            "seconds": round(time.time() - t0, 2)})
            if on_test:
                on_test(tid, st, time.time())
            emit(f"{tid}: {st}")
        complete = True
    finally:
        if on_done is not None and results:
            on_done(list(results), complete)       # what ran is evidence, even if cancelled
    return {"tests": results, "command": " ".join(argv), "env": env_line,
            "output": out["tail"].decode("utf-8", "replace"), "output_bytes": out["bytes"],
            "passed": sum(1 for r in results if r["state"] == "green"),
            "failed": sum(1 for r in results if r["state"] != "green")}


def _drive(proc: Any, take: Callable[[bytes], None], tick: Callable[[], None],
           job: Any = None) -> int:
    """Read one runner's output until its pipe ends, watching the cancel flag between reads (the
    read is on its own thread, so a Cancel never waits on it). Returns the runner's exit code;
    raises `Cancelled` — after ending the runner's whole tree — when the job is cancelled."""
    pipe: "queue.Queue[Optional[bytes]]" = queue.Queue()
    reader = threading.Thread(target=_pump, args=(proc.stdout, pipe), daemon=True,
                              name="mokata-test-output")
    reader.start()
    rc: Optional[int] = None
    idle, last = 0, 0.0
    try:
        while True:
            if cancelled():
                _kill_tree(proc, job=job)
                checkpoint()                       # raises Cancelled: the job ends NOW
            try:
                chunk = pipe.get(timeout=_POLL_S)
            except queue.Empty:
                chunk = b""
            if chunk is None:                      # EOF: every writer of the pipe is gone
                break
            if chunk:
                idle = 0
                take(chunk)
                if time.time() - last > 0.25:
                    last = time.time()
                    tick()
                continue
            if rc is None and proc.poll() is not None:
                # The runner has exited. What it left in its group (a server a test started, a
                # worker) is ended too, so the pipe reaches EOF and the lane is free; anything that
                # escaped the group cannot hold the job: the dead run's pipe is drained until quiet.
                rc = proc.returncode
                _kill_tree(proc, leftovers_only=True, job=job)
            elif rc is not None:
                idle += 1
                if idle * _POLL_S >= _DRAIN_IDLE_S:
                    break
        rc = proc.wait() if rc is None else rc
        _kill_tree(proc, leftovers_only=True, job=job)      # nothing a test started outlives its run
        return rc
    finally:
        if proc.poll() is None:                    # an exception mid-read: never leave it running
            _kill_tree(proc, job=job)
        reader.join(_DRAIN_IDLE_S)                 # the tree is dead: its pipe ends at once
        if not reader.is_alive() and proc.stdout is not None:
            proc.stdout.close()


# ============================================================================ spec as Markdown
def _md_escape(text: str) -> str:
    """Spec text is DATA: escape what Markdown would read as structure or markup (the renderer
    also refuses raw HTML; this keeps a criterion's `<b>` or leading `#` literal)."""
    out = re.sub(r"([\\`*_{}\[\]<>#|!])", r"\\\1", text or "")
    return out.replace("\n", " ")


def spec_markdown(spec: Any, version: int = 0) -> str:
    """The spec as Markdown: title, approach, domains, each AC (with a UI criterion's states),
    the task plan, clarifications and assumptions. Deterministic — the same spec renders the same
    bytes, so two versions diff on what changed."""
    lines = [f"# {_md_escape(spec.title) or 'Untitled spec'}", ""]
    meta = []
    if version:
        meta.append(f"**Version** v{version}")
    if spec.approach:
        meta.append(f"**Approach** {_md_escape(spec.approach)}")
    if spec.domains:
        meta.append("**Domains** " + ", ".join(_md_escape(d) for d in spec.domains))
    if meta:
        lines += [" · ".join(meta), ""]
    lines += ["## Acceptance criteria", ""]
    for c in spec.criteria:
        lines.append(f"- **{_md_escape(c.id)}** {_md_escape(c.text)}")
        for st, what in sorted(getattr(c, "states", {}).items()):
            lines.append(f"    - *{_md_escape(st)}*: {_md_escape(what)}")
    if not spec.criteria:
        lines.append("*No criteria.*")
    tasks = getattr(spec, "tasks", None) or []
    if tasks:
        lines += ["", "## Tasks", ""]
        for i, t in enumerate(tasks, 1):
            d = t.to_dict() if hasattr(t, "to_dict") else dict(t)
            acs = ", ".join(_md_escape(a) for a in d.get("acs", d.get("ac_ids", [])) or [])
            title = d.get("title") or d.get("name") or d.get("id") or f"task {i}"
            lines.append(f"{i}. {_md_escape(str(title))}" + (f" — {acs}" if acs else ""))
    for heading, items in (("Clarifications", getattr(spec, "clarifications", None) or []),
                           ("Assumptions", getattr(spec, "assumptions", None) or [])):
        if items:
            lines += ["", f"## {heading}", ""]
            for it in items:
                d = it.to_dict() if hasattr(it, "to_dict") else dict(it)
                text = d.get("question") or d.get("text") or d.get("statement") or ""
                ans = d.get("answer") or d.get("recommended") or ""
                lines.append(f"- {_md_escape(str(text))}" + (f" — *{_md_escape(str(ans))}*"
                                                              if ans else ""))
    return "\n".join(lines) + "\n"


def spec_diff(root: str, run_id: str, a: Any = "", b: Any = "") -> Dict[str, Any]:
    """Two versions of the run's spec, each as Markdown, plus the AC-level change list. `b`
    defaults to the current version and `a` to the one before it."""
    from ..engine import spec_history as SH
    from . import plans
    rid = plans.check_local_run(root, run_id)
    store = plans._run_store(root, rid)
    cur = SH.current_version(store)
    if not cur:
        from ..cli_commands.spec import NO_SPEC_RECOVERY
        return {"sample": False, "run": rid, "state": "no-spec", "note": NO_SPEC_RECOVERY,
                "current_version": 0}
    vb = _page_arg(b, "b", cur)
    va = _page_arg(a, "a", max(1, vb - 1))
    sides = {}
    for name, v in (("old", va), ("new", vb)):
        row = SH.spec_at_version(store, rid, v)
        if row["malformed"]:
            sides[name] = {"version": v, "state": "malformed", "markdown": ""}
        elif row["spec"] is None:
            sides[name] = {"version": v, "state": "absent", "markdown": ""}
        else:
            sides[name] = {"version": v, "state": "ok", "title": row["spec"].title,
                           "markdown": spec_markdown(row["spec"], v), "_spec": row["spec"]}
    old = sides["old"].pop("_spec", None)
    new = sides["new"].pop("_spec", None)
    ca = {c.id: c.to_dict() for c in (old.criteria if old else [])}
    cb = {c.id: c.to_dict() for c in (new.criteria if new else [])}
    changes = {"added": [{"id": k, "text": cb[k].get("text", "")} for k in cb if k not in ca],
               "removed": [{"id": k, "text": ca[k].get("text", "")} for k in ca if k not in cb],
               "changed": [{"id": k, "old": ca[k].get("text", ""), "new": cb[k].get("text", "")}
                           for k in cb if k in ca and ca[k] != cb[k]],
               "same": sum(1 for k in cb if k in ca and ca[k] == cb[k])}
    return {"sample": False, "run": rid, "state": "ok", "current_version": cur,
            "versions": [r["version"] for r in SH.spec_versions(store, rid) if r["present"]],
            "old": sides["old"], "new": sides["new"], "criteria": changes,
            "approval": approval_of(store, new)}


# ============================================================================ doc text
def _git(root: str, *args: str) -> Tuple[int, bytes, str]:
    """git, no shell, no timeout (callers that can be long run as a job), never a console."""
    try:
        p = subprocess.run(["git", "-C", root, *args], capture_output=True,
                           stdin=subprocess.DEVNULL, **hidden())
    except FileNotFoundError:
        return 127, b"", "git is not installed"
    except OSError as exc:
        return 1, b"", str(exc)
    return p.returncode, p.stdout, p.stderr.decode("utf-8", "replace")


def check_rev(rev: str, what: str = "rev") -> str:
    from .diffs import _REF
    r = (rev or "").strip()
    if not _REF.match(r) or ".." in r:
        raise BadRequest(f"{what} must be a plain branch / commit name")
    return r


def check_doc_path(root: str, path: str) -> str:
    from . import snapshots as S
    from .nav import PathRefused
    rel = S.rel_path(root, path or "") if path else None
    if not rel:
        raise PathRefused(S.refusal(root, path or "",
                                    "a repo-relative path inside the repository is required"))
    if not rel.lower().endswith(MD_EXT):
        raise BadRequest("only Markdown files (.md, .markdown, .mdx) render here")
    return rel


def doc_text(root: str, path: str, rev: str = "") -> Dict[str, Any]:
    """A Markdown file's text — the working tree's (`rev` empty) or at a git revision — for the
    rendered view and the rendered diff. The whole file: the page renders it lazily."""
    rel = check_doc_path(root, path)
    if not rev:
        from .diffs import open_contained
        got = open_contained(root, rel)
        if got == "":
            return {"path": rel, "rev": "", "state": "absent", "text": "", "bytes": 0}
        if not got:
            return {"path": rel, "rev": "", "state": "unreadable", "text": "", "bytes": 0}
        fh, _st = got
        with fh:
            data = fh.read()
    else:
        r = check_rev(rev)
        code, data, err = _git(root, "show", "--end-of-options", f"{r}:{rel}")
        if code != 0:
            return {"path": rel, "rev": r, "state": "absent", "text": "", "bytes": 0,
                    "note": err.strip()[:300]}
    if b"\0" in data[:8192]:
        return {"path": rel, "rev": rev, "state": "binary", "text": "", "bytes": len(data)}
    return {"path": rel, "rev": rev, "state": "ok", "bytes": len(data),
            "text": data.decode("utf-8", "replace").replace("\r\n", "\n")}


# ============================================================================ blame on the hunk
def parse_hunk(header: str) -> Tuple[int, int, int, int]:
    m = _HUNK.match(header or "")
    if not m:
        raise BadRequest("hunk must be a unified-diff hunk header (@@ -a,b +c,d @@)")
    o, oc, n, nc = int(m.group(1)), int(m.group(2) or 1), int(m.group(3)), int(m.group(4) or 1)
    return o, oc, n, nc


def _porcelain(text: str) -> List[Dict[str, Any]]:
    """`git blame --porcelain` → one row per line: {line, commit, author, time, summary}."""
    rows: List[Dict[str, Any]] = []
    meta: Dict[str, Dict[str, Any]] = {}
    cur: Optional[Dict[str, Any]] = None
    for ln in text.splitlines():
        if ln.startswith("\t"):
            if cur is not None:
                m = meta.setdefault(cur["commit"], {})
                rows.append({"line": cur["line"], "commit": cur["commit"],
                             "author": m.get("author", ""), "time": m.get("time", 0),
                             "summary": m.get("summary", ""),
                             "uncommitted": set(cur["commit"]) == {"0"}})
            cur = None
            continue
        parts = ln.split(" ")
        if cur is None and len(parts) >= 3 and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}",
                                                            parts[0]):
            cur = {"commit": parts[0], "line": int(parts[2])}
            continue
        if cur is not None:
            m = meta.setdefault(cur["commit"], {})
            key, _, val = ln.partition(" ")
            if key == "author":
                m["author"] = val
            elif key == "author-time" and val.isdigit():
                m["time"] = int(val)
            elif key == "summary":
                m["summary"] = val[:300]
    return rows


def _group(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        g = by.setdefault(r["commit"], {"commit": r["commit"], "author": r["author"],
                                        "time": r["time"], "summary": r["summary"],
                                        "uncommitted": r["uncommitted"], "lines": 0})
        g["lines"] += 1
    return sorted(by.values(), key=lambda g: -g["time"])


def _streamed(root: str, args: List[str]) -> Tuple[int, str]:
    """A git read that may be long (a `log -L` on a deep history): its pipe is killed on cancel."""
    try:
        proc = subprocess.Popen(["git", "-C", root, *args], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **hidden())
    except (FileNotFoundError, OSError) as exc:
        return 127, f"git could not run: {exc}"
    with proc, kill_on_cancel(proc):
        out, err = proc.communicate()
    checkpoint()
    if proc.returncode != 0:
        return proc.returncode, err.decode("utf-8", "replace")
    return 0, out.decode("utf-8", "replace")


def hunk_blame(root: str, path: str, hunk: str, base: str = "HEAD",
               head: str = "") -> Dict[str, Any]:
    """Blame and line history for ONE hunk: its new-side lines at `head` (the working tree when
    empty), its old-side lines at `base`, and `git log -L` over the old-side range at `base` —
    bounded by the hunk's own lines, never the file's whole history. The work of a
    `review.blame` job."""
    from . import snapshots as S
    from .diffs import is_git
    from .nav import PathRefused
    rel = S.rel_path(root, path or "") if path else None
    if not rel:
        raise PathRefused(S.refusal(root, path or "",
                                    "a repo-relative path inside the repository is required"))
    o, oc, n, nc = parse_hunk(hunk)
    b = check_rev(base or "HEAD", "base")
    h = check_rev(head, "head") if head else ""
    if not is_git(root):
        return {"path": rel, "state": "not-git", "note": "not a git repository — no blame"}
    out: Dict[str, Any] = {"path": rel, "hunk": hunk, "base": b, "head": h, "state": "ok",
                           "new": {"start": n, "end": n + nc - 1, "lines": [], "commits": []},
                           "old": {"start": o, "end": o + oc - 1, "lines": [], "commits": []},
                           "history": [], "notes": []}
    report(note="blame · new side", done=0, total=3, unit="steps")
    if nc > 0:
        args = ["blame", "--porcelain", "-L", f"{n},{n + nc - 1}"]
        args += [h, "--", rel] if h else ["--", rel]
        code, text = _streamed(root, args)
        if code == 0:
            out["new"]["lines"] = _porcelain(text)
            out["new"]["commits"] = _group(out["new"]["lines"])
        else:
            out["notes"].append("new side: " + text.strip()[:300])
    report(note="blame · old side", done=1, total=3, unit="steps")
    if oc > 0:
        code, text = _streamed(root, ["blame", "--porcelain", "-L", f"{o},{o + oc - 1}", b,
                                      "--", rel])
        if code == 0:
            out["old"]["lines"] = _porcelain(text)
            out["old"]["commits"] = _group(out["old"]["lines"])
        else:
            out["notes"].append("old side: " + text.strip()[:300])
    report(note="line history", done=2, total=3, unit="steps")
    if oc > 0:
        code, text = _streamed(root, ["log", f"-L{o},{o + oc - 1}:{rel}", "-s",
                                      "--format=%H%x1f%an%x1f%at%x1f%s", b])
        if code == 0:
            for ln in text.splitlines():
                parts = ln.split("\x1f")
                if len(parts) == 4 and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", parts[0]):
                    out["history"].append({"commit": parts[0], "author": parts[1],
                                           "time": int(parts[2]) if parts[2].isdigit() else 0,
                                           "summary": parts[3][:300]})
        else:
            out["notes"].append("history: " + text.strip()[:300])
    else:
        out["notes"].append("this hunk only adds lines — they have no history before it")
    report(done=3, total=3, unit="steps", note="done")
    return out


__all__ = ["PER_PAGE", "TESTS_PER_AC", "tests_panel", "test_history", "approval_of",
           "test_command", "check_test_ids", "run_tests", "record_rerun", "spec_markdown", "spec_diff",
           "doc_text", "hunk_blame", "parse_hunk", "RERUN_NOTE"]
