"""doctor / baseline / config — diagnose the config, report the baseline test suite, and get/set backend config (set is human-gated)."""
from __future__ import annotations

import argparse
import sys

from ._common import (
    ConfigError,
    Surface,
    config_cmd,
    diagnose,
    ManifestError,
    _load_surface,
)


def cmd_doctor(args: argparse.Namespace) -> int:
    from ..legibility import _color_enabled
    # DOC-ONBOARD — `--wiring`: the WIRING-ONLY check, answered BEFORE a Surface is loaded.
    # This is what a user runs the instant a new package lands (`mokata update`'s follow-up) and
    # when the wiring is the broken thing; demanding an initialized `.mokata/` first would make
    # the diagnostic unavailable in exactly the cases it exists for. Read-only.
    if getattr(args, "wiring", False):
        from ..govern.doctor import wiring_check_lines
        ok, lines = wiring_check_lines(root=args.path, home=getattr(args, "home", None),
                                       ascii_only=not _color_enabled())
        for line in lines:
            print(line)
        return 0 if ok else 1
    # 0.0.26 L8 (MP.S1) — `--plugin`: the plugin route's health, answered BEFORE a Surface is
    # loaded (a plugin-only user may not have initialized any repo yet). Read-only; what
    # `/mokata:doctor` runs through the plugin's pinned launcher.
    if getattr(args, "plugin", False):
        from ..plugin_cache import read_plugin_root
        from ..plugin_runtime import plugin_route_report
        root = getattr(args, "plugin_root", None) or read_plugin_root()
        rep = plugin_route_report(root)
        if not root:
            print("mokata plugin route: no plugin root recorded (~/.mokata/plugin-root) — the "
                  "plugin's SessionStart hook has not run on this machine; pass --plugin-root.")
        for line in rep.lines(ascii_only=not _color_enabled()):
            print(line)
        unresolved = _pin_does_not_resolve(root, rep)
        if unresolved:
            print(unresolved)
        return 0 if (rep.ok and root) else 1
    # 0.0.29 H4 — `--fix`: the plan, ONE human yes (or `--yes`), then rotate / re-bake / stop / reset,
    # ledgered. Answered before a Surface loads: the files it repairs exist in an uninitialised repo too.
    if getattr(args, "fix", False):
        return cmd_fix(args)
    surface = _load_surface(args.path)
    report = diagnose(surface)
    # DB.S1 — the DSN DEEP-CHECK. For a TEAM-connected repo, classify the shared-DB connection into
    # NAMED, secret-free findings (driver / network / auth / pooler / schema-version), each with its
    # concrete fix and the env-var NAME (never the DSN value). Reuses `teamdb.probe` (bounded ≤500ms,
    # fail-closed — never a second probe) + `dsn_inspect` (DB.S0 shape). A local / no-DSN repo is
    # SILENT (None — zero probe, zero noise; P8). Computed here, BEFORE `report.render()`, so its rich
    # section prints separately below while its findings feed `report.ok` further down.
    from .. import db_doctor
    db_check = db_doctor.deep_check(surface)
    if db_check is not None:
        # Fold each DSN finding into `report.findings` BEFORE render, so the overall
        # status line AND the exit code both derive from the SAME `report.ok` — a hard
        # failure (driver / network / auth / schema-broken → error) reads as PROBLEMS FOUND
        # and exits non-zero; pooler + an in-range version difference are warnings (OK). The
        # terse `summary` is the table cell; the full fix renders in the section below.
        from ..govern.doctor import DoctorFinding
        for f in db_check.findings:
            report.findings.append(DoctorFinding(f.severity, f.code, f.summary))
    # Colour + a Unicode box only on a real TTY (NO_COLOR unset); a piped / redirected /
    # NO_COLOR run degrades to a clean plain-ASCII table with zero escape codes.
    color = _color_enabled()
    print(report.render(ascii_only=not color, color=color))
    # MCP wiring — registered? enabled? permitted? CONNECTED? — via the SAME shared reporter as
    # `mokata mcp status` (mcp_admin.full_status) so the two can't drift. Informational: it never
    # changes doctor's ok/exit (derived from `report.ok`); a broken/ungranted server prints its
    # named fix (`mokata mcp install`) but doesn't fail doctor.
    from .. import mcp_admin
    print("")
    for line in mcp_admin.full_status(root=args.path,
                                      home=getattr(args, "home", None)).lines:
        print(line)
    from ..ide import trust as ide_trust
    trust_line = ide_trust.status_line(args.path)
    if trust_line:
        print(trust_line)
    # 0.0.28 C1 — the private set's verdicts (informational: never changes doctor's ok/exit). A file
    # on a volume that cannot hold privacy is SAID, never claimed private. 0.0.29 H4: what `--fix` would
    # change is shown as STAGED lines; plain `doctor` changes nothing.
    for line in privacy_lines(args.path):
        print(line)
    import os
    for line in plan_lines(fix_plan(os.path.realpath(args.path)), staged=True):
        print(line)
    # B-VER — the version-parity finding (parity + scope/plugin shadow), the SAME shared reporter
    # `mokata mcp status` uses. Informational: doctor's ok/exit stays derived from `report.ok`,
    # never from parity. This is the MCP-server version axis — a DIFFERENT axis than DB.S1's shared
    # schema-VERSION finding just below (which mokata binary Claude Code launches vs which shared
    # schema the DB carries); the two are kept as distinct findings, never conflated.
    for line in mcp_admin.parity_lines(root=args.path, home=getattr(args, "home", None),
                                       quiet_when_ok=False):
        print(line)
    # DB.S1 — the DSN deep-check SECTION: the full, actionable `database (team DSN)` block for a
    # team-connected repo (each finding names its axis, the concrete fix, and the env-var NAME —
    # never the DSN value). Its findings already fed `report.ok` above; this is the human-facing
    # rich view. Silent on a local / no-DSN repo (P8). The SCHEMA-VERSION axis here is distinct from
    # the MCP-server version parity above — different axes, never conflated.
    if db_check is not None:
        print("")
        for line in db_check.render_lines(ascii_only=not color):
            print(line)
    # B-SKILLS — the skills-visibility finding: are mokata's Agent Skills + `/`-commands actually
    # wired in THIS root (a new session on a worktree / fresh checkout shows an empty `/` menu),
    # and if present, the restart hint that answers the Claude-Code-side caching case. Same shared
    # reporter style as parity_lines; informational — never changes doctor's ok/exit (derived from
    # `report.ok` above). Loud when wrong; on the OK path it shows the "visible ✓ + restart" line.
    from .. import skills_visibility as _skills_visibility
    for line in _skills_visibility.skills_visibility_lines(
            root=args.path, home=getattr(args, "home", None), quiet_when_ok=False):
        print(line)
    # MCP-R.D2 (B-AMEND-STUCK / UX-STUCK) — "what is waiting on YOU". The returning-user answer to
    # the worst UX in the product: a run that is regressed with an amendment pending looks wedged
    # from the outside, and until now doctor — the command you run when things look wedged — said
    # nothing about it. Same shared-reporter style as parity_lines / skills_visibility_lines.
    #
    # INFORMATIONAL, and emphatically so: a pending proposal is the gate WORKING (P2), not a
    # problem. It is never a DoctorFinding and never touches `report.ok`, so doctor's exit stays
    # derived from `report.ok` alone — exactly as it does for the MCP/parity/skills sections above.
    # Secret-safe: names the tool, the id, and the commands; never the proposal's summary/preview.
    from .. import awaiting as _awaiting
    for line in _awaiting.pending_lines(args.path, quiet_when_ok=False):
        print(line)
    # The LIVENESS line. D0 already made "no MCP call hangs past its budget" true (the R1 wall-clock
    # timeout + per-class budgets); this only tells the human that guarantee EXISTS, so a slow call
    # reads as bounded rather than as the hang it used to be. Referencing D0, not rebuilding it.
    for line in _awaiting.liveness_lines():
        print(line)
    # DB.S4 — the RETRIEVAL STACK: which engines actually rank a recall in this repo (semantic:
    # model2vec / hashing / off; lexical: fts5 / tsvector / jaccard). Retrieval quality used to be
    # invisible — two installs could both read "memory: ok" while one ranked by meaning and the
    # other by token-hash — so this says which, and labels the hashing tier as NOT semantic.
    #
    # INFORMATIONAL, like the MCP/parity/skills/awaiting sections above: every state it prints is a
    # supported configuration (hashing + jaccard is a working zero-dep install), so it emits no
    # DoctorFinding and doctor's ok/exit stays derived from `report.ok` alone.
    from ..memory import tier_report
    print("")
    for line in tier_report.retrieval_lines(surface, ascii_only=not color):
        print(line)
    # R13 — the full pass/degraded/fail coverage matrix is opt-in (`--matrix`) so the default
    # problem summary stays lean. It's informational: doctor's ok/exit stays derived from
    # `report.ok` above, never from the matrix. Read-only, degrade-clean.
    if getattr(args, "matrix", False):
        from ..govern import coverage_matrix
        print(coverage_matrix(surface).render(ascii_only=not color, color=color))
    return 0 if report.ok else 1


#: How long the plugin-route doctor waits for `uvx` to answer whether the pin resolves. It is a
#: network resolve (not the full install): long enough for a slow index, never forever.
PIN_PROBE_TIMEOUT_SECONDS = 120.0


def _pin_does_not_resolve(root, rep) -> "str | None":
    """0.0.28 I1 (`PLUGIN-PIN-BEFORE-PUBLISH`, H8). When the plugin route has no pinned environment
    yet, ask uv whether the plugin's pin RESOLVES at all — the field failure was `No solution found …
    no version of mokata[full]==0.0.27`: the plugin pinned a version PyPI did not have yet, and the
    server never started. Returns the doctor line naming that cause and its fix, or None (healthy,
    not checkable, or failing for another reason uv's own error names).

    Runs only when "package resolved" failed, so a healthy doctor does no network read."""
    import subprocess
    from .. import quiet_spawn
    from ..legibility import _color_enabled
    from ..plugin_runtime import UV_PYTHON, _manifest_pin, _plugin_manifest, find_uvx
    if not root or any(c.ok for c in rep.checks if c.label == "package resolved"):
        return None
    pin = _manifest_pin(_plugin_manifest(root))
    uvx = find_uvx()
    if not pin or not uvx:
        return None
    argv = [uvx, "--python", UV_PYTHON, "--from", pin, "mokata-mcp", "--version"]
    try:
        # Through the process door: uvx is a tree (uv → python), ended whole on timeout.
        rc, _out, err = quiet_spawn.run_jobbed(argv, timeout=PIN_PROBE_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None
    if rc != 0 and ("No solution found" in err or "no version of" in err):
        return (f"  {'!!' if not _color_enabled() else '✗'} pinned `{pin}` does not resolve on "
                f"the package index — the plugin was published before its release reached PyPI\n"
                f"      fix: update the plugin, or wait for the release to reach PyPI")
    return None


def cmd_baseline(args: argparse.Namespace) -> int:
    # Stage 34B — report the test suite green/red at baseline; degrade-clean if no command
    # is known (mokata never guesses a test framework). Read-only diagnostic.
    from ..baseline import baseline_command, baseline_status, silence_seconds
    manifest = None
    if Surface.is_initialized(args.path):
        try:
            manifest = Surface.load(args.path).manifest
        except (ConfigError, ManifestError):
            manifest = None
    from ..project_env import settings_python
    cmd = baseline_command(manifest, override=args.cmd)
    result = baseline_status(cmd, cwd=args.path, silence=silence_seconds(manifest),
                             settings_python=settings_python(manifest))
    print(result.render())
    # green and unknown exit 0 (unknown = no command, degrades clean); red AND not-run exit 1 —
    # a suite the door could not run (no job, no runner) proves nothing, so it is not a pass.
    return 0 if result.ok else 1


def cmd_config(args: argparse.Namespace) -> int:
    # Stage 24A — read/update backend config in the committed manifest. `get` is
    # read-only; `set` is human-gated (preview + confirm; secrets are a hard block).
    # RT.S3 A3 — `wizard` is an interactive, gated walk that routes every change through
    # the SAME `config_set` write path (never a second authority).
    try:
        if args.action == "wizard":
            from ..config_wizard import run_wizard
            run_wizard(args.path)
            return 0
        if args.action in ("get", "set") and not args.key:
            print(f"error: `config {args.action} <key>` requires a key", file=sys.stderr)
            return 2
        if args.action == "get":
            found, val = config_cmd.config_get(args.path, args.key)
            if not found:
                print(f"{args.key}: (unset)")
                return 1
            import json as _json
            print(_json.dumps(val))
            return 0
        # set
        if args.value is None:
            print("error: `config set <key> <value>` requires a value",
                  file=sys.stderr)
            return 2
        # config_set prints its own preview / rejection detail; we add only the result.
        res = config_cmd.config_set(args.path, args.key, args.value,
                                    assume_yes=args.yes)
        if res.committed:
            print(f"set {res.key}")
            return 0
        return 1
    except config_cmd.ConfigCommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def private_set_files(root: str) -> "list":
    """The private set's files that exist for `root` (0.0.28 C1): the IDE server and bridge records, the
    workspace-trust record, and every `.claude/settings.local.json` mokata baked a hook token into —
    the repo's own and each copy Claude Code made into a session worktree (`.worktreeinclude`)."""
    import glob
    import os
    from .. import winacl
    from ..ide import bus, server, trust
    out = [p for p in (server.record_path(root), bus.bridge_path(root), trust.record_path(root))
           if os.path.isfile(p)]
    claude = os.path.join(root, ".claude")
    for p in [os.path.join(claude, "settings.local.json")] + sorted(
            glob.glob(os.path.join(glob.escape(claude), "worktrees", "*", ".claude",
                                   "settings.local.json"))):
        got = winacl.read_record(p)                     # a regular file only, never blocking on a FIFO
        if got is not None and got[1] is not None and \
                bus.HOOK_HEADER in got[1].decode("utf-8", "replace").lower():
            out.append(p)
    return out


def _examine(path: str):
    """`(state, verdict, raw)` of one private-set file, from ONE handle (`winacl.read_record`, never
    raises): `absent`, `odd` (not a regular file this user can open), `private`, `foreign`, `noacl` (ours,
    on a volume that cannot hold privacy) or `weak` (ours, and others can get in)."""
    import os
    from .. import winacl
    got = winacl.read_record(path, ask=True)
    if got is None:
        return ("odd" if os.path.lexists(path) else "absent"), None, None
    v, raw = got
    if v.private:
        return "private", v, raw
    if not v.owner_is_me:
        return "foreign", v, raw
    if not v.holds:
        return "noacl", v, raw
    return "weak", v, raw


def privacy_lines(root: str) -> "list":
    """`mokata doctor`'s file-privacy section: one line per private-set file with its verdict, read by
    `winacl.read_record` (never inferred): ONE open, no FIFO wait. READ-ONLY (0.0.29 H4): what `--fix`
    would do is the STAGED plan (`fix_plan` / `plan_lines`), applied only by `mokata doctor --fix` on a
    human's yes. A file owned by someone else is named and never touched; there is no delete remedy."""
    import os
    from .. import fs_identity, winacl
    files = private_set_files(root)
    lines = []
    if files:
        lines += ["", "File privacy (tokens and workspace trust):"]
    for p in files:
        state, v, _raw = _examine(p)
        if state in ("odd", "absent"):
            lines.append(f"  {p}: REFUSED — not a regular file this user can open; never trusted and "
                         f"never changed")
        elif state == "private":
            lines.append(f"  {p}: private")
        elif state == "foreign":
            lines.append(f"  {p}: FOREIGN — not yours, never trusted and never changed ({v.reason})")
        elif state == "noacl":
            lines.append(f"  {p}: NOT private — {winacl.NO_ACCESS_CONTROL}")
        else:
            lines.append(f"  {p}: WEAK ({v.reason}) — `mokata doctor --fix` shows what it would change")
    if not fs_identity.identity_known(root):
        lines.append(f"  {os.path.realpath(root)}: this volume gives the repository folder no file "
                     f"identity — whether a path is inside it is decided by its spelling alone")
    return lines


# ------------------------------------------------------------------------- doctor --fix (0.0.29 H4)
#
# `DOCTOR-FIX-KEEPS-WEAK-TOKEN`. `--fix` used to rewrite a weak file with its EXACT bytes: a hook token
# others could read stayed in force, and a "trusted" someone else could have written became a real one.
# Now: a read-only PLAN first (`fix_plan`), shown before anything changes; applied only on a human's
# yes at a terminal or `--yes` (never from MCP or `/mokata:doctor`, which only show it); one setup-ledger
# record of what was done. A weak token is ROTATED and re-baked into every settings file that carries
# it; an exposed IDE is stopped only after it proved over its own ping (and the OS's socket table)
# which process it is; a weak trust record is reset to not trusted.

FIX_QUESTION = "Apply these changes?"
RESTART_LINE = ("running Claude Code sessions in this repo must be restarted to pick up the new hook "
                "token; until then their gated calls are refused")


class FixStep:
    """One staged change. `kind`: `rotate` · `rebake` · `stop-ide` · `reset-trust` · `refuse`."""

    def __init__(self, kind: str, path: str, why: str = "", **extra) -> None:
        self.kind, self.path, self.why = kind, path, why
        self.files = list(extra.pop("files", []))          # rotate / rebake: settings files rewritten
        self.foreign = dict(extra.pop("foreign", {}))      # file -> its non-mokata hook entries
        self.second_yes = list(extra.pop("second_yes", []))  # files others could WRITE, with foreign hooks
        self.digests = dict(extra.pop("digests", {}))      # file -> sha256 the plan was built from
        self.extra = extra                                  # stop-ide: port, token, pid, pages, terminal

    def __repr__(self) -> str:          # never the token
        return f"FixStep({self.kind!r}, {self.path!r})"


def _others_may_write(path: str, v) -> bool:
    """Could someone else have WRITTEN this weak file of ours? POSIX: a group/other write bit, another
    hard link, or an ACL entry. Windows: assumed yes (the DACL is not parsed for which right)."""
    import os
    import stat as _stat
    if os.name == "nt":
        return True
    try:
        st = os.lstat(path)
    except OSError:
        return True
    return bool(_stat.S_IMODE(st.st_mode) & 0o022 or st.st_nlink > 1
                or "ACL" in (v.reason if v is not None else ""))


def _json(raw):
    import json
    try:
        got = json.loads(raw.decode("utf-8")) if raw is not None else None
    except (UnicodeDecodeError, ValueError):
        return None
    return got if isinstance(got, dict) else None


UNSTABLE = "changed-while-the-plan-was-read"   # a digest no file can match: the apply refuses


def _shown(path: str, read):
    """`(read(path), digest)` — the digest is taken BEFORE what the plan shows is read from the file, so
    any byte that arrives after it (during the read, or before the apply) makes the apply's check fail
    (round 2, M3: consent is to the bytes that were shown, never to bytes that arrived after)."""
    from ..harness_setup import file_digest
    before = file_digest(path)
    return read(path), before


def _settings_files(root: str) -> "list":
    from ..ide import bus, server, trust
    records = {server.record_path(root), bus.bridge_path(root), trust.record_path(root)}
    return [p for p in private_set_files(root) if p not in records]


def ide_ping(port: int, token: str):
    """The IDE's own `/api/ping` answer on 127.0.0.1:`port`, or None. Loopback only, never a redirect."""
    from ..ide.launch import _call
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    if not 0 < port < 65536 or not token:
        return None
    pong = _call(port, str(token), "/api/ping")
    return pong if isinstance(pong, dict) and pong.get("ok") else None


def fix_plan(root: str) -> "list":
    """What `mokata doctor --fix` WOULD do, as `FixStep`s. READ-ONLY: raw reads that never raise, one
    ping of a weak server record's port; nothing is written, signalled or removed."""
    import hashlib
    from .. import harness_setup as HS
    from ..ide import bus, server, trust
    from pathlib import Path
    steps = []
    bridge_p = bus.bridge_path(root)
    b_state, b_v, b_raw = _examine(bridge_p)
    settings = []
    for p in _settings_files(root):
        s_state, s_v, _ = _examine(p)
        if s_state in ("foreign", "odd"):
            steps.append(FixStep("refuse", p, s_v.reason if s_v is not None else
                                 "not a regular file this user can open"))
        else:
            settings.append((p, s_state, s_v))
    if b_state in ("foreign", "odd"):
        steps.append(FixStep("refuse", bridge_p, b_v.reason if b_v is not None else
                             "not a regular file this user can open"))
    else:
        rec = _json(b_raw) if b_state in ("private", "noacl") else None
        weak_settings = [p for p, st, _ in settings if st == "weak"]
        if b_state == "weak" or weak_settings or (settings and not rec):
            why = ([f"the bridge record is weak ({b_v.reason})"] if b_state == "weak" else []) + \
                  [f"{p} is weak ({v.reason})" for p, st, v in settings if st == "weak"] + \
                  (["the bridge record is missing or unreadable"] if b_state != "weak" and not rec
                   else [])
            files = [p for p, _st, _v in settings]
            foreign, digests = {}, {}
            for p in files:
                foreign[p], digests[p] = _shown(p, lambda q: HS.foreign_hooks(Path(q)))
            # A second yes for a file someone else could have WRITTEN that holds foreign hooks: weak
            # with a write route, or on a volume that holds no access control at all (Low 6).
            second = [p for p, st, v in settings if foreign.get(p) and (
                st == "noacl" or (st == "weak" and _others_may_write(p, v)))]
            old = _json(b_raw)
            keep_port = 0
            if old and b_state == "weak" and not _others_may_write(bridge_p, b_v):
                keep_port = old.get("port") if isinstance(old.get("port"), int) else 0
            elif rec:
                keep_port = rec.get("port") if isinstance(rec.get("port"), int) else 0
            steps.append(FixStep("rotate", bridge_p, "; ".join(why), files=files,
                                 foreign={p: f for p, f in foreign.items() if f},
                                 second_yes=second, port=keep_port, digests=digests))
        elif rec:
            stale = [p for p, _st, _v in settings
                     if HS.baked_tokens(Path(p)) != [str(rec.get("token"))]]
            if stale:
                foreign, digests = {}, {}
                for p in stale:
                    foreign[p], digests[p] = _shown(p, lambda q: HS.foreign_hooks(Path(q)))
                digests[bridge_p] = HS.file_digest(bridge_p) if HS.file_digest(bridge_p) == \
                    hashlib.sha256(b_raw).hexdigest() else UNSTABLE
                steps.append(FixStep("rebake", bridge_p, "a settings file carries another token "
                                     "than the bridge record", files=stale,
                                     foreign={p: f for p, f in foreign.items() if f},
                                     digests=digests))
    s_p = server.record_path(root)
    s_state, s_v, s_raw = _examine(s_p)
    if s_state == "weak":
        rec = _json(s_raw) or {}
        port, token = rec.get("port"), str(rec.get("token") or "")
        pong = ide_ping(port, token)
        steps.append(FixStep("stop-ide", s_p, s_v.reason, port=port, token=token,
                             pid=int(pong.get("pid") or 0) if pong else 0,
                             pages=pong.get("pages") if pong else None,
                             terminal=bool(pong.get("terminal")) if pong else False,
                             answered=bool(pong), same_root=bool(pong) and _same_root(pong, root)))
    elif s_state in ("foreign", "odd"):
        steps.append(FixStep("refuse", s_p, s_v.reason if s_v is not None else
                             "not a regular file this user can open"))
    t_p = trust.record_path(root)
    t_state, t_v, _ = _examine(t_p)
    if t_state == "weak":
        steps.append(FixStep("reset-trust", t_p, t_v.reason,
                             written=_others_may_write(t_p, t_v)))
    elif t_state in ("foreign", "odd"):
        steps.append(FixStep("refuse", t_p, t_v.reason if t_v is not None else
                             "not a regular file this user can open"))
    return steps


def _same_root(pong, root: str) -> bool:
    from .. import fs_identity
    got = pong.get("root") if isinstance(pong, dict) else None
    return isinstance(got, str) and bool(got) and fs_identity.same_entry(got, root)


def _text(s: str) -> str:
    """A path or hook command as printable text (never anything a terminal interprets)."""
    return "".join(c if c.isprintable() else "?" for c in str(s))


def plan_lines(steps: "list", staged: bool = False) -> "list":
    """The plan, as the lines a human reads before saying yes (or, `staged`, as `mokata doctor` and the
    read-only surfaces show it)."""
    if not steps:
        return []
    head = ("Staged by `mokata doctor --fix` (nothing changed yet):" if staged
            else "`mokata doctor --fix` will:")
    out = ["", head]
    for s in steps:
        if s.kind == "rotate":
            out.append(f"  ROTATE the hook token ({_text(s.why)}): write a new {_text(s.path)}, and "
                       f"re-bake it into:")
            out += [f"    {_text(p)}" for p in s.files] or ["    (no settings file carries it)"]
            out.append(f"    {RESTART_LINE}")
        elif s.kind == "rebake":
            out.append(f"  RE-BAKE the bridge record's token ({_text(s.why)}) into:")
            out += [f"    {_text(p)}" for p in s.files]
        elif s.kind == "stop-ide":
            if s.extra.get("answered") and s.extra.get("same_root"):
                pages = s.extra.get("pages")
                out.append(f"  STOP the IDE (pid {s.extra.get('pid')}, "
                           f"{pages if pages is not None else '?'} pages open; its embedded terminal "
                           f"sessions end) — only once the OS shows that pid is the process listening "
                           f"on its port: its record {_text(s.path)} is weak ({_text(s.why)})")
            elif s.extra.get("answered"):
                out.append(f"  REMOVE {_text(s.path)} (weak: {_text(s.why)}); the IDE answering on its "
                           f"port serves another repository: nothing is stopped")
            else:
                out.append(f"  REMOVE {_text(s.path)} (weak: {_text(s.why)}); no IDE answers on its "
                           f"port: nothing is stopped")
        elif s.kind == "reset-trust":
            out.append(f"  RESET workspace trust to NOT trusted: {_text(s.path)} is "
                       f"{'writable' if s.extra.get('written') else 'readable'} by others "
                       f"({_text(s.why)})")
        else:
            out.append(f"  LEAVE {_text(s.path)} untouched: not yours or not a regular file "
                       f"({_text(s.why)}); nothing is rotated or adopted from it")
        for p, entries in sorted(s.foreign.items()):
            hold = p in s.second_yes
            out.append(f"    {_text(p)} holds hooks that are not mokata's"
                       + (" — it may hold hooks someone else wrote; they are kept only on a second, "
                          "separate yes at a terminal (`--yes` never answers it)" if hold else "") + ":")
            out += [f"      {_text(e)}" for e in entries]
    return out


def _fix_ledger(root: str, paths, *, stopped, rotated, trust_reset, failed) -> None:
    from ..harness_setup import _record_setup_ledger
    _record_setup_ledger(root, "doctor-fix", paths, stopped=stopped or None, rotated=rotated,
                         trust_reset=trust_reset, failed=sorted(failed) or None)


def apply_fix(root: str, steps: "list", *, second_yes=None, out=print) -> int:
    """Apply a plan the human approved. ORDER (fail closed): stop the exposed IDE (re-pinged: it must be
    the IDE the plan showed) → mint the new record IN MEMORY → re-bake each settings file → write the
    bridge record → reset trust → ONE ledger record. A file that fails is named and the rest go on;
    rc 1 when anything failed. `second_yes(path) -> bool` answers the per-file question for a file others
    could write that holds foreign hooks (None: nobody can answer it — the file is not rewritten)."""
    import os
    from pathlib import Path
    from .. import harness_setup as HS
    from ..ide import bus, launch, trust
    touched, failed = [], []
    stopped, rotated, trust_reset = 0, False, False
    moved = sorted({p for s in steps for p, d in s.digests.items() if HS.file_digest(p) != d})
    if moved:
        # Round 2, M3: the yes was to the plan that was SHOWN. A file that differs now (a hook planted
        # after the plan, say) is never acted on silently: nothing at all is changed.
        for p in moved:
            out(f"{p}: changed since the plan was shown")
        out("nothing was changed — re-run `mokata doctor --fix` and read the new plan")
        return 1
    for s in [s for s in steps if s.kind == "stop-ide"]:
        port, token = s.extra.get("port"), s.extra.get("token") or ""
        pong = ide_ping(port, token)
        if pong is None:
            out(f"no IDE answered on the record's port: nothing was stopped; {s.path} removed")
            _unlink(s.path, touched, failed, out)
            continue
        if not _same_root(pong, root):
            out(f"the IDE on the record's port serves another repository: nothing was stopped; "
                f"{s.path} removed")
            _unlink(s.path, touched, failed, out)
            continue
        pid = int(pong.get("pid") or 0)
        if pid != int(s.extra.get("pid") or 0):
            out(f"the IDE changed since the plan (pid {pid}, not {s.extra.get('pid')}): nothing was "
                f"stopped; re-run `mokata doctor --fix`")
            failed.append(s.path)
            continue
        out(f"waiting for the IDE (pid {pid}) to stop")
        got = launch._stop(pid, root, port=int(port),
                           ping=lambda: ide_ping(port, token) is not None)
        if got == "stopped":
            stopped = pid
            out(f"the IDE (pid {pid}) stopped")
            _unlink(s.path, touched, failed, out)
        elif got == "still-running":
            out(f"the IDE (pid {pid}) did not stop; nothing else was changed for it")
            failed.append(s.path)
        else:
            out(f"the IDE (pid {pid}) could not be proven to be the process on port {port}: nothing "
                f"was signalled; nothing else was changed for it")
            failed.append(s.path)
    for s in [s for s in steps if s.kind in ("rotate", "rebake")]:
        if s.kind == "rotate":
            rec = bus.new_bridge_record(root, int(s.extra.get("port") or 0))
        else:
            from ..errors import RecordRefused
            try:
                rec = bus.read_bridge(root)
            except RecordRefused:
                rec = None
            if not rec:
                out(f"{s.path}: changed since the plan; nothing re-baked — re-run `mokata doctor --fix`")
                failed.append(s.path)
                continue
        baked_ok = True
        for p in s.files:
            if p in s.second_yes and not (second_yes is not None and second_yes(p)):
                out(f"{p}: NOT rewritten — it may hold hooks someone else wrote and they were not "
                    f"approved; its gates refuse until you fix the file, then re-run `mokata doctor "
                    f"--fix`")
                failed.append(p)
                baked_ok = False
                continue
            try:
                HS.rebake_token(Path(p), rec, root, expect=s.digests.get(p))
                touched.append(p)
                out(f"{p}: re-baked")
            except (HS.SetupError, OSError, ValueError) as exc:
                out(f"{p}: could not be re-baked ({exc}); it keeps the old token and its gates "
                    f"refuse — fix the file, then re-run `mokata doctor --fix`")
                failed.append(p)
                baked_ok = False
        if s.kind == "rotate":
            try:
                bus.write_bridge(root, rec)
                touched.append(s.path)
                rotated = True if baked_ok else "partial"
                out(f"{s.path}: new hook token written")
                out(RESTART_LINE)
            except OSError as exc:
                rotated = "partial"
                failed.append(s.path)
                out(f"{s.path}: the new record could NOT be written ({exc}); the re-baked files carry "
                    f"a token no record holds, so their gates refuse — re-run `mokata doctor --fix`")
    for s in [s for s in steps if s.kind == "reset-trust"]:
        res = trust.decide(root, False, "doctor-fix")
        trust_reset = True
        touched.append(s.path)
        how = "writable" if s.extra.get("written") else "readable"
        out(f"workspace trust was reset because the record was {how} by others: re-trust in the IDE"
            + (f" (WARNING: {res['ledger_error']})" if res.get("ledger_error") else ""))
    if touched or failed or stopped:
        _fix_ledger(root, touched, stopped=stopped, rotated=rotated, trust_reset=trust_reset,
                    failed=failed)
    for s in [s for s in steps if s.kind == "refuse"]:
        out(f"{s.path}: left untouched (not yours, or not a regular file)")
    return 1 if failed else 0


def _unlink(path: str, touched, failed, out) -> None:
    import os
    try:
        os.unlink(path)
        touched.append(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        out(f"{path}: could not be removed ({exc})")
        failed.append(path)


def cmd_fix(args: argparse.Namespace) -> int:
    """`mokata doctor --fix`: the plan, then ONE y/N typed by a human at a real terminal, then apply.
    THERE IS NO `--yes` (0.0.29 H-a round 3, coordinator ruling): an agent can strip any marker from its
    environment, so the only consent this command takes is an answer read from an interactive TTY. No
    TTY: the plan is shown and nothing changes. An agent's shell (`prompt.agent_shell`) is refused
    earlier, with its own line — defense in depth, not the gate."""
    import os
    from .. import prompt
    root = os.path.realpath(args.path)
    for line in privacy_lines(root):
        print(line)
    steps = fix_plan(root)
    if not [s for s in steps if s.kind != "refuse"]:
        for line in plan_lines(steps):
            print(line)
        print("mokata doctor --fix: nothing to change")
        return 0
    print("\n".join(plan_lines(steps)))   # shown BEFORE the question, and when nobody can answer it
    signal = prompt.agent_shell()
    if signal:
        print(f"nothing was changed — this is an agent's shell ({signal} is set): the answer must be "
              f"yours, typed at your own terminal. Run `mokata doctor --fix` in your own terminal to "
              f"apply the plan above.")
        return 1
    if not prompt._stdin_is_tty():
        # THE GATE (P2): no terminal, no human answer — and no flag stands in for one.
        print("nothing was changed — applying this plan needs your answer typed at a terminal, and "
              "there is none here. Run `mokata doctor --fix` in your own terminal.")
        return 1
    if not prompt.read_yes_no("", FIX_QUESTION):
        print("nothing was changed — no yes was read at the terminal.")
        return 1
    return apply_fix(root, steps, second_yes=lambda p: bool(prompt.read_yes_no(
        f"{p} may hold hooks someone else wrote (listed above).", "Keep them and rewrite this file?")))


def register(sub, common):
    p_doc = sub.add_parser(
        "doctor", parents=[common],
        help="diagnose the manifest/config (missing deps, conflicts, bad trust)",
    )
    p_doc.add_argument("--wiring", action="store_true",
                       help="check ONLY the harness hook wiring — are mokata's gates wired, "
                            "launchable, and current? (works on an uninitialized repo; exit 1 "
                            "if a gate would not fire OR the wiring is out of date)")
    p_doc.add_argument("--plugin", action="store_true",
                       help="check ONLY the Claude Code plugin route — uv found, the pinned "
                            "package resolved, plugin/package versions match, the MCP server "
                            "running, the hooks firing (works on an uninitialized repo; exit 1 "
                            "if any check fails)")
    p_doc.add_argument("--plugin-root", default=None,
                       help="the plugin checkout to check (default: the one the plugin's "
                            "SessionStart recorded in ~/.mokata/plugin-root)")
    p_doc.add_argument("--matrix", action="store_true",
                       help="also print the full capability coverage matrix "
                            "(pass/degraded/fail); read-only, does not change the exit code")
    p_doc.add_argument("--fix", action="store_true",
                       help="repair mokata's token and trust files others could read or write: show "
                            "the plan (rotate the hook token and re-bake it, stop an exposed IDE, "
                            "reset workspace trust), then apply it only on your answer typed at a "
                            "terminal (there is no --yes); a file someone else owns is never touched")
    p_doc.set_defaults(func=cmd_doctor)

    p_base = sub.add_parser(
        "baseline", parents=[common],
        help="report the test suite green/red at baseline (degrades clean if no command)",
    )
    p_base.add_argument("--cmd", default=None,
                        help="test command to run (else settings.baseline.test_command)")
    p_base.set_defaults(func=cmd_baseline)

    p_config = sub.add_parser(
        "config", parents=[common],
        help="get/set config, or run the gated settings wizard (set/wizard are human-gated)",
    )
    p_config.add_argument("action", choices=("get", "set", "wizard"),
                          help="read a key, set one (preview + confirm), or walk the settings wizard")
    p_config.add_argument("key", nargs="?", default=None,
                          help="dotted manifest key, e.g. tools.sqlite.config.path (get/set)")
    p_config.add_argument("value", nargs="?", default=None,
                          help="value to set (required for 'set')")
    p_config.add_argument("--yes", action="store_true",
                          help="non-interactive; skip the confirmation prompt")
    p_config.set_defaults(func=cmd_config)


__all__ = [
    "cmd_doctor",
    "cmd_baseline",
    "cmd_config",
]
