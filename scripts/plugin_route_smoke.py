#!/usr/bin/env python3
"""Plugin-route smoke test (0.0.26 L8, MP.S1) — the launcher contract a fresh install relies on.

Claude Code (checked at 2.1.306) installs a plugin headlessly (`claude plugin marketplace add` +
`claude plugin install`) but `claude mcp list` does not list a plugin's MCP servers without a
logged-in session, and CI has no login and no API key. So after the CI job installs the plugin with
the real CLI, this script drives the plugin's own declared commands exactly as Claude Code spawns
them, with nothing mokata-specific installed on the machine beforehand:

  1. the MCP server — `plugin.json`'s `mcpServers.mokata` command + args, `${CLAUDE_PLUGIN_ROOT}`
     substituted, `CLAUDE_PLUGIN_ROOT`/`CLAUDE_PLUGIN_DATA` exported: it must answer `initialize`
     and `tools/list` over stdio, and record its pinned interpreter in the data dir;
  2. the hooks — `hooks.json`'s SessionStart command through bash, then the secret guard on a
     planted secret: the gate must BLOCK (exit 2) through the pinned runtime;
  3. the CLI launcher — `mokata-launch --version` prints the plugin's version;
  4. doctor — `mokata-launch doctor --plugin` must report every check healthy while the server runs;
  5. (`--full-mode`) the one-line install's END STATE, on the plugin route: in a repo with Python and
     TypeScript, `mokata-launch setup claude --mode full --plugin --yes` must wire every row active
     (no ✗, never "PARTIALLY wired"), `doctor` must report code-review-graph as the live graph,
     model2vec as the semantic tier and no `graph-ts-absent` (install-e2e.yml's own assertions),
     no `.claude/` copy of the wiring is written, and the next SessionStart owes nothing.

`--find-links DIR` lets CI serve the checkout's own wheel to uv (the pinned version may not be on
PyPI yet when the release PR runs); the manifests are used unmodified.

Usage: python scripts/plugin_route_smoke.py --plugin-root <dir> [--find-links <dir>]
Exit 0 = every step passed; 1 = a step failed (the failing step is printed).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

FAKE_SECRET = "AKIAIOSFODNN7EXAMPLE"   # AWS's documented example key: a real secret SHAPE
FIRST_RUN_TIMEOUT = 900                 # seconds: a cold uv cache fetches ~100 packages


def _fail(step: str, detail: str) -> int:
    print(f"FAIL [{step}] {detail}", flush=True)
    return 1


def _substitute(value: str, root: str, data: str) -> str:
    return value.replace("${CLAUDE_PLUGIN_ROOT}", root).replace("${CLAUDE_PLUGIN_DATA}", data)


def _reader(stream, q: "queue.Queue[str]") -> None:
    for line in iter(stream.readline, ""):
        q.put(line)
    q.put("")


def _rpc(proc, q, msg, timeout):
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            line = q.get(timeout=max(0.1, deadline - time.time()))
        except queue.Empty:
            break
        if not line:
            return None
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if data.get("id") == msg.get("id"):
            return data
    return None


def _duplicate_wiring(repo):
    """What the plugin already wires and `setup --plugin` must NOT write again: the MCP
    registration, commands, skills, hooks, the statusline and the MCP grant. The session-worktree
    settings (`worktree.baseRef`, P31) are project settings no plugin can carry, so setup writes
    them on this route too — they are not a second copy of anything."""
    if os.path.exists(os.path.join(repo, ".mcp.json")):
        return ".mcp.json"
    for sub in ("commands", "skills", "agents"):
        if os.path.exists(os.path.join(repo, ".claude", sub)):
            return ".claude/" + sub
    settings = os.path.join(repo, ".claude", "settings.json")
    if os.path.exists(settings):
        with open(settings, encoding="utf-8") as fh:
            data = json.load(fh)
        for key in ("hooks", "statusLine", "mcpServers", "enabledMcpjsonServers"):
            if key in data:
                return ".claude/settings.json:" + key
        allow = (data.get("permissions") or {}).get("allow") or []
        if any(str(a).startswith("mcp__mokata") for a in allow):
            return ".claude/settings.json:permissions.allow"
    return None


def _full_mode(bash, launcher, start_cmd, env, repo) -> int:
    """Step 5 — the plugin route ends where `uv tool install "mokata[full]"` + setup ends."""
    with open(os.path.join(repo, "billing.py"), "w", encoding="utf-8") as fh:
        fh.write("def charge(total):\n    return total\n\n\ndef checkout(total):\n"
                 "    return charge(total)\n")
    with open(os.path.join(repo, "shop.ts"), "w", encoding="utf-8") as fh:
        fh.write("export function charge(t: number) { return t }\n"
                 "export function checkout(t: number) { return charge(t) }\n")
    for argv in (["git", "init", "-q"], ["git", "add", "-A"],
                 ["git", "-c", "user.email=ci@example.invalid", "-c", "user.name=ci", "commit",
                  "-qm", "init"]):
        subprocess.run(argv, cwd=repo, check=True, env=env)
    res = subprocess.run([bash, launcher, "setup", "claude", "--mode", "full", "--plugin", "--yes",
                          "--path", "."], capture_output=True, text=True, encoding="utf-8",
                         env=env, cwd=repo, timeout=900)
    out = res.stdout + res.stderr
    print(out, flush=True)
    if res.returncode != 0 or "✗ " in out or "PARTIALLY wired" in out:
        return _fail("full-mode", f"setup --plugin did not wire every row (exit {res.returncode})")
    dup = _duplicate_wiring(repo)
    if dup:
        return _fail("full-mode", "setup --plugin wrote a second copy of the harness wiring: " + dup)
    res = subprocess.run([bash, launcher, "doctor", "--path", "."], capture_output=True,
                         text=True, encoding="utf-8", env=env, cwd=repo, timeout=900)
    out = res.stdout + res.stderr
    print(out, flush=True)
    for want in ("code graph active (code-review-graph)", "semantic: model2vec"):
        if want not in out:
            return _fail("full-mode", f"doctor does not report `{want}`")
    if "graph-ts-absent" in out:
        return _fail("full-mode", "doctor says the TypeScript parser is absent")
    payload = json.dumps({"cwd": repo, "session_id": "smoke-2", "source": "startup",
                          "hook_event_name": "SessionStart"})
    res = subprocess.run([bash, "-c", start_cmd], input=payload, capture_output=True, text=True,
                         encoding="utf-8", env=env, cwd=repo, timeout=300)
    if "mode is not complete" in res.stdout:
        return _fail("full-mode", "the next SessionStart still says full mode is not complete")
    print("ok [full-mode] graph + semantic + TypeScript live on the plugin route", flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plugin-root", required=True)
    ap.add_argument("--find-links", default=None)
    ap.add_argument("--full-mode", action="store_true",
                    help="also prove the full-mode end state (needs huggingface.co for the model)")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.plugin_root)
    work = tempfile.mkdtemp(prefix="mk-plugin-smoke-")
    data = os.path.join(work, "plugin-data")
    os.makedirs(data)
    repo = os.path.join(work, "a repo with spaces")
    os.makedirs(repo)

    with open(os.path.join(root, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    version = manifest["version"]
    spec = manifest["mcpServers"]["mokata"]
    pin = f"mokata[full]=={version}"
    if pin not in spec.get("args", []):
        return _fail("manifest", f"MCP args {spec.get('args')} do not pin {pin}")
    print(f"plugin {version} at {root}; MCP: {spec['command']} {' '.join(spec['args'])}", flush=True)

    env = dict(os.environ)
    env.update(CLAUDE_PLUGIN_ROOT=root, CLAUDE_PLUGIN_DATA=data, CLAUDE_PROJECT_DIR=repo)
    for k in ("PYTHONPATH", "MOKATA_HOOK", "MOKATA_PYTHON"):
        env.pop(k, None)
    if args.find_links:
        env["UV_FIND_LINKS"] = os.path.abspath(args.find_links)

    # 1 — the MCP server, spawned the way Claude Code spawns a plugin stdio server: no shell.
    cmd = [_substitute(spec["command"], root, data)] + [
        _substitute(a, root, data) for a in spec.get("args", [])]
    exe = shutil.which(cmd[0], path=env.get("PATH"))
    if not exe:
        return _fail("mcp", f"`{cmd[0]}` is not on PATH — the plugin route needs uv")
    t0 = time.time()
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW — what a GUI harness does
    proc = subprocess.Popen([exe] + cmd[1:], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env,
                            cwd=repo, **kw)
    q: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=_reader, args=(proc.stdout, q), daemon=True).start()
    errq: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=_reader, args=(proc.stderr, errq), daemon=True).start()
    try:
        init = _rpc(proc, q, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "mokata-plugin-smoke", "version": "1"}}}, FIRST_RUN_TIMEOUT)
        if not init or "result" not in init:
            err = "".join(list(errq.queue))[-2000:]
            return _fail("mcp", f"no initialize response (stderr tail: {err})")
        print(f"ok [mcp] initialize answered after {time.time() - t0:.1f}s "
              f"(server {init['result'].get('serverInfo')})", flush=True)
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()
        tools = _rpc(proc, q, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, 120)
        names = {t.get("name") for t in ((tools or {}).get("result") or {}).get("tools", [])}
        if "doctor" not in names:
            return _fail("mcp", f"tools/list lacks `doctor` ({sorted(names)[:10]}…)")
        print(f"ok [mcp] tools/list: {len(names)} tools", flush=True)

        record = os.path.join(data, f"runtime-{version}.txt")
        if not os.path.isfile(record):
            return _fail("mcp", f"the server did not record its runtime at {record}")
        with open(record, encoding="utf-8") as fh:
            pinned = fh.read().strip()
        if not os.path.isfile(pinned):
            return _fail("mcp", f"recorded runtime {pinned} does not exist")
        print(f"ok [mcp] pinned runtime recorded: {pinned}", flush=True)

        # 2 — the hooks, through bash exactly as hooks.json declares them.
        bash = shutil.which("bash", path=env.get("PATH"))
        if not bash:
            return _fail("hooks", "no bash (Windows needs Git for Windows — Claude Code requires it)")
        with open(os.path.join(root, "src", "mokata", "hooks", "hooks.json"), encoding="utf-8") as fh:
            hooks = json.load(fh)["hooks"]
        start_cmd = _substitute(hooks["SessionStart"][0]["hooks"][0]["command"],
                                root.replace("\\", "/"), data)
        payload = json.dumps({"cwd": repo, "session_id": "smoke", "source": "startup",
                              "hook_event_name": "SessionStart"})
        res = subprocess.run([bash, "-c", start_cmd], input=payload, capture_output=True,
                             text=True, encoding="utf-8", env=env, cwd=repo, timeout=300)
        if res.returncode != 0:
            return _fail("hooks", f"SessionStart exit {res.returncode}: {res.stderr[-800:]}")
        seen = os.path.join(data, "hooks-seen.json")
        if not os.path.isfile(seen):
            return _fail("hooks", "SessionStart did not stamp hooks-seen.json")
        with open(seen, encoding="utf-8") as fh:
            stamp = json.load(fh)
        if os.path.normcase(os.path.abspath(stamp.get("python", ""))) != \
                os.path.normcase(os.path.abspath(pinned)):
            return _fail("hooks", f"the hook ran on {stamp.get('python')}, not the pinned {pinned}")
        print(f"ok [hooks] SessionStart ran on the pinned runtime", flush=True)

        guard = next(h["hooks"][0]["command"] for h in hooks["PreToolUse"]
                     if "secret-guard" in h["hooks"][0]["command"])
        guard = _substitute(guard, root.replace("\\", "/"), data)
        res = subprocess.run([bash, "-c", f'{guard} --text "{FAKE_SECRET}" --path config.py'],
                             capture_output=True, text=True, encoding="utf-8", env=env, cwd=repo,
                             stdin=subprocess.DEVNULL, timeout=120)
        if res.returncode != 2:
            return _fail("hooks", f"secret-guard did not BLOCK (exit {res.returncode}): "
                                  f"{res.stderr[-800:]}")
        print("ok [hooks] secret-guard blocks a planted secret (exit 2)", flush=True)

        # 3 — the CLI launcher.
        launcher = os.path.join(root, "src", "mokata", "hooks", "mokata-launch")
        res = subprocess.run([bash, launcher.replace("\\", "/"), "--version"], capture_output=True,
                             text=True, encoding="utf-8", env=env, cwd=repo, timeout=300)
        if res.returncode != 0 or version not in res.stdout:
            return _fail("launcher", f"exit {res.returncode}: {res.stdout} {res.stderr[-800:]}")
        print(f"ok [launcher] {res.stdout.strip()}", flush=True)

        # 4 — doctor, while the server runs.
        res = subprocess.run([bash, launcher.replace("\\", "/"), "doctor", "--plugin",
                              "--plugin-root", root], capture_output=True, text=True,
                             encoding="utf-8", env=env, cwd=repo, timeout=300)
        print(res.stdout, flush=True)
        if res.returncode != 0:
            return _fail("doctor", f"exit {res.returncode}: {res.stderr[-800:]}")
        print("ok [doctor] plugin route healthy", flush=True)

        if args.full_mode:
            rc = _full_mode(bash, launcher.replace("\\", "/"), start_cmd, env, repo)
            if rc:
                return rc
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
    print("PASS plugin route", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
