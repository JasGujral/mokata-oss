"""The shipped `ux.notify` documentation is held to what the notifier ACTUALLY DOES, per platform.

0.0.20, `MANIFEST-UX-NOTIFY-ROW-OVERPROMISES-ON-WINDOWS` (doc 84, filed at 0.0.19 stage 09a). The
row: *"`docs/reference/manifest.md` documents `ux.notify` as defaulting `true` with **no Windows
caveat**"* — while Windows ships **no visual arm at all**, because a toast needs a PowerShell script
string, which `notify.py`'s rule 1 forbids. The row's own closing line is why this module exists:

> ⛔ *"The gate must not let this ship uncaveated: it is the exact defect B5 grades, in a document
> rather than a constant."*

⭐ **THE CAVEAT IS PRESENT TODAY AND NOTHING GRADES IT.** `test_f13_windows_audio_arm` grades the
CODE and the shipped refusal NOTICE; **no test reads `docs/reference/manifest.md` at all.** So the
row is closeable on evidence and the evidence can silently rot — which is the same shape as the
constant B5 grades, one artefact over.

DERIVED BY CALLING THE DISPATCHERS, NOT BY READING THEM (§7e)
--------------------------------------------------------------
Which platforms have an arm is answered by **invoking `notify._visual_argv` / `notify._audio_argv`
for each platform string** and seeing what comes back. ⛔ Not by parsing the `if platform ==` chain:
a source read would still agree with itself after the dispatch changed shape, and the whole failure
mode here is prose agreeing with a stale reading of the code.

⚠ THE PROBE SET IS DECLARED, NOT DISCOVERED (§7j)
---------------------------------------------------
`sys.platform` is an open set — a caller could hand this `"freebsd12"` — so the probe cannot
enumerate every platform mokata might run on. It probes the three the project ships for and SAYS SO.
`unnamed_platforms` returns the ones a doc mentions that the probe never asked about, so a widening
of the docs is visible rather than silent.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import re


#: The platforms this project ships for, each with the name the SHIPPED DOCS use for it.
#: ⛔ A declared vocabulary, and it is graded: `docs_name_every_probed_platform` requires each of
#: these names to appear in the documentation, so a rename in the docs cannot make this table's
#: entries quietly unmatchable — which would turn every check below vacuously green.
PROBED_PLATFORMS = (
    ("darwin", "macOS"),
    ("linux", "Linux"),
    ("win32", "Windows"),
)

#: The settings rows this module grades, by the key the table's first column carries.
VISUAL_KEY = "ux.notify"
AUDIO_KEY = "ux.notify_audio"

#: Phrases that make an audio row honest about what a successful call proves. ANY of them satisfies
#: it — this is a presence check over a small vocabulary, which is weaker than the visual property
#: below, and the weakness is stated rather than hidden: prose cannot be derived, only bounded.
AUDIBILITY_QUALIFIERS = (
    "not that anything was audible",
    "queued with the OS",
    "reports nothing back",
)


def _row(text, key):
    """The markdown table row whose first cell is `` `key` ``, or None."""
    pattern = re.compile(r"^\|\s*`%s`\s*\|.*$" % re.escape(key), re.M)
    m = pattern.search(text or "")
    return m.group(0) if m else None


def documented_rows(text):
    """`{key: row-text}` for the rows this module grades. A missing row is ABSENT, not empty."""
    return {key: _row(text, key) for key in (VISUAL_KEY, AUDIO_KEY)}


def probe_body(notify_module):
    """A body the notifier will actually BUILD AN ARGV AROUND.

    ⛔ NOT A STRING OF THIS MODULE'S CHOOSING, and the first cut of this file learned why the hard
    way. `notify._require_fixed` refuses any body outside the frozen `BODIES` set — rule 2, the
    whole reason that module can promise it never interpolates a command — so probing with
    `"a body"` made EVERY platform report *no visual arm*, including macOS. Paired with a second
    defect in the denial reader, that produced a **GREEN verdict with an empty derived set**: the
    exact vacuity this file exists to catch, inside the file itself.
    """
    return sorted(notify_module.BODIES)[0]


#: The dispatcher this module probes. Named here so a rename in `notify.py` is a LOOKUP FAILURE
#: rather than a silent "no platform has an arm" — see `platforms_with_visual_arm`.
VISUAL_DISPATCHER = "_notification_argv"


def platforms_with_visual_arm(notify_module, body=None):
    """The probed platforms for which the notifier produces a visual command. CALLED, not read.

    ⛔ NOTHING IS SWALLOWED HERE, and the first cut of this file is why. It probed a function name
    that does not exist (`_visual_argv`; the real one is `_notification_argv`) inside a
    `except Exception: continue`, so an AttributeError became *"this platform has no arm"* — for
    every platform. The derived set came back EMPTY and the whole grade went vacuously GREEN.
    **A probe that cannot tell an ABSENT ARM from an ABSENT FUNCTION is §7g in the instrument**,
    and it is the same defect this file exists to catch, one layer in.
    """
    body = probe_body(notify_module) if body is None else body
    dispatch = getattr(notify_module, VISUAL_DISPATCHER, None)
    if dispatch is None:
        raise AttributeError(
            "notify.%s is gone — this probe grades nothing until it is re-pointed, and reporting "
            "'no platform has a visual arm' would be a false GREEN" % VISUAL_DISPATCHER)
    return tuple(tag for tag, _name in PROBED_PLATFORMS if dispatch(tag, body) is not None)


def platforms_without_visual_arm(notify_module, body=None):
    have = set(platforms_with_visual_arm(notify_module, body))
    return tuple(tag for tag, _name in PROBED_PLATFORMS if tag not in have)


def name_of(tag):
    return dict(PROBED_PLATFORMS)[tag]


class DocVerdict(object):
    """What the shipped table says, measured against what the arms do."""

    __slots__ = ("row_missing", "unnamed_exclusions", "false_exclusions", "audio_overpromises",
                 "no_coverage_claim")

    def __init__(self, row_missing=(), unnamed_exclusions=(), false_exclusions=(),
                 audio_overpromises=False, no_coverage_claim=False):
        self.row_missing = tuple(row_missing)
        self.unnamed_exclusions = tuple(unnamed_exclusions)
        self.false_exclusions = tuple(false_exclusions)
        self.audio_overpromises = audio_overpromises
        self.no_coverage_claim = no_coverage_claim

    @property
    def ok(self):
        return not (self.row_missing or self.unnamed_exclusions or self.false_exclusions
                    or self.audio_overpromises or self.no_coverage_claim)

    def render(self):
        if self.ok:
            return "GREEN — the shipped ux.notify rows match what the notifier does on every probed platform."
        lines = ["RED — the shipped documentation and the notifier disagree."]
        if self.no_coverage_claim:
            lines.append("  NO COVERAGE CLAIM: the `%s` row never says WHICH platforms the visual "
                         "arm works on, so every platform is silently implied — including the one "
                         "that ships no arm at all. This is the filed defect verbatim."
                         % VISUAL_KEY)
        for key in self.row_missing:
            lines.append("  NO ROW for `%s` — the setting is documented nowhere, which is not the "
                         "same as documented wrongly." % key)
        for tag in self.unnamed_exclusions:
            lines.append("  OVERPROMISE: the `%s` row says the visual arm covers %s, and the "
                         "notifier produces NO visual command there. A user on %s reads 'notify "
                         "you when it is YOUR move' and gets nothing they can see."
                         % (VISUAL_KEY, name_of(tag), name_of(tag)))
        for tag in self.false_exclusions:
            lines.append("  UNDERSTATED: the notifier DOES produce a visual command on %s, and the "
                         "`%s` row's coverage claim leaves it out."
                         % (name_of(tag), VISUAL_KEY))
        if self.audio_overpromises:
            lines.append("  OVERPROMISE: the `%s` row claims a sound without saying that a "
                         "successful call means it was QUEUED, not heard. No platform's audio arm "
                         "can report audibility." % AUDIO_KEY)
        return "\n".join(lines)


#: The clause in which a row states WHICH platforms the visual arm covers — an "only" enumeration.
#: ⛔ READ POSITIVELY, NOT AS A LIST OF DENIALS, and the first cut of this file got that wrong.
#: It looked for denial phrases and treated *"macOS and Linux only"* as denying macOS, because the
#: phrase and the name were both present in the row. Reading the ENUMERATION instead has no such
#: ambiguity and grades BOTH directions from one derivation: a platform missing from it that has no
#: arm is an OVERPROMISE, and one present in it that has no arm is an UNDERPROMISE.
#: ⚠ THE CLAUSE STOPS AT `;` AND `:` AS WELL AS `.` AND `|`, and that is not punctuation
#: pedantry. The shipped row reads *"…is macOS and Linux only: mokata raises it with
#: osascript (macOS) or notify-send (Linux). Windows ships no visual arm"* — a clause that
#: ran past the colon would swallow "Windows" out of the very sentence that EXCLUDES it and
#: report the row as claiming coverage it explicitly denies.
_ONLY_CLAUSE = re.compile(r"[^.|;:]*\bonly\b[^.|;:]*")


def covered_platforms(row):
    """(names, found) — the platforms a row's `only` clause says the visual arm covers.

    `found` is False when the row makes no such claim at all, which is a different fact from a
    claim that covers nothing (§7g) — and it is the ORIGINAL defect: a row that promises
    notification and never says where it works.
    """
    m = _ONLY_CLAUSE.search(row or "")
    if m is None:
        return (), False
    clause = m.group(0).lower()
    return tuple(tag for tag, name in PROBED_PLATFORMS if name.lower() in clause), True


def grade(text, notify_module, body=None):
    """Grade the shipped table against the notifier's real per-platform behaviour."""
    rows = documented_rows(text)
    missing = tuple(k for k, v in rows.items() if v is None)
    if missing:
        return DocVerdict(row_missing=missing)

    visual_row = rows[VISUAL_KEY]
    with_arm = set(platforms_with_visual_arm(notify_module, body))
    claimed, found = covered_platforms(visual_row)
    if not found:
        # ⭐ THE ORIGINAL DEFECT, EXACTLY: the row promises notification and never says WHERE it
        # works, so every platform is silently implied — including the one that ships no arm.
        return DocVerdict(no_coverage_claim=True,
                          audio_overpromises=not any(q.lower() in rows[AUDIO_KEY].lower()
                                                     for q in AUDIBILITY_QUALIFIERS))
    claimed = set(claimed)
    unnamed = tuple(t for t, _n in PROBED_PLATFORMS if t in claimed and t not in with_arm)
    false_ex = tuple(t for t, _n in PROBED_PLATFORMS if t in with_arm and t not in claimed)

    audio_row = rows[AUDIO_KEY].lower()
    overpromises = not any(q.lower() in audio_row for q in AUDIBILITY_QUALIFIERS)
    return DocVerdict((), unnamed, false_ex, overpromises)


def unnamed_platforms(text):
    """Probed platform names the documented rows never mention — the vocabulary's live half."""
    rows = documented_rows(text)
    blob = " ".join(v for v in rows.values() if v).lower()
    return tuple(name for _tag, name in PROBED_PLATFORMS if name.lower() not in blob)
