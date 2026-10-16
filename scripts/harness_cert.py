#!/usr/bin/env python3
"""G4 — run / record the mokata-mcp harness-certification matrix.

    python scripts/harness_cert.py contract <client|all> [--use-config] [--keep]
        Local contract run (C1..C6) against a scratch repo wired by `mokata setup <harness>`.
        By default it launches THIS interpreter's `python -m mokata.mcp_server`; --use-config
        launches the command the written config names instead (what the real client would run).
        Proves the SERVER half only — it never records into the matrix.
    python scripts/harness_cert.py checklist <client>
        Print the owner's run sheet for the REAL client.
    python scripts/harness_cert.py record <client> <C1..C6> <pass|fail|manual>
            --client-version X --by NAME [--date YYYY-MM-DD] [--note ...]
        Record one owner-observed result into the matrix file.
    python scripts/harness_cert.py show
        Print the matrix. A client is certified only with an evidenced PASS on every criterion.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import datetime
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
if os.path.isdir(os.path.join(_SRC, "mokata")):
    sys.path.insert(0, os.path.abspath(_SRC))

from mokata import harness_cert as hc  # noqa: E402

DEFAULT_MATRIX = os.path.join(_HERE, "..", "docs", "reference", "harness-cert-matrix.json")


def _env() -> dict:
    env = dict(os.environ)
    src = os.path.abspath(_SRC)
    if os.path.isdir(os.path.join(src, "mokata")):
        env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def _contract(client: str, use_config: bool, keep: bool) -> int:
    root = tempfile.mkdtemp(prefix=f"mokata-cert-{client}-")
    env = _env()
    try:
        subprocess.run(["git", "init", "-q", root], check=True, timeout=60)
        cp = subprocess.run([sys.executable, "-m", "mokata", "setup", hc.CERT_CLIENTS[client].harness,
                             "--yes", "--path", root], env=env, capture_output=True, text=True,
                            timeout=300)
        if cp.returncode != 0:
            print(f"`mokata setup` failed:\n{cp.stdout}\n{cp.stderr}")
            return 1
        launch = None if use_config else [sys.executable, "-m", "mokata.mcp_server"]
        rep = hc.run_contract(client, root, launch=launch, env=env)
        print(rep.render())
        return 0 if rep.server_contract_ok else 1
    finally:
        if keep:
            print(f"(kept {root})")
        else:
            shutil.rmtree(root, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="harness_cert")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("contract")
    c.add_argument("client", choices=list(hc.CERT_CLIENTS) + ["all"])
    c.add_argument("--use-config", action="store_true")
    c.add_argument("--keep", action="store_true")
    k = sub.add_parser("checklist")
    k.add_argument("client", choices=list(hc.CERT_CLIENTS))
    r = sub.add_parser("record")
    r.add_argument("client", choices=list(hc.CERT_CLIENTS))
    r.add_argument("criterion", choices=list(hc.CRITERION_IDS))
    r.add_argument("result", choices=[hc.PASS, hc.FAIL, hc.MANUAL])
    r.add_argument("--client-version", required=True)
    r.add_argument("--by", required=True)
    r.add_argument("--date", default=datetime.date.today().isoformat())
    r.add_argument("--note", default="")
    r.add_argument("--matrix", default=DEFAULT_MATRIX)
    s = sub.add_parser("show")
    s.add_argument("--matrix", default=DEFAULT_MATRIX)
    a = ap.parse_args(argv)

    if a.cmd == "contract":
        clients = list(hc.CERT_CLIENTS) if a.client == "all" else [a.client]
        return max(_contract(cl, a.use_config, a.keep) for cl in clients)
    if a.cmd == "checklist":
        print(hc.checklist(a.client))
        return 0
    if a.cmd == "record":
        m = hc.record_result(a.matrix, a.client, a.criterion, a.result,
                             client_version=a.client_version, recorder=a.by, date=a.date,
                             note=a.note)
        print(hc.render_matrix(m))
        return 0
    print(hc.render_matrix(hc.load_matrix(a.matrix)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
