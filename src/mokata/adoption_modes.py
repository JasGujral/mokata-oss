"""G1 — GRADUATED ADOPTION: `mokata init --mode {seatbelt,memory,full}`.

Bare Claude Code's onboarding is "read the docs". mokata's is three NAMED on-ramps, each
landing a visible win in under five minutes and printing the ONE command that proves it.

A mode is an **alias + an onboarding flavour**, never a second config axis (P8, and the
Two-modes-one-shape decision). It resolves to an EXISTING profile, and the PROFILE is the
only thing persisted: a manifest written by `--mode memory` is byte-identical to one written
by `--profile standard`. Nothing records the mode: the offers fire and the quickstart prints
inside the same init, and the one later reader — the SessionStart check on miss (0.0.25 I-04,
`first_session_check`) — DERIVES the mode from the persisted profile + embedder setting.

**Why `seatbelt` maps to `standard`, not `minimal`** (grounded, not assumed). The seatbelt IS
the gates, and brainstorm's HARD-GATE is a two-lens gate whose Lens 1 is the blast radius from
`KnowledgeLayer.blast_radius()`. `settings.graph.required` is ON by default (GR.S3), so a
DEGRADED radius is REFUSED as decision input. Verified from code on one scratch repo, same
symbol, only the profile changed:

    --profile minimal   ->  blast_radius(beta) via grep [grep fallback]
                            "(lexical fallback (no structural graph; results are approximate))"
    --profile standard  ->  blast_radius(beta) via ast  [graph]

`minimal` disables the `knowledge` layer, so `code_graph` is dropped at resolution (K1) and the
radius falls to the lexical floor with `degraded=True` (a genuine absence — no floor can
account for the symbol at all) — which `govern.graph_required` then refuses at `spec emit`
(`mcp/tools_spec.py`, phase 7). ⚠ NOT at `BrainstormSession.approve(graph_gate=...)`, as this
note said until 0.0.17: that parameter has no production caller and the approve-path refusal is
inert (`GATE-UNREACHABLE-BRAINSTORM`, doc 84; wiring deferred to 0.0.20 by ruling F2(b), which
supersedes D14 — the JS/TS floor it must land beside moved with it). The CONCLUSION
below is unchanged — the refusal still lands, just later in the pipeline — so a `seatbelt` mode
on `minimal` would still ship a mode whose headline command dead-ends on default settings, and
the dead-end is WORSE for arriving at phase 7 than it would be at phase 1. The gates consume
the AST floor, so seatbelt wires it. `seatbelt` and `memory` land the same profile and differ
in FLAVOUR: memory offers the semantic tier, seatbelt structurally never does.

A mode's optional packages are fetched by the INSTALLER, never by setup (0.0.26 L1): `memory`
is `mokata[embeddings]`, `full` is `mokata[full]`. `wire_mode` only detects and wires them, and
for anything missing prints the ONE install line for the installer that owns this environment —
so no setup, `--yes` or not, ever reaches `pip` or rebuilds the env it runs from.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ModeSpec:
    """One named on-ramp: the profile it aliases, which offers it carries, its quickstart."""

    name: str
    profile: str
    summary: str
    offers_embeddings: bool
    offers_graph: bool
    quickstart: Tuple[str, ...]
    extra: str = ""               # the pyproject extra that installs everything this mode wires


# The three on-ramps. `profile` is the ONLY thing that reaches the manifest.
ADOPTION_MODES: Dict[str, ModeSpec] = {
    "seatbelt": ModeSpec(
        name="seatbelt",
        profile="standard",
        summary="just the gates (and the code graph they need to be real)",
        offers_embeddings=False,
        offers_graph=False,
        quickstart=(
            "── seatbelt mode: the gates are on ─────────────────────────────",
            "  You got: the spec-driven TDD engine, the governance gates (human-gated",
            "  writes, secret scanning, RED-before-GREEN), and the built-in AST code",
            "  graph the blast-radius gate needs to answer structurally instead of by grep.",
            "  Try it now:  /brainstorm  — propose a change and watch the HARD-GATE refuse",
            "  to emit a spec until you have approved an approach.",
            "  See the guardrails any time:  mokata rules",
        ),
    ),
    "memory": ModeSpec(
        name="memory",
        profile="standard",
        summary="the gates + typed project memory that survives every session",
        offers_embeddings=True,
        offers_graph=False,
        quickstart=(
            "── memory mode: the gates + memory that persists ────────────────",
            "  You got: everything seatbelt gives you, plus typed project memory on the",
            "  local SQLite store — rules, guardrails, decisions and context that outlive",
            "  the session instead of being re-explained every time.",
            "  Try it now:  /onboard  — teach mokata one project rule, then run",
            "  `mokata memory` to see it back in the store.",
            "  Semantic recall comes with mokata[embeddings] (or mokata[full]).",
        ),
        extra="embeddings",
    ),
    "full": ModeSpec(
        name="full",
        profile="full",
        summary="everything the spine can wire",
        offers_embeddings=True,
        offers_graph=True,
        quickstart=(
            "── full mode: everything wired ─────────────────────────────────",
            "  You got: the gates, persistent memory, and every graph and memory provider",
            "  the spine knows about (each degrades to its floor when absent).",
            "  Try it now:  mokata query blast_radius <symbol>  — ask who actually calls a",
            "  function; answered from the code graph, not from a grep.",
            "  Then:  /brainstorm  — that same blast radius lands inside the decision.",
        ),
        extra="full",
    ),
}


#: Row statuses. Each non-active row also carries its CAUSE — "not installed", "installed but the
#: model could not be fetched", and "installed but not switched on" are different facts with
#: different fixes and never share a representation (doc 85 §7g).
ACTIVE = "active"
MISSING = "not installed"
BLOCKED = "installed, not usable"
NOT_WIRED = "installed, not switched on"


@dataclass
class DepRow:
    """One optional dependency of a mode and what setup found."""

    key: str                      # "embeddings" | "code-graph" | "code-graph-index" | "graph-ts"
    label: str                    # what a human calls it
    status: str = ""
    cause: str = ""               # WHY a non-active row is not active — never empty for one
    fix: str = ""                 # the one line that fixes THIS row, when it is not the install
    detail: str = ""              # what is live, for an active row (e.g. the embedder id)

    @property
    def ok(self) -> bool:
        return self.status == ACTIVE


@dataclass
class ModeWiringResult:
    """What setup's detect-and-wire step found and did. `*Result` = produced value (doc 85 §3)."""

    mode: str = ""
    rows: List[DepRow] = field(default_factory=list)
    install_line: str = ""        # the ONE line that installs every missing package, or ""
    graph_adopted: bool = False
    embedder_wired: bool = False  # settings.memory.embedder is set — WITHOUT it the semantic
                                  # tier is OFF even with model2vec installed (measured, I-02)
    notes: List[str] = field(default_factory=list)
    rerun: str = ""               # the setup command a fix names; "" = `setup_command_for(mode)`

    def row(self, key: str) -> Optional[DepRow]:
        return next((r for r in self.rows if r.key == key), None)

    @property
    def complete(self) -> bool:
        """Every dependency the mode wants is installed, usable and switched on."""
        return all(r.ok for r in self.rows) and not self.notes


def mode_names() -> List[str]:
    """The `--mode` choices, in adoption order (least to most)."""
    return list(ADOPTION_MODES)


def mode_spec(mode: str) -> ModeSpec:
    try:
        return ADOPTION_MODES[mode]
    except KeyError:
        raise ValueError(
            f"unknown mode '{mode}'; choose one of {mode_names()}"
        ) from None


def profile_for_mode(mode: str) -> str:
    """The profile a mode aliases. `*_for` always returns (doc 85 §3) — unknown mode raises."""
    return mode_spec(mode).profile


def setup_command_for(mode: str) -> str:
    """The command that detects and wires `mode`, as THIS route runs it.

    0.0.26 L8: on the plugin route (a plugin hook or MCP server — `CLAUDE_PLUGIN_ROOT` — or the
    plugin's CLI launcher — `MOKATA_VIA_PLUGIN`) there is no `mokata` on PATH, so every fix line
    names the plugin's pinned command instead of a CLI the user does not have."""
    from .plugin_runtime import on_plugin_route
    if on_plugin_route():
        return plugin_setup_command(mode)
    return f"mokata setup claude --mode {mode_spec(mode).name}"


def render_quickstart(mode: str, outcome: Optional["ModeWiringResult"] = None) -> str:
    """The mode's printed 5-minute quickstart. Pure: renders, never writes (doc 85 §3).

    Every command named here is real and works on the wiring the mode actually lands — the
    doc-00 rule-2 spirit applied to CLI output. Slash commands use the BARE `/<name>` form,
    which is the canonical pip/setup route this command belongs to (doc 85 §3).

    0.0.25 I-02 (`INSTALL-OUTCOME-REPORT`): given an `outcome` that is not complete, the banner
    never claims "everything wired" — it says what is not, and points at the table above it."""
    lines = list(mode_spec(mode).quickstart)
    if outcome is not None and not outcome.complete:
        lines[0] = lines[0].replace("everything wired", "PARTIALLY wired")
        lines.append("  ⚠ Not every dependency is active — the table above names each "
                     "one, why, and its fix.")
    return "\n".join([""] + lines)


def render_outcome(outcome: "ModeWiringResult") -> str:
    """The one closing table: each dependency, its status and CAUSE, the row's own fix when it has
    one — and, when packages are missing, the ONE install line for THIS installer followed by the
    setup re-run. Empty for a mode with no dependencies."""
    if not outcome.rows:
        return ""
    w = max(len(r.label) for r in outcome.rows)
    out = ["", f"── {outcome.mode} mode: optional dependencies " + "─" * 20]
    for r in outcome.rows:
        mark = "✓" if r.ok else "✗"
        text = r.status + (f" ({r.detail})" if r.ok and r.detail else "")
        if not r.ok and r.cause:
            text += f" — {r.cause}"
        out.append(f"  {mark} {r.label.ljust(w)}  {text}")
        if not r.ok and r.fix:
            out.append(f"      fix: {r.fix}")
    for n in outcome.notes:
        out.append(f"  note: {n}")
    if outcome.install_line:
        out.append(f"  → one line installs everything {outcome.mode} mode needs "
                   f"(setup never rebuilds its own environment):")
        out.append(f"      {outcome.install_line}")
        out.append(f"    then:  {outcome.rerun or setup_command_for(outcome.mode)}")
    return "\n".join(out)


#: What a human calls each dependency — one table, read by the setup rows AND the I-04 check.
_DEP_LABELS: Dict[str, str] = {
    "embeddings": "semantic memory model",
    "code-graph": "code-review-graph (structural)",
    "graph-ts": "TypeScript parser (graph-ts)",
    "lexical": "word search (ripgrep)",
}

#: 0.0.26 O13 (R-b) — the row `--mode full` adds once code-review-graph is pinned: its index for
#: THIS repo, built by setup. Not a package, so it is not in `mode_dependencies` (the I-04 check
#: and the install line are about packages); `wire_mode` appends it after the code-graph row.
INDEX_ROW_KEY = "code-graph-index"
INDEX_ROW_LABEL = "code-review-graph index"

#: The package a missing row names in its cause.
_DEP_PACKAGES: Dict[str, str] = {
    "embeddings": "model2vec",
    "code-graph": "code-review-graph",
    "graph-ts": "tree-sitter-typescript",
    "lexical": "ripgrep",
}


def mode_dependencies(mode: str) -> List[str]:
    """The dependency keys `mode` wires, in table order."""
    spec = mode_spec(mode)
    return ((["embeddings"] if spec.offers_embeddings else [])
            + (["code-graph", "graph-ts", "lexical"] if spec.offers_graph else []))


def _importable(mod: str) -> bool:
    from importlib.util import find_spec
    try:
        return find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def _present_embeddings() -> bool:
    """Offline: is the package there? (The model is a separate fact — `_warm_model`.)"""
    return _importable("model2vec")


def _present_crg() -> bool:
    """0.0.26 O15: installed IN mokata's environment (usable or refused for its version) — a copy
    on PATH from another environment does not count; the row's cause names it instead."""
    from .crg_resolve import resolve_crg
    res = resolve_crg()
    return res.ok or bool(res.refused)


def _present_graph_ts() -> bool:
    from .knowledge import ts_edges
    return ts_edges.available()


#: Is each dependency's PACKAGE importable in this environment? (setup: imports are fine here.)
#: The machines the pinned `ripgrep` wheel exists for (pyproject's marker on `ripgrep==14.1.0`).
_RG_WHEEL_MACHINES = ("x86_64", "AMD64", "aarch64", "arm64")


def _rg_wheel_here() -> bool:
    import platform
    return platform.machine() in _RG_WHEEL_MACHINES


def _rg_command() -> Optional[str]:
    from .detect import find_command
    return find_command("rg")


def _present_lexical() -> bool:
    """ripgrep found — or this machine has no ripgrep wheel, where the pure-Python floor IS the
    end state (and the row says so) rather than a package the install line could fetch."""
    return _rg_command() is not None or not _rg_wheel_here()


_PRESENT: Dict[str, Callable[[], bool]] = {
    "embeddings": _present_embeddings,
    "code-graph": _present_crg,
    "graph-ts": _present_graph_ts,
    "lexical": _present_lexical,
}


def _warm_model() -> str:
    """Load (downloading on a cold cache, ~30 MB from huggingface.co) and encode once — the model
    warm-up. Returns the live embedder id; raises with the reason when it cannot."""
    from .memory.embed import Model2VecEmbedder
    return Model2VecEmbedder().embedder_id


def _crg_missing_cause(cause: str) -> str:
    """0.0.26 O15 — a code-review-graph on PATH from ANOTHER environment is named, not used."""
    from .crg_resolve import resolve_crg
    res = resolve_crg()
    if res.disabled:
        return f"{cause} (code-review-graph is switched off: MOKATA_CRG=off)"
    outside = res.outside
    if outside:
        return (f"{cause}; found code-review-graph on PATH outside mokata's environment "
                f"({outside}) — it is not used")
    return cause


def _crg_command() -> Optional[str]:
    from .detect import find_command
    return find_command("code-review-graph")


def model_fetch_cause(error: str, environ: Optional[Dict[str, str]] = None) -> str:
    """See `memory.embed.model_fetch_cause` — one classifier, read by setup and doctor."""
    from .memory.embed import model_fetch_cause as _cause
    return _cause(error, environ)


_EMBEDDER_KEY = "settings.memory.embedder"


def _embedder_wired(root: str) -> bool:
    from .knowledge.graph_adopt import _load_manifest_data
    data = _load_manifest_data(root)
    return bool(((data.get("settings") or {}).get("memory") or {}).get("embedder"))


def _wire_embedder(root: str, *, out: Callable[[str], None], ledger: Any) -> bool:
    """`settings.memory.embedder = auto` through the gated config write. The setup run that calls
    this was itself approved (its own gate, or `--yes`), and choosing `--mode memory|full` IS the
    request to switch the semantic tier on (P2: consent already given, not assumed)."""
    from .config_cmd import config_set
    res = config_set(root, _EMBEDDER_KEY, "auto", assume_yes=True, out=out, ledger=ledger)
    return bool(res.committed)


def _graph_pinned(root: str) -> bool:
    from .knowledge.graph_adopt import _load_manifest_data, graph_pinned_tool
    return bool(graph_pinned_tool(_load_manifest_data(root)))


def _build_index(root: str, out: Callable[[str], None]):
    """R-b: build code-review-graph's index now, with progress on `out`. `crg_build.build_now`
    holds the repo's build lock (a first-use background build cannot start beside it), has NO time
    limit (a 1M+ line repo takes many minutes), removes a partial index on failure or Ctrl+C, and
    on a re-run over a built index is incremental (`update`)."""
    from .knowledge import crg_build
    if crg_build.index_built(root):
        out("bringing code-review-graph's index up to date (incremental)")
    else:
        out("building code-review-graph's index for this repo — a one-time cost (many minutes on "
            "a million-line repo; progress below); Ctrl+C skips it and the first query builds it "
            "in the background instead")
    return crg_build.build_now(root, on_progress=lambda s: out(f"  {s}"))


SEARCH_ROW_KEY = "meaning-index"
SEARCH_ROW_LABEL = "meaning search index (IDE)"


def _build_search_index(root: str, out: Callable[[str], None]):
    """Build (or bring up to date, or RESUME) the IDE's meaning index — `ide.search.build_index`,
    with progress on `out`. No time limit; Ctrl+C writes a checkpoint and the next run (or the
    first meaning search) resumes from it. Returns `(ok, detail_or_error, interrupted)`."""
    from .ide import search as SR
    emb = SR._load_embedder(allow_network=False)
    meta = SR._read_meta(root)
    out("building the meaning-search index (resumes from where it stopped; Ctrl+C keeps what is "
        "done)" if not meta.get("complete") else
        "bringing the meaning-search index up to date (changed files only)")

    def say(p):
        pct = 100 * p["done"] // p["total"] if p["total"] else 100
        m, sec = divmod(int(p["elapsed"]), 60)
        out(f"  embedding: {p['done']}/{p['total']} files ({pct}%) · {p['chunks']} chunks · "
            f"{m}:{sec:02d}")
    try:
        res = SR.build_index(root, emb, on_progress=say)
    except KeyboardInterrupt:
        return False, "interrupted (Ctrl+C) — what was embedded is kept and the next run resumes", True
    m, sec = divmod(int(res.get("elapsed", 0)), 60)
    rows = res["index"].get("rows", 0)
    return True, (f"{rows} chunks from {len(res['index'].get('files', {}))} files; "
                  f"{res['embedded']} embedded now in {m}:{sec:02d}"), False


def _search_index_row(root: str, emit: Callable[[str], None],
                      build_fn: Optional[Callable[..., Any]]) -> DepRow:
    row = DepRow(SEARCH_ROW_KEY, SEARCH_ROW_LABEL)
    try:
        ok, said, interrupted = (build_fn or _build_search_index)(root, emit)
    except Exception as exc:                          # noqa: BLE001 — a row, never a lost setup
        ok, said, interrupted = False, f"{type(exc).__name__}: {exc}"[:300], False
    if ok:
        row.status, row.detail = ACTIVE, said
    else:
        row.status, row.cause = BLOCKED, said if interrupted else f"not built — {said}"
        row.fix = setup_command_for("full") + " (resumes)" if interrupted else "mokata doctor"
    return row


def _index_row(root: str, mode: str, emit: Callable[[str], None],
               build_fn: Optional[Callable[..., Any]]) -> DepRow:
    """The outcome row for R-b: built (and what), already built, or not built + WHY + the fix."""
    row = DepRow(INDEX_ROW_KEY, INDEX_ROW_LABEL)
    try:
        res = (build_fn or _build_index)(root, emit)
    except Exception as exc:                          # noqa: BLE001 — a row, never a lost setup
        row.status, row.cause = BLOCKED, f"the index build could not run ({type(exc).__name__}: {exc})"
        row.fix = "mokata graph build"
        return row
    if getattr(res, "busy", None) is not None:
        row.status, row.cause = BLOCKED, (f"{res.busy.line()} in another process — it is used "
                                          f"as soon as it finishes")
        row.fix = "mokata graph status"
    elif res.ok:
        row.status, row.detail = ACTIVE, f"built: {res.detail}"
    elif getattr(res, "interrupted", False):
        row.status = BLOCKED
        row.cause = ("not built — skipped with Ctrl+C; the partial index was removed, and the "
                     "first structural query builds it in the background")
        row.fix = "mokata graph build"
    else:
        row.status = BLOCKED
        row.cause = (f"not built — {res.error}; the partial index was removed and the AST floor "
                     f"answers meanwhile")
        row.fix = "mokata graph build"
    return row


def _install_line(extra: str) -> str:
    from .install_route import plan_extra_install
    return plan_extra_install(extra).display


def wire_mode(
    root: str,
    mode: str,
    *,
    ledger: Any = None,
    out: Optional[Callable[[str], None]] = None,
    present: Optional[Dict[str, Callable[[], bool]]] = None,
    warm_fn: Optional[Callable[[], str]] = None,
    crg_command_fn: Optional[Callable[[], Optional[str]]] = None,
    install_line_fn: Optional[Callable[[str], str]] = None,
    adopt_fn: Optional[Callable[..., Any]] = None,
    graph_pinned_fn: Optional[Callable[[str], bool]] = None,
    embedder_wired_fn: Optional[Callable[[str], bool]] = None,
    wire_embedder_fn: Optional[Callable[..., bool]] = None,
    rerun: str = "",
    build_index_fn: Optional[Callable[..., Any]] = None,
    build_search_fn: Optional[Callable[..., Any]] = None,
    rg_command_fn: Optional[Callable[[], Optional[str]]] = None,
) -> ModeWiringResult:
    """DETECT and WIRE the mode's optional dependencies. Never installs anything.

    0.0.26 L1 (`FULL-INSTALLS-EVERYTHING-IN-ONE-GO`, absorbs `WIN-FULL-SEMANTIC-OFF-AFTER-WITH-
    DEPS`). Until 0.0.25 this step INSTALLED the extras into the environment mokata runs from —
    on Windows + uv tool that env holds the running, locked `mokata.exe`, so the rebuild was
    deferred to a printed line, and a full setup took three commands and still ended "PARTIALLY
    wired" when the second setup run was missed (Jas, 2026-10-21). Now the INSTALLER fetches
    everything (`uv tool install "mokata[full]"`), and this step only:

      * detects each package (model2vec, code-review-graph, tree-sitter-typescript);
      * switches the semantic tier on (`memory.embedder = auto`) and WARMS the model — the
        ~30 MB download happens here, in setup, with its cause named when it is blocked;
      * pins code-review-graph as the code graph (gated `adopt_graph`);
      * `full` only (0.0.26 O13, R-b): BUILDS code-review-graph's index for this repo, with
        progress, and adds the `code-review-graph index` row saying built / failed + why;
      * for anything missing, names ONE install line for THIS installer (`install_route.
        plan_extra_install`) and the setup re-run — and spawns nothing.

    The writes go through the existing gates with `assume_yes`: the setup that calls this was
    itself approved, and `--mode` is the request for exactly this wiring (ruling R-c, 0.0.26 O13:
    approving the setup run IS the consent for the embedder setting, the CRG pin and the index
    build).
    DEGRADE_CLEAN: the repo is already wired when this runs; nothing here can undo that."""
    emit = out or print
    spec = mode_spec(mode)
    result = ModeWiringResult(mode=mode, rerun=rerun)
    keys = mode_dependencies(mode)
    if not keys:
        return result
    probes = {**_PRESENT, **(present or {})}
    try:
        result.rows = [DepRow(k, _DEP_LABELS[k]) for k in keys]
        for r in result.rows:
            if not probes[r.key]():
                r.status = MISSING
                r.cause = f"{_DEP_PACKAGES[r.key]} is not in the environment mokata runs from"
                if r.key == "code-graph":
                    r.cause = _crg_missing_cause(r.cause)
        if any(r.status == MISSING for r in result.rows):
            result.install_line = (install_line_fn or _install_line)(spec.extra)

        e = result.row("embeddings")
        if e is not None and e.status != MISSING:
            wired = bool((embedder_wired_fn or _embedder_wired)(root))
            if not wired:
                # Wired even when the model cannot load yet: `auto` degrades to the hashing tier
                # until it can, and switches itself on once the model is cached.
                wired = bool((wire_embedder_fn or _wire_embedder)(root, out=emit, ledger=ledger))
            result.embedder_wired = wired
            try:
                e.detail = (warm_fn or _warm_model)()
                e.status = ACTIVE
            except Exception as exc:                  # noqa: BLE001 — see the D5 register
                e.status = BLOCKED
                e.cause = ("the model (~30 MB) could not be fetched: "
                           + model_fetch_cause(f"{type(exc).__name__}: {exc}"))
                e.fix = f"fix the network cause above, then `{result.rerun or setup_command_for(mode)}`"
            if e.status == ACTIVE and not wired:
                e.status, e.cause = NOT_WIRED, "the `memory.embedder` setting write was not committed"
                e.fix = "mokata config set settings.memory.embedder auto"
            if mode == "full":
                # 0.0.26 O13 — full mode ENDS with meaning search built, not "built on first use".
                if e.status == ACTIVE:
                    srow = _search_index_row(root, emit, build_search_fn)
                else:
                    srow = DepRow(SEARCH_ROW_KEY, SEARCH_ROW_LABEL, status=BLOCKED,
                                  cause="needs the semantic model above",
                                  fix=f"fix that row, then `{setup_command_for(mode)}`")
                result.rows.insert(result.rows.index(e) + 1, srow)

        g = result.row("code-graph")
        if g is not None and g.status != MISSING:
            if not (crg_command_fn or _crg_command)():
                g.status = BLOCKED
                from .crg_resolve import INSTALL_HINT, resolve_crg
                from .detect import env_scripts_dirs
                res = resolve_crg()
                g.cause = res.refused or (
                    "the package is installed but its `code-review-graph` command is not in "
                    "mokata's environment (" + ", ".join(env_scripts_dirs()) + ")")
                if res.outside:
                    g.cause += (f"; the code-review-graph on PATH ({res.outside}) belongs to "
                                f"another environment and is not used")
                g.fix = INSTALL_HINT
            else:
                pinned = bool((graph_pinned_fn or _graph_pinned)(root))
                if not pinned:
                    if adopt_fn is None:
                        from .knowledge.graph_adopt import adopt_graph as adopt_fn
                    ares = adopt_fn(root, tool="code-review-graph", assume_yes=True, out=emit,
                                    ledger=ledger)
                    pinned = bool(getattr(ares, "committed", False))
                result.graph_adopted = pinned
                if pinned:
                    g.status = ACTIVE
                    g.detail = "pinned as the code graph"
                    if mode == "full":
                        idx = _index_row(root, mode, emit, build_index_fn)
                        result.rows.insert(result.rows.index(g) + 1, idx)
                else:
                    g.status, g.cause = NOT_WIRED, "pinning it as the code graph was not committed"
                    g.fix = "mokata graph adopt"

        t = result.row("graph-ts")
        if t is not None and t.status != MISSING:
            t.status = ACTIVE

        lx = result.row("lexical")
        if lx is not None and lx.status != MISSING:
            rg = (rg_command_fn or _rg_command)()
            lx.status = ACTIVE
            lx.detail = (f"ripgrep: {rg}" if rg else
                         "pure-Python floor — ripgrep ships no wheel for this machine")
    except Exception as exc:                      # noqa: BLE001 — DEGRADE_CLEAN
        # The repo is already wired; a dependency step that blows up costs the user the extra
        # tiers, never their setup. Same posture as onboarding.run_wizard.
        result.notes.append(f"dependency step skipped ({type(exc).__name__}: {exc})")
        emit(f"note: the dependency step was skipped ({type(exc).__name__}: {exc}); "
             f"mokata's built-in floors are active.")
    return result


# ------------------------------------------------------------------ I-04: check on miss
# 0.0.25 I-04 (`PLUGIN-ROUTE-FIRST-SESSION-INSTALL`). Claude Code has NO plugin install hook, and
# a plugin's `dependencies` are other PLUGINS — never pip packages. So the plugin route cannot
# install a mode's optional dependencies at install time, and must not try at session time either:
# a hook that installs ~360 MB unasked is exactly what DB.S4 / P2 forbid. The documented pattern is
# CHECK ON MISS at SessionStart: if the repo's mode wants dependencies this environment lacks, the
# briefing names the commands that reach the CLI route's end state — the ONE install line for
# `mokata[<extra>]` when a package is missing, then `mokata setup claude --mode <m>` (0.0.26 L1:
# setup itself never installs).
#
# The mode is DERIVED, never recorded (the module docstring's rule — a mode is not a second config
# axis). Two persisted facts name it:
#   * profile `full`                               -> full   (only `--mode full` lands on it)
#   * profile `standard` + `settings.memory.embedder` set to a semantic embedder -> memory
#     (the user asked for the semantic tier; seatbelt structurally never sets it)
# A seatbelt / plain-standard repo has no mode dependencies, so it is never told anything.
#
# Plugin `userConfig` was considered as the place to record the mode and NOT used: `options`
# (the picker) needs Claude Code >= 2.1.271 — far above mokata's floor — and a plugin using it
# fails to LOAD below that; a free-text option adds an enable-time dialog to the one-step install
# and makes the mode a second persisted axis beside the profile.

#: The marketplace id the README tells users to install (`/plugin install mokata@mostack`).
PLUGIN_ID = "mokata@mostack"

#: Embedder names that are a request for SEMANTIC recall (`memory.embed.make_embedder`).
_SEMANTIC_EMBEDDERS = ("auto", "model2vec")


def mode_for_manifest(data: Dict[str, Any]) -> Optional[str]:
    """The adoption mode a manifest's persisted facts imply, or None when it implies none with
    dependencies. Pure (`*_for` reads; here None is a real answer: 'no mode dependencies')."""
    profile = data.get("profile")
    if profile == "full":
        return "full"
    embedder = ((data.get("settings") or {}).get("memory") or {}).get("embedder")
    if profile == "standard" and embedder in _SEMANTIC_EMBEDDERS:
        return "memory"
    return None


def _present_code_graph() -> bool:
    """'present' is what mokata will actually run (`detect.find_command`: 0.0.26 O15, mokata's own
    environment only, version-checked from installed metadata). Never imports it, never spawns."""
    from .detect import find_command
    return find_command("code-review-graph") is not None


def _present_graph_ts_spec() -> bool:
    return _importable("tree_sitter_typescript")


#: Offline, import-free presence probes for the SessionStart check (no model load, no import of a
#: heavy package, no subprocess — the hook budget).
_PRESENCE: Dict[str, Callable[[], bool]] = {
    "embeddings": _present_embeddings,
    "code-graph": _present_code_graph,
    "graph-ts": _present_graph_ts_spec,
    "lexical": _present_lexical,
}


@dataclass(frozen=True)
class FirstSessionCheck:
    """What the SessionStart check found. `*Check` = a read-only verdict (doc 85 §3)."""

    mode: Optional[str] = None
    missing: Tuple[str, ...] = ()       # human labels of what the mode still lacks
    commands: Tuple[str, ...] = ()      # what the human runs, in order, to reach the end state
    via_plugin: bool = False            # the briefing is served by the PLUGIN's hook

    @property
    def line(self) -> Optional[str]:
        """The one briefing line, or None when there is nothing to say."""
        if not self.mode or not self.missing:
            return None
        # Commands are listed, never `&&`-joined: Windows PowerShell 5.1 has no `&&`.
        steps = ", then ".join(f"`{c}`" for c in self.commands)
        text = (f"⚠ mokata {self.mode} mode is not complete here — missing: "
                f"{', '.join(self.missing)}. To finish it, the human runs in their terminal: "
                f"{steps}. Do not run it for them.")
        if self.via_plugin:
            # 0.0.26 L8 (MP.S1): the plugin's uvx environment IS `mokata[full]==<plugin version>`,
            # so the plugin route reaches the one-line install's end state ON the plugin route:
            # the command runs that same pinned environment and wires only this repo's mode.
            text += (" It runs the plugin's own pinned mokata (no pip, no separate CLI install) "
                     "and wires only this repo's mode; the plugin keeps its hooks and MCP server.")
        return text


def plugin_setup_command(mode: str, *, platform: Optional[str] = None) -> str:
    """The plugin route's `setup claude --mode <m>`: through `uvx`, pinned to the plugin's version
    (the same environment the plugin's MCP server runs in), with `--plugin`."""
    from .install_route import quote_arg
    from .plugin_runtime import UV_PYTHON, pin_spec
    return (f"uvx --python {quote_arg(UV_PYTHON, platform)} --from {quote_arg(pin_spec(), platform)} mokata setup claude "
            f"--mode {mode_spec(mode).name} --plugin")


def finish_commands_for(mode: str, *, packages_missing: bool, mokata_on_path: bool,
                        via_plugin: bool = False,
                        install_line_fn: Optional[Callable[[str], str]] = None,
                        platform: Optional[str] = None) -> Tuple[str, ...]:
    """The commands that take this machine to `mode`'s end state, in order.

    A package is missing, or there is no `mokata` CLI -> first the ONE install line: the README's
    `uv tool install "mokata[<extra>]"` on a plugin-only machine or from the plugin's uvx env
    (whose installer is not the user's), else the line for the installer that owns this env.
    Then always the setup that detects and wires."""
    extra = mode_spec(mode).extra
    if via_plugin:
        # 0.0.26 L8 — the plugin route: ONE command, the plugin's pinned environment (it carries
        # every `full` package), `--plugin` so it wires the mode and leaves the plugin's hooks and
        # MCP server as the harness wiring. Works in bash, zsh, PowerShell and cmd alike.
        return (plugin_setup_command(mode, platform=platform),)
    cmds: List[str] = []
    if packages_missing or not mokata_on_path:
        if via_plugin or not mokata_on_path:
            from .install_route import quote_arg
            cmds.append(f"uv tool install {quote_arg(f'mokata[{extra}]', platform)}")
        else:
            cmds.append((install_line_fn or _install_line)(extra))
    cmds.append(setup_command_for(mode))
    return tuple(cmds)


def first_session_check(
    root: str,
    *,
    data: Optional[Dict[str, Any]] = None,
    presence: Optional[Dict[str, Callable[[], bool]]] = None,
    mokata_on_path: Optional[bool] = None,
    via_plugin: bool = False,
    install_line_fn: Optional[Callable[[str], str]] = None,
) -> FirstSessionCheck:
    """Check on miss (I-04): does this repo's mode lack anything its CLI-route end state has?

    Read-only, offline, never raises past a narrow set (the caller is a SessionStart hook that must
    never break a session), never installs and never writes. The end state compared against is
    `wire_mode`'s `complete`: every package present, the embedder wired (memory/full),
    code-review-graph pinned (full)."""
    try:
        if data is None:
            from .knowledge.graph_adopt import _load_manifest_data
            data = _load_manifest_data(root)
    except (OSError, ValueError):
        return FirstSessionCheck()
    mode = mode_for_manifest(data)
    if mode is None:
        return FirstSessionCheck()
    spec = mode_spec(mode)
    probes = {**_PRESENCE, **(presence or {})}
    # 0.0.26 L8 — on the plugin route the packages live in the plugin's pinned `mokata[full]`
    # environment, not necessarily in the Python this hook happens to run on (it may be a system
    # Python running the plugin's source). Probing THIS interpreter would report them missing
    # forever; only the repo's WIRING is this route's to check.
    missing: List[str] = [] if via_plugin else [
        _DEP_LABELS[k] for k in mode_dependencies(mode) if not probes[k]()]
    packages_missing = bool(missing)
    if spec.offers_embeddings and not (
            ((data.get("settings") or {}).get("memory") or {}).get("embedder")):
        missing.append("semantic tier switched on (memory.embedder)")
    if spec.offers_graph:
        from .knowledge.graph_adopt import graph_pinned_tool
        if not graph_pinned_tool(data):
            missing.append("code-review-graph pinned as the code graph")
    if not missing:
        return FirstSessionCheck(mode=mode, via_plugin=via_plugin)
    if mokata_on_path is None:
        from .detect import find_command
        mokata_on_path = find_command("mokata") is not None
    return FirstSessionCheck(
        mode=mode, missing=tuple(missing), via_plugin=via_plugin,
        commands=finish_commands_for(mode, packages_missing=packages_missing,
                                     mokata_on_path=mokata_on_path, via_plugin=via_plugin,
                                     install_line_fn=install_line_fn))


__all__ = [
    "ADOPTION_MODES",
    "FirstSessionCheck",
    "finish_commands_for",
    "plugin_setup_command",
    "first_session_check",
    "mode_dependencies",
    "mode_for_manifest",
    "model_fetch_cause",
    "setup_command_for",
    "ModeSpec",
    "ModeWiringResult",
    "mode_names",
    "mode_spec",
    "profile_for_mode",
    "render_quickstart",
    "render_outcome",
    "wire_mode",
    "DepRow",
]
