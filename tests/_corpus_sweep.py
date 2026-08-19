"""CORPUS-IS-A-FILESYSTEM-WALK-NOT-THE-INDEX — which tree is each sweep's question about?

The defect, stated plainly: a test whose corpus is a filesystem walk sees untracked and
gitignored files, so its verdict depends on what happens to be sitting on the disk it runs on.
Measured in this repo at stage 3, `test_pin_drift`'s corpus was 11,912 paths of which 11,404
were untracked — 11,327 of them `.mokata/temp_local/*.json` left behind by earlier suite runs.
It passed only because none of them contained the pin string it greps for. That is the whole
class: *index-equivalent today by luck, not by construction.*

⚠ CONVERTING EVERY WALK WOULD BE WRONG, AND THAT IS THE POINT OF THIS MODULE. `sync-public.sh`
mirrors with `rsync`, which copies the WORKING TREE — an untracked `tests/foo.py` really does
ship to the public mirror. So `_shipped_reads` and `test_suite_count_integrity` must stay disk
reads; converting them to the index would reintroduce `SHIPPED-TEST-READS-INTERNAL-FILE` from
the blind side. There are two legitimate corpora, and FOUR answers a site can give:

    "what does GIT TRACK?"        -> the index is truth; a walk is the bug   ASKS_INDEX
    "what will RSYNC SHIP?"       -> the disk is truth; the index is blind   ASKS_WORKING_TREE
    "neither — I built this tree" -> a fixture is its own ground truth       ASKS_NEITHER
    "I cannot tell — my CALLER
     picks the root"              -> undecidable HERE, and counted as such   ASKS_UNKNOWN

WHAT IS SWEPT IS THE QUESTION, NOT THE CALL — the same move stage 2 made when it graded the
DISPOSITION of a PyYAML conditional rather than its import style, which is why that sweep held.
`os.walk` is not evidence of anything on its own: `test_nested_checkout_boundary` walks the disk
on purpose because the walker is its subject.

EVERY BUCKET IS EXPOSED, NONE FOLDED. Two thirds of this repo's sites are not in the defect
class at all — some because the test built the tree, some because the function is a helper whose
root its caller supplies. Those are DIFFERENT facts (§7g) and the sweep reported them as one
until mutant M02 of its own batch survived and showed it: 60 sites were reading as "a fixture,
not in the class", among them `_shipped_reads.shipped_test_sources`, the single site the row
names by hand as must-not-convert. A count that folds "I could not tell" into "nothing to see
here" is not a measurement, it is a reassurance.

HOW A SITE'S QUESTION IS DECIDED — derivation, then declaration, then a cross-check:

 1. `anchor_of` traces the walk's ROOT expression back through assignments to its origin. A root
    that bottoms out in `__file__` or `os.getcwd()` is REPO_ANCHORED; one that bottoms out in
    `mkdtemp`/`TemporaryDirectory`/a `tmp()` helper is FIXTURE_ANCHORED. This is data flow, not a
    regex over variable names: `ROOT` and `root` and `d` tell you nothing, and a sweep that
    trusted them would be the typed-scope mistake (doc 85 §7j) one level down.
 2. A REPO_ANCHORED site must DECLARE its question with a `CORPUS: THE INDEX` / `CORPUS: THE
    WORKING TREE` comment in the enclosing scope. An undeclared repo-anchored walk is an
    offender — not because it is necessarily wrong, but because nobody has said which tree it
    means, and that silence is what let 48 sites accumulate.
 3. THE DECLARATION IS CHECKED AGAINST THE CODE. A site declaring THE INDEX must read the index;
    one declaring THE WORKING TREE must not. This is why the rule is a predicate and not an
    allow-list: you cannot satisfy it by writing your name in a register, because the call has
    to match the claim. An allow-list here would be the per-caller `skip_dirs` mistake — the one
    this row already names — one level up.

PURE FUNCTIONS OVER A SUPPLIED CORPUS (doc 85 §7i). Once the offenders are converted the tree
holds none, so a guard that walked the real tree would pass whether or not it worked. Every
function here takes its corpus as an argument; `tests/test_stage3_corpus_walk.py` feeds it
planted sites, one per shape, including the two that must NOT be flagged.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import os
import re
import _support

# --- what tree the site's question is about ------------------------------------------------
ASKS_INDEX = "ASKS_INDEX"                  # git is truth; a walk here is the defect
ASKS_WORKING_TREE = "ASKS_WORKING_TREE"    # the disk is truth (rsync ships it); correct as-is
ASKS_NEITHER = "ASKS_NEITHER"              # a fixture tree the test built; not in the class
ASKS_UNKNOWN = "ASKS_UNKNOWN"              # the CALLER supplies the root — undecidable here
UNDECLARED = "UNDECLARED"                  # repo-anchored and nobody said which — the offender

# ⚠ ASKS_UNKNOWN AND ASKS_NEITHER ARE DIFFERENT FACTS AND MUST NOT SHARE A REPRESENTATION (§7g).
# "the test built this tree, so tracked has no meaning" and "this is a helper whose root its
# CALLER picks, so I cannot tell from here" are not the same claim, and only one of them is a
# reassurance. Folding the second into the first is what this sweep did until mutant M02 of its
# own batch survived and exposed it — 60 sites, including `_shipped_reads.shipped_test_sources`,
# the one site the row names by hand as must-not-convert, were reading as "not in the class".

# --- where the walk's root came from -------------------------------------------------------
REPO_ANCHORED = "repo-anchored"            # traces back to __file__ / os.getcwd()
FIXTURE_ANCHORED = "fixture-anchored"      # traces back to mkdtemp / TemporaryDirectory
UNRESOLVED = "unresolved"                  # the trace did not bottom out — surfaced, not guessed

# --- the idioms, named so a failure says WHICH shape it found ------------------------------
WALK = "os.walk"
LISTDIR = "os.listdir"
SCANDIR = "os.scandir"
GLOB = "glob"
RGLOB = "rglob"
ITERDIR = "iterdir"
WORKTREE_READER = "iter_worktree_files"
INDEX_READER = "iter_tracked_files"
RELATIVE_EXISTS = "relative-path-exists"   # the shape the stage-29 AST scope MISSED (§7j)

# Calls that read a directory's contents off the disk. `ast.walk` is deliberately absent — it
# walks a syntax tree, not a filesystem, and counting it was a live risk when this list was
# first written.
_DIR_CALLS = {
    "os.walk": WALK,
    "os.listdir": LISTDIR,
    "os.scandir": SCANDIR,
    "glob.glob": GLOB,
    "glob.iglob": GLOB,
}
_DIR_METHODS = {"rglob": RGLOB, "glob": GLOB, "iterdir": ITERDIR}
_READERS = {"iter_worktree_files": WORKTREE_READER, "iter_tracked_files": INDEX_READER}

# Existence checks. Harmless against an absolute path; against a RELATIVE one they are resolved
# against the process CWD, so the verdict depends on where the runner was invoked from. This
# class is in the sweep because stage 2 found exactly such a site
# (`test_ph_gate_s0`: FAILED from `tests/`, OK from the root) and the stage-29 AST scope did not
# contain it — `os.path.exists` was not in the call list that derived "50 sites". A derivation
# that types its own scope derives a subset (doc 85 §7j).
_EXISTS_CALLS = {"os.path.exists", "os.path.isfile", "os.path.isdir", "os.path.lexists"}

_DECLARATION = re.compile(r"CORPUS:\s*THE\s+(INDEX|WORKING\s+TREE|FIXTURE)\b", re.I)


class Site:
    """One corpus read, with enough provenance to name it in a failure message."""

    __slots__ = ("module", "line", "question", "idiom", "anchor", "scope")

    def __init__(self, module, line, question, idiom, anchor, scope=""):
        self.module = module
        self.line = line
        self.question = question
        self.idiom = idiom
        self.anchor = anchor
        self.scope = scope

    def __repr__(self):
        return "%s:%d %s (%s, %s)" % (
            self.module, self.line, self.question, self.idiom, self.anchor)

    def __eq__(self, other):
        return isinstance(other, Site) and repr(self) == repr(other)

    def __hash__(self):
        return hash(repr(self))


def _dotted(node):
    """Best-effort dotted name for a call target: `os.path.exists`, `p.rglob`, `helper`."""
    parts, cur = [], node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        parts.append("?")
    parts.reverse()
    return ".".join(parts)


# ─────────────────────────────────────────────────────── 1. where did the root come from?

_FIXTURE_ORIGINS = {
    "mkdtemp", "TemporaryDirectory", "TemporaryFile", "NamedTemporaryFile",
    "mkstemp", "tmp", "tmp_path", "make_repo", "_make_repo", "write_sample_repo",
    "write_polyglot_repo",
}
# Traversals that keep you inside whatever tree you started in, so the origin is unchanged.
_TRANSPARENT = {
    "dirname", "abspath", "realpath", "normpath", "join", "relpath", "expanduser",
    "resolve", "parent", "absolute", "Path", "PurePath", "as_posix", "rstrip", "strip",
}


def _assignments(tree):
    """{name: [value_expr, …]} for every simple assignment anywhere in one module.

    Deliberately scope-BLIND. A scope-accurate resolver is a bigger machine than this needs,
    and being blind here is the SAFE direction: it can only widen what a name might mean, and a
    widened trace that reaches two different origins reports UNRESOLVED rather than picking one.
    """
    out = {}
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            targets = [node.optional_vars]
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            targets = [node.target]
        for t in targets:
            value = getattr(node, "value", None) or getattr(node, "iter", None) \
                or getattr(node, "context_expr", None)
            if value is None:
                continue
            if isinstance(t, ast.Name):
                out.setdefault(t.id, []).append(value)
            elif isinstance(t, ast.Attribute):        # self.td = tempfile.mkdtemp()
                out.setdefault(t.attr, []).append(value)
    return out


def anchor_of(expr, assigns, _depth=0):
    """REPO_ANCHORED / FIXTURE_ANCHORED / UNRESOLVED for a walk-root expression.

    Traces the expression back through assignments to its ORIGIN, rather than reading its name.
    `ROOT`, `root`, `src`, `d` are all just names; what decides the class is whether the value
    was computed from `__file__` (this checkout) or from `mkdtemp` (a tree the test built).

    ⚠ `_depth` is the ONLY termination guard, and that is deliberate. `_assignments` is
    scope-blind, so `path = os.path.join(path, name)` is a real cycle in the name graph — this
    module crashed on it at first contact with the real tree. A visited-set guard was written
    alongside the depth limit and then DELETED: measured over all 429 test modules it changed
    not one verdict, so it was a second code path nothing could grade, and mutant M08 of this
    stage's own batch survived precisely because the depth limit already covered it.
    """
    if _depth > 12:
        return UNRESOLVED

    if isinstance(expr, ast.Name):
        if expr.id == "__file__":
            return REPO_ANCHORED
        found = {anchor_of(v, assigns, _depth + 1) for v in assigns.get(expr.id, [])}
        found.discard(UNRESOLVED)
        if len(found) == 1:
            return found.pop()
        return UNRESOLVED                          # unknown, or two origins — never a coin flip

    if isinstance(expr, ast.Attribute):
        if expr.attr == "__file__":                # mokata.__file__ — inside the package
            return REPO_ANCHORED
        if expr.attr in _TRANSPARENT:
            return anchor_of(expr.value, assigns, _depth + 1)
        return anchor_of(ast.Name(id=expr.attr, ctx=ast.Load()), assigns, _depth + 1)

    if isinstance(expr, ast.Call):
        name = _dotted(expr.func)
        tail = name.split(".")[-1]
        if tail in _FIXTURE_ORIGINS:
            return FIXTURE_ANCHORED
        if name in ("os.getcwd", "getcwd"):
            return REPO_ANCHORED                   # the process CWD IS a checkout under test
        if tail in _TRANSPARENT and expr.args:
            found = {anchor_of(a, assigns, _depth + 1) for a in expr.args}
            found.discard(UNRESOLVED)
            if len(found) == 1:
                return found.pop()
            return UNRESOLVED
        return UNRESOLVED

    if isinstance(expr, ast.Subscript):            # Path(__file__).parents[1]
        return anchor_of(expr.value, assigns, _depth + 1)

    if isinstance(expr, ast.BinOp):                # ROOT / "docs"  and  ROOT + "/docs"
        left = anchor_of(expr.left, assigns, _depth + 1)
        return left if left != UNRESOLVED else anchor_of(expr.right, assigns, _depth + 1)

    return UNRESOLVED


# ─────────────────────────────────────────────────────── 2. what did the site DECLARE?

def _scopes(tree):
    """[(node, name)] for every module/class/function scope, innermost last."""
    out = [(tree, "<module>")]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append((node, node.name))
    return out


def _declaration_on(line):
    """The question a single source line declares, or None. It must be a COMMENT.

    A `CORPUS:` inside a docstring or an error message is prose ABOUT the rule, not an
    assertion of it — this module's own header would otherwise declare every site in it.
    """
    m = _DECLARATION.search(line)
    if not m or not line.lstrip().startswith("#"):
        return None
    token = re.sub(r"\s+", " ", m.group(1).upper())
    return {"INDEX": ASKS_INDEX,
            "WORKING TREE": ASKS_WORKING_TREE,
            "FIXTURE": ASKS_NEITHER}[token]


def declared_question(source, lineno, tree=None):
    """The `CORPUS:` declaration governing `lineno`, or None.

    SCOPED BY THE AST, not by counting lines. A declaration governs a site when it sits either
    inside the site's enclosing scope above it, or in the contiguous comment block directly
    above that scope's `def`/`class` (where a reader naturally writes it). Failing that the
    search widens to the next scope out, and finally to the module header.

    The scoping is the whole point: a line-counting version of this let one `CORPUS:` comment
    near the top of a module absolve every walk below it, which is an allow-list with extra
    steps — precisely the thing this sweep exists not to be.
    """
    lines = source.splitlines()
    if lineno > len(lines) or lineno < 1:
        return None
    if tree is None:
        tree = ast.parse(source)

    # innermost enclosing scope first, then outward
    scopes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", None) or node.lineno
            if node.lineno <= lineno <= end:
                scopes.append(node)
    scopes.sort(key=lambda n: -n.lineno)

    for node in scopes:
        # (a) inside the body, above the site
        for i in range(lineno - 2, node.lineno - 1, -1):
            found = _declaration_on(lines[i])
            if found:
                return found
        # (b) the comment block directly above the def/class (above its decorators)
        top = min([d.lineno for d in getattr(node, "decorator_list", [])] or [node.lineno])
        for i in range(top - 2, -1, -1):
            stripped = lines[i].strip()
            if not stripped:
                continue                      # blank lines do not break the block
            if not stripped.startswith("#"):
                break                         # real code — the block has ended
            found = _declaration_on(lines[i])
            if found:
                return found

    if not scopes:                            # a module-level site
        for i in range(lineno - 2, -1, -1):
            found = _declaration_on(lines[i])
            if found:
                return found
            if lines[i].strip() and not lines[i].strip().startswith("#"):
                break
    return None


def _enclosing_scope_name(tree, lineno):
    best, best_line = "<module>", -1
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", None) or lineno
            if node.lineno <= lineno <= end and node.lineno > best_line:
                best, best_line = node.name, node.lineno
    return best


# ─────────────────────────────────────────────────────── 3. the sweep itself

def corpus_sites(module, source):
    """Every corpus read in one module's source, classified. Pure; touches no filesystem."""
    tree = ast.parse(source, filename=module)
    assigns = _assignments(tree)
    sites = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        tail = name.split(".")[-1]

        idiom = root = None
        if name in _DIR_CALLS:
            idiom = _DIR_CALLS[name]
            root = node.args[0] if node.args else None
        elif tail in _READERS:
            idiom = _READERS[tail]
            root = node.args[0] if node.args else None
        elif tail in _DIR_METHODS and isinstance(node.func, ast.Attribute):
            idiom = _DIR_METHODS[tail]
            root = node.func.value
        elif name in _EXISTS_CALLS and node.args:
            # Only the RELATIVE form is a corpus question. An absolute path answers the same
            # everywhere; a relative one silently re-roots itself at the process CWD.
            if _is_relative_literal_chain(node.args[0], assigns):
                sites.append(Site(module, node.lineno, ASKS_INDEX, RELATIVE_EXISTS,
                                  REPO_ANCHORED, _enclosing_scope_name(tree, node.lineno)))
            continue

        if idiom is None:
            continue

        anchor = anchor_of(root, assigns) if root is not None else UNRESOLVED
        scope = _enclosing_scope_name(tree, node.lineno)

        if anchor == FIXTURE_ANCHORED:
            question = ASKS_NEITHER
        elif anchor == UNRESOLVED:
            # NOT an offender — the root is the caller's choice, so there is nothing this site
            # could truthfully declare on its own. But it is counted and reported, so the
            # population cannot grow in silence.
            question = declared_question(source, node.lineno, tree) or ASKS_UNKNOWN
        else:
            question = declared_question(source, node.lineno, tree) or UNDECLARED

        sites.append(Site(module, node.lineno, question, idiom, anchor, scope))

    return sorted(sites, key=lambda s: (s.module, s.line))


def _is_relative_literal_chain(expr, assigns, _depth=0, _seen=frozenset()):
    """True when this path expression is a bare relative string — no root, no `__file__`.

    `os.path.exists("src/mokata/gate_hook.py")` and `os.path.exists(GATE.enforcement_point)`
    both qualify when the value traces to a string literal that does not start at `/` and is
    not joined onto an anchored root.

    Carries the same cycle + depth guard as `anchor_of`, and for the same reason: `_assignments`
    is scope-blind, so `path = os.path.join(path, name)` inside a loop is a real cycle in the
    name graph and recursion through it does not terminate on its own.
    """
    if _depth > 12:
        return False

    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return not os.path.isabs(expr.value) and ("/" in expr.value or "\\" in expr.value)
    if isinstance(expr, ast.Name):
        if expr.id in _seen:
            return False
        _seen = _seen | {expr.id}
        return any(_is_relative_literal_chain(v, assigns, _depth + 1, _seen)
                   for v in assigns.get(expr.id, []))
    if isinstance(expr, ast.Attribute):
        # GATE.enforcement_point — resolve through the attribute name, same as anchor_of does
        if expr.attr in _seen:
            return False
        _seen = _seen | {expr.attr}
        return any(_is_relative_literal_chain(v, assigns, _depth + 1, _seen)
                   for v in assigns.get(expr.attr, []))
    if isinstance(expr, ast.Call):
        name = _dotted(expr.func)
        if name in ("os.path.join", "join") and expr.args:
            return _is_relative_literal_chain(expr.args[0], assigns, _depth + 1, _seen)
    return False


def sweep(corpus):
    """corpus: {module_name: source_text} -> every corpus read across it, classified."""
    out = []
    for module in sorted(corpus):
        out.extend(corpus_sites(module, corpus[module]))
    return out


def undeclared_sites(corpus):
    """The offenders: repo-anchored corpus reads where nobody said which tree they mean."""
    return [s for s in sweep(corpus) if s.question == UNDECLARED]


def mismatched_sites(corpus):
    """Sites whose DECLARATION contradicts the call they make.

    The half that makes this a predicate rather than a register. A site can claim `CORPUS: THE
    INDEX` and still walk the disk — that claim is exactly the false green this row is about,
    and it must go red on the claim, not on the absence of one.
    """
    bad = []
    for s in sweep(corpus):
        if s.question == ASKS_INDEX and s.idiom not in (INDEX_READER, RELATIVE_EXISTS):
            bad.append(s)
        elif s.question == ASKS_WORKING_TREE and s.idiom == INDEX_READER:
            bad.append(s)
    return bad


def by_question(corpus):
    """{question: [site, …]} — every population reported separately, never folded."""
    out = {ASKS_INDEX: [], ASKS_WORKING_TREE: [], ASKS_NEITHER: [],
           ASKS_UNKNOWN: [], UNDECLARED: []}
    for s in sweep(corpus):
        out[s.question].append(s)
    return out


def read_corpus(directory, recursive=False):
    """{name: source} for every .py under `directory`. The ONLY function here that touches disk.

    CORPUS: THE WORKING TREE — deliberately. This reads whatever the caller hands it, including
    the planted fixtures `test_stage3_corpus_walk.py` writes into a tempdir, so it must not
    require its argument to be a checkout at all.
    """
    corpus = {}
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = sorted(d for d in dirnames if d not in ("__pycache__", ".git"))
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            rel = _support.posix_rel(path, directory).replace(os.sep, "/")
            with open(path, encoding="utf-8") as fh:
                corpus[rel] = fh.read()
        if not recursive:
            break
    return corpus


def render(sites):
    """One line per site, for a failure message or the stage report."""
    return "\n".join("  %s  in %s" % (s, s.scope or "<module>") for s in sites)
