"""Derive, from the mirror boundary itself, which SHIPPED tests read a file that does not ship.

Stage 28 (SHIPPED-TEST-READS-INTERNAL-FILE). `tests/` ships to the public mirror; `docs/build/`,
`scripts/sync-public.sh`, `scripts/release.sh` and `scripts/check-tracker-tables.py` do not. A test
file therefore lands on the mirror carrying a read of a path that is not there, and the read fails
as an ERROR — a suite that is green in this repo and broken in the one users clone.

★ THE THIRD INSTANCE OF ONE SHAPE. `test_stage68_supply_chain.py:342` and `:369` already carry the
answer — `@unittest.skipUnless(os.path.exists(RELEASE_SH), ...)` — and have since stage 68. Stages
11 and 12 wrote new files that read the very same two scripts and walked straight past it. That is
the same failure as stage 10 fixing `embeddings-leg.yml`'s missing PyYAML install and not
`release.sh`'s: a rule established at the CALL SITES it was found at, protecting those call sites
and nothing else. **A guard established for two call sites protects two call sites; only a
derivation protects the class.** So this module does not add two decorators. It derives the
property, over the whole shipped corpus, from the exclude list rather than from a written-down
list of internal paths:

    no test file that SHIPS to the mirror may READ a path whose presence differs across the
    mirror boundary, without a class-level `@unittest.skipUnless(os.path.exists(...))` guard.

REUSES THE AUDITED EXCLUDE READER. The exclude set comes from `_mirror_bookkeeping.exclude_entries`
(stage 11) — the same parser that already strips comments and reads the values inside `--exclude=`
clauses rather than substring-searching the block. A second exclude parser beside the audited one
is the `badge_run` / `find_active_run` mistake, and writing one HERE — in the stage that exists
because a class fix was applied to one instance — would be self-refuting.

THREE OUTCOMES, THREE REPRESENTATIONS (§7g). The collapse this module must refuse is specific and
easy: **an empty exclude set makes the property vacuously true, and a vacuous true is
byte-identical to "every read is guarded."** So an empty exclude set is `BASIS_NO_EXCLUDES` —
UNDECIDABLE, with a reason — never GREEN. Likewise a source that will not parse and a corpus that
could not be read. This is `_preflight_parity.BASIS_NO_CI_REQUIREMENTS` (stage 27) wearing this
stage's clothes. It is also the answer the sweep must give when it runs FROM the mirror, where
`sync-public.sh` is itself excluded and there is nothing to derive an exclude set from.

A GUARD IS THE DECORATOR SHAPE, AND ONLY THE DECORATOR SHAPE. `setUpClass` raising `SkipTest` is
NOT accepted, and the refusal is explicit (`GUARD_SETUPCLASS_SKIP`) rather than incidental. Two
measured reasons, in that order:

  * **THE COUNT.** A class decorator still COLLECTS every test and reports each as a skip, so they
    remain in unittest's `Ran N`. A `setUpClass` raising `SkipTest` collapses the whole class into
    ONE skip and its tests leave `Ran N` altogether — measured on a two-test class: the decorator
    gives `Ran 2 ... OK (skipped=2)`, the setUpClass skip gives `Ran 0 ... OK (skipped=1)`. So the
    decorator keeps the mirror's count equal to this repo's and the setUpClass shape silently
    diverges it, which is what `tests/test_suite_count_integrity.py` pins.
  * **IT IS THE SHAPE THAT ACTUALLY FAILED.** `test_s11`'s `TestTheDocIsHeldToTheDerivation` did
    its reads INSIDE `setUpClass`, so on the mirror it errored before any skip could be reached. A
    guard that runs after collection is a guard whose own body can beat it to the file; a
    decorator is evaluated at class creation and `setUpClass` never runs at all.

Encoding the distinction here, instead of leaving it in prose, is what lets a mutant that accepts
the wrong shape die.

WHAT COUNTS AS "EXCLUDED": TRACKEDNESS, NOT PRESENCE ON DISK
------------------------------------------------------------
The `--exclude` list holds two different kinds of entry. `docs/build/` and `scripts/release.sh`
are carried BY THE REPO and will not exist on the mirror — a test that reads one behaves
differently across the boundary. `.mutate.lock`, `dist/` and `site/` are regenerable artifacts
that no checkout carries until something builds them — a test naming one already behaves
identically on both sides, and condemning it would be inventing an offender. So the caller narrows
the exclude set to the entries **the git INDEX of the tree being swept carries**
(`tracked_excludes`); the boundary only bites where trackedness differs.

★ IT USED TO ASK THE DISK, AND THAT WAS THE DEFECT (stage 29 rider). The first cut of this asked
`os.path.exists`. A working tree is not a repo: it also holds ignored and untracked files, and the
answer therefore differed per machine. Three consequences, all measured on this repo:

  * `docs/marketing/` is gitignored (`.gitignore:60`). It is in NO clone. On the author's laptop it
    made the deriving set look 3 entries larger than any clone's, and the anti-vacuity floor below
    was met **only because those files were sitting on one disk** — a floor that measured the
    machine, not the repo. On CI, which is always a fresh clone, the floor was RED on every leg.
  * `.git` "exists" in every checkout INCLUDING the mirror's, so presence made it a
    boundary-crossing path when it crosses nothing. Trackedness drops it, correctly.
  * `.venv`, `.claude`, `.mokata`, `build`, `.pytest_cache`, `_to_delete` are developer-local: in
    neither a clone nor the mirror. Presence admitted whichever of them a given laptop happened to
    have; trackedness admits none of them, on every machine.

So the index is not merely the reproducible source, it is the CORRECT one. A clone reproduces the
index and nothing else, and "what a clone reproduces" is exactly the question this sweep asks.

The lenient direction is declared: an internal path that is gitignored (`docs/marketing/`) leaves
the deriving set, so a shipped test reading it is not condemned here. That read is already broken
in every fresh clone of THIS repo, so the private suite catches it before the mirror ever does —
this sweep is not its only control. `sync-public.sh`'s `INTERNAL_PATHS` guard still covers it on
the sync itself, which is why that list is deliberately WIDER than this one.

WHAT "READ" MEANS HERE, AND WHAT THIS CANNOT SEE
------------------------------------------------
A reference counts as a READ only when it is a **root-anchored path expression** —
`os.path.join(<root-name>, "scripts", "release.sh")`, where `<root-name>` is a module-level name
assigned from an expression mentioning `__file__` (the `ROOT = os.path.dirname(...)` idiom every
test file here uses). Anchoring is the whole read/mention discriminator:

  * a bare string literal (`"docs/build"`, `"scripts/release.sh"`) is a NEEDLE — the corpus is full
    of tests asserting that some shipped file does NOT mention an internal path — so literals are
    not paths here, no matter how many segments they have;
  * `os.path.join(tmpdir, "docs", "build", "x.md")` builds a FIXTURE the test just made; the
    mirror boundary has no opinion about it. Only the repo root anchors a real read.

Two further shapes are resolved, because the real offenders hide behind them:

  * **accessors.** A module-level `def f(base, ...)` that joins one of its own PARAMETERS with an
    excluded tail is a path accessor (`_mirror_bookkeeping.read_script`). It is not itself a read —
    the caller decides. A CALL to it passing a root name (`mb.read_script(ROOT)`) is.
  * **taint, to a fixpoint, one hop across modules.** A module-level constant holding an anchored
    path taints it; a module-level function mentioning a tainted name is itself tainted; and
    `import X as A` / `from X import f` carries both across, because the corpus contains the
    test-support modules too.

★ A PROBE IS NOT A READ. `os.path.exists(RELEASE_SH)` asks whether the boundary is here; it never
opens anything and it is exactly what the absent-only-because-of-the-mirror companions are built
from. References sitting inside `exists`/`isfile`/`isdir`/`lexists` are therefore not reads — a
sweep that condemned them would condemn its own remedy.

DECLARED BLIND SPOTS — this sweep means less than its name unless they are written down (§7h):

  1. **Paths built at run time.** An f-string, a `+` concatenation, a `%` format, or a join with a
     variable segment is not reconstructed, not reported, and not counted against the file.
  2. **Indirection more than one module hop deep.** A -> B -> C, where C holds the path, is
     invisible. One hop is resolved; two are not.
  3. **Globs.** Only LITERAL exclude entries decide. `--exclude='*.mp4'`, `'/mokata-*/'` and
     friends do not participate, because whether an rsync pattern covers a path is not decidable
     by string comparison (`_mirror_bookkeeping._coverage` reaches the same conclusion and routes
     to UNDECIDABLE; here the entry is simply dropped, which is the LENIENT direction — say so).
  4. **Reads through a subprocess, a chdir, or a fixture tree the test copied from the repo.**
     Only source-level path expressions are seen.
  5. **A root name that is imported rather than derived.** `_root_names` looks for `__file__` in
     the module's OWN assignments; `from _support import ROOT` would not be recognised.
  6. **What SHIPS is taken from the caller.** This module judges the corpus it is handed; it does
     not decide which files ship. `shipped_test_sources` supplies `tests/` + `tests/integration/`
     because `sync-public.sh` excludes neither, but a caller may supply anything.

Consequently a GREEN here means "no unguarded read that this reader can see", never "no unguarded
read". The shipped-subset run is the ground truth; this is the derivation that stops the class from
recurring between subset runs.
"""

import ast
import os
import subprocess

from _mirror_bookkeeping import exclude_entries  # noqa: F401  (re-exported: ONE exclude parser)

# ---- verdicts --------------------------------------------------------------------------------
GREEN = "green"
RED = "red"
UNDECIDABLE = "undecidable"

# ---- bases: WHICH rung produced the verdict (the one stored signal) --------------------------
BASIS_NO_EXCLUDED_READS = "no_excluded_reads"    # the file reads no boundary-crossing path
BASIS_ALL_READS_GUARDED = "all_reads_guarded"    # it reads one, and every reader is guarded
BASIS_UNGUARDED_READS = "unguarded_reads"        # it reads one from an unguarded scope
BASIS_NO_EXCLUDES = "no_excludes"                # nothing to check against -> vacuously true
BASIS_SOURCE_UNREADABLE = "source_unreadable"
BASIS_SOURCE_UNPARSEABLE = "source_unparseable"

#: basis -> the ONE verdict it produces. `verdict` is a property of `basis`, never stored beside
#: it — `_mirror_bookkeeping`'s and `_preflight_parity`'s discipline, for the same reason.
_VERDICT_OF = {
    BASIS_NO_EXCLUDED_READS: GREEN,
    BASIS_ALL_READS_GUARDED: GREEN,
    BASIS_UNGUARDED_READS: RED,
    BASIS_NO_EXCLUDES: UNDECIDABLE,
    BASIS_SOURCE_UNREADABLE: UNDECIDABLE,
    BASIS_SOURCE_UNPARSEABLE: UNDECIDABLE,
}

UNDECIDABLE_BASES = frozenset(b for b, v in _VERDICT_OF.items() if v == UNDECIDABLE)

# ---- guard shapes: what protects a read, and what only looks like it does ---------------------
GUARD_DECORATOR = "decorator"              # @unittest.skipUnless(os.path.exists(...)) — the one
GUARD_SETUPCLASS_SKIP = "setupclass_skip"  # setUp/setUpClass raising SkipTest — NOT accepted
GUARD_NONE = "none"

#: The ONLY guard shape that protects a read. A set of one, deliberately: the acceptance test is a
#: membership check with something to mutate, rather than an `== GUARD_DECORATOR` buried inside a
#: conditional. A mutant that adds GUARD_SETUPCLASS_SKIP here must die.
ACCEPTED_GUARDS = frozenset([GUARD_DECORATOR])

_SKIP_UNLESS_NAMES = ("unittest.skipUnless", "skipUnless")
_EXISTS_NAMES = ("os.path.exists", "path.exists", "exists",
                 "os.path.isfile", "path.isfile", "isfile",
                 "os.path.isdir", "path.isdir", "isdir",
                 "os.path.lexists", "path.lexists", "lexists")
_JOIN_NAMES = ("os.path.join", "path.join")
_SKIPTEST_NAMES = ("unittest.SkipTest", "SkipTest")

#: Where a read can sit. `<module>` is worse than a class: no decorator can guard import-time code.
SCOPE_MODULE = "<module>"

_GLOB_CHARS = "*?["


class Read(object):
    """One (scope, boundary-crossing path) pair and the guard shape that scope carries."""

    __slots__ = ("scope", "path", "guard", "via")

    def __init__(self, scope, path, guard, via=""):
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "guard", guard)
        object.__setattr__(self, "via", via)

    def __setattr__(self, *_a):                       # pragma: no cover - immutability guard
        raise AttributeError("Read is immutable")

    @property
    def guarded(self):
        return self.guard in ACCEPTED_GUARDS

    def render(self):
        via = (" (via %s)" % self.via) if self.via else ""
        if self.scope == SCOPE_MODULE:
            return ("module level reads %s at import time%s — no class decorator can guard this; "
                    "the import itself fails on the mirror" % (self.path, via))
        if self.guard == GUARD_SETUPCLASS_SKIP:
            return ("%s reads %s%s and skips inside setUp/setUpClass. That collapses the whole "
                    "class into ONE skip and drops its tests out of unittest's `Ran N` (measured: "
                    "`Ran 0 ... skipped=1` against the decorator's `Ran 2 ... skipped=2`), so the "
                    "mirror's count diverges from this repo's — and the read in setUpClass beats "
                    "its own skip to the file anyway. Use the class decorator."
                    % (self.scope, self.path, via))
        return "%s reads %s%s with no guard" % (self.scope, self.path, via)

    def __repr__(self):
        return "<Read %s %s guard=%s>" % (self.scope, self.path, self.guard)


class FileResolution(object):
    """One shipped file's status, derived. Frozen by convention (no setters are offered)."""

    __slots__ = ("filename", "basis", "reads", "detail")

    def __init__(self, filename, basis, reads=(), detail=""):
        if basis not in _VERDICT_OF:
            raise ValueError("unknown basis %r" % (basis,))
        if basis in UNDECIDABLE_BASES and not detail:
            raise ValueError(
                "%s: basis=%r is UNDECIDABLE and carries no reason. An undecidable row that "
                "cannot say why is indistinguishable from a clean one, and a reader rounds it to "
                "green — which is the defect this module exists to remove." % (filename, basis))
        object.__setattr__(self, "filename", filename)
        object.__setattr__(self, "basis", basis)
        object.__setattr__(self, "reads", tuple(reads))
        object.__setattr__(self, "detail", detail)

    def __setattr__(self, *_a):                       # pragma: no cover - immutability guard
        raise AttributeError("FileResolution is immutable")

    @property
    def verdict(self):
        return _VERDICT_OF[self.basis]

    @property
    def decided(self):
        return self.verdict != UNDECIDABLE

    @property
    def unguarded(self):
        """The reads that are NOT protected — the actionable part of a RED."""
        return tuple(r for r in self.reads if not r.guarded)

    def render(self):
        if self.verdict == UNDECIDABLE:
            return "UNKNOWN — %s" % self.detail
        if self.basis == BASIS_NO_EXCLUDED_READS:
            return "GREEN — reads nothing held back by the mirror boundary"
        if self.basis == BASIS_ALL_READS_GUARDED:
            return "GREEN — %d boundary read(s), every reader guarded" % len(self.reads)
        return "RED — %s" % "; ".join(r.render() for r in self.unguarded)

    def __repr__(self):
        return "<%s %s basis=%s>" % (self.filename, self.verdict.upper(), self.basis)


# ---- the exclude set that can decide ---------------------------------------------------------

def literal_excludes(excludes):
    """The exclude entries that can DECIDE: literals only (blind spot 3)."""
    return frozenset(
        e.strip("/") for e in excludes
        if e.strip("/") and not any(c in e for c in _GLOB_CHARS))


def tracked_paths(root):
    """Every path `root`'s git INDEX carries, plus the directories those paths imply — or None if
    the index could not be read at all.

    None is a THIRD answer, not an empty set: "this is not a checkout / git is not installed" and
    "this checkout carries nothing" must not share a representation, because the second is what an
    empty deriving set means and the first is the reader failing (§7g).
    """
    try:
        proc = subprocess.run(["git", "-C", root, "ls-files", "-z"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    paths = set()
    for entry in proc.stdout.decode("utf-8", "surrogateescape").split("\0"):
        if not entry:
            continue
        parts = entry.split("/")
        for i in range(1, len(parts) + 1):
            paths.add("/".join(parts[:i]))
    return frozenset(paths)


def tracked_excludes(root, excludes):
    """The literal excludes `root`'s git index CARRIES — the ones whose trackedness differs across
    the boundary — or None if the index could not be read.

    Not `os.path.exists`: see the module docstring. A working tree also holds ignored and untracked
    files, so a disk probe answers differently on every machine and a floor built on it measures
    the machine rather than the repo.
    """
    tracked = tracked_paths(root)
    if tracked is None:
        return None
    return frozenset(e for e in literal_excludes(excludes) if e in tracked)


# ---- the ANTI-VACUITY floor, and what its number is derived from -----------------------------
#
# The floor exists because "the deriving set is empty" and "nothing violates the rule" are the same
# green. Its number is NOT a constant somebody nudged until the run went green: it is
# `len(DECLARED_TRACKED_EXCLUDES)`, and that declaration is held to the tracked corpus in BOTH
# directions, which is strictly stronger than a floor — a floor cannot tell "we closed one" from
# "we lost one", and losing one is precisely how this guard went vacuous.

#: The literal `--exclude` entries this repo's git index CARRIES. A reviewed decision, exactly like
#: `test_shim_declaration.DECLARED_TRANSLATING_MODULES`, and edited deliberately — never nudged.
#: Every entry is an internal path that ships to nobody: CLAUDE.md and the three internal doc trees,
#: plus the three dev-only scripts. What it EXCLUDES is as load-bearing as what it holds —
#: `docs/marketing/` is gitignored, `.git` exists on both sides of the boundary, and `.venv`/
#: `build`/`dist`/`site`/`.mokata` are in no checkout at all.
DECLARED_TRACKED_EXCLUDES = frozenset([
    "CLAUDE.md",
    "docs/build",
    "docs/launch",
    "docs/talks",
    "scripts/check-tracker-tables.py",
    "scripts/release.sh",
    "scripts/sync-public.sh",
])

BASIS_DERIVING_SET_DECLARED = "deriving_set_declared"   # matches the declaration -> GREEN
BASIS_DERIVING_SET_DRIFTED = "deriving_set_drifted"     # gained and/or lost entries -> RED
BASIS_DERIVING_SET_EMPTY = "deriving_set_empty"         # nothing to derive from -> UNDECIDABLE
BASIS_INDEX_UNREADABLE = "index_unreadable"             # the reader failed -> UNDECIDABLE

_DERIVING_VERDICT_OF = {
    BASIS_DERIVING_SET_DECLARED: GREEN,
    BASIS_DERIVING_SET_DRIFTED: RED,
    BASIS_DERIVING_SET_EMPTY: UNDECIDABLE,
    BASIS_INDEX_UNREADABLE: UNDECIDABLE,
}

DERIVING_UNDECIDABLE_BASES = frozenset(
    b for b, v in _DERIVING_VERDICT_OF.items() if v == UNDECIDABLE)


class DerivingSetResolution(object):
    """Whether the set the sweep judges AGAINST is real. Verdict is a property of basis, never
    stored beside it — the same discipline as `FileResolution`."""

    __slots__ = ("basis", "gained", "lost", "detail")

    def __init__(self, basis, gained=(), lost=(), detail=""):
        if basis not in _DERIVING_VERDICT_OF:
            raise ValueError("unknown basis %r" % (basis,))
        if basis in DERIVING_UNDECIDABLE_BASES and not detail:
            raise ValueError(
                "basis=%r is UNDECIDABLE and carries no reason. An undecidable that cannot say "
                "why is a shrug, and a reader rounds a shrug to green." % (basis,))
        object.__setattr__(self, "basis", basis)
        object.__setattr__(self, "gained", tuple(sorted(gained)))
        object.__setattr__(self, "lost", tuple(sorted(lost)))
        object.__setattr__(self, "detail", detail)

    def __setattr__(self, *_a):                       # pragma: no cover - immutability guard
        raise AttributeError("DerivingSetResolution is immutable")

    @property
    def verdict(self):
        return _DERIVING_VERDICT_OF[self.basis]

    def render(self):
        if self.verdict == UNDECIDABLE:
            return "UNKNOWN — %s" % self.detail
        if self.basis == BASIS_DERIVING_SET_DECLARED:
            return "GREEN — the deriving set is the declared %d tracked excludes" % len(
                DECLARED_TRACKED_EXCLUDES)
        return ("RED — the tracked exclude set drifted from the declaration; no longer tracked: "
                "%s; newly tracked and undeclared: %s. Update DECLARED_TRACKED_EXCLUDES "
                "deliberately — it is a reviewed decision, not a tally to be nudged until the "
                "test goes green." % (list(self.lost) or "none", list(self.gained) or "none"))

    def __repr__(self):                               # pragma: no cover - debugging aid
        return "<deriving-set %s basis=%s>" % (self.verdict.upper(), self.basis)


def resolve_deriving_set(tracked, declared=DECLARED_TRACKED_EXCLUDES):
    """Derive whether `tracked` is a real deriving set. NEVER returns GREEN by default.

    Three answers, never two (§7g):

      * `tracked is None`  -> UNDECIDABLE, the index could not be read;
      * `tracked` empty    -> UNDECIDABLE, there is nothing to judge against and every file would
                              go green by seeing nothing;
      * otherwise          -> GREEN iff it equals the declaration, else RED naming both drifts.
    """
    if tracked is None:
        return DerivingSetResolution(
            BASIS_INDEX_UNREADABLE,
            detail="the git index could not be read, so which excluded paths this repo actually "
                   "carries is unknown — which is not the same as it carrying none")
    tracked = frozenset(tracked)
    if not tracked:
        return DerivingSetResolution(
            BASIS_DERIVING_SET_EMPTY,
            detail="no literal exclude entry is tracked in this checkout, so 'reads nothing held "
                   "back by the boundary' is vacuously true of every shipped file. An empty "
                   "deriving set and a clean sweep must not share a green.")
    gained = tracked - frozenset(declared)
    lost = frozenset(declared) - tracked
    if gained or lost:
        return DerivingSetResolution(BASIS_DERIVING_SET_DRIFTED, gained=gained, lost=lost)
    return DerivingSetResolution(BASIS_DERIVING_SET_DECLARED)


def excluded_match(path, literals):
    """The exclude entry covering `path`, or None. Prefix match on SEGMENTS, not substrings."""
    want = path.strip("/")
    for entry in sorted(literals, key=len, reverse=True):
        if want == entry or want.startswith(entry + "/"):
            return entry
    return None


# ---- path reconstruction ---------------------------------------------------------------------

def _dotted(node):
    """`os.path.join` from an Attribute chain, or None for anything else."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _const_str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _join_tail(node):
    """(head-name, "a/b") for `os.path.join(HEAD, "a", "b")` with an all-constant tail, else None.

    A non-constant segment anywhere in the tail returns None — blind spot 1, stated rather than
    guessed at.
    """
    if not isinstance(node, ast.Call) or _dotted(node.func) not in _JOIN_NAMES:
        return None
    if len(node.args) < 2 or not isinstance(node.args[0], ast.Name):
        return None
    segments = []
    for arg in node.args[1:]:
        text = _const_str(arg)
        if text is None:
            return None
        segments.append(text)
    return node.args[0].id, "/".join(segments)


def _root_names(tree):
    """Module-level names assigned from an expression mentioning `__file__` (blind spot 5)."""
    names = set()
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not any(isinstance(n, ast.Name) and n.id == "__file__" for n in ast.walk(stmt.value)):
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return frozenset(names)


def _probe_args(node):
    """Every node sitting inside an existence probe — asked about, never opened."""
    inside = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and _dotted(child.func) in _EXISTS_NAMES:
            for arg in child.args:
                for sub in ast.walk(arg):
                    inside.add(id(sub))
    return inside


class _Context(object):
    """Everything a scope needs to decide whether it reads across the boundary."""

    __slots__ = ("literals", "roots", "tainted", "accessors", "alias_tainted", "alias_accessors")

    def __init__(self, literals, roots, tainted, accessors, alias_tainted=None,
                 alias_accessors=None):
        self.literals = literals
        self.roots = roots
        self.tainted = tainted
        self.accessors = accessors
        self.alias_tainted = alias_tainted or {}
        self.alias_accessors = alias_accessors or {}


def _anchored_path(node, ctx):
    """The boundary-crossing path this node names by ROOT ANCHOR, or None."""
    tail = _join_tail(node)
    if tail is None or tail[0] not in ctx.roots:
        return None
    return excluded_match(tail[1], ctx.literals) and tail[1]


def _accessor_path(node, ctx):
    """The path a CALL to a known accessor reaches, when it is handed a root name."""
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name):
        path = ctx.accessors.get(node.func.id)
        label = node.func.id
    elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
        path = ctx.alias_accessors.get((node.func.value.id, node.func.attr))
        label = "%s.%s" % (node.func.value.id, node.func.attr)
    else:
        return None
    if path is None:
        return None
    for arg in node.args:
        if isinstance(arg, ast.Name) and arg.id in ctx.roots:
            return path, "%s(%s)" % (label, arg.id)
    return None


def _reads_in(node, ctx):
    """[(path, via)] — every boundary-crossing read reachable from `node`. Probes excluded."""
    probed = _probe_args(node)
    hits = []
    for child in ast.walk(node):
        if id(child) in probed:
            continue
        anchored = _anchored_path(child, ctx)
        if anchored:
            hits.append((anchored, ""))
            continue
        accessed = _accessor_path(child, ctx)
        if accessed:
            hits.append(accessed)
            continue
        if isinstance(child, ast.Name) and child.id in ctx.tainted:
            hits.append((ctx.tainted[child.id], child.id))
        elif isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name):
            key = (child.value.id, child.attr)
            if key in ctx.alias_tainted:
                hits.append((ctx.alias_tainted[key], "%s.%s" % key))
    return hits


# ---- taint, to a fixpoint --------------------------------------------------------------------

def _accessors(tree, literals):
    """{module-level function name -> path} for functions that join a PARAMETER with an excluded
    tail. The function itself reads nothing: whether it crosses the boundary is the caller's."""
    found = {}
    for stmt in tree.body:
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = {a.arg for a in stmt.args.args} | {a.arg for a in stmt.args.kwonlyargs}
        if stmt.args.vararg:
            params.add(stmt.args.vararg.arg)
        for child in ast.walk(stmt):
            tail = _join_tail(child)
            if tail and tail[0] in params and excluded_match(tail[1], literals):
                found[stmt.name] = tail[1]
                break
    return found


def module_taint(tree, literals):
    """({name -> path} for anchored constants and the functions that touch them, {accessors})."""
    roots = _root_names(tree)
    accessors = _accessors(tree, literals)
    ctx = _Context(literals, roots, {}, accessors)
    tainted = {}
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        hits = _reads_in(stmt.value, ctx)
        if hits:
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    tainted[target.id] = hits[0][0]
    changed = True
    while changed:
        changed = False
        ctx = _Context(literals, roots, tainted, accessors)
        for stmt in tree.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if stmt.name in tainted:
                continue
            hits = _reads_in(stmt, ctx)
            if hits:
                tainted[stmt.name] = hits[0][0]
                changed = True
    return tainted, accessors


def _aliases(tree, module_taints, module_accessors):
    """One hop across modules: {(alias, attr) -> path} and {name -> path} for both kinds."""
    alias_tainted, alias_accessors = {}, {}
    local_tainted, local_accessors = {}, {}
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                local = alias.asname or alias.name.split(".")[0]
                for key, path in module_taints.get(alias.name, {}).items():
                    alias_tainted[(local, key)] = path
                for key, path in module_accessors.get(alias.name, {}).items():
                    alias_accessors[(local, key)] = path
        elif isinstance(stmt, ast.ImportFrom):
            taints = module_taints.get(stmt.module or "", {})
            accs = module_accessors.get(stmt.module or "", {})
            for alias in stmt.names:
                local = alias.asname or alias.name
                if alias.name in taints:
                    local_tainted[local] = taints[alias.name]
                if alias.name in accs:
                    local_accessors[local] = accs[alias.name]
    return alias_tainted, alias_accessors, local_tainted, local_accessors


# ---- guards ----------------------------------------------------------------------------------

def _is_existence_condition(node):
    """`os.path.exists(P)`, or an `and` of nothing but those.

    An `and` is accepted because a class needing TWO internal files must be able to require both,
    and requiring more is strictly stronger. An `or` is NOT: it runs the class when only one of
    the files it reads is present, which is the un-guard wearing a guard's clothes.
    """
    if isinstance(node, ast.Call):
        return _dotted(node.func) in _EXISTS_NAMES
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        return all(_is_existence_condition(v) for v in node.values)
    return False


def guard_of(classdef):
    """Which guard shape a class carries. Only `GUARD_DECORATOR` is in `ACCEPTED_GUARDS`."""
    for dec in classdef.decorator_list:
        if not isinstance(dec, ast.Call) or not dec.args:
            continue
        if _dotted(dec.func) not in _SKIP_UNLESS_NAMES:
            continue
        if _is_existence_condition(dec.args[0]):
            return GUARD_DECORATOR
    for stmt in classdef.body:
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if stmt.name not in ("setUpClass", "setUp"):
            continue
        for child in ast.walk(stmt):
            if isinstance(child, ast.Raise) and _dotted(child.exc) in _SKIPTEST_NAMES:
                return GUARD_SETUPCLASS_SKIP
            if isinstance(child, ast.Call):
                name = _dotted(child.func) or ""
                if name.split(".")[-1] in ("skipTest", "SkipTest"):
                    return GUARD_SETUPCLASS_SKIP
    return GUARD_NONE


def guard_of_source(source):
    """The guard shape of the FIRST class in `source` — the fixture-sized form of `guard_of`."""
    for stmt in ast.parse(source).body:
        if isinstance(stmt, ast.ClassDef):
            return guard_of(stmt)
    return GUARD_NONE


# ---- the derivation --------------------------------------------------------------------------

def resolve(filename, source, excludes, module_taints=None, module_accessors=None):
    """Derive one shipped file's status. NEVER returns GREEN by default."""
    if source is None:
        return FileResolution(
            filename, BASIS_SOURCE_UNREADABLE,
            detail="%s could not be read, so nothing about its reads could be inspected"
                   % filename)
    literals = literal_excludes(excludes)
    if not literals:
        return FileResolution(
            filename, BASIS_NO_EXCLUDES,
            detail="no LITERAL mirror-exclude entries were supplied, so 'reads nothing held back "
                   "by the boundary' is vacuously true of every file and byte-identical to "
                   "'every read is guarded'. sync-public.sh is itself excluded, so this is the "
                   "expected answer when the sweep runs FROM the mirror.")
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return FileResolution(
            filename, BASIS_SOURCE_UNPARSEABLE,
            detail="%s did not parse (%s), so its reads are unknown rather than absent"
                   % (filename, exc))

    tainted, accessors = module_taint(tree, literals)
    alias_tainted, alias_accessors, from_tainted, from_accessors = _aliases(
        tree, module_taints or {}, module_accessors or {})
    tainted = dict(tainted, **from_tainted)
    accessors = dict(accessors, **from_accessors)
    ctx = _Context(literals, _root_names(tree), tainted, accessors,
                   alias_tainted, alias_accessors)

    reads = []
    for stmt in tree.body:
        if isinstance(stmt, ast.ClassDef):
            hits = _reads_in(stmt, ctx)
            if hits:
                reads.append(Read(stmt.name, hits[0][0], guard_of(stmt), hits[0][1]))
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Import,
                               ast.ImportFrom)):
            continue                      # a def is only a read once something CALLS it
        else:
            # A module-level assignment that merely BUILDS a path is a definition, not a read:
            # `RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")` opens nothing and is the
            # normal idiom in every guarded file here. `TEXT = open(RELEASE_SH).read()` is a read,
            # and no class decorator can guard it — the import itself fails on the mirror.
            value = getattr(stmt, "value", None)
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and value is not None \
                    and _anchored_path(value, ctx):
                continue
            hits = _reads_in(stmt, ctx)
            if hits:
                reads.append(Read(SCOPE_MODULE, hits[0][0], GUARD_NONE, hits[0][1]))

    if not reads:
        return FileResolution(filename, BASIS_NO_EXCLUDED_READS)
    if all(r.guarded for r in reads):
        return FileResolution(filename, BASIS_ALL_READS_GUARDED, reads)
    return FileResolution(filename, BASIS_UNGUARDED_READS, reads)


def resolve_all(corpus, excludes):
    """`corpus` is {filename: source-or-None}. Pure: it discovers nothing and opens nothing."""
    literals = literal_excludes(excludes)
    module_taints, module_accessors = {}, {}
    if literals:
        for filename, source in sorted(corpus.items()):
            if source is None:
                continue
            try:
                tree = ast.parse(source, filename=filename)
            except SyntaxError:
                continue
            module = os.path.basename(filename)
            module = module[:-3] if module.endswith(".py") else module
            module_taints[module], module_accessors[module] = module_taint(tree, literals)
    return tuple(
        resolve(f, s, excludes, module_taints, module_accessors)
        for f, s in sorted(corpus.items()))


def offenders(resolutions):
    return tuple(r for r in resolutions if r.verdict == RED)


def undecided(resolutions):
    return tuple(r for r in resolutions if not r.decided)


def report(resolutions):
    """The lines a RED prints — one per offending file, naming the scope and the path."""
    return tuple(
        "%s: %s" % (r.filename, r.render()) for r in offenders(resolutions))


# ---- impure edges: reading the real tree -----------------------------------------------------

#: The directories `sync-public.sh` does NOT exclude, i.e. the test files that ship. Stated here
#: and nowhere else, so blind spot 6 has exactly one address.
SHIPPED_TEST_DIRS = ("tests", "tests/integration")


def shipped_test_sources(root, dirs=SHIPPED_TEST_DIRS):
    """{repo-relative filename: source or None} for every shipped `.py` under `dirs`."""
    corpus = {}
    for rel in dirs:
        directory = os.path.join(root, *rel.split("/"))
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".py"):
                continue
            path = os.path.join(directory, name)
            if not os.path.isfile(path):
                continue
            key = "%s/%s" % (rel, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    corpus[key] = fh.read()
            except OSError:
                corpus[key] = None
    return corpus
