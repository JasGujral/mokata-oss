"""A changed image, before against after (0.0.27 P9 - `ID-IMAGE-DIFF`). The server never decodes a
pixel: it reads a file's HEADER (dimensions, byte size), decides whether the browser may be handed
the image, and serves the bytes. The modes (side by side, swipe, onion skin, pixel difference) are
drawn by the page; the pixel difference runs in a Worker off the main thread.

    is_image_path(name)            a supported image NAME (the type is the name's, never the bytes')
    sniff(name, head)              dimensions from the header bytes - or why the file is not that image
    pair(root, path, old, base, head)   both sides of one changed image: state, size, dimensions
    read_image(root, path, rev)    the bytes of ONE side, bounded - what `/api/image/raw` serves

THE RULES (each one a test in tests/test_p9_ide_image_diff.py):
  * the answer for a side is one of `ok`, `missing` (the file is not on that side: an added or a
    deleted image has ONE side, and `status` says which), `too_large`, `undecodable`, `unreadable`;
  * a side is `too_large` by BYTES (over `MAX_BYTES`) or by PIXELS (over `MAX_VIEW_PIXELS`: a
    20000x20000 PNG is a few KB on disk and 1.6 GB decoded) and says so in words - "too large to
    diff - 200.0 MB, open externally" - never a silent cap and never a blank. Only the header is
    read for that verdict, so a 200 MB file costs the same as a 2 KB one;
  * the pixel difference decodes BOTH images, so it has its own, lower budget (`MAX_DIFF_PIXELS`
    over the larger of each dimension): a pair over it keeps the other modes and is told why;
  * a file is an image only if its HEADER agrees with its name (a `.png` holding HTML is
    `undecodable`, not served as a PNG); the Content-Type is the name's, never sniffed from content;
  * reads are contained: `snapshots.rel_path` (realpath'd - a symlink out of the repo, `..`, an
    absolute path outside are refused; `.git` and `.mokata` in ANY spelling - case, Unicode twin,
    trailing dot/space, a disk alias - are refused by its one folded rule), the working tree is
    opened with `diffs.open_contained` (no final symlink, regular file, re-checked after the open),
    the base side comes from `git cat-file` at a validated revision. No path or revision from the
    request is ever handed to a shell or to git as an option.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import re
import struct
import time
import subprocess
from typing import Any, Callable, Dict, Iterable, Iterator, Optional, Tuple

from ..errors import MokataError
from ..quiet_spawn import hidden

#: One side over this many bytes is not handed to the browser.
MAX_BYTES = 32 * 1024 * 1024
#: One side over this many pixels (width x height, from its header) is not handed to the browser.
MAX_VIEW_PIXELS = 64_000_000
#: The pixel difference decodes both images (and one result); the larger of each dimension must fit.
MAX_DIFF_PIXELS = 16_000_000
#: The header bytes read to learn a size (a JPEG's EXIF/ICC segments can be tens of KB before its SOF).
HEAD_BYTES = 256 * 1024
GIT_TIMEOUT = 10.0
#: How long git may stay SILENT (no byte at all) while a blob is read before the read gives up. The
#: clock bounds silence, never size or total time: it restarts at every chunk that arrives, so a
#: slow read of a large image that keeps making progress is never cut. Generous on purpose: a
#: partial clone fetching a blob over a slow network can take tens of seconds to send its first byte.
STALL_SECS = 60.0
#: One read from git's pipe takes what is there, up to this many bytes (a `read1`), so progress is
#: seen at the speed git produces it.
CHUNK = 64 * 1024
#: The MINIMUM SPEED: once `GRACE_SECS` have passed, a read whose average (bytes so far / seconds
#: since its FIRST byte) is under `MIN_BPS` is given up. This is not a timeout on the read - a large image
#: at any speed above the floor finishes, however long it takes - it ends a git that "answers" a
#: byte every few seconds, which the silence clock alone would wait on for ever. 4 KB/s is three
#: orders of magnitude under what a local blob read does and well under a slow network fetch.
MIN_BPS = 4096
GRACE_SECS = 30.0
_popen = subprocess.Popen

MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
        ".webp": "image/webp", ".svg": "image/svg+xml", ".ico": "image/x-icon",
        ".bmp": "image/bmp"}
KIND = {".jpeg": "jpeg", ".jpg": "jpeg", ".png": "png", ".gif": "gif", ".webp": "webp",
        ".svg": "svg", ".ico": "ico", ".bmp": "bmp"}


SLOW = ("git did not answer fast enough: it was sending data too slowly to finish "
        "(a partial clone may be fetching this file); nothing was shown. Try again, or open the "
        "file externally")
UNRESPONSIVE = ("git did not answer in time (a partial clone may be fetching this file); "
                "nothing was shown. Try again, or open the file externally")


class ImageUnavailable(MokataError):
    """One side cannot be shown. `state` is the side's state (`missing`, `too_large`,
    `undecodable`, `unreadable`); the message is the words the page shows."""

    def __init__(self, state: str, note: str) -> None:
        super().__init__(note)
        self.state, self.note = state, note


def _ext(name: str) -> str:
    return os.path.splitext(name or "")[1].lower()


def is_image_path(name: str) -> bool:
    return _ext(name) in MIME


def mime_of(name: str) -> str:
    return MIME[_ext(name)]


def fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    x = float(n)
    for unit in ("KB", "MB", "GB"):
        x /= 1024.0
        if x < 1024.0 or unit == "GB":
            return f"{x:.1f} {unit}"
    return f"{n} B"                                  # pragma: no cover - the loop returns


# ================================================================================== the header
def _fail(note: str) -> Dict[str, Any]:
    return {"ok": False, "kind": "", "width": None, "height": None, "note": note}


def _ok(kind: str, w: Optional[int], h: Optional[int]) -> Dict[str, Any]:
    return {"ok": True, "kind": kind, "width": w, "height": h, "note": ""}


def _png(b: bytes) -> Dict[str, Any]:
    if b[:8] != b"\x89PNG\r\n\x1a\n" or len(b) < 24 or b[12:16] != b"IHDR":
        return _fail("not a PNG: no PNG header")
    w, h = struct.unpack(">II", b[16:24])
    return _ok("png", w, h) if w and h else _fail("PNG header says 0 pixels")


class _Seq:
    """A forward-only reader over an iterator of byte chunks: `take(n)` returns n bytes (None at
    the end), `skip(n)` discards n. It never holds more than one chunk, so a walk over a file of
    any size costs constant memory."""

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._it, self._buf = iter(chunks), b""

    def _more(self) -> bool:
        for c in self._it:
            if c:
                self._buf += c
                return True
        return False

    def take(self, n: int) -> Optional[bytes]:
        while len(self._buf) < n:
            if not self._more():
                return None
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def skip(self, n: int) -> bool:
        while n > 0:
            if not self._buf and not self._more():
                return False
            k = min(n, len(self._buf))
            self._buf, n = self._buf[k:], n - k
        return True


def gif_extent(chunks: Iterable[bytes]) -> Optional[Tuple[int, int]]:
    """`(width, height)` the WHOLE GIF needs: the logical screen, grown to every frame's
    left+width / top+height. A file may declare a small screen over a far larger frame (a 10x10
    screen, a 9000x9000 frame later on), so the answer comes from the frame descriptors, however
    far into the file they sit. Block by block, sub-blocks skipped by their length prefix: constant
    memory, O(blocks) time, no byte cap and no clock. A truncated file ends the walk with what was
    seen. None when the bytes are not a GIF header."""
    seq = _Seq(chunks)
    head = seq.take(13)
    if head is None or head[:6] not in (b"GIF87a", b"GIF89a"):
        return None
    w, h = struct.unpack("<HH", head[6:10])
    if head[10] & 0x80 and not seq.skip(3 * (2 << (head[10] & 7))):
        return w, h

    def sub_blocks() -> bool:
        while True:
            n = seq.take(1)
            if n is None:
                return False
            if n[0] == 0:
                return True
            if not seq.skip(n[0]):
                return False

    while True:
        tag = seq.take(1)
        if tag is None or tag[0] not in (0x21, 0x2C):     # trailer, or anything unknown
            return w, h
        if tag[0] == 0x21:                                # extension: label, then sub-blocks
            if seq.take(1) is None or not sub_blocks():
                return w, h
            continue
        d = seq.take(9)                                   # image descriptor
        if d is None:
            return w, h
        left, top, fw, fh = struct.unpack("<HHHH", d[:8])
        w, h = max(w, left + fw), max(h, top + fh)
        if d[8] & 0x80 and not seq.skip(3 * (2 << (d[8] & 7))):
            return w, h
        if seq.take(1) is None or not sub_blocks():       # LZW minimum code size, then the data
            return w, h


def _gif(b: bytes) -> Dict[str, Any]:
    if b[:6] not in (b"GIF87a", b"GIF89a") or len(b) < 10:
        return _fail("not a GIF: no GIF header")
    w, h = struct.unpack("<HH", b[6:10])
    if not (w and h):
        return _fail("GIF header says 0 pixels")
    got = gif_extent([b]) if len(b) >= 13 else None
    return _ok("gif", *(got or (w, h)))


def _bmp(b: bytes) -> Dict[str, Any]:
    if b[:2] != b"BM" or len(b) < 26:
        return _fail("not a BMP: no BMP header")
    size = struct.unpack("<I", b[14:18])[0]
    if size == 12:
        w, h = struct.unpack("<HH", b[18:22])
    elif size >= 40:
        w, h = struct.unpack("<ii", b[18:26])
    else:
        return _fail("BMP with an unknown header")
    w, h = abs(w), abs(h)
    return _ok("bmp", w, h) if w and h else _fail("BMP header says 0 pixels")


def _ico(b: bytes) -> Dict[str, Any]:
    if len(b) < 6 or b[:4] != b"\0\0\1\0":
        return _fail("not an ICO: no ICO header")
    n = struct.unpack("<H", b[4:6])[0]
    if n < 1 or len(b) < 6 + 16 * min(n, 1):
        return _fail("ICO with no images")
    w = h = 0
    for i in range(min(n, 256)):
        e = b[6 + 16 * i:6 + 16 * i + 16]
        if len(e) < 16:
            break
        w, h = max(w, e[0] or 256), max(h, e[1] or 256)
    return _ok("ico", w, h) if w and h else _fail("ICO with no images")


def _jpeg(b: bytes) -> Dict[str, Any]:
    if b[:2] != b"\xff\xd8":
        return _fail("not a JPEG: no JPEG header")
    i, n = 2, len(b)
    while i + 4 <= n:
        if b[i] != 0xFF:
            return _fail("damaged JPEG header")
        m = b[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m in (0x01, 0xD8) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        if m in (0xD9, 0xDA):
            break
        seg = struct.unpack(">H", b[i + 2:i + 4])[0]
        if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
            if i + 9 > n:
                break
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            return _ok("jpeg", w, h) if w and h else _fail("JPEG header says 0 pixels")
        i += 2 + seg
    return _fail("JPEG with no size in its first %d KB" % (HEAD_BYTES // 1024))


def _webp(b: bytes) -> Dict[str, Any]:
    if len(b) < 25 or b[:4] != b"RIFF" or b[8:12] != b"WEBP":
        return _fail("not a WebP: no WebP header")
    t = b[12:16]
    if t in (b"VP8X", b"VP8 ") and len(b) < 30:
        return _fail("WebP header is cut short")
    if t == b"VP8X":
        w = int.from_bytes(b[24:27], "little") + 1
        h = int.from_bytes(b[27:30], "little") + 1
    elif t == b"VP8L" and b[20] == 0x2F:
        bits = struct.unpack("<I", b[21:25])[0]
        w, h = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    elif t == b"VP8 " and b[23:26] == b"\x9d\x01\x2a":
        w, h = struct.unpack("<HH", b[26:30])
        w, h = w & 0x3FFF, h & 0x3FFF
    else:
        return _fail("WebP with an unknown header")
    return _ok("webp", w, h) if w and h else _fail("WebP header says 0 pixels")


_SVG_TAG = re.compile(r"<svg(?=[\s/>])((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", re.IGNORECASE)
_NUM = r"([0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)"
_PX = re.compile(r"^\s*" + _NUM + r"\s*(?:px)?\s*$")


def _svg_attr(tag: str, name: str) -> Optional[str]:
    m = re.search(r"(?<![\w:.-])" + name + r"\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", tag)
    return None if m is None else (m.group(1) if m.group(1) is not None else m.group(2))


def _svg(b: bytes) -> Dict[str, Any]:
    text = b.decode("utf-8", "replace").lstrip("﻿")
    m = _SVG_TAG.search(text)
    if m is None:
        return _fail("not an SVG: no <svg> element")
    tag = m.group(1)
    dims = []
    for name in ("width", "height"):
        v = _svg_attr(tag, name)
        px = _PX.match(v) if v is not None else None
        dims.append(float(px.group(1)) if px else None)
    if None in dims:
        vb = _svg_attr(tag, "viewBox")
        parts = re.split(r"[\s,]+", (vb or "").strip())
        nums = []
        for p in parts:
            try:
                nums.append(float(p))
            except ValueError:
                break
        if len(nums) == 4 and nums[2] > 0 and nums[3] > 0 and dims == [None, None]:
            dims = [nums[2], nums[3]]
    if None in dims or dims[0] <= 0 or dims[1] <= 0 or dims[0] > 2 ** 31 or dims[1] > 2 ** 31:
        return _ok("svg", None, None)                # a size the browser works out - said, not guessed
    return _ok("svg", int(round(dims[0])), int(round(dims[1])))


_SNIFF = {"png": _png, "gif": _gif, "bmp": _bmp, "ico": _ico, "jpeg": _jpeg, "webp": _webp,
          "svg": _svg}


def sniff(name: str, head: bytes) -> Dict[str, Any]:
    """`{ok, kind, width, height, note}` from the header bytes of a file called `name`. SVG's size
    is None when it declares none the browser could not work out itself (a percentage, a unit)."""
    kind = KIND.get(_ext(name))
    if kind is None:
        return _fail("not a supported image type")
    return _SNIFF[kind](head)


# ====================================================================================== a side
def _rel(root: str, path: str) -> str:
    from . import snapshots as S
    from .nav import PathRefused
    rel = S.rel_path(root, path or "") if path else None
    if not rel:
        raise PathRefused(S.refusal(root, path or "",
                                    "a repo-relative path inside the repository is required"))
    if "\n" in rel or "\r" in rel:                 # `.git`/`.mokata` (any spelling): rel_path
        raise PathRefused("that path is not an image in the repository")
    if not is_image_path(rel):
        raise ValueError("only PNG, JPEG, GIF, WebP, SVG, ICO and BMP files are shown as images")
    return rel


def _rev(rev: str, what: str) -> str:
    from .review_context import check_rev
    return check_rev(rev, what) if rev else ""


def _git_size(root: str, rev: str, rel: str) -> Tuple[Optional[int], str]:
    """Bytes of the BLOB `rev:rel` - or None and why not. `--batch-check` reads the name from stdin,
    so nothing from the request is ever an option or a shell word."""
    try:
        p = subprocess.run(["git", "-C", root, "cat-file", "--batch-check"],
                           input=f"{rev}:{rel}\n".encode("utf-8"), capture_output=True,
                           timeout=GIT_TIMEOUT, **hidden())
    except FileNotFoundError:
        return None, "git is not installed"
    except subprocess.TimeoutExpired:
        return None, UNRESPONSIVE
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git could not run: {exc}"
    out = p.stdout.decode("utf-8", "replace").strip()
    parts = out.split()
    if p.returncode == 0 and len(parts) == 3 and parts[1] == "blob" and parts[2].isdigit():
        return int(parts[2]), ""
    if p.returncode == 0 and parts and parts[-1] in ("missing", "ambiguous"):
        return None, f"not in {rev}"
    if p.returncode == 0 and len(parts) == 3:
        return None, f"{rel} is a {parts[1]} in {rev}, not a file"
    return None, (p.stderr.decode("utf-8", "replace").strip()[:200] or "git could not answer")


def _git_chunks(root: str, rev: str, rel: str, limit: int,
                cancelled: Optional[Callable[[], bool]] = None) -> Iterator[bytes]:
    """At most `limit` bytes of the blob `rev:rel`, streamed: a longer blob is cut by closing the
    pipe. Every read of the pipe is bounded by `bounded_io.read_bounded` for `STALL_SECS` of SILENCE
    (restarted by each chunk that arrives); git saying nothing, or stopping part-way, kills it and
    raises `ImageUnavailable("unresponsive")`. `cancelled` (a request that went away) does the same."""
    from ..bounded_io import READ_ANSWERED, READ_CLOSED, READ_ERROR, read_bounded
    proc = _popen(["git", "-C", root, "cat-file", "blob", f"{rev}:{rel}"],
                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                  stderr=subprocess.DEVNULL, **hidden())
    stream = proc.stdout
    got = 0
    began: Optional[float] = None     # the floor counts from the FIRST byte: silence is the stall clock's
    try:
        while got < limit:
            want = min(CHUNK, limit - got)
            res = read_bounded(lambda: stream.read1(want), STALL_SECS, cancelled)
            if res.outcome == READ_ANSWERED:
                now = time.monotonic()
                if began is None:
                    began = now                      # grace and average start at the first byte
                got += len(res.text)
                yield res.text
                spent = now - began
                if got < limit and spent > GRACE_SECS and got / spent < MIN_BPS:
                    raise ImageUnavailable("unresponsive", SLOW)
            elif res.outcome == READ_CLOSED:
                break
            elif res.outcome == READ_ERROR:
                raise ImageUnavailable("unreadable", f"git could not be read: {res.error}")
            else:                                    # timeout (silence) or cancelled
                raise ImageUnavailable("unresponsive", UNRESPONSIVE)
    finally:
        if proc.poll() is None:
            proc.kill()                              # also unparks a reader still waiting on the pipe
        proc.wait()
        stream.close()


def _git_bytes(root: str, rev: str, rel: str, limit: int,
               cancelled: Optional[Callable[[], bool]] = None) -> bytes:
    """At most `limit` bytes of the blob, whole (see `_git_chunks`)."""
    return b"".join(_git_chunks(root, rev, rel, limit, cancelled))


def _file_chunks(root: str, rel: str) -> Iterator[bytes]:
    from .diffs import open_contained
    got = open_contained(root, rel)
    if not got:
        raise ImageUnavailable("unreadable", "not a readable file inside the repository")
    fh, _st = got
    with fh:
        while True:
            c = fh.read(CHUNK)
            if not c:
                return
            yield c


def _side_chunks(root: str, rel: str, rev: str, size: int,
                 cancelled: Optional[Callable[[], bool]] = None) -> Iterator[bytes]:
    """The whole of one side as chunks, never held at once: a file or a streamed git blob."""
    return _file_chunks(root, rel) if not rev else _git_chunks(root, rev, rel, size, cancelled)


def _cap(want: int, size: int) -> int:
    """How much of a `size`-byte file to read when `want` bytes are wanted: a file over `MAX_BYTES`
    is refused whatever its bytes say, so only its header is read for the label."""
    return min(want, HEAD_BYTES) if size > MAX_BYTES else want


def _load(root: str, rel: str, rev: str, want: int,
          cancelled: Optional[Callable[[], bool]] = None) -> Tuple[str, int, bytes, str]:
    """(state, size, first `want` bytes, note) of `rel` on one side. `state` is `missing`,
    `unreadable` or `present` (judged next by `_judge`)."""
    if not rev:
        from .diffs import open_contained
        got = open_contained(root, rel)
        if got == "":
            return "missing", 0, b"", "not in the working tree"
        if not got:
            return "unreadable", 0, b"", "not a readable file inside the repository"
        fh, st = got
        try:
            with fh:
                # a file already known to be over the limit is judged on its header: never read
                # up to the limit just to be refused
                return "present", st.st_size, fh.read(_cap(want, st.st_size)), ""
        except OSError as exc:
            return "unreadable", 0, b"", f"could not read it: {exc}"
    size, why = _git_size(root, rev, rel)
    if size is None:
        state = "missing" if why.startswith("not in ") else "unresponsive" if why == UNRESPONSIVE \
            else "unreadable"
        return state, 0, b"", why
    need = min(_cap(want, size), size)
    try:
        data = _git_bytes(root, rev, rel, need, cancelled) if size else b""
    except ImageUnavailable as exc:
        return exc.state, 0, b"", exc.note
    except OSError as exc:
        return "unreadable", 0, b"", f"git could not run: {exc}"
    if len(data) < need:
        return "unreadable", 0, b"", f"git ended after {len(data)} of {need} bytes"
    return "present", size, data, ""


def _too_large_note(size: int, w: Optional[int], h: Optional[int]) -> str:
    dims = f", {w}×{h}" if w and h else ""
    return f"too large to diff — {fmt_bytes(size)}{dims}, open externally"


def _judge(rel: str, size: int, head: bytes, rev_label: str,
           chunks: Optional[Callable[[], Iterable[bytes]]] = None) -> Dict[str, Any]:
    """`chunks()` gives the WHOLE file again as a stream. Read for a GIF only (a frame descriptor
    anywhere in the file can need far more than its header says); one walker, `gif_extent`, serves
    the info, raw and difference paths alike. A file over `MAX_BYTES` is refused on size first."""
    s = sniff(rel, head[:HEAD_BYTES])
    if chunks is not None and s["ok"] and s["kind"] == "gif" and size <= MAX_BYTES:
        got = gif_extent(chunks())
        if got:
            s = dict(s, width=got[0], height=got[1])
    base: Dict[str, Any] = {"state": "ok", "size": size, "mime": mime_of(rel), "kind": s["kind"],
                            "width": s["width"], "height": s["height"], "note": "",
                            "rev": rev_label}
    w, h = s["width"], s["height"]
    if size > MAX_BYTES or (s["ok"] and w and h and w * h > MAX_VIEW_PIXELS):
        base.update(state="too_large", note=_too_large_note(size, w, h))
    elif not s["ok"]:
        base.update(state="undecodable", kind="", note=s["note"] + f" ({rel})")
    return base


def _side(root: str, rel: str, rev: str,
          cancelled: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    state, size, head, note = _load(root, rel, rev, HEAD_BYTES, cancelled)
    label = rev or "working tree"
    if state != "present":
        return {"state": state, "size": 0, "mime": mime_of(rel), "kind": "", "width": None,
                "height": None, "note": note, "rev": label}
    try:
        return _judge(rel, size, head, label,
                      lambda: _side_chunks(root, rel, rev, size, cancelled))
    except ImageUnavailable as exc:
        return {"state": exc.state, "size": 0, "mime": mime_of(rel), "kind": "", "width": None,
                "height": None, "note": exc.note, "rev": label}
    except OSError as exc:
        return {"state": "unreadable", "size": 0, "mime": mime_of(rel), "kind": "", "width": None,
                "height": None, "note": f"could not read it: {exc}", "rev": label}


def pair(root: str, path: str, old_path: str, base: str, head: str,
         cancelled: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """Both sides of one changed image: the file at `base` (default HEAD) as `old_path`, and at
    `head` (empty = the working tree) as `path`. Raises `PathRefused` (a path outside the repo) or
    `ValueError` (not an image name, an option-shaped revision)."""
    rel = _rel(root, path)
    old_rel = _rel(root, old_path) if old_path else rel
    base_rev, head_rev = _rev(base, "base") or "HEAD", _rev(head, "head")
    old, new = _side(root, old_rel, base_rev, cancelled), _side(root, rel, head_rev, cancelled)
    if old["state"] == "missing" and new["state"] != "missing":
        status = "added"
    elif new["state"] == "missing" and old["state"] != "missing":
        status = "deleted"
    elif old["state"] == "missing":
        status = "absent"
    else:
        status = "modified"
    return {"state": "ok", "sample": False, "path": rel, "old_path": old_rel, "base": base_rev,
            "head": head_rev, "status": status, "old": old, "new": new,
            "diff": _diffable(old, new), "limits": limits()}


def limits() -> Dict[str, int]:
    return {"bytes": MAX_BYTES, "view_pixels": MAX_VIEW_PIXELS, "diff_pixels": MAX_DIFF_PIXELS}


def _diffable(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    if old["state"] != "ok" or new["state"] != "ok":
        return {"ok": False, "note": "pixel difference needs both versions shown as images"}
    if old["width"] and new["width"]:
        px = max(old["width"], new["width"]) * max(old["height"], new["height"])
        if px > MAX_DIFF_PIXELS:
            return {"ok": False,
                    "note": f"pixel difference decodes both images: {px / 1e6:.1f} MP is over the "
                            f"{MAX_DIFF_PIXELS // 1_000_000} MP budget. Side by side, swipe and "
                            "onion skin still work"}
    return {"ok": True, "note": ""}


def read_image(root: str, path: str, rev: str,
               cancelled: Optional[Callable[[], bool]] = None) -> Tuple[str, bytes]:
    """`(mime, bytes)` of ONE side - the working tree (`rev` empty) or the file at `rev` - for the
    image route. Raises `ImageUnavailable(state, words)` when it cannot be shown, `PathRefused` /
    `ValueError` for a bad request. The bytes are bounded by `MAX_BYTES` and judged again as read."""
    rel = _rel(root, path)
    rv = _rev(rev, "rev")
    state, size, data, note = _load(root, rel, rv, MAX_BYTES + 1, cancelled)
    if state != "present":
        raise ImageUnavailable(state, note)
    side = _judge(rel, size, data, rv or "working tree", lambda: [data])
    if side["state"] != "ok" or len(data) > MAX_BYTES:
        if len(data) > MAX_BYTES:
            side.update(state="too_large", note=_too_large_note(max(size, len(data)), side["width"],
                                                                side["height"]))
        raise ImageUnavailable(side["state"], side["note"])
    return mime_of(rel), data


__all__ = ["HEAD_BYTES", "ImageUnavailable", "MAX_BYTES", "MAX_DIFF_PIXELS", "MAX_VIEW_PIXELS",
           "fmt_bytes", "is_image_path", "limits", "mime_of", "pair", "read_image", "sniff"]
