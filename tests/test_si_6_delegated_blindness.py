"""SI.6-DELEGATED-BLINDNESS — the zero-bypass sweep learns to see DELEGATED and SQL writes.

THE BUG, in one sentence: `TestZeroBypass` is a static AST scan over a FIXED VOCABULARY of write
NAMES, so a write that reaches disk through a name not on that list is invisible to it — and the
register's own failure message ("a new durable write appeared in src/ and nobody said whether it is
gated") makes a GREEN sweep read as "every durable writer is classified". That is the exact false
assurance that let `apply_consolidation` sit outside the gate for six stages (74 C1) while every
doc said otherwise. An audit is trusted in proportion to how complete it claims to be.

Two independent LIVE confirmations, both from real builds rather than inspection, and each has a
named regression test below:

  (1) DELEGATED — DB.S7d/P10 (2026-07-31). A `journal._append` planted inside `_ask_group`, an
      asker whose whole charter is "writes nothing", passed all 4972 pre-P10 unit tests, SI.6
      included. P10 closed THAT site with a whole-tree byte comparison; the class stayed open.
      → `test_si6_delegated_blindness_regression`

  (2) SQL — DB.S7a (2026-07-31). The entire v5 edge substrate lands durable rows on BOTH engines
      through `conn.execute(INSERT …)` in `memory/edges.py:project_edges`, reached from
      `SQLiteBackend.put`, `PostgresBackend.put` and `team_journal.apply_memory_write`. The sweep
      stayed green through the whole build and never asked anyone to classify it, because SQL
      execution was not in the vocabulary at all.
      → `test_si6_sql_blindness_regression`

AND THE SELF-GUARD, which matters as much as either. An audit that has never been observed FAILING
is an audit nobody has tested, and both new instruments have a characteristic way of going
vacuously green: the coherence contract passes trivially if it can never fire, and the closure
passes trivially if it resolves nothing. So there is a test that plants the P10 shape and asserts
the sweep REDS, and a test that plants two same-named functions in different modules and asserts
only the RESOLVED one becomes an edge — without which the 1,650-site bare-name explosion measured
at the stage grounding can silently return, and a register nobody can read is the same false
assurance in a new costume.

THE 0.0.17 REVIEW SENT THIS BACK, and the three things it changed are worth stating here because
each was a way the instrument claimed more than it proved:

  F1 · THE KEY COLLAPSED DEFINITIONS. The register keyed on (file, BARE innermost name), so 15 of
       112 keys covered more than one definition — `memory/store.py:_commit` covered EIGHT commit
       closures under one sentence. A planted `class ExfilBackend: def put(...)` appended to a copy
       of `memory/backends.py` landed under the existing `put` key, left the site count at 112 and
       kept the sweep GREEN. Keys are now QUALIFIED (`SQLiteBackend.put`,
       `MemoryStore.remember._commit`): one entry, one definition, 135 sites.
       → `test_a_second_definition_sharing_a_former_key_is_now_its_own_site`

  F2 · THE RECEIVER VOCABULARY WAS NARROWER THAN THE GUARANTEE. Ten ordinary write shapes were
       confirmed to walk past both instruments. Rule (e) closes the constructor-bound receiver
       (`store = SQLiteBackend(p)` … `store.put(i)`); the REST ARE DECLARED, in the sweep's failure
       message and in the audit report, not only in a docstring their author reads.
       → `test_the_receiver_rule_resolves_a_constructor_bound_local`
       → `test_the_failure_message_and_the_report_both_name_the_blind_spots`

  F3 · FOUR ENTRIES EARNED THEIR CLASSIFICATION FROM A PROSE CALLER LIST NOTHING CHECKS, and all
       four were false. They are corrected in the register itself (session_transport write_bundle's
       third caller; memory/vector put's reembed caller; migrate_memory's --drop-source, which is a
       bespoke TTY confirm with NO ledger record, not "inside the per-item WriteGate"; the vault
       channel, which is not gated via migrate_memory/import_memory). Deriving caller lists
       mechanically is filed CALLER-LIST-UNPINNED and is a later stage — these four are corrected,
       not automated. The process note the review drew from (c): MOVING A REGISTER ENTRY MEANS
       RE-VERIFYING THE SENTENCE YOU ARE MOVING, not only the register it lands in.

NO PRODUCTION IMPORTS. This suite and `_writegraph` use stdlib `ast` only and import nothing from
`mokata.knowledge` — an audit that depends on the subsystem it audits cannot fail honestly when
that subsystem is wrong.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import textwrap
import unittest
from unittest import mock

import _writegraph
import test_si_6_writegate_side_doors as sweep


def _plant(root, files):
    """Write a throwaway package and return its root. Used to prove the audit can go RED."""
    for rel, body in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(body))
    return root


# ======================================================================================
# THE TWO REGRESSIONS. Each must be RED on the pre-stage instrument and GREEN on this one.
# ======================================================================================

class TestTheSweepSeesDelegatedWrites(unittest.TestCase):
    """Live confirmation (1): a durable write reached by DELEGATION."""

    def test_si6_delegated_blindness_regression(self):
        """`team_journal.mark_flushed` is the P10 shape on the live tree: a bookkeeping function
        whose own body writes nothing, one resolved call away from `_append_all`, the registered
        durable writer that appends to the journal.

        RED on the old instrument: the vocabulary scan named no write in `mark_flushed`, so the
        sweep had no row for it and no opinion about it — the audit's claim to classify every
        durable writer simply did not cover it. GREEN here: it is a DERIVED site with a traceable
        path to a registered writer."""
        direct = sweep._durable_write_sites()
        _graph, closure = sweep._write_closure()
        delegate = ("team_journal.py", "TeamJournal.mark_flushed")
        writer = ("team_journal.py", "TeamJournal._append_all")

        self.assertNotIn(delegate, direct,
                         "the vocabulary scan should still see nothing in `mark_flushed`'s own "
                         "body — that blindness is the bug, and it is what the closure answers")
        self.assertIn(writer, direct, "the delegate's target must be a direct write site")
        self.assertIn(delegate, closure.derived,
                      "a durable write reached by delegation is INVISIBLE to the sweep — this is "
                      "SI.6-DELEGATED-BLINDNESS, live confirmation (1) (DB.S7d P10)")
        self.assertEqual(closure.derived[delegate][-1], writer,
                         "the derived site must name the writer it actually reaches: "
                         + closure.path_text(delegate))

    def test_the_delegation_path_is_reported_not_just_the_verdict(self):
        """A delegated classification the reader cannot trace is one they will rubber-stamp, so the
        path is the deliverable, not a debugging aid."""
        _graph, closure = sweep._write_closure()
        text = closure.path_text(("team_journal.py", "TeamJournal.mark_flushed"))
        self.assertIn("team_journal.py:TeamJournal.mark_flushed -> ", text)
        self.assertTrue(text.endswith("team_journal.py:TeamJournal._append_all"), text)


class TestTheSweepSeesSqlWrites(unittest.TestCase):
    """Live confirmation (2): a durable write executed as SQL."""

    def test_si6_sql_blindness_regression(self):
        """`memory/edges.py:project_edges` — by that exact (file, function) key — is the site the
        DB.S7a build shipped through a green sweep that never asked anyone to classify it.

        RED on the old instrument: `.execute` was not in the vocabulary, so the whole v5 edge
        substrate was unregistered AND unreported. GREEN here: it is a direct site and a human has
        written down which gate runs it."""
        direct = sweep._durable_write_sites()
        site = ("memory/edges.py", "project_edges")
        self.assertIn(site, direct,
                      "the v5 edge substrate's durable INSERT is invisible to the sweep — this is "
                      "SI.6-DELEGATED-BLINDNESS, live confirmation (2) (DB.S7a)")
        self.assertIn(site, sweep.GATED,
                      "seeing it is only half the fix: a human must say which gate runs it")
        self.assertTrue(sweep.GATED[site].strip(), "the entry must carry a real reason")

    def test_the_sql_class_does_not_filter_on_the_statement_text(self):
        """M4, pinned. The backlog row's suggested noise filter for the SQL class — 'filter on the
        statement's leading verb' — was MEASURED wrong: it cuts 53 (file, function) keys to 17 and
        DROPS `project_edges` itself, because that INSERT is f-string-assembled at `edges.py:272`
        and is not a literal at the call site. A filter that misses the motivating example is not a
        noise filter, it is a blind spot with a rationale.

        Two things prove no such filter is in force: an f-string-assembled INSERT is caught (above),
        and read-only SELECT sites are caught too — they are in the register, classified by hand,
        rather than quietly excluded by the detector."""
        direct = sweep._durable_write_sites()
        for read_only in (("memory/backends.py", "SQLiteBackend.get"),
                          ("memory/backends.py", "PostgresBackend.get"),
                          ("memory/vector.py", "PgVectorBackend.semantic_search"),
                          ("team_audit.py", "SharedAuditLog.read"),
                          ("teamdb.py", "probe._work")):
            self.assertIn(read_only, direct,
                          f"{read_only} is a SELECT-shaped site and it must still be SEEN — the "
                          "detector filters on the RECEIVER SHAPE, never on the statement text")
            self.assertIn(read_only, sweep.UNGATED_BY_DESIGN,
                          f"{read_only} must be classified by a HUMAN, per site, with a reason — "
                          "there is no blanket 'SELECT is fine' rule and no reasoning from names")

    def test_every_sql_site_says_what_that_statement_does(self):
        """Deliverable 4 — each of the ~50 new SQL sites carries a real one-line reason, exactly as
        the original 62 do. An entry with an empty or placeholder justification is worse than no
        entry: it looks classified."""
        direct = sweep._durable_write_sites()
        sql_sites = [k for k, labels in direct.items()
                     if any(lbl.startswith("sql.") for lbl in labels)]
        self.assertGreaterEqual(len(sql_sites), 50, "the SQL receiver class found too few sites")
        for site in sql_sites:
            for reg in (sweep.GATED, sweep.UNGATED_BY_DESIGN, sweep.LEDGERED, sweep.KNOWN_BYPASS):
                if site in reg:
                    self.assertGreater(len(reg[site].strip()), 40,
                                       f"{site} carries no real reason: {reg[site]!r}")
                    break
            else:
                self.fail(f"{site} executes SQL and nobody classified it")


# ======================================================================================
# THE SELF-GUARD. The sweep must be unable to go vacuously green.
# ======================================================================================

class TestTheSweepCanActuallyFail(unittest.TestCase):
    """Both new instruments have a characteristic vacuous-green failure mode. These fire them."""

    def test_a_planted_delegated_write_is_seen_by_the_closure(self):
        """The P10 shape, planted: an asker that claims to write nothing, one resolved call away
        from a journal append. The vocabulary scan sees nothing in the asker; the closure does."""
        with tempfile.TemporaryDirectory() as d:
            root = _plant(d, {
                "__init__.py": "",
                "journal.py": """
                    from .io import atomic_write_text

                    def _append(record):
                        atomic_write_text("journal", record)
                """,
                "asker.py": """
                    from .journal import _append

                    def ask_group(question):
                        '''ASKS and settles NOTHING.'''
                        return _append(question)
                """,
                "io.py": """
                    def atomic_write_text(path, text):
                        pass
                """,
            })
            direct = sweep._durable_write_sites(root)
            graph = _writegraph.build_call_graph(root, package="planted")
            closure = _writegraph.close_over_writers(graph, direct)

            asker = ("asker.py", "ask_group")
            self.assertNotIn(asker, direct, "the planted asker names no writer in its own body — "
                                            "that is precisely why the old sweep was blind to it")
            self.assertIn(asker, closure.derived, "the closure must SEE the delegated write")
            self.assertEqual(closure.path_text(asker),
                             "asker.py:ask_group -> journal.py:_append")

    def test_a_planted_write_free_claim_reds_the_coherence_contract(self):
        """THE SWEEP MUST BE ABLE TO GO RED, on the REAL tree, not just in a sandbox.

        `mcp/tools_share.py:vault_push` genuinely reaches `vault.py:commit_push`, which is GATED.
        Claiming it is write-free is therefore a live contradiction — and the coherence test must
        fail loudly and name the path. If this ever passes, the contract has gone vacuous and the
        `_ask_group` class is open again."""
        liar = ("mcp/tools_share.py", "vault_push")
        self.assertNotIn(liar, sweep.UNGATED_BY_DESIGN, "precondition: it is not claimed today")

        with mock.patch.dict(sweep.UNGATED_BY_DESIGN,
                             {liar: "PLANTED — a false write-freedom claim"}):
            case = sweep.TestZeroBypass("test_no_write_free_claim_delegates_to_a_governed_write")
            with self.assertRaises(AssertionError) as caught:
                case.test_no_write_free_claim_delegates_to_a_governed_write()

        message = str(caught.exception)
        self.assertIn("INCOHERENT REGISTER ENTRY", message)
        self.assertIn("mcp/tools_share.py:vault_push -> vault.py:commit_push", message,
                      "the failure must NAME THE PATH — a contradiction the reader cannot trace "
                      "is one they will wave through")

    def test_the_resolver_does_not_match_by_bare_name(self):
        """WITHOUT THIS, M1's 1,650-SITE EXPLOSION CAN SILENTLY RETURN.

        Two functions named `put` in different modules; only one of them writes. A bare-NAME match
        would make `caller` a write site because it calls something spelled `put`. A RESOLVED edge
        goes to the definition site, so `caller` reaches the harmless local `put` and nothing else.

        This is the difference between a 201-site register a reader can audit and a 1,650-site one
        nobody will ever read — and the second is the same false assurance in a new costume."""
        with tempfile.TemporaryDirectory() as d:
            root = _plant(d, {
                "__init__.py": "",
                "quiet.py": """
                    def put(item):
                        '''Same NAME as the writer next door. Writes nothing.'''
                        return item

                    def caller(item):
                        return put(item)
                """,
                "loud.py": """
                    from .io import atomic_write_text

                    def put(item):
                        atomic_write_text("store", item)
                """,
                "io.py": """
                    def atomic_write_text(path, text):
                        pass
                """,
            })
            direct = sweep._durable_write_sites(root)
            graph = _writegraph.build_call_graph(root, package="planted")
            closure = _writegraph.close_over_writers(graph, direct)

            self.assertEqual(graph.callees(("quiet.py", "caller")), frozenset({("quiet.py", "put")}),
                             "the call must resolve to the SAME-MODULE definition, not to every "
                             "function in the package that happens to be spelled `put`")
            self.assertIn(("loud.py", "put"), direct)
            self.assertNotIn(("quiet.py", "caller"), closure.derived,
                             "BARE-NAME MATCHING HAS RETURNED — `quiet.caller` calls a harmless "
                             "local `put` and has been counted as reaching `loud.put`. This is the "
                             "62 -> 1,650 explosion measured at the stage grounding")
            self.assertNotIn(("quiet.py", "put"), closure.direct)

    def test_a_self_call_resolves_only_when_the_enclosing_class_defines_it(self):
        """Rule (c), both directions. `self.save()` inside a class that DEFINES `save` is a real
        edge; `self.helper()` inside a class that does NOT define `helper` is not one, even when a
        module-level function of that name is sitting right there writing to disk.

        The negative half is the load-bearing one: relaxing this rule to 'any `self.<name>`' is a
        bare-name match wearing a receiver, and it is how the register starts growing edges nobody
        can justify."""
        with tempfile.TemporaryDirectory() as d:
            root = _plant(d, {
                "__init__.py": "",
                "svc.py": """
                    from .io import atomic_write_text

                    def helper(x):
                        '''A MODULE-level function. Not a method of Service. It does write.'''
                        atomic_write_text("helper", x)

                    class Service:
                        def _write(self, x):
                            atomic_write_text("svc", x)

                        def save(self, x):
                            return self._write(x)      # the class DOES define _write

                        def sneak(self, x):
                            return self.helper(x)      # the class does NOT define helper
                """,
                "io.py": """
                    def atomic_write_text(path, text):
                        pass
                """,
            })
            direct = sweep._durable_write_sites(root)
            graph = _writegraph.build_call_graph(root, package="planted")
            closure = _writegraph.close_over_writers(graph, direct)

            self.assertIn(("svc.py", "Service._write"), direct)
            self.assertIn(("svc.py", "helper"), direct)
            self.assertEqual(closure.path_text(("svc.py", "Service.save")),
                             "svc.py:Service.save -> svc.py:Service._write",
                             "a self-call to a method the enclosing class defines IS an edge")
            self.assertNotIn(("svc.py", "Service.sneak"), closure.derived,
                             "`self.helper` resolved even though `Service` does not define "
                             "`helper` — the resolver has widened into a bare-name match on a "
                             "receiver, which is how the 62 -> 1,650 explosion begins")

    def test_the_resolver_follows_an_imported_module_alias(self):
        """Rule (d). `from . import writer` / `from .sub import mod` bind a MODULE, and a call
        through that alias is a resolved edge — dropping it would make every cross-module delegation
        written in this (very common) style invisible, which is the original bug in miniature."""
        with tempfile.TemporaryDirectory() as d:
            root = _plant(d, {
                "__init__.py": "",
                "io.py": """
                    def atomic_write_text(path, text):
                        pass
                """,
                "writer.py": """
                    from .io import atomic_write_text

                    def write_it(x):
                        atomic_write_text("f", x)
                """,
                "sub/__init__.py": "",
                "sub/deep.py": """
                    from ..io import atomic_write_text

                    def write_deep(x):
                        atomic_write_text("g", x)
                """,
                "caller.py": """
                    from . import writer
                    from .sub import deep

                    def do(x):
                        return writer.write_it(x)

                    def do_deep(x):
                        return deep.write_deep(x)
                """,
            })
            direct = sweep._durable_write_sites(root)
            graph = _writegraph.build_call_graph(root, package="planted")
            closure = _writegraph.close_over_writers(graph, direct)

            self.assertEqual(closure.path_text(("caller.py", "do")),
                             "caller.py:do -> writer.py:write_it",
                             "`from . import writer` + `writer.write_it()` must resolve")
            self.assertEqual(closure.path_text(("caller.py", "do_deep")),
                             "caller.py:do_deep -> sub/deep.py:write_deep",
                             "`from .sub import deep` binds a MODULE, not a symbol — the resolver "
                             "must re-file it as an alias or the delegation goes unseen")

    def test_a_second_definition_sharing_a_former_key_is_now_its_own_site(self):
        """THE F1 PLANTED COLLISION, on the shape that was demonstrated at the 0.0.17 review.

        The review appended `class ExfilBackend: def put(...)` — writing to /tmp/exfil.jsonl — to a
        COPY of `memory/backends.py`. Under the BARE key it landed under the existing
        ('memory/backends.py', 'put') entry, the site count stayed at 112, and THE SWEEP STAYED
        GREEN. A new exfiltrating writer inherited a stranger's justification simply by choosing a
        name already in the file.

        Here the same shape is planted on a scratch package: a registered writer `Known.put`, and
        an unregistered `Exfil.put` beside it in the SAME file. The qualified key must make them
        two sites, so the second is UNREGISTERED and the sweep REDS naming it. This test is the
        whole point of fix 1 — without it, the qualification is a refactor with no guard."""
        with tempfile.TemporaryDirectory() as d:
            root = _plant(d, {
                "__init__.py": "",
                "io.py": """
                    def atomic_write_text(path, text):
                        pass
                """,
                "backends.py": """
                    from .io import atomic_write_text

                    class Known:
                        def put(self, item):
                            atomic_write_text("known", item)

                    class Exfil:
                        def put(self, item):
                            atomic_write_text("/tmp/exfil.jsonl", item)
                """,
            })
            sites = sweep._durable_write_sites(root)

            self.assertIn(("backends.py", "Known.put"), sites)
            self.assertIn(("backends.py", "Exfil.put"), sites,
                          "THE PLANTED WRITER VANISHED. Two `put` definitions in one file have "
                          "collapsed to one key again, which is how an exfiltrating backend "
                          "inherits a stranger's justification and keeps the sweep green.")
            self.assertEqual(len([k for k in sites if k[0] == "backends.py"]), 2,
                             f"one entry must describe one definition: {sorted(sites)}")

            # and the sweep goes RED on it: with only the known writer registered, the planted one
            # is unregistered, and the failure NAMES it.
            registered = {("backends.py", "Known.put"): "the registered one"}
            unregistered = sorted(set(sites) - set(registered))
            self.assertEqual(unregistered, [("backends.py", "Exfil.put")],
                             "the planted writer must be the one thing the sweep cannot account "
                             "for — that is the RED the bare key could not produce")

    def test_direction_b_reds_when_the_declared_exception_is_removed(self):
        """DIRECTION B MUST BE ABLE TO CONVICT, and the thing it must convict is live in src/ right
        now — `GATED-ADMITS-UNGATED-CALLER`, the entry stage 1a made TRUE and thereby exposed.

        Both halves are proved here, because only the pair is evidence. WITH the declared
        exception the contract is green (the falsehood is carried deliberately, dated, and expires
        by itself). WITHOUT it the contract REDS and names the entry and the ungated call site. If
        this ever passes with the exceptions removed, direction B has gone vacuous and the register
        is free to admit ungated callers in prose again — which is the state the sweep was in when
        it reported GREEN with a knowably-false entry in it.

        ⚠ RE-ANCHORED AT 0.0.17 STAGE 17, and re-anchoring rather than deleting is the point. This
        control used to name `write_bundle` / `migrate_channels.py:304`, because that was the live
        instance. Stage 17 GATED that call site, its exception expired and was deleted — so the
        control's own subject stopped existing. A negative control whose subject has been fixed is
        not a passing control, it is an ABSENT one, and quietly dropping it would have retired the
        proof that direction B can still fail. It is pointed at the instance that IS live:
        `PgVectorBackend.put`, reached ungated from `memory/reembed.py:124`. The day that one
        closes too, this control has no live subject at all and must be rebuilt on a synthetic one
        rather than deleted."""
        case = sweep.TestZeroBypass("test_no_gated_entry_declares_an_ungated_caller")
        case.test_no_gated_entry_declares_an_ungated_caller()          # green WITH the exceptions

        with mock.patch.dict(sweep.GATED_CALLER_EXCEPTIONS, clear=True):
            with self.assertRaises(AssertionError) as caught:
                case.test_no_gated_entry_declares_an_ungated_caller()
        message = str(caught.exception)
        self.assertIn("memory/vector.py:PgVectorBackend.put", message,
                      "the failure must NAME the entry whose caller list contradicts its register")
        self.assertIn("memory/reembed.py:124 is NOT gate-enclosed", message,
                      "and it must NAME the call site — a contradiction the reader cannot go and "
                      "look at is one they will wave through")
        self.assertIn("DIRECTIONS THIS CONTRACT RUNS", message,
                      "the failure must declare which directions it covers; an instrument whose "
                      "coverage must be re-derived from its call site is the audited defect class "
                      "one level up (COHERENCE-CONTRACT-ONE-DIRECTIONAL)")

    def test_a_declared_exception_that_has_been_gated_is_reported_as_expired(self):
        """★ THE SELF-EXECUTING EXPIRY, exercised. An exception whose call site becomes
        gate-enclosed must RED and demand its own deletion — otherwise `expires_when` is a comment,
        and a comment is what the four false caller strings were."""
        class _Gated:
            def is_gate_run(self, rel, lineno):
                return True

        case = sweep.TestZeroBypass("test_a_declared_exception_dies_the_moment_its_caller_is_gated")
        case.test_a_declared_exception_dies_the_moment_its_caller_is_gated()      # green today

        with mock.patch.dict(sweep._GRAPH_CACHE, {"enclosure": _Gated()}):
            with self.assertRaises(AssertionError) as caught:
                case.test_a_declared_exception_dies_the_moment_its_caller_is_gated()
        self.assertIn("HAS EXPIRED — DELETE IT", str(caught.exception))

    def test_the_caller_pin_reds_when_a_new_caller_appears(self):
        """CALLER-LIST-UNPINNED's mechanism, fired. Stage 1a corrected four false caller strings by
        hand; this is what stops the fifth — so it must be shown REDDENING on a new caller rather
        than merely existing.

        The pin's failure mode if it were weak is the interesting one: it would go green while a
        justification that says "sole callers are X and Y" quietly acquired a Z."""
        case = sweep.TestZeroBypass(
            "test_the_caller_list_of_every_registered_site_is_derived_not_asserted")
        case.test_the_caller_list_of_every_registered_site_is_derived_not_asserted()

        victim = ("vault.py", "commit_push")
        thinner = tuple(c for c in sweep.CALLER_PINS[victim] if c[0] != "mcp/tools_share.py")
        self.assertEqual(len(thinner), len(sweep.CALLER_PINS[victim]) - 1, "precondition")
        with mock.patch.dict(sweep.CALLER_PINS, {victim: thinner}):
            with self.assertRaises(AssertionError) as caught:
                case.test_the_caller_list_of_every_registered_site_is_derived_not_asserted()
        message = str(caught.exception)
        self.assertIn("CALLERS CHANGED for vault.py:commit_push", message)
        self.assertIn("mcp/tools_share.py:vault_push", message,
                      "the failure must name the caller that appeared, not just that something did")

    def test_a_declared_caller_that_has_drifted_off_its_line_is_caught(self):
        """A `file.py:LINE` claim rots the moment the file above it grows, and a stale line for a
        caller declared UNGATED would silently DELETE a direction-B violation — the instrument
        would re-green itself by decay. So the line must still hold a call spelled with the site's
        own method name."""
        case = sweep.TestZeroBypass(
            "test_every_declared_caller_really_calls_what_it_is_declared_to_call")
        case.test_every_declared_caller_really_calls_what_it_is_declared_to_call()

        drifted = dict(sweep.DECLARED_CALLERS)
        drifted[("vault.py", "commit_push")] = (("cli_commands/collab.py", 110),)
        with mock.patch.dict(sweep.DECLARED_CALLERS, drifted, clear=True):
            with self.assertRaises(AssertionError) as caught:
                case.test_every_declared_caller_really_calls_what_it_is_declared_to_call()
        self.assertIn("has drifted off its line", str(caught.exception))

    def test_gate_enclosure_separates_a_commit_closure_from_a_bare_caller(self):
        """THE PRIMITIVE DIRECTION B RESTS ON, on a planted package where the answer is known.

        Both halves matter and the NEGATIVE one is load-bearing: a detector that answered "gate-run"
        for everything would silence direction B permanently and look green doing it. And the
        `sometimes` case is pinned too — a helper called from BOTH a commit closure and a bare path
        must NOT count as gate-run, because "it can run under a gate" is not "it runs under a
        gate", and the safe direction of error for an audit is to flag rather than to excuse."""
        with tempfile.TemporaryDirectory() as d:
            root = _plant(d, {
                "__init__.py": "",
                "io.py": """
                    def atomic_write_text(path, text):
                        pass
                """,
                "gate.py": """
                    class WriteGate:
                        def submit(self, request, commit=None):
                            return commit()

                    def _gated_write(root, kind, target, content, run, gate):
                        return run()
                """,
                "flows.py": """
                    from .io import atomic_write_text
                    from .gate import WriteGate, _gated_write

                    def committer(x):
                        atomic_write_text("a", x)      # GATE-RUN: only reached from commit=

                    def shared(x):
                        atomic_write_text("b", x)      # BOTH ways — must NOT count as gate-run

                    def bare(x):
                        atomic_write_text("c", x)      # never gated

                    def gated_flow(x):
                        gate = WriteGate()
                        return gate.submit("req", commit=lambda: committer(x))

                    def wrapper_flow(x):
                        return _gated_write("r", "k", "t", x, lambda: shared(x), None)

                    def ungated_flow(x):
                        shared(x)
                        return bare(x)
                """,
            })
            graph = _writegraph.build_call_graph(root, package="planted")
            enc = _writegraph.gate_enclosure(root, package="planted", graph=graph)

            self.assertIn(("flows.py", "committer"), enc.gate_run,
                          "a function reached ONLY from a `commit=` callable is gate-run")
            self.assertNotIn(("flows.py", "bare"), enc.gate_run,
                             "a function no commit callable reaches is not gate-run")
            self.assertNotIn(("flows.py", "shared"), enc.gate_run,
                             "`shared` is reached from a gate wrapper AND from a bare path — "
                             "counting it gate-run would EXCUSE the bare path, which is the one "
                             "direction of error an audit must never make")

            # the call INSIDE `committer` inherits the enclosure; the one inside `bare` does not
            write_in_committer = graph.call_lines(("flows.py", "committer"), ("io.py",
                                                                             "atomic_write_text"))
            write_in_bare = graph.call_lines(("flows.py", "bare"), ("io.py", "atomic_write_text"))
            self.assertTrue(write_in_committer and write_in_bare, "precondition: both resolve")
            self.assertTrue(enc.is_gate_run("flows.py", write_in_committer[0]))
            self.assertFalse(enc.is_gate_run("flows.py", write_in_bare[0]))

            # and the LEXICAL half: a call written inside the `commit=` lambda itself, and one
            # inside a lambda handed POSITIONALLY to a declared wrapper (the shape
            # `mcp/tools_share.py:59` uses, which the `commit=` keyword alone does not reach).
            for fn in ("gated_flow", "wrapper_flow"):
                line = graph.call_lines(("flows.py", fn), ("flows.py", "committer")) or \
                       graph.call_lines(("flows.py", fn), ("flows.py", "shared"))
                self.assertTrue(line, f"precondition: {fn}'s inner call resolves")
                self.assertTrue(enc.is_gate_run("flows.py", line[0]),
                                f"the call inside {fn}'s commit callable must be gate-enclosed")

    def test_a_call_cycle_does_not_hang_the_closure(self):
        """Mutual recursion is ordinary code; a fixpoint that revisits it forever is not."""
        with tempfile.TemporaryDirectory() as d:
            root = _plant(d, {
                "__init__.py": "",
                "loop.py": """
                    from .io import atomic_write_text

                    def a(n):
                        return b(n) if n else atomic_write_text("x", n)

                    def b(n):
                        return a(n - 1)
                """,
                "io.py": """
                    def atomic_write_text(path, text):
                        pass
                """,
            })
            direct = sweep._durable_write_sites(root)
            graph = _writegraph.build_call_graph(root, package="planted")
            closure = _writegraph.close_over_writers(graph, direct)
            self.assertIn(("loop.py", "a"), closure.direct)
            self.assertIn(("loop.py", "b"), closure.derived)
            self.assertEqual(closure.path_text(("loop.py", "b")), "loop.py:b -> loop.py:a")


# ======================================================================================
# THE REPORT. The numbers live in CI output, not in a stage prompt.
# ======================================================================================

class TestTheAuditReportsWhatItCanAndCannotSee(unittest.TestCase):

    def test_the_report_prints_direct_and_derived_counts_separately(self):
        """Deliverable 7 — a reader of CI output must be able to tell how much of the register is
        asserted by a human and how much is derived by the machine, without running anything."""
        case = sweep.TestZeroBypass("test_the_audit_report")
        with mock.patch("builtins.print") as printed:
            case.test_the_audit_report()
        text = "\n".join(str(call.args[0]) for call in printed.call_args_list if call.args)
        _graph, closure = sweep._write_closure()
        self.assertIn(f"DIRECT sites (asserted by a human): {len(closure.direct)}", text)
        self.assertIn(f"DERIVED sites (closed by the call graph): {len(closure.derived)}", text)
        self.assertIn(f"TOTAL sites the audit can see: {closure.total}", text)
        self.assertIn("resolver coverage:", text)

    def test_the_audit_states_what_the_resolver_cannot_see(self):
        """P16, honest degradation. This instrument's claim is 'everything it sees is real', never
        'it sees everything' — inherited methods, non-name receivers, callables passed as
        arguments and dynamic dispatch are all outside it. An audit that does not say so is making
        the SI.6 overclaim in a new place."""
        graph, _closure = sweep._write_closure()
        self.assertGreater(graph.unresolved_calls, 0,
                           "an unresolved count of zero would mean the resolver believes it sees "
                           "every call in the package, which is not true of any of the five rules")
        self.assertIn("WHAT THIS RESOLVER CANNOT SEE", _writegraph.__doc__)
        self.assertIn("unresolved", _writegraph.WriteGraphResult.__doc__)

    def test_the_failure_message_and_the_report_both_name_the_blind_spots(self):
        """A LIMITATION ONLY ITS AUTHOR READS IS NOT A DECLARED LIMITATION (0.0.17 review, F2).

        Before this, the sweep's failure said "a new durable write appeared in src/ and nobody said
        whether it is gated" — a completeness promise — while the shapes that evade it lived in a
        module docstring. Ten ordinary write shapes were confirmed to walk past the detector. The
        caveat must therefore ride BOTH reader-facing surfaces: the message someone gets when the
        sweep reds, and the report CI prints on every push.

        This test is the pin that stops the next author deleting the caveat as clutter. It asserts
        the two named limits specifically — the RECEIVER shape and the KEY COLLISION — rather than
        just 'some text is present', because those two are the ones that were measured false."""
        graph, _closure = sweep._write_closure()

        # 1) the FAILURE MESSAGE. Provoke it with a planted unregistered site so the real string is
        #    exercised, not a reconstruction of it.
        case = sweep.TestZeroBypass("test_every_durable_write_site_in_src_is_registered")
        with mock.patch.dict(sweep.GATED, clear=False):
            sweep.GATED.pop(("memory/edges.py", "project_edges"))
            with self.assertRaises(AssertionError) as caught:
                case.test_every_durable_write_site_in_src_is_registered()
        message = str(caught.exception)

        # 2) the AUDIT REPORT.
        report_case = sweep.TestZeroBypass("test_the_audit_report")
        with mock.patch("builtins.print") as printed:
            report_case.test_the_audit_report()
        report = "\n".join(str(c.args[0]) for c in printed.call_args_list if c.args)

        for surface, text in (("failure message", message), ("audit report", report)):
            self.assertIn("WHAT THIS SWEEP CANNOT SEE", text,
                          f"the {surface} does not declare its blind spots at all")
            self.assertIn("receiver is not a bare name", text,
                          f"the {surface} does not name the RECEIVER-SHAPE limit — the one that "
                          "let ten ordinary write shapes past the detector")
            self.assertIn("KEY COLLISIONS still live in src/", text,
                          f"the {surface} does not name the KEY-COLLISION limit — qualification "
                          "shrank it, it did not remove it")
            for shape in _writegraph.UNCLOSED_SHAPES:
                self.assertIn(shape, text,
                              f"the {surface} renders a PARAPHRASE, not the declared list: a "
                              "second copy of a caveat is a second thing to forget to update")

        self.assertIn("KEY COLLISIONS still live in src/ (1): "
                      "memory/store.py:MemoryStore.apply_proposal._commit [3 definitions]", report,
                      "the report must name the live collision by KEY and COUNT, and there must be "
                      "exactly ONE in src/. A measured residual is a different claim from a "
                      "declared one; a count that drifts silently is neither.")

    def test_both_reader_surfaces_name_the_directions_the_contract_runs(self):
        """COHERENCE-CONTRACT-ONE-DIRECTIONAL (doc 84), pinned the way the blind spots are.

        The contract ran ONE direction and nobody had written down which — so nobody noticed the
        other one was missing, and the sweep reported GREEN with an entry in it that its own words
        convicted. An instrument whose coverage must be re-derived from its CALL SITE is exactly
        the defect class this stage audits, one level up. So the directions ride BOTH reader-facing
        surfaces, rendered from `_writegraph.CONTRACT_DIRECTIONS` rather than paraphrased — a
        second copy of a caveat is a second thing to forget to update."""
        graph, _closure = sweep._write_closure()

        case = sweep.TestZeroBypass("test_no_write_free_claim_delegates_to_a_governed_write")
        liar = ("mcp/tools_share.py", "vault_push")
        with mock.patch.dict(sweep.UNGATED_BY_DESIGN, {liar: "PLANTED"}):
            with self.assertRaises(AssertionError) as caught:
                case.test_no_write_free_claim_delegates_to_a_governed_write()
        message = str(caught.exception)

        report_case = sweep.TestZeroBypass("test_the_audit_report")
        with mock.patch("builtins.print") as printed:
            report_case.test_the_audit_report()
        report = "\n".join(str(c.args[0]) for c in printed.call_args_list if c.args)

        for surface, text in (("failure message", message), ("audit report", report)):
            self.assertIn("DIRECTIONS THIS CONTRACT RUNS", text,
                          f"the {surface} does not say which directions the contract covers")
            for direction in _writegraph.CONTRACT_DIRECTIONS:
                self.assertIn(direction, text,
                              f"the {surface} renders a PARAPHRASE of the directions, not the "
                              "declared list")
            for unseen in _writegraph.UNSEEN_ENCLOSURE:
                self.assertIn(unseen, text,
                              f"the {surface} does not declare what gate enclosure CANNOT "
                              "establish — a direction-B green is only as good as that list")
        self.assertGreater(len(_writegraph.CONTRACT_DIRECTIONS), 1,
                           "the contract is one-directional again — that is the whole defect")

    def test_the_receiver_rule_resolves_a_constructor_bound_local(self):
        """RULE (e), the review's fix for the receiver gap: `store = SQLiteBackend(p)` then
        `store.put(item)` was invisible to BOTH instruments — the vocabulary detector's receiver
        whitelist is four bare names, and the resolver only ever handled `self.`.

        Both halves are asserted, and the negative half is the load-bearing one: a receiver bound
        to something that is NOT a known constructor must stay unresolved rather than guess. That
        is the difference between closing a gap and inventing an edge."""
        with tempfile.TemporaryDirectory() as d:
            root = _plant(d, {
                "__init__.py": "",
                "io.py": """
                    def atomic_write_text(path, text):
                        pass
                """,
                "store.py": """
                    from .io import atomic_write_text

                    class Store:
                        def save(self, item):
                            atomic_write_text("s", item)

                    def make_store():
                        return Store()
                """,
                "caller.py": """
                    from .store import Store, make_store

                    def by_constructor(item):
                        s = Store()
                        return s.save(item)          # rule (e): a KNOWN constructor

                    def by_factory(item):
                        s = make_store()
                        return s.save(item)          # NOT closed — declared, not guessed

                    def by_missing_method(item):
                        s = Store()
                        return s.absent(item)        # Store has no `absent` — no edge to invent

                    def by_rebound(item):
                        s = Store()
                        s = make_store()             # REBOUND to something unknown
                        return s.save(item)          # the stale binding must not survive

                    def by_shadowed(item):
                        s = Store()                  # an OUTER binding that IS a constructor

                        def inner(x):
                            s = make_store()         # shadows it, in a nested scope
                            return s.save(x)         # must not reach the outer binding

                        return inner(item)
                """,
            })
            direct = sweep._durable_write_sites(root)
            graph = _writegraph.build_call_graph(root, package="planted")
            closure = _writegraph.close_over_writers(graph, direct)

            self.assertIn(("store.py", "Store.save"), direct)
            self.assertEqual(closure.path_text(("caller.py", "by_constructor")),
                             "caller.py:by_constructor -> store.py:Store.save",
                             "rule (e) must resolve a bare-name receiver bound to a constructor")
            self.assertNotIn(("caller.py", "by_factory"), closure.derived,
                             "a FACTORY-bound receiver must stay unresolved — inferring a return "
                             "type wrongly is worse than the declared gap, and the gap IS declared "
                             "(see _writegraph.UNCLOSED_SHAPES)")
            self.assertNotIn(("store.py", "Store.absent"),
                             graph.callees(("caller.py", "by_missing_method")),
                             "the bound class does not DEFINE that method — resolving it anyway "
                             "would be a bare-name match wearing a receiver, the 62 -> 1,650 shape")
            # `s = Store()` IS itself a resolved call, to a target that is not a def. Counted as a
            # dangling target and declared, never quietly folded into "resolved to a definition".
            self.assertGreater(graph.dangling_targets, 0)
            self.assertNotIn(("caller.py", "by_rebound"), closure.derived,
                             "a receiver REBOUND to something unknown must lose its binding — a "
                             "stale binding that outlives its assignment is an invented edge")
            self.assertNotIn(("caller.py", "by_shadowed.inner"), closure.derived,
                             "a nested scope's `s = make_store()` must SHADOW the enclosing "
                             "scope's `s = Store()`. This is the half that only a NESTED rebind "
                             "reaches: a lookup that walks outwards past a known-not-a-constructor "
                             "binding instead of stopping at it will find the outer one and invent "
                             "the edge. (M07 survived the first mutation pass on exactly this gap — "
                             "the same-scope rebind above does not distinguish the two lookups.)")
            self.assertGreater(graph.receiver_calls, 0,
                               "rule (e) resolved nothing at all — it is inert, not additive")

    def test_a_bare_call_does_not_reach_a_sibling_method(self):
        """RULE (a) IS LEXICAL, and class scopes do not participate — because Python's do not.

        Inside `Service.sneak`, a bare `write_it(...)` cannot reach `Service.write_it`; it reaches
        the MODULE-level `write_it`, and if there is none it reaches nothing. Letting the resolver
        climb through the class scope would make every method reachable from every sibling by bare
        name, which is a bare-name match with extra steps — the 62 -> 1,650 explosion again, this
        time inside a class body."""
        with tempfile.TemporaryDirectory() as d:
            root = _plant(d, {
                "__init__.py": "",
                "io.py": """
                    def atomic_write_text(path, text):
                        pass
                """,
                "svc2.py": """
                    from .io import atomic_write_text

                    class Service:
                        def write_it(self, x):
                            atomic_write_text("svc", x)

                        def sneak(self, x):
                            return write_it(x)       # NOT self.write_it — a bare name
                """,
                "svc3.py": """
                    from .svc2 import Service

                    class Holder:
                        svc = Service()              # a CLASS-BODY binding

                        def sneak(self, x):
                            return svc.write_it(x)   # a method cannot see it by bare name
                """,
            })
            direct = sweep._durable_write_sites(root)
            graph = _writegraph.build_call_graph(root, package="planted")
            closure = _writegraph.close_over_writers(graph, direct)

            self.assertIn(("svc2.py", "Service.write_it"), direct)
            self.assertEqual(graph.callees(("svc2.py", "Service.sneak")), frozenset(),
                             "a bare call climbed INTO the enclosing class scope — Python does not "
                             "do that, and a resolver that does is inventing edges")
            self.assertNotIn(("svc2.py", "Service.sneak"), closure.derived)

            # the same refusal on rule (e)'s side: a CLASS-BODY receiver binding is not visible to
            # a method body, so it must not be offered to one.
            self.assertNotIn(("svc3.py", "Holder.sneak"), closure.derived,
                             "rule (e) reached a class-BODY binding from a method body — `_lexical` "
                             "already refuses that climb, and the receiver lookup must refuse it "
                             "for the identical reason")


if __name__ == "__main__":
    unittest.main()
