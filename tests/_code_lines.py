"""ONE comment-stripping reader for the test tree (0.0.22 B2, `A-SENTENCE-ABOUT-A-COMMAND-IS-NOT-A-COMMAND`).

The rule (doc 85): a pin that reads a script, a workflow or a module must not be satisfied by the
PROSE describing the thing it pins. Every shell script and workflow here explains its controls in
`#` comments that name the very literals under pin, so a raw substring check passes on the comment
after the control itself is gone. Seven private copies of this rule existed (stage 00 counted them:
`_release_retry`, `_mirror_bookkeeping`, `_preflight_parity`, `test_sync_public_nested_checkout`,
`test_s12_release_publisher`, `test_stage68_supply_chain`, `test_tm_s12a_branch_protection`) —
`TWO-DECLARATIONS-OF-ONE-DECISION` five times over. This is the one implementation.

WHOLE-LINE comments only, deliberately: a `#` inside a shell string (`echo "… # …"`) is not a
comment, and a naive split would truncate the very `echo` lines several pins inspect.
"""


def is_comment(line):
    """A whole-line `#` comment: the only line shape every reader here agrees to drop."""
    return line.lstrip().startswith("#")


def code_lines(text):
    """`[(lineno, line)]` — 1-based line numbers of the ORIGINAL text, comment lines dropped."""
    return [(n, ln) for n, ln in enumerate((text or "").splitlines(), 1) if not is_comment(ln)]


def code_text(text):
    """`text` with every whole-line comment dropped, joined back with newlines."""
    return "\n".join(ln for _n, ln in code_lines(text))


def code_lines_blanked(text):
    """Every line of `text`, a comment line replaced by `""` so a line's index stays its line."""
    return ["" if is_comment(ln) else ln for ln in (text or "").splitlines()]


def code_line_texts(text):
    """The non-comment lines of `text`, as a list of strings (no line numbers)."""
    return [ln for _n, ln in code_lines(text)]
