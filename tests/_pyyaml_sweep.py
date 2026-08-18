"""PYYAML-SKIP-CLUSTER — does any test TOLERATE a missing YAML parser?

The defect this answers, stated plainly: a test that needs PyYAML and does not have it has three
honest options and one dishonest one. It can SKIP (visible in the run summary), it can RAISE
(visible as a red), or it can be written not to need the parser at all. What it must never do is
report a PASS for a check it did not perform — and the cluster this module sweeps had that in two
flavours: a fall-through to a substring assertion under the same test name, and a bare `return`
with no assertion, no fallback and no skip marker, which is indistinguishable from a test that ran.

WHAT IS SWEPT IS THE DISPOSITION, NOT THE IMPORT STYLE. `try: import yaml` is not itself a defect —
`tests/_workflow_pins.py` uses exactly that shape and is correct, because the handler RAISES. What
makes a site an offender is what it does on the absent path. So the classifier answers one
question per conditional: when the parser is absent, does this code REFUSE, or does it carry on?

PURE FUNCTIONS OVER A SUPPLIED CORPUS (doc 85 §7i). Once the tree is clean there is no offender
left in it, so a guard that walked the real tree itself would pass whether or not it worked. Every
function here takes the corpus as an argument, so the guard can be fed PLANTED offenders — one per
shape — and graded against them. `tests/test_pyyaml_skip_cluster.py` does exactly that.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import os
import re

# What a PyYAML-conditional does when the parser is ABSENT.
REFUSE = "REFUSE"        # raises, or self.fail()s — the run cannot report OK having not checked
TOLERATE = "TOLERATE"    # skips, returns, or falls through to a weaker assertion — an offender

# The idioms, named so a failure message can say WHICH shape it found rather than "a site".
SKIP_DECORATOR = "skip-decorator"     # @unittest.skipUnless(_HAVE_YAML, …)
SKIP_CALL = "skipTest"                # self.skipTest("PyYAML not installed")
SILENT_RETURN = "silent-return"       # if not _HAVE_YAML: return          <- the worst
WEAKENED = "weakened-assertion"       # if not _HAVE_YAML: assertIn(…); return
FALLTHROUGH = "fallthrough"           # if _HAVE_YAML: <the real check>    (no else at all)
TOLERANT_HANDLER = "tolerant-handler" # except ImportError: return None


class Site:
    """One PyYAML-conditional, with enough provenance to name it in a failure message."""

    __slots__ = ("module", "line", "disposition", "idiom")

    def __init__(self, module, line, disposition, idiom):
        self.module = module
        self.line = line
        self.disposition = disposition
        self.idiom = idiom

    def __repr__(self):
        return "%s:%d %s (%s)" % (self.module, self.line, self.disposition, self.idiom)

    def __eq__(self, other):
        return isinstance(other, Site) and repr(self) == repr(other)

    def __hash__(self):
        return hash(repr(self))


def _guards_yaml_import(try_node):
    """True when this `try:` block is guarding an `import yaml`."""
    for stmt in try_node.body:
        if isinstance(stmt, ast.Import):
            if any(a.name == "yaml" or a.name.startswith("yaml.") for a in stmt.names):
                return True
        elif isinstance(stmt, ast.ImportFrom):
            if (stmt.module or "").split(".")[0] == "yaml":
                return True
    return False


def _catches_import_error(handler):
    exc = handler.type
    if exc is None:                                   # bare `except:` still catches it
        return True
    names = exc.elts if isinstance(exc, ast.Tuple) else [exc]
    return any(isinstance(n, ast.Name) and n.id in ("ImportError", "ModuleNotFoundError")
               for n in names)


def _refuses(body):
    """Does this branch REFUSE — raise, or call `.fail(...)` — rather than carry on?

    `self.fail(...)` counts because unittest's own refusal is a failure, not an exception the
    reader recognises as one; a site that calls it has not reported a pass.
    """
    for node in body:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Raise):
                return True
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "fail"):
                return True
    return False


def _asserts(body):
    for node in body:
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr.startswith("assert")):
                return True
    return False


def _returns(body):
    for node in body:
        for sub in ast.walk(node):
            if isinstance(sub, (ast.Return, ast.Pass)):
                return True
    return False


def _mentions_yaml(node):
    """A skipTest/skipUnless message that names the parser."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            if "pyyaml" in sub.value.lower() or "yaml parser" in sub.value.lower():
                return True
    return False


def _flag_names(tree):
    """Names a try/except-around-`import yaml` binds to a bool — the `_HAVE_YAML` idiom.

    Restricted to CONSTANT booleans on purpose: `yaml = None` in the same handler is a stub for
    the module, not a predicate anyone branches on, and treating it as a flag would make every
    later mention of `yaml` look like a conditional.
    """
    flags = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not _guards_yaml_import(node):
            continue
        for stmt in list(node.body) + [s for h in node.handlers for s in h.body]:
            if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant) \
                    and isinstance(stmt.value.value, bool):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        flags.add(target.id)
    return flags


def _absent_branch(test, node, flags):
    """(branch, found) — the statements that run when the parser is ABSENT, for an `if` on a flag.

    `if FLAG:` runs its `orelse` on the absent path; `if not FLAG:` runs its `body`.
    """
    if isinstance(test, ast.Name) and test.id in flags:
        return node.orelse, True
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = test.operand
        if isinstance(inner, ast.Name) and inner.id in flags:
            return node.body, True
    return [], False


def _skips(body):
    for node in body:
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "skipTest"):
                return True
    return False


def conditional_sites(module, source):
    """Every PyYAML-conditional in one module's source, classified. Pure; no filesystem."""
    tree = ast.parse(source, filename=module)
    flags = _flag_names(tree)
    sites = []
    # `if not _HAVE_YAML: self.skipTest(…)` is ONE site, not two. Rule (b) owns it — it can see
    # the flag — so rule (d) must not count the same call again. Counting a site twice is a
    # small lie in the same family as the one this module exists to catch.
    claimed = set()

    for node in ast.walk(tree):
        # (a) `except ImportError:` around `import yaml` that RETURNS instead of raising —
        #     the inline idiom (`test_stage66_cross_platform`) and the tolerant factory
        #     (`test_ci_matrix_floor._yaml`). A handler that only binds a flag is NEUTRAL here;
        #     its disposition is decided at the `if` sites below.
        if isinstance(node, ast.Try) and _guards_yaml_import(node):
            for handler in node.handlers:
                if not _catches_import_error(handler):
                    continue
                if _refuses(handler.body):
                    continue
                if _returns(handler.body):
                    idiom = WEAKENED if _asserts(handler.body) else TOLERANT_HANDLER
                    sites.append(Site(module, handler.lineno, TOLERATE, idiom))

        # (b) an `if` on the availability flag
        if isinstance(node, ast.If):
            branch, found = _absent_branch(node.test, node, flags)
            if found:
                for stmt in branch:
                    for sub in ast.walk(stmt):
                        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                                and sub.func.attr == "skipTest":
                            claimed.add(sub.lineno)
                if _refuses(branch):
                    sites.append(Site(module, node.lineno, REFUSE, "refusal"))
                elif _skips(branch):
                    sites.append(Site(module, node.lineno, TOLERATE, SKIP_CALL))
                elif not branch:
                    sites.append(Site(module, node.lineno, TOLERATE, FALLTHROUGH))
                elif _asserts(branch):
                    sites.append(Site(module, node.lineno, TOLERATE, WEAKENED))
                else:
                    sites.append(Site(module, node.lineno, TOLERATE, SILENT_RETURN))

        # (c) `@unittest.skipUnless(_HAVE_YAML, …)` / `@unittest.skipIf(not _HAVE_YAML, …)`
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not dec.args:
                    continue
                name = dec.func.attr if isinstance(dec.func, ast.Attribute) else \
                    getattr(dec.func, "id", "")
                if not name.startswith("skip"):
                    continue
                if any(isinstance(n, ast.Name) and n.id in flags for n in ast.walk(dec.args[0])):
                    sites.append(Site(module, dec.lineno, TOLERATE, SKIP_DECORATOR))

        # (d) `self.skipTest("PyYAML not installed …")` — no flag needed to recognise it
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "skipTest" and _mentions_yaml(node) \
                and node.lineno not in claimed:
            sites.append(Site(module, node.lineno, TOLERATE, SKIP_CALL))

    return sorted(sites, key=lambda s: (s.module, s.line))


def sweep(corpus):
    """corpus: {module_name: source_text} -> every conditional across it, classified."""
    out = []
    for module in sorted(corpus):
        out.extend(conditional_sites(module, corpus[module]))
    return out


def tolerant_sites(corpus):
    """The offenders — conditionals that let a run report OK with the check unperformed."""
    return [s for s in sweep(corpus) if s.disposition == TOLERATE]


# ───────────────────────────────────────────────────────────── the other half: the CI side
# The tests above can only be honest about a missing parser. Whether the parser is THERE is a
# workflow question, and it is the half stage 10 found: `embeddings-leg.yml` ran the whole unit
# suite with no PyYAML installed, so seventeen tests skipped on every run while the job reported
# OK. Now that those seventeen RAISE instead, such a job goes red — but red at CI time, from an
# import error, is a worse message than red at lint time from a sweep that names the job.
#
# `parse` is INJECTED rather than imported. This module stays stdlib-only, so the guard for
# "tests that quietly need a parser" does not itself quietly need one.

_UNITTEST = re.compile(r"python\s+-m\s+unittest\b([^\n;|&)]*)")


def _test_invocations(job):
    out = []
    for step in job.get("steps") or []:
        folded = str(step.get("run", "")).replace("\\\n", " ")
        for args in _UNITTEST.findall(folded):
            out.append(args.strip())
    return out


def runs_the_whole_unit_suite(args):
    """Does this `python -m unittest <args>` load AND run every module under tests/?

    `-p` restricts which files are imported; `-k` deselects by test name AFTER importing them.
    Either one means the job does not run the parser-dependent tests, so it needs no parser —
    that is the distinction a grep over workflow files cannot make, and the reason three
    workflows were suspected at this stage's open and cleared by measurement.
    """
    tokens = args.split()
    if not tokens or tokens[0] != "discover":
        return False
    if "-s" not in tokens:
        return False
    source = tokens[tokens.index("-s") + 1] if tokens.index("-s") + 1 < len(tokens) else ""
    if source.rstrip("/") != "tests":
        return False                                     # tests/integration is a different corpus
    return "-p" not in tokens and "-k" not in tokens


def jobs_without_the_parser(corpus, parse):
    """(workflow, job) for every job that RUNS the whole unit suite without installing PyYAML.

    corpus: {workflow filename: text}. parse: text -> the parsed document.
    """
    offenders = []
    for name in sorted(corpus):
        doc = parse(corpus[name]) or {}
        for job_name, job in sorted((doc.get("jobs") or {}).items()):
            job = job or {}
            if not any(runs_the_whole_unit_suite(a) for a in _test_invocations(job)):
                continue
            blob = "\n".join(str(s.get("run", "")) for s in (job.get("steps") or []))
            if "requirements/ci.txt" not in blob:
                offenders.append((name, job_name))
    return offenders


def read_corpus(directory):
    """{name: source} for every .py in `directory`. The ONLY function here that touches disk."""
    corpus = {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            corpus[name] = fh.read()
    return corpus


def render(sites):
    """One line per site, for a failure message or the stage report."""
    return "\n".join("  %s" % s for s in sites)
