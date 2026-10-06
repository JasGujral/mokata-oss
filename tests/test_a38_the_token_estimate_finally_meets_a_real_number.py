"""0.0.21 stage 09 · H-2 — the chars/4 estimate finally meets a real number, and the loop R11
wired in 0.0.16 is closed.

THE SHAPE OF THE DEFECT, because it is the one stage 00 found fifteen times. R11 built the
calibration record, the margin constant, and `doctor`'s `calibration-drift` finding. Its ONE
production caller — `hook_cli.session_start_main` — passes `actual=None`, and its own comment
says why: *"Claude Code reports no real token count at inject time"*. That sentence is TRUE
about the envelope and FALSE about what the envelope points at. So a finding shipped that could
not fire outside a test, and every budget in the tree (the 2000-token bootstrap, the handback
caps, the savings ledger, the parallel cost estimates) was enforced against a safety margin
nobody had ever measured. ⭐ **An instrument whose only runner is a test is not an instrument**,
and P16 is the principle it was in breach of.

WHAT THIS FILE GRADES, and the order matters because each class is the reason the next one is
not vacuous:

  1. the adapter measures only what it can account for WHOLLY — a message whose
     `output_tokens` covers a `tool_use` or `thinking` block it cannot read is EXCLUDED, never
     partially counted, which is the scope limit §7j asks to be declared rather than implied;
  2. a sample cannot carry transcript TEXT, probed by planting a marker string in a transcript
     and searching the whole reading for it, not by reading the dataclass and trusting it;
  3. the ID validates the OFFSET — a session change, a shrunk file and a partial last line all
     behave, because *position is not a state*;
  4. "nothing new", "nothing calibratable" and "nothing understood" are three answers (§7g),
     and the third is what a Claude Code schema change looks like BEFORE the measurement
     quietly starts measuring nothing (§7e);
  5. the ratio is a quotient of TOTALS, pinned against the mean-of-ratios that would let the
     shortest messages outvote the longest;
  6. the calibration section survives the memory COUNTERS — the latent §7j this stage found in
     `_persist_stats`, graded in both directions and with the pre-fix shape planted;
  7. the loop CLOSES: `diagnose` reports drift after `observe` alone, with nothing in the test
     ever calling `log_calibration` by hand;
  8. and the PostToolUse hook is the production caller, proved by RUNNING it.

  9. clause (c): the degraded token trail is KEPT and MARKED, so `actual_cost` stops reporting
     $0.0000 for a batch that spent money.
 10. and the measured constant has a reader, because a field every writer populates and no
     reader ever sees is a row this backlog already carries.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import dataclasses
import io
import json
import os
import shutil
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.adapters.transcript import (
    TRANSCRIPT_LINE_CAP_BYTES,
    TokenSample,
    TranscriptCursor,
    read_samples,
    response_of,
    sample_of_response,
)
from mokata.govern.calibration import (
    CALIBRATION_MIN_ESTIMATE_TOKENS,
    CALIBRATION_STATS_KEY,
    ModelCalibration,
    calibration_of,
    fold,
    load_cursor,
    merged_stats,
    observe,
)
from mokata.govern.tokens import CALIBRATION_KIND, CALIBRATION_MARGIN_RATIO


# --------------------------------------------------------------------------------- the fixtures

_RESPONSE_SEQ = [0]


def envelope(model="claude-test", text="x", output_tokens=100, blocks=None,
             role="assistant", wrapped=True, usage=True, msg_id=None, thinking=None):
    """One transcript record. `blocks` overrides the content entirely so a test can plant a
    `tool_use`/`thinking` message without this helper deciding what those look like.

    ⚠ `msg_id` DEFAULTS TO A FRESH ID per call, because `usage` describes an API RESPONSE and
    the adapter groups records by `message.id`. Two records sharing an id are two BLOCKS of one
    response — which is the real shape the first live transcript taught us — so a test that
    wants two independent responses must not accidentally hand them one id."""
    if msg_id is None:
        _RESPONSE_SEQ[0] += 1
        msg_id = "resp-%d" % _RESPONSE_SEQ[0]
    message = {"role": role, "model": model, "id": msg_id,
               "content": blocks if blocks is not None else [{"type": "text", "text": text}]}
    if usage:
        u = {"input_tokens": 9999, "output_tokens": output_tokens}
        if thinking is not None:
            u["output_tokens_details"] = {"thinking_tokens": thinking}
        message["usage"] = u
    return {"type": role, "message": message} if wrapped else message


def sample_of(record):
    """The sample for a SINGLE-record response — the shape most of this file's fixtures use.

    It exists because the unit changed under these tests: `usage` is per API response, so the
    adapter's own entry points are `response_of` (one record's facts) and `sample_of_response`
    (a fully-collected response). A one-record response is the degenerate case, and keeping it
    expressible here is what let the per-record assertions below survive the rework unchanged."""
    group = response_of(record)
    return sample_of_response(group) if group is not None else None


# A user turn. ⭐ IT IS NOT DECORATION: `usage` describes an API RESPONSE, so the adapter holds
# the newest response OPEN until something proves it finished — and a non-assistant record is
# that proof. A real transcript is full of them; a fixture that writes only assistant records
# and expects an immediate sample is testing a file shape that does not occur.
_CLOSER = {"type": "user", "message": {"role": "user", "content": "ok"}}


def write_jsonl(path, records, trailing_newline=True, close=True):
    """Write a transcript fixture. `close=True` appends a user turn so the last assistant
    response is CLOSED — pass `close=False` to test the open-response boundary itself."""
    rows = list(records) + ([_CLOSER] if close else [])
    body = "\n".join(json.dumps(r) if not isinstance(r, str) else r for r in rows)
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(body + ("\n" if trailing_newline else ""))
    return path


def a_repo():
    """An initialized mokata repo in a temp dir, plus a transcript path inside it."""
    from mokata.init import init_repo
    d = tempfile.mkdtemp()
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_a: None)
    return d, os.path.join(d, "transcript.jsonl")


def stats_of(root):
    from mokata.memory.store import MEMORY_STATS_KEY
    from mokata.state import StateStore
    from mokata.tdd_state import state_dir
    return StateStore(state_dir(root)).read(MEMORY_STATS_KEY)


def ledger_calibrations(root):
    from mokata.govern.ledger import AuditLedger
    led = AuditLedger.from_mokata_dir(os.path.join(root, ".mokata"))
    return [e for e in led.entries() if e.get("kind") == CALIBRATION_KIND]


# ------------------------------------------------------ 1 · wholly accountable, or not at all

class TheAdapterMeasuresOnlyWhatItCanAccountForWholly(unittest.TestCase):

    def test_an_all_text_assistant_message_is_a_sample(self):
        s = sample_of(envelope(text="a" * 400, output_tokens=96))
        self.assertIsNotNone(s)
        self.assertEqual(s.model, "claude-test")
        self.assertEqual(s.estimate, 100)        # ceil(400/4)
        self.assertEqual(s.actual, 96)

    def test_a_tool_use_block_makes_the_message_UNUSABLE_not_partially_counted(self):
        """`output_tokens` covers the serialized tool input, whose characters are not in `text`.
        Counting the text half would inflate every ratio and the instrument would report the
        margin blown by a defect of its own making."""
        rec = envelope(blocks=[{"type": "text", "text": "a" * 400},
                               {"type": "tool_use", "id": "t1", "name": "Edit", "input": {}}],
                       output_tokens=500)
        self.assertIsNone(sample_of(rec))

    def test_a_thinking_block_is_the_same_refusal(self):
        rec = envelope(blocks=[{"type": "thinking", "thinking": "..."},
                               {"type": "text", "text": "a" * 400}], output_tokens=900)
        self.assertIsNone(sample_of(rec))

    def test_an_unknown_block_type_is_refused_rather_than_skipped_over(self):
        """The list is not an exclusion list of the block types we know about — it is an
        inclusion list of the ONE type whose characters are the output. A block type invented
        next year must refuse, not be ignored."""
        rec = envelope(blocks=[{"type": "text", "text": "a" * 400},
                               {"type": "a_block_type_from_2027", "payload": "?"}])
        self.assertIsNone(sample_of(rec))

    def test_a_string_content_is_its_own_text(self):
        rec = envelope(output_tokens=25)
        rec["message"]["content"] = "b" * 100
        s = sample_of(rec)
        self.assertIsNotNone(s)
        self.assertEqual(s.estimate, 25)

    def test_no_output_tokens_is_NOT_a_sample_with_actual_zero(self):
        """§7g at the smallest scale that matters: `actual=0` against a real estimate reads as a
        catastrophic harness under-count, which is a different claim from "no number was
        reported"."""
        self.assertIsNone(sample_of(envelope(usage=False)))
        self.assertIsNone(sample_of(envelope(output_tokens=0)))
        self.assertIsNone(sample_of(envelope(output_tokens=-3)))

    def test_a_bool_is_not_a_token_count(self):
        """`isinstance(True, int)` is True in Python, so a schema that ever puts a flag where a
        count belongs would otherwise contribute `actual=1`."""
        self.assertIsNone(sample_of(envelope(output_tokens=True)))

    def test_a_user_message_and_a_missing_model_are_both_refused(self):
        self.assertIsNone(sample_of(envelope(role="user")))
        self.assertIsNone(sample_of(envelope(model="")))
        self.assertIsNone(sample_of(envelope(model=None)))

    def test_both_documented_envelope_shapes_are_accepted_and_a_third_is_not(self):
        self.assertIsNotNone(sample_of(envelope(text="a" * 40, wrapped=True)))
        self.assertIsNotNone(sample_of(envelope(text="a" * 40, wrapped=False)))
        self.assertIsNone(sample_of({"event": "assistant_message", "tokens": 12}))
        self.assertIsNone(sample_of("a bare string"))
        self.assertIsNone(sample_of(None))


# ------------------------------------------------------------- 2 · a sample cannot carry text

class ASampleCannotCarryTranscriptTEXT(unittest.TestCase):
    """The privacy floor, probed rather than asserted from the type."""

    def test_the_sample_type_has_no_field_a_fragment_could_land_in(self):
        names = {f.name for f in dataclasses.fields(TokenSample)}
        self.assertEqual(names, {"model", "estimate", "actual"})
        self.assertTrue(getattr(TokenSample, "__dataclass_params__").frozen)

    def test_a_planted_marker_appears_NOWHERE_in_a_whole_reading(self):
        marker = "SECRET-MARKER-9f3a21-DO-NOT-CARRY"
        with tempfile.TemporaryDirectory() as d:
            path = write_jsonl(os.path.join(d, "t.jsonl"),
                               [envelope(text=marker + "q" * 400, output_tokens=120)])
            reading = read_samples(path, session_id="s")
        self.assertEqual(len(reading.samples), 1, "the fixture must actually produce a sample")
        self.assertNotIn(marker, repr(reading))
        self.assertNotIn(marker, json.dumps([dataclasses.asdict(s) for s in reading.samples]))

    def test_and_the_probe_itself_is_not_vacuous(self):
        """The negative half of the previous test: the marker IS in the file it was planted in,
        so a reading that failed to contain it proves something."""
        marker = "SECRET-MARKER-9f3a21-DO-NOT-CARRY"
        with tempfile.TemporaryDirectory() as d:
            path = write_jsonl(os.path.join(d, "t.jsonl"),
                               [envelope(text=marker + "q" * 400, output_tokens=120)])
            self.assertIn(marker, io.open(path, encoding="utf-8").read())


# ------------------------------------------- 2b · `usage` IS PER RESPONSE, NOT PER RECORD

class UsageDescribesAResponseAndNotARecord(unittest.TestCase):
    """⛔ **THE DEFECT THE FIRST REAL TRANSCRIPT FOUND, and it is the sharpest thing this module
    has produced.** The first version classified one JSONL line at a time and reported a chars/4
    ratio of **7.48** on a live Claude Code session — impossible for text. Claude Code writes
    **one record per content BLOCK** and every one of them repeats the whole response's `usage`:
    measured on that session, **415 API responses, 237 written across more than one record**. A
    32-character text record carried `output_tokens: 5666`, because the response it belonged to
    also held the tool call that wrote a 300-line file. ⭐ *The module was comparing a whole to a
    part* — and its own docstring had warned about the adjacent mistake while being blind to
    this one."""

    def test_two_records_with_ONE_id_are_ONE_response(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_jsonl(os.path.join(d, "t.jsonl"), [
                envelope(text="a" * 400, output_tokens=200, msg_id="r1"),
                envelope(text="b" * 400, output_tokens=200, msg_id="r1"),
            ])
            reading = read_samples(path, session_id="s")
        self.assertEqual(reading.responses, 1, "one id is one response, whatever the record count")
        self.assertEqual(len(reading.samples), 1)
        # the response's text is BOTH blocks, and its actual is the usage reported ONCE
        self.assertEqual(reading.samples[0].estimate, 200)
        self.assertEqual(reading.samples[0].actual, 200)

    def test_INTERLEAVED_responses_each_collect_ALL_of_their_own_blocks(self):
        """⛔ THE REVIEW FINDING (F1), AND IT IS 7.48 ARRIVING BY A NEW ROUTE.

        The first version closed a group when the NEXT record carried a different `id`. Records
        of two responses INTERLEAVE — a sidechain `Task` does it by construction — so `A, B, A`
        closed A on B's arrival and judged it from its FIRST BLOCK ONLY, then treated A's
        remaining blocks as a THIRD response. ⭐ Fewer chars against the same token count is an
        UNDER-estimate of chars/token, which is exactly the defect this stage exists to fix.

        `A` here carries 400 + 400 characters against ONE reported count. Judged whole it is the
        same arithmetic as `test_two_records_with_ONE_id_are_ONE_response`; judged on its first
        block it is half the text for all of the tokens."""
        with tempfile.TemporaryDirectory() as d:
            path = write_jsonl(os.path.join(d, "t.jsonl"), [
                envelope(text="a" * 400, output_tokens=200, msg_id="rA"),
                envelope(text="b" * 400, output_tokens=200, msg_id="rB"),
                envelope(text="a" * 400, output_tokens=200, msg_id="rA"),
            ])
            reading = read_samples(path, session_id="s")
        self.assertEqual(reading.responses, 2, "two ids are two responses, interleaved or not")
        self.assertEqual(reading.interleaved, 1,
                         "a record returning to an earlier response must be COUNTED, not assumed absent (P16)")
        by_id = {s.estimate: s for s in reading.samples}
        self.assertEqual(len(reading.samples), 2)
        chars = sorted(s.chars for s in reading.samples) if hasattr(
            reading.samples[0], "chars") else None
        del by_id, chars                 # shape-dependent; the arithmetic below is the assertion
        self.assertEqual([200, 200], sorted(s.actual for s in reading.samples))
        # ⭐ THE ASSERTION THAT WOULD HAVE FAILED BEFORE THE FIX: A's estimate is built from BOTH
        # of its blocks, so it equals B's single-block estimate DOUBLED minus nothing — the two
        # samples are NOT equal, and A is the larger.
        estimates = sorted(s.estimate for s in reading.samples)
        self.assertEqual(estimates[1], estimates[0] * 2,
                         "A's two blocks did not both reach its sample: %r" % (estimates,))

    def test_an_interleaved_window_does_NOT_invent_a_third_response(self):
        """The other half of F1: the old code emitted `A, B, A` as THREE closed responses, so the
        instrument over-counted its own denominator as well as under-pricing its numerator."""
        with tempfile.TemporaryDirectory() as d:
            path = write_jsonl(os.path.join(d, "t.jsonl"), [
                envelope(text="a" * 100, output_tokens=50, msg_id="rA"),
                envelope(text="b" * 100, output_tokens=50, msg_id="rB"),
                envelope(text="a" * 100, output_tokens=50, msg_id="rA"),
                envelope(text="b" * 100, output_tokens=50, msg_id="rB"),
            ])
            reading = read_samples(path, session_id="s")
        self.assertEqual(reading.responses, 2)
        self.assertEqual(reading.assistant, 4, "all four records were read")
        self.assertEqual(reading.interleaved, 2, "both ids came back")

    def test_a_NON_interleaved_window_reports_ZERO_regrouping(self):
        """The negative control. A counter that is never zero is a counter nobody can read."""
        with tempfile.TemporaryDirectory() as d:
            path = write_jsonl(os.path.join(d, "t.jsonl"), [
                envelope(text="a" * 100, output_tokens=50, msg_id="rA"),
                envelope(text="b" * 100, output_tokens=50, msg_id="rB"),
            ])
            reading = read_samples(path, session_id="s")
        self.assertEqual(reading.responses, 2)
        self.assertEqual(reading.interleaved, 0)

    def test_a_PARTIAL_usage_on_the_first_record_does_not_block_a_REAL_one_later(self):
        """⛔ REVIEW F3. The group took a record's `usage` only when it had none at all, so a
        FIRST record carrying a `usage` dict with no readable output-token count locked the group
        out of a LATER record's real one. The response then read as unpriced, `priced` stayed 0,
        and `schema_blind` fired — **a false "the transcript JSONL shape may have moved" over a
        transcript whose shape is fine**, which is the one finding this instrument exists to be
        believed about."""
        first = envelope(text="a" * 200, msg_id="r1", usage=False)
        first["message"]["usage"] = {"input_tokens": 9999}        # a dict, and unreadable
        with tempfile.TemporaryDirectory() as d:
            path = write_jsonl(os.path.join(d, "t.jsonl"), [
                first,
                envelope(text="b" * 200, output_tokens=120, msg_id="r1"),
            ])
            reading = read_samples(path, session_id="s")
        self.assertEqual(reading.responses, 1)
        self.assertEqual(reading.priced, 1,
                         "the second record's real count never reached the group")
        self.assertFalse(reading.schema_blind,
                         "a partial usage on one record read as a moved schema")
        self.assertEqual(reading.samples[0].actual, 120)

    def test_a_TOOL_USE_BLOCK_IN_ANOTHER_RECORD_of_the_same_response_disqualifies_it(self):
        """⭐ The exact live shape. The text record alone looks perfectly calibratable; it is the
        SIBLING record that makes its `output_tokens` unattributable."""
        with tempfile.TemporaryDirectory() as d:
            path = write_jsonl(os.path.join(d, "t.jsonl"), [
                envelope(text="a" * 400, output_tokens=5666, msg_id="r1"),
                envelope(blocks=[{"type": "tool_use", "id": "t", "name": "Write", "input": {}}],
                         output_tokens=5666, msg_id="r1"),
            ])
            reading = read_samples(path, session_id="s")
        self.assertEqual(reading.responses, 1)
        self.assertEqual(reading.priced, 1, "it HAS a real token count — it is simply not ours")
        self.assertEqual(reading.usable, 0, "and the text half must not be counted against it")

    def test_and_the_PRE_FIX_READING_is_what_7_point_48_looked_like(self):
        """The planted offender (§7i): judge that same text record ALONE and the ratio explodes.
        This is the arithmetic the old code did, kept here so the fix grades the property."""
        rec = envelope(text="a" * 400, output_tokens=5666, msg_id="r1")
        alone = sample_of(rec)
        self.assertIsNotNone(alone, "a lone record still looks calibratable — that was the trap")
        self.assertGreater(alone.actual / alone.estimate, 50,
                           "the per-record reading is absurd, which is why the unit changed")

    def test_thinking_tokens_are_SUBTRACTED_not_left_to_inflate_the_ratio(self):
        """Thinking is billed as output and its characters are not in the content, so removing
        it leaves exactly the text's own count. ⚠ On the one real transcript available, every
        calibratable response had zero thinking tokens, so subtracting and excluding agreed —
        subtraction is kept because it is the rule that survives them disagreeing."""
        with tempfile.TemporaryDirectory() as d:
            path = write_jsonl(os.path.join(d, "t.jsonl"),
                               [envelope(text="a" * 400, output_tokens=150, thinking=50)])
            reading = read_samples(path, session_id="s")
        self.assertEqual(len(reading.samples), 1)
        self.assertEqual(reading.samples[0].actual, 100, "150 output minus 50 thinking")

    def test_a_response_that_is_ALL_thinking_is_no_sample_at_all(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_jsonl(os.path.join(d, "t.jsonl"),
                               [envelope(text="a" * 400, output_tokens=50, thinking=50)])
            self.assertEqual(read_samples(path, session_id="s").samples, [])

    def test_a_NON_ASSISTANT_record_closes_the_open_response(self):
        """A user turn after a response proves it finished. Without this the newest response in
        every window stayed open and the instrument ran one response behind forever — which, on
        a hook that fires per tool call, is most of them."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.jsonl")
            with io.open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(json.dumps(envelope(text="a" * 400, output_tokens=90)) + "\n")
            self.assertEqual(read_samples(path, session_id="s").samples, [],
                             "nothing has proved the response finished yet")
            with io.open(path, "a", encoding="utf-8", newline="") as fh:
                fh.write(json.dumps(_CLOSER) + "\n")
            self.assertEqual(len(read_samples(path, session_id="s").samples), 1)

    def test_the_cursor_STOPS_at_the_open_response_so_the_next_read_can_close_it(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.jsonl")
            first_rec = json.dumps(envelope(text="a" * 400, output_tokens=90, msg_id="r1"))
            with io.open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(first_rec + "\n" + json.dumps(_CLOSER) + "\n"
                         + json.dumps(envelope(text="b" * 400, output_tokens=95, msg_id="r2")) + "\n")
            first = read_samples(path, session_id="s")
            self.assertEqual(len(first.samples), 1, "r1 closed; r2 is still open")
            self.assertLess(first.cursor.offset, os.path.getsize(path),
                            "the cursor must stop BEFORE the open response")
            with io.open(path, "a", encoding="utf-8", newline="") as fh:
                fh.write(json.dumps(_CLOSER) + "\n")
            second = read_samples(path, session_id="s", cursor=first.cursor)
            self.assertEqual(len(second.samples), 1, "r2 is read exactly once, not twice")

    def test_the_line_cap_is_SMALLER_than_the_read_cap_and_that_is_load_bearing(self):
        """⚠ A window holding no complete line advances past itself and reports a reason — which
        moves the offset into the MIDDLE of a record and loses it. That path is unreachable in
        production only because a single line can never fill the read window, and this is the
        relationship that makes it so. If a future author raises the line cap above the read
        cap, the loss becomes reachable and silent."""
        from mokata.adapters.transcript import TRANSCRIPT_READ_CAP_BYTES
        self.assertLess(TRANSCRIPT_LINE_CAP_BYTES, TRANSCRIPT_READ_CAP_BYTES)

    def test_A_COLD_READ_AND_MANY_SMALL_WINDOWS_AGREE_EXACTLY(self):
        """⭐ The property that validates the whole streaming group-by, and the one I ran against
        the real 2,201-record transcript: 115 incremental windows totalled byte-for-byte what one
        cold read produced. A group-by with a resumable boundary either has this property or it
        is silently losing and double-counting at every window edge."""
        with tempfile.TemporaryDirectory() as d:
            records = []
            for i in range(12):
                records.append(envelope(text=("x" * (120 + i * 40)), output_tokens=60 + i,
                                        msg_id="r%d" % i))
                records.append(_CLOSER)
            path = write_jsonl(os.path.join(d, "t.jsonl"), records)

            cold = read_samples(path, session_id="s", cap=10_000_000)
            cold_total = (len(cold.samples), sum(s.estimate for s in cold.samples),
                          sum(s.actual for s in cold.samples))

            cursor, n, est, act = None, 0, 0, 0
            for _ in range(500):
                step = read_samples(path, session_id="s", cursor=cursor, cap=1200)
                for s in step.samples:
                    n += 1; est += s.estimate; act += s.actual
                if cursor is not None and step.cursor.offset == cursor.offset and not step.samples:
                    break
                cursor = step.cursor
        self.assertEqual(cold_total[0], 12, "the fixture must produce every response")
        self.assertGreater(n, 0, "the incremental read produced nothing at all")
        self.assertEqual((n, est, act), cold_total,
                         "the incremental read and the cold read disagree — the window boundary "
                         "is losing or double-counting responses")


# ------------------------------------------------------------------ 3 · the id validates the offset

class AnOversizedRecordDoesNotWEDGETheSessionsCalibration(unittest.TestCase):
    """⛔ REVIEW F2. `read_samples` answers a line longer than the whole read cap with a REASON
    *and* an advanced cursor plus a non-zero `consumed` — written that way so the reader does not
    sit on it. `observe` returned on `not readable` BEFORE saving the cursor, so that progress was
    thrown away and **every later hook call in the session re-read the same bytes and reported the
    same reason.** The instrument looked like it was running.

    ⭐ §7g, in the reason channel: *"the read could not happen"* and *"the read made progress and
    produced nothing"* are two answers, and one field was carrying both."""

    def test_the_cursor_ADVANCES_past_a_line_longer_than_the_read_cap(self):
        from mokata.adapters.transcript import TRANSCRIPT_READ_CAP_BYTES
        from mokata.govern.calibration import load_cursor, observe

        root, path = a_repo()
        self.addCleanup(shutil.rmtree, root, True)
        with io.open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("x" * (TRANSCRIPT_READ_CAP_BYTES + 10_000))   # ⚠ NO trailing newline

        first = observe(root, transcript_path=path, session_id="s")
        self.assertFalse(first.reading.readable, "fixture assumption: this read has a reason")
        self.assertGreater(first.reading.consumed, 0,
                           "fixture assumption: the read made PROGRESS despite the reason")
        saved = load_cursor(root, path)
        self.assertIsNotNone(saved, "the cursor was discarded because a reason was set")
        self.assertEqual(saved.offset, first.reading.cursor.offset)

    def test_and_a_REAL_record_after_it_is_then_MEASURED(self):
        """The assertion that matters: the session is not merely unwedged, it CALIBRATES again.
        Before the fix the appended response was never reached."""
        from mokata.adapters.transcript import TRANSCRIPT_READ_CAP_BYTES
        from mokata.govern.calibration import observe

        root, path = a_repo()
        self.addCleanup(shutil.rmtree, root, True)
        with io.open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("x" * (TRANSCRIPT_READ_CAP_BYTES + 10_000))
        observe(root, transcript_path=path, session_id="s")        # the wedge, if there is one
        with io.open(path, "a", encoding="utf-8", newline="") as fh:
            fh.write("\n" + json.dumps(envelope(text="a" * 400, output_tokens=100, msg_id="r1"))
                     + "\n" + json.dumps(_CLOSER) + "\n")

        second = observe(root, transcript_path=path, session_id="s")
        self.assertTrue(second.reading.readable, second.reason)
        self.assertEqual(1, len(second.reading.samples),
                         "the response after the oversized line was never reached: %r"
                         % (second.reason,))

    def test_an_UNREADABLE_transcript_still_saves_NO_cursor(self):
        """The negative control, and it is what keeps the fix narrow. A missing file, a bad
        session id, a stat failure — all answer with `consumed == 0`, so moving the save above
        the readability branch must not start persisting a cursor for any of them (§7f: a fix
        that also fires on the healthy case is a fix somebody reverts)."""
        from mokata.govern.calibration import load_cursor, observe

        root, path = a_repo()
        self.addCleanup(shutil.rmtree, root, True)
        missing = os.path.join(root, "not-there.jsonl")
        out = observe(root, transcript_path=missing, session_id="s")
        self.assertFalse(out.reading.readable)
        self.assertEqual(0, out.reading.consumed)
        self.assertIsNone(load_cursor(root, missing))


class TheIdValidatesTheOffset(unittest.TestCase):

    def _two_records(self, d):
        return write_jsonl(os.path.join(d, "t.jsonl"),
                           [envelope(text="a" * 400, output_tokens=90),
                            envelope(text="b" * 400, output_tokens=95)])

    def test_a_matching_session_resumes_from_the_cursor(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._two_records(d)
            first = read_samples(path, session_id="s1")
            self.assertEqual(len(first.samples), 2)
            again = read_samples(path, session_id="s1", cursor=first.cursor)
            self.assertEqual(again.samples, [])
            self.assertEqual(again.records, 0)
            self.assertTrue(again.readable)

    def test_a_DIFFERENT_session_id_restarts_the_read(self):
        """An offset whose identity does not check out is a guess about someone else's file."""
        with tempfile.TemporaryDirectory() as d:
            path = self._two_records(d)
            spent = read_samples(path, session_id="s1").cursor
            fresh = read_samples(path, session_id="s2", cursor=spent)
            self.assertEqual(len(fresh.samples), 2)
            self.assertEqual(fresh.cursor.session_id, "s2")

    def test_a_file_SHORTER_than_the_offset_restarts_the_read(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._two_records(d)
            spent = read_samples(path, session_id="s1").cursor
            write_jsonl(path, [envelope(text="c" * 400, output_tokens=97)])   # replaced, smaller
            again = read_samples(path, session_id="s1", cursor=spent)
            self.assertEqual(len(again.samples), 1)
            self.assertEqual(again.cursor.offset, os.path.getsize(path))

    def test_a_PARTIAL_last_line_is_left_for_the_next_read(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.jsonl")
            whole = json.dumps(envelope(text="a" * 400, output_tokens=90))
            closer = json.dumps(_CLOSER)
            half = json.dumps(envelope(text="b" * 400, output_tokens=95))
            with io.open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(whole + "\n" + closer + "\n" + half[:40])   # truncated mid-record
            first = read_samples(path, session_id="s1")
            self.assertEqual(len(first.samples), 1)
            self.assertEqual(first.records, 2, "the partial line must not be parsed")
            with io.open(path, "a", encoding="utf-8", newline="") as fh:
                fh.write(half[40:] + "\n" + closer + "\n")           # the rest arrives
            second = read_samples(path, session_id="s1", cursor=first.cursor)
            self.assertEqual(len(second.samples), 1, "the completed record is read exactly once")

    def test_the_read_cap_bounds_one_call_and_the_cursor_still_advances(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_jsonl(os.path.join(d, "t.jsonl"),
                               [envelope(text="a" * 400, output_tokens=90) for _ in range(8)])
            one = read_samples(path, session_id="s1", cap=900)
            self.assertTrue(one.truncated)
            self.assertLess(len(one.samples), 8)
            self.assertGreater(one.cursor.offset, 0)
            seen = len(one.samples)
            cursor = one.cursor
            for _ in range(20):
                nxt = read_samples(path, session_id="s1", cursor=cursor, cap=900)
                seen += len(nxt.samples)
                cursor = nxt.cursor
                if not nxt.truncated and not nxt.samples:
                    break
            self.assertEqual(seen, 8, "a capped read loses nothing — it continues")

    def test_an_OVERSIZED_line_is_counted_rather_than_hidden(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_jsonl(os.path.join(d, "t.jsonl"),
                               ["x" * (TRANSCRIPT_LINE_CAP_BYTES + 10),
                                json.dumps(envelope(text="a" * 400, output_tokens=90))])
            reading = read_samples(path, session_id="s1", cap=2 * TRANSCRIPT_LINE_CAP_BYTES)
            self.assertEqual(reading.records, 3, "the oversized line, the response, the closer")
            self.assertEqual(reading.usable, 1)

    def test_an_absent_or_unreadable_transcript_gives_a_REASON_not_an_empty_success(self):
        with tempfile.TemporaryDirectory() as d:
            gone = read_samples(os.path.join(d, "nope.jsonl"), session_id="s")
        self.assertFalse(gone.readable)
        self.assertIn("not readable", gone.reason)
        self.assertEqual(gone.samples, [])
        none = read_samples(None)
        self.assertFalse(none.readable)
        self.assertIsNotNone(none.reason)


# ---------------------------------------------- 4 · three different nothings, three answers

class NothingNewAndNothingUnderstoodAreDifferentAnswers(unittest.TestCase):

    def test_an_empty_transcript_is_readable_with_no_records(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.jsonl")
            io.open(path, "w", encoding="utf-8").close()
            reading = read_samples(path, session_id="s")
        self.assertTrue(reading.readable)
        self.assertEqual((reading.records, reading.usable), (0, 0))
        self.assertFalse(reading.schema_blind)

    def test_a_RENAMED_usage_FIELD_is_schema_blind(self):
        """§7e. The JSONL is not a public contract, so the day Claude Code renames `usage` the
        calibration must announce that it understood nothing — not report an empty measurement
        indistinguishable from a quiet afternoon. The records here are still RECOGNIZABLY
        assistant messages; it is the token count that moved, which is the realistic shape of
        such a change."""
        moved = [envelope(text="a" * 400), envelope(text="b" * 400)]
        for rec in moved:
            rec["message"]["token_usage"] = rec["message"].pop("usage")
        with tempfile.TemporaryDirectory() as d:
            path = write_jsonl(os.path.join(d, "t.jsonl"), moved)
            reading = read_samples(path, session_id="s")
        self.assertTrue(reading.readable)
        self.assertEqual((reading.responses, reading.priced, reading.usable), (2, 0, 0))
        self.assertTrue(reading.schema_blind)

    def test_a_window_of_TOOL_USE_messages_is_NOT_schema_blind(self):
        """⛔ The false-positive class this flag was keyed wrong for in its first draft. An
        assistant message whose content is all `tool_use` is an ordinary minute in an ordinary
        session: it has a real token count and is simply not calibratable. A detector that cried
        "the schema moved" at it would be a detector whose output is almost all noise."""
        with tempfile.TemporaryDirectory() as d:
            path = write_jsonl(os.path.join(d, "t.jsonl"),
                               [envelope(blocks=[{"type": "tool_use", "id": "t", "name": "Edit",
                                                  "input": {}}], output_tokens=300)])
            reading = read_samples(path, session_id="s")
        self.assertEqual((reading.responses, reading.priced, reading.usable), (1, 1, 0))
        self.assertFalse(reading.schema_blind)

    def test_a_window_of_USER_turns_is_NOT_schema_blind_either(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_jsonl(os.path.join(d, "t.jsonl"),
                               [envelope(role="user"), envelope(role="user")])
            reading = read_samples(path, session_id="s")
        self.assertEqual(reading.responses, 0, "user turns are not responses")
        self.assertFalse(reading.schema_blind)

    def test_an_empty_transcript_is_NOT_schema_blind_which_is_what_makes_the_flag_mean_anything(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.jsonl")
            io.open(path, "w", encoding="utf-8").close()
            self.assertFalse(read_samples(path, session_id="s").schema_blind)

    def test_observe_gives_each_nothing_its_OWN_reason(self):
        root, path = a_repo()
        write_jsonl(path, [envelope(text="a" * 1200, output_tokens=280)])
        good = observe(root, transcript_path=path, session_id="s1")
        self.assertTrue(good.recorded)

        nothing_new = observe(root, transcript_path=path, session_id="s1")
        self.assertFalse(nothing_new.recorded)
        self.assertIn("no new transcript records", nothing_new.reason)

        moved = envelope(text="a" * 1200)
        moved["message"]["token_usage"] = moved["message"].pop("usage")
        with io.open(path, "a", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps(moved) + "\n" + json.dumps(_CLOSER) + "\n")
        blind = observe(root, transcript_path=path, session_id="s1")
        self.assertFalse(blind.recorded)
        self.assertIn("NOT ONE carried a token count", blind.reason)

        with io.open(path, "a", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps(envelope(role="user")) + "\n")
        uncalibratable = observe(root, transcript_path=path, session_id="s1")
        self.assertFalse(uncalibratable.recorded)
        self.assertIn("calibratable", uncalibratable.reason)

        self.assertEqual(len({nothing_new.reason, blind.reason, uncalibratable.reason}), 3,
                         "three different nothings must not share one sentence")

    def test_observe_never_raises_on_a_directory_handed_in_as_a_transcript(self):
        root, _ = a_repo()
        outcome = observe(root, transcript_path=root, session_id="s1")
        self.assertFalse(outcome.recorded)
        self.assertIsNotNone(outcome.reason)


# ---------------------------------------------------------- 5 · a quotient of totals, not a mean

class TheRatioIsAQuotientOfTotals(unittest.TestCase):

    def test_fold_sums_per_model(self):
        rows = fold([TokenSample("a", 100, 90), TokenSample("a", 300, 310),
                     TokenSample("b", 400, 380)])
        self.assertEqual(rows["a"].samples, 2)
        self.assertEqual((rows["a"].estimate_total, rows["a"].actual_total), (400, 400))
        self.assertEqual(rows["b"].samples, 1)

    def test_the_totals_answer_DIFFERS_from_the_mean_of_ratios_and_this_is_the_totals_one(self):
        """One tiny message with a wild ratio and one large message with a calm one. A mean of
        ratios puts them on equal footing; a quotient of totals weights each observation by the
        size it actually contributed to the budgets."""
        samples = [TokenSample("m", 4, 12), TokenSample("m", 1000, 900)]
        mean_of_ratios = ((12 / 4) + (900 / 1000)) / 2            # 1.95
        rows = fold(samples)
        self.assertAlmostEqual(rows["m"].ratio, 912 / 1004)        # 0.908...
        self.assertLess(rows["m"].ratio, 1.0)
        self.assertGreater(mean_of_ratios, 1.0)
        self.assertFalse(rows["m"].over_margin,
                         "the mean-of-ratios reading would have cried drift on one short line")

    def test_below_the_floor_the_ratio_is_NONE_and_never_a_number(self):
        small = ModelCalibration("m", samples=1, estimate_total=CALIBRATION_MIN_ESTIMATE_TOKENS - 1,
                                 actual_total=9999)
        self.assertIsNone(small.ratio)
        self.assertFalse(small.over_margin,
                         "an unpublished ratio cannot be over a margin it has not cleared")
        at_floor = ModelCalibration("m", samples=1,
                                    estimate_total=CALIBRATION_MIN_ESTIMATE_TOKENS,
                                    actual_total=CALIBRATION_MIN_ESTIMATE_TOKENS)
        self.assertEqual(at_floor.ratio, 1.0)

    def test_the_margin_fires_ABOVE_the_ratio_and_not_AT_it(self):
        exact = ModelCalibration("m", 1, 1000, 1000)
        self.assertEqual(exact.ratio, CALIBRATION_MARGIN_RATIO)
        self.assertFalse(exact.over_margin, "the margin is 'estimate >= actual', inclusive")
        over = ModelCalibration("m", 1, 1000, 1001)
        self.assertTrue(over.over_margin)

    def test_a_zero_estimate_total_yields_no_ratio_rather_than_a_division(self):
        self.assertIsNone(ModelCalibration("m", 0, 0, 0).ratio)


# ------------------------------------- 6 · the calibration survives the memory counters (§7j)

class TheCalibrationSectionSurvivesTheMemoryCOUNTERS(unittest.TestCase):

    def test_merge_counters_keeps_every_key_it_did_not_come_to_change(self):
        from mokata.memory.store import merge_counters
        out = merge_counters({"reads": 2, "writes": 1,
                              CALIBRATION_STATS_KEY: {"m": {"samples": 3}},
                              "something_from_2027": True}, reads=1)
        self.assertEqual(out["reads"], 3)
        self.assertEqual(out[CALIBRATION_STATS_KEY], {"m": {"samples": 3}})
        self.assertTrue(out["something_from_2027"])

    def test_merged_stats_keeps_the_counters_and_adds_to_the_rows(self):
        first = merged_stats({"reads": 5, "writes": 2}, fold([TokenSample("m", 100, 90)]))
        self.assertEqual((first["reads"], first["writes"]), (5, 2))
        second = merged_stats(first, fold([TokenSample("m", 300, 280)]))
        row = second[CALIBRATION_STATS_KEY]["m"]
        self.assertEqual((row["samples"], row["estimate_total"], row["actual_total"]),
                         (2, 400, 370))
        self.assertEqual((second["reads"], second["writes"]), (5, 2))

    def test_the_PRE_FIX_MUTATOR_SHAPE_LOSES_IT_which_is_why_this_class_exists(self):
        """The planted offender (§7i). `_persist_stats` returned exactly this literal, and it was
        a latent §7j for five releases because the file had nothing else in it to lose."""
        def pre_fix(cur, reads=1, writes=0):
            return {"reads": int((cur or {}).get("reads", 0) or 0) + reads,
                    "writes": int((cur or {}).get("writes", 0) or 0) + writes}
        before = merged_stats({"reads": 1, "writes": 0}, fold([TokenSample("m", 400, 390)]))
        self.assertIn(CALIBRATION_STATS_KEY, before)
        self.assertNotIn(CALIBRATION_STATS_KEY, pre_fix(before))

    def test_END_TO_END_a_counted_memory_read_does_not_erase_the_measurement(self):
        from mokata.config import Surface
        from mokata.memory import MemoryStore
        root, path = a_repo()
        write_jsonl(path, [envelope(text="a" * 4000, output_tokens=900)])
        self.assertTrue(observe(root, transcript_path=path, session_id="s1").recorded)
        self.assertIn("claude-test", calibration_of(stats_of(root)))

        MemoryStore.from_surface(Surface.load(root))._bump_read(1)

        after = stats_of(root)
        self.assertEqual(after["reads"], 1, "the counter must still count")
        self.assertIn("claude-test", calibration_of(after),
                      "and must not have taken the measurement with it")


# ------------------------------------------------------------------- 7 · the loop actually closes

class TheLoopActuallyCLOSES(unittest.TestCase):
    """The reason the stage exists. No test in this class calls `log_calibration`."""

    def test_observe_writes_a_ledger_record_carrying_a_REAL_actual(self):
        root, path = a_repo()
        write_jsonl(path, [envelope(text="a" * 4000, output_tokens=900)])
        self.assertTrue(observe(root, transcript_path=path, session_id="s1").recorded)
        rows = ledger_calibrations(root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["context"], "transcript:claude-test")
        self.assertEqual(rows[0]["actual"], 900)
        self.assertIsNotNone(rows[0]["ratio"])

    def test_the_PRE_STAGE_production_path_logs_an_estimate_with_NO_actual(self):
        """The measurement this stage's existence rests on, taken from the shipped path rather
        than asserted: `log_bootstrap_calibration` is R11's one production caller, and the row it
        writes has no `actual` for `calibration_drift_findings` to compare against. That is the
        state of the tree before `observe`, and it is why the finding could not fire."""
        from mokata.config import Surface
        from mokata.govern import log_bootstrap_calibration
        root, _ = a_repo()
        log_bootstrap_calibration(Surface.load(root), 1234)
        rows = ledger_calibrations(root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["estimate"], 1234)
        self.assertNotIn("actual", rows[0])

    def test_DIAGNOSE_reports_drift_after_observe_ALONE(self):
        """⭐ The whole stage in one assertion. An over-margin transcript goes in; a
        `calibration-drift` finding comes out of `doctor`; and nothing between the two was done
        by hand."""
        from mokata.config import Surface
        from mokata.govern import diagnose
        root, path = a_repo()
        # 4000 chars => estimate 1000; 1100 real output tokens => ratio 1.10, margin blown.
        write_jsonl(path, [envelope(text="a" * 4000, output_tokens=1100)])
        self.assertTrue(observe(root, transcript_path=path, session_id="s1").recorded)
        findings = [f for f in diagnose(Surface.load(root)).findings
                    if f.code == "calibration-drift"]
        self.assertEqual(len(findings), 1, "the finding R11 shipped must finally fire")
        self.assertIn("transcript:claude-test", findings[0].detail)

    def test_a_HEALTHY_margin_produces_no_drift_finding(self):
        from mokata.config import Surface
        from mokata.govern import diagnose
        root, path = a_repo()
        write_jsonl(path, [envelope(text="a" * 4000, output_tokens=800)])   # ratio 0.80
        self.assertTrue(observe(root, transcript_path=path, session_id="s1").recorded)
        self.assertFalse(any(f.code == "calibration-drift"
                             for f in diagnose(Surface.load(root)).findings))

    def test_a_cold_repo_reports_neither_drift_nor_a_calibration(self):
        from mokata.config import Surface
        from mokata.govern import diagnose
        root, _ = a_repo()
        codes = {f.code for f in diagnose(Surface.load(root)).findings}
        self.assertNotIn("calibration-drift", codes)
        self.assertNotIn("token-calibration", codes)

    def test_the_cursor_is_persisted_so_a_second_hook_call_does_not_recount(self):
        root, path = a_repo()
        write_jsonl(path, [envelope(text="a" * 4000, output_tokens=900)])
        observe(root, transcript_path=path, session_id="s1")
        cursor = load_cursor(root, path)
        self.assertIsNotNone(cursor)
        self.assertEqual(cursor.session_id, "s1")
        self.assertEqual(cursor.offset, os.path.getsize(path))
        observe(root, transcript_path=path, session_id="s1")
        row = calibration_of(stats_of(root))["claude-test"]
        self.assertEqual(row.samples, 1, "the same window must not be folded twice")

    def test_a_lost_cursor_cannot_move_the_RATIO_even_though_it_recounts(self):
        """Why `save_cursor` is SUPPRESS_OK rather than a loud degrade: both totals move by the
        same window, so the quotient is identical. Only `samples` inflates."""
        root, path = a_repo()
        write_jsonl(path, [envelope(text="a" * 4000, output_tokens=900)])
        observe(root, transcript_path=path, session_id="s1")
        first = calibration_of(stats_of(root))["claude-test"]
        os.remove(load_cursor.__globals__["cursor_path"](root, path))
        observe(root, transcript_path=path, session_id="s1")
        second = calibration_of(stats_of(root))["claude-test"]
        self.assertEqual(second.samples, 2, "the window really was folded twice")
        self.assertEqual(first.ratio, second.ratio)


# --------------------------------------- 8 · the PostToolUse hook IS the production caller

class AndThePostToolUseHookIsTheProductionCALLER(unittest.TestCase):
    """§7i's sibling: a mechanism nothing runs grades nothing, and fifteen of stage 00's
    reversals were exactly that. So the hook is RUN here, not read."""

    def _run_hook(self, root, payload):
        import io as _io
        import sys
        from mokata.hook_cli import dirty_track_main
        saved = sys.stdin
        sys.stdin = _io.StringIO(json.dumps(payload))
        try:
            return dirty_track_main([])
        finally:
            sys.stdin = saved

    def test_the_hook_records_a_calibration_from_the_envelopes_transcript_path(self):
        root, path = a_repo()
        write_jsonl(path, [envelope(text="a" * 4000, output_tokens=900)])
        rc = self._run_hook(root, {"cwd": root, "session_id": "s1", "transcript_path": path,
                                   "tool_input": {"file_path": os.path.join(root, "a.py")}})
        self.assertEqual(rc, 0)
        self.assertIn("claude-test", calibration_of(stats_of(root)))

    def test_it_runs_EVEN_WHEN_THERE_IS_NOTHING_TO_MARK_DIRTY(self):
        """The dirty-set's `not paths` exit is someone else's guard. A PostToolUse call with no
        target path is still a turn whose output is measurable, and tying the sample set to
        another feature's early return is how a measurement silently becomes a subset."""
        root, path = a_repo()
        write_jsonl(path, [envelope(text="a" * 4000, output_tokens=900)])
        rc = self._run_hook(root, {"cwd": root, "session_id": "s1", "transcript_path": path})
        self.assertEqual(rc, 0)
        self.assertIn("claude-test", calibration_of(stats_of(root)))

    def test_an_envelope_with_no_transcript_path_is_a_clean_no_op(self):
        root, path = a_repo()
        write_jsonl(path, [envelope(text="a" * 4000, output_tokens=900)])
        rc = self._run_hook(root, {"cwd": root, "session_id": "s1"})
        self.assertEqual(rc, 0)
        self.assertEqual(calibration_of(stats_of(root) or {}), {})

    def test_an_UNINITIALIZED_repo_gets_no_mokata_dir_out_of_this(self):
        """A hook that fires in a tree the user never initialized must not create `.mokata/` —
        the rule `session_start_main`'s WT-ROOT comment states from the other side."""
        with tempfile.TemporaryDirectory() as d:
            path = write_jsonl(os.path.join(d, "t.jsonl"),
                               [envelope(text="a" * 4000, output_tokens=900)])
            from mokata.govern.calibration import cursor_dir
            rc = self._run_hook(d, {"cwd": d, "session_id": "s1", "transcript_path": path})
            self.assertEqual(rc, 0)
            self.assertFalse(os.path.exists(cursor_dir(d)))

    def test_the_hook_survives_an_unparseable_envelope_and_a_missing_transcript(self):
        root, _ = a_repo()
        self.assertEqual(self._run_hook(root, {"cwd": root, "session_id": "s1",
                                               "transcript_path": "/nope/missing.jsonl"}), 0)


# ------------------------------------------- 9 · clause (c) — the trail is KEPT and MARKED

class _DiesOnTheSecondTask:
    """A runner that really runs task one, then goes away — the deterministic version of a
    subagent harness dropping mid-batch."""

    def __init__(self):
        self.ran = []

    def run(self, task, **_kw):
        from mokata.execmode import SubagentUnavailable, TaskResult
        if self.ran:
            raise SubagentUnavailable("the harness went away")
        self.ran.append(task.id)
        return TaskResult(task.id, True, f"done {task.id}", output="out",
                          input_tokens=700, output_tokens=300, seen_context=task.context)


class TheDegradedTrailIsKeptAndMarked(unittest.TestCase):

    def _run(self, budget=None):
        from mokata.execmode import PARALLEL, ExecutionChoice, Task, run_tasks
        tasks = [Task("t1", "do t1", context="c1"), Task("t2", "do t2", context="c2")]
        choice = ExecutionChoice(PARALLEL, isolation=True, fanout=False)
        return run_tasks(tasks, choice, runner=_DiesOnTheSecondTask(), budget=budget)

    def test_the_REAL_partial_spend_is_reported_instead_of_zero(self):
        """⛔ `tracker.entries.clear()` reported $0.0000 for a batch that had spent 1000 tokens."""
        res = self._run()
        self.assertTrue(res.degraded)
        self.assertEqual(res.actual_input, 700)
        self.assertEqual(res.actual_output, 300)
        self.assertEqual(res.actual_total, 1000)
        self.assertGreater(res.actual_cost, 0.0)

    def test_the_accounting_says_it_is_PARTIAL(self):
        res = self._run()
        self.assertTrue(res.accounting_is_partial)

    def test_a_degrade_that_ran_NOTHING_is_honestly_zero_and_NOT_partial(self):
        """The other side of the same distinction: `degraded` alone never meant "there is a
        partial number here", and `accounting_is_partial` is derived from both facts rather than
        stored as a third (§7f)."""
        from mokata.execmode import PARALLEL, ExecutionChoice, Task, run_tasks
        res = run_tasks([Task("t1", "x", context="c")],
                        ExecutionChoice(PARALLEL, isolation=True), runner=None)
        self.assertTrue(res.degraded)
        self.assertEqual(res.actual_total, 0)
        self.assertFalse(res.accounting_is_partial)

    def test_a_budget_blown_BEFORE_the_degrade_is_no_longer_reported_as_within_budget(self):
        """The consequence that makes this clause a correctness fix rather than a metrics one:
        `within_budget` is `actual_total <= budget`, so an erased trail made every degraded run
        compliant with every budget."""
        res = self._run(budget=500)
        self.assertFalse(res.within_budget)

    def test_the_SIMULATED_fallback_still_contributes_nothing(self):
        """R-13F's principle is preserved, not traded away: the sequential retry after a degrade
        executes nothing, so the 1000 tokens above are the parallel work and only that."""
        res = self._run()
        self.assertEqual(res.actual_total, 1000)
        self.assertTrue(all(r.simulated for r in res.results if r.task_id == "t2"))

    def test_the_trail_marks_its_rows_and_the_report_says_PARTIAL(self):
        from mokata.govern import TokenTracker
        tracker = TokenTracker()
        tracker.add("task:t1", input_tokens=700, output_tokens=300)
        self.assertFalse(tracker.degraded)
        self.assertNotIn("PARTIAL", tracker.report())
        self.assertEqual(tracker.mark_degraded(), 1)
        self.assertTrue(tracker.degraded)
        self.assertTrue(all(e.degraded for e in tracker.entries))
        self.assertIn("PARTIAL", tracker.report())
        self.assertEqual(tracker.total_input, 700, "marking is not clearing")

    def test_the_degrade_ledger_row_says_how_many_rows_it_kept(self):
        from mokata.execmode import PARALLEL, ExecutionChoice, Task, run_tasks
        from mokata.govern.ledger import AuditLedger
        root, _ = a_repo()
        led = AuditLedger.from_mokata_dir(os.path.join(root, ".mokata"))
        run_tasks([Task("t1", "a", context="c1"), Task("t2", "b", context="c2")],
                  ExecutionChoice(PARALLEL, isolation=True, fanout=False),
                  runner=_DiesOnTheSecondTask(), ledger=led)
        rows = [e for e in led.entries() if e.get("kind") == "exec_degrade"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["accounted_rows_kept"], 1)


# ------------------------------------------------- 10 · the measured constant has a reader

class TheMeasuredConstantHasAReader(unittest.TestCase):
    """A field every writer populates and no reader ever sees is
    `DEGRADE-DETAIL-IS-COMPUTED-BY-38-SITES-AND-SHOWN-BY-NONE`, which is already a row."""

    def _findings(self, root):
        from mokata.config import Surface
        from mokata.govern.doctor import token_calibration_findings
        return token_calibration_findings(Surface.load(root))

    def test_the_aggregate_is_reported_whether_or_not_it_is_in_trouble(self):
        root, path = a_repo()
        write_jsonl(path, [envelope(text="a" * 4000, output_tokens=800)])
        observe(root, transcript_path=path, session_id="s1")
        found = self._findings(root)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].code, "token-calibration")
        self.assertEqual(found[0].severity, "info")
        self.assertIn("0.80", found[0].detail)
        self.assertIn("margin holding", found[0].detail)

    def test_an_over_margin_aggregate_says_BLOWN(self):
        root, path = a_repo()
        write_jsonl(path, [envelope(text="a" * 4000, output_tokens=1100)])
        observe(root, transcript_path=path, session_id="s1")
        self.assertIn("margin BLOWN", self._findings(root)[0].detail)

    def test_under_the_floor_it_says_so_rather_than_printing_a_noisy_ratio(self):
        root, path = a_repo()
        write_jsonl(path, [envelope(text="a" * 40, output_tokens=99)])   # estimate 10
        observe(root, transcript_path=path, session_id="s1")
        detail = self._findings(root)[0].detail
        self.assertIn("under the", detail)
        self.assertIn("floor", detail)
        self.assertNotIn("actual/estimate", detail)

    def test_it_is_a_DIFFERENT_finding_from_the_per_window_warning(self):
        """§7f does not condemn the pair because they are not the same fact: one is the running
        constant, the other is one window that blew the margin. The aggregate appears when
        nothing is wrong, which no warning ever does."""
        from mokata.config import Surface
        from mokata.govern import diagnose
        root, path = a_repo()
        write_jsonl(path, [envelope(text="a" * 4000, output_tokens=800)])
        observe(root, transcript_path=path, session_id="s1")
        codes = [f.code for f in diagnose(Surface.load(root)).findings]
        self.assertIn("token-calibration", codes)
        self.assertNotIn("calibration-drift", codes)

    def test_a_malformed_calibration_row_is_dropped_rather_than_raising_into_doctor(self):
        self.assertEqual(calibration_of({CALIBRATION_STATS_KEY: {"m": "not a dict"}}), {})
        self.assertEqual(calibration_of({CALIBRATION_STATS_KEY: "not a dict"}), {})
        self.assertEqual(calibration_of(None), {})
        self.assertEqual(calibration_of({CALIBRATION_STATS_KEY: {"m": {"samples": "x"}}}), {})


if __name__ == "__main__":
    unittest.main()
