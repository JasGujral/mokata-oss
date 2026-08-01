"""SECRET-IGNORE — a road out of a secret-scan FALSE POSITIVE, without the floor moving.

WHY THIS FILE EXISTS. `secret-guard` is non-overridable (G4/I1), and that is right for a
recognised credential. It is wrong for a GUESS: SECRET-VALUE-SCAN cut corpus false positives
13->4 and MEASURED two residual classes as surviving, so "the predicate will eventually be
perfect" is not a plan. Today a user whom the entropy backstop guesses wrong at is walled with
no road out at all, and 0.0.15 measured them ABANDONING mokata over it.

THE THREAT MODEL THIS IS DESIGNED TO (deliverable 0), asserted here rather than merely written
down, so a later change cannot quietly widen it:

  DEFENDS AGAINST: a false positive from the ENTROPY GUESS costing a user their day. That is
  the whole of it.

  DOES NOT DEFEND AGAINST: a determined user who wants to commit a credential. They own the
  repo, the file, and the commit; they can delete the hook, uninstall mokata, or `git commit
  --no-verify`. No allowlist design changes that, and pretending otherwise would be the
  dangerous comment this stage must not write.

  SO THE PROPERTY THAT ACTUALLY BUYS SAFETY IS NOT SECRECY, IT IS REVIEWABILITY: the ignore is
  version-controlled, so it lands in a PR diff where a human sees that someone suppressed a
  secret finding, and why. Everything below exists to keep that true, and to keep the
  non-negotiable layers (signatures + the known-shape floor) genuinely non-negotiable.

Real-secret literals and the measured false-positive identifiers are both ASSEMBLED from
sub-20-char fragments at runtime (the convention of test_secret_corpus / test_secret_fp /
test_secret_value_scan) so this source file carries no blockable literal — the FP identifiers
BLOCK on 0.0.15, which is the bug, demonstrated on this file.
All "secrets" here are LEAK-CANARY fakes — never a real key.
Dependency-free, deterministic.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.govern import secrets
from mokata.govern.secrets import scan

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "src", "mokata", "hooks", "secret_guard.py")


def j(*parts):
    """Join fragments — the runtime string; the source only holds the (benign) fragments."""
    return "".join(parts)


# ── The MEASURED false positives this feature exists for: an ordinary identifier the entropy
#    backstop GUESSES is a credential, so `is_ignorable` is true of it.
#
#    REPOINTED 2026-07-31 (`84:74` + `84:68`, the function-word list). These were the two
#    `KNOWN_SURVIVORS` of test_secret_value_scan — a test CLASS name and an identifier used as
#    a VALUE — and both are now FIXED at the predicate, so neither blocks and neither could
#    still serve as a live-false-positive fixture. They are replaced by the residual class that
#    genuinely survives: acronym DENSITY. An all-acronym constant has no function word and no
#    segment ≥4 chars, so it fails the word-structure majorities and still blocks
#    (`test_secret_value_scan.test_residual_2_acronym_density_is_STILL_LIVE_when_bare`,
#    `test_secret_fp.test_the_acronym_density_residual_is_NOT_claimed_fixed`).
#
#    That the fixture had to be replaced at all is the feature working as designed: SECRET-
#    IGNORE is for the guess that is wrong TODAY, and the right long-run repair is to fix the
#    predicate — which is what removed the old pair. ────────────────────────────────────────
SURVIVOR = j("API_AWS_KEY", "_MAX_TTL", "_SEC_V2")      # acronym-dense: residual (2), R7/0.1.2
SURVIVOR_2 = j("JWT_RSA", "_ECDSA_HS", "_ALG_V2_SET")   # the same class, a distinct token

# ── LEAK-CANARY credentials, by layer. ────────────────────────────────────────────────────
AWS_KEY = j("AKIA", "IOSFODNN7", "EXAMPLE")             # signature + known-shape floor
AWS_KEY_CHUNKED = j("AKIA", "_", "IOSFODNN7", "_", "EXAMPLE")
GH_TOKEN = j("ghp_", "abcdefghij", "klmnopqrst", "uvwxyz0123", "456789")
# An UNKNOWN-vendor key: no signature, no known shape — the entropy guess is all that catches
# it, so it is the one credential class this feature can be pointed at. Named, not hidden.
UNKNOWN_KEY = j("xY9kZ2mQ7p", "L4nR8vT3wB", "6cF1dG5hJ0")

TARGET = "src/app/config.py"
OTHER = "src/app/other.py"


def forge_store(path, entries):
    """Write a store with a VALID checksum — exactly what a determined user does once the
    hand-edit refusal tells them the file is checksummed (the algorithm is in the source they
    already have). It lives HERE, not in `src/`, so production ships no test-only writer; the
    adversarial suite uses it to prove the safety does not rest on the checksum at all."""
    from mokata.govern import secret_ignore
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(secret_ignore._render(list(entries)))


def render_of(root):
    from mokata.govern.secret_ignore import render_list
    return render_list(root)


def _entropy_kinds(text, **kw):
    return [f.kind for f in scan(text=text, **kw) if f.layer == "entropy"]


class _RepoCase(unittest.TestCase):
    """A real on-disk mokata repo: the store is a committed file, so nothing here is faked."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="mokata-ignore-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        mdir = os.path.join(self.root, ".mokata")
        os.makedirs(mdir)
        with open(os.path.join(mdir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(sample_manifest_data(), fh)
        for rel in (TARGET, OTHER):
            os.makedirs(os.path.join(self.root, os.path.dirname(rel)), exist_ok=True)
            with open(os.path.join(self.root, rel), "w", encoding="utf-8") as fh:
                fh.write("# placeholder\n")

    def store(self):
        from mokata.govern.secret_ignore import IgnoreStore
        return IgnoreStore.load(self.root)

    def add(self, token=SURVIVOR, path=TARGET, reason="measured false positive"):
        from mokata.govern import secret_ignore
        return secret_ignore.add_ignore(self.root, token, path, reason=reason,
                                        assume_yes=True, out=lambda _m: None)

    def store_text(self):
        from mokata.govern.secret_ignore import ignores_path
        with open(ignores_path(self.root), encoding="utf-8") as fh:
            return fh.read()


# ══════════════════════════════════════════════════════════════════════════════════════════
# DELIVERABLE 1 — the repro. A measured false positive blocks, and the block names no remedy.
# ══════════════════════════════════════════════════════════════════════════════════════════

class TestTheFalsePositiveBlocksToday(_RepoCase):

    def test_a_measured_survivor_is_blocked_by_the_entropy_guess(self):
        """Not a hypothetical: both rows are the acronym-density residual that
        `test_secret_value_scan.test_residual_2_acronym_density_is_STILL_LIVE_when_bare`
        measures as open — an identifier this layer cannot tell from a key without a
        dictionary it deliberately does not have."""
        for row in (SURVIVOR, SURVIVOR_2):
            with self.subTest(row=row):
                self.assertEqual(_entropy_kinds(row, path=TARGET), ["high-entropy-token"])

    def test_the_block_message_names_the_remedy(self):
        """RED BEFORE: the block said what was wrong and offered nothing to do about it — on a
        gate with no override, that is a total wall. It must now name the exact command."""
        from mokata.govern.secret_ignore import render_block
        msg = render_block(scan(text=SURVIVOR, path=TARGET), path=TARGET)
        self.assertIn("mokata secret ignore", msg)
        self.assertIn(TARGET, msg)
        self.assertIn(SURVIVOR, msg, "the command must be pasteable as printed")

    def test_the_hook_itself_names_the_remedy(self):
        """The surface the walled user actually meets."""
        proc = subprocess.run([sys.executable, HOOK, "--text", SURVIVOR, "--path", TARGET],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("mokata secret ignore", proc.stderr)


# ══════════════════════════════════════════════════════════════════════════════════════════
# DELIVERABLE 2 — the store. Hash-keyed, path-scoped, checksummed, LITERAL NEVER WRITTEN.
# ══════════════════════════════════════════════════════════════════════════════════════════

class TestTheLiteralIsNeverWritten(_RepoCase):
    """(c) — writing the flagged token into a committed file would persist the very thing the
    scanner exists to keep out of the repo. Pinned on EVERY path that touches the store."""

    def test_the_literal_is_absent_after_add(self):
        self.add()
        self.assertNotIn(SURVIVOR, self.store_text())

    def test_the_literal_is_absent_after_several_adds_and_a_remove(self):
        from mokata.govern import secret_ignore
        self.add(SURVIVOR, TARGET)
        self.add(SURVIVOR_2, TARGET)
        self.add(SURVIVOR, OTHER)
        secret_ignore.remove_ignore(self.root, SURVIVOR_2, TARGET, assume_yes=True,
                                    out=lambda _m: None)
        text = self.store_text()
        for literal in (SURVIVOR, SURVIVOR_2):
            self.assertNotIn(literal, text)

    def test_no_prefix_of_the_literal_leaks_either(self):
        """A "just a hint" prefix is still the secret's first bytes. There must be none."""
        self.add()
        text = self.store_text()
        for n in range(8, len(SURVIVOR) + 1):
            self.assertNotIn(SURVIVOR[:n], text)

    def test_the_ledger_never_carries_the_literal_either(self):
        from mokata.govern.ledger import AuditLedger
        self.add()
        entries = AuditLedger.from_mokata_dir(os.path.join(self.root, ".mokata")).entries()
        self.assertNotIn(SURVIVOR, json.dumps(entries))

    def test_what_is_stored_is_the_sha256_of_the_token(self):
        import hashlib
        self.add()
        digest = hashlib.sha256(SURVIVOR.encode("utf-8")).hexdigest()
        self.assertIn(digest, self.store_text())


class TestTheStoreIsVersionControlled(_RepoCase):
    """(e) — the safety property is REVIEWABILITY. A local-only file would suppress a secret
    finding invisibly; a committed one lands in the PR diff with its reason."""

    def test_it_sits_at_the_committed_mokata_root_not_under_temp_local(self):
        from mokata.govern.secret_ignore import ignores_path
        path = ignores_path(self.root)
        self.assertEqual(os.path.dirname(path), os.path.join(self.root, ".mokata"))
        self.assertNotIn("temp_local", path)

    def test_the_reason_is_required_and_persisted(self):
        self.add(reason="draft-validator attribute name, not a key")
        self.assertIn("draft-validator attribute name, not a key", self.store_text())


class TestHandEditsAreRefused(_RepoCase):
    """(d) — the file carries an integrity checksum. It is a SPEED BUMP and an AUDIT TRAIL, not
    a security boundary: anyone can recompute it, and `TestAdversarial` proves a fully forged
    store still cannot launder a recognised credential."""

    def _hand_edit(self):
        from mokata.govern.secret_ignore import ignores_path
        path = ignores_path(self.root)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["entries"].append({"hash": "0" * 64, "path": OTHER, "reason": "snuck in",
                                "added_at": "2026-07-28T00:00:00+00:00", "actor": "hand"})
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def test_loading_a_hand_edited_store_raises(self):
        from mokata.govern.secret_ignore import IgnoreStore, TamperedIgnoreFile
        self.add()
        self._hand_edit()
        with self.assertRaises(TamperedIgnoreFile) as ctx:
            IgnoreStore.load(self.root)
        self.assertIn("Re-add it via the CLI",str(ctx.exception))

    def test_a_hand_edited_store_suppresses_NOTHING(self):
        """Fail CLOSED: a store that cannot be trusted grants no ignores at all — including the
        ones that were legitimately there before the edit."""
        self.add()
        self._hand_edit()
        from mokata.govern.secret_ignore import load_for_scan
        store, notice = load_for_scan(self.root)
        self.assertIsNone(store)
        self.assertIn("Re-add it via the CLI",notice)
        self.assertEqual(_entropy_kinds(SURVIVOR, path=TARGET, ignores=store),
                         ["high-entropy-token"])


# ══════════════════════════════════════════════════════════════════════════════════════════
# DELIVERABLE 3 — the CLI is the only entry, and it REFUSES the non-negotiable layers BY NAME.
# ══════════════════════════════════════════════════════════════════════════════════════════

class TestOnlyTheGuessingLayerIsNegotiable(_RepoCase):
    """(a) — the entire reversal is safe only because this holds."""

    def test_a_signature_layer_credential_is_refused_by_name(self):
        from mokata.govern.secret_ignore import NotIgnorable, add_ignore
        with self.assertRaises(NotIgnorable) as ctx:
            add_ignore(self.root, AWS_KEY, TARGET, reason="trust me", assume_yes=True,
                       out=lambda _m: None)
        self.assertEqual(ctx.exception.shape, "aws-access-key")
        self.assertIn("aws-access-key", str(ctx.exception))

    def test_the_refusal_states_why_not_merely_that(self):
        from mokata.govern.secret_ignore import NotIgnorable, add_ignore
        with self.assertRaises(NotIgnorable) as ctx:
            add_ignore(self.root, GH_TOKEN, TARGET, reason="trust me", assume_yes=True,
                       out=lambda _m: None)
        msg = str(ctx.exception)
        self.assertEqual(ctx.exception.shape, "github-token")
        self.assertIn("recognised credential", msg)
        self.assertIn("entropy", msg, "the refusal must say WHICH layer is negotiable")

    def test_a_chunked_known_shape_is_refused_too(self):
        """The known-shape floor tests separator-stripped, so chunking buys nothing here either."""
        from mokata.govern.secret_ignore import NotIgnorable, add_ignore
        with self.assertRaises(NotIgnorable) as ctx:
            add_ignore(self.root, AWS_KEY_CHUNKED, TARGET, reason="trust me", assume_yes=True,
                       out=lambda _m: None)
        self.assertEqual(ctx.exception.shape, "aws-access-key")

    def test_a_string_the_backstop_never_flags_is_refused(self):
        """Nothing to ignore. This also stops the store being pre-seeded with short fragments."""
        from mokata.govern.secret_ignore import IgnoreError, add_ignore
        with self.assertRaises(IgnoreError):
            add_ignore(self.root, "hello", TARGET, reason="x", assume_yes=True,
                       out=lambda _m: None)

    def test_a_known_shape_finding_is_not_ignorable_even_if_the_store_says_so(self):
        """`known-secret-shape` is reported on the ENTROPY layer but is part of the FLOOR, not
        the guess. Layer alone is therefore NOT the test — kind is."""
        from mokata.govern.secret_ignore import is_ignorable
        floor = [f for f in scan(text=AWS_KEY_CHUNKED)
                 if f.kind == "known-secret-shape"]
        self.assertTrue(floor, "fixture no longer reaches the known-shape floor")
        for f in floor:
            self.assertEqual(f.layer, "entropy")
            self.assertFalse(is_ignorable(f))

    def test_the_reason_is_required(self):
        from mokata.govern.secret_ignore import IgnoreError, add_ignore
        for bad in ("", "   "):
            with self.subTest(reason=bad):
                with self.assertRaises(IgnoreError):
                    add_ignore(self.root, SURVIVOR, TARGET, reason=bad, assume_yes=True,
                               out=lambda _m: None)


class TestTheCliSurface(_RepoCase):
    """`mokata secret ignore` / `ignores` / `--remove`."""

    def _cli(self, *argv):
        from mokata.cli import main
        import io
        import contextlib
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            code = main(["secret", *argv, "--path", self.root])
        return code, buf.getvalue() + err.getvalue()

    def test_ignore_adds_an_entry(self):
        code, _out = self._cli("ignore", "--token", SURVIVOR, "--file", TARGET,
                               "--reason", "measured FP", "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.store().entries()), 1)

    def test_reason_is_required(self):
        code, out = self._cli("ignore", "--token", SURVIVOR, "--file", TARGET, "--yes")
        self.assertNotEqual(code, 0)
        self.assertIn("reason", out)
        self.assertEqual(len(self.store().entries()), 0)

    def test_ignores_lists_them_without_the_literal(self):
        self.add()
        code, out = self._cli("ignores")
        self.assertEqual(code, 0)
        self.assertIn(TARGET, out)
        self.assertIn("measured false positive", out)
        self.assertNotIn(SURVIVOR, out)

    def test_remove_revokes_and_the_token_blocks_again(self):
        self.add()
        self.assertEqual(_entropy_kinds(SURVIVOR, path=TARGET, ignores=self.store()), [])
        code, _out = self._cli("ignore", "--remove", "--token", SURVIVOR, "--file", TARGET,
                               "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(_entropy_kinds(SURVIVOR, path=TARGET, ignores=self.store()),
                         ["high-entropy-token"])

    def test_remove_accepts_the_truncated_hash_the_listing_prints(self):
        """The listing→remove loop must actually close: `ignores` prints an abbreviated hash, so
        pasting exactly what is on screen (ellipsis and all) has to work, git-style."""
        self.add()
        _code, listed = self._cli("ignores")
        shown = next(w for w in listed.split() if w.startswith(self.store().entries()[0].hash[:8]))
        code, _out = self._cli("ignore", "--remove", "--hash", shown, "--file", TARGET, "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(self.store().entries(), [])

    def test_an_ambiguous_hash_prefix_is_refused_rather_than_guessed(self):
        from mokata.govern.secret_ignore import IgnoreError, remove_ignore
        self.add(SURVIVOR, TARGET)
        self.add(SURVIVOR_2, TARGET)
        with self.assertRaises(IgnoreError):
            remove_ignore(self.root, "", TARGET, assume_yes=True, out=lambda _m: None)

    def test_a_refused_shape_exits_non_zero_and_names_it(self):
        code, out = self._cli("ignore", "--token", AWS_KEY, "--file", TARGET,
                              "--reason", "trust me", "--yes")
        self.assertNotEqual(code, 0)
        self.assertIn("aws-access-key", out)


# ══════════════════════════════════════════════════════════════════════════════════════════
# DELIVERABLE 4 — the scanner honours ignores at the ENTROPY layer only, and stays VISIBLE.
# ══════════════════════════════════════════════════════════════════════════════════════════

class TestTheIgnoreIsAsNarrowAsItLooks(_RepoCase):
    """(b) — hash(token) + path. The narrowest unit: this exact string, in this file."""

    def test_that_token_in_that_file_stops_blocking(self):
        self.add()
        self.assertEqual(_entropy_kinds(SURVIVOR, path=TARGET, ignores=self.store()), [])

    def test_the_same_token_in_another_file_still_blocks(self):
        self.add()
        self.assertEqual(_entropy_kinds(SURVIVOR, path=OTHER, ignores=self.store()),
                         ["high-entropy-token"])

    def test_another_token_in_the_same_file_still_blocks(self):
        self.add()
        self.assertEqual(_entropy_kinds(SURVIVOR_2, path=TARGET, ignores=self.store()),
                         ["high-entropy-token"])

    def test_a_scan_with_no_path_is_never_suppressed(self):
        """An entry is path-SCOPED, so a pathless scan (a Bash command line, an egress check)
        can never match one. Fail-closed by construction."""
        self.add()
        self.assertEqual(_entropy_kinds(SURVIVOR, ignores=self.store()),
                         ["high-entropy-token"])

    def test_an_absolute_path_resolves_to_the_same_entry(self):
        self.add()
        self.assertEqual(
            _entropy_kinds(SURVIVOR, path=os.path.join(self.root, TARGET),
                           ignores=self.store()), [])

    def test_the_signature_layer_is_untouched_by_any_ignore(self):
        self.add()
        line = 'API_KEY = "' + UNKNOWN_KEY + '"'
        kinds = [f.kind for f in scan(text=line, path=TARGET, ignores=self.store())]
        self.assertIn("secret-assignment", kinds)

    def test_egress_is_computed_after_suppression_not_before(self):
        """An ignored finding is not a secret, so it must not manufacture an egress block —
        and a real one still must."""
        self.add()
        clean = scan(text=SURVIVOR, path=TARGET, for_send=True, ignores=self.store())
        self.assertEqual(clean, [])
        dirty = scan(text=AWS_KEY, path=TARGET, for_send=True, ignores=self.store())
        self.assertTrue(any(f.layer == "egress" for f in dirty))


class TestSuppressedIsNotForgotten(_RepoCase):
    """(f) — an ignore that nobody can see is how a suppression list rots."""

    def test_doctor_names_the_active_count(self):
        from mokata.govern.doctor import secret_ignore_findings
        self.add(SURVIVOR, TARGET)
        self.add(SURVIVOR_2, TARGET)
        findings = secret_ignore_findings(_Surface(self.root))
        self.assertTrue(findings)
        self.assertIn("2", findings[0].detail)
        self.assertIn("secret", findings[0].detail)

    def test_doctor_is_silent_when_there_are_none(self):
        from mokata.govern.doctor import secret_ignore_findings
        self.assertEqual(secret_ignore_findings(_Surface(self.root)), [])

    def test_doctor_reports_a_tampered_store_as_an_error(self):
        from mokata.govern.doctor import secret_ignore_findings
        from mokata.govern.secret_ignore import ignores_path
        self.add()
        path = ignores_path(self.root)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["entries"][0]["path"] = OTHER          # a real hand-edit: the SCOPE was widened
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        findings = secret_ignore_findings(_Surface(self.root))
        self.assertEqual([f.severity for f in findings], ["error"])

    def test_reformatting_the_file_is_not_treated_as_tampering(self):
        """The checksum covers the ENTRIES, not the bytes — so `git` normalising whitespace, or
        a formatter reflowing the JSON, must not void a repo's whole ignore list."""
        from mokata.govern.doctor import secret_ignore_findings
        from mokata.govern.secret_ignore import ignores_path
        self.add()
        path = ignores_path(self.root)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=8)           # same entries, different bytes
        self.assertEqual([f.severity for f in secret_ignore_findings(_Surface(self.root))],
                         ["info"])

    def test_an_inert_entry_is_named(self):
        """The no-TTL hygiene answer: an entry that suppresses nothing is SAID to suppress
        nothing, so the list is pruned on evidence rather than on a clock."""
        from mokata.govern.doctor import secret_ignore_findings
        self.add()
        with open(os.path.join(self.root, TARGET), "w", encoding="utf-8") as fh:
            fh.write("x = 1  # the flagged identifier is gone\n")
        detail = secret_ignore_findings(_Surface(self.root))[0].detail
        self.assertIn("inert", detail.lower())


class _Surface:
    """The two attributes `secret_ignore_findings` reads (same shape as every other doctor
    helper's surface use)."""

    def __init__(self, root):
        self.root = root
        self.mokata_dir = os.path.join(root, ".mokata")


class TestExpiryDecision(_RepoCase):
    """Deliverable 6 — entries do NOT expire on a clock. Grounding, pinned as behaviour:

    A TTL re-walls a user on a day they changed nothing, which is precisely the failure this
    stage exists to remove — reintroduced on a timer. What a TTL is actually reaching for is
    "do not let the list rot", and (f) delivers that better and without a clock: the entry is
    keyed to hash(token)+path, so it EXPIRES ON CONTENT — fix the identifier, rename the file,
    delete it, and the entry stops matching anything and is reported STALE. Hygiene on
    evidence, not on a calendar."""

    def test_an_entry_carries_no_expiry_field(self):
        self.add()
        entry = self.store().entries()[0]
        self.assertFalse([k for k in vars(entry) if "expir" in k or k in ("ttl", "until")])

    def test_an_entry_does_not_lapse_with_time(self):
        from mokata.govern.secret_ignore import IgnoreStore, ignores_path
        self.add()
        with open(ignores_path(self.root), encoding="utf-8") as fh:
            data = json.load(fh)
        data["entries"][0]["added_at"] = "2019-01-01T00:00:00+00:00"
        forge_store(ignores_path(self.root), data["entries"])   # re-checksummed, so it loads
        self.assertEqual(_entropy_kinds(SURVIVOR, path=TARGET,
                                        ignores=IgnoreStore.load(self.root)), [])

    def test_content_expiry_is_what_reports_instead(self):
        """An entry stops mattering when its CONTENT changes, and that is what gets reported."""
        self.add()
        with open(os.path.join(self.root, TARGET), "w", encoding="utf-8") as fh:
            fh.write("x = " + SURVIVOR + "\n")
        self.assertEqual(self.store().inert(), [])            # still present → still active
        with open(os.path.join(self.root, TARGET), "w", encoding="utf-8") as fh:
            fh.write("x = 1  # identifier renamed; the ignore now matches nothing\n")
        self.assertEqual([e.path for e, _why in self.store().inert()], [TARGET])

    def test_a_pending_write_is_not_reported_as_rot(self):
        """Found in the LIVE demo, not by inspection. The commonest way to record an ignore is
        from a BLOCKED write — so at that moment the target file does not exist yet, because the
        write it unblocks has not been retried. Calling that "stale (file gone)" makes the very
        first thing a freshly-unwalled user sees read like a mistake."""
        pending = "src/app/not_written_yet.py"
        self.add(SURVIVOR, pending)
        why = dict((e.path, w) for e, w in self.store().inert()).get(pending, "")
        self.assertNotIn("stale", (why + render_of(self.root)).lower())
        self.assertNotIn("gone", why.lower())


# ══════════════════════════════════════════════════════════════════════════════════════════
# DELIVERABLE 5 — ONE shared builder; identical wording on the CLI hook and the MCP gate.
# ══════════════════════════════════════════════════════════════════════════════════════════

class TestOneBuilderTwoSurfaces(_RepoCase):

    def test_the_gate_reason_is_the_shared_builder_output(self):
        from mokata.govern.gate import WriteGate, WriteRequest
        from mokata.govern.secret_ignore import render_block
        content = SURVIVOR
        outcome = WriteGate().submit(
            WriteRequest(kind="code", target=os.path.join(self.root, TARGET), content=content),
            assume_yes=True)
        self.assertFalse(outcome.committed)
        expected = render_block(outcome.findings, path=os.path.join(self.root, TARGET))
        self.assertEqual(outcome.reason, expected)

    def test_both_surfaces_print_the_same_remedy_line(self):
        from mokata.govern.gate import WriteGate, WriteRequest
        target = os.path.join(self.root, TARGET)
        hook = subprocess.run([sys.executable, HOOK, "--text", SURVIVOR, "--path", target],
                              capture_output=True, text=True).stderr
        gate = WriteGate().submit(
            WriteRequest(kind="code", target=target, content=SURVIVOR),
            assume_yes=True).reason
        remedy = [ln.strip() for ln in gate.splitlines()
                  if ln.strip().startswith("mokata secret ignore")]
        self.assertTrue(remedy, "the gate surface named no remedy")
        for line in remedy:
            self.assertIn(line, hook, "the two surfaces drifted — wording must come from ONE "
                                      "builder, never be written inside a tool")

    def test_a_non_negotiable_finding_offers_no_command(self):
        from mokata.govern.secret_ignore import render_block
        msg = render_block(scan(text=AWS_KEY, path=TARGET), path=TARGET)
        self.assertNotIn("mokata secret ignore", msg)
        self.assertIn("aws-access-key", msg)

    def test_the_printed_command_actually_works_when_pasted(self):
        """The point of the whole stage: the walled user copies one line and is unwalled."""
        import shlex
        from mokata.govern.secret_ignore import render_block
        msg = render_block(scan(text=SURVIVOR, path=TARGET), path=TARGET)
        line = next(ln.strip() for ln in msg.splitlines()
                    if ln.strip().startswith("mokata secret ignore"))
        argv = shlex.split(line)
        self.assertEqual(argv[:3], ["mokata", "secret", "ignore"])
        from mokata.cli import main
        import io
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = main(argv[1:] + ["--yes", "--path", self.root])
        self.assertEqual(code, 0)
        self.assertEqual(_entropy_kinds(SURVIVOR, path=TARGET, ignores=self.store()), [])


# ══════════════════════════════════════════════════════════════════════════════════════════
# THE ADVERSARIAL PASS — can a REAL credential be laundered through this feature?
# This is the test that decides whether the reversal was safe.
# ══════════════════════════════════════════════════════════════════════════════════════════

class TestAdversarialLaundering(_RepoCase):

    def _blocks(self, text, path=TARGET, store=None):
        try:
            store = store if store is not None else self.store()
        except Exception:                     # a tampered store grants nothing
            store = None
        return bool(scan(text=text, path=path, ignores=store))

    def test_route_1_ignore_the_credential_directly(self):
        from mokata.govern.secret_ignore import NotIgnorable, add_ignore
        with self.assertRaises(NotIgnorable):
            add_ignore(self.root, AWS_KEY, TARGET, reason="x", assume_yes=True,
                       out=lambda _m: None)
        self.assertTrue(self._blocks(AWS_KEY))

    def test_route_2_chunk_it_first(self):
        from mokata.govern.secret_ignore import NotIgnorable, add_ignore
        with self.assertRaises(NotIgnorable):
            add_ignore(self.root, AWS_KEY_CHUNKED, TARGET, reason="x", assume_yes=True,
                       out=lambda _m: None)
        self.assertTrue(self._blocks(AWS_KEY_CHUNKED))

    def test_route_3_ignore_a_benign_token_then_swap_the_credential_in(self):
        self.add(SURVIVOR, TARGET)
        self.assertTrue(self._blocks(AWS_KEY))
        self.assertTrue(self._blocks(UNKNOWN_KEY))

    def test_route_4_rename_the_binding_around_it(self):
        self.add(SURVIVOR, TARGET)
        for name in ("SURVIVOR", "harmless_looking_name", SURVIVOR):
            with self.subTest(name=name):
                self.assertTrue(self._blocks(name + ' = "' + AWS_KEY + '"'))

    def test_route_5_move_the_file(self):
        self.add(SURVIVOR, TARGET)
        self.assertTrue(self._blocks(SURVIVOR, path=OTHER))

    def test_route_6_forge_the_checksum_by_hand(self):
        """The honest one. The checksum is NOT a security boundary — a determined user can
        recompute it. So the safety must not rest on it: even a PERFECTLY forged store cannot
        launder a recognised credential, because the signature layer and the known-shape floor
        never consult ignores at all."""
        import hashlib
        from mokata.govern.secret_ignore import IgnoreStore, ignores_path
        forge_store(ignores_path(self.root), [
            {"hash": hashlib.sha256(AWS_KEY.encode()).hexdigest(), "path": TARGET,
             "reason": "forged", "added_at": "2026-07-28T00:00:00+00:00", "actor": "attacker"}])
        store = IgnoreStore.load(self.root)        # checksum is valid — the forge succeeded
        self.assertEqual(len(store.entries()), 1)
        self.assertTrue(scan(text=AWS_KEY, path=TARGET, ignores=store),
                        "LAUNDERED — a forged store suppressed a recognised credential")

    def test_route_7_forge_an_ignore_for_every_layer_at_once(self):
        import hashlib
        from mokata.govern.secret_ignore import IgnoreStore, ignores_path
        rows = []
        for tok in (AWS_KEY, AWS_KEY_CHUNKED, GH_TOKEN):
            rows.append({"hash": hashlib.sha256(tok.encode()).hexdigest(), "path": TARGET,
                         "reason": "forged", "added_at": "2026-07-28T00:00:00+00:00",
                         "actor": "attacker"})
        forge_store(ignores_path(self.root), rows)
        store = IgnoreStore.load(self.root)
        for tok in (AWS_KEY, AWS_KEY_CHUNKED, GH_TOKEN):
            with self.subTest(token=tok[:4]):
                self.assertTrue(scan(text=tok, path=TARGET, ignores=store))

    def test_route_8_path_traversal_out_of_the_repo(self):
        from mokata.govern.secret_ignore import IgnoreError, add_ignore
        for bad in ("../outside.py", "/etc/passwd", "..", ""):
            with self.subTest(path=bad):
                with self.assertRaises(IgnoreError):
                    add_ignore(self.root, SURVIVOR, bad, reason="x", assume_yes=True,
                               out=lambda _m: None)

    def test_route_9_a_directory_or_glob_cannot_be_scoped(self):
        from mokata.govern.secret_ignore import IgnoreError, add_ignore
        for bad in ("src/", "src/*.py", "src/app/*"):
            with self.subTest(path=bad):
                with self.assertRaises(IgnoreError):
                    add_ignore(self.root, SURVIVOR, bad, reason="x", assume_yes=True,
                               out=lambda _m: None)

    def test_the_residual_is_named_not_hidden(self):
        """An UNKNOWN-vendor credential has no signature and no known shape — the entropy guess
        is the only thing that ever caught it, so it IS ignorable. That is the accepted residual
        of the reversal, and it is contained rather than denied: one exact string, in one exact
        file, listed, ledgered, counted by doctor, and visible in the PR diff."""
        self.add(UNKNOWN_KEY, TARGET, reason="believed benign")
        self.assertEqual(_entropy_kinds(UNKNOWN_KEY, path=TARGET, ignores=self.store()), [])
        self.assertEqual(_entropy_kinds(UNKNOWN_KEY, path=OTHER, ignores=self.store()),
                         ["high-entropy-token"])
        from mokata.govern.doctor import secret_ignore_findings
        self.assertTrue(secret_ignore_findings(_Surface(self.root)))


# ══════════════════════════════════════════════════════════════════════════════════════════
# DELIVERABLE 6 (f) — the ledger. Add AND remove, reusing I3, never a second log.
# ══════════════════════════════════════════════════════════════════════════════════════════

class TestLedgered(_RepoCase):

    def _kinds(self):
        from mokata.govern.ledger import AuditLedger
        return [e.get("kind") for e in
                AuditLedger.from_mokata_dir(os.path.join(self.root, ".mokata")).entries()]

    def test_an_add_is_ledgered(self):
        self.add()
        self.assertIn("secret_ignore", self._kinds())

    def test_a_remove_is_ledgered_too(self):
        from mokata.govern import secret_ignore
        self.add()
        secret_ignore.remove_ignore(self.root, SURVIVOR, TARGET, assume_yes=True,
                                    out=lambda _m: None)
        from mokata.govern.ledger import AuditLedger
        entries = [e for e in
                   AuditLedger.from_mokata_dir(os.path.join(self.root, ".mokata")).entries()
                   if e.get("kind") == "secret_ignore"]
        self.assertEqual([e.get("action") for e in entries], ["added", "removed"])

    def test_the_ledger_entry_carries_the_reason(self):
        from mokata.govern.ledger import AuditLedger
        self.add(reason="draft-validator attribute name")
        entry = next(e for e in
                     AuditLedger.from_mokata_dir(os.path.join(self.root, ".mokata")).entries()
                     if e.get("kind") == "secret_ignore")
        self.assertEqual(entry.get("reason"), "draft-validator attribute name")

    def test_it_reuses_the_existing_ledger_not_a_new_one(self):
        from mokata.govern.ledger import AuditLedger, LEDGER_FILENAME
        self.add()
        led = AuditLedger.from_mokata_dir(os.path.join(self.root, ".mokata"))
        self.assertTrue(os.path.exists(led.path))
        self.assertTrue(led.path.endswith(LEDGER_FILENAME))
        self.assertTrue(led.verify().intact)


# ══════════════════════════════════════════════════════════════════════════════════════════
# REGRESSION — the layers this stage promised not to move.
# ══════════════════════════════════════════════════════════════════════════════════════════

class TestTheFloorDidNotMove(unittest.TestCase):

    def test_scan_without_ignores_is_byte_identical_to_before(self):
        for text in (SURVIVOR, AWS_KEY, GH_TOKEN, UNKNOWN_KEY, "ordinary code here"):
            with self.subTest(text=text[:8]):
                self.assertEqual([(f.layer, f.kind, f.detail, f.line)
                                  for f in scan(text=text, path=TARGET)],
                                 [(f.layer, f.kind, f.detail, f.line)
                                  for f in scan(text=text, path=TARGET, ignores=None)])

    def test_the_signature_set_is_unchanged(self):
        self.assertEqual(len(secrets._SIGNATURES), 16)

    def test_the_known_shape_floor_is_unchanged(self):
        self.assertEqual([n for n, _ in secrets._KNOWN_SHAPES],
                         ["aws-access-key", "github-token", "github-pat", "openai-key",
                          "slack-token", "gcp-api-key", "jwt"])

    def test_only_high_entropy_token_is_ever_ignorable(self):
        from mokata.govern.secret_ignore import IGNORABLE_KINDS
        self.assertEqual(IGNORABLE_KINDS, ("high-entropy-token",))


if __name__ == "__main__":
    unittest.main()
