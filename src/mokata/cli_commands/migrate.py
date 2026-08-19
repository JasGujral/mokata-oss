"""migrate — the answer for a channel this release REMOVED. It no longer migrates anything.

★ WHAT THIS COMMAND IS NOW, AND WHY IT IS NOT GONE (0.0.18 lane D slice 4).

Every migratable channel has left. The vault migrator (migrate_channels.py, un-backticked because
it is HISTORY — the file was deleted at slice 4 and a backticked name is a citation of something
that exists) was never a framework with channels plugged into it; it WAS the vault migrator, and
it is gone, and with `vault` removed there is nothing
left for `mokata migrate <x>` to move. The obvious conclusion is to delete the command too, and it
is wrong, for the reason the whole lane exists:

**THIS IS WHERE THE NOTICES SENT PEOPLE.** Every deprecation notice mokata printed for a year ended
*"Migrate now with `mokata migrate <channel>`"*, and those notices are in shipped wheels, in
terminal scrollback and in a year of published docs. Delete the command and the user who does what
we told them to do gets `invalid choice: 'migrate'` from the TOP-LEVEL parser — argparse's word for
a TYPO — one level further up than the same defect the last three slices each had to fix. A removed
channel is not a typo, and neither is the command it was removed from.

So `migrate` survives as a REMOVAL-ANSWER SURFACE and nothing else: it accepts exactly the channels
this release removed, renders each one's own record, and exits 1 because the migration the user
asked for did not happen. The help says that in those words — it no longer advertises a channel or
a schedule, because there is neither.

⚠ THE `metavar` PROBLEM THAT BLOCKED THIS CUT (`MIGRATE-HELP-SELLS-REMOVED-CHANNELS`, doc 84 §1).
With the live set empty, the old `metavar="{%s}" % ",".join(CHANNELS)` renders a literal `{}` and
the old help still sold channels *"scheduled for removal in 0.0.18"* — in the release that removes
them. Both strings are gone rather than patched: the argument now advertises the REMOVED set, which
is what it actually accepts, so help text and behaviour are the same derivation.

⚠ THIS FILE HAND-TYPED "0.0.17" THREE TIMES, IN `--help` TEXT, AND SURVIVED THE STAGE THAT EXISTED
TO END THAT (0.0.18 stage 9 corrected eleven surfaces; this was a twelfth). It survived because
stage 9's class guard, `_deprecation_removal.notice_pins`, ranges over TEST files — the axis it
does not range over is `src/` (doc 85 §7j). `src_release_pins` now covers this corpus, and nothing
below types a release at all.
"""
from __future__ import annotations

import argparse
import os
import sys

from ._common import _load_surface
from .. import deprecation
from ..deprecation import REMOVED_CHANNELS


def _present(channel: str, root: str) -> bool:
    """Does this repo actually still hold anything in `channel`'s removed location?

    The probe lives HERE, not in `deprecation`, and that is load-bearing: nothing in that module
    opens or lists a path (its own suite grades the module's AST for it), because a removal record
    that learned to read is one refactor away from being the legacy reader the record replaced.

    A file channel's location may be a FILE (`memory-share.json`) or a DIRECTORY (the vault's
    session-bundle store), so "present" is `exists` for the first and "holds a bundle" for the
    second — an empty directory left behind by a `rm` is not data, and telling a user their bundles
    are "still at" an empty path would be the same false comfort in the other direction."""
    path = deprecation.removed_file_path(channel, root)
    if os.path.isdir(path):
        from ..session_transport import removed_bundle_tags
        return bool(removed_bundle_tags(channel, root))
    return os.path.exists(path)


def cmd_migrate(args: argparse.Namespace) -> int:
    """Render the removal record for the named channel. EXIT 1 IN EVERY CASE.

    The data-present/absent split changes the MESSAGE, never the code: the migration the user asked
    for did not happen either way, and an exit status that said otherwise would be the §7g collapse
    one layer down. `choices` has already refused anything that is not a removed channel."""
    surface = _load_surface(args.path)
    channel = args.channel
    present = deprecation.is_removed_file_channel(channel) and _present(channel, surface.root)
    print(deprecation.removal_answer(channel, surface.root, present), file=sys.stderr)
    return 1


def register(sub, common):
    p = sub.add_parser(
        "migrate", parents=[common],
        help=("what happened to a channel this release removed (it no longer migrates anything — "
              "each channel's record names where your data is and how to bring it across)"),
        description=(
            "mokata no longer migrates: every deprecated channel that had a migration has been "
            "removed. This command survives to ANSWER the command the deprecation notices told "
            "you to run — it names what was removed, where your data still is, and the one way "
            "to bring it across. It writes nothing and always exits 1."))
    # ⚠ DERIVED FROM THE REMOVED REGISTRY, and it is now the same list twice on purpose: `choices`
    # accepts exactly what `metavar` advertises, because with no live channel left there is no
    # longer any gap between "what works" and "what we still answer for". A channel removed AFTER
    # this stage is accepted and advertised without anyone editing this file — which is the whole
    # of `MIGRATE-ANSWERS-A-REMOVED-CHANNEL-AS-A-TYPO`, closed by derivation rather than by a list.
    p.add_argument("channel", choices=tuple(REMOVED_CHANNELS),
                   metavar="{%s}" % ",".join(REMOVED_CHANNELS),
                   help="the removed channel to explain")
    p.set_defaults(func=cmd_migrate)


__all__ = ["cmd_migrate", "register"]
