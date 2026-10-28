"""0.0.26 P38 `APPROVE-IN-IDE` — PRD G6 / R29: approve or reject a proposed durable write from the
IDE, through mokata's EXISTING approval channel (`approval.approve` / `approval.reject`), without
handing a shell-capable agent an approve door.

The decision this proves (ide/approve_codes.py has the threat model):
  * the pairing token is NOT consent — an agent can read it from server.json and send it as a header
    (curl sets any Origin). A client holding only the token can never approve or reject;
  * Approve / Reject here need a ONE-TIME CODE the server printed to its OWN terminal: offered only
    when the server holds a human TTY (`mokata ide --foreground`, not in a Claude Code session);
    single-use, bound to (proposal, decision), expires, 3 wrong answers void it, mints rate-limited;
    never written to a file, never in a response, never on the event stream;
  * without a terminal the card shows the proposal in full and the `mokata approve <id>` command;
  * R29: approve in the window → the agent's redeem succeeds once; ledger rows approved (actor
    ide-tty) + redeemed; a rejected proposal licenses nothing; `mokata approve --reject` is the CLI
    twin, behind the same TTY re-confirm.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from test_p0_ide_api_contract import BASE, ORIGIN, PORT, TOKEN, fixture

from mokata import approval
from mokata.ide import approve_codes as AC
from mokata.ide.features import approvals as FA

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
AGENT = {"X-Mokata-Token": TOKEN}          # what an agent's curl can send: the token, no browser


def boot(root, tty=False, demo=False):
    from starlette.testclient import TestClient
    from mokata.ide.server import AppState, build_app
    state = AppState(root, TOKEN, PORT, None, demo, False, approve_here=False)
    if tty:
        state.approve_tty = (True, "")                  # as a foreground server at a human TTY
    app = build_app(state)
    page = TestClient(app, base_url=BASE, follow_redirects=False)
    page.get(f"/pair?t={TOKEN}")
    agent = TestClient(app, base_url=BASE, follow_redirects=False)
    return page, agent, state


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


def propose(root, n=1, tool="remember"):
    return approval.propose(root, tool=tool, args={"n": n}, run_id="R-1", target=f"memory:x{n}",
                            summary=f"remember x{n}", preview=f"line one\nline two {n}")


class _Repo(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.d = os.path.realpath(self._t.name)
        fixture(self.d)

    def tearDown(self):
        self._t.cleanup()

    def ledger(self):
        from mokata.govern.ledger import AuditLedger
        led = AuditLedger.from_mokata_dir(os.path.join(self.d, ".mokata"))
        return [e for e in led.entries() if e.get("kind") == approval.LEDGER_KIND]


# ===================================================================== the code book (pure)

class TheOneTimeCode(unittest.TestCase):

    def setUp(self):
        self.t = [1000.0]
        self.book = AC.CodeBook(clock=lambda: self.t[0])

    def test_a_code_is_single_use_bound_to_its_decision_and_expires(self):
        code, why, _ = self.book.mint("p-1", "approve")
        self.assertRegex(code, r"^[A-Z2-9]{8}$")
        self.assertEqual((False, "that code was printed to approve, not to reject"),
                         self.book.check("p-1", "reject", code))
        self.assertTrue(self.book.check("p-1", "approve", code[:4] + "-" + code[4:].lower())[0])
        self.assertFalse(self.book.check("p-1", "approve", code)[0], "a code is single-use")
        self.t[0] += 10
        code2, _, _ = self.book.mint("p-1", "approve")
        self.t[0] += AC.CODE_TTL + 1
        ok, why = self.book.check("p-1", "approve", code2)
        self.assertEqual((False, "that code has expired — ask for a new one"), (ok, why))

    def test_three_wrong_answers_void_a_code_and_mints_are_rate_limited(self):
        code, _, _ = self.book.mint("p-1", "approve")
        for _ in range(AC.MAX_TRIES):
            self.assertFalse(self.book.check("p-1", "approve", "WRONGWRONG")[0])
        self.assertIn("no code is waiting", self.book.check("p-1", "approve", code)[1],
                      "the right code after three wrong ones is too late")
        got, why, retry = self.book.mint("p-2", "approve")
        self.assertIsNone(got, "a second mint within MIN_GAP is refused")
        self.assertGreater(retry, 0)
        n = 0
        for _ in range(20):
            self.t[0] += AC.MIN_GAP
            n += self.book.mint(f"p-x{_}", "approve")[0] is not None
        self.assertLessEqual(n, AC.MAX_MINTS, "at most MAX_MINTS codes a minute")

    def test_the_terminal_test_is_the_approve_bar_and_fails_closed(self):
        tty, pipe = FakeTTY(), io.StringIO()
        self.assertEqual((True, ""), AC.human_terminal({}, tty, tty))
        self.assertFalse(AC.human_terminal({}, pipe, tty)[0], "stdin not a TTY (an agent's Bash)")
        self.assertFalse(AC.human_terminal({}, tty, pipe)[0], "nowhere to print the code")
        self.assertEqual(AC.IN_SESSION, AC.human_terminal({"CLAUDECODE": "1"}, tty, tty)[1])
        import _support                 # O15: "no stdin" must not mean "whatever the runner has"
        with _support.stdin_is(_support.NoTtyStdin()):
            self.assertFalse(AC.human_terminal({}, None, tty)[0])
        self.assertFalse(AC.say("x", pipe), "a code is never printed to a non-terminal")


# ===================================================================== the channel (reject)

class RejectIsPartOfTheChannel(_Repo):

    def test_a_rejected_proposal_licenses_nothing_and_leaves_the_pending_list(self):
        from mokata.govern.ledger import AuditLedger
        p = propose(self.d)
        led = AuditLedger.from_mokata_dir(os.path.join(self.d, ".mokata"))
        res = approval.reject(self.d, p.proposal_id, actor="human", ledger=led)
        self.assertTrue(res.ok)
        self.assertEqual([], approval.pending(self.d))
        c = approval.redeem(self.d, p.proposal_id, tool="remember", args={"n": 1}, run_id="R-1")
        self.assertEqual(approval.REFUSED_REJECTED, c.code)
        self.assertFalse(approval.approve(self.d, p.proposal_id).ok, "no approve after a reject")
        self.assertEqual(["rejected"], [e["decision"] for e in self.ledger()])

    def test_an_unredeemed_approval_can_be_taken_back_but_a_used_one_cannot(self):
        p = propose(self.d)
        approval.approve(self.d, p.proposal_id)
        self.assertTrue(approval.reject(self.d, p.proposal_id).ok)
        self.assertFalse(approval.redeem(self.d, p.proposal_id, tool="remember", args={"n": 1},
                                         run_id="R-1").granted)
        q = propose(self.d, 2)
        approval.approve(self.d, q.proposal_id)
        self.assertTrue(approval.redeem(self.d, q.proposal_id, tool="remember", args={"n": 2},
                                        run_id="R-1").granted)
        self.assertEqual(approval.REFUSED_USED, approval.reject(self.d, q.proposal_id).code)

    def test_the_cli_reject_fails_closed_off_a_tty_and_works_with_the_humans_yes(self):
        p = propose(self.d)
        env = dict(os.environ, PYTHONPATH=SRC)
        env.pop("CLAUDECODE", None)
        run = lambda *a: subprocess.run([sys.executable, "-m", "mokata.cli", "approve", *a,
                                         "--path", self.d], capture_output=True, text=True,
                                        stdin=subprocess.DEVNULL, env=env, timeout=120)
        r = run(p.proposal_id, "--reject")
        self.assertNotEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertEqual("proposed", approval.load(self.d, p.proposal_id).status)
        r = run(p.proposal_id, "--reject", "--yes")
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertEqual("rejected", approval.load(self.d, p.proposal_id).status)


# ===================================================================== the routes

class TheTokenIsNotConsent(_Repo):
    """The firing control: an agent-style client holding server.json's token (header, no Origin)
    — or the paired page itself — cannot approve or reject without the terminal's code."""

    def test_an_agent_with_the_token_cannot_approve_without_a_terminal(self):
        p = propose(self.d)
        page, agent, state = boot(self.d, tty=False)
        for c, h in ((agent, AGENT), (page, ORIGIN)):
            r = c.post("/api/approvals/code", json={"id": p.proposal_id, "decision": "approve"}, headers=h)
            self.assertEqual(409, r.status_code)
            self.assertEqual("NoTerminal", r.json()["error"])
            self.assertEqual(f"mokata approve {p.proposal_id}", r.json()["command"])
            r = c.post("/api/approvals/decide", json={"id": p.proposal_id, "decision": "approve",
                                                      "code": "AAAA-AAAA"}, headers=h)
            self.assertEqual(409, r.status_code)
        self.assertEqual("proposed", approval.load(self.d, p.proposal_id).status)

    def test_an_agent_with_the_token_cannot_approve_even_when_a_human_terminal_is_attached(self):
        p = propose(self.d)
        page, agent, state = boot(self.d, tty=True)
        out = FakeTTY()
        with mock.patch.object(AC.sys, "stdout", out):
            r = agent.post("/api/approvals/code", json={"id": p.proposal_id, "decision": "approve"},
                           headers=AGENT)
        self.assertEqual(200, r.status_code)
        code = re.search(r"([A-Z2-9]{4}-[A-Z2-9]{4})", out.getvalue()).group(1)
        self.assertNotIn(code.replace("-", ""), r.text.replace("-", ""), "the code is never in a response")
        self.assertNotIn(code, json.dumps(page.get("/api/approvals").json()))
        for guess in ("AAAA-AAAA", "BBBB-BBBB", "CCCC-CCCC"):
            r = agent.post("/api/approvals/decide", json={"id": p.proposal_id, "decision": "approve",
                                                          "code": guess}, headers=AGENT)
            self.assertEqual(403, r.status_code)
        r = agent.post("/api/approvals/decide", json={"id": p.proposal_id, "decision": "approve",
                                                      "code": code}, headers=AGENT)
        self.assertEqual(403, r.status_code, "three wrong guesses void the code — even the right one fails now")
        self.assertEqual("proposed", approval.load(self.d, p.proposal_id).status)
        self.assertEqual([], self.ledger())
        # the code was never written anywhere on disk
        hits = []
        for base, _dirs, files in os.walk(self.d):
            for f in files:
                try:
                    with open(os.path.join(base, f), "rb") as fh:
                        if code.replace("-", "").encode() in fh.read().replace(b"-", b""):
                            hits.append(f)
                except OSError:
                    pass
        self.assertEqual([], hits)

    def test_wrong_expired_and_reused_codes_are_refused_by_name(self):
        p = propose(self.d)
        page, _agent, state = boot(self.d, tty=True)
        out = FakeTTY()
        with mock.patch.object(AC.sys, "stdout", out):
            page.post("/api/approvals/code", json={"id": p.proposal_id, "decision": "approve"}, headers=ORIGIN)
        code = re.findall(r"([A-Z2-9]{4}-[A-Z2-9]{4})", out.getvalue())[-1]
        r = page.post("/api/approvals/decide", json={"id": p.proposal_id, "decision": "approve", "code": "ZZZZ-ZZZZ"}, headers=ORIGIN)
        self.assertEqual((403, "BadCode"), (r.status_code, r.json()["error"]))
        self.assertIn("2 tries left", r.json()["note"])
        r = page.post("/api/approvals/decide", json={"id": p.proposal_id, "decision": "approve", "code": code}, headers=ORIGIN)
        self.assertEqual(200, r.status_code, r.text)
        r = page.post("/api/approvals/decide", json={"id": p.proposal_id, "decision": "approve", "code": code}, headers=ORIGIN)
        self.assertEqual(403, r.status_code, "reused")
        q = propose(self.d, 2)
        state.codes._clock = lambda: time.time() + 3600          # every code minted is already old
        state.codes._mints = []
        with mock.patch.object(AC.sys, "stdout", FakeTTY()) as o2:
            page.post("/api/approvals/code", json={"id": q.proposal_id, "decision": "reject"}, headers=ORIGIN)
        state.codes._clock = lambda: time.time() + 3600 + AC.CODE_TTL + 5
        code2 = re.findall(r"([A-Z2-9]{4}-[A-Z2-9]{4})", o2.getvalue())[-1]
        r = page.post("/api/approvals/decide", json={"id": q.proposal_id, "decision": "reject", "code": code2}, headers=ORIGIN)
        self.assertIn("expired", r.json()["note"])
        self.assertEqual("proposed", approval.load(self.d, q.proposal_id).status)

    def test_a_used_or_unknown_proposal_is_shown_with_its_reason(self):
        page, _a, _s = boot(self.d, tty=True)
        with mock.patch.object(AC.sys, "stdout", FakeTTY()):
            r = page.post("/api/approvals/code", json={"id": "p-000000000000", "decision": "approve"}, headers=ORIGIN)
        self.assertEqual((409, "UnknownProposal"), (r.status_code, r.json()["error"]))
        p = propose(self.d)
        approval.approve(self.d, p.proposal_id)
        approval.redeem(self.d, p.proposal_id, tool="remember", args={"n": 1}, run_id="R-1")
        one = page.get("/api/approvals", params={"id": p.proposal_id}).json()["one"]
        self.assertEqual(("used", False), (one["status"], one["decidable"]))


class R29_ApproveInTheWindowThenTheAgentRedeems(_Repo):

    def test_R29_approve_with_the_code_then_redeem_succeeds_once_and_the_ledger_says_who(self):
        p = propose(self.d)
        page, _agent, state = boot(self.d, tty=True)
        listed = page.get("/api/approvals").json()
        self.assertEqual([p.proposal_id], [c["proposal_id"] for c in listed["pending"]])
        card = listed["pending"][0]
        self.assertIn("line two 1", card["render"], "the write is shown in full")
        self.assertEqual(f"mokata approve {p.proposal_id}", card["command"])
        self.assertTrue(listed["approve_here"]["available"])
        out = FakeTTY()
        with mock.patch.object(AC.sys, "stdout", out):
            r = page.post("/api/approvals/code", json={"id": p.proposal_id, "decision": "approve"}, headers=ORIGIN)
        self.assertEqual(200, r.status_code)
        code = re.search(r"([A-Z2-9]{4}-[A-Z2-9]{4})", out.getvalue()).group(1)
        r = page.post("/api/approvals/decide", json={"id": p.proposal_id, "decision": "approve", "code": code}, headers=ORIGIN)
        self.assertEqual(200, r.status_code, r.text)
        self.assertEqual(["approved"], [x["decision"] for x in r.json()["ledger"]])
        self.assertEqual("ide-tty", r.json()["ledger"][0]["actor"])
        # the agent's act 3 — the SAME redeem every MCP write tool calls
        c = approval.consent(self.d, tool="remember", args={"n": 1}, run_id="R-1", proposal_id=p.proposal_id)
        self.assertTrue(c.granted)
        from mokata.govern.ledger import AuditLedger
        approval.record_redemption(AuditLedger.from_mokata_dir(os.path.join(self.d, ".mokata")), c.proposal)
        self.assertFalse(approval.consent(self.d, tool="remember", args={"n": 1}, run_id="R-1",
                                          proposal_id=p.proposal_id).granted, "single-use")
        self.assertEqual(["approved", "redeemed"], [e["decision"] for e in self.ledger()])
        one = page.get("/api/approvals", params={"id": p.proposal_id}).json()["one"]
        self.assertEqual(["approved", "redeemed"], [x["decision"] for x in one["ledger"]])

    def test_R29_reject_in_the_window_and_the_redeem_is_refused(self):
        p = propose(self.d)
        page, _agent, _state = boot(self.d, tty=True)
        out = FakeTTY()
        with mock.patch.object(AC.sys, "stdout", out):
            page.post("/api/approvals/code", json={"id": p.proposal_id, "decision": "reject"}, headers=ORIGIN)
        code = re.search(r"([A-Z2-9]{4}-[A-Z2-9]{4})", out.getvalue()).group(1)
        r = page.post("/api/approvals/decide", json={"id": p.proposal_id, "decision": "reject", "code": code}, headers=ORIGIN)
        self.assertEqual("rejected", r.json()["proposal"]["status"])
        c = approval.consent(self.d, tool="remember", args={"n": 1}, run_id="R-1", proposal_id=p.proposal_id)
        self.assertEqual(approval.REFUSED_REJECTED, c.code)

    def test_R29_the_ide_writes_through_the_channel_only(self):
        """No new write path: the routes module never opens, replaces or removes a file itself —
        every state change is approval.approve / approval.reject (registered in the SI.6 sweeps)."""
        src = open(FA.__file__, encoding="utf-8").read() + open(AC.__file__, encoding="utf-8").read()
        for bad in ("open(", "os.replace", "shutil.", "os.remove", "StateStore", ".write("):
            hits = [ln for ln in src.splitlines() if bad in ln and "out.write(" not in ln
                    and "with open(path, \"rb\")" not in ln]
            self.assertEqual([], hits, bad)
        self.assertIn("approval.approve if decision", src)

    def test_a_demo_window_never_approves(self):
        page, _a, _s = boot(self.d, demo=True)
        d = page.get("/api/approvals").json()
        self.assertEqual((True, []), (d["sample"], d["pending"]))
        r = page.post("/api/approvals/code", json={"id": "p-1", "decision": "approve"}, headers=ORIGIN)
        self.assertEqual("Sample", r.json()["error"])


# ===================================================================== a real terminal

def _pty_ok():
    if os.name != "posix":
        return "a pseudo-terminal needs POSIX (Windows: owed, see the report)"
    try:
        import pty  # noqa: F401
        import starlette  # noqa: F401
    except ImportError:
        return "pty or the IDE web stack is not importable"
    return ""


PTY_SKIP = _pty_ok()


def _serve(root, port, tty):
    """`mokata ide --foreground` as a human runs it (tty=True: in a pseudo-terminal) or as an agent's
    Bash runs it (stdin/stdout pipes). Returns (proc, pairing url, read-the-terminal callable)."""
    env = dict(os.environ, PYTHONPATH=SRC, HOME=os.path.join(root, ".home"),
               USERPROFILE=os.path.join(root, ".home"))
    env.pop("CLAUDECODE", None)
    cmd = [sys.executable, "-m", "mokata.cli", "ide", "--path", root, "--no-browser",
           "--foreground", "--port", str(port)]
    buf = []
    if tty:
        import pty
        master, slave = pty.openpty()
        proc = subprocess.Popen(cmd, cwd=root, env=env, stdin=slave, stdout=slave, stderr=slave,
                                close_fds=True)
        os.close(slave)

        def pump():
            while True:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    return
                if not chunk:
                    return
                buf.append(chunk.decode("utf-8", "replace"))
        threading.Thread(target=pump, daemon=True).start()
    else:
        proc = subprocess.Popen(cmd, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        threading.Thread(target=lambda: [buf.append(x) for x in proc.stdout], daemon=True).start()
    t0 = time.time()
    url = None
    while time.time() - t0 < 60 and url is None:
        m = re.search(r"(http://127\.0\.0\.1:%d/pair\?t=[\w-]+)" % port, "".join(buf))
        url = m.group(1) if m else None
        if url is None and proc.poll() is not None:      # it died: stop waiting out the 60s
            break
        time.sleep(0.1)
    import socket
    while url and time.time() - t0 < 60:                 # the link is printed before it listens
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            break
        except OSError:
            time.sleep(0.2)
    def diagnosis():
        """⚠ O16: on Jas's macOS run `url` was None AND the terminal was EMPTY, which says the
        child printed nothing at all — not that the link was malformed. A bare `assertTrue(url)`
        cannot tell those apart, so the message now carries what a diagnosis needs: whether the
        process is still alive, its exit code, how long we waited, the port, and the raw bytes."""
        alive = proc.poll() is None
        return ("mokata ide --foreground printed no pairing link.\n"
                "  tty=%s port=%s waited=%.1fs alive=%s returncode=%s\n"
                "  argv: %s\n"
                "  --- terminal (%d bytes) ---\n%s"
                % (tty, port, time.time() - t0, alive, proc.returncode, " ".join(cmd),
                   len("".join(buf)), "".join(buf)[-2000:] or "(nothing)"))

    return proc, url, lambda: "".join(buf), diagnosis


@unittest.skipIf(PTY_SKIP, PTY_SKIP)
class InARealTerminal(_Repo):

    def _stop(self, proc):
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()

    def test_a_human_terminal_gets_the_code_an_agents_pipe_never_does(self):
        import urllib.request
        p = propose(self.d)
        port = 23000 + os.getpid() % 1500
        for tty in (False, True):
          with self.subTest(tty=tty):                    # each half reports on its own (O16)
            proc, url, screen, why = _serve(self.d, port + tty, tty)
            try:
                self.assertTrue(url, why())
                tok = re.search(r"t=([\w-]+)", url).group(1)
                req = urllib.request.Request(url.split("/pair")[0] + "/api/approvals/code",
                                             data=json.dumps({"id": p.proposal_id, "decision": "approve"}).encode(),
                                             headers={"X-Mokata-Token": tok, "Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=20) as r:
                        status, body = r.status, r.read().decode()
                except urllib.error.HTTPError as e:
                    status, body = e.code, e.read().decode()
                time.sleep(0.5)
                if not tty:
                    self.assertEqual(409, status, body)
                    self.assertNotRegex(screen(), r"[A-Z2-9]{4}-[A-Z2-9]{4}")
                else:
                    self.assertEqual(200, status, body)
                    m = re.search(r"to APPROVE %s .*?([A-Z2-9]{4}-[A-Z2-9]{4})" % p.proposal_id, screen())
                    self.assertTrue(m, screen()[-800:])
                    self.assertNotIn(m.group(1), body)
                    rec = open(os.path.join(self.d, ".mokata", "temp_local", "ide", "server.json"), encoding="utf-8").read()
                    self.assertNotIn(m.group(1), rec)
            finally:
                self._stop(proc)


# ===================================================================== the card in Chromium

def _why_no_browser():
    if PTY_SKIP:
        return PTY_SKIP
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            pw.chromium.launch().close()
    except Exception as exc:                              # noqa: BLE001 — any failure skips
        return f"Playwright/Chromium unavailable: {str(exc).splitlines()[0][:120]}"
    return ""


BROWSER_SKIP = _why_no_browser()


@unittest.skipIf(BROWSER_SKIP, BROWSER_SKIP)
class TheCardInTheBrowser(_Repo):

    def run_page(self, tty, fn):
        from playwright.sync_api import sync_playwright
        port = 24600 + os.getpid() % 1300 + (7 if tty else 0)
        proc, url, screen, why = _serve(self.d, port, tty)
        try:
            self.assertTrue(url, why())
            with sync_playwright() as pw:
                b = pw.chromium.launch()
                try:
                    pg = b.new_page(viewport={"width": 1440, "height": 900})
                    errs = []
                    pg.on("pageerror", lambda e: errs.append(str(e)))
                    pg.goto(url)
                    pg.wait_for_function("() => window.MK && MK.ui && MK.ui.state")
                    fn(pg, screen)
                    self.assertEqual([], errs)
                finally:
                    b.close()
        finally:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()

    def test_the_card_approves_with_the_terminal_code_and_shows_the_ledger_row(self):
        p = propose(self.d)

        def drive(pg, screen):
            pg.click("nav.rail .rail-btn[data-screen=review]")
            card = pg.locator(f"#approvals-review .ap-card[data-pid='{p.proposal_id}']")
            card.wait_for()
            self.assertIn("line two 1", card.inner_text())
            card.get_by_role("button", name="Approve", exact=True).click()
            card.locator("input.ap-code").wait_for()
            t0 = time.time()
            m = None
            while time.time() - t0 < 10 and not m:
                m = re.search(r"to APPROVE %s .*?([A-Z2-9]{4}-[A-Z2-9]{4})" % p.proposal_id, screen())
                time.sleep(0.1)
            self.assertTrue(m, "the code was printed to the terminal")
            card.locator("input.ap-code").fill(m.group(1).lower())
            card.locator("input.ap-code").press("Enter")
            pg.wait_for_function("(pid) => /approved by ide-tty/.test((document.querySelector(`#approvals-review .ap-card[data-pid='${pid}']`) || {}).innerText || '')", arg=p.proposal_id)
        self.run_page(True, drive)
        self.assertEqual("approved", approval.load(self.d, p.proposal_id).status)
        self.assertTrue(approval.consent(self.d, tool="remember", args={"n": 1}, run_id="R-1",
                                         proposal_id=p.proposal_id).granted)

    def test_without_a_terminal_the_card_shows_why_and_the_command_and_updates_live(self):
        p = propose(self.d)

        def drive(pg, _screen):
            pg.click("#activity-chip")
            card = pg.locator(f"#approvals-drawer .ap-card[data-pid='{p.proposal_id}']")
            card.wait_for()
            txt = card.inner_text()
            self.assertIn(f"mokata approve {p.proposal_id}", txt)
            self.assertIn("--foreground", txt)
            self.assertEqual(0, card.get_by_role("button", name="Approve", exact=True).count())
            # the live stream is up (its first `changed` arrived) and the watcher has primed
            pg.wait_for_function("() => MK.ui.lastFp !== undefined")
            pg.wait_for_timeout(1500)
            # the human approves in a terminal (D) → the card turns approved, live, with its row
            from mokata.govern.ledger import AuditLedger
            approval.approve(self.d, p.proposal_id, ledger=AuditLedger.from_mokata_dir(os.path.join(self.d, ".mokata")))
            pg.wait_for_function("(pid) => /approved by human/.test((document.querySelector(`#approvals-drawer .ap-card[data-pid='${pid}']`) || {}).innerText || '')", arg=p.proposal_id, timeout=15000)
        self.run_page(False, drive)


if __name__ == "__main__":
    unittest.main()
