"""RUN-REG-ENFORCED-BY-PROSE-ALONE — the run-registration step, graded as REQUIREMENTS.

WHAT THIS EXISTS FOR (doc 84, OSS #28). `brainstorm`'s step 1 is an INSTRUCTION and nothing else:
"before you ask the first question, call `session_save` with `register` = true". Everything
downstream binds on the checkpoint that call writes — `progress` finds the run, `spec` has
something to attach to, and `gate_hook.py`'s phase gate has state to bind on. When the instruction
is not followed the gate does not fail; it answers `"no active mokata run — not policed"` and lets
the write through, which is the correct answer to the question it was asked. So a VANISHED
instruction and a FOLLOWED one are today indistinguishable at the gate, and nothing anywhere pins
the sentence that carries the whole mechanism.

THIS MODULE PINS ONLY THAT THE INSTRUCTION CANNOT SILENTLY VANISH. It does not make registration
structural and it is not a step toward doing so: firing the registration from a non-prose surface
(SessionStart, say) collides with the gate's POSITIVE-TRIGGER contract — a run registered for every
window would put ordinary hand-editing in a mokata repo under house arrest, which is the exact
outcome the `not policed` floor exists to prevent. That was triaged and ruled out; this is the
cheap half.

WHY REQUIREMENTS AND NOT A STRING (doc 85 §7g). "The instruction is ABSENT" and "the instruction
was REWORDED" must not share a representation. A pin keyed on one exact sentence reds on a
legitimate copy-edit, and a pin that reds on copy-edits is one a future author weakens rather than
obeys. So four requirements are graded independently and each failure NAMES what went missing:

    REQ_SECTION            the step exists at all
    REQ_TOOL               it names the `session_save` tool
    REQ_REGISTER_TRUE      it requires `register` be set TRUE, not merely mentioned
    REQ_ORDER              it is positioned BEFORE the first-question instruction

A fifth outcome is NOT a requirement and is deliberately not folded into one: REQ_ORDER is
relative to an anchor, and a document with no question-asking instruction left in it gives the
ordering check nothing to be relative to. That renders UNGRADABLE with its reason, never a
default-green — the two-meanings defect ("no anchor found" silently meaning "order is fine") is the
one 0.0.17 spent itself closing.

PURE FUNCTION OVER SUPPLIED TEXT (doc 85 §7i). The shipped prose is CORRECT today, so a checker
that reads the tree grades nothing from the moment it is written. Every entry point here takes the
document text as an argument and touches no filesystem, so the test can hand it synthetic
offenders — the section deleted, `register = true` downgraded to a mention, the section moved below
the first question — and watch each one red.
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

# ---- the four requirements. The id is what a failure REPORTS, so it is the user-facing name.
REQ_SECTION = "registration-step-present"
REQ_TOOL = "names-the-session_save-tool"
REQ_REGISTER_TRUE = "requires-register-true"
REQ_ORDER = "positioned-before-the-first-question"

REQUIREMENTS = (REQ_SECTION, REQ_TOOL, REQ_REGISTER_TRUE, REQ_ORDER)

# The tool the step must name. An API identifier, not prose — a copy-edit does not rename it, which
# is what makes it a safe anchor where a sentence would not be.
TOOL_NAME = "session_save"

# The registration step is located by the `regist-` STEM, not by its heading text. "First: register
# the run", "Register the run first", "Step 0 — registering the run" all carry it; that is the
# tolerance §7g asks for. What it does NOT tolerate is the step losing the word entirely, and that
# is deliberate: a step that no longer says it registers anything is not a copy-edit.
_REGIST = re.compile(r"\bregist\w*", re.IGNORECASE)

# `register` must be SET, not name-dropped. Accepts `register` = true / register: True /
# register is true / set `register` to true, all after backtick-stripping and space-collapsing.
_REGISTER_TRUE = re.compile(r"\bregister\b\s*(?:=|:|is|set\s+to|to)\s*true\b", re.IGNORECASE)

# The ordering ANCHOR: an instruction to ask the user a question. Bounded to a single clause (no
# sentence-end, no newline between the verb and its object) so a red-flags table row like
# "I'll ask everything up front to save time." | One question at a time." cannot pose as one.
_ASK_QUESTION = re.compile(r"\bask\b[^.\n]{0,40}?\bquestions?\b", re.IGNORECASE)

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Section:
    """One markdown section: its heading, its body, and WHERE it starts in the source text.

    `start` is a character offset into the document that was parsed, which is what makes the
    ordering requirement a comparison rather than a guess about document structure."""
    heading: str
    body: str
    start: int
    end: int


@dataclass(frozen=True)
class ProseReport:
    """The verdict on one document. `missing` and `ungradable` each carry a REASON per entry."""
    missing: Tuple[Tuple[str, str], ...] = ()
    ungradable: Tuple[Tuple[str, str], ...] = ()
    basis: str = ""

    @property
    def ok(self) -> bool:
        """GREEN only when every requirement was both GRADED and MET. An ungradable requirement is
        not a pass — it is the checker saying it could not answer, which is a different claim."""
        return not self.missing and not self.ungradable

    @property
    def missing_ids(self) -> Tuple[str, ...]:
        return tuple(rid for rid, _reason in self.missing)

    @property
    def ungradable_ids(self) -> Tuple[str, ...]:
        return tuple(rid for rid, _reason in self.ungradable)

    def describe(self) -> str:
        """The failure as a human reads it — one line per requirement, naming what vanished."""
        lines = []
        for rid, reason in self.missing:
            lines.append(f"  MISSING  {rid}: {reason}")
        for rid, reason in self.ungradable:
            lines.append(f"  UNGRADABLE  {rid}: {reason}")
        return "\n".join(lines) or "  (every requirement met)"


def _strip_ticks(text: str) -> str:
    return text.replace("`", "")


def _normalise(text: str) -> str:
    """Backticks dropped and whitespace collapsed, so `register` = true and ``register``=True and a
    line-wrapped "register\\n= true" are the same claim to the matcher. Markdown emphasis is
    presentation; the requirement is about what the sentence SAYS."""
    return re.sub(r"\s+", " ", _strip_ticks(text))


def parse_sections(text: str) -> List[Section]:
    """Split a markdown document into sections at ATX headings, fenced code excluded.

    Content before the first heading becomes a leading section with an empty heading, so an
    instruction that has LOST its heading is still findable in a body scan rather than silently
    disappearing along with the `##` line."""
    sections: List[Section] = []
    heading = ""
    body_start = 0
    offset = 0
    in_fence = False
    for line in text.splitlines(keepends=True):
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            m = _HEADING.match(line.rstrip("\n"))
            if m:
                sections.append(Section(heading, text[body_start:offset], body_start, offset))
                heading = m.group(2).strip()
                body_start = offset
        offset += len(line)
    sections.append(Section(heading, text[body_start:offset], body_start, offset))
    return sections


def find_registration_section(sections: Sequence[Section]) -> Tuple[Optional[Section], str]:
    """Locate the run-registration step, and SAY HOW it was found.

    Two bases, tried in order, because "the step is gone" and "the step lost its heading" are
    different facts and the caller is entitled to tell them apart:

      "heading"  a section whose HEADING carries the regist- stem. What ships today.
      "body"     no such heading, but a section BODY registers the run and names the tool. The
                 step survives, folded into another section.
      "absent"   neither. The instruction is gone.
    """
    for section in sections:
        if _REGIST.search(section.heading):
            return section, "heading"
    for section in sections:
        if _REGIST.search(section.body) and TOOL_NAME in _strip_ticks(section.body):
            return section, "body"
    return None, "absent"


def find_first_question_anchor(sections: Sequence[Section],
                               exclude: Optional[Section] = None) -> Optional[Section]:
    """The earliest section instructing the agent to ASK the user a question.

    `exclude` drops the registration section itself from the candidates: its own prose says
    "before you ask the first question", and a section that anchors on itself would satisfy the
    ordering requirement by construction — which is a pin measuring nothing."""
    for section in sections:
        if exclude is not None and section.start == exclude.start:
            continue
        if _ASK_QUESTION.search(_normalise(section.body)):
            return section
    return None


def check_registration_step(text: str) -> ProseReport:
    """Grade one SKILL.md / command-template text against the four requirements.

    Pure: `text` is the whole input. Returns a `ProseReport` whose `missing` entries name the
    requirement that failed AND why, so a red says what vanished rather than that something did."""
    sections = parse_sections(text)
    section, basis = find_registration_section(sections)

    if section is None:
        return ProseReport(
            missing=((REQ_SECTION,
                      "no section registers the run — the brainstorm protocol's opening state "
                      "write is gone from this document, so a run driven conversationally leaves "
                      "no checkpoint and the phase gate has nothing to bind on"),),
            ungradable=((REQ_TOOL, "no registration step to read the tool name out of"),
                        (REQ_REGISTER_TRUE, "no registration step to read the argument out of"),
                        (REQ_ORDER, "no registration step to position")),
            basis=basis,
        )

    missing: List[Tuple[str, str]] = []
    ungradable: List[Tuple[str, str]] = []
    body = _normalise(section.body)

    if TOOL_NAME not in body:
        missing.append((
            REQ_TOOL,
            f"the registration step no longer names the `{TOOL_NAME}` tool — an agent told to "
            f"'register the run' with no tool named has no call to make"))

    if not _REGISTER_TRUE.search(body):
        missing.append((
            REQ_REGISTER_TRUE,
            "the registration step no longer REQUIRES `register` = true — mentioning the "
            "parameter is not instructing that it be set, and an unset `register` writes no "
            "checkpoint"))

    anchor = find_first_question_anchor(sections, exclude=section)
    if anchor is None:
        ungradable.append((
            REQ_ORDER,
            "no first-question instruction remains in this document, so 'before the first "
            "question' has nothing to be relative to — the ordering is UNANSWERED, not satisfied"))
    elif section.start > anchor.start:
        missing.append((
            REQ_ORDER,
            f"the registration step is positioned AFTER the question-asking instruction "
            f"({section.heading or '<unheaded>'!r} at offset {section.start} follows "
            f"{anchor.heading or '<unheaded>'!r} at offset {anchor.start}) — a run registered "
            "after the conversation started is a run the earlier turns were invisible to"))

    return ProseReport(missing=tuple(missing), ungradable=tuple(ungradable), basis=basis)
