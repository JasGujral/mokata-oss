"""A7 — `mokata init` onboarding.

First-run experience that makes the framework adoptable:
  1. Detect which known tools are actually installed (A3 detection).
  2. Let the user pick a starting profile (minimal / standard / full).
  3. Scaffold a *valid* committed config: .mokata/manifest.json + constitution.md.

The scaffold is a durable write, so it is **human-gated** (P2): init shows exactly
what it will write and which tools it found, then waits for confirmation. `assume_yes`
is the non-interactive escape hatch (CI, scripted setup); `force` is required to
overwrite an existing config (never silently clobber a committed artifact).
"""

from __future__ import annotations

from .prompt import read_yes_no

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import (
    CONSTITUTION_FILENAME,
    MANIFEST_FILENAME,
    MOKATA_DIR,
    __version__,
)
from .detect import Detector
from .manifest import Manifest
from .profiles import (
    DEFAULT_PROFILE,
    TOOL_CATALOG,
    build_manifest_data,
    profile_names,
)

# A short, prose constitution scaffold. These are governing *articles* (not the
# always-on rules layer, which is capped at 60 lines — G1, a later stage). It is
# committed so it is reviewable (K7) and editable by the team.
DEFAULT_CONSTITUTION = """\
# mokata constitution

The governing articles for this project. Committed, reviewable, and editable. mokata
reads this as the standing contract for how work is done here.

## Article 1 — Human-gate every durable write
Every write to code, memory, or config is staged and approved by a human. Nothing is
written silently or autonomously. (Inviolable — cannot be configured away.)

## Article 2 — Local-first, private by default
Nothing leaves this machine unless a human explicitly wires an external service. No
telemetry. (Inviolable — cannot be configured away.)

## Article 3 — Spec before code; prove completeness
No implementation before an approved spec whose acceptance criteria each map to a
test. RED before GREEN. Correctness is demonstrated with evidence, not asserted.

## Article 4 — Degrade, never break
When a wired tool is absent, fall back to a declared alternative. A missing optional
dependency is never a hard failure.

## Article 5 — Review every decision
Every gate decision, tool call, and durable write is auditable. A human can
reconstruct and walk back any choice the system made.
"""

# Committed under .mokata/ so the runtime split is version-controlled (Stage 24D): the
# committed config (manifest, constitution, an exported stack you choose to commit) stays
# at the .mokata/ root; everything transient/runtime lives under temp_local/ and is ignored.
GITIGNORE_FILENAME = ".gitignore"
DEFAULT_GITIGNORE = """\
# mokata keeps all its data under .mokata/. Committed config (manifest.json,
# constitution.md, an exported stack if you choose to commit it, and the shared design
# vault/) lives at this root; everything transient/runtime — pipeline state, resume
# checkpoints, the freshness index, caches, the SQLite memory store, and the audit ledger —
# lives under temp_local/.
temp_local/
"""


@dataclass
class InitPlan:
    root: str
    profile: str
    manifest_data: Dict
    detected: Dict[str, bool]               # tool_id -> present (whole catalog)
    manifest_path: str
    constitution_path: str
    gitignore_path: str
    overwrites: List[str] = field(default_factory=list)

    def write_files(self) -> List[str]:
        os.makedirs(os.path.join(self.root, MOKATA_DIR), exist_ok=True)
        written: List[str] = []

        manifest = Manifest.from_dict(self.manifest_data, self.manifest_path)
        with open(self.manifest_path, "w", encoding="utf-8") as fh:
            fh.write(manifest.to_json())
        written.append(self.manifest_path)

        # Never overwrite a hand-edited constitution; only scaffold if absent.
        if not os.path.exists(self.constitution_path):
            with open(self.constitution_path, "w", encoding="utf-8") as fh:
                fh.write(DEFAULT_CONSTITUTION)
            written.append(self.constitution_path)

        # The committed ignore rule that keeps temp_local/ out of version control (24D).
        # Only scaffold if absent so a hand-edited ignore is never clobbered.
        if not os.path.exists(self.gitignore_path):
            with open(self.gitignore_path, "w", encoding="utf-8") as fh:
                fh.write(DEFAULT_GITIGNORE)
            written.append(self.gitignore_path)

        return written


@dataclass
class InitResult:
    written: List[str]
    plan: InitPlan
    aborted: bool = False
    message: str = ""


def plan_init(
    root: str,
    profile: str,
    detector: Optional[Detector] = None,
) -> InitPlan:
    """Build (but do not write) the init plan: detect tools, assemble the manifest."""
    if profile not in profile_names():
        raise ValueError(
            f"unknown profile '{profile}'; choose one of {profile_names()}"
        )
    detector = detector or Detector()

    # Detect the *whole* catalog so the user sees their full environment, even tools a
    # given profile won't wire.
    detected = {tid: detector.is_present(tid, tdef) for tid, tdef in TOOL_CATALOG.items()}

    manifest_data = build_manifest_data(profile, __version__)

    mdir = os.path.join(root, MOKATA_DIR)
    manifest_path = os.path.join(mdir, MANIFEST_FILENAME)
    constitution_path = os.path.join(mdir, CONSTITUTION_FILENAME)
    gitignore_path = os.path.join(mdir, GITIGNORE_FILENAME)

    overwrites = [p for p in (manifest_path, constitution_path) if os.path.exists(p)]

    return InitPlan(
        root=root,
        profile=profile,
        manifest_data=manifest_data,
        detected=detected,
        manifest_path=manifest_path,
        constitution_path=constitution_path,
        gitignore_path=gitignore_path,
        overwrites=overwrites,
    )


def render_plan(plan: InitPlan) -> str:
    """Human-readable preview of what init will do (the human-gate surface)."""
    lines: List[str] = []
    lines.append(f"mokata init — profile '{plan.profile}'")
    lines.append("")
    lines.append("Detected tools in this environment:")
    for tid in sorted(plan.detected):
        mark = "present" if plan.detected[tid] else "absent "
        provides = TOOL_CATALOG[tid]["provides"]
        lines.append(f"  [{mark}] {tid}  ({provides})")
    lines.append("")

    caps = plan.manifest_data.get("capabilities", {})
    if caps:
        lines.append("Capabilities this profile wires (declared fallback order):")
        for need, cap in caps.items():
            chain = " -> ".join(cap["fallback"])
            lines.append(f"  {need}: {chain}")
    else:
        lines.append("Capabilities: none (engine-only profile).")
    lines.append("")

    lines.append("Will write:")
    lines.append(f"  {plan.manifest_path}")
    if not os.path.exists(plan.constitution_path):
        lines.append(f"  {plan.constitution_path}")
    if not os.path.exists(plan.gitignore_path):
        lines.append(f"  {plan.gitignore_path}  (ignores temp_local/)")
    if plan.overwrites:
        lines.append("")
        lines.append("WARNING — these already exist and will be overwritten:")
        for p in plan.overwrites:
            lines.append(f"  {p}")
    return "\n".join(lines)


def _default_confirm(prompt: str) -> bool:
    return read_yes_no(prompt)


def init_repo(
    root: str = ".",
    profile: str = DEFAULT_PROFILE,
    assume_yes: bool = False,
    force: bool = False,
    detector: Optional[Detector] = None,
    confirm: Optional[Callable[[str], bool]] = None,
    out: Optional[Callable[[str], None]] = None,
) -> InitResult:
    """Run onboarding end to end. Returns an InitResult; writes only after gating."""
    emit = out or print
    plan = plan_init(root, profile, detector)

    emit(render_plan(plan))

    # Guard a committed artifact: overwriting requires an explicit --force.
    manifest_exists = os.path.exists(plan.manifest_path)
    if manifest_exists and not force:
        return InitResult(
            written=[],
            plan=plan,
            aborted=True,
            message=(
                f"{plan.manifest_path} already exists. Re-run with --force to "
                f"overwrite, or remove it first."
            ),
        )

    # Human-gate the durable write (P2).
    if not assume_yes:
        gate = confirm or _default_confirm
        if not gate("\nWrite this config? [y/N] "):
            return InitResult(
                written=[], plan=plan, aborted=True, message="aborted by user"
            )

    written = plan.write_files()

    # Verify the artifact we just wrote is valid (evidence over claims, P10).
    Manifest.load(plan.manifest_path)

    emit("")
    for path in written:
        emit(f"wrote {path}")
    emit(f"mokata initialized with profile '{plan.profile}'.")
    return InitResult(written=written, plan=plan, message="ok")
