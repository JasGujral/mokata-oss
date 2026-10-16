"""0.0.23 stage C5 — `mokata ide`: the plan + spec viewer, the run lifecycle strip, searchable
choosers, and independently scrolling columns.

What is asserted, each against the behaviour a user relies on:
  * the plan IS the run's approved approach and the spec IS `mokata spec show`'s spec (same
    readers), with its version history, anchors, scope and per-criterion test record;
  * a superseded version is read back from its archive; a missing version and a malformed spec are
    said as such, never shown as "no spec";
  * the lifecycle arc is DERIVED from the pipeline's stage list, marks done / current / pending
    from recorded evidence, and carries gate blocks, review verdicts and amendments with times;
  * every new endpoint is behind the pairing guard, and a run id is validated by shape AND
    existence (no traversal, no unknown run);
  * team mode: the team source reads the shared bundles through the session transport; an
    unreachable store is an explicit `team-unreachable` answer — never local data labelled team;
  * the page: every chooser is the shared combobox (no bare <select>), and the three columns
    declare their own scrolling while the page itself does not scroll.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata.init import init_repo

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "..", "src", "mokata", "ide", "static")

RUN = "a" * 32
RUN2 = "b" * 32


def _silent(*_a, **_k):
    pass


def _client(root, demo=False):
    from starlette.testclient import TestClient
    from mokata.ide.server import AppState, build_app
    state = AppState(root, "tok", 9999, None, demo)
    c = TestClient(build_app(state), base_url="http://127.0.0.1:9999")
    c.get("/pair?t=tok")
    return c


def _store(root, rid):
    from mokata.session_state import scoped_store
    from mokata.state import StateStore
    from mokata.tdd_state import state_dir
    return scoped_store(StateStore(state_dir(root)), rid)


APPROACH = {
    "topic": "empty input crashes the parser",
    "approach": {"name": "Guard empty input", "summary": "Return `[]` for empty input.\n\n- small",
                 "pros": ["one function"], "cons": ["behaviour change"],
                 "targets": ["src/app/core.py::parse"],
                 "decisions": [{"id": "D1", "statement": "parse('') returns []",
                                "rationale_ref": "Q1", "about_code": ["parse"], "deferred": []}]},
    "answered_questions": [{"text": "What should empty input return?", "rationale": "",
                            "answer": "an empty list"}],
    "approver": "jas", "approved_at": "2026-10-15T09:00:00+00:00", "domains": ["parsing"],
}


def _plant(root, *, spec_versions=2, events=True, amend_open=False):
    """A repo with one run that has: an approved plan, a spec at v2 (v1 archived), a TDD record,
    progress events (stage_enter x3, a gate block, a review verdict), and ledger rows (registered,
    amended v1 -> v2)."""
    from mokata.engine.emit import spec_commit
    from mokata.engine.spec import AcceptanceCriterion, Spec
    from mokata.govern.ledger import AuditLedger
    from mokata.govern.resume import PipelineCheckpoint
    from mokata.progress_events import (GATE_BLOCK, REVIEW_VERDICT, STAGE_ENTER, ProgressLog)
    from mokata import tdd_state
    from mokata.tdd_state import state_dir
    init_repo(root=root, profile="standard", assume_yes=True, out=_silent)
    st = _store(root, RUN)
    PipelineCheckpoint(st, RUN).ensure_registered()
    st.write("approved_approach", APPROACH)
    spec_commit(st, Spec("parse handles empty input",
                         [AcceptanceCriterion("AC1", "parse('') returns []")]),
                version=1, run_id=RUN)
    if spec_versions >= 2:
        from mokata.spec_scope import archive_key
        spec_commit(st, Spec("parse handles empty input",
                             [AcceptanceCriterion("AC1", "parse('') returns []"),
                              AcceptanceCriterion("AC2", "fields are **stripped**")]),
                    version=2, archive_key=archive_key(RUN, 1), run_id=RUN)
    tdd_state.record(st, RUN, red=["AC1", "AC2"], green=["AC1"])
    if amend_open:
        from mokata.spec_scope import amend_key
        st.write(amend_key(RUN), {"run_id": RUN, "status": "open", "reason": "widen",
                                  "item": "", "from_version": 2, "to_version": 3,
                                  "red_owed": []})
    if events:
        log = ProgressLog.from_state_dir(state_dir(root))
        log.append_event(STAGE_ENTER, "brainstorm", run_id=RUN)
        log.append_event(STAGE_ENTER, "spec", run_id=RUN)
        log.append_event(STAGE_ENTER, "develop", run_id=RUN)
        log.append_event(GATE_BLOCK, "develop", run_id=RUN, data={"gate": "tdd",
                                                                  "path": "src/app/core.py"})
        log.append_event(STAGE_ENTER, "review", run_id=RUN2)          # another run's — ignored
        log.append_event(REVIEW_VERDICT, "review", run_id=RUN, data={"passed": False,
                                                                     "independent": True})
        led = AuditLedger.from_mokata_dir(os.path.join(root, ".mokata"))
        led.record("checkpoint", run=RUN, phase="registered")
        led.record("spec_amend", run=RUN, from_version=1, to_version=2, reason="AC2")


class PlanAndSpec(unittest.TestCase):

    def test_the_plan_is_the_approved_approach_and_the_spec_is_spec_shows(self):
        with tempfile.TemporaryDirectory() as d:
            _plant(d)
            c = _client(d)
            r = c.get("/api/plan", params={"run": RUN})
            self.assertEqual(200, r.status_code, r.text)
            p = r.json()
            self.assertEqual("local", p["source"])
            self.assertFalse(p["sample"])
            ap = p["approach"]
            self.assertEqual("ok", ap["state"])
            self.assertEqual("Guard empty input", ap["name"])
            self.assertEqual("D1", ap["decisions"][0]["id"])
            self.assertEqual("an empty list", ap["questions"][0]["answer"])
            sp = p["spec"]
            self.assertEqual("ok", sp["state"])
            self.assertEqual(2, sp["version"])
            self.assertFalse(sp["superseded"])
            self.assertEqual(["AC1", "AC2"], [c_["id"] for c_ in sp["criteria"]])
            tests = {c_["id"]: c_["test"] for c_ in sp["criteria"]}
            self.assertEqual({"AC1": "passing", "AC2": "failing"}, tests)
            self.assertEqual([1, 2], [v["version"] for v in p["versions"]])
            self.assertTrue(p["versions"][1]["current"])
            # the SAME criteria the MCP `spec_show` reader answers for the same run
            from mokata.mcp.tools_read import spec_show
            shown = spec_show(path=d, run=RUN)
            self.assertEqual([x["id"] for x in shown["criteria"]], ["AC1", "AC2"])
            self.assertEqual(shown["title"], sp["title"])

    def test_a_superseded_version_is_read_back_from_its_archive(self):
        with tempfile.TemporaryDirectory() as d:
            _plant(d)
            sp = _client(d).get("/api/plan", params={"run": RUN, "version": "1"}).json()["spec"]
            self.assertEqual(1, sp["version"])
            self.assertTrue(sp["superseded"])
            self.assertEqual(["AC1"], [c_["id"] for c_ in sp["criteria"]])
            gone = _client(d).get("/api/plan", params={"run": RUN, "version": "7"}).json()["spec"]
            self.assertEqual("no-such-version", gone["state"])

    def test_absent_and_malformed_are_said_apart(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            st = _store(d, RUN)
            from mokata.govern.resume import PipelineCheckpoint
            PipelineCheckpoint(st, RUN).ensure_registered()
            p = _client(d).get("/api/plan", params={"run": RUN}).json()
            self.assertEqual("absent", p["approach"]["state"])
            self.assertEqual("absent", p["spec"]["state"])
            st.write("emitted_spec", {"criteria": "not a list"})
            st.write("approved_approach", {"no": "topic"})
            p = _client(d).get("/api/plan", params={"run": RUN}).json()
            self.assertEqual("malformed", p["spec"]["state"])
            self.assertEqual("malformed", p["approach"]["state"])

    def test_runs_lists_the_planted_run_with_its_title(self):
        with tempfile.TemporaryDirectory() as d:
            _plant(d)
            r = _client(d).get("/api/runs").json()
            self.assertEqual([RUN], [x["id"] for x in r["runs"]])
            self.assertEqual("parse handles empty input", r["runs"][0]["title"])
            self.assertEqual(RUN, r["default"])
            self.assertFalse(r["team"]["configured"])

    def test_repo_text_is_data_not_markup(self):
        with tempfile.TemporaryDirectory() as d:
            _plant(d)
            st = _store(d, RUN)
            evil = dict(APPROACH, approach=dict(APPROACH["approach"],
                                                summary="<img src=x onerror=alert(1)>"))
            st.write("approved_approach", evil)
            p = _client(d).get("/api/plan", params={"run": RUN}).json()
            self.assertEqual("<img src=x onerror=alert(1)>", p["approach"]["summary"])


class Guards(unittest.TestCase):

    def test_new_routes_need_pairing_and_the_right_host(self):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        with tempfile.TemporaryDirectory() as d:
            _plant(d, events=False)
            app = build_app(AppState(d, "tok", 9999, None, False))
            raw = TestClient(app, base_url="http://127.0.0.1:9999")
            for path in ("/api/runs", f"/api/plan?run={RUN}", f"/api/lifecycle?run={RUN}"):
                self.assertEqual(401, raw.get(path).status_code, path)
            evil = TestClient(app, base_url="http://evil.example:9999")
            self.assertEqual(403, evil.get(f"/api/plan?run={RUN}").status_code)

    def test_run_ids_are_validated_by_shape_and_existence(self):
        with tempfile.TemporaryDirectory() as d:
            _plant(d, events=False)
            c = _client(d)
            for bad in ("", "../x", "..", "a/b", "a\\b", "/etc/passwd", "C:\\x", "a.b",
                        "x" * 200, "%2e%2e", RUN2, "a\x00b", " " + RUN + "/../"):
                for path in ("/api/plan", "/api/lifecycle"):
                    with self.subTest(bad=bad, path=path):
                        r = c.get(path, params={"run": bad})
                        self.assertEqual(400, r.status_code, r.text)
                        self.assertEqual("BadRequest", r.json()["error"])

    def test_bad_source_and_version_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            _plant(d, events=False)
            c = _client(d)
            self.assertEqual(400, c.get("/api/runs", params={"source": "elsewhere"}).status_code)
            for v in ("-1", "x", "1e3", "99999"):
                self.assertEqual(400, c.get("/api/plan", params={"run": RUN, "version": v})
                                 .status_code, v)
            # team source on a repo that is not team-connected: refused, not answered locally
            r = c.get("/api/plan", params={"run": RUN, "source": "team"})
            self.assertEqual(400, r.status_code)
            self.assertEqual(400, c.get("/api/runs", params={"source": "team"}).status_code)

    def test_nothing_is_written_by_reading(self):
        with tempfile.TemporaryDirectory() as d:
            _plant(d)

            def snap():
                out = {}
                for base, _dirs, files in os.walk(os.path.join(d, ".mokata")):
                    for f in files:
                        p = os.path.join(base, f)
                        out[p] = os.path.getsize(p)
                return out
            before = snap()
            c = _client(d)
            c.get("/api/runs")
            c.get("/api/plan", params={"run": RUN, "version": "1"})
            c.get("/api/lifecycle", params={"run": RUN})
            self.assertEqual(before, snap())


class Team(unittest.TestCase):

    def _team(self, d):
        _plant(d, events=False)
        return mock.patch("mokata.session_transport.transport_kind_for_mode",
                          return_value="postgres")

    def test_unreachable_team_store_is_explicit_never_local(self):
        from mokata.session_transport import SessionTransportUnavailable
        with tempfile.TemporaryDirectory() as d, self._team(d):
            with mock.patch("mokata.ide.plans._team_transport",
                            side_effect=SessionTransportUnavailable("no DSN")):
                c = _client(d)
                for path, params in (("/api/runs", {"source": "team"}),
                                     ("/api/plan", {"run": RUN, "source": "team"})):
                    r = c.get(path, params=params)
                    self.assertEqual(200, r.status_code)
                    body = r.json()
                    self.assertEqual("team-unreachable", body["state"])
                    self.assertEqual("team", body["source"])
                    self.assertNotIn("approach", body)
                    self.assertNotIn("runs", body)
            # a driver error mid-read (not a mokata exception) is the same explicit state
            fake = mock.Mock()
            fake.list_tags.side_effect = OSError("connection refused")
            with mock.patch("mokata.ide.plans._team_transport", return_value=fake):
                body = _client(d).get("/api/runs", params={"source": "team"}).json()
                self.assertEqual("team-unreachable", body["state"])

    def test_team_source_reads_the_shared_bundle_through_the_transport(self):
        from mokata.config import Surface
        from mokata.session_bundle import build_session_bundle, serialize_bundle
        with tempfile.TemporaryDirectory() as d, self._team(d):
            surface = Surface.load(d)
            surface.state = _store(d, RUN)
            bundle = build_session_bundle(surface, run_id=RUN, author="teammate",
                                          now="2026-10-15T10:00:00+00:00")
            blob = serialize_bundle(bundle)

            class Fake:
                name = "postgres"

                def list_tags(self):
                    return ["shared-tag"]

                def read_bundle(self, tag):
                    return blob if tag == "shared-tag" else None
            with mock.patch("mokata.ide.plans._team_transport", return_value=Fake()):
                c = _client(d)
                runs = c.get("/api/runs", params={"source": "team"}).json()
                self.assertEqual("ok", runs["state"])
                self.assertEqual([RUN], [r["id"] for r in runs["runs"]])
                self.assertEqual("teammate", runs["runs"][0]["author"])
                p = c.get("/api/plan", params={"run": RUN, "source": "team"}).json()
                self.assertEqual("team", p["source"])
                self.assertIn("team store", p["label"])
                self.assertEqual("Guard empty input", p["approach"]["name"])
                self.assertEqual(["AC1", "AC2"], [x["id"] for x in p["spec"]["criteria"]])
                self.assertEqual(400, c.get("/api/plan", params={"run": RUN2, "source": "team"})
                                 .status_code)

    def test_team_reads_reuse_the_session_channel_and_hold_no_sql(self):
        with open(os.path.join(HERE, "..", "src", "mokata", "ide", "plans.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        for needle in ("transport_kind_for_mode", 'make_transport("postgres"', "parse_bundle"):
            self.assertIn(needle, src)
        for sql in ("SELECT ", "INSERT ", "psycopg", ".execute("):
            self.assertNotIn(sql, src)


class Lifecycle(unittest.TestCase):

    def test_the_arc_is_derived_from_the_pipeline_stage_list(self):
        from mokata.ide.plans import lifecycle_stages
        from mokata.progress import STAGE_BADGE_STAGES
        from mokata.stacks import _COMMON_SKILLS
        arc = lifecycle_stages()
        self.assertEqual(tuple(_COMMON_SKILLS), arc)
        self.assertEqual(list(STAGE_BADGE_STAGES), [s for s in arc if s in STAGE_BADGE_STAGES])
        with mock.patch("mokata.stacks._COMMON_SKILLS", ("spec", "brainstorm")):
            self.assertEqual(tuple(STAGE_BADGE_STAGES), lifecycle_stages())

    def test_done_current_pending_blocks_amendments_and_times(self):
        with tempfile.TemporaryDirectory() as d:
            _plant(d)
            lc = _client(d).get("/api/lifecycle", params={"run": RUN}).json()
            st = {r["stage"]: r for r in lc["stages"]}
            self.assertEqual(["brainstorm", "spec", "test", "develop", "review", "ship"],
                             [r["stage"] for r in lc["stages"]])
            self.assertEqual("review", lc["current"])
            for s in ("brainstorm", "spec", "test", "develop"):
                self.assertEqual("done", st[s]["status"], s)
            self.assertEqual("current", st["review"]["status"])
            self.assertEqual("pending", st["ship"]["status"])
            self.assertEqual("recorded", st["test"]["evidence"])
            self.assertEqual(1, st["develop"]["blocks"])
            self.assertEqual(1, st["spec"]["amendments"])
            kinds = [m["kind"] for m in lc["marks"]]
            for k in ("registered", "stage_enter", "gate_block", "review_verdict", "spec_amend",
                      "approved"):
                self.assertIn(k, kinds)
            self.assertNotIn("review", [m["stage"] for m in lc["marks"]
                                        if m["kind"] == "stage_enter"])  # RUN2's — not this run
            self.assertEqual("registration", lc["started_basis"])
            self.assertGreater(lc["started"], 0)
            ts = [m["t"] for m in lc["marks"] if m["t"]]
            self.assertEqual(sorted(ts), ts)

    def test_an_open_amendment_puts_the_run_back_at_spec(self):
        with tempfile.TemporaryDirectory() as d:
            _plant(d, amend_open=True)
            lc = _client(d).get("/api/lifecycle", params={"run": RUN}).json()
            self.assertTrue(lc["regressed"])
            self.assertEqual("spec", lc["current"])

    def test_a_finished_run_is_all_done(self):
        from mokata.progress_events import RUN_FINISHED, ProgressLog
        from mokata.tdd_state import state_dir
        with tempfile.TemporaryDirectory() as d:
            _plant(d)
            ProgressLog.from_state_dir(state_dir(d)).append_event(
                RUN_FINISHED, "ship", run_id=RUN, data={"landing": "merged"})
            lc = _client(d).get("/api/lifecycle", params={"run": RUN}).json()
            self.assertTrue(lc["finished"])
            self.assertIsNone(lc["current"])
            self.assertEqual({"done"}, {r["status"] for r in lc["stages"]})

    def test_events_read_is_bounded(self):
        from mokata.ide import plans
        from mokata.progress_events import STAGE_ENTER, ProgressLog
        from mokata.tdd_state import state_dir
        with tempfile.TemporaryDirectory() as d:
            _plant(d, events=False)
            log = ProgressLog.from_state_dir(state_dir(d))
            with mock.patch.object(plans, "MAX_EVENTS", 5):
                for _ in range(12):
                    log.append_event(STAGE_ENTER, "develop", run_id=RUN)
                self.assertEqual(5, len(plans._run_events(d, RUN)))

    def test_demo_is_labelled_sample(self):
        with tempfile.TemporaryDirectory() as d:
            c = _client(d, demo=True)
            runs = c.get("/api/runs").json()
            self.assertTrue(runs["sample"])
            rid = runs["default"]
            for path in ("/api/plan", "/api/lifecycle"):
                body = c.get(path, params={"run": rid}).json()
                self.assertTrue(body["sample"], path)
            self.assertEqual(400, c.get("/api/plan", params={"run": "../x"}).status_code)
            self.assertEqual(400, c.get("/api/plan", params={"run": "nope"}).status_code)


class Page(unittest.TestCase):

    def _read(self, name):
        with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
            return fh.read()

    def test_no_bare_select_every_chooser_is_the_combobox(self):
        html = self._read("index.html")
        self.assertNotRegex(html, r"<select\b")
        for js in ("app.js", "nav.js", "search.js", "graph.js", "plan.js", "combo.js", "boot.js"):
            self.assertNotIn('createElement("select")', self._read(js), js)
            self.assertNotIn('el("select"', self._read(js), js)
        for cid in ("base", "head", "g-depth", "insp-pick", "session-pick", "plan-run",
                    "plan-version"):
            m = re.search(rf'<div[^>]*\bid="{cid}"[^>]*>', html)
            self.assertIsNotNone(m, cid)
            self.assertIn('class="combo"', m.group(0), cid)
            self.assertIn("data-label=", m.group(0), cid)

    def test_the_combobox_is_accessible_and_keyboard_driven(self):
        js = self._read("combo.js")
        for needle in ('"combobox"', '"listbox"', '"option"', "aria-activedescendant",
                       "aria-expanded", "aria-controls", "aria-selected", '"ArrowDown"',
                       '"ArrowUp"', '"Enter"', '"Escape"', "textContent"):
            self.assertIn(needle, js)
        self.assertNotIn("innerHTML", js)
        self.assertLess(self._read("index.html").index("combo.js"),
                        self._read("index.html").index("boot.js"))

    def test_three_columns_scroll_independently_and_the_page_does_not(self):
        css = self._read("app.css")

        def rule(sel):
            m = re.search(r"(?m)^" + re.escape(sel) + r"\s*\{([^}]*)\}", css)
            self.assertIsNotNone(m, sel)
            return m.group(1)
        body = rule("html, body")
        self.assertIn("overflow: hidden", body)
        for sel in (".left", ".center", ".right"):
            r = rule(sel)
            self.assertIn("min-height: 0", r, sel)
            self.assertRegex(r, r"overflow(-y)?: (auto|hidden)", sel)
        for sel in (".lp", ".scroll", ".right"):
            self.assertRegex(rule(sel), r"overflow(-y)?: auto", sel)
        # no media query turns the page itself back into the scroller
        self.assertNotRegex(css, r"body\s*\{[^}]*overflow:\s*auto")
        for fixed in (".top", ".lc-strip"):
            self.assertIn("flex: none", rule(fixed), fixed)

    def test_lifecycle_strip_sits_under_the_top_bar(self):
        html = self._read("index.html")
        self.assertLess(html.index('class="top"'), html.index('id="lifecycle"'))
        self.assertLess(html.index('id="lifecycle"'), html.index('class="shell"'))
        self.assertIn('id="lc-detail"', html)
        self.assertIn('data-mode="plan"', html)
        self.assertIn('id="view-plan"', html)

    def test_plan_renderer_builds_text_nodes_only(self):
        js = self._read("plan.js")
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            self.assertNotIn(bad, js)
        self.assertIn("mdLite", js)

    def test_md_lite_never_makes_markup(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not on PATH — mdLite is exercised in the browser smoke instead")
        js = self._read("plan.js")
        m = re.search(r"// --- mdLite ---(.*?)// --- end mdLite ---", js, re.S)
        self.assertIsNotNone(m)
        prog = """
const made = [];
const node = (tag) => ({ tag, kids: [], text: "", append(...k) { this.kids.push(...k); } });
globalThis.document = {
  createElement: (t) => { const n = node(t); made.push(t); return n; },
  createTextNode: (t) => ({ tag: "#text", text: t }),
  createDocumentFragment: () => node("#frag"),
};
Object.defineProperty(Object.prototype, "textContent", {
  set(v) { this.text = v; }, get() { return this.text; }, configurable: true });
const el = (tag, cls, text) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = String(text); return n; };
""" + m.group(1) + """
const f = mdLite("hello **<b>x</b>** and `<script>`\\n\\n- one\\n- <img onerror=1>");
const texts = []; const walk = (n) => { if (n.tag === "#text") texts.push(n.text); if (n.text) texts.push(n.text); (n.kids || []).forEach(walk); };
walk(f);
console.log(JSON.stringify({ made: [...new Set(made)].sort(), texts }));
"""
        out = subprocess.run([node, "-e", prog], capture_output=True, text=True, timeout=30)
        self.assertEqual(0, out.returncode, out.stderr)
        res = json.loads(out.stdout)
        self.assertTrue(set(res["made"]) <= {"p", "ul", "li", "b", "code", "h4"}, res["made"])
        joined = "".join(res["texts"])
        self.assertIn("<b>x</b>", joined)
        self.assertIn("<script>", joined)
        self.assertIn("<img onerror=1>", joined)


if __name__ == "__main__":
    unittest.main()
