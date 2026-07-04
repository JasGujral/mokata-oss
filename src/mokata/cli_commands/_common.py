"""Shared imports + helpers for the cli_commands package.

This module is a re-export hub: the symbols below are imported for the command
modules (`from ._common import ...`), not used here — hence the explicit __all__.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional

from .. import __version__
from ..bootstrap import build_bootstrap
from ..brainstorm import ground, load_approved_approach, render_launch
from ..config import ConfigError, Surface
from .. import config_cmd
# Stage 3d.1: the mcp_admin ↔ harness_setup cycle is broken (shared pieces extracted to
# harness_paths), so this import — previously lazy inside `_cmd_mcp_status` — is now top-level.
from .. import mcp_admin
from ..crossplat import current_user as _current_user
from ..detect import Detector
from ..init import init_repo, plan_init, render_plan
from ..prompt import read_yes_no
from .. import MOKATA_DIR
from ..adapters import (
    AdapterContract,
    MCPRegistry,
    negotiate,
    overlapping_capabilities,
)
from ..brainstorm import PIPELINE_PHASES
from ..compose import SuggestionContext, plan_chain, suggest
from ..execmode import (
    PARALLEL,
    SEQUENTIAL,
    ExecutionChoice,
    resolve_execution_choice,
)
from ..govern import (
    AuditLedger,
    BudgetReport,
    budget_statusline,
    diagnose,
    load_rules,
    plan_reset,
    reset_state,
    validate_caps,
    WriteGate,
    WriteRequest,
)
from ..harness import HARNESS_CAPABILITIES, claude_code_harness
from ..share import SHARE_FILENAME, apply_manifest, export_manifest, load_shared
from ..knowledge import QUERY_KINDS, KnowledgeIndex, KnowledgeLayer, lat_check
from ..manifest import ManifestError
from ..memory import MemoryStore
from ..engine import preview_pipeline
from ..pipeline import ENTRY_PHASES, PhaseError, plan_entry, render_entry
from ..playbook import run_playbook
from ..skills import SKILL_NAMES, SkillNotFound, get_skill, list_skills, render_skill
from ..profiles import DEFAULT_PROFILE, TOOL_CATALOG, profile_names
from ..harness_setup import (
    HARNESSES,
    SCOPES,
    SetupError,
    setup_harness,
    unsetup_harness,
)


def _load_surface(root: str) -> Surface:
    try:
        return Surface.load(root)
    except (ConfigError, ManifestError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)


# Stage 71a — review scoping for the SHARED backends (memory / audit / sessions). A shared DSN can
# host many projects; review DEFAULTS to the current project, with `--all` / `--project` escapes.
_SCOPE_CURRENT = object()          # the default: the current project only


def _review_scope(args: argparse.Namespace):
    """Resolve --all / --project into a scope: `ALL_PROJECTS` (span all), a specific project id, or
    the `_SCOPE_CURRENT` sentinel (the default — the current project)."""
    from ..project import ALL_PROJECTS
    if getattr(args, "all", False):
        return ALL_PROJECTS
    proj = getattr(args, "project", None)
    if proj:
        return proj
    return _SCOPE_CURRENT


def _backend_projects(obj: Any) -> Optional[List[str]]:
    """The distinct project keys a shared backend/transport reports, or None when it can't (a
    local, per-repo backend has no cross-project space). Degrade-clean — never raises."""
    fn = getattr(obj, "list_projects", None)
    if fn is None:
        return None
    try:
        return fn()
    except Exception:
        return None


def _cli_ask(question: str, default: str) -> str:
    # Stage 1b (E8/P2) — fail closed on a non-interactive stdin. When stdin isn't a TTY
    # (CI, piped, closed), never call input() blindly: take the safe default and log to
    # stderr so the decision is visible, not silent.
    if not sys.stdin.isatty():
        print(f"execution: stdin is not a TTY — defaulting to '{default}' without "
              f"prompting.", file=sys.stderr)
        return default
    try:
        return input(f"{question} [{default}] ").strip() or default
    except (EOFError, OSError):
        return default


def _profile_for(path: str) -> str:
    """The project profile if initialized, else a friendly placeholder. Never raises."""
    try:
        if Surface.is_initialized(path):
            return Surface.load(path).manifest.profile
    except Exception:
        pass
    return "(not initialized)"


def _ledger_for(path: str):
    """The audit ledger if the repo is initialized, else None (degrade-clean)."""
    try:
        if Surface.is_initialized(path):
            return AuditLedger.from_mokata_dir(Surface.load(path).mokata_dir)
    except Exception:
        pass
    return None


__all__ = [
    "argparse",
    "os",
    "sys",
    "Any",
    "Dict",
    "List",
    "Optional",
    "__version__",
    "build_bootstrap",
    "ground",
    "load_approved_approach",
    "render_launch",
    "ConfigError",
    "Surface",
    "config_cmd",
    "mcp_admin",
    "_current_user",
    "Detector",
    "init_repo",
    "plan_init",
    "render_plan",
    "read_yes_no",
    "MOKATA_DIR",
    "AdapterContract",
    "MCPRegistry",
    "negotiate",
    "overlapping_capabilities",
    "PIPELINE_PHASES",
    "SuggestionContext",
    "plan_chain",
    "suggest",
    "PARALLEL",
    "SEQUENTIAL",
    "ExecutionChoice",
    "resolve_execution_choice",
    "AuditLedger",
    "BudgetReport",
    "budget_statusline",
    "diagnose",
    "load_rules",
    "plan_reset",
    "reset_state",
    "validate_caps",
    "WriteGate",
    "WriteRequest",
    "HARNESS_CAPABILITIES",
    "claude_code_harness",
    "SHARE_FILENAME",
    "apply_manifest",
    "export_manifest",
    "load_shared",
    "QUERY_KINDS",
    "KnowledgeIndex",
    "KnowledgeLayer",
    "lat_check",
    "ManifestError",
    "MemoryStore",
    "preview_pipeline",
    "ENTRY_PHASES",
    "PhaseError",
    "plan_entry",
    "render_entry",
    "run_playbook",
    "SKILL_NAMES",
    "SkillNotFound",
    "get_skill",
    "list_skills",
    "render_skill",
    "DEFAULT_PROFILE",
    "TOOL_CATALOG",
    "profile_names",
    "HARNESSES",
    "SCOPES",
    "SetupError",
    "setup_harness",
    "unsetup_harness",
    "_load_surface",
    "_SCOPE_CURRENT",
    "_review_scope",
    "_backend_projects",
    "_cli_ask",
    "_profile_for",
    "_ledger_for",
]
