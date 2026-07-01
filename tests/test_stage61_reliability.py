"""Stage 61 — reliability & edge-case hardening (it-just-works).

A focused, seeded fuzz/edge battery across the HOT PATHS (the secret-guard saga lesson):
malformed / huge / empty / binary-ish / weird-unicode / truncated-JSON / missing-key inputs
must DEGRADE CLEAN — a clear error or a safe no-op — and NEVER crash a session with a
traceback. The two inviolable invariants are asserted together:

  * NO FALSE-BLOCKS — a large seeded corpus of benign paths/URLs/identifiers/hex/UUIDs/SHAs
    never blocks; only real secrets block (exit 2). Precision over zeal.
  * DEGRADE-CLEAN — every "the state/engine isn't well-formed" path returns a clean fallback
    (the documented "never raises" contracts actually hold), never a traceback.

Rough edges this stage fixed, each with a regression test below:
  1. a corrupt / truncated / wrong-shape pipeline CHECKPOINT crashed the read-only
     progress / lanes / badge / resume / bundle hot paths (StateStore.read +
     PipelineCheckpoint now degrade a bad state file to "absent / fresh", never raise);
  2. the completeness gate's AC-mapper crashed on a None test list (None-everywhere mandate).

Real-secret literals are ASSEMBLED from sub-20-char fragments at runtime, so this source
file itself carries no blockable literal. Dependency-free, deterministic (seeded RNG only).
"""

import json
import os
import random
import string
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

from mokata.state import StateStore
from mokata.govern.resume import CHECKPOINT_PREFIX, PipelineCheckpoint
from mokata.govern.secrets import has_secrets, scan
from mokata.progress import (
    build_progress,
    build_run_lanes,
    build_stage_badge,
    find_active_run,
    render_lanes,
    render_progress,
)


def _frag(*parts):
    """Assemble a runtime literal from benign fragments (no blockable literal in source)."""
    return "".join(parts)


# ======================================================================================
# ROUGH EDGE 1 — a corrupt / malformed checkpoint must not crash the progress hot paths.
# ======================================================================================
# Every form a state file can be broken in: truncated JSON, valid-JSON-wrong-shape, a
# top-level list, "passed" not a list. Each must degrade to "no run" (read-only views) or
# a fresh/empty checkpoint — never a traceback into the session.

# Genuinely UNPARSEABLE files (truncated / not-JSON / binary) — read degrades to "absent".
_UNPARSEABLE = {
    "truncated-json":  '{"run_id": "r", "passed": [',
    "empty-file":      '',
    "whitespace-only": '   \n  ',
    "not-json":        'this is not json at all',
    "binary-ish":      '\x00\x01\xff\xfe garbage',
}
# Valid JSON of the WRONG SHAPE — read still returns it, but the checkpoint reader must
# tolerate the shape (degrade to a fresh/empty checkpoint) rather than crash.
_WRONG_SHAPE = {
    "valid-but-no-passed": '{"run_id": "r"}',
    "passed-is-int":       '{"run_id": "r", "passed": 5}',
    "passed-is-str":       '{"run_id": "r", "passed": "abc"}',
    "passed-is-null":      '{"run_id": "r", "passed": null}',
    "top-level-list":      '[1, 2, 3]',
    "top-level-string":    '"just a string"',
    "top-level-number":    '42',
}
_BAD_CHECKPOINTS = {**_UNPARSEABLE, **_WRONG_SHAPE}


class TestCorruptCheckpointDegradesClean(unittest.TestCase):
    def _store_with(self, raw):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, CHECKPOINT_PREFIX + "r.json"), "w",
                  encoding="utf-8", errors="surrogateescape") as fh:
            fh.write(raw)
        return StateStore(d)

    def test_state_read_returns_none_on_unreadable_file(self):
        for label, raw in _UNPARSEABLE.items():
            with self.subTest(case=label):
                store = self._store_with(raw)
                # corrupt/truncated/unreadable degrades to "absent", never raises
                self.assertIsNone(store.read(CHECKPOINT_PREFIX + "r"))

    def test_pipeline_checkpoint_tolerates_any_shape(self):
        for label, raw in _BAD_CHECKPOINTS.items():
            with self.subTest(case=label):
                store = self._store_with(raw)
                cp = PipelineCheckpoint(store, "r")     # must not raise
                self.assertEqual(cp.passed, [])         # bad state -> empty (fresh)

    def test_find_active_run_never_crashes(self):
        for label, raw in _BAD_CHECKPOINTS.items():
            with self.subTest(case=label):
                store = self._store_with(raw)
                find_active_run(store)                  # must not raise

    def test_build_progress_degrades_to_inactive(self):
        for label, raw in _BAD_CHECKPOINTS.items():
            with self.subTest(case=label):
                store = self._store_with(raw)
                prog = build_progress(store)            # must not raise
                # an empty/fresh checkpoint reads as an active fresh run OR inactive; either
                # way it renders cleanly and never throws.
                self.assertIsInstance(render_progress(prog), str)

    def test_build_run_lanes_degrades_clean(self):
        for label, raw in _BAD_CHECKPOINTS.items():
            with self.subTest(case=label):
                store = self._store_with(raw)
                lanes = build_run_lanes(store)          # must not raise
                self.assertIsInstance(render_lanes(lanes), str)

    def test_stage_badge_never_crashes_on_bad_state(self):
        class _Surface:
            def __init__(self, store):
                self.state = store
        for label, raw in _BAD_CHECKPOINTS.items():
            with self.subTest(case=label):
                surface = _Surface(self._store_with(raw))
                badge = build_stage_badge(surface)      # must not raise
                self.assertTrue(badge.startswith("mokata"))


# ======================================================================================
# ROUGH EDGE 2 — completeness AC-mapper must tolerate a None test list.
# ======================================================================================
class TestCompletenessGateFuzz(unittest.TestCase):
    def _spec(self, n):
        from mokata.engine.spec import AcceptanceCriterion, Spec
        return Spec(title="s",
                    criteria=[AcceptanceCriterion(id=f"AC-{i}", text=f"c{i}")
                              for i in range(n)])

    def test_ac_mapper_tolerates_none_tests(self):
        from mokata.engine.acmapper import map_acceptance_criteria
        res = map_acceptance_criteria(self._spec(3), None)   # must not raise
        self.assertEqual(res.unmapped_ids, ["AC-0", "AC-1", "AC-2"])

    def test_gate_blocks_on_empty_spec_cleanly(self):
        from mokata.engine.completeness import run_completeness_gate
        from mokata.engine.spec import Spec
        res = run_completeness_gate(Spec(title="empty", criteria=[]), [])
        self.assertFalse(res.passed)
        self.assertIsInstance(res.render(), str)

    def test_gate_handles_a_gigantic_ac_set(self):
        from mokata.engine.completeness import run_completeness_gate
        res = run_completeness_gate(self._spec(5000), [])     # bounded, no crash
        self.assertFalse(res.passed)
        self.assertEqual(len(res.unmapped_ids), 5000)

    def test_gate_degrades_clean_on_malformed_persisted_spec(self):
        # A truncated / wrong-shape persisted spec must not crash the gate path; the gate reads
        # it through load_emitted_spec (guarded) and BLOCKS with the actionable message.
        from mokata.engine.spec_gate import check_spec_persisted, load_emitted_spec
        for bad in ({"title": "x", "criteria": "abc"},        # criteria not a list
                    {"title": "x", "criteria": [{"text": "t"}]},  # missing id
                    {"title": "x", "criteria": None},
                    {"title": "x", "criteria": [42]},
                    "not even a dict"):
            with self.subTest(bad=bad):
                d = tempfile.mkdtemp()
                store = StateStore(d)
                store.write("emitted_spec", {"x": 1})  # placeholder so file exists
                # overwrite with the malformed payload
                with open(store.path("emitted_spec"), "w", encoding="utf-8") as fh:
                    json.dump(bad, fh)
                self.assertIsNone(load_emitted_spec(store))    # guarded -> None
                res = check_spec_persisted(store)              # must not raise
                self.assertFalse(res.passed)


# ======================================================================================
# NO FALSE-BLOCKS — a large seeded benign corpus never blocks; real secrets still block.
# ======================================================================================
class TestSecretGuardNoFalseBlocks(unittest.TestCase):
    """Extends the Stage 46 fuzz precedent with a much larger, more varied seeded corpus.
    Precision is the saga lesson: random real-world-shaped benign tokens must NEVER block."""

    def _rng_word(self, rng, lo=3, hi=14):
        return "".join(rng.choice(string.ascii_lowercase + string.digits)
                       for _ in range(rng.randint(lo, hi)))

    def test_large_benign_corpus_never_blocks(self):
        rng = random.Random(0xBEEF61)
        for _ in range(1500):
            kind = rng.randrange(8)
            if kind == 0:                                       # deep file path
                s = "/".join(self._rng_word(rng) for _ in range(rng.randint(2, 8))) \
                    + "." + rng.choice(("py", "md", "txt", "json", "ts", "rs", "go"))
            elif kind == 1:                                     # https URL with a path + query
                s = "https://" + self._rng_word(rng) + "." + \
                    rng.choice(("com", "io", "dev", "org")) + "/" + \
                    "/".join(self._rng_word(rng) for _ in range(rng.randint(1, 5)))
            elif kind == 2:                                     # snake / kebab identifier
                sep = rng.choice(("_", "-"))
                s = sep.join(self._rng_word(rng) for _ in range(rng.randint(3, 10)))
            elif kind == 3:                                     # hex digest (sha/md5/uuid hex)
                s = "".join(rng.choice("0123456789abcdef")
                            for _ in range(rng.choice((7, 32, 40, 64))))
            elif kind == 4:                                     # UUID
                h = "".join(rng.choice("0123456789abcdef") for _ in range(32))
                s = f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"
            elif kind == 5:                                     # semver / version range
                s = f"{rng.randint(0,9)}.{rng.randint(0,99)}.{rng.randint(0,99)}" \
                    + rng.choice(("", "-beta.1", "-rc.2", "+build.7"))
            elif kind == 6:                                     # a sentence of prose
                s = " ".join(self._rng_word(rng, 2, 9) for _ in range(rng.randint(4, 12)))
            else:                                               # python import / dotted path
                s = ".".join(self._rng_word(rng) for _ in range(rng.randint(2, 5)))
            self.assertFalse(has_secrets(scan(text=s)),
                             f"FALSE BLOCK on benign: {s!r}")

    def test_real_secrets_still_block(self):
        # Assembled at runtime so this source carries no blockable literal.
        secrets = [
            _frag("AKIA", "IOSFODNN7", "EXAMPLE"),
            _frag("ghp_", "016c1f2e3a", "4b5d6e7f8a", "9b0c1d2e3f"),
            _frag("AIza", "SyDaGkq3mN", "pL7wRtVbGc", "HdEf1JkMnO", "pXyZ3"),
            "api_key = \"" + _frag("Xy9KqWmZ3b", "PnL7vTsRdG") + "\"",
            _frag("sk_live_", "4eC39HqLyj", "WDarjtT1zd", "p7dc"),
            _frag("-----BEGIN ", "OPENSSH PRIVATE ", "KEY-----"),
        ]
        for sec in secrets:
            with self.subTest(secret=sec[:6]):
                self.assertTrue(has_secrets(scan(text=sec)), "MISSED a real secret")

    def test_secret_egress_is_fatal(self):
        sec = _frag("AKIA", "IOSFODNN7", "EXAMPLE")
        findings = scan(text="leak " + sec, for_send=True)
        self.assertTrue(any(f.layer == "egress" for f in findings))


# ======================================================================================
# DEGRADE-CLEAN — fuzz the remaining hot paths with malformed / huge / binary / unicode.
# ======================================================================================
class TestHotPathFuzzDegradesClean(unittest.TestCase):
    def _no_crash(self, fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — the whole point is "must not raise"
            self.fail(f"hot path crashed: {type(exc).__name__}: {exc}")

    def test_secret_scan_handles_pathological_text(self):
        for text in ("", None, "\x00\x01\xff\xfe" * 200, "A" * 200000,
                     "‮​\U0001F600" * 500, "\n" * 5000, "\t  \r\n"):
            with self.subTest(text=repr(text)[:24]):
                self._no_crash(lambda t=text: scan(text=t))

    def test_secret_scan_handles_pathological_path(self):
        for path in ("", None, "/" * 1000, "x" * 5000, ".env\x00", "/a/b/.env"):
            with self.subTest(path=repr(path)[:24]):
                self._no_crash(lambda p=path: scan(path=p))

    def test_spec_awareness_check_change_fuzz(self):
        from mokata.engine.spec_awareness import ChangeSet, check_change
        cases = [
            ChangeSet(),
            ChangeSet(symbols=[None, "", "x"], files=[None, "", "a/b.py"]),
            ChangeSet(symbols=["s"] * 2000, files=["f.py"] * 2000),
            ChangeSet(text="\x00\xff" * 100),
        ]
        for ch in cases:
            with self.subTest(change=ch):
                self._no_crash(lambda c=ch: check_change(c, [], []))

    def test_memory_recall_on_empty_and_weird_queries(self):
        from mokata.memory.brain import always_on_lines, jit_recall
        from mokata.memory.tiered import tiered_recall

        class _EmptyStore:
            backend = None
            def all_active(self, *a, **k):
                return []
            def peek_active(self, *a, **k):
                return []

        store = _EmptyStore()
        for q in ("", None, "A" * 100000, "\x00\xff", "‮​" * 100):
            with self.subTest(query=repr(q)[:20]):
                self._no_crash(lambda x=q: jit_recall(store, x))
                self._no_crash(lambda x=q: tiered_recall(store, x))
                self._no_crash(lambda: always_on_lines(store, 5, query=q))

    def test_memory_recall_with_items_and_huge_query(self):
        from mokata.memory.brain import jit_recall
        from mokata.memory.item import CONTEXT, MemoryItem

        class _Store:
            def __init__(self, items):
                self._items = items
            def all_active(self, *a, **k):
                return self._items

        items = [MemoryItem.create(f"subject {i}", f"value {i}", mtype=CONTEXT, kind="context")
                 for i in range(50)]
        store = _Store(items)
        for q in ("subject", "A" * 50000, "\x00", ""):
            with self.subTest(query=repr(q)[:16]):
                self._no_crash(lambda x=q: jit_recall(store, x, top_k=5))

    def test_session_bundle_parse_rejects_garbage_cleanly(self):
        from mokata.session_bundle import SessionBundleError, parse_bundle
        for blob in ("", "   ", "not json", "{", "{}", "[]", "null", "123", '"str"',
                     '{"kind": "wrong"}', '{"kind": "mokata-session-bundle"}',
                     "\x00\xff\xfe", "A" * 100000):
            with self.subTest(blob=repr(blob)[:24]):
                try:
                    parse_bundle(blob)
                except SessionBundleError:
                    pass  # the documented clean error
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"parse_bundle crashed on {blob!r}: "
                              f"{type(exc).__name__}: {exc}")

    def test_safe_tag_rejects_path_escapes_cleanly(self):
        from mokata.session_bundle import SessionBundleError, _safe_tag
        for tag in ("", ".", "..", "a/b", "a\\b", "../x", "/etc/passwd", "a:b", "x" * 5000):
            with self.subTest(tag=repr(tag)[:20]):
                if tag and tag == os.path.basename(tag) and tag not in (".", "..") \
                        and not any(c in tag for c in "/\\:"):
                    _safe_tag(tag)  # a long-but-simple slug is allowed
                else:
                    with self.assertRaises(SessionBundleError):
                        _safe_tag(tag)


# ======================================================================================
# DEGRADE-CLEAN — the hooks never block a session on a routing / engine / input problem.
# ======================================================================================
class TestHookCliDegradesClean(unittest.TestCase):
    def _quiet_guard(self, env):
        """Run secret_guard_main on `env` via stdin, with stdout/stderr silenced (the hook
        writes its BLOCKED lines to stderr — real behaviour, kept out of the test output)."""
        import contextlib
        import io
        import sys

        from mokata import hook_cli
        old = sys.stdin
        sys.stdin = io.StringIO(env)
        try:
            with contextlib.redirect_stderr(io.StringIO()), \
                    contextlib.redirect_stdout(io.StringIO()):
                return hook_cli.secret_guard_main([])
        finally:
            sys.stdin = old

    def test_secret_guard_envelope_fuzz_never_crashes(self):
        from mokata.hook_cli import _find_path, _from_envelope, _iter_strings
        payloads = [
            json.dumps({"tool_input": {"a": {"b": {"c": ["x"] * 200}}}}),
            json.dumps({"tool_input": 5}),
            json.dumps({"tool_input": None}),
            json.dumps({"tool_input": {"content": "A" * 200000}}),
            json.dumps({"no_tool_input": 1}),
            "not json", "", "[]", "null",
            json.dumps({"tool_input": {"edits": [{"new_string": "x"}] * 100}}),
        ]
        for p in payloads:
            with self.subTest(payload=repr(p)[:24]):
                try:
                    res = _from_envelope(p)
                    if res is not None:
                        list(_iter_strings(json.loads(p).get("tool_input")))
                except Exception as exc:  # noqa: BLE001
                    self.fail(f"_from_envelope crashed on {p!r}: "
                              f"{type(exc).__name__}: {exc}")
        # _find_path / _iter_strings over weird shapes
        for obj in ({}, [], None, 3, "s", {"a": [None, {"path": "p.py"}]}):
            list(_iter_strings(obj))
            _find_path(obj)

    def test_secret_guard_main_blocks_real_secret_via_stdin(self):
        env = json.dumps({"tool_name": "Write",
                          "tool_input": {"file_path": "a.txt",
                                         "content": "k " + _frag("AKIA", "IOSFODNN7",
                                                                  "EXAMPLE")}})
        self.assertEqual(self._quiet_guard(env), 2)    # a real secret hard-blocks (exit 2)

    def test_secret_guard_main_passes_clean_content_via_stdin(self):
        env = json.dumps({"tool_name": "Edit",
                          "tool_input": {"file_path": "foo.py",
                                         "old_string": "a", "new_string": "b"}})
        self.assertEqual(self._quiet_guard(env), 0)

    def test_hook_dispatcher_unknown_subcommand_is_a_clean_noop(self):
        import contextlib
        import io

        from mokata.hook_cli import main
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([]), 0)                    # missing subcommand
            self.assertEqual(main(["bogus-subcommand"]), 0)  # unknown subcommand
            self.assertEqual(main(["statusline"]), 0)        # known, read-only, exits 0


if __name__ == "__main__":
    unittest.main()
