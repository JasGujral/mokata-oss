"""VERIFIER PROBE 2 (lane B) - real servers' file URIs on Windows. Scratch, probe branch only."""
import faulthandler
faulthandler.dump_traceback_later(420, exit=True)
import json
import os
import shutil
import subprocess
import tempfile
import unittest

import _support  # noqa: F401

from mokata.ide import lsp as L

NT = os.name == "nt"


@unittest.skipUnless(NT, "windows probe")
class RealUris(unittest.TestCase):
    def test_from_uri_forms(self):
        for u in ("file:///C:/Users/x/a.ts", "file:///c:/Users/x/a.ts", "file:///c%3A/Users/x/a.ts"):
            print("FROM_URI", u, "->", repr(L.from_uri(u)), flush=True)

    def _proj(self, deps):
        d = os.path.realpath(tempfile.mkdtemp())
        with open(os.path.join(d, "package.json"), "w") as fh:
            json.dump({"name": "p", "version": "1.0.0"}, fh)
        r = subprocess.run([shutil.which("npm"), "install", "--no-audit", "--no-fund", *deps], cwd=d,
                           capture_output=True, text=True)
        print("NPM", r.returncode, r.stderr[-300:], flush=True)
        return d

    def _refs(self, d, language, name, argv, decl_rel, line0, ch, lang_id):
        srv = L.Server(d, language, name, argv)
        try:
            srv.start()
            uri, _ = srv.open(os.path.join(d, *decl_rel.split("/")), lang_id)
            import time
            res = None
            for _ in range(20):
                res = srv.request("textDocument/references", {
                    "textDocument": {"uri": uri}, "position": {"line": line0, "character": ch},
                    "context": {"includeDeclaration": True}}, wait=60)
                if res and len(res) >= 2:
                    break
                time.sleep(1)
            print(name, "RAW URIS", sorted({r["uri"] for r in (res or [])}), flush=True)
            locs = [L.location(d, r, L._Lines()) for r in (res or [])]
            for x in locs:
                print(name, "LOC", x["path"], x["line"], "outside" if x["outside"] else "inside",
                      repr(x["snippet"]), flush=True)
            return locs
        finally:
            srv.stop("probe")

    def test_typescript_references_across_files(self):
        d = self._proj(["typescript@5.8.3", "typescript-language-server@4.3.4"])
        os.makedirs(os.path.join(d, "src"))
        with open(os.path.join(d, "tsconfig.json"), "w") as fh:
            json.dump({"compilerOptions": {"strict": True}, "include": ["src"]}, fh)
        with open(os.path.join(d, "src", "a.ts"), "w") as fh:
            fh.write("export function parse(t: string) { return t; }\n")
        with open(os.path.join(d, "src", "b.ts"), "w") as fh:
            fh.write("import { parse } from './a';\nexport const y = parse('x');\n")
        f = L.find_server(d, "typescript")
        print("TS FIND", f["argv"], flush=True)
        locs = self._refs(d, "typescript", "typescript-language-server", f["argv"], "src/a.ts", 0, 17,
                          "typescript")
        self.assertTrue(locs)
        self.assertEqual([], [x for x in locs if x["outside"]], "a location inside the repo read as outside")

    def test_pyright_references_across_files(self):
        d = self._proj(["pyright@1.1.400"])
        os.makedirs(os.path.join(d, "pkg"))
        with open(os.path.join(d, "pkg", "__init__.py"), "w") as fh:
            fh.write("")
        with open(os.path.join(d, "pkg", "a.py"), "w") as fh:
            fh.write("def parse(t):\n    return t\n")
        with open(os.path.join(d, "pkg", "b.py"), "w") as fh:
            fh.write("from pkg.a import parse\ny = parse('x')\n")
        f = L.find_server(d, "python")
        print("PY FIND", f["argv"], flush=True)
        locs = self._refs(d, "python", f["name"], f["argv"], "pkg/a.py", 0, 5, "python")
        self.assertTrue(locs)
        self.assertEqual([], [x for x in locs if x["outside"]], "a location inside the repo read as outside")


if __name__ == "__main__":
    unittest.main()
