"""0.0.27 INTEG-FIX2: the saved-view restore against the person moving while it is in flight.

  * A screen picked while the saved workspace is still loading (`g s` before `GET /api/workspace`
    answers) is SAVED once the restore is done, so a reload puts it back. It used to be dropped.
  * A reload mid-restore never saves a half-restored workspace.
  (UX-SIMPLIFY §6 P1 deleted Welcome's first-run takeover, so the "Welcome never replaces a
  restored tab" race has no second party left: Get started is a section of Health.)

Both are races, so each step HOLDS the request that decides the order (Playwright `route`) instead
of hoping for a timing. Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import _harness as H


class TheRestoreNeverLosesTheScreenThePersonPicked(H.IdeFlow):
    FIXTURE = "shop"

    def _hold(self, pattern, method="GET"):
        """Hold every `method` request to `pattern` until released; returns (held, release)."""
        held, state = [], {"on": True}

        def handler(route):
            if state["on"] and route.request.method == method:
                held.append(route)
            else:
                route.continue_()
        self.page.route(pattern, handler)

        def release():
            state["on"] = False
            for r in held:
                r.continue_()
            self.page.unroute(pattern, handler)
        return held, release

    def _wait_held(self, held, what):
        for _ in range(400):
            if held:
                return
            self.page.wait_for_timeout(25)        # pumps the route handler
        self.fail("timed out waiting for %s to be held" % what)

    def _reload_and_settle(self):
        self.page.evaluate("localStorage.clear()")   # Welcome's "seen" mark is per browser
        self.page.reload()
        self.until("!!(window.MK && MK.ui && MK.ui.restored)", "the saved view to be restored")
        self.page.wait_for_timeout(1500)            # nothing that lands later may move the view
        return self.page.evaluate("document.body.dataset.mode")

    def test_1_a_screen_picked_while_the_workspace_loads_survives_a_reload(self):
        self.page.evaluate("localStorage.clear()")
        held, release = self._hold("**/api/workspace")
        self.page.reload()
        self._wait_held(held, "the saved workspace's GET")
        self.until("!!(window.MK && MK.ui)", "the page to boot")
        self.page.evaluate("document.activeElement && document.activeElement.blur()")
        self.press("g")
        self.press("s")
        self.page.wait_for_selector("#view-activity:not([hidden])")
        n = len(self.requests)
        release()
        self.until("MK.ui.restored === true", "the restore to finish")
        self.assertEqual("activity", self.page.evaluate("document.body.dataset.mode"))
        for _ in range(200):                       # the owed save is debounced (500 ms)
            if any(m == "POST" and "/api/workspace" in u for m, u in self.requests[n:]):
                break
            self.page.wait_for_timeout(25)
        self.assertEqual("activity", self._reload_and_settle(),
                         "the screen picked mid-restore came back after the reload")


LONG = "".join("def f%d():\n    return %d\n\n" % (i, i) for i in range(400))


def build_clean(base):
    """A mokata-inited repo with NOTHING uncommitted and one long file: Review has no diff to open,
    so the restore re-opens the saved source tab — the path where it awaits a file read."""
    repo = H._git_repo(base, "fixture-clean", dict(H.SHOP, **{"shop/long.py": LONG}))
    H.subprocess.run([H.sys.executable, "-m", "mokata", "init", "--profile", "standard", "--yes",
                      "--path", repo], check=True, env=H._env(), stdin=H.subprocess.DEVNULL,
                     stdout=H.subprocess.DEVNULL, stderr=H.subprocess.DEVNULL)
    H._git(repo, "add", "-A")
    H._git(repo, "commit", "-q", "-m", "fixture")
    return repo


class AReloadMidRestoreKeepsWhatWasSaved(H.IdeFlow):
    """While the restore is still re-opening the saved tab (its file read in flight), nothing it has
    half put back may be SAVED: a reload in that window (pagehide saves at once) would otherwise
    overwrite the saved workspace with a half-restored one — here, the tab's scroll read off a pane
    whose file has not arrived (0)."""
    FIXTURE = staticmethod(build_clean)

    def _saved(self):
        return self.page.evaluate(
            "fetch('/api/workspace').then(r => r.json()).then(w => ({active: w.active, mode: w.mode,"
            " tabs: (w.tabs || []).map(t => [t.key, t.scroll || 0])}))")

    def test_1_a_reload_while_the_saved_tab_is_reopening_keeps_its_scroll(self):
        p = self.page
        self.open_file("shop/text.py")
        p.locator("#tabstrip .tab", has_text="text.py").first.dblclick()          # keep it
        self.open_file("shop/long.py")
        self.until("document.getElementById('code-scroll').scrollHeight > 3000", "the long file")
        p.evaluate("document.getElementById('code-scroll').scrollTop = 2400")
        for _ in range(200):                     # the scroll save: 600 ms settle + 500 ms debounce
            w = self._saved()
            if dict(w["tabs"]).get("file:shop/long.py", 0) > 2000:
                break
            p.wait_for_timeout(50)
        before = self._saved()
        self.assertEqual("file:shop/long.py", before["active"], before)
        self.assertGreater(dict(before["tabs"])["file:shop/long.py"], 2000, before)
        # reload; the restore re-opens long.py and its read is HELD — the restore is in flight
        p.evaluate("localStorage.clear()")
        held, state = [], {"on": True}

        def handler(route):
            if state["on"]:
                held.append(route)
            else:
                route.continue_()
        is_file_read = lambda url: "/api/file?" in url
        p.route(is_file_read, handler)
        p.reload()
        self._wait_held(held)
        self.assertFalse(p.evaluate("!!MK.ui.restored"), "the restore is still in flight")
        # ... and the page goes away mid-restore. Its `pagehide` save is fired here, in the page, and
        # awaited — a real unload's keepalive POST is not reliably delivered under interception.
        p.evaluate("""async () => {
            const posts = [], f = window.fetch;
            window.fetch = (...a) => { const r = f(...a); if (a[1] && a[1].method === "POST") posts.push(r.catch(() => null)); return r; };
            window.dispatchEvent(new Event("pagehide"));
            await Promise.all(posts);
        }""")
        state["on"] = False
        p.unroute(is_file_read, handler)
        p.reload()
        self.until("!!(window.MK && MK.ui && MK.ui.restored)", "the saved tab to be restored")
        after = self._saved()
        self.assertEqual(before["active"], after["active"], after)
        self.assertEqual(sorted(k for k, _ in before["tabs"]), sorted(k for k, _ in after["tabs"]),
                         "every saved tab survived")
        self.assertGreater(dict(after["tabs"])["file:shop/long.py"], 2000,
                           "a reload mid-restore overwrote the saved scroll with a half-restored one")
        self.until("document.getElementById('code-scroll').scrollTop > 2000", "the scroll put back")

    def _wait_held(self, held):
        for _ in range(400):
            if held:
                return
            self.page.wait_for_timeout(25)
        self.fail("timed out waiting for the restore's file read to be held")
