"""Derive mokata's public numbers FROM THE REGISTRIES, and grade the docs that state them.

`LANDING-COUNTS-UNGUARDED` (doc 84), filed by LP-15 and built at GATE-COUNT-TRUTH.

WHY THIS EXISTS. LP-15 fixed six numbers across the two landing surfaces, and every one of them
had rotted the same way: a human typed a count, the code moved, and nothing read both. The sweep
that *does* read `docs/` — `docsync` — checks command forms and section staleness; **it does not
derive counts.** It returned ZERO findings for `docs/index.md` while that file asserted 10 backed
gates against a code truth of 9.

TWO AXES, BOTH DERIVED (doc 85 §7j — "of any mechanism that derives, ask which axis is derived
and which is a literal").

  * THE VALUES are derived from the registries: `agent_skills.CURATED_SKILLS`,
    `domains.shipped_domain_skills()`, `cli.build_parser()`, `mcp.registry.TOOLS`,
    `skill_contracts.GATES`, `gate_hook.GATES`, `pyproject.toml`. Never a constant in this file.
  * THE CORPUS is derived from `scripts/sync-public.sh` — the file set that actually ships to the
    public mirror, minus the historical records declared below. A hand-typed list of "the pages
    with numbers on them" would be a derivation of the part somebody remembered, and
    `overrides/home.html` is the standing proof that the part nobody remembers is where the drift
    lives: `docsync.find_docs` sweeps `README.md` + `docs/` only (`docsync.py:733-740`), and
    `overrides/` is not under `docs/`, so **no sweep in mokata's history had ever read it.**

MEMBERSHIP, NOT CARDINALITY (doc 85 §4). The backed-gate count went 9 → 10 → 9 across two
corrections that landed together, and *"a count assertion would have been green through both
moves"*. So a roster is graded on the SET, and the count assertion rides on top of it rather than
standing in for it.

A GUARD FED SYNTHETIC OFFENDERS (doc 85 §7i). Every gate below is a pure function over a supplied
corpus — text in, findings out — never a walk that both discovers and judges. The tests plant
violations and require each function to find them; a function that finds nothing in a clean tree
proves nothing.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import _mirror_bookkeeping  # noqa: E402  (test helper, same directory)
import _support


# ---- the one command a failure message tells you to run ---------------------------------------

#: Printed by every mismatch. A guard whose red does not say how to re-derive gets overridden.
REDERIVE_CMD = (
    "PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest "
    "discover -s tests -t tests -k test_public_counts_guard"
)


# ---- 1 · the derived facts --------------------------------------------------------------------

class Fact:
    """One public number, with the code that produced it and (where the number is a set) its
    members. `members` is None when the fact is genuinely only a count."""

    __slots__ = ("key", "value", "members", "source")

    def __init__(self, key, value, source, members=None):
        self.key = key
        self.value = value
        self.source = source
        self.members = None if members is None else frozenset(members)

    def __repr__(self):  # pragma: no cover - diagnostics only
        return "Fact(%s=%r from %s)" % (self.key, self.value, self.source)


def _runtime_dependencies(root):
    """The `dependencies = [...]` array in `pyproject.toml`, counted. Parsed rather than imported
    because the installed distribution's metadata answers a different question (what is installed)
    than the one the docs make (what mokata requires)."""
    path = os.path.join(root, "pyproject.toml")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    match = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
    if match is None:
        raise AssertionError(
            "pyproject.toml has no top-level `dependencies = [...]` array — the runtime-dependency "
            "count cannot be derived, and a guard that cannot derive must not pass. (%s)" % path
        )
    return [item for item in re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))]


def derived_facts(root):
    """Every public number mokata states, derived from the registry that owns it."""
    from mokata import agent_skills, cli, domains, gate_hook, skill_contracts
    from mokata.mcp import registry
    import argparse

    curated = tuple(agent_skills.CURATED_SKILLS)
    domain = tuple(domains.shipped_domain_skills())

    parser = cli.build_parser()
    subcommands = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            subcommands.update(action.choices)

    tools = tuple(registry.TOOLS)
    kinds = {}
    for tool in tools:
        kinds.setdefault(tool.kind, []).append(tool.name)

    # `gate.backed`, not `getattr(gate, "backed", False)`. `GateRef` is a dataclass with
    # `backed: bool = True`, so that default was unreachable — and it said the OPPOSITE of the
    # field it was defending, so had it ever been reached it would have quietly demoted a real
    # gate. Mutant B01 survived against the getattr form for exactly this reason: an unreachable
    # branch is ungradable (doc 85 §7f), and pre-1.0 the answer is to delete it, not test around it.
    backed = {name for name, gate in skill_contracts.GATES.items() if gate.backed}
    run_state = set(gate_hook.GATES)
    # The hook lane is NOT a subset of the backed nine: `spec-scope` is run-state and unbacked,
    # `self-protect` is backed and runs ahead of all four. Two overlapping sets, never "N of the M".
    hook_enforced = run_state | {"self-protect"}

    deps = _runtime_dependencies(root)

    facts = [
        Fact("skills", len(curated) + len(domain),
             "agent_skills.CURATED_SKILLS + domains.shipped_domain_skills()",
             set(curated) | set(domain)),
        Fact("curated_skills", len(curated), "agent_skills.CURATED_SKILLS", curated),
        Fact("domain_skills", len(domain), "domains.shipped_domain_skills()", domain),
        Fact("slash_commands", len(slash_command_names(root)),
             "src/mokata/templates/commands/*.md", slash_command_names(root)),
        Fact("cli_subcommands", len(subcommands), "cli.build_parser()", subcommands),
        Fact("mcp_tools", len(tools), "mcp.registry.TOOLS", {t.name for t in tools}),
        Fact("mcp_read", len(kinds.get("read", ())), "mcp.registry.TOOLS .kind == 'read'",
             kinds.get("read", ())),
        Fact("mcp_write", len(kinds.get("write", ())), "mcp.registry.TOOLS .kind == 'write'",
             kinds.get("write", ())),
        Fact("mcp_approve", len(kinds.get("approve", ())), "mcp.registry.TOOLS .kind == 'approve'",
             kinds.get("approve", ())),
        Fact("backed_gates", len(backed), "skill_contracts.GATES where backed=True", backed),
        Fact("run_state_gates", len(run_state), "gate_hook.GATES", run_state),
        Fact("hook_enforced", len(hook_enforced), "gate_hook.GATES + self-protect", hook_enforced),
        Fact("runtime_dependencies", len(deps), "pyproject.toml dependencies", deps),
    ]
    return {fact.key: fact for fact in facts}


def slash_command_names(root):
    """The rendered slash commands — one per `templates/commands/*.md`, the same source the
    skill/command drift-guard re-renders from."""
    directory = os.path.join(root, "src", "mokata", "templates", "commands")
    return sorted(
        name[:-3] for name in os.listdir(directory)
        if name.endswith(".md") and not name.startswith("_")
    )


def gate_vocabulary():
    """Every id that IS a gate, backed or not — the closed vocabulary a roster is read against.
    Derived from both registries so a new gate joins the vocabulary without an edit here."""
    from mokata import gate_hook, skill_contracts

    return frozenset(skill_contracts.GATES) | frozenset(gate_hook.GATES)


# ---- 2 · the corpus: what actually ships --------------------------------------------------------

#: Dated records. A count inside a release note is a claim about THAT release, not about today —
#: "a ninth backed gate shipped at 0.0.14" stays true forever and rewriting it would falsify the
#: history. Declared here with the reason rather than silently skipped (doc 85 §7j).
HISTORICAL_FILES = (
    "CHANGELOG.md",
    "docs/changelog.md",
)

#: Directories that carry no prose claims and would only add noise.
_SKIP_DIRS = ("docs/assets", "site", "__pycache__", ".git")

_CLAIM_SUFFIXES = (".md", ".html")


def public_claim_files(root):
    """Every shipped file that could carry a public number: `README.md`, the `docs/` tree and
    `overrides/`, minus whatever `scripts/sync-public.sh` excludes from the mirror, minus the
    dated records above.

    The mirror script is the authority on "public" because it is the thing that actually copies
    the bytes — `.gitignore` does not control the boundary (CLAUDE.md), and neither does docsync's
    own `_INTERNAL_DOC_DIRS`, which knows nothing about `docs/talks/`."""
    excluded = _mirror_excluded_prefixes(root)
    found = []
    for base in ("README.md", "docs", "overrides"):
        full = os.path.join(root, base)
        if os.path.isfile(full):
            candidates = [base]
        elif os.path.isdir(full):
            candidates = []
            for dirpath, dirnames, filenames in os.walk(full):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                for name in sorted(filenames):
                    rel = _support.posix_rel(os.path.join(dirpath, name), root).replace(os.sep, "/")
                    candidates.append(rel)
        else:  # pragma: no cover - a missing tree contributes nothing, never an error
            continue
        for rel in candidates:
            if not rel.endswith(_CLAIM_SUFFIXES):
                continue
            if rel in HISTORICAL_FILES:
                continue
            if any(rel == d or rel.startswith(d + "/") for d in _SKIP_DIRS):
                continue
            if any(rel == e or rel.startswith(e + "/") for e in excluded):
                continue
            found.append(rel)
    return sorted(found)


class MirrorScriptAbsent(AssertionError):
    """`scripts/sync-public.sh` is not in this tree, so the public corpus cannot be derived.

    An `AssertionError` subclass on purpose. The two shapes this could have taken both fail the
    thing this fix exists to prevent:

      * a `SkipTest` would make an unguarded caller SKIP — and mokata's own mirror guard already
        rules that shape out, `_shipped_reads.ACCEPTED_GUARDS` holding `GUARD_DECORATOR` alone and
        rejecting `GUARD_SETUPCLASS_SKIP`. Burying the same shape one level deeper, inside a
        derivation helper, would be less visible and no more honest;
      * a returned sentinel is only as good as the caller's memory to check it, and the caller
        forgetting is the exact defect below. An empty `frozenset` is worse still — the helper's
        own docstring warns that an empty read "derives RED for every path".

    So: a NAMED failure a caller may catch deliberately, and which an unguarded caller cannot turn
    into a green by forgetting.
    """


def _mirror_excluded_prefixes(root):
    """The literal path prefixes `sync-public.sh` keeps out of the mirror (`docs/build/`,
    `docs/talks/`, …). Glob entries are ignored here: a glob cannot be turned into a prefix
    without guessing, and the ones this boundary uses (`*.pyc`, `/mokata-*/`) name nothing under
    `docs/`.

    ⚠ THE HELPER'S CONTRACT, HONOURED HERE (GATE-COUNT-TRUTH-FU). `read_script` returns **None**
    when the script cannot be opened, and says why in its own docstring: *"so an unreadable script
    becomes UNDECIDABLE rather than a crash or, worse, an empty read that derives RED for every
    path."* As first written this function passed that None straight into `exclude_entries`, which
    dereferences it — the helper kept its side of the contract and the caller broke it, and the
    friendly `AssertionError` below was unreachable dead code behind an `AttributeError`.

    That matters because `scripts/sync-public.sh` is itself excluded from the public mirror: on the
    tree users clone the script is simply absent, and this read was 8 errors there while the dev
    tree stayed green. The corpus-reading test classes carry the mirror decorator so they never
    reach here on the mirror; this raise is what makes a NEW unguarded caller loud instead of
    cryptic."""
    script = _mirror_bookkeeping.read_script(root)
    if script is None:
        raise MirrorScriptAbsent(
            "scripts/sync-public.sh is not in this tree, so the public corpus cannot be derived "
            "and no file can be graded. The script is excluded from the public mirror, so this is "
            "the EXPECTED state there — a test that reaches this needs the class decorator\n"
            "    @unittest.skipUnless(os.path.exists(SYNC_SH),\n"
            "                         \"sync-public.sh is dev-only, excluded from the public "
            "mirror\")\n"
            "(the shape `_shipped_reads.ACCEPTED_GUARDS` accepts), never a setUpClass skip. "
            "Looked in: %s" % os.path.join(root, "scripts", "sync-public.sh")
        )
    prefixes = set()
    for entry in _mirror_bookkeeping.exclude_entries(script):
        entry = entry.strip("/")
        if not entry or any(ch in entry for ch in "*?["):
            continue
        prefixes.add(entry)
    if not prefixes:
        raise AssertionError(
            "scripts/sync-public.sh yielded NO literal --exclude entries — the public corpus "
            "cannot be derived, and an underived corpus would silently grade the wrong files."
        )
    return frozenset(prefixes)


# ---- 3 · reading a claim out of prose -----------------------------------------------------------

_TAG = re.compile(r"<[^>]+>")
_EMPHASIS = re.compile(r"[*`_]+")

_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def normalise(line):
    """Flatten one line so a claim reads the same in Markdown and in HTML: tags become spaces (so
    `<span>9</span><span>backed gates` does not weld into `9backed`), emphasis characters vanish
    (so `**10 *backed* gates**` reads as `10 backed gates`)."""
    return _EMPHASIS.sub("", _TAG.sub(" ", line))


def _number(token):
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _WORD_NUMBERS.get(token)


_NUM = r"(\d+|[A-Za-z]+)"

#: fact key -> the patterns that state it. One group per pattern, holding the number as written.
CLAIM_PATTERNS = (
    # "26 Agent Skills" · "26 skills in total" · "26 skills ship" — but NOT "the two skills
    # layers", which is a noun phrase and not a count of anything.
    ("skills", re.compile(
        _NUM + r"\s+(?:Agent )?skills\b(?!\s*(?:layer|reference|page))", re.I)),
    ("curated_skills", re.compile(_NUM + r"\s+(?:curated|pipeline/capability skills)", re.I)),
    ("domain_skills", re.compile(_NUM + r"\s+domain(?:[- ]knowledge)?(?:\s+skills)?\b", re.I)),
    ("slash_commands", re.compile(_NUM + r"\s+slash commands", re.I)),
    ("cli_subcommands", re.compile(_NUM + r"\s+CLI subcommands", re.I)),
    ("mcp_tools", re.compile(_NUM + r"\s+(?:MCP )?tools\b", re.I)),
    ("backed_gates", re.compile(_NUM + r"\s+backed gates", re.I)),
    ("run_state_gates", re.compile(_NUM + r"\s+run-state gates", re.I)),
    ("run_state_gates", re.compile(r"[Aa]head of all\s+" + _NUM, re.I)),
    ("hook_enforced", re.compile(_NUM + r"\s+of those gates are enforced by a hook", re.I)),
    ("hook_enforced", re.compile(r"the hook stops\s+" + _NUM + r"\s+things", re.I)),
    ("hook_enforced", re.compile(_NUM + r"\s+in the hook", re.I)),
    ("hook_enforced", re.compile(_NUM + r"\s+enforced on native writes", re.I)),
    ("runtime_dependencies", re.compile(_NUM + r"\s+runtime dependen(?:cy|cies)", re.I)),
)

#: Claims that only mean anything as a TUPLE. The MCP split is never written as a lone "20 write"
#: — reading one leg on its own turns "it licenses exactly one write, expires in 15 minutes" into
#: a claim about `registry.TOOLS`, which is how a guard earns the reputation that gets it skipped.
SPLIT_PATTERNS = (
    # `[^0-9]*?` between the legs, not `[·,]\s*`: the split is written three ways in the tree —
    # "40 read · 20 write · 1 approve", "40 read, 20 write and 1 opt-in approve", and with a
    # parenthetical listing example tools between the first two legs. Forbidding digits in the
    # gaps keeps it from ever bridging to an unrelated number.
    (("mcp_read", "mcp_write", "mcp_approve"), re.compile(
        r"(\d+)\s+read\b[^0-9]*?(\d+)\s+write\b[^0-9]*?(\d+)\s+(?:opt-in\s+)?approve", re.I)),
)

#: `mcp_tools` is the one anchor loose enough to catch an unrelated "N tools". It only counts on a
#: line that is talking about the MCP surface.
_ANCHOR_REQUIRES = {"mcp_tools": re.compile(r"MCP", re.I)}

#: A "16 skills" inside a sentence about the CURATED catalog is a claim about the curated 16, not
#: a stale total. Re-key it rather than exempt it — an exemption stops grading the number.
_CURATED_CONTEXT = re.compile(r"(?:curated|catalog)", re.I)
_CONTEXT_WINDOW = 48


class Claim:
    __slots__ = ("key", "stated", "line_no", "text", "token")

    def __init__(self, key, stated, line_no, text, token=""):
        self.key = key
        self.stated = stated
        self.line_no = line_no
        self.text = text
        #: the number exactly as the page spelled it — "5" or "Five". Kept because a word-number
        #: that stops resolving is DROPPED rather than failed, so the tests need to see which
        #: spellings the corpus actually leans on.
        self.token = token

    def __repr__(self):  # pragma: no cover - diagnostics only
        return "Claim(%s=%r L%s)" % (self.key, self.stated, self.line_no)


def word_numbers_in_use(text):
    """The English number-words this text states a public count with, lowercased."""
    return {c.token.lower() for c in claims(text) if not c.token.isdigit()}


def paragraphs(text):
    """Blank-line-delimited blocks, each normalised and joined into ONE string, with a map back
    to line numbers.

    Claims are matched over the block rather than the line because prose WRAPS. This page's own
    repaired paragraph wrapped as `there are **9 backed` / `gates**`, and a line-at-a-time reader
    saw no claim at all — the guard would have shipped green over the very sentence it was built
    to grade, on the very page the stage exists to fix. Yields `(joined, offsets)` where `offsets`
    is `[(char_offset, line_no), …]`."""
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        parts, offsets, position = [], [], 0
        while index < len(lines) and lines[index].strip():
            piece = normalise(lines[index])
            offsets.append((position, index + 1))
            parts.append(piece)
            position += len(piece) + 1
            index += 1
        yield " ".join(parts), offsets


def _line_of(offset, offsets):
    line_no = offsets[0][1]
    for start, candidate in offsets:
        if start > offset:
            break
        line_no = candidate
    return line_no


def claims(text):
    """Every public number a body of text states. Pure: text in, claims out."""
    found = []
    for line, offsets in paragraphs(text):
        for key, pattern in CLAIM_PATTERNS:
            required = _ANCHOR_REQUIRES.get(key)
            if required is not None and not required.search(line):
                continue
            for match in pattern.finditer(line):
                value = _number(match.group(1))
                if value is None:
                    continue
                resolved = key
                if key == "skills":
                    before = line[max(0, match.start() - _CONTEXT_WINDOW):match.start()]
                    if _CURATED_CONTEXT.search(before):
                        resolved = "curated_skills"
                found.append(Claim(resolved, value, _line_of(match.start(), offsets),
                                   match.group(0).strip(), match.group(1)))
        for keys, pattern in SPLIT_PATTERNS:
            for match in pattern.finditer(line):
                for key, group in zip(keys, match.groups()):
                    value = _number(group)
                    if value is None:  # pragma: no cover - split groups are \d+
                        continue
                    found.append(Claim(key, value, _line_of(match.start(), offsets),
                                       _squash(match.group(0)), group))
    return found


def _squash(text):
    """A claim spanning a wrap reads back as one line in the failure message."""
    return re.sub(r"\s+", " ", text).strip()


def grade(path, text, facts):
    """Every stated number in `text` that disagrees with the derived fact. Returns messages; an
    empty list means the file agrees with the code."""
    findings = []
    for claim in claims(text):
        fact = facts.get(claim.key)
        if fact is None:  # pragma: no cover - CLAIM_PATTERNS keys are all derived facts
            continue
        if claim.stated != fact.value:
            findings.append(
                "%s:%d states %s = %s, the code derives %s.\n"
                "    claim   : %r\n"
                "    source  : %s\n"
                "    re-derive: %s"
                % (path, claim.line_no, claim.key, claim.stated, fact.value,
                   claim.text, fact.source, REDERIVE_CMD)
            )
    return findings


# ---- 4 · membership: the assertion a count would have been green through ------------------------

#: A roster is fenced by these so the guard grades a SET rather than guessing where an enumeration
#: begins. Both forms are comments in Markdown and in HTML, so one marker serves both surfaces.
ROSTER_OPEN = re.compile(r"<!--\s*mokata:gates\s+(backed|run-state)\s*-->")
ROSTER_CLOSE = re.compile(r"<!--\s*/mokata:gates\s*-->")

#: fact key each roster kind is graded against.
ROSTER_FACT = {"backed": "backed_gates", "run-state": "run_state_gates"}


class Roster:
    __slots__ = ("kind", "line_no", "ids")

    def __init__(self, kind, line_no, ids):
        self.kind = kind
        self.line_no = line_no
        self.ids = frozenset(ids)


def rosters(text, vocabulary):
    """The marked gate rosters in `text`, each as the set of gate ids named inside it."""
    found = []
    kind = None
    start = 0
    body = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        if kind is None:
            match = ROSTER_OPEN.search(raw)
            if match is not None:
                kind, start, body = match.group(1), line_no, []
            continue
        if ROSTER_CLOSE.search(raw):
            found.append(Roster(kind, start, _ids_in("\n".join(body), vocabulary)))
            kind = None
            continue
        body.append(raw)
    if kind is not None:
        found.append(Roster(kind, start, _ids_in("\n".join(body), vocabulary)))
    return found


def _ids_in(text, vocabulary):
    words = set(re.findall(r"[a-z][a-z-]*[a-z]", normalise(text).lower()))
    return words & set(vocabulary)


def grade_rosters(path, text, facts, vocabulary):
    """Each marked roster must name EXACTLY the derived set. Doc 85 §4: the backed count moved
    9 → 10 → 9 on two corrections that cancelled, so the set is the assertion and the count is a
    consequence of it."""
    findings = []
    for roster in rosters(text, vocabulary):
        fact = facts[ROSTER_FACT[roster.kind]]
        if roster.ids == fact.members:
            continue
        findings.append(
            "%s:%d roster '%s' names the wrong set of gates.\n"
            "    stated but not %s : %s\n"
            "    %s but not stated : %s\n"
            "    source  : %s\n"
            "    re-derive: %s"
            % (path, roster.line_no, roster.kind, roster.kind,
               ", ".join(sorted(roster.ids - fact.members)) or "(none)",
               roster.kind.capitalize(),
               ", ".join(sorted(fact.members - roster.ids)) or "(none)",
               fact.source, REDERIVE_CMD)
        )
    return findings


#: An enumeration is a line that both claims backing AND names several gates — the exact shape of
#: the defect this stage repaired ("There are 10 backed gates — `write-gate`, `secret-guard`, …").
_BACKED_ANCHOR = re.compile(r"backed\b", re.I)
_ENUMERATION_FLOOR = 2


def unmarked_rosters(path, text, vocabulary):
    """Lines that enumerate gates as backed OUTSIDE any marker — a roster the membership check
    would never see. Without this, the membership assertion silently means "the rosters somebody
    remembered to mark" (doc 85 §7j)."""
    marked = set()
    inside = False
    for line_no, raw in enumerate(text.splitlines(), start=1):
        if ROSTER_OPEN.search(raw):
            inside = True
        if inside:
            marked.add(line_no)
        if ROSTER_CLOSE.search(raw):
            inside = False

    findings = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        if line_no in marked:
            continue
        line = normalise(raw)
        if not _BACKED_ANCHOR.search(line):
            continue
        named = _ids_in(line, vocabulary)
        if len(named) < _ENUMERATION_FLOOR:
            continue
        findings.append(
            "%s:%d enumerates gates as backed outside a <!-- mokata:gates backed --> marker, so "
            "no membership check reads it.\n"
            "    names   : %s\n"
            "    fix     : wrap the roster in the marker, or state the gates without enumerating "
            "them.\n"
            "    re-derive: %s"
            % (path, line_no, ", ".join(sorted(named)), REDERIVE_CMD)
        )
    return findings
