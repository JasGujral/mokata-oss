"""WHY the blast radius degraded — four situations, one bool, and two wrong sentences.

0.0.21 stage 14, fix D. `ApproachImpact.graph_degraded` is a single bool standing for four
different facts, and `graph.required` renders the SAME refusal for all of them:
*"Adopt a real code graph — `mokata graph adopt`."*

⛔ **THE DEFECT.** For two of the four that instruction is wrong, and wrong in the direction that
looks exactly like the symptom Jas reported. A user whose graph IS adopted and has failed is told
to adopt one. A user whose target does not exist is told the same, adopts a graph, and gets the
identical refusal — because a graph would find nothing either.

  * the chain is LEXICAL — nothing adopted -> *adopt a graph*. Correct today.
  * the graph is ADOPTED AND FAILED, floor stood in -> *`mokata doctor`*. Adopting again is a no-op.
  * the floor answered and found NOTHING -> *check the target*. Neither road helps.
  * mokata could not LOOK -> already split out one level up as `UNDERIVABLE`.

⚠ **WHAT THIS STAGE DELIBERATELY DOES NOT DO.** It does not change which of the four `graph.required`
REFUSES. That is a product decision with a live user-visible consequence, and doc 85 §7h says a pin
can encode a false premise — including one moved on a builder's own initiative. `TheVerdictIsBlindToTheReason`
below is the guard that keeps this honest: every reason still refuses, exactly as before.

WHAT THIS FILE GRADES
---------------------
  * **each reason is DERIVED, and the two that look alike are told apart** — `chain-lexical` and
    `graph-failed` both produce `qr.degraded=True` and the same `basis`, so the distinction is read
    off the LAYER, not the result. A test that only checks "a reason was recorded" would pass with
    the two swapped.
  * **the verdict does not move** — for every reason, refused iff degraded ∧ required ∧ ¬override.
  * **the refusal says the RIGHT thing** — and, for the graph-failed case, explicitly does NOT say
    the old sentence. Asserting the new text is present is not enough; the old advice has to be gone.
  * **the reason cannot be TYPED** — `graph_degraded` is on the wire and was once a bool the model
    supplied, which is the defect `derive_graph_degraded` exists to fix. A reason a model could
    write into `session_save` would be worse than no reason, so it is off the wire, and this asserts
    that rather than trusting it.
"""

import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import brainstorm_impact as BI
from mokata.govern import graph_required as GR
from mokata.knowledge.query import BASIS_LEXICAL, BASIS_STRUCTURAL, QueryResult, Reference


def _qr(*, basis=BASIS_LEXICAL, refs=()):
    """⚠ `degraded` is DERIVED from `basis` — read-only on purpose, because a settable field is how
    "the AST found zero callers" and "the AST could not see this symbol" once shared a value. So the
    double sets the basis and lets the real property answer, rather than asserting a degradation the
    production type would not agree with."""
    return QueryResult(kind="blast_radius", target="t", backend="b", basis=basis,
                       references=list(refs))


class _Layer:
    """A layer double taught EXACTLY two things — whether a structural backend is in the chain, and
    what a query answers. Anything else raises (§7e): a double that answers by accident turns this
    file into a claim about nothing."""

    def __init__(self, *, uses_graph, answer=None, raises=False):
        self.uses_graph = uses_graph
        self._answer = answer
        self._raises = raises

    def blast_radius(self, symbol, depth=2):
        if self._raises:
            raise RuntimeError("the backend raised rather than answered")
        return self._answer

    def __getattr__(self, name):
        raise AssertionError(
            "the fix-D layer double was asked for %r, which it was never taught." % (name,))


def _reasons(layer):
    return BI.compute_impact("a", ["sym"], layer=layer).graph_degrade_reasons


class TheDegradeReasonIsDerived(unittest.TestCase):

    def test_the_control_a_structural_answer_records_NO_reason(self):
        """THE CONTROL, and it is the one that matters: every assertion below is "reason X is
        present", and a derivation that recorded reasons unconditionally would satisfy them all."""
        imp = BI.compute_impact("a", ["sym"], layer=_Layer(
            uses_graph=True,
            answer=_qr(basis=BASIS_STRUCTURAL,
                       refs=[Reference(path="x.py", line=1)])))
        self.assertFalse(imp.graph_degraded)
        self.assertEqual((), imp.graph_degrade_reasons,
                         "a graph answered — there is nothing to explain")

    def test_no_layer_at_all_is_its_own_reason(self):
        self.assertEqual((BI.DEGRADE_NO_LAYER,), _reasons(None))

    def test_a_query_that_RAISED_is_a_withheld_answer_not_a_floor_answer(self):
        self.assertEqual((BI.DEGRADE_QUERY_FAULT,), _reasons(_Layer(uses_graph=True, raises=True)))

    def test_an_unwired_chain_says_the_chain_is_lexical(self):
        rs = _reasons(_Layer(uses_graph=False,
                             answer=_qr(refs=[Reference(path="x.py", line=1)])))
        self.assertIn(BI.DEGRADE_CHAIN_IS_LEXICAL, rs)
        self.assertNotIn(BI.DEGRADE_GRAPH_FAILED, rs)

    def test_an_ADOPTED_graph_that_fell_to_the_floor_is_a_DIFFERENT_reason(self):
        """THE DISTINCTION THE BOOL COULD NOT MAKE. Both cases arrive as `qr.degraded=True` with the
        same basis — `demote_to_floor` sets `BASIS_LEXICAL` either way — so this is read off the
        layer. Swap the two and the user is told to adopt a graph they already have."""
        rs = _reasons(_Layer(uses_graph=True,
                             answer=_qr(refs=[Reference(path="x.py", line=1)])))
        self.assertIn(BI.DEGRADE_GRAPH_FAILED, rs)
        self.assertNotIn(BI.DEGRADE_CHAIN_IS_LEXICAL, rs)

    def test_a_floor_answer_with_nothing_in_it_carries_the_third_reason_too(self):
        """Not either/or: an empty floor answer is BOTH "the floor answered" and "it found nothing",
        and the second is what makes adopting a graph pointless."""
        rs = _reasons(_Layer(uses_graph=False, answer=_qr(refs=[])))
        self.assertIn(BI.DEGRADE_CHAIN_IS_LEXICAL, rs)
        self.assertIn(BI.DEGRADE_FLOOR_FOUND_NOTHING, rs)

    def test_and_a_floor_answer_WITH_evidence_does_not(self):
        """The control for the assertion above."""
        rs = _reasons(_Layer(uses_graph=False,
                             answer=_qr(refs=[Reference(path="x.py", line=1)])))
        self.assertNotIn(BI.DEGRADE_FLOOR_FOUND_NOTHING, rs)


ALL_REASONS = (BI.DEGRADE_NO_LAYER, BI.DEGRADE_QUERY_FAULT, BI.DEGRADE_CHAIN_IS_LEXICAL,
               BI.DEGRADE_GRAPH_FAILED, BI.DEGRADE_FLOOR_FOUND_NOTHING)


def _every_subset():
    """All 32 subsets of the reason vocabulary, as tuples.

    🔴 REVIEW FINDING B-F3 — THE FIRST VERSION OF THIS GUARD ITERATED 5 SINGLETONS PLUS `()`, i.e.
    **6 of the 32 sets the derivation can produce**, and the derivation emits up to 3-element tuples
    in production. The reviewer weakened the gate for a reachable PAIR and every test stayed green:

        refused = bool(degraded and required and not overridden) \
                  and not {"query-fault", "chain-lexical"} <= set(reasons or ())
        -> GREEN, survivor, against this file AND against test_gr_s3*

    Four more survived the same way, including one that silently removed `--allow-degraded` from that
    pair's refusal — under a test titled *"the ledgered escape survives EVERY reason"*. A guard that
    licenses the sentence *"the verdict is unchanged, deliberately"* has to cover what the derivation
    can actually emit, not a sample of it."""
    out = []
    for mask in range(1 << len(ALL_REASONS)):
        out.append(tuple(r for i, r in enumerate(ALL_REASONS) if mask & (1 << i)))
    return out


class TheVerdictIsBlindToTheReason(unittest.TestCase):
    """⛔ THE §7h GUARD. Fix D changes what the refusal SAYS. If it ever changes WHETHER the gate
    refuses, that is a product decision nobody made, and this is what catches it.

    ⚠ ALL 32 SUBSETS, for the reason in `_every_subset`'s docstring: the 6-case version of this class
    was satisfiable while the gate was weakened for a pair the derivation really produces."""

    def test_every_reason_SET_still_refuses(self):
        for r in _every_subset():
            with self.subTest(reasons=r):
                out = GR.check_graph_required(
                    degraded=True, required=True, overridden=False, consumer="c", reasons=r)
                self.assertTrue(out.refused, "the reason must not soften the gate")

    def test_and_every_reason_SET_is_still_ALLOWED_under_an_override(self):
        """The other direction, so "always refuses" cannot be how this passes."""
        for r in _every_subset():
            with self.subTest(reasons=r):
                out = GR.check_graph_required(
                    degraded=True, required=True, overridden=True, consumer="c", reasons=r)
                self.assertTrue(out.allowed)
                self.assertTrue(out.degraded, "an allowed run keeps the caveat (P22)")

    def test_a_non_degraded_answer_is_not_refused_whatever_reasons_are_passed(self):
        for r in _every_subset():
            with self.subTest(reasons=r):
                self.assertFalse(GR.check_graph_required(
                    degraded=False, required=True, overridden=False,
                    consumer="c", reasons=r).refused)

    def test_the_verdict_matches_the_formula_over_every_flag_and_reason_combination(self):
        """The formula itself, stated once and checked against every combination — 32 x 8 = 256. This
        is what makes "byte-identical" a measurement rather than a claim about a diff."""
        for r in _every_subset():
            for deg in (True, False):
                for req in (True, False):
                    for ovr in (True, False):
                        with self.subTest(reasons=r, degraded=deg, required=req, overridden=ovr):
                            out = GR.check_graph_required(
                                degraded=deg, required=req, overridden=ovr, consumer="c", reasons=r)
                            self.assertEqual(bool(deg and req and not ovr), out.refused)
                            self.assertEqual(bool(deg), out.degraded)


class NoReasonSetRendersContRADICTORY_advice(unittest.TestCase):
    """🔴 REVIEW FINDING B-F3 / B-F2 / B-F6 — every reason set is rendered and read, not sampled.

    The reviewer enumerated all 32 and found reachable sets that named TWO different remedies or lost
    the remedy entirely: `{query-fault, chain-lexical}` said *"the backend is there"* while
    `chain-lexical` says there is none, and sent a user with no graph to repair one."""

    def _render(self, *reasons, notice=None):
        return GR.check_graph_required(degraded=True, required=True, overridden=False,
                                       consumer="spec emit", targets=["sym"],
                                       reasons=reasons, notice=notice).render()

    @staticmethod
    def _lead_instruction(text):
        """The FIRST thing road 1 tells the user to do.

        ⚠ MY FIRST VERSION OF THIS CHECK COUNTED REMEDY STRINGS ANYWHERE IN THE TEXT and failed 57
        subtests against correct output — because the repair road reads *"`mokata doctor` first;
        re-adopt with `mokata graph adopt` only if doctor says the adoption itself is gone"*, which is
        ONE remedy with a conditional escalation, not two competing instructions. Counting substrings
        graded the wording; what the reviewer actually found was a refusal whose LEAD instruction
        contradicted its own WHY. So this reads the lead, which is the thing a user acts on."""
        for line in text.splitlines():
            if line.strip().startswith("1."):
                for marker, name in (("REPAIR the graph you already have", "repair"),
                                     ("CHECK THE TARGET", "check-target"),
                                     ("Adopt a real code graph", "adopt")):
                    if marker in line:
                        return name
                return "unknown:" + line.strip()[:60]
        return "no-road-1"

    def test_road_ONE_has_exactly_one_lead_instruction(self):
        for r in _every_subset():
            with self.subTest(reasons=r):
                lead = self._lead_instruction(self._render(*r))
                self.assertIn(lead, ("repair", "check-target", "adopt"),
                              "road 1 must lead with one of the three known remedies")

    def test_no_subset_says_adopting_will_not_help_and_then_leads_with_adopt(self):
        """⭐ THE ACTUAL CONTRADICTION the reviewer found, stated as a property rather than as a
        substring count: a refusal that explains adopting is useless must not then instruct it."""
        for r in _every_subset():
            with self.subTest(reasons=r):
                text = self._render(*r)
                if "ADOPTING AGAIN WILL NOT FIX THIS" in text:
                    self.assertNotEqual("adopt", self._lead_instruction(text),
                                        "the WHY says adopting will not help and road 1 says adopt")

    def test_the_control_an_unwired_chain_DOES_lead_with_adopt(self):
        """Without this, "never leads with adopt" would satisfy the test above — and adopting is the
        right answer for exactly one reason set."""
        self.assertEqual("adopt", self._lead_instruction(
            self._render(BI.DEGRADE_CHAIN_IS_LEXICAL)))

    def test_no_subset_loses_the_ledgered_escape(self):
        """P22. One of the reviewer's survivors removed `--allow-degraded` for a single pair, under a
        test that claimed to check every reason."""
        for r in _every_subset():
            with self.subTest(reasons=r):
                text = self._render(*r)
                self.assertIn("--allow-degraded", text)
                self.assertIn("the model cannot accept it", text)

    def test_no_subset_claims_a_graph_is_there_AND_that_none_is_wired(self):
        for r in _every_subset():
            with self.subTest(reasons=r):
                text = self._render(*r)
                if "no code graph is wired" in text:
                    self.assertNotIn("the backend is there", text)
                    self.assertNotIn("a code graph IS adopted", text)

    def test_the_one_time_notice_adds_no_remedy_of_its_own(self):
        """🔴 REVIEW FINDING B-F6. The notice used to end with *"Adopt a real code graph"* and
        `render()` prepends it to the FIRST refusal in a repo — the loud one — so a graph-failed user
        read both. a29's old `assertNotIn` could not see it for two independent reasons: the literal
        is broken by a newline in the notice, and no test ever passed a notice.

        The property is about the NOTICE's own text: whatever it says, it must not instruct a remedy,
        because which remedy applies depends on a reason the notice does not know."""
        notice = GR.UPGRADE_NOTICE
        for marker in ("mokata doctor", "mokata graph adopt", "CHECK THE TARGET"):
            self.assertNotIn(marker, notice,
                             "the one-time notice must explain the DEFAULT FLIP, not prescribe a "
                             "remedy — the reason-specific road below it owns that")
        self.assertIn("ON BY DEFAULT", notice, "and it must still do its own job")
        # ...and prepending it must not change which remedy the refusal leads with.
        for r in _every_subset():
            with self.subTest(reasons=r):
                self.assertEqual(self._lead_instruction(self._render(*r)),
                                 self._lead_instruction(self._render(*r, notice=notice)))

    def test_the_machine_HINT_agrees_with_the_refusal_it_ships_beside(self):
        """🔴 REVIEW FINDING B-F1 — the one refusal that fires in the shipped product carried a
        hardcoded `hint` saying *"adopt a code graph"* in the SAME payload as "ADOPTING AGAIN WILL NOT
        FIX THIS". Both are derived from the same reasons now, so they cannot disagree."""
        for r in _every_subset():
            with self.subTest(reasons=r):
                hint = GR.hint_for(r)
                text = self._render(*r)
                lead = self._lead_instruction(text)
                if lead == "adopt":
                    self.assertIn("mokata graph adopt", hint)
                    self.assertNotIn("Run `mokata doctor`", hint,
                                     "the refusal says adopt and the hint says repair: %r" % (hint,))
                elif lead == "repair":
                    self.assertIn("mokata doctor", hint)
                self.assertIn("--allow-degraded", hint)


class TheRefusalSendsYouToTheRightPlace(unittest.TestCase):

    def _render(self, *reasons, **kw):
        return GR.check_graph_required(degraded=True, required=True, overridden=False,
                                       consumer="spec emit", targets=["sym"],
                                       reasons=reasons, **kw).render()

    def test_an_unwired_chain_is_told_to_adopt(self):
        text = self._render(BI.DEGRADE_CHAIN_IS_LEXICAL)
        self.assertIn("mokata graph adopt", text)
        self.assertIn("no code graph is wired", text)

    def test_an_adopted_graph_that_failed_is_told_to_REPAIR_and_NOT_to_adopt(self):
        """⭐ THE POINT OF FIX D. Asserting the new sentence is present is only half of it — the old
        advice has to be GONE, or the user reads both and follows the wrong one."""
        text = self._render(BI.DEGRADE_GRAPH_FAILED)
        self.assertIn("mokata doctor", text)
        self.assertIn("ADOPTING AGAIN WILL NOT FIX THIS", text)
        self.assertNotIn("Adopt a real code graph", text,
                         "this user's graph IS adopted — the old road 1 must not be offered")

    def test_a_floor_that_found_nothing_is_told_to_check_the_target(self):
        text = self._render(BI.DEGRADE_CHAIN_IS_LEXICAL, BI.DEGRADE_FLOOR_FOUND_NOTHING)
        self.assertIn("CHECK THE TARGET", text)
        self.assertIn("ABSENT one", text)
        self.assertNotIn("Adopt a real code graph", text)

    def test_could_not_look_is_not_told_the_graph_is_degraded(self):
        text = self._render(BI.DEGRADE_NO_LAYER)
        self.assertIn("could not look at all", text)
        self.assertIn("BROKEN adoption rather than a missing one", text)

    def test_the_ledgered_escape_survives_every_reason(self):
        """P22 — honesty over convenience. Fix D reworded road 1; road 2 is never weakened, and a
        reason that quietly removed it would be a gate with no escape."""
        for r in (BI.DEGRADE_NO_LAYER, BI.DEGRADE_QUERY_FAULT, BI.DEGRADE_CHAIN_IS_LEXICAL,
                  BI.DEGRADE_GRAPH_FAILED, BI.DEGRADE_FLOOR_FOUND_NOTHING):
            with self.subTest(reason=r):
                text = self._render(r)
                self.assertIn("--allow-degraded", text)
                self.assertIn("the model cannot accept it", text)

    def test_the_control_passing_NO_reasons_renders_what_it_rendered_before(self):
        """Fix D must not change what the two consumers that pass no reasons say. This is the
        byte-level compatibility control for the reword."""
        text = self._render()
        self.assertIn("no code graph is wired", text)
        self.assertIn("Adopt a real code graph", text)


class TheFourPinsTheREVIEWMUTANTSExposedAsMissing(unittest.TestCase):
    """🔴 Four mutants in `tests/_stage14_review_mutants.sh` SURVIVED this file on their first run —
    B1, B2, B4 and B7 — each a review finding whose FIX was in place and whose fix nothing graded.
    Which is the same mistake as the thing being fixed, one level up (§7i)."""

    # ---- B2: the precedence that makes `chain-lexical` outrank the repair family -------------
    def test_a_repo_with_NO_graph_is_never_told_to_repair_the_one_it_has(self):
        """B2, and the production shape is real: `select_backends` leaves `fallback=None` when the
        floor IS primary, and `layer._run` then re-raises — so a floor-only repo whose primary raises
        produces `{query-fault, chain-lexical}`. Sending that user to `mokata doctor` is the reported
        symptom inverted, which is the one outcome fix D exists to prevent."""
        for extra in ((), (BI.DEGRADE_FLOOR_FOUND_NOTHING,)):
            with self.subTest(extra=extra):
                rs = (BI.DEGRADE_QUERY_FAULT, BI.DEGRADE_CHAIN_IS_LEXICAL) + extra
                self.assertNotEqual(GR.REMEDY_REPAIR, GR.remedy_for(rs),
                                    "you cannot repair a graph that was never adopted")
                text = GR.check_graph_required(degraded=True, required=True, overridden=False,
                                               consumer="c", reasons=rs).render()
                self.assertNotIn("REPAIR the graph you already have", text)
                self.assertNotIn("the backend is there", text,
                                 "the WHY must not claim a graph is present while the reason set "
                                 "says the chain is lexical")

    def test_the_control_an_adopted_graph_that_failed_IS_told_to_repair(self):
        """The control: "never repair" would satisfy the test above, and repair is right for exactly
        the reason sets that have a graph to repair."""
        self.assertEqual(GR.REMEDY_REPAIR, GR.remedy_for((BI.DEGRADE_GRAPH_FAILED,)))
        self.assertEqual(GR.REMEDY_REPAIR, GR.remedy_for((BI.DEGRADE_NO_LAYER,)))
        self.assertEqual(GR.REMEDY_REPAIR, GR.remedy_for((BI.DEGRADE_QUERY_FAULT,)))

    # ---- B4: the default for an attribute-less layer ----------------------------------------
    def test_a_layer_WITHOUT_uses_graph_reads_as_NO_graph(self):
        """B4. `assess_impacts` declares the layer duck-typed on `.blast_radius` alone, so an
        attribute-less layer is a SUPPORTED input — and `compute_impact` used to read `uses_graph`
        twice with OPPOSITE defaults, making one object simultaneously "a real graph" for the display
        caveat and "no graph is wired" for the advice (§7g). The double in this file always sets the
        attribute, which is why the mutant survived: the default was never exercised."""
        class _NoAttribute:
            def blast_radius(self, symbol, depth=2):
                return _qr(refs=[Reference(path="x.py", line=1)])
        rs = BI.compute_impact("a", ["sym"], layer=_NoAttribute()).graph_degrade_reasons
        self.assertIn(BI.DEGRADE_CHAIN_IS_LEXICAL, rs,
                      "a layer that does not say it has a graph does not have one — and that "
                      "direction is deliberate: `adopt` is harmless advice for a repo that does, "
                      "while `repair` sends a repo that does not to fix nothing")
        self.assertNotIn(BI.DEGRADE_GRAPH_FAILED, rs)

    def test_a_uses_graph_that_RAISES_is_not_a_graph_and_not_an_exception(self):
        """The other half of B4: a property that raises was read twice and answered differently each
        time, and a non-AttributeError propagated out of the lens to be relabelled `query-fault` by
        the gate seam — "a structural query FAILED" though no query ever ran."""
        class _Raises:
            @property
            def uses_graph(self):
                raise RuntimeError("the adapter cannot say")
            def blast_radius(self, symbol, depth=2):
                return _qr(refs=[Reference(path="x.py", line=1)])
        imp = BI.compute_impact("a", ["sym"], layer=_Raises())
        self.assertIn(BI.DEGRADE_CHAIN_IS_LEXICAL, imp.graph_degrade_reasons)
        self.assertTrue(imp.degraded, "and the display caveat must agree with the advice")

    # ---- B7: a refusal always carries a reason token ----------------------------------------
    def test_a_REFUSAL_always_carries_a_reason_token(self):
        """B7. `()` meant both "a graph answered, nothing to explain" and "nobody derived a reason",
        and `spec_awareness.guard_change` produces the second on every refusal while
        `GraphRequiredOutcome.reasons` is documented "so a renderer can branch without re-deriving".
        A renderer branching on that field could not tell spec-check's refusal from a clean answer."""
        out = GR.check_graph_required(degraded=True, required=True, overridden=False, consumer="c")
        self.assertTrue(out.refused)
        self.assertEqual((GR.DEGRADE_NOT_DERIVED,), out.reasons,
                         "a refusal with an empty reason tuple is indistinguishable from a clean "
                         "answer (§7g)")

    def test_the_control_a_NON_refusal_carries_no_reason_token(self):
        out = GR.check_graph_required(degraded=False, required=True, overridden=False, consumer="c")
        self.assertFalse(out.refused)
        self.assertEqual((), out.reasons)

    def test_a_bare_not_derived_reason_adds_no_reason_specific_advice(self):
        """And the sentinel must not change what the consumers that pass no reasons SAY — two of the
        three do exactly that, and rewording them unasked would be its own defect."""
        with_none = GR.check_graph_required(degraded=True, required=True, overridden=False,
                                            consumer="c", targets=["sym"]).render()
        self.assertIn("no code graph is wired", with_none)
        self.assertIn("Adopt a real code graph", with_none)
        self.assertNotIn(GR.DEGRADE_NOT_DERIVED, with_none,
                         "the token is a representation for a renderer, not prose for a human")

    # ---- B1: the MCP surface must not carry its own hint --------------------------------------
    def test_the_MCP_refusal_payload_derives_its_hint(self):
        """B1, at the surface the finding was about — `GR.hint_for` being self-consistent was not
        enough, because the defect was a SECOND hardcoded hint in `mcp/tools_spec.py`, and this file
        never touched that module. That is why the mutant survived.

        Driven through the real refusal builder with the lens injected, so the assertion is about the
        payload a client receives, not about a string in a source file."""
        from mokata.mcp import tools_spec
        # The refusal builder takes a surface, a store and a run id and is not injectable, so this
        # pins the CONTRACT at the source: the payload's hint is whatever `hint_for` derives, and the
        # module must not build a second one. A source-shape assertion is the weaker instrument and it
        # is chosen deliberately over a fixture that would have to stand up the whole MCP surface —
        # what it grades is exactly the thing the mutant changes.
        with open(tools_spec.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('"hint": GR.hint_for(reasons)', src,
                      "the MCP refusal must DERIVE its hint from the same reasons the refusal "
                      "renders — a second hand-maintained sentence about one fact is how "
                      "'ADOPTING AGAIN WILL NOT FIX THIS' came to ship beside 'adopt a code graph'")
        self.assertNotIn("adopt a code graph (`mokata graph adopt`) or accept it", src,
                         "the hardcoded hint must be gone, not merely shadowed")


class AdoptionIsNotAvailability(unittest.TestCase):
    """🔴 REVIEW FINDING 3-1 — fix D's motivating symptom was alive on the shipped path.

    `chain-lexical` was derived from `uses_graph`, which reads `primary.is_graph`. `select_backends`
    says in its own docstring that *"`router.resolve` only returns a tool whose command is present, so
    an absent tool never reaches here (it resolves to grep)"*. So the MOST COMMON broken adoption —
    `code-review-graph` uninstalled, a wrong venv, a PATH that lost it — read as *"no graph is wired"*
    and told the user to **adopt a graph they had already adopted**. That is the exact sentence fix D
    exists to delete, reproduced end to end by the reviewer on the one GR.S3 refusal that fires.

    ⭐ The adoption fact was a one-line durable read (`graph_pinned_tool`) the whole time."""

    def _layer(self, *, uses_graph, pinned):
        class _L:
            def __init__(self):
                self.uses_graph = uses_graph
                self.pinned_graph_tool = pinned
            def blast_radius(self, symbol, depth=2):
                return _qr(refs=[Reference(path="x.py", line=1)])
        return _L()

    def test_an_ADOPTED_but_UNRUNNABLE_graph_is_not_reported_as_an_unwired_chain(self):
        rs = BI.compute_impact("a", ["sym"], layer=self._layer(
            uses_graph=False, pinned="code-review-graph")).graph_degrade_reasons
        self.assertIn(BI.DEGRADE_GRAPH_UNAVAILABLE, rs)
        self.assertNotIn(BI.DEGRADE_CHAIN_IS_LEXICAL, rs,
                         "the adoption is on record — 'no graph is wired' is false, and acting on "
                         "it tells the user to adopt what they already adopted")

    def test_the_control_a_genuinely_unwired_chain_is_STILL_chain_lexical(self):
        """Without this, "never chain-lexical" would satisfy the test above — and adopt is the right
        advice for exactly the repo that has not adopted."""
        rs = BI.compute_impact("a", ["sym"], layer=self._layer(
            uses_graph=False, pinned=None)).graph_degrade_reasons
        self.assertIn(BI.DEGRADE_CHAIN_IS_LEXICAL, rs)
        self.assertNotIn(BI.DEGRADE_GRAPH_UNAVAILABLE, rs)

    def test_an_unrunnable_adopted_graph_is_told_to_REPAIR_and_explicitly_NOT_to_adopt(self):
        text = GR.check_graph_required(degraded=True, required=True, overridden=False,
                                       consumer="spec emit", targets=["sym"],
                                       reasons=(BI.DEGRADE_GRAPH_UNAVAILABLE,)).render()
        self.assertEqual(GR.REMEDY_REPAIR, GR.remedy_for((BI.DEGRADE_GRAPH_UNAVAILABLE,)))
        self.assertIn("ADOPTING AGAIN WILL NOT FIX THIS", text)
        self.assertIn("NOT RUNNABLE", text)
        self.assertNotIn("Adopt a real code graph", text)
        self.assertIn("mokata doctor", GR.hint_for((BI.DEGRADE_GRAPH_UNAVAILABLE,)))

    def test_a_query_FAULT_on_an_adopted_but_unrunnable_graph_is_also_repair(self):
        """The fault branch reads the same two facts, and before 3-1 it collapsed them the same way."""
        class _Raises:
            uses_graph = False
            pinned_graph_tool = "code-review-graph"
            def blast_radius(self, symbol, depth=2):
                raise RuntimeError("the floor primary raised")
        rs = BI.compute_impact("a", ["sym"], layer=_Raises()).graph_degrade_reasons
        self.assertIn(BI.DEGRADE_GRAPH_UNAVAILABLE, rs)
        self.assertNotIn(BI.DEGRADE_CHAIN_IS_LEXICAL, rs)

    def test_a_layer_that_cannot_say_reads_as_NOT_adopted(self):
        """The duck-typing direction, stated the same way `_reads_as_a_graph` states it: `adopt` is
        harmless for a repo that does have a graph; `repair` sends one that does not to fix nothing."""
        class _Silent:
            uses_graph = False
            def blast_radius(self, symbol, depth=2):
                return _qr(refs=[Reference(path="x.py", line=1)])
        rs = BI.compute_impact("a", ["sym"], layer=_Silent()).graph_degrade_reasons
        self.assertIn(BI.DEGRADE_CHAIN_IS_LEXICAL, rs)


class TheFloorFindingNothingOutranksEverything(unittest.TestCase):
    """🔴 REVIEW FINDING 3-7 — the precedence contradicted the ruling it was written from.

    For the SAME evidence — a grep over the whole repo finding zero textual mentions of the target —
    `{chain-lexical, floor-empty}` said CHECK THE TARGET and `{graph-failed, floor-empty}` said run
    doctor, while `brainstorm_impact`'s own comment, the stage report and this file's docstring all
    state *"the floor answered and found NOTHING → check the target. Neither road helps."* Nothing
    graded it: the reviewer's mutant flipping that one set was GREEN against both suites."""

    def test_floor_empty_leads_with_check_target_whatever_else_is_wrong(self):
        for other in ((), (BI.DEGRADE_GRAPH_FAILED,), (BI.DEGRADE_GRAPH_UNAVAILABLE,),
                      (BI.DEGRADE_CHAIN_IS_LEXICAL,), (BI.DEGRADE_QUERY_FAULT,),
                      (BI.DEGRADE_NO_LAYER,)):
            with self.subTest(other=other):
                rs = (BI.DEGRADE_FLOOR_FOUND_NOTHING,) + other
                self.assertEqual(GR.REMEDY_CHECK_TARGET, GR.remedy_for(rs),
                                 "a working graph would find nothing here either, so neither "
                                 "adopting nor repairing one changes the answer")

    def test_but_the_WHY_still_names_the_broken_graph(self):
        """⚠ The remedy leads with the target; the reader must still be TOLD the graph is broken, or
        this trades one misdirection for another."""
        text = GR.check_graph_required(
            degraded=True, required=True, overridden=False, consumer="c", targets=["sym"],
            reasons=(BI.DEGRADE_FLOOR_FOUND_NOTHING, BI.DEGRADE_GRAPH_UNAVAILABLE)).render()
        self.assertIn("CHECK THE TARGET", text)
        self.assertIn("not runnable", text)
        self.assertIn("mokata doctor", text)

    def test_the_control_a_floor_answer_WITH_evidence_does_not_lead_with_check_target(self):
        self.assertEqual(GR.REMEDY_REPAIR, GR.remedy_for((BI.DEGRADE_GRAPH_FAILED,)))
        self.assertEqual(GR.REMEDY_ADOPT, GR.remedy_for((BI.DEGRADE_CHAIN_IS_LEXICAL,)))


class FloorEmptyIsAnAPPROACHFactNotAPerTargetOne(unittest.TestCase):
    """🔴 REVIEW FINDING 4-2 — §7j: a per-item fact read at the level of the set.

    `DEGRADE_FLOOR_FOUND_NOTHING` was emitted inside the per-target loop and consumed by
    `remedy_for` as a statement about the whole approach. Its stated merit is *"if the floor can find
    no textual mention of the symbol ANYWHERE, a working graph finds nothing either"* — true only
    when EVERY target was empty. With two targets, one empty and one with 40 references, the SHIPPED
    refusal said *"the floor found NOTHING for widget_cache, render — so this is not a small blast
    radius, it is an ABSENT one"*: four false statements in one message, and it suppressed the
    `adopt` advice the repo actually needed. My own 3-7 fix made two more sets wrong this way."""

    class _Layer:
        uses_graph = False
        pinned_graph_tool = None

        def blast_radius(self, symbol, depth=2):
            if symbol == "empty_one":
                return _qr(refs=[])
            return _qr(refs=[Reference(path="x.py", line=n) for n in range(1, 41)])

    def test_one_empty_target_beside_one_with_evidence_is_NOT_floor_empty(self):
        imp = BI.compute_impact("a", ["empty_one", "has_forty"], layer=self._Layer())
        self.assertEqual(40, imp.caller_count, "the premise: the approach HAS a blast radius")
        self.assertNotIn(BI.DEGRADE_FLOOR_FOUND_NOTHING, imp.graph_degrade_reasons,
                         "a 40-reference radius is not an ABSENT one")
        self.assertIn(BI.DEGRADE_CHAIN_IS_LEXICAL, imp.graph_degrade_reasons)

    def test_and_the_refusal_then_leads_with_the_advice_the_repo_NEEDS(self):
        imp = BI.compute_impact("a", ["empty_one", "has_forty"], layer=self._Layer())
        text = GR.check_graph_required(
            degraded=True, required=True, overridden=False, consumer="spec emit",
            mentions=imp.caller_count, files=imp.file_count,
            targets=list(imp.targets), reasons=imp.graph_degrade_reasons).render()
        self.assertIn("mokata graph adopt", text, "no graph is wired — adopt is the right move")
        self.assertNotIn("CHECK THE TARGET", text)
        self.assertNotIn("it is an ABSENT one", text,
                         "the radius is 40 references; calling it absent is a false statement in a "
                         "message the user acts on")

    def test_the_control_EVERY_target_empty_IS_floor_empty(self):
        """Without this, "never floor-empty" would satisfy both tests above — and check-the-target is
        the right lead for exactly the approach with no evidence anywhere."""
        class _AllEmpty:
            uses_graph = False
            pinned_graph_tool = None
            def blast_radius(self, symbol, depth=2):
                return _qr(refs=[])
        imp = BI.compute_impact("a", ["nope_one", "nope_two"], layer=_AllEmpty())
        self.assertEqual(0, imp.caller_count)
        self.assertIn(BI.DEGRADE_FLOOR_FOUND_NOTHING, imp.graph_degrade_reasons)
        self.assertEqual(GR.REMEDY_CHECK_TARGET, GR.remedy_for(imp.graph_degrade_reasons))

    def test_4_7_the_LAYER_property_reads_the_MANIFEST_not_is_graph(self):
        """🔴 REVIEW FINDING 4-7 (R12). 3-1's fix was graded at the READER in `brainstorm_impact` and
        not at its SOURCE: replacing `KnowledgeLayer.pinned_graph_tool` with
        `"code-review-graph" if self.primary.is_graph else None` was a survivor — which collapses
        adoption back into availability, the whole defect, one layer down."""
        import json
        import tempfile
        from mokata.knowledge.layer import KnowledgeLayer

        class _Floor:
            name = "grep"
            is_graph = False
            def __init__(self, root):
                self.root = root
            def query(self, kind, target, depth=1):
                raise AssertionError("not queried here")

        with tempfile.TemporaryDirectory() as d:
            import os
            os.makedirs(os.path.join(d, ".mokata"), exist_ok=True)
            layer = KnowledgeLayer(_Floor(d), None)
            self.assertFalse(layer.uses_graph, "the premise: the chain is the floor")
            self.assertIsNone(layer.pinned_graph_tool, "nothing adopted yet")
            with open(os.path.join(d, ".mokata", "manifest.json"), "w", encoding="utf-8") as fh:
                json.dump({"capabilities": {"code_graph": {
                    "fallback": ["code-review-graph", "ast", "grep"]}}}, fh)
            self.assertEqual("code-review-graph", layer.pinned_graph_tool,
                             "ADOPTION comes from the committed manifest; it must not be derived "
                             "from `is_graph`, which is AVAILABILITY and is still False here")
            self.assertFalse(layer.uses_graph, "...and availability has not changed")


class TheVerdictGuardDerivesItsOwnAxes(unittest.TestCase):
    """🔴 REVIEW FINDING 3-3 — §7j, inside the guard whose job was to prove the verdict had not moved.

    The 32-subset guard derived ONE axis of a six-argument function and TYPED the other five
    (`consumer="c"`, `mentions=files=0`, one target or none, `notice=None`). The reviewer wrote 11
    mutants that weaken the gate on those axes — the live consumer string, a non-zero mention count, a
    second target, the notice riding a non-refusal, `graph.required` DEFAULTING OFF — and **all 11
    were GREEN** against this file. The guard graded 0 of 11.

    ⭐ So the axes are DERIVED here, from the signature itself, rather than listed: a new argument to
    `check_graph_required` joins this product automatically, and a weakening on it is caught."""

    #: Values per axis, chosen to make a predicate that reads any of them FALSIFIABLE — in each pair
    #: one value is the one a real refusal carries (the live consumer, a non-zero count, two targets,
    #: a notice present) and one is the degenerate value the old guard typed.
    AXES = {
        "consumer": ["c", "blast radius (Lens 1)", "spec-check (regression guard)"],
        "backend": [None, "ast", "code-review-graph"],
        "mentions": [0, 7],
        "files": [0, 3],
        "targets": [None, ["sym"], ["sym", "other"]],
        "notice": [None, "⚠ a one-time notice"],
    }

    def test_every_argument_of_the_gate_is_covered_by_this_class(self):
        """⛔ THE GUARD ON THE GUARD. §7j is a derivation that types its own scope, so the scope is
        read off the signature: if someone adds an argument to `check_graph_required` and does not
        add it here, this fails rather than silently narrowing."""
        import inspect
        params = set(inspect.signature(GR.check_graph_required).parameters) - {
            "degraded", "required", "overridden", "reasons"}
        self.assertEqual(params, set(self.AXES),
                         "an argument of the gate is not exercised by the verdict guard — that is "
                         "exactly the gap review finding 3-3 found (§7j)")

    def test_the_verdict_is_the_FORMULA_across_every_axis(self):
        """One axis varied at a time against every reachable reason set, which is the product that
        matters: a weakening keyed on ANY argument now has a case that sees it."""
        import itertools
        base = dict(consumer="c", backend=None, mentions=0, files=0, targets=None, notice=None)
        for reasons in _reachable_subsets():
            for axis, values in self.AXES.items():
                for v in values:
                    kw = dict(base, **{axis: v})
                    for deg, req, ovr in itertools.product((True, False), repeat=3):
                        out = GR.check_graph_required(degraded=deg, required=req, overridden=ovr,
                                                      reasons=reasons, **kw)
                        if out.refused != bool(deg and req and not ovr):
                            self.fail("the verdict moved on axis %s=%r with reasons=%r "
                                      "(degraded=%s required=%s overridden=%s)"
                                      % (axis, v, reasons, deg, req, ovr))
                        self.assertEqual(bool(deg), out.degraded)

    def test_the_NOTICE_rides_only_a_refusal(self):
        """One of the 11 survivors: `notice=notice if refused else None` -> `notice=notice`, so the
        one-time upgrade banner would fire on a pass."""
        allowed = GR.check_graph_required(degraded=False, required=True, overridden=False,
                                          consumer="c", notice="⚠ a one-time notice")
        self.assertFalse(allowed.refused)
        self.assertIsNone(allowed.notice,
                          "the one-time notice is spent by a REFUSAL; firing it on a pass burns it "
                          "and the user never sees it when it matters")
        self.assertEqual("", allowed.render())

    def test_the_gate_DEFAULT_is_on(self):
        """The reviewer's R10: `REQUIRED_LEAF, True` -> `False` opens the entire gate, and every test
        in this file passed because they all pass `required=` explicitly. The DEFAULT is the product
        decision — doc 85 calls it the D1 differentiator — so it is pinned at its source."""
        class _Broken:
            @property
            def manifest(self):
                raise RuntimeError("unreadable")
        self.assertTrue(GR.graph_required_enabled(_Broken()),
                        "an unreadable manifest must FAIL to required-on")

        class _Silent:
            class manifest:
                @staticmethod
                def setting(group, default=None):
                    return {}
        self.assertTrue(GR.graph_required_enabled(_Silent()),
                        "an absent `graph` block reads TRUE — opt-out, no migration write")

        class _Off:
            class manifest:
                @staticmethod
                def setting(group, default=None):
                    return {"required": False}
        self.assertFalse(GR.graph_required_enabled(_Off()),
                         "the CONTROL: an explicit false is respected, or the setting is a lie")

    def test_the_derivation_FAILS_CLOSED_when_the_lens_answers_without_the_field(self):
        """🔴 SURVIVOR A07. `getattr(impact, "graph_degraded", True)` -> `False` was GREEN against 47
        tests — the module's own docstring says *"a gate that cannot see is not a gate that
        approves"*, and the default that enforces it had nothing grading it. Every test handed the
        derivation a lens whose result CARRIES the field, so the default was never reached."""
        class _NoField:
            pass                      # a lens result with no `graph_degraded` at all
        basis, degraded, reasons = GR.derive_graph_degraded_detail(
            object(), ["pay"], _lens=lambda *_a, **_k: _NoField(),
            _build_layer=lambda _s: object())
        self.assertTrue(degraded,
                        "a result that does not say must read as DEGRADED — doubt refuses")
        self.assertEqual(GR.DERIVED_DEGRADED, basis)

    def test_the_control_a_lens_that_SAYS_clean_is_believed(self):
        """Without this, "always degraded" would satisfy the test above."""
        class _Clean:
            graph_degraded = False
        _basis, degraded, _r = GR.derive_graph_degraded_detail(
            object(), ["pay"], _lens=lambda *_a, **_k: _Clean(),
            _build_layer=lambda _s: object())
        self.assertFalse(degraded)

    def test_every_remedy_has_a_HINT_that_names_it(self):
        """🔴 SURVIVOR A10. The hint/refusal agreement test branched on `adopt` and `repair` and
        asserted nothing but `--allow-degraded` for `check-target` — so deleting `hint_for`'s whole
        CHECK-TARGET branch shipped a "CHECK THE TARGET" refusal beside an "adopt a code graph" hint.
        That is B-F1 verbatim, on a reason set the derivation really produces, and it was GREEN.

        ⭐ Derived from the remedy vocabulary rather than listed, so a NEW remedy cannot be added
        without a hint that names it."""
        remedies = {GR.REMEDY_ADOPT: ("mokata graph adopt",),
                    GR.REMEDY_REPAIR: ("mokata doctor",),
                    GR.REMEDY_CHECK_TARGET: ("check the symbol names",)}
        seen = set()
        for reasons in _reachable_subsets():
            remedy = GR.remedy_for(reasons)
            if remedy not in remedies:      # `()` and bare not-derived fall through to adopt
                remedy = GR.REMEDY_ADOPT
            hint = GR.hint_for(reasons)
            for needle in remedies[remedy]:
                self.assertIn(needle, hint,
                              "reasons=%r lead with %s and the hint does not name it: %r"
                              % (reasons, remedy, hint))
            seen.add(remedy)
        self.assertEqual(set(remedies), seen,
                         "a remedy is never reached by any reachable reason set — either it is dead "
                         "or the reachability derivation is wrong, and both matter (§7i)")

    def test_the_live_call_site_PASSES_the_reasons_it_derived(self):
        """🔴 REVIEW FINDING 4-3 — ALL OF FIX D IS DELETABLE WITH ONE TOKEN AND EVERY SUITE STAYS
        GREEN. Drop `reasons=reasons` at `tools_spec`'s single GR.S3 call site and
        `check_graph_required` stamps `(DEGRADE_NOT_DERIVED,)`, `_why` falls through to the pre-fix-D
        sentence and `remedy_for` to ADOPT — so an adopted-but-unrunnable graph is told to adopt one.
        3-1 and B-F2, verbatim.

        ⛔ AND THE GUARD WRITTEN TO FIX §7j EXCLUDED THAT ARGUMENT BY NAME. `AXES` omits `reasons`
        because the reason set is the thing being swept — correct for the product, and it left the
        DELIVERY of that set ungraded. A derivation that types one argument out of its own scope is
        §7j again, inside the fix for §7j."""
        from mokata.mcp import tools_spec
        with open(tools_spec.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("reasons=reasons", src,
                      "the live call site must PASS the reasons it derived — without this, every "
                      "sentence fix D produces is replaced by the one it exists to delete")
        self.assertIn("derive_graph_degraded_detail", src,
                      "and it must be the DETAIL derivation that produced them")
        self.assertIn("GR.hint_for(reasons)", src,
                      "and the machine hint must derive from the same set (B-F1)")

    def test_the_UNDERIVABLE_basis_still_refuses_on_the_live_path(self):
        """The reviewer's R5 skipped the refusal when the basis was UNDERIVABLE — "mokata could not
        look" silently allowed, on the only path that fires. The emit refusal's contract is pinned at
        the source because the builder is not injectable."""
        from mokata.mcp import tools_spec
        with open(tools_spec.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("if not degraded:", src)
        self.assertNotIn("if not degraded or basis ==", src,
                         "a basis-keyed skip would let the 'could not look' refusal through")
        self.assertIn("consumer=consumer", src)


def _reachable_subsets():
    """The reason sets `compute_impact` can actually EMIT, derived rather than listed.

    ⚠ The 32-subset sweep included 21 sets the derivation cannot produce, which is not harmless: it
    spends the guard's attention on cases no user meets while the reviewer's weakenings lived on the
    axes it had typed to constants. `no-layer` never combines (the per-target loop is skipped), and
    `chain-lexical` / `graph-unavailable` / `graph-failed` are mutually exclusive within one call
    because the layer's two facts are constant across it."""
    out = [(), (BI.DEGRADE_NO_LAYER,)]
    for chain in (BI.DEGRADE_CHAIN_IS_LEXICAL, BI.DEGRADE_GRAPH_UNAVAILABLE,
                  BI.DEGRADE_GRAPH_FAILED):
        for empty in ((), (BI.DEGRADE_FLOOR_FOUND_NOTHING,)):
            for fault in ((), (BI.DEGRADE_QUERY_FAULT,)):
                out.append(fault + (chain,) + empty)
    out.append((BI.DEGRADE_QUERY_FAULT,))
    return out


class TheReasonCannotBeTyped(unittest.TestCase):
    """SI.3 with evidence in place of consent. `graph_degraded` reached the gate as a bool the MODEL
    wrote, through `session_save`'s dict. A reason with the same hole would be worse than none: it
    would let a model explain away its own refusal."""

    def test_the_reason_is_not_on_the_wire(self):
        imp = BI.compute_impact("a", ["sym"], layer=_Layer(
            uses_graph=True, answer=_qr(refs=[])))
        self.assertTrue(imp.graph_degrade_reasons, "the fixture must actually have reasons")
        self.assertNotIn("graph_degrade_reasons", imp.to_dict(),
                         "a reason on the wire is a reason the model can type")

    def test_a_dict_that_CLAIMS_a_reason_is_ignored(self):
        """The planted offender: a hand-written payload asserting its own degradation is benign."""
        d = BI.ApproachImpact(approach="a", targets=["sym"]).to_dict()
        d["graph_degrade_reasons"] = [BI.DEGRADE_CHAIN_IS_LEXICAL]
        self.assertEqual((), BI.ApproachImpact.from_dict(d).graph_degrade_reasons)


if __name__ == "__main__":
    unittest.main()
