"""The mutant-driver conformance sweep — a PURE FUNCTION over a SUPPLIED CORPUS of drivers.

0.0.18 exit criterion 5 (`MUTANT-DRIVER-CONTRACT-UNIMPLEMENTED`, doc 84 §1). `scripts/mutate.sh`'s
EXIT CONTRACT header is the single source and every mutant driver in the tree CONSUMES it —
twenty-nine times, by hand, each copy made from whichever driver its author happened to be
reading. Until now exactly one of those copies was graded: `test_mutant_batch_driver.py` self-tests
`tests/_run_mutants.sh`, and its own docstring says so — *"the ONLY production consumer"*, true
when written and false since stage 16. **The contract was pinned for ONE MEMBER OF A CLASS**, which
is `MIRROR-PIN-COVERS-FOUR-OF-SIXTEEN` (0.0.18 stage 4) one subsystem over, and it is exactly why
`tests/_sync_marker_drift_mutants.sh` implemented none of the contract for three days after the row
naming it was filed.

WHAT A NON-CONFORMING DRIVER COSTS, stated once so the elements below are not a style guide. On
**exit 4** the mutator deliberately LEAVES THE TARGET MUTATED; a driver that carries on grades every
later mutant against an uncontrolled edit. On **exit 7** the tests were already failing, so every
later mutant is scored RED for a failure that has nothing to do with it — a batch of those reports a
PERFECT SCORE, and 0.0.17 stage 28's clean sweep turned out to be one. And a driver that never reads
stdout cannot tell six kills from five kills and a survivor: it exits 0 either way. That last one is
not hypothetical either — it is how `c658c73`'s "6 mutants RED" came to be a reading rather than a
derivation.

THREE DESIGN CONSTRAINTS, each from something that already went wrong here:

1. **IT TAKES A CORPUS, it does not go and find the repo** (doc 85 §7i, and `tests/_workflow_pins.py`
   is the precedent this follows rather than re-derives). Once the one offender is fixed the tree
   holds nothing left to catch, so a sweep wired to the real corpus would pass having graded
   NOTHING. `nonconforming()` is a pure function over `{name: text}`, so the tests hand it synthetic
   offenders — one per element — and watch it red.

2. **EVERY AXIS IS DERIVED, and the two that are not are DECLARED** (§7j). The domain comes from a
   walk, not a list; the dispatch functions come from each driver's own body, not from the name
   `mutant`; the call-site count ranges over every dispatch function a driver defines, not over one
   of them; the verdict counters come from the driver's own case arms. What remains literal is
   `DECLARED_INTERNAL_DRIVERS` and `DECLARED_NONCONFORMING` — both graded for EXACTNESS, so a stale
   entry reds as loudly as a missing one.

3. **IT ANSWERS THE LOCATION QUESTION rather than leaving it implied.** See below.

⚠ **WHAT THIS SWEEP DOES NOT GRADE, declared rather than discovered:**

  * **The SHAPE is the tree's, not the contract's.** The contract lives in prose in `mutate.sh`'s
    header; conformance is read here off the `case "$out" in RED*) … GREEN*) … *)` idiom that all
    twenty-nine drivers use because they were all copied from each other. A driver written with an
    `if`-chain instead would red here while honouring the contract. That is a FALSE RED, it is loud,
    and the remedy is one of two things: adopt the shape, or extend this file. It is not silent, and
    the real fix is `MUTANT-DRIVER-CONTRACT-DUPLICATED`'s extraction into one sourced driver — after
    which this sweep grades one implementation instead of twenty-nine copies.
  * **The static axis only.** `TOTAL` is compared against the call sites a reader can COUNT in the
    file. A call site inside a loop or a branch would make the static count and the dynamic one
    disagree; `_run_mutants.sh` carries its own runtime `ran != TOTAL` check for that axis and this
    sweep does not require one.
  * **`BASELINE-WINDOW-BETWEEN-CHECK-AND-GRADE`** (doc 84, 0.0.19/if-observed) is untouched and not
    narrowed. It is a property of `mutate.sh`'s design, one test run wide, and nothing a driver does
    can close it.
"""

import os
import re
import _support

# ---- the single source ------------------------------------------------------------------------

MUTATOR_RELPATH = "scripts/mutate.sh"

# The domain predicate, kept IDENTICAL to the command the backlog publishes for this number
# (`grep -rl 'mutate\.sh' --include='*.sh' .`, discounting the mutator itself) so that the
# derivation in the docs and the derivation in the pin cannot part. It is deliberately
# over-inclusive: a shell file that NAMES the mutator without driving it should not exist, and if
# one appears it must declare itself rather than sit silently outside the sweep.
MUTATOR_REFERENCE = "mutate.sh"

# Directories a walk must not descend into, split by HOW they are matched — and the split is not
# cosmetic. `build/` has to be skipped (it holds an installed copy of the tree, so a basename walk
# would grade artefacts nobody edits) but matching it BY NAME AT ANY DEPTH silently drops
# `docs/build/`, which is where three of the twenty-nine drivers live. That is this sweep's own
# subject arriving in its own domain derivation: an over-eager skip shrinking the corpus to 26
# while still reading as "every driver". Anchored entries are matched against the path FROM THE
# ROOT; unanchored ones are matched by name anywhere. Same defect `sync-public.sh`'s own
# `--exclude` list carries a warning about, one file over.
SKIP_DIRS = frozenset({
    ".git", ".hg", ".tox", ".venv", "venv", ".mypy_cache", ".pytest_cache", "__pycache__",
    ".mokata", "node_modules", ".eggs",
})
SKIP_PATHS = frozenset({"build", "dist", "htmlcov"})

# The statuses this sweep reasons about, checked against the header itself by
# `test_mutant_driver_contract` so a moved contract is noticed rather than assumed.
STATUSES_REASONED_ABOUT = (4, 5, 6, 7)
THE_ONE_RULE = "exit 0  IF AND ONLY IF  A VERDICT WAS PRODUCED."


def contract_header(mutator_text):
    """`mutate.sh`'s EXIT CONTRACT section, or None if the header is not where this expects it.

    None rather than an empty string: "the contract moved" and "the contract is empty" are
    different facts and a caller that cannot tell them apart would silently grade nothing.
    """
    at = mutator_text.find("EXIT CONTRACT")
    if at < 0:
        return None
    return mutator_text[at:]


def documented_statuses(header):
    """The exit codes the header defines, derived from its own `#   N  ...` entries."""
    if header is None:
        return frozenset()
    return frozenset(int(n) for n in re.findall(r"^#\s{2,}(\d{1,3})\s{2,}\S", header, re.M))


# ---- the contract elements ----------------------------------------------------------------

RC_CAPTURED = "rc-captured"
NONZERO_ABORTS = "nonzero-aborts"
ABORT_CARRIES_STATUS = "abort-carries-the-mutator-status"
NO_STATUS_EXEMPTED = "no-status-exempted"
RUN_COUNTED = "run-counted"
VERDICT_COUNTED = "verdict-parsed-and-counted"
NO_VERDICT_ABORTS = "no-verdict-aborts"
TOTAL_DECLARED = "total-declared"
TOTAL_MATCHES_CALL_SITES = "total-equals-call-sites"
SUMMARY_EMITTED = "summary-emitted"

CONTRACT = (
    RC_CAPTURED, NONZERO_ABORTS, ABORT_CARRIES_STATUS, NO_STATUS_EXEMPTED, RUN_COUNTED,
    VERDICT_COUNTED, NO_VERDICT_ABORTS, TOTAL_DECLARED, TOTAL_MATCHES_CALL_SITES, SUMMARY_EMITTED,
)

# What each element costs when it is absent — carried in the failure message, because a sweep that
# reports a missing token teaches nobody why the token was there.
WHY = {
    RC_CAPTURED:
        "the mutator's exit status is discarded, so exits 4/5/6/7 are all swallowed and the batch "
        "cannot know a verdict was never produced",
    NONZERO_ABORTS:
        "a harness failure does not STOP the batch — on exit 4 the target is left mutated on "
        "purpose, so every later mutant is graded against an uncontrolled edit",
    ABORT_CARRIES_STATUS:
        "the abort collapses the mutator's status into a literal, so 4 (the tree is still mutated) "
        "and 5 (nothing was touched) reach the caller as the same fact",
    NO_STATUS_EXEMPTED:
        "the abort carves out a specific status, so that one code alone is read as benign — which "
        "is how exit 7 would make the green-baseline guard decorative for this batch only",
    RUN_COUNTED:
        "nothing counts the mutants that ran, so `N of M never ran` has no N and a truncated batch "
        "reads exactly like a complete one",
    VERDICT_COUNTED:
        "the RED/GREEN verdict is never read, so a survivor and a kill leave the batch in the same "
        "state and it exits 0 either way",
    NO_VERDICT_ABORTS:
        "a status-0 run carrying no verdict is counted as graded — a non-result laundered into a "
        "result, which is the shape every hole in mutate.sh's header turned out to be",
    TOTAL_DECLARED:
        "the driver declares no TOTAL, so it cannot say how many mutants never ran when it aborts",
    TOTAL_MATCHES_CALL_SITES:
        "the declared TOTAL and the real number of call sites disagree, so every `N of M` line the "
        "driver prints is wrong by the difference and it reports more coverage than it has",
    SUMMARY_EMITTED:
        "the batch prints no tally, so nobody reads the score without re-counting the log by hand",
}

# ---- shell reading ---------------------------------------------------------------------------

_ASSIGN = re.compile(
    r"^[ \t]*(?:(?:local|export|declare|readonly|typeset)[ \t]+)*"
    r"([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.M)
_FUNC_HEAD = re.compile(
    r"^[ \t]*(?:function[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*\([ \t]*\)[ \t]*\{", re.M)
_STATUS_ASSIGN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)=\$\?")
_INCREMENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)=\$\(\(\s*\1\s*\+\s*1\s*\)\)")
_TOTAL_DECL = re.compile(r"^TOTAL=(\d+)\s*$", re.M)
_LITERAL_MUTATOR_CALL = re.compile(r"(?<![\w.-])mutate\.sh[\"']?[ \t]+[\"'$]")
_CASE_ON_RC = r"\bcase[ \t]+\"?\$\{?%s\}?\"?[ \t]+in\b.*?\besac\b"


def mutator_vars(text):
    """Variables bound to the mutator, derived from the driver's own assignments.

    ⚠ The binding must be a PATH, not a mention. `_run_mutants.sh`'s abort block assigns REMEDY
    PROSE that names `scripts/mutate.sh` in a sentence; a substring test promotes `remedy` to a
    mutator handle, which promotes `abort()` to a dispatch function, which then reports the tree's
    own reference driver as non-conforming on four elements. That is `PIN-SUBSTRING-COMMENT-HOLE`
    exactly — a scanned region containing prose about itself.

    ⚠⚠ TWO CONDITIONS, AND THEY ARE SEPARATELY GRADABLE ON PURPOSE (§7f, the 2026-08-04
    amendment). Both excluded `remedy`, so mutating either one alone survived — two defences of one
    property are not twice as safe, they are untestable. Rather than delete one (each sees a shape
    the other does not), each is given an offender only it can catch:

        single token   `remedy="  see the EXIT CONTRACT in scripts/mutate.sh"`  ENDS with the name
        ends-with      `LOG="$ROOT/mutate.sh.log"`                              one token, contains it
    """
    found = set()
    for name, rhs in _ASSIGN.findall(text):
        value = rhs.strip().strip("\"'")
        if " " in value or "\t" in value:
            continue
        if value.endswith(MUTATOR_REFERENCE) or "MUTATE_SH" in value:
            found.add(name)
    return frozenset(found)


def shell_functions(text):
    """`{name: (start, end, body)}` for every `name() { ... }`, by brace matching."""
    out = {}
    for head in _FUNC_HEAD.finditer(text):
        open_at = text.index("{", head.start())
        depth, i = 0, open_at
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out[head.group(1)] = (head.start(), min(i + 1, len(text)), text[open_at + 1:i])
    return out


def top_level(text):
    """The driver with every function definition removed — where the call sites and the summary
    live. A call site and a definition are different things and must not be counted together."""
    spans = sorted((s, e) for s, e, _ in shell_functions(text).values())
    out, at = [], 0
    for start, end in spans:
        if start >= at:
            out.append(text[at:start])
            at = end
    out.append(text[at:])
    return "".join(out)


def _calls(body, names):
    """Which of `names` this body invokes, at a COMMAND POSITION.

    Not line-anchored: `_run_mutants.sh` aborts with `if [ "$rc" -ne 0 ]; then abort "$rc" ...; fi`
    on one line, and a line-start test would miss it and report the tree's reference driver as
    never stopping on a harness failure.
    """
    return {n for n in names
            if re.search(r"(?:^|[;&|(]|\bthen\b|\belse\b|\bdo\b|\{)[ \t]*%s\b" % re.escape(n),
                         body, re.M)}


def _closure(name, funcs):
    """A function's body plus the bodies of every function it reaches.

    `_run_mutants.sh` tests `$rc` in `mutant()` and exits in `abort()`; a check that looked only at
    the dispatch function's own body would call the tree's reference driver non-conforming.
    """
    seen, stack, parts = set(), [name], []
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in funcs:
            continue
        seen.add(cur)
        body = funcs[cur][2]
        parts.append(body)
        stack.extend(_calls(body, set(funcs) - seen))
    return "\n".join(parts)


def dispatch_functions(text):
    """`{name: body}` for every function that INVOKES the mutator.

    THE DERIVED AXIS, and the reason `_stage19c_mutants.sh` comes back clean: it declares two —
    `mutant` and `equivalent` — and a sweep keying on the name `mutant` alone reads its TOTAL=7
    against 5 call sites and reports a defect that is not there.
    """
    variables = mutator_vars(text)
    out = {}
    for name, (_s, _e, body) in shell_functions(text).items():
        if _LITERAL_MUTATOR_CALL.search(body) or any(
                re.search(r"\$\{?%s\b" % re.escape(v), body) for v in variables):
            out[name] = body
    return out


def call_sites(text, names):
    """How many times the driver invokes any of its dispatch functions, at top level."""
    body = top_level(text)
    return sum(len(re.findall(r"(?m)^[ \t]*%s[ \t]+[\"'$]" % re.escape(n), body)) for n in names)


def declared_total(text):
    found = _TOTAL_DECL.search(text)
    return int(found.group(1)) if found else None


def _case_arms(body):
    """`[(pattern, arm_body)]` for every `case ... in` arm in the body."""
    arms = []
    for block in re.finditer(r"\bcase[ \t]+(\S+)[ \t]+in\b(.*?)\besac\b", body, re.S):
        for chunk in block.group(2).split(";;"):
            stripped = re.sub(r"^(?:\s*#[^\n]*\n)*", "", chunk)
            head = re.match(r"\s*\(?([^\n)]*)\)", stripped)
            if head:
                arms.append((head.group(1).strip(), stripped[head.end():]))
    return arms


def _nonzero_guard(body, status):
    """The `if` block that tests the mutator's status against zero, or None."""
    test = re.search(
        r"\$\{?%s\}?\"?[ \t]*(?:-ne|!=|-gt)[ \t]*\"?0|\"?0\"?[ \t]*(?:-ne|!=|-lt)[ \t]*\"?\$\{?%s\b"
        % (re.escape(status), re.escape(status)), body)
    if test is None:
        return None
    opens = [m for m in re.finditer(r"\bif\b", body) if m.start() <= test.start()]
    if not opens:
        return None
    at = opens[-1].start()
    depth = 0
    for tok in re.finditer(r"\b(if|fi)\b", body[at:]):
        depth += 1 if tok.group(1) == "if" else -1
        if depth == 0:
            return body[at:at + tok.end()]
    return body[at:]


def missing_elements(text):
    """THE PURE FUNCTION. Which contract elements one driver's TEXT does not implement.

    Elements are reported CONDITIONALLY where one absence implies another: a driver with no
    `TOTAL` is missing `total-declared` and is not also charged with `total-equals-call-sites`,
    so that every synthetic offender below can isolate exactly one element.
    """
    missing = set()
    funcs = shell_functions(text)
    dispatch = dispatch_functions(text)
    if not dispatch:
        # Nothing invokes the mutator from a function, so there is no accounting to inspect and
        # every element that depends on one is absent. Reported whole rather than one at a time:
        # this is the bare-passthrough shape, not a single missing element.
        whole = set(CONTRACT) - {TOTAL_MATCHES_CALL_SITES}
        if declared_total(text) is not None:
            whole -= {TOTAL_DECLARED}
            whole |= {TOTAL_MATCHES_CALL_SITES}
        return frozenset(whole)

    run_counters, verdict_counters = set(), set()
    for name, body in dispatch.items():
        closure = _closure(name, funcs)
        status = _STATUS_ASSIGN.search(closure)
        if status is None:
            missing.add(RC_CAPTURED)
            # A driver with no status capture might still be structurally able to stop — the
            # capture is one edit away. One that ALSO contains no `exit` at all cannot stop under
            # any circumstance, and that is a separate, worse fact: it is the bare passthrough.
            # Charging only `rc-captured` for it would understate a driver that inspects nothing.
            if not re.search(r"\bexit\b", closure):
                missing.add(NONZERO_ABORTS)
        else:
            rc = status.group(1)
            guard = _nonzero_guard(closure, rc)
            if guard is None:
                missing.add(NONZERO_ABORTS)
            else:
                reached = guard + "\n" + "\n".join(
                    _closure(n, funcs) for n in _calls(guard, set(funcs)))
                if not re.search(r"\bexit\b", reached):
                    missing.add(NONZERO_ABORTS)
                elif not re.search(r"\bexit[ \t]+\"?\$\{?%s\b" % re.escape(rc), reached):
                    missing.add(ABORT_CARRIES_STATUS)
            # A remedy `case "$rc" in 1) … esac` EXPLAINS the codes; it does not exempt one. Only
            # a comparison against a non-zero literal outside such a block can do that.
            without_dispatch = re.sub(_CASE_ON_RC % re.escape(rc), "", closure, flags=re.S)
            if re.search(r"\$\{?%s\}?\"?[ \t]*(?:-ne|!=|-eq|==?)[ \t]*\"?[1-9]"
                         % re.escape(rc), without_dispatch):
                missing.add(NO_STATUS_EXEMPTED)

        arms = _case_arms(body)
        red = [a for p, a in arms if "RED" in p]
        green = [a for p, a in arms if "GREEN" in p]
        default = [a for p, a in arms if p.strip() in ("*", '"*"')]
        if not red or not green or not all(
                re.search(r"\b[A-Za-z_]\w*=", a) for a in red + green):
            missing.add(VERDICT_COUNTED)
        else:
            for arm in red + green:
                verdict_counters.update(re.findall(r"\b([A-Za-z_]\w*)=", arm))
        if not default or not any(re.search(r"\bexit\b", a) for a in default):
            missing.add(NO_VERDICT_ABORTS)

        incremented = {m.group(1) for m in _INCREMENT.finditer(body)}
        run_counters = incremented if not run_counters else (run_counters & incremented)

    run_counters -= verdict_counters
    if not run_counters:
        missing.add(RUN_COUNTED)

    total = declared_total(text)
    if total is None:
        missing.add(TOTAL_DECLARED)
    elif total != call_sites(text, dispatch):
        missing.add(TOTAL_MATCHES_CALL_SITES)

    # A tally: at least two of the driver's own verdict counters, against a denominator — either
    # the mutants that RAN or the TOTAL it declared. Both spellings are live in the tree
    # (`%s ran of %s — %s RED, %s GREEN` and `%s RED / %s GREEN out of %s`) and both make a
    # truncated batch visible, which is the whole point of the element. The FORMAT is not the
    # contract; the presence of an attributable score is.
    # Conditional, like `total-equals-call-sites`: a driver that never parses a verdict has no
    # counters for a tally to name, so charging it with BOTH would make one defect read as two and
    # no synthetic offender could isolate either.
    denominators = set(run_counters) | ({"TOTAL"} if total is not None else set())
    if VERDICT_COUNTED not in missing:
        for line in re.findall(r"(?m)^[ \t]*(?:printf|echo)\b[^\n]*",
                               top_level(text).replace("\\\n", " ")):
            counters = {c for c in verdict_counters if re.search(r"\$\{?%s\b" % c, line)}
            if len(counters) >= 2 and any(re.search(r"\$\{?%s\b" % d, line) for d in denominators):
                break
        else:
            missing.add(SUMMARY_EMITTED)

    return frozenset(missing)


def nonconforming(corpus):
    """`{name: missing_elements}` for every driver in a SUPPLIED corpus that fails the contract."""
    out = {}
    for name, text in corpus.items():
        gaps = missing_elements(text)
        if gaps:
            out[name] = gaps
    return out


# ---- the domain -------------------------------------------------------------------------------


def discover_drivers(root):
    """Every shell file in the tree that references the mutator, except the mutator itself.

    The DERIVED axis (§7j). Nothing here names a driver, a directory or a count — shrink any of
    those and `test_mutant_driver_contract` reds.
    """
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        here = _support.posix_rel(dirpath, root).replace(os.sep, "/")
        prefix = "" if here == "." else here + "/"
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and (prefix + d) not in SKIP_PATHS]
        for filename in filenames:
            if not filename.endswith(".sh"):
                continue
            full = os.path.join(dirpath, filename)
            rel = _support.posix_rel(full, root).replace(os.sep, "/")
            if rel == MUTATOR_RELPATH:
                continue
            try:
                with open(full, encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            if MUTATOR_REFERENCE in text:
                found.append(rel)
    return tuple(sorted(found))


def read_corpus(root, names):
    corpus = {}
    for rel in names:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            corpus[rel] = fh.read()
    return corpus


# ---- WHERE A DRIVER BELONGS — the question, answered ------------------------------------------
#
# `MUTANT-DRIVERS-IN-DOCS-BUILD-DO-NOT-SHIP` (doc 84, 2026-08-12) found the drivers SPLIT ACROSS
# TWO DIRECTORIES mid-release, with nothing declaring where one belongs — so "next to the report I
# am writing" and "next to the tests it grades" were both defensible and the split cost nothing to
# create. That is an UNDECLARED PARTITION, and drift is what its absence looks like.
#
# THE ANSWER, and it is a real answer rather than a preference: LOCATION IS PART OF THE CONTRACT,
# because it decides whether the driver SHIPS. `scripts/sync-public.sh` excludes `docs/build/`, so
# a driver placed there is absent from the public mirror — and a public contributor running the
# batches then grades a corpus that is incomplete and READS as complete. Three states, never two
# (§7g): a driver is SHIPPED, or DECLARED-INTERNAL, or UNDECLARED — and the third is a failure.

LOCATION_SHIPPED = "shipped"
LOCATION_DECLARED_INTERNAL = "declared-internal"
LOCATION_UNDECLARED = "undeclared"

DRIVER_HOME = "tests"

# The internal partition, declared with the control that makes it true. `docs/build/` is excluded
# from the mirror TWICE (the `--exclude` list and the hard guard's `INTERNAL_PATHS`), and
# `test_mutant_driver_contract` re-derives that through `_mirror_bookkeeping` rather than trusting
# this comment.
INTERNAL_ROOT = "docs/build"

# ⚠ These three are DECLARED, NOT ENDORSED. Stages 3a, 4 and 5 each filed their batch beside the
# report it belonged to, which is why the convention broke mid-release. Moving them is a separate
# row's fix (`MUTANT-DRIVERS-IN-DOCS-BUILD-DO-NOT-SHIP`) — doing it here would close today's three
# and leave the class open. What this file adds is the thing that was missing: a FOURTH one is now
# a test failure rather than another undeclared instance.
DECLARED_INTERNAL_DRIVERS = {
    "docs/build/handoff/03a-mutants.sh": "stage 3a batch, filed beside its report (0.0.18)",
    "docs/build/handoff/04-mutants.sh": "stage 4 batch, filed beside its report (0.0.18)",
    "docs/build/handoff/05-mutants.sh": "stage 5 batch, filed beside its report (0.0.18)",
    "docs/build/handoff/03a-second-silent-allow-mutants.sh":
        "stage 03a batch, filed beside its report (0.0.19). ⚠ NOT `03a-mutants.sh` — the stage "
        "NUMBER repeats across releases and that name is 0.0.18's. Unlike the three above it "
        "CONFORMS, so it appears here and NOT in DECLARED_NONCONFORMING.",
    "docs/build/handoff/07-mutants.sh":
        "stage 07 batch (0.0.19), filed beside its report. CONFORMS — it is in this table for its "
        "LOCATION only, and NOT in DECLARED_NONCONFORMING.",
    "docs/build/handoff/07-assertion-weight.sh":
        "stage 07 (0.0.19). ⚠ NOT A MUTANT DRIVER, and declared rather than hidden. It is "
        "discovered because it NAMES `scripts/mutate.sh` in a comment explaining why it "
        "deliberately does not use it: showing that an assertion carries the weight needs TWO "
        "simultaneous mutations, and mutate.sh applies exactly one by contract. `discover_drivers` "
        "matches the mutator's path anywhere in the text, so a file that says 'not this' reads the "
        "same as one that calls it — the mention-versus-call distinction `disabled_calls` draws in "
        "_release_repo_guards. Rewording the comment to dodge the sweep would be hiding from it; "
        "it is declared here and, being no driver at all, in DECLARED_NONCONFORMING entire.",
    "docs/build/handoff/09a-mutants.sh":
        "stage 09a batch (0.0.19, F13 — the Windows audio arm), filed beside its report. "
        "CONFORMS — it is in this table for its LOCATION only, and NOT in DECLARED_NONCONFORMING.",
}


def location_verdict(rel):
    if rel in DECLARED_INTERNAL_DRIVERS:
        return LOCATION_DECLARED_INTERNAL
    if rel.split("/")[0] == DRIVER_HOME:
        return LOCATION_SHIPPED
    return LOCATION_UNDECLARED


# ---- WHAT IS KNOWN TO BE BROKEN — declared, dated, and bounded ---------------------------------
#
# Not an exemption: a BOUND. The set is asserted EXACT in both directions, so a new non-conformance
# reds and a repaired driver left in this table reds too.
#
# ⚠ THE ROW SAID THERE WOULD BE NOTHING LEFT TO CATCH ONCE `_sync_marker_drift_mutants.sh` WAS
# FIXED. There were three more, and they arrived AFTER the row was filed — stages 3a, 4 and 5 each
# wrote a driver of a NEW shape: it captures the status and stops on a harness failure (so it is
# not the 2026-08-07 defect) and it counts NOTHING. A GREEN survivor in one of those batches
# printed one line among eighteen and the batch exited 0 exactly as a clean run does, with
# `=== end of batch ===` under it. The elements below are what `missing_elements` returns; the
# summary line is absent too and is not separately charged, for the reason given at that check.
DECLARED_NONCONFORMING = {
    "docs/build/handoff/03a-mutants.sh": frozenset({
        RUN_COUNTED, VERDICT_COUNTED, NO_VERDICT_ABORTS, TOTAL_DECLARED}),
    "docs/build/handoff/04-mutants.sh": frozenset({
        RUN_COUNTED, VERDICT_COUNTED, NO_VERDICT_ABORTS, TOTAL_DECLARED}),
    "docs/build/handoff/05-mutants.sh": frozenset({
        RUN_COUNTED, VERDICT_COUNTED, NO_VERDICT_ABORTS, TOTAL_DECLARED}),
    # Not a driver — see its entry in DECLARED_INTERNAL_DRIVERS. It implements NO element of the
    # contract because it invokes no mutator, so the whole contract is listed. If it ever grows
    # real accounting this set reds in the other direction and must be cut back, which is the
    # property that keeps this table from becoming a place to park things.
    # Every element EXCEPT `total-equals-call-sites`, which `missing_elements` reports
    # conditionally: with no TOTAL declared there is nothing for the call-site count to disagree
    # with, so charging both would make one absence read as two (§7f).
    "docs/build/handoff/07-assertion-weight.sh":
        frozenset(set(CONTRACT) - {TOTAL_MATCHES_CALL_SITES}),
}
