"""GR.S2 (e)+(g)+(h)+(i) — adopt / offer / detect-and-disclose for the code graph.

code-review-graph is the RECOMMENDED DEFAULT at setup (embedder pattern, NOT hard-mandatory —
doc 00:42 "recommended, never required — always ship a fallback"). This module holds:

  * adopt_graph          (i) PIN a graph tool into the committed manifest — the ONLY durable
                             write, and it goes through the WriteGate (P2). (e): existing repos
                             refresh here too, never via a silent rewrite.
  * offer_graph_at_setup (i) the consented setup OFFER: ASK (fail-closed off TTY) + assisted
                             install; accept => adopt (gated); decline => USER-scoped record,
                             never re-asked.
  * detected_graph_overlay (g) surface a detected-but-UNPINNED real graph tool for read-only
                             use via the router — detection never writes the manifest.
  * disclose_first_use   (h) once-per-repo disclosed + ledgered notice on the first USE of a
                             newly PATH-detected external graph binary (supply-chain, P14).

Doc 85 boundary holds throughout: mokata ADOPTS a commodity graph; it never builds a parser or
a vector-first code RAG of its own.

Stdlib-only. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from .. import MANIFEST_FILENAME, MOKATA_DIR, TEMP_LOCAL_DIRNAME
from .. import schema
from ..govern.gate import WriteGate, WriteRequest
from ..govern.secrets import scan
from ..profiles import TOOL_CATALOG
from ..tdd_state import state_dir
from . import user_prefs

# The real structural graphs mokata can pin/adopt (the lexical + AST floors are never "adopted").
ADOPTABLE_GRAPH_TOOLS = ("code-review-graph", "serena")

_FIRST_USE_PREFIX = "graph_first_use__"


@dataclass
class AdoptResult:
    committed: bool
    message: str = ""
    tool: str = ""


@dataclass
class OfferResult:
    adopted: bool
    declined: bool = False
    already: bool = False
    message: str = ""


def _manifest_path(root: str) -> str:
    return os.path.join(root, MOKATA_DIR, MANIFEST_FILENAME)


def _load_manifest_data(root: str) -> dict:
    with open(_manifest_path(root), encoding="utf-8") as fh:
        return json.load(fh)


def graph_pinned_tool(data: dict) -> Optional[str]:
    """The real graph tool pinned in the committed code_graph chain, or None (floor-only)."""
    chain = (((data.get("capabilities") or {}).get("code_graph") or {}).get("fallback")) or []
    for t in chain:
        if t in ADOPTABLE_GRAPH_TOOLS:
            return t
    return None


def adopt_graph(
    root: str,
    tool: str = "code-review-graph",
    *,
    assume_yes: bool = False,
    confirm: Optional[Callable[[str], bool]] = None,
    out: Optional[Callable[[str], None]] = None,
    ledger: Any = None,
    policy: Any = None,
    client: Any = None,
) -> AdoptResult:
    """PIN a graph tool into the committed manifest — the ONE durable write, through the gate.

    Adds the tool's catalog entry and prepends it to the `code_graph` chain (the floors stay
    beneath it). A version-compat handshake runs first when a `client` is given: a skew REFUSES
    the adoption rather than pinning a tool mokata can't map (GR.S2(k))."""
    emit = out or print
    if tool not in ADOPTABLE_GRAPH_TOOLS or tool not in TOOL_CATALOG:
        return AdoptResult(False, f"'{tool}' is not an adoptable graph tool", tool)

    # Version-compat handshake at adopt time (refuse a skew before writing anything).
    if client is not None:
        from .crg_client import CrgVersionSkew, check_version_string
        vfn = getattr(client, "check_version_compat", None) or getattr(client, "version", None)
        try:
            if getattr(client, "check_version_compat", None):
                client.check_version_compat(root)
            elif vfn:
                check_version_string(vfn(root))
        except CrgVersionSkew as exc:
            emit(f"mokata graph adopt refused — {exc}")
            return AdoptResult(False, str(exc), tool)

    data = _load_manifest_data(root)
    caps = data.setdefault("capabilities", {})
    cg = caps.setdefault("code_graph", {})
    chain: List[str] = list(cg.get("fallback") or [])
    if tool in chain:
        chain.remove(tool)
    chain.insert(0, tool)                       # pin at the front (most-preferred)
    cg["fallback"] = chain
    # ensure the layer + capability owner are on (adopting a graph implies the knowledge layer)
    data.setdefault("tools", {})[tool] = dict(TOOL_CATALOG[tool], enabled=True)

    new_text = json.dumps(data, indent=2, sort_keys=False) + "\n"
    path = _manifest_path(root)

    findings = scan(text=new_text, path=path)
    if findings:
        emit("mokata graph adopt blocked — secret detected in the manifest.")
        return AdoptResult(False, "blocked: secret detected", tool)
    errors = schema.validate_manifest(data)
    if errors:
        emit("mokata graph adopt rejected — the change would make the manifest invalid.")
        return AdoptResult(False, "invalid manifest", tool)

    emit(f"mokata graph adopt {tool}: pin into code_graph -> {chain}")

    def _commit() -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)

    from ..govern.trust import (CLI_SURFACE, policy_approved, policy_surface, policy_tool,
                                policy_trust)
    gate = WriteGate(ledger=ledger, trust=policy_trust(policy))
    outcome = gate.submit(
        WriteRequest(kind="config", target=path, content=new_text,
                     tool=policy_tool(policy, "graph_adopt"),
                     surface=policy_surface(policy, CLI_SURFACE)),
        commit=_commit, confirm=confirm, assume_yes=assume_yes,
        human_approved=policy_approved(policy))

    if outcome.committed:
        if ledger is not None:
            ledger.record("graph_adopt", tool=tool, scope="repo")
        # (j) provision CRG's OWN bundled semantic index in the SAME consented flow (operational,
        # best-effort — a missing [embeddings] extra just leaves structural-only, degrade-clean).
        provision = getattr(client, "provision_semantic", None)
        if callable(provision):
            try:
                if provision(root):
                    emit("provisioned code-review-graph's local semantic index.")
            except Exception:
                emit("note: semantic index not provisioned (install "
                     "code-review-graph[embeddings]); structural queries still work.")
    return AdoptResult(outcome.committed, outcome.reason, tool)


def offer_graph_at_setup(
    root: str,
    *,
    prompt_fn: Optional[Callable[[str], bool]] = None,
    assume_yes: bool = False,
    out: Optional[Callable[[str], None]] = None,
    ledger: Any = None,
    install_fn: Optional[Callable[[], bool]] = None,
    user_home: Optional[str] = None,
    client: Any = None,
) -> OfferResult:
    """The consented setup OFFER (embedder pattern). Idempotent + no-nag:
      * already pinned            -> no ask, adopted=True.
      * previously declined       -> no ask (USER-scoped record), declined=True.
      * ASK (fail-closed off TTY) -> accept: assisted install + gated adopt; decline: record it.
    """
    emit = out or print
    data = _load_manifest_data(root)
    if graph_pinned_tool(data):
        return OfferResult(adopted=True, already=True, message="a code graph is already adopted")
    if user_prefs.graph_declined(root, user_home=user_home):
        return OfferResult(adopted=False, declined=True, message="previously declined (no nag)")

    if prompt_fn is None:
        from ..prompt import read_yes_no
        prompt_fn = lambda q: read_yes_no(q)   # noqa: E731 — fail-closed off TTY by default

    question = ("Adopt code-review-graph for richer structural queries "
                "(callers/blast-radius/semantic)? It stays optional — mokata's AST floor "
                "carries on without it. [y/N] ")
    if not prompt_fn(question):
        user_prefs.record_graph_decline(root, user_home=user_home)
        if ledger is not None:
            ledger.record("graph_offer", decision="declined", scope="repo")
        emit("Keeping the AST floor. (mokata won't ask again — `mokata graph adopt` any time.)")
        return OfferResult(adopted=False, declined=True, message="declined")

    # accepted: assisted install, then the gated pin.
    if install_fn is not None:
        install_fn()
    else:
        emit("Install with:  pip install code-review-graph[embeddings]   "
             "(bundled local embedding; no API key)")
    res = adopt_graph(root, tool="code-review-graph", assume_yes=assume_yes,
                      out=out, ledger=ledger, client=client)
    return OfferResult(adopted=res.committed, message=res.message)


def detected_graph_overlay(router: Any, root: str) -> Optional[str]:
    """(g) A detected-but-UNPINNED real graph tool that MAY be used read-only via the router.

    Returns the tool id when the committed chain pins no real graph yet one is detected on this
    machine; else None. Detection NEVER writes the manifest — pinning stays gated (`graph adopt`).
    """
    from ..manifest import ManifestError
    try:
        chain = router.manifest.fallback_order("code_graph")
    except (ManifestError, AttributeError):
        return None
    if any(t in ADOPTABLE_GRAPH_TOOLS for t in chain):
        return None                              # a real graph is already pinned — no overlay
    det = getattr(router, "detector", None)
    if det is None:
        return None
    for tool in ADOPTABLE_GRAPH_TOOLS:
        try:
            if det.is_present(tool, TOOL_CATALOG.get(tool, {})):
                return tool
        except (OSError, AttributeError):
            continue
    return None


def disclose_first_use(root: str, tool: str, *, ledger: Any = None,
                       out: Optional[Callable[[str], None]] = None) -> bool:
    """(h) Fire the once-per-repo disclosed + ledgered notice the FIRST time a PATH-detected
    external graph binary is USED. Returns True on the firing use, False forever after. Backed by
    an atomic O_EXCL marker under temp_local (run-state; ungated)."""
    emit = out or print
    sdir = state_dir(root)
    try:
        os.makedirs(sdir, exist_ok=True)
    except OSError:
        return False
    marker = os.path.join(sdir, _FIRST_USE_PREFIX + tool.replace("/", "_"))
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        return False                             # already disclosed for this repo
    except OSError:
        return False
    emit(f"note: using a detected external code graph '{tool}' (read-only). It was found on "
         f"PATH and is being used to answer structural queries; pin it durably with "
         f"`mokata graph adopt` or ignore this to keep it read-only.")
    if ledger is not None:
        ledger.record("graph_first_use", tool=tool, disclosed=True, scope="repo")
    return True
