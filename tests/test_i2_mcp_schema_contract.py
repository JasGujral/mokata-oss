"""0.0.28 lane I2 — the `tools/list` contract: what a model is told before its first call.

Measured on master (design note I §2d): 0 of 339 parameters carried a description, 0 `required`, 0
`enum`, 16 arrays typed `items: {}`, a `tools/list` of 143 KB, 40 descriptions quoting internal ids,
and `spec_emit`'s description truncated by Claude Code from 4897 to its 4096-character limit (Claude
Code's own MCP log). So `{}` was a valid call to every tool, and a model learned the real shape by
failing first. This module is the pure sweep of the schemas the server builds; the stdio half is
`test_i2_mcp_first_call` and `test_i1_mcp_strict_front_door`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import ast
import json
import os
import re
import unittest

import _support  # noqa: F401  (puts src/ on the path)
import _mcp_sdk

from mokata.mcp import server as MS  # noqa: E402
from mokata.mcp.registry import TOOLS  # noqa: E402

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "mokata"))

#: Claude Code truncates a tool description at this many characters (its own MCP log, 2026-11-05:
#: `Tool "spec_emit" description truncated from 4897 to 4096 chars`) — a client fact.
CLIENT_DESCRIPTION_LIMIT = 4096

#: The first sentence says what the tool does and when to call it, in plain words.
FIRST_SENTENCE_MAX = 160

#: The size of the `tools/list` RESULT as the server sends it on the wire: the UTF-8 bytes of the
#: `result` member of the real stdio reply (every tool's name, description, inputSchema,
#: outputSchema and annotations), measured by a round trip, not rebuilt from in-process fields. A
#: RATCHET pinned to the exact measured value, so it can only go down: any growth reds, and only a
#: shrink moves it. Round 3 (verifier L1): the earlier pin (106,450) counted four fields and missed
#: `outputSchema`, about 9 KB of the reply. Master was 143 KB; the 40 KB design target is NOT met.
TOOLS_LIST_CEILING = 115_453

#: Write tools that genuinely take no argument a call cannot do without (a share/clean/reset of the
#: whole repo); every other write tool has at least one `required` property.
WRITE_TOOLS_WITHOUT_REQUIRED = {
    "init": "the profile defaults to `standard`; initializing the repo needs nothing else",
    "reconfigure": "with nothing named it re-applies the current profile (an idempotent re-wire)",
    "reset": "resets this repo's mokata state as a whole; there is no narrower target to name",
    "export_stack": "exports the whole stack to its default file unless a destination is named",
    "memory_export": "exports all memory to its default file unless a destination is named",
    "audit_share": "shares the whole audit log; nothing narrower exists to name",
    "events_share": "shares the whole event log; nothing narrower exists to name",
    "spec_check": "checks the spec already on record for this run; it takes no input",
    "worktree_clean": "removes every stale worktree the plan finds; nothing to name",
    "worktree_merge": "merges this session's own worktree; the strategy defaults",
    "evidence_commit": "commits the current run's ledger and evidence; the run is implied",
    "spec_emit": ("title/criteria/tests/tasks are required unless `proposal_id` alone redeems an "
                  "approved proposal (0.0.28 round-3 ruling) — enforced in-body, named refusal"),
}

#: Parameters whose value is a closed set: each must be an `enum` in the schema (a `Literal`).
CLOSED_SETS = {
    "apply_proposal": {"decision"},
    "baseline": {"response_format"},
    "budget": {"response_format"},
    "coverage": {"response_format"},
    "decompose": {"response_format"},
    "doctor": {"response_format"},
    "init": {"profile"},
    "lanes": {"response_format"},
    "lat_check": {"response_format"},
    "progress": {"response_format"},
    "progress_mark": {"stage"},
    "query": {"kind"},
    "recall": {"memory_type"},
    "reconfigure": {"profile"},
    "remember": {"kind", "memory_type"},
    "review_record": {"outcome"},
    "review_status": {"response_format"},
    "run_finish": {"landing"},
    "session_name": {"transport"},
    "session_pull": {"transport"},
    "session_push": {"transport"},
    "spec_show": {"response_format"},
    "vault_push": {"kind"},
    "worktree_merge": {"strategy"},
}

#: Internal ids a model cannot use: stage ids (`O14`, `SI.3`, `MS.S2`), backlog rows
#: (`OSS-46`, `RUN-ID-DRIFT`), doc cites (`doc 88`, `§3`).
_ALLOWED_TOKENS = re.compile(r"\b(?:JSON-RPC|UTF-8|SHA-256|ISO-8601|YYYY-MM-DD|AC\d+|T\d+)\b")
_INTERNAL_ID = re.compile(
    r"§|\bdoc \d+|\b[Ss]tage \d+[a-zA-Z]?\b"
    r"|\b[A-Z][A-Z0-9]*\.[A-Z]?\d+[a-z]?\b"
    r"|\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+\b"
    r"|\b[A-Z]{1,2}\d{1,3}[a-z]?\b")


def _tools():
    return MS.build_server()._tool_manager.list_tools()


def _kind():
    return {t.name: t.kind for t in TOOLS}


def _first_sentence(text):
    return re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]


def _alts(prop):
    return prop.get("anyOf") or [prop]


def _resolve(prop, defs):
    ref = prop.get("$ref")
    return defs.get(ref.split("/")[-1], {}) if isinstance(ref, str) else prop


def _tools_list_wire_bytes():
    """UTF-8 bytes of the `tools/list` result the real stdio server sends. The server writes compact
    JSON with raw UTF-8 (pydantic `model_dump_json`); re-encoding the parsed `result` the same way
    reproduces those bytes exactly, independent of the JSON-RPC id."""
    import tempfile
    import _mcp_stdio as H
    d = tempfile.mkdtemp(prefix="i2-wire-")
    srv = H.McpStdio(d)
    try:
        reply = srv.request("tools/list")
    finally:
        srv.close()
        import shutil
        shutil.rmtree(d, ignore_errors=True)
    return len(json.dumps(reply["result"], separators=(",", ":"), ensure_ascii=False)
               .encode("utf-8"))


@_mcp_sdk.requires_mcp_sdk
class TestI2_SchemaContract(unittest.TestCase):
    """Every tool's schema and description, against the contract."""

    _built = None

    def setUp(self):
        # Built in setUp, not setUpClass: `requires_mcp_sdk` gates `setUp`, so a class fixture would
        # build the server BEFORE the gate (an error, not a skip, on the `jsonschema=absent` cell).
        cls = type(self)
        if cls._built is None:
            cls._built = (_tools(), _kind())
        self.tools, self.kind = cls._built

    def test_I2_every_parameter_is_described(self):
        bare = [f"{t.name}.{p}" for t in self.tools
                for p, spec in (t.parameters.get("properties") or {}).items()
                if not (spec.get("description") or "").strip()]
        self.assertEqual(bare, [])

    def test_I2_closed_sets_are_enums(self):
        by = {t.name: t for t in self.tools}
        missing = [f"{n}.{p}" for n, ps in CLOSED_SETS.items() for p in ps
                   if not any("enum" in _resolve(a, by[n].parameters.get("$defs") or {})
                              for a in _alts(by[n].parameters["properties"][p]))]
        self.assertEqual(missing, [])

    def test_I2_arrays_have_typed_items(self):
        untyped = []
        for t in self.tools:
            defs = t.parameters.get("$defs") or {}
            for p, spec in (t.parameters.get("properties") or {}).items():
                for alt in _alts(spec):
                    alt = _resolve(alt, defs)
                    if alt.get("type") == "array" and not alt.get("items"):
                        untyped.append(f"{t.name}.{p}")
        self.assertEqual(untyped, [])

    def test_I2_no_back_compat_parameters(self):
        old = [f"{t.name}.{p}" for t in self.tools for p in t.parameters.get("properties") or {}
               if p in ("approve", "confirm", "mtype")]
        self.assertEqual(old, [], "pre-1.0: no compatibility parameters (delete, do not demote)")

    def test_I2_every_write_tool_requires_something(self):
        none = [t.name for t in self.tools if self.kind[t.name] == "write"
                and t.name not in WRITE_TOOLS_WITHOUT_REQUIRED
                and not [r for r in t.parameters.get("required") or [] if r != "path"]]
        self.assertEqual(none, [])

    def test_I2_descriptions_fit_the_client(self):
        over = {t.name: len(t.description) for t in self.tools
                if len(t.description) > CLIENT_DESCRIPTION_LIMIT}
        self.assertEqual(over, {})

    def test_I2_descriptions_lead_with_a_short_first_sentence(self):
        long = {t.name: len(_first_sentence(t.description)) for t in self.tools
                if len(_first_sentence(t.description)) > FIRST_SENTENCE_MAX}
        self.assertEqual(long, {})

    def test_I2_descriptions_say_what_comes_back_and_what_can_fail(self):
        shapeless = [t.name for t in self.tools
                     if "Returns:" not in t.description or "Errors:" not in t.description]
        self.assertEqual(shapeless, [])

    def test_I2_descriptions_carry_no_internal_ids(self):
        found = {}
        for t in self.tools:
            hits = _INTERNAL_ID.findall(_ALLOWED_TOKENS.sub("", t.description))
            if hits:
                found[t.name] = sorted(set(hits))
        self.assertEqual(found, {})

    def test_I2_tools_list_never_grows(self):
        size = _tools_list_wire_bytes()
        self.assertLessEqual(size, TOOLS_LIST_CEILING,
                             f"tools/list is {size} bytes; the ratchet is {TOOLS_LIST_CEILING}")

    def test_I2_append_only_tools_are_not_read_only(self):
        from mokata.mcp.tool_annotations import APPEND_ONLY_TOOLS
        by = {t.name: t for t in self.tools}
        for name in APPEND_ONLY_TOOLS:
            ann = by[name].annotations
            self.assertIs(ann.readOnlyHint, False, name)
            self.assertIs(ann.destructiveHint, False, name)


class TestI2_CallerFaultIsRefusedNotError(unittest.TestCase):
    """No tool module builds a literal `"status": "error"` (a caller's fault is a raised
    `ValidationError`, which the front door turns into `refused`); `error` belongs to `_serve`'s
    R2/R3 and to the allowlisted real mokata-side faults below. AST sweep, pure."""

    #: (module, function) -> why that `error` is a mokata-side fault, not the caller's.
    ALLOWED = {
        ("tools_config", "config_set"):
            "after approval the manifest write itself failed (unreadable or missing manifest)",
        ("tools_evidence", "evidence_commit"):
            "the run's machine-local ledger or evidence cannot be read whole",
        ("tools_ide", "ide_open"): "the running IDE window did not accept the open",
        ("tools_read", "review_record"): "the verdict could not be written (storage fault)",
        ("tools_read", "progress_mark"): "the stage mark could not be written (storage fault)",
        ("tools_read", "run_finish"): "the finish event could not be written (storage fault)",
        ("tools_read", "working_set"): "the working set could not be read (storage fault)",
        ("tools_share", "vault_push"):
            "VaultError mixes vault faults with caller faults and its text names the value; "
            "splitting it is disclosed as a Low, not done here",
        ("tools_worktree", "worktree_clean"): "git refused the clean plan (repo-side state)",
        ("tools_session", "session_push"): "the bundle store failed (corrupt or unwritable)",
        ("tools_session", "session_pull"): "the bundle failed its content-hash check",
        ("tools_session", "session_name"): "the bundle store failed (corrupt or unwritable)",
    }

    # CORPUS: THE WORKING TREE — a tool module edited and not yet committed ships the same reply.
    def test_I2_no_hand_built_error_status_in_tool_modules(self):
        found = []
        mcp = os.path.join(SRC, "mcp")
        for fn in sorted(os.listdir(mcp)):
            if not (fn.startswith("tools_") and fn.endswith(".py")):
                continue
            tree = ast.parse(open(os.path.join(mcp, fn), encoding="utf-8").read())
            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(func):
                    if isinstance(node, ast.Dict):
                        for k, v in zip(node.keys, node.values):
                            if (isinstance(k, ast.Constant) and k.value == "status"
                                    and _is_error(v)
                                    and (fn[:-3], func.name) not in self.ALLOWED):
                                found.append(f"{fn}:{node.lineno} {func.name}")
        self.assertEqual(sorted(set(found)), [])


def _is_error(v):
    if isinstance(v, ast.Constant) and v.value == "error":
        return True
    return isinstance(v, ast.Attribute) and v.attr == "ERROR"


class TestI2_RemedyCommandParses(unittest.TestCase):
    """Every `remedyCommand` mokata derives is a real mokata command that parses with the CLI's own
    parser: the backticked form only (the bare-prose regex produced `mokata records what` and
    `mokata fault`). FIRING CONTROL (mutant): re-add `_BARE`."""

    def test_I2_prose_is_never_mistaken_for_a_command(self):
        from mokata.axi_out import remedy_command
        for prose in ("this is not a mokata fault", "mokata records what the review said",
                      "re-call it; mokata keeps the work going"):
            self.assertIsNone(remedy_command(prose), prose)
        self.assertEqual(remedy_command("run `mokata init` in the repo root"), "mokata init")

    # CORPUS: THE WORKING TREE — a tool module edited and not yet committed ships the same reply.
    def test_I2_every_backticked_remedy_in_the_tool_modules_parses(self):
        """A backticked `mokata …` names a real command: an unknown command or flag reds. A
        backticked command FAMILY (`mokata spec emit`, naming the command without its required
        `--file`) is a reference, not a broken remedy, so a missing-argument error passes."""
        import contextlib
        import io
        import shlex
        from mokata.axi_out import _CMD
        from mokata.cli import build_parser
        parser = build_parser()
        bad = []
        # The MCP surface (every tool module, consent, server) plus the extractor itself. The CLI
        # modules carry status lines (`mokata · develop`) that are not commands; their broken
        # references are disclosed for the backlog, not swept here.
        mcp = os.path.join(SRC, "mcp")
        files = [os.path.join(mcp, fn) for fn in sorted(os.listdir(mcp)) if fn.endswith(".py")]
        for path in files + [os.path.join(SRC, "axi_out.py")]:
            fn = os.path.basename(path)
            text = open(path, encoding="utf-8").read()
            for m in _CMD.finditer(text):
                cmd = m.group(1).strip().rstrip(".,;:")
                if not cmd.startswith("mokata ") or "<" in cmd or "{" in cmd or "…" in cmd:
                    continue
                err = io.StringIO()
                try:
                    with contextlib.redirect_stderr(err), \
                            contextlib.redirect_stdout(io.StringIO()):
                        parser.parse_args(shlex.split(cmd)[1:])
                except SystemExit as exc:
                    said = err.getvalue()
                    if exc.code and ("invalid choice" in said
                                     or "unrecognized arguments" in said):
                        bad.append(f"{fn}: {cmd}")
                except ValueError:
                    continue
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
