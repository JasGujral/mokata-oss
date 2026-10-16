"""0.0.23 D12 — G4: the mokata-mcp harness-certification rig (Cursor / Codex CLI / Gemini CLI /
Copilot CLI).

Business-level claims graded here:
  * the six criteria are explicit and stable, and the doc + matrix file key on exactly them;
  * the contract suite drives them against the REAL `mokata-mcp` over stdio from a minimal client
    (no third-party client), for a repo wired by `mokata setup <harness>`;
  * every criterion can FAIL — a dead server, version skew, a leaky gate, a failed mint are each
    reported as such, never as a pass (negative controls, incl. a fake leaky server);
  * NOTHING claims certification: the shipped matrix certifies no client, and `certified()` needs
    an evidenced PASS (version + recorder + date) on every criterion.
"""

import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest

import _support  # noqa: F401  (puts src/ on the path)
from _mcp_sdk import requires_mcp_sdk

from mokata import harness_cert as hc
from mokata.harness_setup import setup_harness

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
_REPO = os.path.dirname(_SRC)
_SERVER = [sys.executable, "-m", "mokata.mcp_server"]


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def _wired(test, harness):
    d = tempfile.mkdtemp(prefix="mk-cert-")
    test.addCleanup(shutil.rmtree, d, True)
    setup_harness(harness, root=d, assume_yes=True, out=lambda *_: None)
    return d


# A fake MCP server whose `remember` COMMITS without any approval — the gate leak C5 must catch.
_LEAKY = textwrap.dedent('''
    import json, sys
    mem = {}
    def reply(i, result): sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": i, "result": result}) + "\\n"); sys.stdout.flush()
    for line in sys.stdin:
        m = json.loads(line)
        if "id" not in m: continue
        meth, p = m["method"], m.get("params") or {}
        if meth == "initialize": reply(m["id"], {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "leaky"}})
        elif meth == "tools/list": reply(m["id"], {"tools": [{"name": n} for n in ("status", "remember", "recall")]})
        elif meth == "tools/call":
            a = p["arguments"]
            if p["name"] == "status": out = {"version": "X"}
            elif p["name"] == "remember": mem[a["subject"]] = a["value"]; out = {"status": "committed", "committed": True}
            else: out = {"items": [{"subject": k, "value": v} for k, v in mem.items()]}
            reply(m["id"], {"content": [{"type": "text", "text": json.dumps(out)}]})
''')


class CriteriaAreExplicit(unittest.TestCase):
    def test_six_stable_criteria_and_four_clients(self):
        self.assertEqual(hc.CRITERION_IDS, ("C1", "C2", "C3", "C4", "C5", "C6"))
        self.assertTrue(all(c.statement for c in hc.CRITERIA))
        self.assertEqual(set(hc.CERT_CLIENTS), {"cursor", "codex", "gemini", "copilot"})

    def test_doc_names_every_criterion_and_client(self):
        with open(os.path.join(_REPO, "docs", "reference", "harness-certification.md"),
                  encoding="utf-8") as fh:
            doc = fh.read()
        for cid in hc.CRITERION_IDS:
            self.assertIn(cid, doc)
        for c in hc.CERT_CLIENTS.values():
            self.assertIn(c.display, doc)
        self.assertIn("no client is certified", doc.lower())

    def test_checklist_covers_every_criterion_and_manual_config(self):
        for key, c in hc.CERT_CLIENTS.items():
            text = hc.checklist(key)
            for cid in hc.CRITERION_IDS:
                self.assertIn(f"**{cid} ", text)
            self.assertIn(f"mokata setup {c.harness}", text)
            if c.manual_config:
                self.assertIn("mokata-mcp", text)
                self.assertIn("`manual`", text)


class ConfigProbe(unittest.TestCase):
    def test_auto_wired_clients_read_back_the_written_entry(self):
        for client in ("cursor", "gemini"):
            probe = hc.setup_config_entry(client, _wired(self, client))
            self.assertEqual(probe.state, hc.CONFIG_WRITTEN, probe.detail)
            self.assertEqual(probe.command, "mokata-mcp")

    def test_manual_clients_are_manual_never_written(self):
        for client in ("codex", "copilot"):
            probe = hc.setup_config_entry(client, _wired(self, client))
            self.assertEqual(probe.state, hc.CONFIG_MANUAL)
            self.assertIsNone(probe.path)

    def test_missing_unreadable_and_entryless_configs(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.assertEqual(hc.setup_config_entry("cursor", d).state, hc.CONFIG_MISSING)
        os.makedirs(os.path.join(d, ".cursor"))
        p = os.path.join(d, ".cursor", "mcp.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(hc.setup_config_entry("cursor", d).state, hc.CONFIG_UNREADABLE)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"mcpServers": {"other": {"command": "x"}}}, fh)
        self.assertEqual(hc.setup_config_entry("cursor", d).state, hc.CONFIG_MISSING)

    def test_unknown_client_refused(self):
        with self.assertRaises(ValueError):
            hc.run_contract("windsurf", ".")


@requires_mcp_sdk
class ContractSuiteAgainstTheRealServer(unittest.TestCase):
    def test_cursor_all_six_pass_over_stdio(self):
        root = _wired(self, "cursor")
        rep = hc.run_contract("cursor", root, launch=_SERVER, env=_env())
        self.assertEqual([r.status for r in rep.results], [hc.PASS] * 6, rep.render())
        self.assertTrue(rep.server_contract_ok)
        self.assertIn("SERVER half only", rep.render())

    def test_codex_c1_is_manual_and_the_server_half_still_holds(self):
        root = _wired(self, "codex")
        rep = hc.run_contract("codex", root, launch=_SERVER, env=_env())
        self.assertEqual(rep.status("C1"), hc.MANUAL)
        self.assertTrue(rep.server_contract_ok, rep.render())

    def test_version_skew_fails_c3_and_c4(self):
        root = _wired(self, "gemini")
        from mokata.mcp.registry import tool_names
        rep = hc.run_contract("gemini", root, launch=_SERVER, env=_env(),
                              expected_tools=tool_names() + ["a_tool_from_another_mokata"],
                              expected_version="9.9.9")
        self.assertEqual(rep.status("C3"), hc.FAIL)
        self.assertIn("skew", rep.results[2].detail)
        self.assertEqual(rep.status("C4"), hc.FAIL)

    def test_a_failed_human_mint_fails_c6_and_nothing_lands(self):
        root = _wired(self, "cursor")
        rep = hc.run_contract("cursor", root, launch=_SERVER, env=_env(),
                              approve=lambda r, pid: False)
        self.assertEqual(rep.status("C5"), hc.PASS)
        self.assertEqual(rep.status("C6"), hc.FAIL)
        from mokata.memory import MemoryStore
        from mokata.config import Surface
        store = MemoryStore.from_surface(Surface.load(root))
        self.assertEqual(store.recall("mokata-cert-probe"), [])


class NegativeControls(unittest.TestCase):
    def test_a_server_that_dies_fails_c2_and_blocks_the_rest(self):
        root = _wired(self, "cursor")
        rep = hc.run_contract("cursor", root, launch=[sys.executable, "-c", "import sys; sys.exit(3)"],
                              timeout=20)
        self.assertEqual(rep.status("C2"), hc.FAIL)
        self.assertEqual([r.status for r in rep.results[2:]], [hc.BLOCKED] * 4)
        self.assertFalse(rep.server_contract_ok)

    def test_no_launchable_entry_blocks_everything_after_c1(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        rep = hc.run_contract("cursor", d)            # no setup, no override
        self.assertEqual(rep.status("C1"), hc.FAIL)
        self.assertEqual([r.status for r in rep.results[1:]], [hc.BLOCKED] * 5)

    def test_a_leaky_gate_fails_c5(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        script = os.path.join(d, "leaky.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(_LEAKY)
        rep = hc.run_contract("gemini", d, launch=[sys.executable, script],
                              expected_tools=["status", "remember", "recall"],
                              expected_version="X", timeout=20)
        self.assertEqual(rep.status("C2"), hc.PASS)
        self.assertEqual(rep.status("C5"), hc.FAIL, rep.render())
        self.assertIn("gate leaked", rep.results[4].detail)


class TheMatrixClaimsNothing(unittest.TestCase):
    def test_shipped_matrix_certifies_no_client(self):
        m = hc.load_matrix(os.path.join(_REPO, "docs", "reference", "harness-cert-matrix.json"))
        self.assertEqual(set(m["clients"]), set(hc.CERT_CLIENTS))
        for key in hc.CERT_CLIENTS:
            self.assertFalse(hc.certified(m, key))
            for cid in hc.CRITERION_IDS:
                self.assertEqual(m["clients"][key][cid]["result"], hc.UNTESTED)

    def _tmp_matrix(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        return os.path.join(d, "m.json")

    def test_certified_only_with_an_evidenced_pass_on_every_criterion(self):
        path = self._tmp_matrix()
        for cid in hc.CRITERION_IDS[:-1]:
            hc.record_result(path, "gemini", cid, hc.PASS, client_version="0.9",
                             recorder="owner", date="2026-10-15")
        self.assertFalse(hc.certified(hc.load_matrix(path), "gemini"))
        m = hc.record_result(path, "gemini", "C6", hc.PASS, client_version="0.9",
                             recorder="owner", date="2026-10-15")
        self.assertTrue(hc.certified(m, "gemini"))
        self.assertFalse(hc.certified(m, "cursor"))
        self.assertIn("| Gemini CLI | pass | pass | pass | pass | pass | pass | yes |",
                      hc.render_matrix(m))

    def test_manual_c1_never_certifies(self):
        path = self._tmp_matrix()
        hc.record_result(path, "codex", "C1", hc.MANUAL, client_version="1", recorder="o",
                         date="d")
        for cid in hc.CRITERION_IDS[1:]:
            m = hc.record_result(path, "codex", cid, hc.PASS, client_version="1", recorder="o",
                                 date="d")
        self.assertFalse(hc.certified(m, "codex"))

    def test_unevidenced_or_bogus_records_are_refused(self):
        path = self._tmp_matrix()
        with self.assertRaises(ValueError):
            hc.record_result(path, "cursor", "C1", hc.PASS, client_version="", recorder="o",
                             date="d")
        with self.assertRaises(ValueError):
            hc.record_result(path, "cursor", "C1", hc.UNTESTED, client_version="1",
                             recorder="o", date="d")
        with self.assertRaises(ValueError):
            hc.record_result(path, "cursor", "C9", hc.PASS, client_version="1", recorder="o",
                             date="d")
        self.assertFalse(os.path.exists(path))

    def test_hand_edited_unevidenced_pass_does_not_certify(self):
        m = hc.empty_matrix()
        for cid in hc.CRITERION_IDS:
            m["clients"]["cursor"][cid] = {"result": hc.PASS}
        self.assertFalse(hc.certified(m, "cursor"))

    def test_an_unparseable_matrix_refuses_rather_than_blanking(self):
        path = self._tmp_matrix()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("[]")
        with self.assertRaises(ValueError):
            hc.load_matrix(path)
        with self.assertRaises(ValueError):
            hc.record_result(path, "cursor", "C1", hc.PASS, client_version="1", recorder="o",
                             date="d")
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "[]")


class TheOwnerScript(unittest.TestCase):
    def _run(self, *args):
        import subprocess
        return subprocess.run([sys.executable, os.path.join(_REPO, "scripts", "harness_cert.py"),
                               *args], capture_output=True, text=True, timeout=120, env=_env())

    def test_show_and_checklist(self):
        cp = self._run("show")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("| Cursor | untested", cp.stdout)
        self.assertNotIn("| yes |", cp.stdout)
        cp = self._run("checklist", "codex")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("[mcp_servers.mokata]", cp.stdout)

    def test_record_refuses_without_evidence_flags(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        cp = self._run("record", "cursor", "C1", "pass", "--matrix", os.path.join(d, "m.json"))
        self.assertNotEqual(cp.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(d, "m.json")))


if __name__ == "__main__":
    unittest.main()
