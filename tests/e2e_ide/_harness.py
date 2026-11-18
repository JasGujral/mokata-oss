"""The IDE browser e2e harness (0.0.26 O9, IDE-E2E-IN-CI): fixture repos, the server, the page.

A FLOW is one `IdeFlow` subclass in a `tests/e2e_ide/test_*.py` module. It gets its own fixture
repo, its own `mokata ide --foreground` (this checkout's src/), its own headless Chromium page,
and — for free — a last step that fails on ANY console error, page error or HTTP >= 400 the flow
caused. Its `test_N_*` methods run in name order (the numbers are the order a user takes them).

    import _harness as H                     # never `from _harness import IdeFlow`: an imported
                                             # TestCase class is collected again in your module
    class TheThingWorks(H.IdeFlow):
        FIXTURE = "shop"                     # or "bare" (a git repo `mokata init` never touched)

        def test_1_open_it(self):
            self.open_file("shop/text.py")
            self.press("Control+F")
            ...

Fixtures are built by `make_repo(kind)` — add a kind there when a flow needs a different repo.
Keep them SMALL: this runs on every PR; the big-repo scale suite is `tests/e2e_scale/` (nightly).

Needs `playwright` and a Chromium it can launch (`python -m playwright install chromium`, or
PLAYWRIGHT_BROWSERS_PATH). Without them each flow SKIPS — unless `MOKATA_REQUIRE_E2E=1` (ci.yml's
`ide-e2e` job), where a missing browser is a FAILURE: a skipped e2e reads green and tested nothing.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src")
# 0.0.29 D1: the ONE hermetic git door (this harness cannot import `_support`: no PyYAML in the e2e
# job). Resolved relative to THIS file and APPENDED, never inserted at 0 — the way `_support` finds
# its own sibling.
_TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TESTS_DIR not in sys.path:
    sys.path.append(_TESTS_DIR)
import _hermetic_git  # noqa: E402

#: PROCESS-WIDE, as `_support` does it: every host `GIT_*` is deleted from THIS process at import,
#: so a flow or fixture that spawns git with no `env=` (`test_search_jobs_worktrees`,
#: `_explain_fixture.git`) inherits a clean env, not the host's `GIT_DIR` / `GIT_CONFIG_*`
#: (verifier D12 M-1; graded by `test_hermetic_git_env`). Names only — a value can carry a token.
HERMETIC_GIT_SCRUBBED = _hermetic_git.host_git_names(os.environ)
for _name in HERMETIC_GIT_SCRUBBED:
    del os.environ[_name]
REQUIRED = os.environ.get("MOKATA_REQUIRE_E2E") == "1"
#: Which playwright browser the flows run in (`MOKATA_E2E_BROWSER`, default chromium). Validated here,
#: at import: a name this harness does not know is an error naming it, never a quiet fallback to
#: Chromium (a "Firefox" job that ran Chromium would read green and test nothing).
BROWSERS = ("chromium", "firefox")
BROWSER = os.environ.get("MOKATA_E2E_BROWSER", "chromium")
if BROWSER not in BROWSERS:
    raise AssertionError("MOKATA_E2E_BROWSER=%r is not a browser this harness runs (one of: %s)"
                         % (BROWSER, ", ".join(BROWSERS)))
#: Per-browser launch options. Firefox has no clipboard permission to grant (Playwright's
#: `clipboard-read` is Chromium-only), so the async clipboard is enabled by pref instead.
_LAUNCH = {"chromium": {},
           "firefox": {"firefox_user_prefs": {"dom.events.asyncClipboard.readText": True,
                                              "dom.events.testing.asyncClipboard": True}}}
START_TIMEOUT = 45.0
STEP_MS = 20000

try:
    from playwright.sync_api import sync_playwright
    NO_PLAYWRIGHT = None
except ImportError as exc:          # pragma: no cover - depends on the machine
    sync_playwright = None
    NO_PLAYWRIGHT = "playwright is not installed (%s)" % exc


# ---- fixture repos -------------------------------------------------------------------------------

#: Lines in `data/words.txt` that each hold the word `needle`: more than one search page
#: (`mokata.ide.search.PAGE_MATCHES` = 1000), so "Load more" (P36) has a second page to fetch.
NEEDLE_LINES = 1500

SHOP = {
    "shop/__init__.py": "",
    "shop/text.py": (
        "def slugify(title):\n"
        "    return '-'.join(title.lower().split())\n"
        "\n"
        "\n"
        "def title_slug(order):\n"
        "    return slugify(order.title)\n"),
    "shop/orders.py": (
        "from shop.text import title_slug\n"
        "\n"
        "\n"
        "class Order:\n"
        "    def __init__(self, title):\n"
        "        self.title = title\n"
        "\n"
        "\n"
        "def order_ref(order):\n"
        "    return 'ord-' + title_slug(order)\n"),
    "tests/test_text.py": (
        "from shop.text import slugify\n"
        "\n"
        "\n"
        "def test_slugify():\n"
        "    assert slugify('A B') == 'a-b'\n"),
    "data/words.txt": "".join("row %04d needle\n" % i for i in range(NEEDLE_LINES)),
    "README.md": "# fixture shop\n",
}

#: The session worktree the "shop" fixture carries (P31): `.claude/worktrees/<name>`, on its own
#: branch, one commit ahead of the main checkout.
WORKTREE = "feat-coupons"


def _env():
    env = _hermetic_git.scrub(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("CLAUDECODE", None)
    return env


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   env=_hermetic_git.git_env())


def _write(repo, rel, body, mode="w"):
    path = os.path.join(repo, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode, encoding="utf-8", newline="\n") as fh:
        fh.write(body)


def _git_repo(base, name, files):
    repo = os.path.join(base, name)
    for rel, body in files.items():
        _write(repo, rel, body)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "e2e@example.invalid")
    _git(repo, "config", "user.name", "e2e")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


def make_repo(kind, base):
    """A small fixture repository of `kind` under `base`; returns its path.

    shop  a `mokata init`ed project: committed, ONE uncommitted change (the review view's diff),
          a 1,500-hit word for search paging, and a session worktree one commit ahead.
    bare  the same code in a plain git repo `mokata init` never ran in (what a stranger opens).

    A flow that needs its own repo sets `FIXTURE = staticmethod(build)`: `build(base)` -> path.
    """
    if callable(kind):
        return kind(base)
    if kind == "bare":
        files = {k: v for k, v in SHOP.items() if k != "data/words.txt"}
        repo = _git_repo(base, "bare-shop", files)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "fixture")
        return repo
    if kind != "shop":
        raise ValueError("unknown fixture kind %r" % kind)
    repo = _git_repo(base, "fixture-shop", SHOP)
    subprocess.run([sys.executable, "-m", "mokata", "init", "--profile", "standard", "--yes",
                    "--path", repo], check=True, env=_env(), stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _write(repo, ".gitignore", ".claude/worktrees/\n", mode="a")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    wt = os.path.join(repo, ".claude", "worktrees", WORKTREE)
    _git(repo, "worktree", "add", "-q", "-b", WORKTREE, wt)
    _write(wt, "shop/coupons.py", "def coupon(code):\n    return code.upper()\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "coupons")
    _write(repo, "shop/orders.py",
           "\n\ndef order_label(order):\n    return order_ref(order).upper()\n", mode="a")
    return repo


# ---- the server ----------------------------------------------------------------------------------

def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class IdeServer(object):
    """`mokata ide --foreground` on `repo`, from THIS checkout's src/; the pairing URL it prints."""

    def __init__(self, repo):
        self.repo = repo
        self.port = _free_port()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "mokata", "ide", "--path", repo, "--foreground",
             "--no-browser", "--port", str(self.port)],
            cwd=repo, env=_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        self.lines = []
        self._q = queue.Queue()
        self._pumper = threading.Thread(target=self._pump, daemon=True)
        self._pumper.start()
        self.url = self._await_url()

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))
            self._q.put(line)
        self._q.put(None)

    def _await_url(self):
        deadline = time.time() + START_TIMEOUT
        url = None
        while time.time() < deadline and url is None:
            try:
                line = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if line is None:
                break
            line = line.strip()
            if line.startswith("http://127.0.0.1:%d/pair?t=" % self.port):
                url = line
        if url is None:
            self.stop()
            raise AssertionError("mokata ide printed no pairing link in %ss:\n%s"
                                 % (START_TIMEOUT, "\n".join(self.lines)))
        # The link is printed BEFORE the server binds; wait until it accepts a connection.
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()
                return url
            except OSError:
                if self.proc.poll() is not None:
                    break
                time.sleep(0.1)
        self.stop()
        raise AssertionError("mokata ide never accepted a connection:\n%s" % "\n".join(self.lines))

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        self._pumper.join(timeout=5)
        if not self._pumper.is_alive():
            self.proc.stdout.close()


# ---- the flow base -------------------------------------------------------------------------------

def grant_clipboard(page, origin):
    """Let the page read and write the clipboard. Chromium needs a permission grant; Firefox has no
    such permission (it is enabled by launch pref, `_LAUNCH`), so there it is a no-op."""
    if BROWSER == "chromium":
        page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=origin)


class IdeFlow(unittest.TestCase):
    """One fixture repo + one server + one page per subclass; see the module docstring.

    Each step is its own assertion with its own message, so a red names the step that broke. A
    flow whose earlier step failed still runs its later ones against whatever the page shows; the
    first red is the one to read."""

    FIXTURE = "shop"
    #: The page's locale, pinned (not the machine's): the product formats counts in the reader's
    #: locale, and Firefox and headless Chromium on Linux follow the OS.
    LOCALE = "en-US"
    #: Fail the whole flow in setUpClass when the page's language is not LOCALE. Only the locale
    #: guard flow turns this off, so a dropped pin reads there as failed STEPS (a gradeable red).
    SELF_CHECK_LOCALE = True
    #: HTTP statuses a flow EXPECTS (e.g. a gated route answering 409); anything else >= 400 reds.
    EXPECTED_HTTP = ()

    no_browser = None
    browser = pw = server = None
    #: 0.0.29 D6: harness navigations in flight (the Firefox filter's term) and the evidence dir.
    _navigating = 0
    _evidence = None

    @classmethod
    def setUpClass(cls):
        if cls is IdeFlow:
            # Never a SkipTest from setUpClass (it drops the tests from `Ran N`,
            # tests/test_suite_count_integrity.py): the base's own step skips itself in setUp.
            cls.no_browser = "the base class is not a flow"
            return
        if NO_PLAYWRIGHT:
            if REQUIRED:
                raise AssertionError("MOKATA_REQUIRE_E2E=1 but %s — cannot run %s"
                                     % (NO_PLAYWRIGHT, BROWSER))
            # Not a SkipTest from setUpClass: that drops the tests from `Ran N` instead of
            # counting them as skips (tests/test_suite_count_integrity.py). Each test skips itself.
            cls.no_browser = NO_PLAYWRIGHT
            return
        cls.tmp = tempfile.mkdtemp(prefix="mokata-e2e-")
        try:
            cls.repo = make_repo(cls.FIXTURE, cls.tmp)
            cls.server = IdeServer(cls.repo)
            cls.pw = sync_playwright().start()
            try:
                cls.browser = getattr(cls.pw, BROWSER).launch(**_LAUNCH[BROWSER])
            except Exception as exc:
                if REQUIRED:
                    raise AssertionError("MOKATA_REQUIRE_E2E=1 but %s did not launch: %s"
                                         % (BROWSER, exc))
                cls.no_browser = "no launchable %s: %s" % (BROWSER, exc)
                cls._teardown()
                return
        except BaseException:
            cls._teardown()
            raise
        cls.errors = []
        cls.requests = []
        cls._contexts, cls._listened, cls._navigating = [], [], 0
        cls._evidence = os.environ.get("MOKATA_E2E_EVIDENCE_DIR") or None
        cls.context = cls.new_context(viewport={"width": 1400, "height": 900})
        if cls._evidence:
            # The action timeline, console and network — NO DOM snapshots. Measured on the mirror
            # (0.0.29 D probe 43416006107, 3 rounds per cell): with snapshots every action is
            # slower (explorer + image flows 31 s -> 45 s Chromium, 39 s -> 57 s Firefox, past the
            # e2e legs' ceilings at suite scale) and it turned a latent race in
            # `test_explorer_locator` test_16 red 3/3 in both browsers (0/3 without). The timeline
            # alone is what named that race: an `until` true at 10124 ms, the row read at 10141 ms.
            cls.context.tracing.start(snapshots=False, screenshots=False, sources=False)
        cls.page = page = cls.context.new_page()
        cls._listen(page)
        cls.page.set_default_timeout(STEP_MS)
        page.goto(cls.server.url)
        got = page.evaluate("navigator.language")
        if cls.SELF_CHECK_LOCALE and got != cls.LOCALE:
            cls._teardown()
            raise AssertionError("the page's locale is %r, not the pinned %r (%s)"
                                 % (got, cls.LOCALE, BROWSER))

    # -- 0.0.29 D6 `E2E-HARNESS-LOWS`: ONE door for a flow's browser contexts ---------------------

    @classmethod
    def new_context(cls, **kw):
        """The one door for a browser context in a flow (`browser.new_context` / `new_page` direct
        is a red: `test_d6_e2e_harness_doors.NoFlowBypassesTheContextDoor`). The locale is PINNED to the flow's
        `LOCALE`, every page the context opens gets the flow's console / pageerror / request /
        response listeners and the navigation counter, and the context is closed at teardown."""
        kw = {**dict(locale=cls.LOCALE), **kw}           # pinned unless the flow names one
        ctx = cls.browser.new_context(**kw)
        cls._contexts.append(ctx)
        ctx.on("page", cls._listen)
        return ctx

    @classmethod
    def _listen(cls, page):
        if any(p is page for p in cls._listened):       # `new_page` also fires the "page" event
            return
        cls._listened.append(page)
        page.on("console", lambda m: cls.errors.append("console.%s: %s" % (m.type, m.text))
                if m.type == "error" and not cls._expected_console(m.text) else None)
        page.on("pageerror", lambda e: cls.errors.append("pageerror: %s" % e))
        page.on("request", lambda r: cls.requests.append((r.method, r.url)))
        page.on("response", lambda r: cls.errors.append("HTTP %d %s" % (r.status, r.url))
                if r.status >= 400 and r.status not in cls.EXPECTED_HTTP else None)
        for name in ("goto", "reload"):
            setattr(page, name, cls._counted(page, getattr(page, name)))

    @classmethod
    def _counted(cls, page, nav):
        """A navigation the HARNESS (a flow's step) starts: counted while in flight, plus one
        round-trip to the page after it returns, so the console events it caused are dispatched
        while it still counts. A navigation the PAGE starts is never counted."""
        def run(*a, **kw):
            cls._navigating += 1
            try:
                out = nav(*a, **kw)
                try:
                    page.evaluate("0")                     # the turn after: drain its events
                except Exception:                          # noqa: BLE001 — a closed page drains nothing
                    pass
                return out
            finally:
                cls._navigating -= 1
        return run

    @classmethod
    def _expected_console(cls, text):
        # Chromium logs "Failed to load resource: ... status of 409" for an EXPECTED status too.
        if any("status of %d" % s in text for s in cls.EXPECTED_HTTP):
            return True
        # Firefox logs a console error when a navigation (`goto`/`reload`, a flow's fresh page)
        # aborts the page's open EventSource while the document is still loading. That is the
        # browser reporting its own abort of the live stream, not the page failing; the filter is
        # this one message about this one route, so a real stream failure still reds.
        # 0.0.29 D6: exempt ONLY while a harness-started navigation is in flight (`_counted`). The
        # same message from a navigation the PAGE started is a product bug, and reds.
        return (BROWSER == "firefox" and cls._navigating > 0 and "/api/events" in text
                and "was interrupted while the page was loading" in text)

    @classmethod
    def _teardown(cls):
        for ctx in getattr(cls, "_contexts", None) or ():
            try:
                ctx.close()
            except Exception:                              # noqa: BLE001 — teardown never raises
                pass
        cls._contexts = []
        for name, stop in (("browser", lambda b: b.close()), ("pw", lambda p: p.stop())):
            obj = getattr(cls, name, None)
            if obj is not None:
                try:
                    stop(obj)
                except Exception:
                    pass
                setattr(cls, name, None)
        if cls.server is not None:
            cls.server.stop()
            cls.server = None
        shutil.rmtree(getattr(cls, "tmp", ""), ignore_errors=True)

    @classmethod
    def tearDownClass(cls):
        cls._teardown()

    def setUp(self):
        if self.no_browser:
            self.skipTest(self.no_browser)
        if self._evidence:
            self.context.tracing.start_chunk(title=self.id())
            self._errors_at, self._requests_at = len(self.errors), len(self.requests)

    def tearDown(self):
        if not getattr(self, "_evidence", None) or self.no_browser:
            return
        if self._failed():
            self._keep_evidence()
        else:
            self.context.tracing.stop_chunk()

    def _failed(self):
        """True when THIS test has failed or errored so far (3.10: `_outcome.errors`; 3.11+: the
        result's lists, which unittest fills as each part fails)."""
        outcome = getattr(self, "_outcome", None)
        if outcome is None:
            return False
        if hasattr(outcome, "errors"):
            return any(exc is not None for _t, exc in outcome.errors)
        result = getattr(outcome, "result", None)
        return any(t is self for t, _tb in (getattr(result, "failures", []) +
                                            getattr(result, "errors", [])))

    def _keep_evidence(self):
        """0.0.29 D6 `E2E-RACES-UNNAMED`: the NEXT once-only red names itself. Under
        `$MOKATA_E2E_EVIDENCE_DIR` (CI sets it; a local run writes nothing): the Playwright trace of
        this test, a screenshot, the console/page errors and the requests since the test began,
        and the server's own output. ci.yml uploads the directory on failure."""
        out = os.path.join(self._evidence, "%s-%s" % (BROWSER, self.id()))
        os.makedirs(out, exist_ok=True)
        for what, act in (
                ("trace", lambda: self.context.tracing.stop_chunk(path=os.path.join(out, "trace.zip"))),
                ("screenshot", lambda: self.page.screenshot(path=os.path.join(out, "screenshot.png"),
                                                            full_page=True))):
            try:
                act()
            except Exception as exc:                       # noqa: BLE001 — evidence never masks the red
                with open(os.path.join(out, "%s.unavailable.txt" % what), "w", encoding="utf-8") as fh:
                    fh.write("%s\n" % exc)
        for name, lines in (
                ("console.txt", self.errors[self._errors_at:]),
                ("requests.txt", ["%s %s" % mu for mu in self.requests[self._requests_at:]]),
                ("server.log", list(self.server.lines) if self.server else [])):
            with open(os.path.join(out, name), "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")

    # -- helpers a flow uses ------------------------------------------------------------------

    def until(self, expr, what, timeout_ms=STEP_MS):
        """Poll `expr` in the page until truthy. Not `page.wait_for_function`: that evaluates a
        STRING in the page, which the IDE's own CSP (`script-src 'self'`, no unsafe-eval) refuses
        — correctly. `page.evaluate` goes through the devtools protocol, outside the CSP."""
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            if self.page.evaluate(expr):
                return
            time.sleep(0.1)
        self.fail("timed out waiting for %s" % what)

    def text_until(self, selector, needle, what):
        """Wait until `selector`'s text contains `needle`; return the text."""
        sel = selector.replace("\\", "\\\\").replace("'", "\\'")
        ndl = needle.replace("\\", "\\\\").replace("'", "\\'")
        self.until("(() => { const n = document.querySelector('%s');"
                   " return !!(n && n.textContent.includes('%s')); })()" % (sel, ndl), what)
        return self.page.text_content(selector)

    def press(self, keys):
        self.page.keyboard.press(keys)

    def go(self, view):
        """Reach a view the way a person does (UX-SIMPLIFY: three top tabs, views as sub-tabs).
        `graph` is Review's "Expand graph"; `search` is Code's Search side tab; `welcome` is the
        Get started section of the Health dialog."""
        p = self.page
        if view == "welcome":
            p.click("#health-chip")
            p.wait_for_selector("#hp-welcome li.wl-row")
            return
        if view == "graph":                  # Impact → Expand graph, from a changed file's symbol
            self.go("review")
            if p.is_disabled("#insp-ripple"):
                p.wait_for_selector("#nav-list li", state="attached")
                p.locator("#nav-list li", has_text=".py").first.click()
            self.until("!document.getElementById('insp-ripple').disabled", "a symbol in the inspector")
            p.click("#insp-ripple")
            p.wait_for_selector("#view-graph:not([hidden])")
            return
        if view == "search":
            self.go("navigate")
            p.click("#ltab-search")
            p.wait_for_selector("#lt-search:not([hidden])")
            return
        tab = p.evaluate("(v) => (MK.VIEWS.find((x) => x.id === v) || {tab: v}).tab", view)
        p.click("#tab-%s" % tab)
        if p.is_visible("#subtab-%s" % view):
            p.click("#subtab-%s" % view)
        self.until("MK.ui.mode === %r" % view, "the %s view" % view)

    def open_file(self, path):
        """Quick-open (⌘P) `path` and wait for its source in the navigate view.

        ⌥-click, the palette's own "the file, not the diff" gesture: a plain ↵/click opens a CHANGED
        file's diff, and whether the palette knows the file is changed depends on whether the change
        list has loaded before the name was typed — a race that sent a plain click to Review on CI
        (0.0.27: the fixtures' CSVs are uncommitted changes, so the first row became `diff`)."""
        p = self.page
        p.click("#qo-open")
        p.wait_for_selector("#qo:not([hidden])")
        p.fill("#qo-input", path)
        # a row of the list that answers THIS query: the empty query's list (drawn when the palette
        # opened) is about to be replaced, and a click aimed at one of its rows lands on whatever the
        # new list puts there - or on nothing (0.0.28 D, the Linux Firefox full-suite red)
        p.locator("#qo-results[data-q=%s] .qo-row" % json.dumps(path),
                  has_text=path.rsplit("/", 1)[-1]).first.click(modifiers=["Alt"])
        p.wait_for_selector("#view-navigate:not([hidden])")
        self.until("!!(window.MK && MK.nav && MK.nav.path === %r)" % path, "%s to open" % path)

    # -- the step every flow gets ---------------------------------------------------------------

    def test_99_no_console_errors(self):
        """Across the whole flow: zero console errors, zero uncaught errors, no unexpected HTTP
        error. A flow that "worked" while the page threw is not a working flow."""
        self.page.wait_for_timeout(400)      # let any late async error land
        self.assertEqual([], self.errors, "the browser logged errors:\n" + "\n".join(self.errors))
