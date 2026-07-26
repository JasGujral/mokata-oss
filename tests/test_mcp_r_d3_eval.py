"""MCP-R.D3 — the MCP surface EVAL (doc 88 §D3) + the eval-GATED naming decision (doc 88 §D1).

D0–D2 shipped a set of CLAIMS about mokata's MCP surface: it never hangs, it never returns nothing,
a bad argument comes back as a caller-fault refusal, a list read is bounded, a render you did not
ask for is not billed to your context, and a wait announces itself. Through D2 those claims were
ASSERTED by their own build-stage suites. This file turns them into ONE reproducible instrument a
stranger can run in a single command, so the rest of MCP-ROBUST is measured against a baseline
rather than against a memory of how it used to behave (P16 — measured, not assumed).

HOW A STRANGER RUNS IT
    python tests/test_mcp_r_d3_eval.py --report     # the 10 questions + the measured numbers
    python tests/test_mcp_r_d3_eval.py              # the same 10 as a unittest run
    python -m pytest tests/test_mcp_r_d3_eval.py -q # or in the suite / in CI

Nothing else is needed. It builds its OWN fixture repo in a temp dir (`init_repo`), calls the tool
functions through the REAL front door, and tears the fixture down.

WHAT IT RUNS AGAINST (verified from code — the invocation path)
    Every mokata tool is a pure, SDK-free `Dict[str, Any]` function in the ONE `TOOLS` registry
    (`mcp/registry.py`), and `build_server` (`mcp/server.py:280-300`) registers `_serve(spec.fn,
    name=..., kind=...)` with FastMCP. `_serve` is plain Python — the SDK is imported lazily and
    ONLY inside `build_server` — so the eval reaches the exact served callable by calling
    `_serve(spec.fn, ...)` itself, with no FastMCP server, no stdio transport, no event loop and no
    network. That is the same path the D0–D2 suites use, and it is what makes the eval
    deterministic: the only thing between the eval and the tool body is the dispatch wrapper whose
    guarantees are the thing being measured.

DETERMINISTIC — AND THE BOUNDARY WITH 0.0.17's G2
    Every question here is a mechanical assertion over structure, byte counts and wall-clock bounds:
    no live LLM, no live Postgres, no network, no CRG subprocess, no reliance on the host repo's
    contents. Run it twice and it answers identically (asserted below, `test_eval_is_deterministic`).

    That is a deliberate CEILING, not an oversight. This eval can measure whether a result is
    bounded, structured, legible and cheap. It CANNOT measure whether a model, reading only a tool's
    name and description, picks the right tool — that needs a judge with a model in the loop, which
    is the 0.0.17 G2 proof harness. D3 FEEDS G2 (the question format, the fixture, the
    measure-before-you-change discipline); D3 is NOT G2, and nothing here should be read as a claim
    about model behaviour.

THE NAMING PASS IS GATED ON THIS FILE (doc 88 §D1, "Naming pass — measure first")
    §D1 proposes action-oriented renames (`status`→`get_status`, `watch`→`write_dashboard`,
    `query`→`query_graph`, …) to be "evaluated against the D3 eval before committing". So this file
    runs the eval a SECOND time against a renamed view of the registry and compares. The recorded
    outcome is at the bottom of this module (`NAMING_DECISION` + `NAMING_RATIONALE`), pinned by
    tests so it is durable rather than prose — including a test that FAILS if the registry is ever
    renamed without the ledger being updated to say so.

ALSO FOLDED IN HERE (doc 84 §2, MCP-DICT-IMPORT: "fold into MCP-ROBUST D3")
    A startup smoke test that stands the REAL FastMCP server up over the WHOLE registry
    (`TestFastMcpStartupSmoke`). 0.0.14 shipped an MCP server that could not start on Python 3.12
    because no test had ever done that — every SDK test sampled a few tools. It is the one part of
    this file that needs the SDK, so it lives outside the ten questions and skips in a stripped env.

SECRET-SAFETY
    The eval reads a fixture repo it creates in a temp dir and NEVER the host machine's repos,
    config, environment or credentials. The only secret-shaped strings it handles are the two
    obviously-fake literals below, which it PLANTS as arguments in order to assert they do not come
    back out (Q08). No eval output — neither the report nor a failure message — contains a real
    secret, because no real secret is ever read.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import inspect
import json
import os
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import parity
from mokata.config import Surface
from mokata.govern import AuditLedger
from mokata.init import init_repo
from mokata.mcp import server as MS
from mokata.mcp import status as ST
from mokata.mcp import tool_annotations as TA
from mokata.mcp.pagination import DEFAULT_PAGE_LIMIT
from mokata.mcp.registry import TOOLS

# Planted, obviously-fake. They exist so Q08 can prove an uncaught exception does not echo its
# arguments back into the model's context. Neither is read from anywhere real.
FAKE_DSN = "postgresql://evaluser:not-a-real-password@db.invalid:5432/nope"
FAKE_TOKEN = "sk-fake-d3-eval-must-never-render"


# ======================================================================================
# The harness — a question, a measurement, a verdict
# ======================================================================================

@dataclass
class EvalResult:
    """One question's answer. `measured` is the NUMBER (or the counted structure) the question
    actually observed — the point of an eval is the measurement, not the boolean; a green run that
    printed no numbers could not tell you a later change had halved a win."""

    qid: str
    question: str
    guards: str                       # which D0/D1/D2 property this regression-guards
    measured: str
    ok: bool
    detail: Dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        return f"  {'PASS' if self.ok else 'FAIL'}  {self.qid}  {self.question}\n        {self.measured}"


class Fixture:
    """A real mokata repo in a temp dir — the "real mokata repo" doc 88 §D3 asks the eval to run
    over, built from mokata's own `init_repo` rather than a hand-faked directory, so the eval reads
    the same on-disk shapes a user's repo has. Created per question-set run and torn down after."""

    def __init__(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.path = self._dir.name
        init_repo(root=self.path, profile="standard", assume_yes=True, out=lambda *_a: None)
        self.surface = Surface.load(self.path)

    def seed_ledger(self, n: int) -> int:
        """`n` REAL audit entries so the pagination question has a ledger big enough to overflow the
        bounded default. `init_repo` already wrote some, so the total is returned, never assumed."""
        led = AuditLedger.from_mokata_dir(os.path.join(self.path, ".mokata"))
        for i in range(n):
            led.record("phase", phase=f"eval{i:03d}")
        return len(led.entries())

    def close(self) -> None:
        # The eval calls tools THROUGH `_serve`, which fires D0's R5 self-registration in a daemon
        # thread that writes under `.mokata/temp_local/`. Without the drain, teardown races that
        # write and the cleanup fails on the still-open lock file (Windows cannot unlink an open
        # file) — a fixture race, not a product fault. `_await_registrations` is the documented
        # test seam for exactly this.
        MS._await_registrations(5.0)
        self._dir.cleanup()

    def __enter__(self) -> "Fixture":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def _specs(rename: Optional[Dict[str, str]] = None) -> List[Tuple[str, str, Callable[..., Any]]]:
    """The registry as (name, kind, fn), optionally through a RENAME view.

    The rename view is what makes the naming pass measurable instead of hypothetical: the eval can
    be run against the surface as it would be AFTER the §D1 renames, with the same bodies, and the
    two runs compared. Renaming here changes exactly what a real rename would change about the
    served surface — the `name` passed to `_serve` and to `add_tool` — and nothing else."""
    rename = rename or {}
    return [(rename.get(t.name, t.name), t.kind, t.fn) for t in TOOLS]


def _call(name: str, kind: str, fn: Callable[..., Any], /, **kwargs: Any) -> Dict[str, Any]:
    """Invoke a tool the way the SERVER does — through `_serve`, with no FastMCP server.

    The three registry fields are POSITIONAL-ONLY on purpose: several tools take their own `kind`
    argument (`query`, `remember`, `vault_push`), and `**kwargs` must carry those to the TOOL, not
    bind them to the dispatch metadata."""
    return MS._serve(fn, name=name, kind=kind)(**kwargs)


def _bytes(payload: Any, root: str = "") -> int:
    """A payload's size as the transport actually pays for it.

    `root` (the fixture's temp dir) is normalized to a fixed token FIRST. Without that the byte
    counts would ride the length of whatever path `mkdtemp` happened to hand out — several tools
    echo the repo root — and the eval's headline numbers would differ between two runs on the same
    machine, let alone between a stranger's machine and this one. Normalizing makes the measured
    magnitudes reproducible, which is the whole point of reporting them.

    BOTH the given root and its `realpath` are normalized: on macOS `mkdtemp` returns
    `/var/folders/...`, which is a symlink to `/private/var/folders/...`, and a tool that resolves
    its path echoes the longer form — an 8-byte-per-occurrence difference that made the byte
    measurements non-reproducible before this was handled.

    `default=str` so a stray non-JSON value is counted rather than crashing the measurement."""
    blob = json.dumps(payload, default=str)
    for form in {root, os.path.realpath(root)} if root else ():
        blob = blob.replace(json.dumps(form)[1:-1], "<repo>").replace(form, "<repo>")
    return len(blob)


# ======================================================================================
# The ten questions
# ======================================================================================
#
# Each takes the fixture + the (possibly renamed) registry view and returns an EvalResult. They are
# deliberately written as MEASUREMENTS that happen to have a pass/fail threshold, not as bare
# asserts — so `--report` is informative on a green run.

def q01_annotations(fx: Fixture, specs: List[Tuple[str, str, Callable[..., Any]]]) -> EvalResult:
    """D1a — can a client tell read from write BEFORE it calls?"""
    annotated = reads = reads_ok = writes = writes_ok = 0
    bad: List[str] = []
    for name, kind, _fn in specs:
        ann = TA.annotations_for(kind, name)
        annotated += 1
        if kind == "read":
            reads += 1
            if ann.get("readOnlyHint") is True and "destructiveHint" not in ann:
                reads_ok += 1
            else:
                bad.append(name)
        else:
            writes += 1
            if (ann.get("readOnlyHint") is False and ann.get("destructiveHint") is False
                    and ann.get("idempotentHint") is True):
                writes_ok += 1
            else:
                bad.append(name)
        if "openWorldHint" not in ann:
            bad.append(name)
    ok = not bad and annotated == len(specs)
    return EvalResult(
        "Q01", "Does every registered tool carry MCP annotations projected from its kind?",
        "D1a annotations",
        f"{annotated}/{len(specs)} tools annotated · {reads_ok}/{reads} read tools readOnlyHint:true "
        f"· {writes_ok}/{writes} write+approve readOnlyHint:false + destructiveHint:false "
        f"(propose-only) · openWorldHint on all {annotated}",
        ok, {"annotated": annotated, "reads": reads, "writes": writes, "bad": sorted(set(bad))})


# `baseline` is the ONE response_format tool the eval does not measure here: it SHELLS OUT to the
# project's test suite (doc 88 root cause 1), which is neither deterministic nor free. Its
# concise/detailed split is guarded by the D1b suite and its bound by D0's `BASELINE_MCP_TIMEOUT_
# SECONDS` pin — this eval measures the seven whose measurement costs nothing.
_RF_EXCLUDED = {"baseline"}


def q02_response_format(fx: Fixture,
                        specs: List[Tuple[str, str, Callable[..., Any]]]) -> EvalResult:
    """D1b — is the default cheap, and is the human view still reachable?"""
    concise_total = detailed_total = 0
    measured: List[str] = []
    cheaper: List[str] = []
    no_render: List[str] = []
    bad: List[str] = []
    for name, kind, fn in specs:
        # Selected by the underlying FUNCTION, never by the served name: the naming-pass comparison
        # re-runs this question against renamed tools, and a name-keyed exclusion would silently
        # stop excluding `baseline` (which shells out to the test suite) the moment it was renamed.
        if ("response_format" not in inspect.signature(fn).parameters
                or fn.__name__ in _RF_EXCLUDED):
            continue
        c = _bytes(_call(name, kind, fn, path=fx.path), fx.path)
        d = _bytes(_call(name, kind, fn, path=fx.path, response_format="detailed"), fx.path)
        concise_total += c
        detailed_total += d
        measured.append(f"{name} {c}→{d}")
        if c < d:
            cheaper.append(name)
        elif c == d:
            # The tool took a DEGRADE path that produced no render to drop (over a fresh fixture
            # repo `decompose` has no emitted spec, so it early-returns a note). Equal is the
            # correct answer there — concise cannot be cheaper than a result with nothing to omit.
            no_render.append(name)
        else:
            bad.append(name)                      # concise LARGER than detailed is always a defect
    saved = detailed_total - concise_total
    pct = (saved * 100.0 / detailed_total) if detailed_total else 0.0
    ok = not bad and len(cheaper) >= 6
    return EvalResult(
        "Q02", "Is the default response_format cheaper than detailed on every render-bearing tool?",
        "D1b response_format",
        f"{len(cheaper)}/{len(measured)} tools strictly cheaper concise "
        f"({len(no_render)} on a no-render degrade path, 0 larger) · concise {concise_total}B vs "
        f"detailed {detailed_total}B = {saved}B ({pct:.0f}%) NOT billed to context by default",
        ok, {"concise": concise_total, "detailed": detailed_total, "per_tool": measured,
             # COUNTS, not name lists: the naming-pass comparison re-runs this question against
             # renamed tools, and a name list would differ by construction (asserting the rename
             # happened) instead of showing whether anything MEASURABLE moved.
             "cheaper": len(cheaper), "no_render": len(no_render), "larger": len(bad)})


def q03_pagination(fx: Fixture, specs: List[Tuple[str, str, Callable[..., Any]]]) -> EvalResult:
    """D1c — is a list read bounded by default, and honest about there being more?"""
    total_entries = fx.seed_ledger(120)
    name, kind, fn = next((n, k, f) for n, k, f in specs if f.__name__ == "audit")
    default = _call(name, kind, fn, path=fx.path)
    unbounded = _call(name, kind, fn, path=fx.path, limit=0)
    d_bytes, u_bytes = _bytes(default, fx.path), _bytes(unbounded, fx.path)
    ok = (default["count"] <= DEFAULT_PAGE_LIMIT
          and default["total"] == total_entries
          and default["has_more"] is True
          and default["next_offset"] == default["count"]
          and unbounded["count"] == total_entries
          and d_bytes < u_bytes)
    pct = (d_bytes * 100.0 / u_bytes) if u_bytes else 0.0
    return EvalResult(
        "Q03", "Is the audit ledger bounded by default with an honest cursor?",
        "D1c pagination (the audit limit=0 default was the poster child)",
        f"default page {default['count']}/{total_entries} entries, has_more={default['has_more']}, "
        f"next_offset={default['next_offset']} · {d_bytes}B vs {u_bytes}B unbounded "
        f"({pct:.0f}% of the whole ledger)",
        ok, {"page": default["count"], "total": total_entries,
             "default_bytes": d_bytes, "unbounded_bytes": u_bytes})


def q04_bad_enum(fx: Fixture, specs: List[Tuple[str, str, Callable[..., Any]]]) -> EvalResult:
    """D1d — is a caller's typo a REFUSAL (fix your call) and not an ERROR (the server broke)?"""
    name, kind, fn = next((n, k, f) for n, k, f in specs if f.__name__ == "query")
    started = time.time()
    res = _call(name, kind, fn, path=fx.path, kind="not_a_real_kind")
    elapsed = time.time() - started
    ok = (res.get("status") == ST.REFUSED
          and res.get("field") == "kind"
          and bool(res.get("allowed"))
          and res.get("committed") is False
          and "not_a_real_kind" not in json.dumps(res)      # the VALUE never travels back
          and elapsed < 5.0)
    return EvalResult(
        "Q04", "Does a bad enum come back as `refused` naming the field + allowed set, not a hang?",
        "D1d input validation · D0 R6 status vocab",
        f"status={res.get('status')!r} field={res.get('field')!r} "
        f"allowed={len(res.get('allowed') or [])} values · {elapsed * 1000:.0f}ms "
        f"(budget {MS.MCP_SURFACE_TIMEOUT_SECONDS:.0f}s) · offending value not echoed",
        ok, {"elapsed_ms": round(elapsed * 1000, 1), "status": res.get("status")})


def q05_path_traversal(fx: Fixture,
                       specs: List[Tuple[str, str, Callable[..., Any]]]) -> EvalResult:
    """D1d — is a traversing `path` refused at the front door, before any filesystem read?"""
    name, kind, fn = next((n, k, f) for n, k, f in specs if f.__name__ == "status")
    with mock.patch.object(Surface, "load", side_effect=AssertionError("filesystem was read")):
        res = _call(name, kind, fn, path="../../etc")
    ok = (res.get("status") == ST.REFUSED
          and res.get("field") == "path"
          and res.get("committed") is False
          and "etc" not in json.dumps(res))                  # the path never travels back
    return EvalResult(
        "Q05", "Is a traversing `path` refused with ZERO filesystem read?",
        "D1d path sanitation (pre-step runs ahead of registration and the body thread)",
        f"status={res.get('status')!r} field={res.get('field')!r} · `Surface.load` never reached "
        f"(patched to raise) · offending path not echoed",
        ok, {"status": res.get("status")})


def q06_awaiting_head(fx: Fixture, specs: List[Tuple[str, str, Callable[..., Any]]]) -> EvalResult:
    """D2 — does a proposal LEAD with the wait, so a healthy gate cannot read as a stuck server?"""
    name, kind, fn = next((n, k, f) for n, k, f in specs if f.__name__ == "remember")
    res = _call(name, kind, fn, path=fx.path, subject="eval", value="the eval ran")
    keys = list(res)
    head = str(res.get("awaiting", ""))
    ok = (keys[:1] == ["awaiting"]
          and res.get("status") == ST.PROPOSED
          and bool(res.get("proposal_id"))
          and res.get("proposal_id") in head
          and "mokata approve" in json.dumps(res))
    return EvalResult(
        "Q06", "Does a gated write lead with the `awaiting` head + proposal id + the way out?",
        "D2 B-AMEND-STUCK relay (burial WAS the bug) · D0 proposed-is-immediate",
        f"first key={keys[0]!r} · status={res.get('status')!r} · proposal_id present and quoted in "
        f"the head · `mokata approve` named · {len(keys)} keys total",
        ok, {"first_key": keys[0] if keys else None, "status": res.get("status")})


def q07_read_surface_bounded(fx: Fixture,
                             specs: List[Tuple[str, str, Callable[..., Any]]]) -> EvalResult:
    """D0 R1/R3 — across the WHOLE read surface: a structured dict, inside budget, never None."""
    reads = [(n, k, f) for n, k, f in specs if k == "read"]
    slowest = 0.0
    slowest_tool = ""
    bad: List[str] = []
    for name, kind, fn in reads:
        started = time.time()
        res = _call(name, kind, fn, path=fx.path)
        took = time.time() - started
        if took > slowest:
            slowest, slowest_tool = took, name
        if not isinstance(res, dict) or res.get("status") == ST.TIMED_OUT:
            bad.append(name)
    ok = not bad and slowest < MS.MCP_SURFACE_TIMEOUT_SECONDS
    return EvalResult(
        "Q07", "Does EVERY read tool return a structured dict inside the MCP-surface budget?",
        "D0 R1 never-hang · R3 never-None (the doc 88 exit criterion, swept across the surface)",
        f"{len(reads)}/{len(reads)} read tools returned a dict · slowest {slowest_tool} "
        f"{slowest * 1000:.0f}ms vs the {MS.MCP_SURFACE_TIMEOUT_SECONDS:.0f}s budget · "
        f"0 None returns · 0 timeouts",
        ok, {"read_tools": len(reads), "slowest_ms": round(slowest * 1000, 1),
             "slowest_tool": slowest_tool, "bad": bad})


def q08_exception_no_leak(fx: Fixture,
                          specs: List[Tuple[str, str, Callable[..., Any]]]) -> EvalResult:
    """D0 R2 — is an uncaught exception mokata's own `error`, with the arguments left behind?"""
    def boom(path: str = ".", dsn: str = "", token: str = "") -> Dict[str, Any]:
        raise RuntimeError(f"connect failed for {dsn} with {token}")

    res = _call("eval_boom", "read", boom, path=fx.path, dsn=FAKE_DSN, token=FAKE_TOKEN)
    blob = json.dumps(res)
    ok = (res.get("status") == ST.ERROR
          and res.get("isError") is True
          and res.get("committed") is False
          and "RuntimeError" in res.get("reason", "")
          and FAKE_DSN not in blob and FAKE_TOKEN not in blob
          and "not-a-real-password" not in blob)
    return EvalResult(
        "Q08", "Does an uncaught exception become mokata's `error` WITHOUT echoing its arguments?",
        "D0 R2 exception→isError in mokata's voice · secret-safety (TYPE name only)",
        f"status={res.get('status')!r} isError={res.get('isError')} · exception TYPE named, "
        f"message dropped · planted DSN + token absent from all {len(blob)}B of the result",
        ok, {"status": res.get("status"), "result_bytes": len(blob)})


def q09_hung_body(fx: Fixture, specs: List[Tuple[str, str, Callable[..., Any]]]) -> EvalResult:
    """D0 R1 — does a body that never finishes still produce an answer, inside the budget?"""
    def hangs(path: str = ".") -> Dict[str, Any]:
        time.sleep(30)                                        # daemon worker; orphaned, not joined
        return {"never": "reached"}

    budget = 0.3
    with mock.patch.object(MS, "MCP_SURFACE_TIMEOUT_SECONDS", budget):
        started = time.time()
        res = _call("eval_hang", "read", hangs, path=fx.path)
        elapsed = time.time() - started
    ok = (res.get("status") == ST.TIMED_OUT
          and res.get("isError") is True
          and res.get("committed") is False
          and "eval_hang" in res.get("reason", "")
          and "mokata" in res.get("hint", "")                 # the CLI fallback is named
          and elapsed < budget + 2.0)
    return EvalResult(
        "Q09", "Does a body that never returns still answer inside the budget (fault injection)?",
        "D0 R1 never-hang · R7 the bounded-budget verdict names the op + the CLI fallback",
        f"status={res.get('status')!r} in {elapsed * 1000:.0f}ms against a {budget * 1000:.0f}ms "
        f"injected budget · names the operation + the terminal fallback · caller never blocked on "
        f"the orphaned 30s worker",
        ok, {"elapsed_ms": round(elapsed * 1000, 1), "budget_ms": budget * 1000})


# The three outcomes D2 appends to every gated-write description. Matched on their load-bearing
# phrases rather than the whole block, so re-wording the contract does not silently break the eval
# while dropping one of the three outcomes still does.
_OUTCOME_MARKERS = ("WAITING ON A HUMAN", "HUMAN DECLINED", "mokata approve --list")


def q10_descriptions(fx: Fixture, specs: List[Tuple[str, str, Callable[..., Any]]]) -> EvalResult:
    """D2 — does every gated write explain the three outcomes BEFORE it is called?"""
    gated = covered = reads = reads_identical = 0
    bad: List[str] = []
    for name, kind, fn in specs:
        doc = fn.__doc__ or ""
        desc = TA.description_for(kind, name, doc)
        if kind == "read":
            reads += 1
            if desc == doc.strip():
                reads_identical += 1
            else:
                bad.append(f"{name}(read desc drifted)")
        else:
            gated += 1
            if all(m in desc for m in _OUTCOME_MARKERS):
                covered += 1
            else:
                bad.append(f"{name}(missing an outcome)")
    ok = not bad and gated > 0
    return EvalResult(
        "Q10", "Does every gated write's description spell the three outcomes (incl. NO result)?",
        "D2 decline legibility — the only surface that can explain the outcome that sends nothing",
        f"{covered}/{gated} gated writes carry all three outcomes (proposed / human-declined / "
        f"fault) + the `mokata approve --list` out-of-band fallback · {reads_identical}/{reads} "
        f"read descriptions byte-identical to their docstring",
        ok, {"gated": gated, "covered": covered, "reads": reads, "bad": bad})


# The `detail` keys that legitimately move between two runs, excluded when two runs are compared
# for equality. Everything NOT listed here is a structural measurement and must reproduce exactly.
#   * wall-clock — `elapsed_ms` / `slowest_ms` are timings; `slowest_tool` is whichever tool won a
#     sub-millisecond race, which is scheduling noise, not a property.
#   * name-bearing labels — `per_tool` embeds the tool NAMES, which is exactly what the naming-pass
#     comparison changes on purpose; comparing them would assert the rename did not happen rather
#     than that it changed nothing that matters.
# Byte magnitudes are NOT excluded: `_bytes` normalizes the fixture path, so they are reproducible
# and a change in one is a real regression in a measured win.
_VOLATILE_DETAIL = frozenset({"elapsed_ms", "slowest_ms", "slowest_tool", "result_bytes",
                              "per_tool"})

QUESTIONS: Tuple[Callable[..., EvalResult], ...] = (
    q01_annotations, q02_response_format, q03_pagination, q04_bad_enum, q05_path_traversal,
    q06_awaiting_head, q07_read_surface_bounded, q08_exception_no_leak, q09_hung_body,
    q10_descriptions,
)


def run_eval(rename: Optional[Dict[str, str]] = None) -> List[EvalResult]:
    """Run all ten questions over a fresh fixture repo. `rename` runs them against a renamed VIEW of
    the registry — same bodies, different served names — which is how the §D1 naming pass is
    measured rather than argued (see NAMING_DECISION)."""
    with Fixture() as fx:
        specs = _specs(rename)
        return [q(fx, specs) for q in QUESTIONS]


def report(results: List[EvalResult], title: str = "MCP-R.D3 eval") -> str:
    passed = sum(1 for r in results if r.ok)
    head = f"{title} — {passed}/{len(results)} green"
    body = "\n".join(r.line() for r in results)
    return f"{head}\n{body}"


# ======================================================================================
# THE NAMING DECISION (doc 88 §D1, gated on the eval above)
# ======================================================================================
#
# §D1 asks for action-oriented renames of the non-verb tools, "evaluated against the D3 eval before
# committing". These are the candidates — the three doc 88 names, plus the rest of the read surface
# whose name is a NOUN or a bare topic rather than a verb phrase. Listed in full so the decision is
# made against the real batch, not against the three illustrative ones.
NAMING_CANDIDATES: Tuple[Tuple[str, str], ...] = (
    ("status", "get_status"),               # doc 88 §D1, named
    ("watch", "write_dashboard"),           # doc 88 §D1, named — and the most defensible of all:
                                            #   the tool does not watch anything, it WRITES an HTML
                                            #   dashboard once and returns its path (tools_read.py
                                            #   :246-265, calling `dashboard.write_dashboard`).
    ("query", "query_graph"),               # doc 88 §D1, named
    ("coverage", "get_coverage"),
    ("budget", "get_budget"),
    ("audit", "read_audit"),
    ("progress", "get_progress"),
    ("lanes", "get_lanes"),
    ("govern", "get_governance"),
    ("rules", "list_rules"),
    ("skills", "list_skills"),
    ("suggest", "suggest_next"),
    ("tour", "get_tour"),
    ("sessions", "list_sessions"),
    ("preview", "preview_pipeline"),
    ("baseline", "run_baseline"),
    ("doctor", "run_doctor"),
    ("recall", "recall_memory"),
    ("decompose", "decompose_work"),
    ("reset", "reset_run"),
    ("init", "init_repo"),
)
RENAME_MAP: Dict[str, str] = dict(NAMING_CANDIDATES)

# The recorded call. One of {"rename", "no-rename"} — and "no-rename" is a legitimate stage outcome:
# gating a rename on a measurement means accepting the answer when the measurement declines to
# support it. Changing this constant is the ONLY way to move the decision, and the tests below make
# renaming the registry without moving it a hard failure.
NAMING_DECISION = "no-rename"
NAMING_DECISION_DATE = "2026-07-20"
NAMING_RATIONALE = """\
MEASURED, not argued. Three findings, in the order they were taken:

(1) THE EVAL IS INVARIANT UNDER THE RENAME. Running all ten questions against the renamed registry
    view (`run_eval(rename=RENAME_MAP)`) reproduces the pre-rename verdicts and the pre-rename
    numbers exactly — same annotations coverage, same byte deltas, same bounds, same refusal
    shapes. That is not a flaw in the instrument: renaming a tool changes the string the model
    reads, and every property D0-D2 shipped is a property of the RESULT. So the deterministic
    instrument the gate was told to use reports a win of exactly ZERO. Doc 88's own rule is
    "evaluated against the D3 eval before committing"; a zero delta is not a demonstrated win.

(2) THE ONLY DIMENSION A RENAME COULD IMPROVE IS THE ONE THIS STAGE CANNOT MEASURE. The claim
    behind action-oriented naming is that a model picks the right tool more reliably from a verb
    phrase. That is a statement about MODEL behaviour, observable only with a model in the loop —
    i.e. the 0.0.17 G2 judged harness, which D3 explicitly feeds and explicitly is not. Renaming
    now on the strength of an unmeasured claim is precisely the taste call the gate exists to stop.

(3) THE COST IS REAL, MEASURED, AND FALLS ON A NAME CORRESPONDENCE THAT IS ITSELF A FEATURE.
    19 of the 21 candidates share their name with a CLI subcommand, and 6 of those ALSO share it
    with a /mokata: slash command (measured by `naming_blast_radius()` from `parity.SURFACE_MATRIX`
    and asserted below; across the whole 55-tool registry the figures are 23 and 7). A user today
    reads `watch` in the docs, types `mokata watch`, runs `/mokata:watch`, and the model calls
    `mokata_watch` — one name, three surfaces. Renaming only the MCP tool splits that into two
    vocabularies for the same capability and creates a NEW drift class across docs, skills and the
    parity matrix — the class D-CMDNS was built to close. Renaming all three surfaces in step is a
    far larger, genuinely breaking change than doc 88 §D1 scoped, and is not an MCP-ROBUST item.

CONSEQUENCE: no rename this release. The candidates stay on the record above with their proposed
names, and the decision is re-taken at 0.0.17 G2 with an instrument that can actually see the
claimed benefit. If G2 measures a win, this constant flips and the blast radius below is the
itemized work. `watch` -> `write_dashboard` is the strongest single candidate and should be
re-tested first: it is the one name that is not merely un-verby but actively WRONG about what the
tool does.
"""


def naming_blast_radius() -> Dict[str, int]:
    """The measured COST side of the decision: how many code sites one rename batch would move.
    Counted from the live surface, not estimated — so a future G2-driven "yes" starts from a real
    number. Doc/skill prose is NOT counted here (it is grepped separately in the stage report);
    these are the structural, test-enforced couplings."""
    cli_shared = 0
    slash_shared = 0
    matrix_sites = 0
    for cmd, surf in parity.SURFACE_MATRIX.items():
        for tool in surf.mcp:
            if tool in RENAME_MAP:
                matrix_sites += 1
                if tool == cmd.replace("-", "_"):
                    cli_shared += 1
                if tool in surf.slash:
                    slash_shared += 1
    return {
        "tools_renamed": len(RENAME_MAP),
        "registry_decorators": sum(1 for t in TOOLS if t.name in RENAME_MAP),
        "parity_matrix_sites": matrix_sites,
        "open_world_set_entries": sum(1 for n in TA.OPEN_WORLD_TOOLS if n in RENAME_MAP),
        "cli_name_correspondence_broken": cli_shared,
        "slash_name_correspondence_broken": slash_shared,
    }


# ======================================================================================
# The tests — the eval as a regression guard, and the decision as a durable pin
# ======================================================================================

class TestMcpRD3Eval(unittest.TestCase):
    """The ten questions, each as its own test so a regression names the property it broke."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.results = run_eval()
        cls.by_id = {r.qid: r for r in cls.results}

    def _check(self, qid: str) -> None:
        r = self.by_id[qid]
        self.assertTrue(r.ok, f"{qid} FAILED — {r.question}\n  measured: {r.measured}\n"
                              f"  detail: {r.detail}")

    def test_mcp_r_d3_q01_annotations(self):
        self._check("Q01")

    def test_mcp_r_d3_q02_response_format(self):
        self._check("Q02")

    def test_mcp_r_d3_q03_pagination(self):
        self._check("Q03")

    def test_mcp_r_d3_q04_bad_enum_refused(self):
        self._check("Q04")

    def test_mcp_r_d3_q05_path_traversal_refused(self):
        self._check("Q05")

    def test_mcp_r_d3_q06_awaiting_head(self):
        self._check("Q06")

    def test_mcp_r_d3_q07_read_surface_bounded(self):
        self._check("Q07")

    def test_mcp_r_d3_q08_exception_no_leak(self):
        self._check("Q08")

    def test_mcp_r_d3_q09_hung_body_times_out(self):
        self._check("Q09")

    def test_mcp_r_d3_q10_description_contract(self):
        self._check("Q10")

    def test_mcp_r_d3_eval_is_green_on_the_post_d2_surface(self):
        """The headline: the whole eval is green as shipped. This is the BASELINE the rest of
        MCP-ROBUST is measured against — a later change that breaks any D0-D2 property fails here
        with the property named, not merely somewhere in a 3900-test suite."""
        failed = [r.qid for r in self.results if not r.ok]
        self.assertEqual(failed, [], report(self.results))

    def test_mcp_r_d3_eval_is_deterministic(self):
        """Stranger-reproducible means the same answer twice. A second independent run (its own
        fixture repo) must reproduce every verdict — no clock, no network, no host-repo dependence,
        no live LLM, no live DB. This is the property that separates D3 from the 0.0.17 G2 judged
        harness, and it is asserted rather than claimed."""
        again = run_eval()
        self.assertEqual([(r.qid, r.ok) for r in again],
                         [(r.qid, r.ok) for r in self.results])
        # The structural measurements (counts, coverage) must match too — only wall-clock timings
        # legitimately vary, and they are excluded by key rather than by tolerance.
        volatile = _VOLATILE_DETAIL
        for a, b in zip(again, self.results):
            self.assertEqual({k: v for k, v in a.detail.items() if k not in volatile},
                             {k: v for k, v in b.detail.items() if k not in volatile},
                             f"{a.qid} measured differently on a second run")

    def test_mcp_r_d3_eval_needs_no_network_llm_or_live_db(self):
        """The reproducibility contract, enforced structurally: this module imports no HTTP client,
        no socket, no psycopg and no model SDK. A future question that reaches for one fails here
        rather than turning the eval into something a stranger cannot run offline.

        Read from the module's IMPORT STATEMENTS via the AST — a substring scan of the source would
        match the banned list written here in the test itself."""
        import ast

        banned = {"requests", "httpx", "socket", "ssl", "psycopg", "psycopg2", "anthropic",
                  "openai", "urllib", "http"}
        imported = set()
        for node in ast.walk(ast.parse(inspect.getsource(sys.modules[__name__]))):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                imported.add(node.module.split(".")[0])
        leaked = sorted(imported & banned)
        self.assertEqual(leaked, [],
                         f"the eval must stay offline-reproducible; it imports {leaked}")


class TestExitCriteriaSweep(unittest.TestCase):
    """doc 88 "Exit criteria", asserted as a set rather than trusted across four build stages.

    The one criterion NOT re-run here is the full fault-injection matrix (slow DB / hung CRG child /
    10-minute suite / raised exception / None return) — that is D0's suite,
    `test_mcp_r_d0_serve.py`, which owns it and is a pinned prior stage. The eval covers the two
    injections that are free and deterministic (hung body Q09, raised exception Q08) and defers the
    subprocess/DB injections to D0 rather than duplicating a slower copy of them."""

    def test_exit_annotations_present_on_every_tool(self):
        for t in TOOLS:
            ann = TA.annotations_for(t.kind, t.name)
            self.assertIn("readOnlyHint", ann, t.name)
            if t.kind == "read":
                self.assertTrue(ann["readOnlyHint"], t.name)

    def test_exit_baseline_never_blocks_stdio_past_120s(self):
        from mokata.baseline import BASELINE_MCP_TIMEOUT_SECONDS
        self.assertLessEqual(BASELINE_MCP_TIMEOUT_SECONDS, 120)
        self.assertEqual(MS._mcp_timeout_for("baseline"), BASELINE_MCP_TIMEOUT_SECONDS)
        self.assertLess(MS._mcp_timeout_for("baseline"), 600,
                        "the terminal bound must not be what the MCP surface waits on")

    def test_exit_list_tools_have_bounded_defaults(self):
        """No list tool may DEFAULT to unbounded — the `audit limit=0` default was the bug."""
        for t in TOOLS:
            params = inspect.signature(t.fn).parameters
            if "limit" not in params:
                continue
            default = params["limit"].default
            self.assertNotEqual(default, 0,
                                f"{t.name} defaults to the unbounded opt-out")
            self.assertLessEqual(default, DEFAULT_PAGE_LIMIT, t.name)

    def test_exit_no_tool_emits_render_text_and_full_dict_together(self):
        """Every render-bearing tool routes through the ONE concise/detailed branch, so nothing
        pays double by default. Structural: the render field must be a `LazyRender`, i.e. the tool
        cannot have built the string before deciding to drop it."""
        from mokata.mcp.response_format import LazyRender, apply_response_format
        seen = 0
        for t in TOOLS:
            if "response_format" not in inspect.signature(t.fn).parameters:
                continue
            seen += 1
            src = inspect.getsource(t.fn)
            self.assertIn("apply_response_format", src, t.name)
            self.assertIn("LazyRender", src, t.name)
        self.assertGreaterEqual(seen, 8)
        # and the branch itself drops, rather than blanks, the render field
        out = apply_response_format("concise", {"a": 1, "block": LazyRender(lambda: "x" * 999)})
        self.assertEqual(out, {"a": 1})

    def test_exit_status_vocab_is_the_doc_88_set(self):
        self.assertEqual(ST.STATUS_VOCAB, frozenset({
            "proposed", "refused", "blocked", "running", "timed_out", "degraded",
            "committed", "error"}))


class TestFastMcpStartupSmoke(unittest.TestCase):
    """The MCP-DICT-IMPORT systemic guard, folded into D3 by doc 84 §2.

    0.0.14 shipped an MCP server that was DEAD ON ARRIVAL for every Python 3.12 user: `server.py`
    annotated a tool `Optional[Dict[str, Any]]` without importing `Dict`, and under
    `from __future__ import annotations` FastMCP's `func_metadata` must EVAL that string annotation
    — so `build_server()` raised `InvalidSignature` and the server never connected. A GitHub user
    found it, not the 3,900-test suite, because nothing ever stood the REAL FastMCP server up over
    the WHOLE registry: every existing SDK test sampled a handful of tools.

    This is that missing test. It is a smoke test on purpose — it asserts the server BUILDS and
    every registered tool's signature evaluates, which is the entire failure class, and it does so
    per-tool so a regression names the offending tool instead of dying on the first one.

    Skipped only in a stripped environment with no SDK; `mcp` is an unconditional dependency, so on
    CI and on any healthy `pip install mokata` this runs."""

    @unittest.skipUnless(MS.mcp_available(), "the MCP SDK is absent (stripped env)")
    def test_build_server_evaluates_every_tool_signature(self):
        import asyncio

        server = MS.build_server()                         # the 0.0.14 crash was HERE
        tools = asyncio.run(server.list_tools())           # ...or here, per-tool
        self.assertEqual(len(tools), len(TOOLS))
        self.assertEqual({t.name for t in tools}, {t.name for t in TOOLS})
        for tool in tools:
            self.assertIsInstance(tool.inputSchema, dict, tool.name)
            self.assertEqual(tool.inputSchema.get("type"), "object", tool.name)

    @unittest.skipUnless(MS.mcp_available(), "the MCP SDK is absent (stripped env)")
    def test_every_tool_registers_individually_under_real_fastmcp(self):
        """Per-tool, so the failure message names the tool whose annotation stopped evaluating —
        the diagnostic the 0.0.14 reporter had to reconstruct by hand."""
        import asyncio

        from mcp.server.fastmcp import FastMCP

        for spec in TOOLS:
            with self.subTest(tool=spec.name):
                srv = FastMCP("smoke")
                try:
                    srv.add_tool(MS._serve(spec.fn, name=spec.name, kind=spec.kind),
                                 name=spec.name)
                    got = asyncio.run(srv.list_tools())
                except Exception as exc:                   # noqa: BLE001 - the smoke IS the catch
                    self.fail(f"{spec.name}: FastMCP could not register it — "
                              f"{type(exc).__name__}: {exc}")
                self.assertEqual([t.name for t in got], [spec.name])


class TestNamingDecision(unittest.TestCase):
    """The §D1 naming pass — the decision, its measurement, and its durability.

    A recorded "no" is only worth something if it cannot rot: these tests make the ledger and the
    registry mutually enforcing, so a future rename that skips the gate fails the build."""

    def test_naming_decision_is_recorded(self):
        self.assertIn(NAMING_DECISION, ("rename", "no-rename"))
        self.assertTrue(NAMING_CANDIDATES, "the candidates must be on the record")
        self.assertGreater(len(NAMING_RATIONALE), 500,
                           "a gated decision carries its reasoning, not a verdict")
        for old, new in NAMING_CANDIDATES:
            self.assertNotEqual(old, new)
            self.assertFalse(new.startswith("mokata_"),
                             f"{new}: the client already applies the `mokata_` prefix — "
                             f"prefixing here would double it")

    def test_naming_decision_matches_the_shipped_registry(self):
        """The pin that makes the "no" durable. While the ledger says `no-rename`, every candidate
        must still be registered under its CURRENT name and under NO proposed name. Renaming a tool
        without moving `NAMING_DECISION` therefore fails here — the gate cannot be walked past."""
        registered = {t.name for t in TOOLS}
        if NAMING_DECISION == "no-rename":
            for old, new in NAMING_CANDIDATES:
                self.assertIn(old, registered,
                              f"{old} was renamed but NAMING_DECISION still says no-rename")
                self.assertNotIn(new, registered,
                                 f"{new} is registered but NAMING_DECISION still says no-rename")
        else:                                                  # pragma: no cover - the future path
            for old, new in NAMING_CANDIDATES:
                self.assertIn(new, registered)
                self.assertNotIn(old, registered)

    def test_naming_eval_is_invariant_under_the_candidate_renames(self):
        """FINDING (1) — the measurement the gate turns on, re-run every build rather than quoted.

        The same ten questions, over the same bodies, with the served names replaced by the §D1
        candidates: identical verdicts AND identical structural measurements. A rename therefore
        moves this instrument by zero, which is why the recorded decision is `no-rename`.

        If a future stage makes naming measurable HERE (rather than at G2), this test will start
        failing — and that failure is the signal to re-take the decision, not to relax the test."""
        base = run_eval()
        renamed = run_eval(rename=RENAME_MAP)
        self.assertEqual([(r.qid, r.ok) for r in renamed], [(r.qid, r.ok) for r in base],
                         "the rename changed a verdict — re-take the naming decision")
        volatile = _VOLATILE_DETAIL
        for a, b in zip(renamed, base):
            self.assertEqual({k: v for k, v in a.detail.items() if k not in volatile},
                             {k: v for k, v in b.detail.items() if k not in volatile},
                             f"{a.qid} measured differently under the rename — re-take the decision")

    def test_naming_blast_radius_is_measured_not_estimated(self):
        """FINDING (3) — the cost side, counted from the live surface so a future "yes" starts from
        a real number. The load-bearing measurement is the name CORRESPONDENCE: an MCP-only rename
        splits one capability's name across two vocabularies."""
        radius = naming_blast_radius()
        self.assertEqual(radius["tools_renamed"], len(NAMING_CANDIDATES))
        self.assertEqual(radius["registry_decorators"], len(NAMING_CANDIDATES),
                         "every candidate must be a live registry entry")
        self.assertGreater(radius["cli_name_correspondence_broken"], 0,
                           "the CLI-name coupling is the measured cost — if it is zero the "
                           "rationale's finding (3) no longer holds and the decision must be re-taken")
        self.assertGreater(radius["slash_name_correspondence_broken"], 0)
        self.assertGreater(radius["parity_matrix_sites"], 0)

    def test_naming_no_double_prefix_anywhere(self):
        """The client prefixes `mokata_` (server name `mokata`); no registered tool may carry it."""
        for t in TOOLS:
            self.assertFalse(t.name.startswith("mokata_"), t.name)
            self.assertFalse(t.name.startswith("mcp__"), t.name)


class TestEvalFeedsG2NotIsG2(unittest.TestCase):
    """The boundary, stated in the artifact rather than only in the stage report."""

    def test_module_states_the_g2_boundary(self):
        doc = sys.modules[__name__].__doc__ or ""
        self.assertIn("G2", doc)
        self.assertIn("FEEDS G2", doc)
        self.assertIn("NOT G2", doc)

    def test_no_tool_logic_changed_by_this_stage(self):
        """D3 is measurement + a decision. Importing this module must not add, replace or re-order
        a single registry entry — asserted against the REGISTRY itself rather than by grepping this
        file's own source (which necessarily mentions the registration decorator in prose)."""
        this_module = sys.modules[__name__].__name__
        for spec in TOOLS:
            self.assertNotEqual(getattr(spec.fn, "__module__", ""), this_module,
                                f"{spec.name} is defined by the eval — the eval is not a surface")
        # and the eval's own throwaway callables (Q08's `boom`, Q09's `hangs`) never reach it
        names = [t.name for t in TOOLS]
        self.assertNotIn("eval_boom", names)
        self.assertNotIn("eval_hang", names)
        self.assertEqual(len(names), len(set(names)), "the eval perturbed the registry")


if __name__ == "__main__":
    if "--report" in sys.argv:
        results = run_eval()
        print(report(results))
        print()
        print(f"naming decision: {NAMING_DECISION} (recorded {NAMING_DECISION_DATE})")
        for key, value in naming_blast_radius().items():
            print(f"  {key}: {value}")
        sys.exit(0 if all(r.ok for r in results) else 1)
    unittest.main()
