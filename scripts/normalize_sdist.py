#!/usr/bin/env python3
"""Stage 68 — deterministic sdist normalizer (stdlib only; no dependency).

setuptools writes a few build-GENERATED members into the sdist (`PKG-INFO`, `setup.cfg`,
`*.egg-info/*`, and the directory entries) stamped with the wall-clock build time rather than
clamped to SOURCE_DATE_EPOCH, so two builds of the same commit produce different tarballs even
though the source FILE contents are identical. This repacks a `.tar.gz` deterministically —
fixed member order, fixed mtimes (SOURCE_DATE_EPOCH), zeroed ownership, normalized modes, and a
gzip wrapper with mtime 0 — so building twice yields a byte-identical sdist.

It touches ONLY archive metadata (mtimes/uids/ordering), never file CONTENTS, so the installed
package is unchanged. Wheels are already reproducible (zip honors SOURCE_DATE_EPOCH); this is the
sdist's missing piece.

    python3 scripts/normalize_sdist.py dist/mokata-X.Y.Z.tar.gz

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import gzip
import io
import os
import sys
import tarfile


def _epoch() -> int:
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    try:
        return int(raw) if raw else 0
    except ValueError:
        return 0


def normalize(path: str) -> None:
    epoch = _epoch()
    with open(path, "rb") as fh:
        src = tarfile.open(fileobj=io.BytesIO(gzip.decompress(fh.read())))

    # Collect (TarInfo, payload) sorted by name for a stable member order.
    entries = []
    for member in src.getmembers():
        data = src.extractfile(member).read() if member.isreg() else None
        entries.append((member, data))
    src.close()
    entries.sort(key=lambda e: e[0].name)

    buf = io.BytesIO()
    out = tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT)
    for member, data in entries:
        ti = tarfile.TarInfo(member.name)
        ti.type = member.type
        ti.mode = 0o755 if member.isdir() else 0o644
        ti.mtime = epoch                      # deterministic timestamp
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = ""              # no build-host identity
        ti.size = len(data) if data is not None else 0
        if member.islnk() or member.issym():
            ti.linkname = member.linkname
        out.addfile(ti, io.BytesIO(data) if data is not None else None)
    out.close()

    # gzip with mtime=0 so the wrapper carries no build timestamp either.
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        gz = gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0)
        gz.write(buf.getvalue())
        gz.close()
    os.replace(tmp, path)


def main(argv):
    if not argv:
        print("usage: normalize_sdist.py <sdist.tar.gz> [...]", file=sys.stderr)
        return 2
    for path in argv:
        normalize(path)
        print(f"normalized (deterministic): {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
