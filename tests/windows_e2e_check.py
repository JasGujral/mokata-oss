"""CAN A USER ACTUALLY RUN MOKATA HERE? — the end-to-end a green unit suite does not answer.

    python tests/windows_e2e_check.py [--keep]

Not a unit test, and deliberately not discoverable as one (the filename does not match
`test*.py`). Its sibling is `tests/hook_execution_check.py`, which exists for the same reason: a
suite full of correctly-spelled assertions proves that the suite agrees with itself. It does not
prove that the installed package starts, that the server answers, or that a write lands.

⭐ WHY THIS EXISTS NOW. Every current mokata user is on Windows. `ci.yml` already runs `mokata
init` + `mokata playbook` on `windows-latest`, so the CLI's happy path is covered — but the MCP
server has NEVER been observed starting on Windows by anything in this repo. `hooks-execute`
proves the hook shims block; `ci.yml`'s MCP step BUILDS the server object in-process and never
spawns it. "The server can be constructed" and "the server answers a client on stdio" are
different facts (doc 85 §7g), and only the second is what a user has.

WHAT IT DRIVES, in the order a user meets it:

    1  the console entry point `mokata-mcp` SPAWNS as a real subprocess on stdio;
    2  it answers `initialize`;
    3  it answers `tools/list` with a plausible tool count — asserted against a FLOOR, not an
       exact number, because the count moves every release and an exact pin would fail for the
       one reason that is not a defect;
    4  `remember` PROPOSES and writes nothing (P2 — the human gate, on the platform where the
       gate has never been exercised);
    5  `mokata approve --yes <id>` — the human's own non-interactive lane — approves it;
    6  the re-call COMMITS;
    7  `recall` RETURNS THE VALUE. ⭐ This is the step the whole file is for: a memory that is
       written under one path spelling and searched for under another comes back empty, and an
       empty recall is indistinguishable from "nothing was remembered". That is precisely the
       failure the NAME/PATH invariant exists to make impossible, and it is invisible to every
       assertion that runs on one platform.

Exit 0 on success; exit 1 with the failing step named. Every step prints, so a CI log reads as a
sequence rather than as a verdict.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

#: The tool count is a FLOOR, never an equality. 61 at the time of writing; a release that adds a
#: tool must not red this, and a release that loses half of them must.
MIN_TOOLS = 40

#: The tools this check actually drives, asserted present by name. A count alone stays green
#: through a swap — the same reason `THE_WORKFLOWS` is a list of names and not a number.
REQUIRED_TOOLS = ("remember", "recall")

SUBJECT = "windows-probe-canary"
VALUE = "the name/path invariant landed and was probed on windows"

READ_TIMEOUT = 30.0


class StepFailed(RuntimeError):
    """A named step did not do what a user needs it to do."""


def _say(step, detail=""):
    print("  %-34s %s" % (step, detail), flush=True)


class Server:
    """The real `mokata-mcp` console entry point, spawned as a subprocess and spoken to on stdio.

    A bounded reader, for the same reason `mcp_admin.handshake` has one: a server that starts and
    then says nothing would otherwise hang this check for the job's whole timeout, and a hang
    reports as an infrastructure problem rather than as the product defect it is.
    """

    def __init__(self, cwd):
        self.proc = subprocess.Popen(
            ["mokata-mcp"], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1)

    def _readline(self):
        holder = {}

        def _read():
            try:
                holder["line"] = self.proc.stdout.readline()
            except Exception as exc:                          # pragma: no cover
                holder["exc"] = exc

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        reader.join(READ_TIMEOUT)
        if reader.is_alive():
            raise StepFailed("the server did not answer within %.0fs — it started and then went "
                             "quiet, which is a different failure from not starting"
                             % READ_TIMEOUT)
        line = holder.get("line")
        if not line:
            err = ""
            try:
                err = (self.proc.stderr.read() or "")[-2000:]
            except Exception:                                 # pragma: no cover
                pass
            raise StepFailed("the server closed its stdout without answering. stderr:\n" + err)
        return line

    def call(self, method, params=None, request_id=1):
        self.proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": request_id, "method": method,
            "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        return json.loads(self._readline())

    def notify(self, method):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def tool(self, name, arguments, request_id):
        """Call a tool and return its decoded JSON payload.

        MCP wraps a tool result in a content envelope of TEXT parts, so the payload arrives as a
        JSON document inside a string. Decoding it here rather than at each call site is what lets
        the steps below read as the user-facing sequence they are."""
        reply = self.call("tools/call",
                          {"name": name, "arguments": arguments}, request_id=request_id)
        if "error" in reply:
            raise StepFailed("tool %r returned an MCP error: %s" % (name, reply["error"]))
        parts = reply.get("result", {}).get("content", [])
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        try:
            return json.loads(text)
        except ValueError:
            raise StepFailed("tool %r did not return JSON: %s" % (name, text[:400]))

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:                                     # pragma: no cover
            try:
                self.proc.kill()
            except Exception:
                pass


def _run(argv, cwd):
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=180)
    return proc


def check(workdir):
    _say("host", "%s / %s / python %s" % (os.name, sys.platform, sys.version.split()[0]))

    # ---- 1 · the CLI scaffolds a repo -----------------------------------------------------
    init = _run(["mokata", "init", "--profile", "standard", "--yes", "--path", workdir], workdir)
    if init.returncode != 0:
        raise StepFailed("`mokata init` exited %d:\n%s\n%s"
                         % (init.returncode, init.stdout[-2000:], init.stderr[-2000:]))
    if not os.path.isdir(os.path.join(workdir, ".mokata")):
        raise StepFailed("`mokata init` reported success and wrote no .mokata/ — the one case a "
                         "zero exit code must never be allowed to mean")
    _say("mokata init", "ok — .mokata/ exists")

    server = Server(workdir)
    try:
        # ---- 2 · the server starts and answers --------------------------------------------
        reply = server.call("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "windows-e2e-check", "version": "0"}}, request_id=1)
        if "result" not in reply:
            raise StepFailed("`initialize` was not answered with a result: %s" % reply)
        server.notify("notifications/initialized")
        _say("mokata-mcp initialize",
             "ok — protocol %s" % reply["result"].get("protocolVersion"))

        # ---- 3 · it lists its tools -------------------------------------------------------
        listed = server.call("tools/list", request_id=2)
        tools = listed.get("result", {}).get("tools", [])
        names = {t.get("name") for t in tools}
        if len(tools) < MIN_TOOLS:
            raise StepFailed("the server listed only %d tools (floor is %d) — it started but is "
                             "not the server a user gets" % (len(tools), MIN_TOOLS))
        missing = [n for n in REQUIRED_TOOLS if n not in names]
        if missing:
            raise StepFailed("the server is missing the tools this check drives: %s" % missing)
        _say("mokata-mcp tools/list", "ok — %d tools" % len(tools))

        # ---- 4 · remember PROPOSES and writes nothing (P2) --------------------------------
        proposed = server.tool("remember", {
            "path": ".", "subject": SUBJECT, "value": VALUE,
            "memory_type": "decision"}, request_id=3)
        if proposed.get("committed") is not False:
            raise StepFailed("`remember` reported committed=%r on a first call — the human gate "
                             "did not hold, which is a P2 violation and not a portability bug"
                             % proposed.get("committed"))
        proposal_id = proposed.get("proposal_id") or proposed.get("awaiting_proposal_id")
        if not proposal_id:
            raise StepFailed("`remember` proposed nothing a human could approve: %s"
                             % json.dumps(proposed)[:600])
        _say("remember (proposes)", "ok — gated, id %s, nothing written" % proposal_id)

        # ---- 5 · the human approves, non-interactively ------------------------------------
        approved = _run(["mokata", "approve", "--yes", proposal_id], workdir)
        if approved.returncode != 0:
            raise StepFailed("`mokata approve --yes %s` exited %d:\n%s\n%s"
                             % (proposal_id, approved.returncode,
                                approved.stdout[-2000:], approved.stderr[-2000:]))
        _say("mokata approve --yes", "ok")

        # ---- 6 · the re-call commits ------------------------------------------------------
        committed = server.tool("remember", {
            "path": ".", "subject": SUBJECT, "value": VALUE,
            "memory_type": "decision", "proposal_id": proposal_id}, request_id=4)
        if not committed.get("committed"):
            raise StepFailed("the approved re-call did not commit: %s"
                             % json.dumps(committed)[:600])
        _say("remember (commits)", "ok — the write landed")

        # ---- 7 · ★ recall returns it ------------------------------------------------------
        recalled = server.tool("recall", {"path": ".", "query": SUBJECT}, request_id=5)
        blob = json.dumps(recalled)
        if VALUE not in blob and SUBJECT not in blob:
            raise StepFailed(
                "★ RECALL DID NOT RETURN THE MEMORY THAT WAS JUST WRITTEN. This is the failure "
                "the NAME/PATH invariant exists to prevent: a value stored under one path "
                "spelling and searched for under another comes back empty, and an empty recall "
                "is indistinguishable from 'nothing was remembered'. Payload:\n%s" % blob[:1500])
        _say("recall", "ok — the value came back")
    finally:
        server.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", action="store_true",
                        help="do not delete the scratch repo (for local poking)")
    args = parser.parse_args(argv)

    workdir = tempfile.mkdtemp(prefix="mokata-e2e-")
    print("END-TO-END: can a user run mokata here?  (scratch repo: %s)" % workdir, flush=True)
    try:
        check(workdir)
    except StepFailed as failure:
        print("\nFAILED: %s" % failure, file=sys.stderr, flush=True)
        return 1
    finally:
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)
    print("\nEND-TO-END OK — the installed package starts, the server answers, the gate holds, "
          "the write lands, and the recall returns it.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
