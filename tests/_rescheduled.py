"""The "Re-scheduled" section rule, as a PURE FUNCTION over supplied text (doc 85 §7i).

0.0.19's own exit criterion: *the notes carry a "Re-scheduled" section naming the Class-C items
that DID slip, what release each was promised for, where it now lands and why — and the PostgreSQL
floor is NOT in it, with its absence part of the criterion.*

⛔ THE HARD HALF IS THE OMISSION, and it is why this rule needs a corpus it did not read. A missing
bullet leaves no text behind: delete the FTS item and you delete its promise with it, so a check
that reads only the section sees a smaller, entirely consistent section. The independent record of
what slipped is `disclosure.PUBLISHED_COMMITMENTS` — declared, dated, and graded in both directions
by `test_b5_a_disclosed_entry_moved_and_a_held_entry_did_not`. Of the commitments published FOR the
release being cut, the ones that MOVED must be named among the section's bullets and the ones that
HELD must not.

⚠ BULLETS, NOT THE WHOLE SECTION. The absence half is about the ITEM LIST: prose explaining why the
floor is deliberately absent is the criterion being met out loud, and a rule that read the whole
section would convict the sentence that states its own compliance.

Pure/offline: every function is a function of the text it is handed. Nothing here walks a tree.
"""


def section_lines(text, heading):
    """The lines under a `### heading` / `## heading` up to the next heading of that depth or above.

    Returns `None` when the heading is absent, which is a different fact from an empty section and
    is reported as one — a section that is missing and a section that lists nothing have different
    remedies (doc 85 §7g).
    """
    lines = text.splitlines()
    start = depth = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") and stripped.lstrip("#").strip().lower() == heading.lower():
            depth = len(stripped) - len(stripped.lstrip("#"))
            start = i + 1
            break
    if start is None:
        return None
    out = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            if len(stripped) - len(stripped.lstrip("#")) <= depth:
                break
        out.append(line)
    return out


def section_items(lines):
    """The section's bullet ITEMS, each joined with its indented continuation lines."""
    items, current = [], None
    for line in lines or []:
        if line.lstrip().startswith(("- ", "* ")) and not line.startswith("  "):
            if current is not None:
                items.append(" ".join(current))
            current = [line.strip()]
        elif current is not None and line.strip():
            current.append(line.strip())
        elif current is not None:
            items.append(" ".join(current))
            current = None
    if current is not None:
        items.append(" ".join(current))
    return items


def rescheduled_faults(text, commitments, cutting, heading="Re-scheduled"):
    """`[fault, …]` — empty when the section satisfies the criterion for `cutting`.

    `commitments` is any sequence of `PublishedCommitment`. Only those PUBLISHED FOR the release
    being cut are consulted: a commitment naming a later slot is a promise this cut is MAKING, not
    one it has to account for, and letting those into the held-set would convict the section for
    listing the very items it re-schedules.
    """
    faults = []
    lines = section_lines(text, heading)
    if lines is None:
        return ["there is no `%s` section at all" % heading]
    items = section_items(lines)
    if not items:
        faults.append("the `%s` section lists no items" % heading)

    published = [c for c in commitments if c.promised_for == cutting]
    moved = sorted({c.subject for c in published if c.moved})
    held = sorted({c.subject for c in published if not c.moved})
    for subject in moved:
        if not subject:
            faults.append("a commitment that slipped carries no subject, so its omission from the "
                          "section cannot be detected")
        elif not any(subject.lower() in item.lower() for item in items):
            faults.append("%r slipped and is named in no item of the `%s` section"
                          % (subject, heading))
    for subject in held:
        if subject and any(subject.lower() in item.lower() for item in items):
            faults.append("%r did NOT move and must not be listed as re-scheduled" % subject)
    return faults
