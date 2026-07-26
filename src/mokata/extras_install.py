"""DB.S4 — the CONSENT-INSTALL primitive: ask → install → verify → ledger, once.

mokata's optional extras are real installs: they run `pip`, they may touch the network, and they
change what the user's environment contains. P2 says a durable change gets a human gate, and this
is the gate for the one class of change that isn't a file write.

The flow, and why each step is a step:

  1. **ASK** — fail-closed off a TTY (`prompt.read_yes_no`). An agent harness leaves stdin
     connected but silent; a bare `input()` there hangs forever, and an install that "defaults to
     yes" because nobody answered is not consent.
  2. **INSTALL** — ONE bounded subprocess (the MCP-R.D0 discipline). `pip` on a wedged index is
     the canonical unbounded hang; a timeout here degrades to the fallback tier, which is a
     perfectly good outcome, whereas a hang is not an outcome at all.
  3. **VERIFY** — an install that reports success and then can't be imported is worse than no
     install, because the user now believes they have the capability. So the caller supplies a
     `verify` callable and its verdict, not pip's exit code, decides.
  4. **LEDGER + NO NAG** — the decision is recorded once. A DECLINE is recorded USER-scoped
     (`~/.mokata/extra_declines.json`, the `knowledge/user_prefs` precedent): it is the human's
     standing preference, it travels across re-clones, and re-asking it every run is how a
     consent prompt becomes a thing people learn to dismiss without reading.

**Reused, not embeddings-specific.** DB.S4 is the first caller (the `mokata[embeddings]` extra),
G1's `--mode=memory|full` is the second, and `knowledge/graph_adopt.offer_graph_at_setup` is the
third — its `install_fn` seam now defaults to `install_extra`, which is the GR.S2 CRG seam-reuse
hook DB.S4 was tasked with carrying: the CI leg at GR.S2-FU pip-installs the real
`code-review-graph` through this same bounded, verified path rather than a bespoke shell line.

Stdlib-only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 — one bounded, fully-argumented pip call; never shell=True
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

# The bound on the pip subprocess. Generous — a cold wheel build on a slow link is legitimately
# minutes — but FINITE, which is the whole point (D0: no unbounded wait anywhere).
INSTALL_TIMEOUT_S = 300.0

_DECLINES_FILE = "extra_declines.json"


@dataclass
class InstallResult:
    ok: bool = False                 # installed AND verified — the only success that counts
    ran: bool = False                # the pip subprocess actually executed
    verified: bool = False
    message: str = ""
    spec: str = ""


@dataclass
class OfferResult:
    accepted: bool = False
    installed: bool = False          # accepted AND the install verified
    declined: bool = False
    asked: bool = False              # False when a prior decline suppressed the ask (no nag)
    message: str = ""


# ------------------------------------------------------------------ the bounded install
def install_extra(spec: str, *, verify: Optional[Callable[[], bool]] = None,
                  timeout: float = INSTALL_TIMEOUT_S,
                  runner: Optional[Callable[..., Any]] = None,
                  out: Optional[Callable[[str], None]] = None) -> InstallResult:
    """`pip install <spec>` in THIS interpreter, bounded, then verify. Never raises.

    `sys.executable -m pip` (not a bare `pip`) so the extra lands in the interpreter that will
    import it — a `pip` earlier on PATH belonging to a different environment installs a package
    mokata then cannot see, and the user is told it worked. `runner` is injectable so tests
    exercise every branch without a real install; `verify` is the caller's proof that the thing
    is actually usable now."""
    emit = out or print
    run = runner or subprocess.run
    cmd = [sys.executable, "-m", "pip", "install", spec]
    emit(f"installing {spec} …")
    try:
        proc = run(cmd, capture_output=True, text=True, timeout=timeout, check=False)  # nosec B603
    except subprocess.TimeoutExpired:
        return InstallResult(ran=True, message=f"install timed out after {int(timeout)}s", spec=spec)
    except (OSError, ValueError) as exc:
        # DEGRADE_CLEAN: no pip / an unexecutable interpreter path (OSError), a malformed argv
        # (ValueError). Both mean "the install did not happen", which the caller handles by
        # staying on the fallback tier — there is nothing here to crash a setup over.
        return InstallResult(ran=False, message=f"could not run pip: {exc}", spec=spec)

    if getattr(proc, "returncode", 1) != 0:
        # pip's own stderr can be long and can echo index URLs (which may carry credentials in a
        # corporate setup), so report the LAST line and the code, never the whole transcript.
        tail = (getattr(proc, "stderr", "") or "").strip().splitlines()
        return InstallResult(ran=True, spec=spec,
                             message=f"pip exited {proc.returncode}"
                                     + (f": {tail[-1]}" if tail else ""))

    if verify is None:
        return InstallResult(ok=True, ran=True, verified=True, spec=spec, message="installed")
    try:
        verified = bool(verify())
    except Exception as exc:
        # DEGRADE_CLEAN, broad by necessity: `verify` is a CALLER-supplied probe that imports an
        # OPTIONAL third-party package and may load a model — its raisables belong to packages
        # mokata cannot import at module scope to name. A failed probe means "not usable", which
        # is precisely the answer the caller needs, so there is nothing to re-raise.
        return InstallResult(ran=True, spec=spec,
                             message=f"installed, but verification failed: "
                                     f"{type(exc).__name__}: {exc}")
    return InstallResult(ok=verified, ran=True, verified=verified, spec=spec,
                         message="installed and verified" if verified else
                                 "installed, but the package is still not usable here")


# ------------------------------------------------------------------ user-scoped decline records
def _user_dir(user_home: Optional[str] = None) -> str:
    return os.path.join(user_home or os.path.expanduser("~"), ".mokata")


def _declines_path(user_home: Optional[str] = None) -> str:
    return os.path.join(_user_dir(user_home), _DECLINES_FILE)


def _load(user_home: Optional[str] = None) -> Dict[str, bool]:
    try:
        with open(_declines_path(user_home), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _key(root: str, extra: str) -> str:
    return f"{extra}@{os.path.abspath(root)}"


def extra_declined(root: str, extra: str, user_home: Optional[str] = None) -> bool:
    """True when the human already declined `extra` for this repo — so we never ask again."""
    return bool(_load(user_home).get(_key(root, extra)))


def record_extra_decline(root: str, extra: str, user_home: Optional[str] = None) -> None:
    """Record the decline in the USER's profile (idempotent). Not a durable PROJECT write, so no
    WriteGate: recording that a human said "no" is not a silent change to their repo (P2)."""
    d = _load(user_home)
    d[_key(root, extra)] = True
    try:
        os.makedirs(_user_dir(user_home), exist_ok=True)
        with open(_declines_path(user_home), "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2)
    except OSError:
        # A user-pref write that can't land is non-fatal: worst case we ask once more later, which
        # is a far better failure than aborting a setup over a read-only home directory.
        pass


# ------------------------------------------------------------------ the whole consented flow
def offer_extra(root: str, extra: str, spec: str, question: str, *,
                already: Optional[Callable[[], bool]] = None,
                verify: Optional[Callable[[], bool]] = None,
                prompt_fn: Optional[Callable[[str], bool]] = None,
                assume_yes: bool = False,
                runner: Optional[Callable[..., Any]] = None,
                timeout: float = INSTALL_TIMEOUT_S,
                ledger: Any = None,
                user_home: Optional[str] = None,
                decline_note: str = "",
                out: Optional[Callable[[str], None]] = None) -> OfferResult:
    """The full ask → install → verify → ledger flow, idempotent and no-nag.

      * already present  -> no ask (`already()` says the capability is there);
      * previously declined -> no ask, `asked=False` (the no-nag guarantee);
      * ask -> accept: bounded install + verify, ledgered; decline: recorded once, ledgered.

    Every failure lands on the SAME outcome — `installed=False` — and the caller stays on its
    fallback tier. That is deliberate: nothing in setup should be able to fail in a way that
    stops setup."""
    emit = out or print
    if already is not None and already():
        return OfferResult(accepted=True, installed=True, asked=False,
                           message=f"{extra} is already available")
    if extra_declined(root, extra, user_home=user_home):
        return OfferResult(declined=True, asked=False, message="previously declined (no nag)")

    if prompt_fn is None and not assume_yes:
        from .prompt import read_yes_no
        prompt_fn = lambda q: read_yes_no(q)       # noqa: E731 — fail-closed off TTY by default

    accepted = True if assume_yes else bool(prompt_fn(question))
    if not accepted:
        record_extra_decline(root, extra, user_home=user_home)
        if ledger is not None:
            # The extra's NAME and the decision. Never the pip spec's index URL, never anything
            # about the memory the tier would have embedded.
            ledger.record("extra_offer", extra=extra, decision="declined", scope="repo")
        emit(decline_note or f"Skipping {extra}. (mokata won't ask again — "
                             f"`pip install {spec}` any time.)")
        return OfferResult(declined=True, asked=True, message="declined")

    res = install_extra(spec, verify=verify, timeout=timeout, runner=runner, out=out)
    if ledger is not None:
        ledger.record("extra_offer", extra=extra, decision="accepted",
                      installed=res.ok, scope="repo")
    if not res.ok:
        emit(f"note: {extra} is not active — {res.message}. mokata carries on with its "
             f"built-in fallback; retry any time with `pip install {spec}`.")
    return OfferResult(accepted=True, installed=res.ok, asked=True, message=res.message)


# ------------------------------------------------------------------ the embeddings offer (DB.S4)
def offer_embeddings(root: str, **kwargs: Any) -> OfferResult:
    """The setup/init OFFER for `mokata[embeddings]` — FULL is the recommended default (Jas
    2026-07-14), so setup ASKS and installs on consent, and a decline leaves the hashing tier.

    `already` and `verify` are the SAME probe on purpose: "is a real embedder usable here" is one
    question, and asking it before deciding to install is what makes the offer idempotent."""
    from .memory.embed import EMBEDDINGS_EXTRA, MODEL2VEC_ID

    def _usable() -> bool:
        from .memory.embed import Model2VecEmbedder, ModelUnavailable
        try:
            return bool(Model2VecEmbedder())
        except ModelUnavailable:
            return False

    question = ("Install mokata's semantic memory model (~30MB, runs locally, no API key)? "
                "Without it, recall uses token-hash matching instead of meaning. [y/N] ")
    kwargs.setdefault("already", _usable)
    kwargs.setdefault("verify", _usable)
    kwargs.setdefault("decline_note",
                      "Keeping the built-in hashing tier (token-hash, not meaning). "
                      "(mokata won't ask again — `pip install 'mokata[embeddings]'` any time.)")
    return offer_extra(root, MODEL2VEC_ID.split(":")[0], EMBEDDINGS_EXTRA, question, **kwargs)
