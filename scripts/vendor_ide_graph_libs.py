#!/usr/bin/env python3
"""Re-fetch (or just verify) the graph libraries `mokata ide` vendors — 0.0.23 C3, ruling R3.

    python scripts/vendor_ide_graph_libs.py           fetch each pinned npm tarball, check its
                                                      registry integrity (sha512), extract the
                                                      pinned member, and check its sha256
    python scripts/vendor_ide_graph_libs.py --check   offline: verify the files on disk only

The pins live in src/mokata/ide/static/vendor/VERSIONS.json. Nothing here runs at mokata runtime
and nothing is loaded from a CDN: the page serves these files from /static/vendor/.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import sys
import tarfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "..", "src", "mokata", "ide", "static", "vendor")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch(url: str, integrity: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as resp:     # noqa: S310 — pinned registry URL
        data = resp.read()
    algo, _, want = integrity.partition("-")
    got = base64.b64encode(hashlib.new(algo, data).digest()).decode()
    if got != want:
        raise SystemExit(f"integrity mismatch for {url}: registry says {want}, got {got}")
    return data


def _member(tgz: bytes, name: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as tf:
        fh = tf.extractfile(name)
        if fh is None:
            raise SystemExit(f"{name} is not a regular file in the tarball")
        return fh.read()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify the vendored files only (offline)")
    a = ap.parse_args(argv)
    with open(os.path.join(VENDOR, "VERSIONS.json"), encoding="utf-8") as fh:
        pins = json.load(fh)
    bad = 0
    for row in pins["files"]:
        path = os.path.join(VENDOR, row["file"])
        if not a.check:
            tgz = _fetch(row["tarball"], row["integrity"])
            data = _member(tgz, row["member"])
            if _sha256(data) != row["sha256"]:
                raise SystemExit(f"{row['file']}: the tarball member's sha256 is not the pinned one")
            with open(path, "wb") as fh:
                fh.write(data)
            lic = _member(tgz, "package/LICENSE")
            with open(os.path.join(VENDOR, row["license_file"]), "wb") as fh:
                fh.write(lic)
        with open(path, "rb") as fh:
            ok = _sha256(fh.read()) == row["sha256"]
        print(f"{'ok ' if ok else 'BAD'} {row['file']}  {row['package']}@{row['version']}")
        bad += not ok
    if not a.check:
        for row in pins.get("licenses_only", []):
            tgz = _fetch(row["tarball"], row["integrity"])
            with open(os.path.join(VENDOR, row["file"]), "wb") as fh:
                fh.write(_member(tgz, row["member"]))
            print(f"ok  {row['file']}  {row['package']}@{row['version']}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
