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
REQUIRED = os.environ.get("MOKATA_REQUIRE_E2E") == "1"
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
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("CLAUDECODE", None)
    return env


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
    """
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

class IdeFlow(unittest.TestCase):
    """One fixture repo + one server + one page per subclass; see the module docstring.

    Each step is its own assertion with its own message, so a red names the step that broke. A
    flow whose earlier step failed still runs its later ones against whatever the page shows; the
    first red is the one to read."""

    FIXTURE = "shop"
    #: HTTP statuses a flow EXPECTS (e.g. a gated route answering 409); anything else >= 400 reds.
    EXPECTED_HTTP = ()

    no_browser = None
    browser = pw = server = None

    @classmethod
    def setUpClass(cls):
        if cls is IdeFlow:
            # Never a SkipTest from setUpClass (it drops the tests from `Ran N`,
            # tests/test_suite_count_integrity.py): the base's own step skips itself in setUp.
            cls.no_browser = "the base class is not a flow"
            return
        if NO_PLAYWRIGHT:
            if REQUIRED:
                raise AssertionError("MOKATA_REQUIRE_E2E=1 but " + NO_PLAYWRIGHT)
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
                cls.browser = cls.pw.chromium.launch()
            except Exception as exc:
                if REQUIRED:
                    raise AssertionError("MOKATA_REQUIRE_E2E=1 but Chromium did not launch: %s"
                                         % exc)
                cls.no_browser = "no launchable Chromium: %s" % exc
                cls._teardown()
                return
        except BaseException:
            cls._teardown()
            raise
        cls.page = cls.browser.new_page(viewport={"width": 1400, "height": 900})
        cls.page.set_default_timeout(STEP_MS)
        cls.errors = []
        cls.requests = []
        page = cls.page
        page.on("console", lambda m: cls.errors.append("console.%s: %s" % (m.type, m.text))
                if m.type == "error" and not cls._expected_console(m.text) else None)
        page.on("pageerror", lambda e: cls.errors.append("pageerror: %s" % e))
        page.on("request", lambda r: cls.requests.append((r.method, r.url)))
        page.on("response", lambda r: cls.errors.append("HTTP %d %s" % (r.status, r.url))
                if r.status >= 400 and r.status not in cls.EXPECTED_HTTP else None)
        page.goto(cls.server.url)

    @classmethod
    def _expected_console(cls, text):
        # Chromium logs "Failed to load resource: ... status of 409" for an EXPECTED status too.
        return any("status of %d" % s in text for s in cls.EXPECTED_HTTP)

    @classmethod
    def _teardown(cls):
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

    def rail(self, panel):
        self.page.click("button.rail-btn[data-screen='%s']" % panel)

    def open_file(self, path):
        """Quick-open (⌘P) `path` and wait for its source in the navigate view."""
        p = self.page
        p.click("#qo-open")
        p.wait_for_selector("#qo:not([hidden])")
        p.fill("#qo-input", path)
        p.locator("#qo-results .qo-row", has_text=path.rsplit("/", 1)[-1]).first.click()
        p.wait_for_selector("#view-navigate:not([hidden])")
        self.until("!!(window.MK && MK.nav && MK.nav.path === %r)" % path, "%s to open" % path)

    # -- the step every flow gets ---------------------------------------------------------------

    def test_99_no_console_errors(self):
        """Across the whole flow: zero console errors, zero uncaught errors, no unexpected HTTP
        error. A flow that "worked" while the page threw is not a working flow."""
        self.page.wait_for_timeout(400)      # let any late async error land
        self.assertEqual([], self.errors, "the browser logged errors:\n" + "\n".join(self.errors))
