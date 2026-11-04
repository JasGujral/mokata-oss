"""G3-EVIDENCE (0.0.27) — the PR check shows its evidence and the run's committed ledger.

Business rules (rule note v2, `G3-EVIDENCE.rule.md`):
  * A CI checkout has no `.mokata/temp_local/`. A run's ledger rows and gate evidence reach the PR
    check only through a COMMITTED run record (`.mokata/runs/<run>/`), written by ONE human-gated
    writer that shows every byte it writes, keeps only allowlisted fields, redacts, folds home paths
    to `~`, and BLOCKS on a secret (naming the row, the field and the way out).
  * The check says which state the records are in — not opted in, none in this PR, not attributable,
    unreadable, inconsistent — and never reads as "no evidence found" when it did not look.
  * A committed record is the PR author's claim: marked `[committed, unverified]` everywhere, and it
    never changes the verdict or the exit code.
  * Untrusted text renders inert (no markdown, HTML, mention or autolink escapes a code span or a
    fence); a URL is never a link; a path is linked only when safe, inside the repo and present.
  * Size: GitHub's comment (65,536 chars) and step-summary (1 MiB) limits are honoured by keeping
    failing sections first and leaving sections out WHOLE, never dropping the verdict, and
    disclosing what was left out by counts plus a pointer — never a silent or partial cut.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

import _support  # noqa: F401

from mokata import ci_evidence as CE
from mokata import run_record as RR
from mokata.ci_check import CICheckResult, CheckLeg, run_ci_check
from mokata.govern.evidence import (Artifact, GateEvidence, TestedCheck, record_evidence,
                                    safe_run_dirname)
from mokata.govern.ledger import AuditLedger
from mokata.init import init_repo

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "mokata")
RUN = "Run-G3"
PAYLOAD = "x`y``@evil/team #99123 </details><img src=q onerror=alert(1)> [z](javascript:alert(1))"
HOSTILE_MARKERS = ("@evil", "99123", "onerror", "javascript:", "</details><img")


def fake_secret() -> str:
    """The AWS-docs example key, assembled at runtime (the secret-guard hook blocks the literal)."""
    return "AKIA" + "IOSFODNN7" + "EXAMPLE"


def fake_pem() -> str:
    return ("-----BEGIN " + "RSA PRIVATE KEY-----\nMIIEow" + "IBAAKCAQEA1x2y3z\nabcdefgh\n"
            "-----END " + "RSA PRIVATE KEY-----")


def _write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class _Repo:
    def __init__(self):
        self._d = tempfile.TemporaryDirectory()
        self.path = os.path.realpath(self._d.name)
        init_repo(root=self.path, profile="standard", assume_yes=True, out=lambda *_a: None)
        self.mokata = os.path.join(self.path, ".mokata")

    def close(self):
        self._d.cleanup()

    @property
    def ledger(self):
        return AuditLedger.from_mokata_dir(self.mokata)

    def evidence_root(self):
        return os.path.join(self.mokata, "temp_local", "evidence")

    def seed_run(self, run=RUN, **extra):
        """A run on this machine: an approval, a write, a gate verdict, a LOWERED gate, a phase."""
        led = self.ledger
        led.record("write_approval", decision="approved", proposal="p1", tool="spec_emit",
                   target=".mokata/specs/x.json", actor="human", run=run,
                   reason="free text that must not be exported")
        led.record("write_gate", write_kind="code", target=f"{os.path.expanduser('~')}/proj/a.py",
                   decision="approved", actor="agent", run=run, prompt="the user's prompt")
        led.record("gate", gate="spec-persisted", phase="develop", decision="passed", run=run,
                   reason="emitted spec present")
        led.record("enforcement_change", subject="completeness", item_id="completeness",
                   direction="demote", old="hard", new="advisory", actor="human", run=run)
        led.record("phase", phase="completeness_gate", ok=True, summary="summary text", run=run)
        led.record("write_gate", write_kind="code", target="b.py", decision="approved",
                   actor="agent")                                     # no run: unattributed
        for k, v in extra.items():
            led.record("write_gate", write_kind="code", target=v, decision="approved",
                       actor="agent", run=run, **{"tag": k})
        ev = GateEvidence("completeness", "pass", "hard",
                          artifacts=[Artifact("path", "tests/test_a.py:3", "test_a"),
                                     Artifact("content", "tests/test_a.py::test_a", "name")],
                          tested=[TestedCheck("ac-maps-to-test:AC-1", "pass", ("AC-1",))])
        record_evidence(self.evidence_root(), run, ev)


def _commit(repo, run=RUN, **kw):
    out = []
    res = RR.commit_run_record(repo.path, run, assume_yes=True, ledger=repo.ledger,
                               out=out.append, **kw)
    return res, "\n".join(out)


def _record_dir(repo, run=RUN):
    return os.path.join(RR.runs_dir(repo.path), RR.record_dirname(run))


def _rel(repo, run=RUN):
    return f".mokata/runs/{RR.record_dirname(run)}/"


def _write_record(root, name, *, rows=(), evidence=(), meta=None):
    d = os.path.join(root, ".mokata", "runs", name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "ledger.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    with open(os.path.join(d, "evidence.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        for e in evidence:
            fh.write(json.dumps(e) + "\n")
    m = {"run_id": name, "head_sha": "a" * 40, "rows_exported": len(rows),
         "evidence_exported": len(evidence), "rows_unattributed": 0, "chain_tip": ""}
    m.update(meta or {})
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(m, fh)
    return f".mokata/runs/{name}/ledger.jsonl"


def _outside_code(md):
    """The markdown text that is NOT inside a fenced block or an inline code span (CommonMark:
    a fence closes on a line of >= as many backticks; a span closes on a run of EXACTLY the
    opening length)."""
    out, fence_len = [], 0
    for line in md.split("\n"):
        stripped = line.strip()
        m = re.match(r"^(`{3,})\s*$", stripped)
        if fence_len:
            if m and len(m.group(1)) >= fence_len:
                fence_len = 0
            continue
        if m or re.match(r"^`{3,}", stripped):
            fence_len = len(re.match(r"^(`+)", stripped).group(1))
            continue
        i, keep = 0, []
        while i < len(line):
            if line[i] == "`":
                j = i
                while j < len(line) and line[j] == "`":
                    j += 1
                n = j - i
                k = j
                close = -1
                while k < len(line):
                    if line[k] == "`":
                        e = k
                        while e < len(line) and line[e] == "`":
                            e += 1
                        if e - k == n:
                            close = e
                            break
                        k = e
                    else:
                        k += 1
                if close == -1:
                    keep.append(line[i:j])
                    i = j
                else:
                    i = close
            else:
                keep.append(line[i])
                i += 1
        out.append("".join(keep))
    return "\n".join(out)


def _ci_result(root, *, legs=None, records=None):
    res = CICheckResult(legs=legs if legs is not None else
                        [CheckLeg("completeness", "pass", "all ACs map to tests")],
                        initialized=True, records=records, root=root)
    return res


# ===================================================================== AC-1 / AC-2 / AC-3: states
class TestTheRecordStateIsAlwaysSaid(unittest.TestCase):

    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)

    def test_AC1_no_runs_dir_says_not_opted_in_and_never_no_evidence_found(self):
        res = run_ci_check(self.repo.path, ["src/a.py"])
        body = res.comment_body()
        self.assertEqual(res.records.state, RR.NOT_OPTED_IN)
        self.assertIn("not opted in", body)
        self.assertIn("mokata evidence commit", body)
        self.assertNotIn("no evidence found", body.lower())
        self.assertEqual(res.overall, "NOT CHECKED")   # SPEC-TRISTATE: no specs, nothing graded

    def test_AC2_an_untouched_record_is_none_in_pr_with_its_count(self):
        _write_record(self.repo.path, "other")
        res = run_ci_check(self.repo.path, ["src/a.py"])
        self.assertEqual(res.records.state, RR.NONE_IN_PR)
        self.assertEqual(res.records.untouched, 1)
        self.assertIn("none in this PR", res.comment_body())
        self.assertIn("1 record(s) in the repo", res.comment_body())

    def test_AC2_a_record_the_pr_only_deleted_is_none_in_pr_not_a_crash(self):
        _write_record(self.repo.path, "kept")
        res = run_ci_check(self.repo.path, [".mokata/runs/gone/ledger.jsonl"])
        self.assertEqual(res.records.state, RR.NONE_IN_PR)

    def test_AC2_no_changed_file_list_is_not_attributable(self):
        _write_record(self.repo.path, "r")
        res = run_ci_check(self.repo.path, [])
        self.assertEqual(res.records.state, RR.NO_FILE_LIST)
        self.assertIn("not attributable", res.comment_body())

    def test_AC2_the_four_reader_states_are_distinct(self):
        self.assertEqual(len({RR.NOT_OPTED_IN, RR.NO_FILE_LIST, RR.NONE_IN_PR, RR.PRESENT}), 4)
        self.assertEqual(len({RR.OK, RR.UNREADABLE, RR.INCONSISTENT}), 3)
        texts = set()
        for st in (RR.NOT_OPTED_IN, RR.NO_FILE_LIST, RR.NONE_IN_PR):
            texts.add(CE.records_status(RR.RunRecords(st))[0])
        self.assertEqual(len(texts), 3)

    def test_AC3_one_bad_line_makes_the_whole_record_unreadable_and_degraded(self):
        changed = _write_record(self.repo.path, "bad", rows=[{"seq": 1, "kind": "gate"}],
                                evidence=[{"gate_id": "g", "verdict": "pass"}])
        with open(os.path.join(self.repo.path, changed), "a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        res = run_ci_check(self.repo.path, [changed])
        rec = res.records.records[0]
        self.assertEqual(rec.state, RR.UNREADABLE)
        self.assertEqual((rec.rows, rec.evidence), ([], []))
        self.assertTrue(res.degraded)
        self.assertEqual(res.exit_code, 0)
        body = res.comment_body()
        self.assertIn("UNREADABLE", body)
        self.assertNotIn("Nothing for mokata to flag", body)
        self.assertNotIn("verdict ` pass `", body)

    def test_AC3_a_row_count_that_disagrees_with_meta_is_inconsistent(self):
        changed = _write_record(self.repo.path, "cut", rows=[{"seq": 1, "kind": "gate"}],
                                meta={"rows_exported": 2})
        res = run_ci_check(self.repo.path, [changed])
        self.assertEqual(res.records.records[0].state, RR.INCONSISTENT)
        self.assertTrue(res.degraded)
        self.assertIn("INCONSISTENT", res.comment_body())
        self.assertNotIn("Nothing for mokata to flag", res.comment_body())

    def test_AC3_a_hand_deleted_evidence_record_is_inconsistent(self):
        # Round 3 survivor (a): meta vouches for 2 evidence records, one was deleted by hand. The
        # ledger half agrees, so only the `evidence_exported` comparison can catch it.
        changed = _write_record(self.repo.path, "cut",
                                rows=[{"seq": 1, "kind": "gate"}],
                                evidence=[{"gate_id": "g", "verdict": "pass"}],
                                meta={"evidence_exported": 2})
        res = run_ci_check(self.repo.path, [changed])
        self.assertEqual(res.records.records[0].state, RR.INCONSISTENT)
        self.assertTrue(res.degraded)
        self.assertIn("INCONSISTENT", res.comment_body())


# ===================================================================== AC-4: never grades
class TestACommittedRecordNeverGrades(unittest.TestCase):

    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)

    def test_AC4_all_pass_committed_rows_do_not_unblock_a_blocked_leg(self):
        changed = _write_record(self.repo.path, "r", rows=[
            {"seq": 1, "kind": "gate", "decision": "passed", "run": "r"}],
            evidence=[{"gate_id": "completeness", "verdict": "pass", "tested": [], "artifacts": []}])
        records = RR.load_for_pr(self.repo.path, [changed])
        res = _ci_result(self.repo.path, legs=[CheckLeg("completeness", "block", "AC-2 unmapped")],
                         records=records)
        self.assertTrue(res.blocked)
        self.assertEqual(res.exit_code, 1)
        self.assertEqual(res.overall, "BLOCKED")

    def test_AC4_a_committed_blocked_row_does_not_block(self):
        changed = _write_record(self.repo.path, "r", rows=[
            {"seq": 1, "kind": "write_gate", "decision": "blocked", "run": "r"}],
            evidence=[{"gate_id": "completeness", "verdict": "block", "tested": [],
                       "artifacts": []}])
        res = run_ci_check(self.repo.path, [changed])
        self.assertFalse(res.blocked)
        self.assertEqual(res.exit_code, 0)

    def test_AC4_every_committed_section_and_row_carries_the_unverified_mark(self):
        changed = _write_record(self.repo.path, "r", rows=[
            {"seq": 1, "kind": "write_gate", "decision": "approved", "run": "r"},
            {"seq": 2, "kind": "gate", "decision": "passed", "run": "r"}],
            evidence=[{"gate_id": "completeness", "verdict": "pass",
                       "tested": [{"check_id": "c", "status": "pass", "ac_ids": ["AC-1"]}],
                       "artifacts": [{"type": "url", "ref": "https://e.x/a", "label": ""}]}])
        body = run_ci_check(self.repo.path, [changed]).comment_body()
        ev = body.split("### Evidence", 1)[1]
        committed_lines = [ln for ln in ev.split("\n")
                           if ln.startswith(("- ", "#### ")) and "This check" not in ln]
        self.assertTrue(committed_lines)
        for ln in committed_lines:
            self.assertIn(RR_MARK, ln, ln)

    def test_AC4_the_record_says_where_it_was_recorded_and_where_this_head_is(self):
        changed = _write_record(self.repo.path, "r", rows=[{"seq": 1, "kind": "gate", "run": "r"}],
                                meta={"head_sha": "b" * 40})
        res = run_ci_check(self.repo.path, [changed])
        body = res.comment_body(CE.LinkContext(None, "c" * 40))
        self.assertIn("recorded at ` " + "b" * 40 + " `; this head is ` " + "c" * 40 + " `", body)


RR_MARK = "[committed, unverified]"


# ===================================================================== AC-5: run= everywhere
EXPORTED = set(RR.EXPORTED_KINDS)


def _record_calls_without_run():
    """Every `<x>.record(<exported kind>, ...)` call in src/ that passes no `run=`."""
    missing = []
    # CORPUS: THE WORKING TREE — every record call in the src/ that ships (sync-public rsyncs the
    # working tree), including a file not yet tracked, must carry its run.
    for dirpath, _dirs, files in os.walk(SRC):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(dirpath, f)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            consts = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                        and isinstance(node.value.value, str):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            consts[t.id] = node.value.value
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "record" and node.args):
                    continue
                a = node.args[0]
                kind = a.value if isinstance(a, ast.Constant) else (
                    consts.get(a.id) if isinstance(a, ast.Name) else None)
                if kind == "gate_advisory" or (isinstance(a, ast.Name)
                                               and a.id == "GATE_ADVISORY_KIND"):
                    kind = "gate_advisory"
                if kind in EXPORTED and not any(k.arg == "run" for k in node.keywords) \
                        and not any(k.arg is None for k in node.keywords):
                    missing.append(f"{os.path.relpath(path, SRC)}:{node.lineno} {kind}")
    return missing


class TestEveryExportedRowCarriesItsRun(unittest.TestCase):

    def test_AC5_every_record_site_of_an_exported_kind_passes_run(self):
        self.assertEqual([], _record_calls_without_run())

    def test_AC5_the_advisory_gate_row_carries_the_current_run(self):
        from mokata.govern.enforce import decide_gate
        from mokata.session import current_run_id
        repo = _Repo()
        self.addCleanup(repo.close)
        gate = types.SimpleNamespace(id="g-advisory", enforcement="advisory", description="")
        decide_gate(gate, False, reason="r", ledger=repo.ledger)
        rows = [e for e in repo.ledger.entries() if e.get("kind") == "gate_advisory"]
        self.assertTrue(rows)
        self.assertEqual(rows[-1]["run"], current_run_id())

    def test_AC5_the_spec_gate_row_carries_the_run_it_was_given(self):
        from mokata.engine.spec_gate import check_spec_persisted
        repo = _Repo()
        self.addCleanup(repo.close)
        check_spec_persisted(None, ledger=repo.ledger, phase="develop", run_id="run-7")
        row = [e for e in repo.ledger.entries() if e.get("kind") == "gate"][-1]
        self.assertEqual(row["run"], "run-7")

    def test_AC5_a_lowered_gate_is_exported_and_unattributed_rows_are_only_counted(self):
        repo = _Repo()
        self.addCleanup(repo.close)
        repo.seed_run()
        plan = RR.plan_run_record(repo.path, repo.mokata, RUN, head_sha="")
        rows = [json.loads(x) for x in plan.files["ledger.jsonl"].splitlines()]
        kinds = [r["kind"] for r in rows]
        self.assertIn("enforcement_change", kinds)
        self.assertEqual({r["run"] for r in rows}, {RUN})
        self.assertNotIn("b.py", plan.files["ledger.jsonl"])
        self.assertGreaterEqual(plan.rows_unattributed, 1)
        self.assertEqual(json.loads(plan.files["meta.json"])["rows_unattributed"],
                         plan.rows_unattributed)


# ===================================================================== AC-6 / AC-7: links
class TestLinksAreSafeOrWithheld(unittest.TestCase):

    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)
        _write(self.repo.path, "src/a b.py", "x = 1\n")
        _write(self.repo.path, ".env", "X=1\n")
        self.link = CE.LinkContext("https://github.com/o/r", "d" * 40)

    def _link(self, ref, link=None):
        return CE.path_link(self.repo.path, ref, link or self.link)

    def test_AC6_hostile_paths_are_withheld_with_a_reason_and_never_linked(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        hostile = ["../x", "/etc/passwd", "C:\\x", "\\\\h\\s", "missing.py", ".env",
                   "src/a b.py:abc", "src/a b.py:12345678"]
        if hasattr(os, "symlink"):
            try:
                os.symlink(outside, os.path.join(self.repo.path, "out"))
                hostile.append("out")
            except OSError:
                pass
        for ref in hostile:
            md = self._link(ref)
            self.assertTrue(md.startswith("path withheld ("), (ref, md))
            self.assertNotIn("](", md, ref)
        self.assertTrue(self._link(".env").startswith("path withheld (sensitive path"))
        if "out" in hostile:
            self.assertTrue(self._link("out").startswith("path withheld (resolves outside"))

    def test_AC6_a_safe_path_links_percent_encoded_with_its_line(self):
        md = self._link("src/a b.py:7")
        self.assertIn("](https://github.com/o/r/blob/" + "d" * 40 + "/src/a%20b.py#L7)", md)

    def test_AC6_a_non_https_server_gives_no_links(self):
        link = CE.LinkContext.from_env({"GITHUB_SERVER_URL": "http://ghe.local",
                                        "GITHUB_REPOSITORY": "o/r", "GITHUB_SHA": "e" * 40})
        self.assertIsNone(link.base)
        self.assertNotIn("](", self._link("src/a b.py:7", link))
        ok = CE.LinkContext.from_env({"GITHUB_SERVER_URL": "https://github.com",
                                      "GITHUB_REPOSITORY": "o/r", "GITHUB_SHA": "e" * 40})
        self.assertEqual(ok.base, "https://github.com/o/r")

    def test_AC7_non_https_urls_are_withheld_and_https_is_never_an_active_link(self):
        for bad in ("javascript:alert(1)", "data:text/html,x", "http://e.x"):
            self.assertTrue(CE.url_text(bad).startswith("url withheld"), bad)
        md = CE.url_text("https://e.x/a")
        self.assertNotIn("](", md)
        self.assertTrue(md.startswith("`"))


# ===================================================================== AC-8: injection
class TestUntrustedTextIsInert(unittest.TestCase):

    def test_AC8_a_hostile_payload_in_every_field_stays_inside_code_or_a_fence(self):
        repo = _Repo()
        self.addCleanup(repo.close)
        row = {k: PAYLOAD for k in ("kind", "decision", "target", "actor", "tool", "gate",
                                    "phase", "run", "subject", "proposal", "at")}
        row["seq"] = 1
        ev = {"gate_id": PAYLOAD, "verdict": PAYLOAD, "run_id": PAYLOAD,
              "tested": [{"check_id": PAYLOAD, "status": PAYLOAD, "ac_ids": [PAYLOAD]}],
              "artifacts": [{"type": "content", "ref": PAYLOAD + "\n```\n" + PAYLOAD,
                             "label": PAYLOAD},
                            {"type": "path", "ref": PAYLOAD, "label": PAYLOAD},
                            {"type": "url", "ref": PAYLOAD, "label": PAYLOAD},
                            {"type": PAYLOAD, "ref": PAYLOAD, "label": PAYLOAD}]}
        changed = _write_record(repo.path, "hostile", rows=[row], evidence=[ev],
                                meta={"head_sha": PAYLOAD})
        records = RR.load_for_pr(repo.path, [changed])
        res = _ci_result(repo.path, records=records)
        live = GateEvidence(PAYLOAD, "pass", artifacts=[Artifact("content", PAYLOAD, PAYLOAD)],
                            tested=[TestedCheck(PAYLOAD, "pass", (PAYLOAD,))])
        res.legs[0].evidence = [live]
        for body in (res.comment_body(CE.LinkContext("https://github.com/o/r", "f" * 40)),
                     res.evidence_markdown()):
            outside = _outside_code(body)
            for marker in HOSTILE_MARKERS:
                self.assertNotIn(marker, outside, marker)
            summaries = re.findall(r"<summary>(.*?)</summary>", body)
            self.assertTrue(summaries)
            for s in summaries:
                self.assertRegex(s, r"^Evidence: [a-z]+ #\d+$")

    def test_AC8_a_code_span_cannot_be_closed_by_its_content(self):
        for value in ("a`b", "``", "`lead", "trail`", "x" + "`" * 5 + "y"):
            span = CE.code_span(value)
            tick = re.match(r"^(`+)", span).group(1)
            self.assertGreater(len(tick), max(len(r) for r in re.findall(r"`+", value)))
            self.assertTrue(span.endswith(tick))

    def test_AC8_a_fence_is_longer_than_any_backtick_run_in_the_content(self):
        open_f, close_f = CE.fence("a ``` b ```` c")
        self.assertEqual(len(open_f), 5)
        self.assertEqual(open_f, close_f)


class TestCommittedSpecTextInTheVerdictsIsInert(unittest.TestCase):
    """The verdict section (L9's AC -> test map) carries COMMITTED-spec text: spec titles, AC text,
    test names. The PR author wrote them, so they render under the same code-span rule."""

    TITLE = "calc @org/team #123 `x`` </details><b>hi</b>"

    def test_a_hostile_committed_spec_title_cannot_ping_link_or_close_details(self):
        import test_l9_g3_fresh_repo as L9
        repo = L9._FreshRepo()
        self.addCleanup(repo.__exit__, None, None, None)
        spec = dict(L9.SPEC, title=self.TITLE)
        L9._write(repo.path, "spec.json", json.dumps(spec))
        repo.enable_commit()
        L9._write(repo.path, "tests/test_calc.py", L9.ONE_TEST)
        code, out = repo.emit_from_inside()
        self.assertEqual(code, 0, out)
        repo.as_ci_checkout()
        res = run_ci_check(repo.path, ["src/calc.py", "tests/test_calc.py"])
        body = res.comment_body()
        self.assertIn("@org/team", body)                    # it IS shown...
        outside = _outside_code(body)
        for marker in ("@org/team", "#123", "</details><b>", "<b>hi"):
            self.assertNotIn(marker, outside, marker)       # ...but only inside code


# ===================================================================== AC-9: secrets
class TestSecretsAreRedactedAndBlock(unittest.TestCase):

    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)

    def test_AC9_secrets_in_content_and_in_a_json_escaped_field_are_redacted_on_render(self):
        changed = _write_record(self.repo.path, "s", rows=[
            {"seq": 1, "kind": "write_gate", "target": "x\n" + fake_secret(), "run": "s"}],
            evidence=[{"gate_id": "completeness", "verdict": "pass", "tested": [],
                       "artifacts": [{"type": "content", "ref": fake_pem(), "label": "k"},
                                     {"type": "content", "ref": "k=" + fake_secret(),
                                      "label": "aws"}]}])
        res = run_ci_check(self.repo.path, [changed])
        for body in (res.comment_body(), res.evidence_markdown()):
            self.assertNotIn(fake_secret(), body)
            self.assertNotIn("MIIEow", body)
            self.assertIn("redacted by mokata's secret scan", body)

    def test_AC9_a_secret_in_an_exported_field_blocks_the_commit_and_names_its_recovery(self):
        self.repo.seed_run(leak="https://x/" + fake_secret())
        res, out = _commit(self.repo)
        self.assertFalse(res.committed)
        self.assertTrue(res.blocked)
        self.assertFalse(os.path.exists(_record_dir(self.repo)))
        self.assertIn("field `target`", out)
        self.assertIn("seq=", out)
        self.assertIn("mokata secret ignore", out)
        self.assertIn(f"mokata evidence commit --run {RUN}", out)
        self.assertNotIn(fake_secret(), out)


class TestHomePathsFoldOnEveryOS(unittest.TestCase):

    def test_a_windows_home_folds_to_tilde(self):
        # Round 3 survivor (b): a record committed from a Windows machine names its user.
        self.assertEqual(RR.fold_home("C:\\Users\\bob\\proj\\a.py"), "~\\proj\\a.py")
        self.assertEqual(RR.fold_home("see d:\\users\\Ann Lee"), "see ~ Lee")
        self.assertNotIn("bob", RR.fold_home("at C:\\Users\\bob"))

    def test_posix_homes_still_fold(self):
        self.assertEqual(RR.fold_home("/Users/ann/x"), "~/x")
        self.assertEqual(RR.fold_home("/home/ann/x"), "~/x")


# ===================================================================== AC-10 / AC-11: size
def _big_records(root, n, *, failing=0, artifact_size=300):
    ev = []
    for i in range(n):
        ev.append({"gate_id": f"g{i}", "verdict": "block" if i < failing else "pass",
                   "tested": [{"check_id": f"c{i}", "status": "pass", "ac_ids": ["AC-1"]}],
                   "artifacts": [{"type": "content", "ref": "y" * artifact_size,
                                  "label": f"l{i}"}]})
    changed = _write_record(root, "big", rows=[], evidence=ev)
    return RR.load_for_pr(root, [changed])


class TestSizeIsDisclosedNeverSilentlyCut(unittest.TestCase):

    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)

    def _disclosure_lines(self, body):
        if "#### Left out of this output" not in body:
            return []
        return body.split("#### Left out of this output", 1)[1].strip().split("\n")

    def test_AC10_the_comment_fits_keeps_the_verdict_and_failing_sections_first(self):
        records = _big_records(self.repo.path, 400, failing=3)
        res = _ci_result(self.repo.path, records=records)
        body = res.comment_body()
        self.assertLessEqual(len(body), CE.GITHUB_COMMENT_MAX)
        self.assertIn("mokata PR check — **PASSED**", body)
        self.assertIn("#### Left out of this output", body)
        for i in range(3):                                      # every failing section kept
            self.assertIn(f"gate ` g{i} ` · verdict ` block `", body)
        kept = re.findall(r"gate ` (g\d+) `", body)
        self.assertEqual(kept[:3], ["g0", "g1", "g2"])
        self.assertIn("passing", "\n".join(self._disclosure_lines(body)))
        self.assertIn("Where to find them:", body)

    def test_AC10_the_disclosure_is_a_fixed_size_whatever_is_left_out(self):
        small = self._disclosure_lines(_ci_result(
            self.repo.path, records=_big_records(self.repo.path, 230)).comment_body())
        shutil.rmtree(os.path.join(self.repo.path, ".mokata", "runs"))
        large = self._disclosure_lines(_ci_result(
            self.repo.path, records=_big_records(self.repo.path, 10000,
                                                 artifact_size=10)).comment_body())
        self.assertTrue(small and large)
        self.assertEqual(len(small), len(large))

    def test_AC10_no_section_appears_partially(self):
        res = _ci_result(self.repo.path, records=_big_records(self.repo.path, 400))
        body = res.comment_body()
        self.assertEqual(body.count("<details>"), body.count("</details>"))
        opens = body.count("<summary>")
        self.assertEqual(opens, body.count("</summary>"))
        # every committed section ends with its record line, so a cut section is visible
        self.assertEqual(body.count("#### ` [committed, unverified] `"),
                         body.count("— recorded at "))
        self.assertTrue(body.rstrip().split("\n")[-1].startswith("Where to find them:"))

    def test_AC10_a_verdict_section_too_big_to_fit_falls_back_to_counts(self):
        detail = [f"spec: AC-{i} -> (no test)" for i in range(6000)]
        res = _ci_result(self.repo.path, legs=[CheckLeg("completeness", "block", "unmapped",
                                                        detail=detail)],
                         records=RR.RunRecords(RR.NOT_OPTED_IN))
        body = res.comment_body()
        self.assertLessEqual(len(body), CE.GITHUB_COMMENT_MAX)
        self.assertIn("mokata PR check — **BLOCKED**", body)
        self.assertIn("summarized by counts", body)
        self.assertIn("completeness: **BLOCK** — 6000 detail line(s)", body)
        self.assertIn("more", body)
        self.assertIn("not opted in", body)

    def test_AC11_the_evidence_file_follows_the_same_rule_under_one_mebibyte(self):
        res = _ci_result(self.repo.path, records=_big_records(self.repo.path, 4000,
                                                              artifact_size=400))
        md = res.evidence_markdown()
        self.assertLessEqual(len(md.encode("utf-8")), CE.STEP_SUMMARY_MAX)
        self.assertIn("#### Left out of this output", md)
        self.assertIn("mokata PR check", md)


# ===================================================================== AC-12: the gated writer
class TestTheGatedWriter(unittest.TestCase):

    def setUp(self):
        self.repo = _Repo()
        self.addCleanup(self.repo.close)
        self.repo.seed_run()

    def test_AC12_declining_writes_nothing(self):
        out = []
        res = RR.commit_run_record(self.repo.path, RUN, confirm=lambda _q: False,
                                   ledger=self.repo.ledger, out=out.append)
        self.assertFalse(res.committed)
        self.assertFalse(os.path.exists(_record_dir(self.repo)))

    def test_AC12_the_preview_bytes_are_the_committed_bytes(self):
        res, out = _commit(self.repo)
        self.assertTrue(res.committed, out)
        for name in RR.RECORD_FILES:
            with open(os.path.join(_record_dir(self.repo), name), encoding="utf-8") as fh:
                written = fh.read()
            self.assertEqual(written, res.plan.files[name])
            self.assertIn(written, out)

    def test_AC12_only_allowlisted_fields_and_home_paths_folded(self):
        res, _out = _commit(self.repo)
        text = res.plan.files["ledger.jsonl"]
        for leaked in ("free text that must not be exported", "the user's prompt",
                       "summary text", "emitted spec present"):
            self.assertNotIn(leaked, text)
        self.assertIn('"~/proj/a.py"', text)
        self.assertNotIn(os.path.expanduser("~") + "/", text)
        for row in map(json.loads, text.splitlines()):
            self.assertLessEqual(set(row), set(RR.FIELDS[row["kind"]]))

    def test_AC12_another_users_home_is_folded_and_a_still_absolute_path_is_dropped(self):
        self.repo.seed_run(other="/Users/someone/x.py", root_abs="/etc/passwd")
        plan = RR.plan_run_record(self.repo.path, self.repo.mokata, RUN, head_sha="")
        self.assertIn('"~/x.py"', plan.files["ledger.jsonl"])
        self.assertNotIn("/etc/passwd", plan.files["ledger.jsonl"])
        self.assertEqual(plan.dropped_absolute, 1)
        self.assertIn("1 absolute path value(s)", plan.preview())

    def test_AC12_cli_evidence_commit_writes_the_record(self):
        from mokata import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main(["evidence", "commit", "--path", self.repo.path, "--run", RUN,
                             "--yes"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertTrue(os.path.isfile(os.path.join(_record_dir(self.repo), "meta.json")))

    def test_AC11_cli_ci_check_writes_the_evidence_file_with_links_from_the_environment(self):
        from mokata import cli
        res, _ = _commit(self.repo)
        self.assertTrue(res.committed)
        _write(self.repo.path, "tests/test_a.py", "x = 1\n")
        dest = os.path.join(self.repo.path, "ev.md")
        env = {"GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "o/r",
               "GITHUB_SHA": "a" * 40}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
            code = cli.main(["ci-check", "--path", self.repo.path, "--no-fail",
                             "--files", ",".join(_rel(self.repo) + n for n in RR.RECORD_FILES),
                             "--evidence-file", dest])
        self.assertEqual(code, 0, out.getvalue())
        with open(dest, encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn("Committed ledger", md)
        self.assertIn("](https://github.com/o/r/blob/" + "a" * 40 + "/tests/test_a.py#L3)", md)

    def test_AC12_the_mcp_twin_proposes_the_exact_bytes_then_commits_once_approved(self):
        from mokata.mcp.tools_evidence import evidence_commit
        proposed = evidence_commit(path=self.repo.path, run=RUN)
        self.assertEqual(proposed["status"], "proposed")
        self.assertIn("ledger.jsonl", proposed["preview"])
        self.assertFalse(os.path.exists(_record_dir(self.repo)))
        done = _support.mcp_commit(evidence_commit, path=self.repo.path, run=RUN)
        self.assertTrue(done["committed"], done)
        with open(os.path.join(_record_dir(self.repo), "ledger.jsonl"), encoding="utf-8") as fh:
            self.assertIn(fh.read(), proposed["preview"])

    def test_AC12_mcp_commit_writes_every_previewed_byte_even_when_the_clock_moved(self):
        # Round 3 M1: the approval binds the EXACT bytes — meta.json's `exported_at` included. The
        # clock moves between the propose and the redeem; the committed files are still the preview.
        from mokata import approval
        from mokata.mcp.tools_evidence import evidence_commit
        from datetime import datetime as _dt, timezone as _tz

        class _Later(_dt):
            @classmethod
            def now(cls, tz=None):
                return _dt(2031, 1, 2, 3, 4, 5, tzinfo=_tz.utc)

        proposed = evidence_commit(path=self.repo.path, run=RUN)
        pid = proposed["proposal_id"]
        approval.approve(self.repo.path, pid, actor="test-human", ledger=self.repo.ledger)
        with mock.patch.object(RR, "datetime", _Later):
            done = evidence_commit(path=self.repo.path, run=RUN, proposal_id=pid)
        self.assertTrue(done["committed"], done)
        rel = _rel(self.repo)
        for name in RR.RECORD_FILES:
            with open(os.path.join(_record_dir(self.repo), name), encoding="utf-8",
                      newline="") as fh:
                body = fh.read()
            header = f"----- {rel}{name} ({len(body)} chars)\n"
            self.assertIn(header + body, proposed["preview"], name)
        with open(os.path.join(_record_dir(self.repo), "meta.json"), encoding="utf-8") as fh:
            self.assertNotIn("2031", fh.read())

    def test_AC12_mcp_redeem_refuses_when_the_source_changed_since_the_preview(self):
        from mokata import approval
        from mokata.mcp.tools_evidence import evidence_commit
        proposed = evidence_commit(path=self.repo.path, run=RUN)
        pid = proposed["proposal_id"]
        approval.approve(self.repo.path, pid, actor="test-human", ledger=self.repo.ledger)
        self.repo.ledger.record("phase", phase="develop", ok=True, run=RUN)   # source moves
        done = evidence_commit(path=self.repo.path, run=RUN, proposal_id=pid)
        self.assertFalse(done["committed"], done)
        self.assertIn("changed since the preview", done["message"])
        self.assertFalse(os.path.exists(_record_dir(self.repo)))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_M2_a_runs_dir_linked_out_of_the_repo_is_never_written_through(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        planted = os.path.join(outside, RR.record_dirname(RUN))
        os.makedirs(planted)
        with open(os.path.join(planted, "meta.json"), "w", encoding="utf-8") as fh:
            fh.write("ORIGINAL")
        try:
            os.symlink(outside, os.path.join(self.repo.mokata, "runs"))
        except OSError:
            self.skipTest("cannot create symlinks here")
        res, out = _commit(self.repo)
        self.assertFalse(res.committed, out)
        self.assertIn("refused", out)
        self.assertNotIn("-----", out, "a preview of an unwritable path was shown for approval")
        self.assertEqual(sorted(os.listdir(planted)), ["meta.json"])
        with open(os.path.join(planted, "meta.json"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "ORIGINAL")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_M2_a_record_dir_linked_out_of_the_repo_is_never_written_through(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        with open(os.path.join(outside, "ledger.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("ORIGINAL")
        os.makedirs(RR.runs_dir(self.repo.path), exist_ok=True)
        try:
            os.symlink(outside, _record_dir(self.repo))
        except OSError:
            self.skipTest("cannot create symlinks here")
        res, out = _commit(self.repo)
        self.assertFalse(res.committed, out)
        self.assertEqual(os.listdir(outside), ["ledger.jsonl"])
        with open(os.path.join(outside, "ledger.jsonl"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "ORIGINAL")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_M2_a_link_swapped_in_after_the_preview_is_refused_at_the_write(self):
        # The re-check runs inside the commit closure, right before the bytes land.
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)

        def _swap(_prompt):
            os.makedirs(self.repo.mokata, exist_ok=True)
            os.symlink(outside, os.path.join(self.repo.mokata, "runs"))
            return True
        out = []
        res = RR.commit_run_record(self.repo.path, RUN, ledger=self.repo.ledger, confirm=_swap,
                                   out=out.append)
        self.assertFalse(res.committed, out)
        self.assertEqual(os.listdir(outside), [])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_M2_a_record_FILE_link_swapped_in_after_the_preview_is_refused_at_the_write(self):
        # The closure's per-file re-check: a meta.json link planted after the preview (inside
        # confirm) is REFUSED, never silently replaced by the rename.
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        victim = os.path.join(outside, "victim")
        with open(victim, "w", encoding="utf-8") as fh:
            fh.write("ORIGINAL")

        def _swap(_prompt):
            os.makedirs(_record_dir(self.repo), exist_ok=True)
            os.symlink(victim, os.path.join(_record_dir(self.repo), "meta.json"))
            return True
        out = []
        res = RR.commit_run_record(self.repo.path, RUN, ledger=self.repo.ledger, confirm=_swap,
                                   out=out.append)
        self.assertFalse(res.committed, out)
        self.assertIn("meta.json", "\n".join(out[-1:]))
        self.assertTrue(os.path.islink(os.path.join(_record_dir(self.repo), "meta.json")))
        with open(victim, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "ORIGINAL")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_M2_a_record_file_that_is_a_link_is_refused_not_replaced(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        victim = os.path.join(outside, "victim")
        with open(victim, "w", encoding="utf-8") as fh:
            fh.write("ORIGINAL")
        os.makedirs(_record_dir(self.repo))
        try:
            os.symlink(victim, os.path.join(_record_dir(self.repo), "meta.json"))
        except OSError:
            self.skipTest("cannot create symlinks here")
        res, out = _commit(self.repo)
        self.assertFalse(res.committed, out)
        self.assertIn("meta.json", out)
        with open(victim, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "ORIGINAL")

    def test_AC12_a_round_trip_record_renders_in_the_pr_check(self):
        res, _ = _commit(self.repo)
        self.assertTrue(res.committed)
        changed = [_rel(self.repo) + n for n in RR.RECORD_FILES]
        shutil.rmtree(os.path.join(self.repo.mokata, "temp_local"))      # a CI checkout
        result = run_ci_check(self.repo.path, changed)
        self.assertEqual(result.records.records[0].state, RR.OK)
        body = result.comment_body()
        self.assertIn("Committed ledger", body)
        self.assertIn("enforcement_change", body)


# ===================================================================== AC-13 / AC-14 / AC-15
class TestPortableNamesSymlinksAndStreaming(unittest.TestCase):

    def test_AC13_reserved_and_trailing_dot_names_are_made_portable(self):
        reserved = {"con", "prn", "aux", "nul", "com1", "lpt1"}
        for rid in ("CON", "con", "NUL.txt", "com1", "a.", "a "):
            name = safe_run_dirname(rid)
            self.assertNotIn(name.split(".")[0].lower(), reserved, rid)
            self.assertFalse(name.endswith((".", " ")), rid)
        self.assertEqual(RR.record_dirname("Run-A"), RR.record_dirname("run-a"))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_AC14_symlinked_runs_dir_record_dir_and_record_file_are_refused(self):
        repo = _Repo()
        self.addCleanup(repo.close)
        elsewhere = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, elsewhere, True)
        _write_record(elsewhere, "real", rows=[{"seq": 1, "kind": "gate"}])
        runs = os.path.join(repo.path, ".mokata", "runs")
        try:
            os.symlink(os.path.join(elsewhere, ".mokata", "runs"), runs)
        except OSError:
            self.skipTest("cannot create symlinks here")
        recs = RR.load_for_pr(repo.path, [".mokata/runs/real/ledger.jsonl"])
        self.assertEqual(recs.records[0].state, RR.UNREADABLE)
        os.unlink(runs)
        os.makedirs(runs)
        os.symlink(os.path.join(elsewhere, ".mokata", "runs", "real"), os.path.join(runs, "lnk"))
        recs = RR.load_for_pr(repo.path, [".mokata/runs/lnk/ledger.jsonl"])
        self.assertEqual(recs.records[0].state, RR.UNREADABLE)
        _write_record(repo.path, "filelink", rows=[{"seq": 1, "kind": "gate"}])
        target = os.path.join(runs, "filelink", "ledger.jsonl")
        os.unlink(target)
        os.symlink(os.path.join(elsewhere, ".mokata", "runs", "real", "ledger.jsonl"), target)
        recs = RR.load_for_pr(repo.path, [".mokata/runs/filelink/ledger.jsonl"])
        self.assertEqual(recs.records[0].state, RR.UNREADABLE)

    def test_AC15_the_reader_streams_and_does_not_cap(self):
        repo = _Repo()
        self.addCleanup(repo.close)
        rows = [{"seq": i, "kind": "phase", "run": "s"} for i in range(10000)]
        changed = _write_record(repo.path, "s", rows=rows)
        reads = []
        real_open = open

        class _Spy:
            def __init__(self, fh, name):
                self.fh, self.name = fh, str(name)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.fh.close()

            def __iter__(self):
                return iter(self.fh)

            def read(self, *a):
                if self.name.endswith(".jsonl"):
                    reads.append(self.name)
                return self.fh.read(*a)

        with mock.patch.object(RR, "open", create=True,
                               side_effect=lambda *a, **k: _Spy(real_open(*a, **k), a[0])):
            recs = RR.load_for_pr(repo.path, [changed])
        self.assertEqual(len(recs.records[0].rows), 10000)
        self.assertEqual(reads, [], "a record file was read whole instead of line by line")


if __name__ == "__main__":
    unittest.main()
