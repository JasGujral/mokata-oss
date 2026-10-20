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

All consent runs through the DB.S4 primitives (`extras_install.offer_embeddings`) and the
GR.S2 seam (`knowledge.graph_adopt.offer_graph_at_setup`) — zero new ask/install machinery, and
the interactive-only guarantee is preserved: `offer_mode_extras` is a no-op unless the caller
says the session is interactive, so a `--yes`/CI init can never reach `pip`.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

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
            "  Semantic recall rides mokata[embeddings]; mokata offers it once, interactively.",
        ),
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
    ),
}


#: Row statuses (three-state discipline, doc 85 §7g: "not asked", "declined" and "failed" are
#: different facts and never share a representation).
PRESENT = "already present"
INSTALLED = "installed"
FAILED = "failed"
DECLINED = "declined"
DEFERRED = "run after setup"
NOT_ASKED = "not installed (non-interactive)"

_OK = (PRESENT, INSTALLED)

#: The ONE user-scoped decline key for a mode's dependency set (extras_install's no-nag store).
MODE_DEPS_KEY = "mode-dependencies"


@dataclass
class DepRow:
    """One optional dependency and what happened to it."""

    key: str                      # "embeddings" | "code-graph"
    label: str                    # what a human calls it
    spec: str                     # what the installer is given
    status: str = ""
    fix: str = ""                 # the one line that fixes a non-OK row, for THIS installer

    @property
    def ok(self) -> bool:
        return self.status in _OK


@dataclass
class ModeOffersResult:
    """What the post-setup dependency step actually did. `*Result` = produced value (doc 85 §3)."""

    mode: str = ""
    rows: List[DepRow] = field(default_factory=list)
    asked: bool = False           # the ONE question was put to a human
    consented: bool = False
    graph_adopted: bool = False
    embedder_wired: bool = False  # settings.memory.embedder is set — WITHOUT it the semantic
                                  # tier is OFF even with model2vec installed (measured, I-02)
    skipped_reason: str = ""
    notes: List[str] = field(default_factory=list)

    def row(self, key: str) -> Optional[DepRow]:
        return next((r for r in self.rows if r.key == key), None)

    @property
    def embeddings_installed(self) -> bool:
        r = self.row("embeddings")
        return bool(r and r.ok)

    @property
    def complete(self) -> bool:
        """Every dependency the mode wants is usable (and the graph adopted, for full)."""
        if not self.rows:
            return True
        return (all(r.ok for r in self.rows)
                and (self.row("code-graph") is None or self.graph_adopted)
                and (self.row("embeddings") is None or self.embedder_wired))


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


def render_quickstart(mode: str, outcome: Optional["ModeOffersResult"] = None) -> str:
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
                     "one and its fix.")
    return "\n".join([""] + lines)


def render_outcome(outcome: "ModeOffersResult") -> str:
    """The one closing table: each dependency, its status, and — when not OK — the exact line
    that fixes it under THIS installer. Empty for a mode with no dependencies."""
    if not outcome.rows:
        return ""
    w = max(len(r.label) for r in outcome.rows)
    out = ["", f"── {outcome.mode} mode: optional dependencies " + "─" * 20]
    for r in outcome.rows:
        mark = "✓" if r.ok else "✗"
        out.append(f"  {mark} {r.label.ljust(w)}  {r.status}")
        if not r.ok and r.fix:
            out.append(f"      fix: {r.fix}")
    e = outcome.row("embeddings")
    if e is not None and e.ok and not outcome.embedder_wired:
        out.append("  ✗ semantic tier not switched on — "
                   "`mokata config set settings.memory.embedder auto`")
    g = outcome.row("code-graph")
    if g is not None and g.ok and not outcome.graph_adopted:
        out.append("  ✗ code-review-graph is installed but not pinned — `mokata graph adopt`")
    for n in outcome.notes:
        out.append(f"  note: {n}")
    return "\n".join(out)


#: What a human calls each dependency — one table, read by the setup rows AND the I-04 check.
_DEP_LABELS: Dict[str, str] = {
    "embeddings": "semantic memory model",
    "code-graph": "code-review-graph (structural)",
}


def _deps_for(mode: str) -> List[DepRow]:
    from .extras_install import fix_command
    from .knowledge.graph_adopt import CRG_SPEC
    from .memory.embed import EMBEDDINGS_EXTRA
    spec = mode_spec(mode)
    rows: List[DepRow] = []
    if spec.offers_embeddings:
        rows.append(DepRow("embeddings", _DEP_LABELS["embeddings"], EMBEDDINGS_EXTRA))
    if spec.offers_graph:
        rows.append(DepRow("code-graph", _DEP_LABELS["code-graph"], CRG_SPEC))
    for r in rows:
        r.fix = fix_command(r.spec)
    return rows


def _importable(mod: str) -> bool:
    from importlib.util import find_spec
    try:
        return find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def _present_embeddings() -> bool:
    """Cheap and OFFLINE: is the package there? Loading the model can fetch ~30 MB, which is
    not something a `--yes` run without `--with-deps` may do (DB.S4 posture)."""
    return _importable("model2vec")


def _probe_embeddings() -> bool:
    """The post-install VERIFY (consent already given): the model actually loads and encodes.
    Raises with the reason when it cannot — the caller puts that reason on the row."""
    from .memory.embed import Model2VecEmbedder
    return bool(Model2VecEmbedder())


#: When the package installed but is not usable, re-running the installer is the wrong fix.
_UNUSABLE_FIX = {
    "embeddings": "the model (~30 MB) downloads from huggingface.co on first use — retry when "
                  "that host is reachable: `mokata doctor`",
    "code-graph": "`mokata doctor` names why code-review-graph will not start",
}

_EMBEDDER_KEY = "settings.memory.embedder"


def _embedder_wired(root: str) -> bool:
    from .knowledge.graph_adopt import _load_manifest_data
    data = _load_manifest_data(root)
    return bool(((data.get("settings") or {}).get("memory") or {}).get("embedder"))


def _wire_embedder(root: str, *, out: Callable[[str], None], ledger: Any) -> bool:
    """`settings.memory.embedder = auto` through the gated config write (consent already given)."""
    from .config_cmd import config_set
    res = config_set(root, _EMBEDDER_KEY, "auto", assume_yes=True, out=out, ledger=ledger)
    return bool(res.committed)


def _probe_graph() -> bool:
    from .knowledge.graph_adopt import _crg_importable
    return _crg_importable()


#: (present?, verified?) per dependency — presence is offline; verification may load a model.
_PROBES: Dict[str, Tuple[Callable[[], bool], Callable[[], bool]]] = {
    "embeddings": (_present_embeddings, _probe_embeddings),
    "code-graph": (_probe_graph, _probe_graph),
}


def offer_mode_extras(
    root: str,
    mode: str,
    *,
    interactive: bool,
    with_deps: bool = False,
    ledger: Any = None,
    user_home: Optional[str] = None,
    out: Optional[Callable[[str], None]] = None,
    prompt_fn: Optional[Callable[[str], bool]] = None,
    runner: Optional[Callable[..., Any]] = None,
    probes: Optional[Dict[str, Tuple[Callable[[], bool], Callable[[], bool]]]] = None,
    install_fn: Optional[Callable[..., Any]] = None,
    adopt_fn: Optional[Callable[..., Any]] = None,
    graph_pinned_fn: Optional[Callable[[str], bool]] = None,
    embedder_wired_fn: Optional[Callable[[str], bool]] = None,
    wire_embedder_fn: Optional[Callable[..., bool]] = None,
) -> ModeOffersResult:
    """The mode's optional dependencies: ONE consent, ONE installer run, one honest outcome.

    0.0.25 I-02 (`FULL-DEPS-AUTO-INSTALL`, `INSTALL-WITH-DEPS-FLAG`). Until 0.0.24 full mode
    asked once per extra and installed per extra; now:

      * `seatbelt` — no dependencies; structurally nothing is probed, asked or installed.
      * what is already usable is a `PRESENT` row and is never asked about;
      * consent is `with_deps` (the explicit non-interactive opt-in) OR one interactive
        y/N (fail-closed off a TTY, P2). `--yes` alone is NOT consent to a network install
        (the DB.S4 posture): a non-interactive run without `with_deps` installs nothing and
        each missing row says the one line that would;
      * a decline is recorded once, user-scoped, and never re-asked (no nag);
      * everything missing goes to `extras_install.install_extras` in ONE call — so a uv tool
        env is rebuilt once — and each row is then PROBED, never inferred from the exit code;
      * full mode then pins code-review-graph through the gated `adopt_graph` (consent given).

    DEGRADE_CLEAN: the repo is already wired when this runs; nothing here can undo that."""
    from . import extras_install as X
    emit = out or print
    spec = mode_spec(mode)
    result = ModeOffersResult(mode=mode)
    if not (spec.offers_embeddings or spec.offers_graph):
        return result

    probes = {**_PROBES, **(probes or {})}
    try:
        result.rows = _deps_for(mode)
        for r in result.rows:
            if probes[r.key][0]():
                r.status, r.fix = PRESENT, ""
        missing = [r for r in result.rows if not r.ok]

        pinned = True
        if spec.offers_graph:
            if graph_pinned_fn is None:
                from .knowledge.graph_adopt import _load_manifest_data, graph_pinned_tool
                pinned = bool(graph_pinned_tool(_load_manifest_data(root)))
            else:
                pinned = bool(graph_pinned_fn(root))
            result.graph_adopted = pinned
        wired = True
        if spec.offers_embeddings:
            wired = bool((embedder_wired_fn or _embedder_wired)(root))
            result.embedder_wired = wired
        if not missing and pinned and wired:
            return result

        if with_deps:
            result.consented = True
        elif not interactive:
            result.skipped_reason = ("non-interactive (nothing installed; pass --with-deps "
                                     "to install the mode's dependencies)")
            for r in missing:
                r.status = NOT_ASKED
            return result
        elif X.extra_declined(root, MODE_DEPS_KEY, user_home=user_home):
            for r in missing:
                r.status = DECLINED + " earlier (not re-asked)"
            return result
        else:
            if prompt_fn is None:
                from .prompt import read_yes_no
                prompt_fn = read_yes_no                        # fail-closed off a TTY (P2)
            what = [f"{r.label} ({r.spec})" for r in missing]
            if not pinned and not any(r.key == "code-graph" for r in missing):
                what.append("pin code-review-graph as the code graph")
            if not wired:
                what.append("switch on the semantic memory tier (memory.embedder = auto)")
            cost = "About 360 MB, all local, no API key."
            if any(r.key == "code-graph" for r in missing) or not pinned:
                # Measured 0.0.25 on a 1.2M-line repo: the graph's first index build took ~6 min
                # and wrote ~1 GB. Said up front so a slow first query is expected, not a bug.
                cost += (" The code graph indexes the repo on first use: seconds on a small repo,"
                         " several minutes and up to ~1 GB (in .code-review-graph/, gitignored)"
                         " on a ~1M-line one.")
            question = (f"{mode} mode wants: " + "; ".join(what)
                        + f". {cost} Install now? [y/N] ")
            result.asked = True
            decision = prompt_fn(question)
            result.consented = bool(decision)
            if not result.consented:
                # Only a HUMAN's no is remembered: an unreadable stdin is not an answer (§7g).
                if getattr(decision, "answered_by_human", True):
                    X.record_extra_decline(root, MODE_DEPS_KEY, user_home=user_home)
                if ledger is not None:
                    ledger.record("extra_offer", extra=MODE_DEPS_KEY, decision="declined",
                                  scope="repo")
                for r in missing:
                    r.status = DECLINED
                return result

        if missing:
            install = install_fn or X.install_extras
            res = install([r.spec for r in missing], runner=runner, out=emit)
            if ledger is not None:
                ledger.record("extra_offer", extra=MODE_DEPS_KEY, decision="accepted",
                              installed=bool(getattr(res, "ok", False)), scope="repo")
            for r in missing:
                if getattr(res, "deferred", False):
                    r.status, r.fix = DEFERRED, getattr(res, "fix", "") or r.fix
                elif getattr(res, "ok", False):
                    try:
                        usable, why = bool(probes[r.key][1]()), ""
                    except Exception as exc:          # noqa: BLE001 — caller-side probe
                        usable, why = False, f"{type(exc).__name__}: {exc}"
                    if usable:
                        r.status, r.fix = INSTALLED, ""
                    else:
                        r.status = (FAILED + " — installed, but not usable here"
                                    + (f" ({why})" if why else ""))
                        r.fix = _UNUSABLE_FIX.get(r.key, r.fix)
                else:
                    r.status = FAILED + (f" — {res.message}" if getattr(res, "message", "")
                                         else "")
                    r.fix = getattr(res, "fix", "") or r.fix

        e = result.row("embeddings")
        if e is not None and not wired and (e.ok or e.status.startswith(FAILED + " — installed")):
            # Wire the setting even when the model could not load yet: `auto` degrades to the
            # hashing tier until it can, and switches itself on once the model is cached.
            result.embedder_wired = bool((wire_embedder_fn or _wire_embedder)(
                root, out=emit, ledger=ledger))

        g = result.row("code-graph")
        if g is not None and g.ok and not pinned:
            if adopt_fn is None:
                from .knowledge.graph_adopt import adopt_graph as adopt_fn
            ares = adopt_fn(root, tool="code-review-graph", assume_yes=True, out=emit,
                            ledger=ledger)
            result.graph_adopted = bool(getattr(ares, "committed", False))
    except Exception as exc:                      # noqa: BLE001 — DEGRADE_CLEAN
        # The repo is already wired; an optional dependency step that blows up costs the user
        # the extra tiers, never their setup. Same posture as onboarding.run_wizard.
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
# briefing names the ONE command that reaches the CLI route's end state — `mokata setup claude
# --mode <m>`, which asks ONE human question before it installs anything.
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
    """CRG is launched by COMMAND NAME, so 'present' is what mokata will actually find — PATH or
    mokata's own scripts dir (`detect.find_command`). Never imports it (SessionStart is cheap)."""
    from .detect import find_command
    return find_command("code-review-graph") is not None


#: Offline, import-free presence probes for the SessionStart check (no model load, no import of a
#: heavy package, no subprocess — the hook budget).
_PRESENCE: Dict[str, Callable[[], bool]] = {
    "embeddings": _present_embeddings,
    "code-graph": _present_code_graph,
}


@dataclass(frozen=True)
class FirstSessionCheck:
    """What the SessionStart check found. `*Check` = a read-only verdict (doc 85 §3)."""

    mode: Optional[str] = None
    missing: Tuple[str, ...] = ()       # human labels of what the mode still lacks
    command: str = ""                   # the ONE command that reaches the CLI route's end state
    silenced: str = ""                  # why nothing is said although something is missing
    via_plugin: bool = False            # the briefing is served by the PLUGIN's hook

    @property
    def line(self) -> Optional[str]:
        """The one briefing line, or None when there is nothing to say."""
        if not self.mode or not self.missing or self.silenced:
            return None
        text = (f"⚠ mokata {self.mode} mode is not complete here — missing: "
                f"{', '.join(self.missing)}. To finish it, the human runs ONE command in their "
                f"terminal: `{self.command}` (it asks once before installing anything; answering "
                f"no there stops this note). Do not run it for them.")
        if self.via_plugin:
            # Measured, not assumed: plugin.json starts the MCP server as `uvx --from
            # mokata==<v> mokata-mcp` — an env with no extras — so the plugin route cannot carry
            # the semantic model itself. The command moves this repo to the CLI route (its hooks
            # then own every event and the plugin's stand down — 0.0.23 B3).
            text += (f" It moves this repo to the CLI route (the plugin's uvx MCP server has no "
                     f"optional dependencies); afterwards `claude plugin disable {PLUGIN_ID} "
                     f"--scope local` keeps one route in this repo.")
        return text


def setup_command_for(mode: str, *, mokata_on_path: bool) -> str:
    """The ONE command for `mode`: the CLI route itself. Without a `mokata` CLI (a plugin-only
    machine) it is prefixed with the README's documented install (uv tool, the primary route)."""
    cmd = f"mokata setup claude --mode {mode_spec(mode).name}"
    return cmd if mokata_on_path else f"uv tool install mokata && {cmd}"


def first_session_check(
    root: str,
    *,
    data: Optional[Dict[str, Any]] = None,
    presence: Optional[Dict[str, Callable[[], bool]]] = None,
    mokata_on_path: Optional[bool] = None,
    user_home: Optional[str] = None,
    via_plugin: bool = False,
) -> FirstSessionCheck:
    """Check on miss (I-04): does this repo's mode lack anything its CLI-route end state has?

    Read-only, offline, never raises past a narrow set (the caller is a SessionStart hook that must
    never break a session), never installs and never writes. The end state compared against is
    `offer_mode_extras`'s `complete`: every dependency present, the embedder wired (memory/full),
    code-review-graph pinned (full)."""
    from .extras_install import extra_declined
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
    wanted = ((["embeddings"] if spec.offers_embeddings else [])
              + (["code-graph"] if spec.offers_graph else []))
    missing: List[str] = [_DEP_LABELS[k] for k in wanted if not probes[k]()]
    if spec.offers_embeddings and not (
            ((data.get("settings") or {}).get("memory") or {}).get("embedder")):
        missing.append("semantic tier switched on (memory.embedder)")
    if spec.offers_graph:
        from .knowledge.graph_adopt import graph_pinned_tool
        if not graph_pinned_tool(data):
            missing.append("code-review-graph pinned as the code graph")
    if mokata_on_path is None:
        from .detect import find_command
        mokata_on_path = find_command("mokata") is not None
    silenced = ""
    if missing and extra_declined(root, MODE_DEPS_KEY, user_home=user_home):
        silenced = "the human declined this mode's dependencies (not re-asked)"
    return FirstSessionCheck(mode=mode, missing=tuple(missing),
                             command=setup_command_for(mode, mokata_on_path=mokata_on_path),
                             silenced=silenced, via_plugin=via_plugin)


__all__ = [
    "ADOPTION_MODES",
    "FirstSessionCheck",
    "first_session_check",
    "mode_for_manifest",
    "setup_command_for",
    "ModeSpec",
    "ModeOffersResult",
    "mode_names",
    "mode_spec",
    "profile_for_mode",
    "render_quickstart",
    "render_outcome",
    "offer_mode_extras",
    "DepRow",
]
