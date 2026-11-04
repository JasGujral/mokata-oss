"""0.0.27 P9 - ID-IMAGE-DIFF: a changed image is SHOWN, before against after, inside the diff view.

Business rules (each test says which):
  * a changed PNG/JPEG/GIF/WebP/SVG/ICO/BMP reports BOTH sides' dimensions and byte sizes, read from the
    file's header - never by decoding it on the server;
  * an added image has one side, a deleted image has one side, and the answer says which;
  * a huge image (by bytes OR by pixels - a 20000x20000 PNG is a few KB on disk) is a labelled
    "too large to diff - N MB, open externally" state, never a silent cap, and costs a header read;
  * an image is read ONLY inside the repo: `..`, a symlink out, a path through a symlinked directory,
    a bad revision are refused; the base side comes from a git revision;
  * SVG is untrusted repo content: it is served as an image (image/svg+xml, nosniff, a CSP that runs
    nothing) and the page code never puts it in the DOM as markup;
  * a file named .png that is not a PNG is not served as one; a non-image binary keeps "binary".
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
import zlib
from unittest import mock

import _support  # noqa: F401

from mokata.ide import imagediff as IM

TOKEN, PORT = "tok", 9999
BASE = f"http://127.0.0.1:{PORT}"
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "src", "mokata", "ide", "static")


# ---------------------------------------------------------------------------------- image bytes
def png(w, h, rgb=(10, 200, 90), fill=True):
    """A real PNG (solid colour), or with fill=False only the header + IEND - a valid-looking
    20000x20000 PNG that is a few dozen bytes on disk."""
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b""
    if fill:
        row = b"\0" + bytes(rgb) * w
        raw = zlib.compress(row * h)
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    if fill:
        out += chunk(b"IDAT", raw)
    return out + chunk(b"IEND", b"")


def gif(w, h):
    return b"GIF89a" + struct.pack("<HH", w, h) + b"\x00\x00\x00;"


def gif_frames(screen, *frames, global_table=False, pad=0):
    """A GIF whose logical screen is `screen` (w, h) with one image descriptor per `(left, top, w, h)`
    frame, each carrying a one-colour local table and a tiny data block. `global_table` adds a
    4-colour global table (the common real-world shape); `pad` puts that many bytes of data
    sub-blocks into the FIRST frame, so a later descriptor sits far into the file."""
    out = b"GIF89a" + struct.pack("<HH", *screen)
    out += (b"\x81\x00\x00" + bytes(12)) if global_table else b"\x00\x00\x00"
    out += b"\x21\xf9\x04\x00\x00\x00\x00\x00"              # a graphic-control extension
    for left, top, w, h in frames:
        out += b"\x2c" + struct.pack("<HHHH", left, top, w, h) + b"\x80"   # local table, 2 colours
        out += b"\x00\x00\x00\xff\xff\xff" + b"\x02" + b"\x02\x44\x01"
        if pad:
            out += (b"\xff" + bytes(255)) * (pad // 256)
            pad = 0
        out += b"\x00"
    return out + b";"


def bmp(w, h):
    return b"BM" + b"\0" * 12 + struct.pack("<IiiHH", 40, w, -h, 1, 24) + b"\0" * 24


def ico(w, h):
    return struct.pack("<HHH", 0, 1, 2) + bytes([16, 16, 0, 0, 1, 0, 32, 0]) + b"\0" * 8 \
        + bytes([w % 256, h % 256, 0, 0, 1, 0, 32, 0]) + b"\0" * 8


def jpeg(w, h, app_pad=0):
    app = b"\xff\xe1" + struct.pack(">H", app_pad + 2) + b"E" * app_pad if app_pad else b""
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app + sof + b"\xff\xd9"


def webp_x(w, h):
    body = b"VP8X" + struct.pack("<I", 10) + b"\0\0\0\0" + (w - 1).to_bytes(3, "little") \
        + (h - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def webp_l(w, h):
    bits = (w - 1) | ((h - 1) << 14)
    body = b"VP8L" + struct.pack("<I", 5) + b"\x2f" + struct.pack("<I", bits)
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def webp_lossy(w, h):
    body = b"VP8 " + struct.pack("<I", 10) + b"\0\0\0" + b"\x9d\x01\x2a" + struct.pack("<HH", w, h)
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


EVIL_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            'width="40" height="30" onload="window.__pwned=1">'
            '<script>window.__pwned=2</script>'
            '<image href="http://127.0.0.1:1/x.png" width="5" height="5"/>'
            '<foreignObject width="40" height="30"><body xmlns="http://www.w3.org/1999/xhtml" '
            'onload="window.__pwned=3"><iframe src="http://127.0.0.1:1/"></iframe></body>'
            '</foreignObject></svg>')


def git(d, *a):
    subprocess.run(["git", "-C", d, *a], check=True, capture_output=True, stdin=subprocess.DEVNULL)


def write(d, rel, data):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    return p


class Repo(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.d = os.path.realpath(self._t.name)
        git(self.d, "init", "-q", "-b", "main")
        git(self.d, "config", "user.email", "t@t")
        git(self.d, "config", "user.name", "t")
        git(self.d, "config", "commit.gpgsign", "false")

    def commit(self, msg="c"):
        git(self.d, "add", "-A")
        git(self.d, "commit", "-qm", msg)


# ======================================================================================= sniffing
class TheHeaderSaysTheSize(unittest.TestCase):
    """Dimensions come from the header bytes. A 20000x20000 PNG is known to be that big without
    decoding a pixel of it."""

    def dims(self, name, data):
        s = IM.sniff(name, data)
        self.assertTrue(s["ok"], s)
        return s["width"], s["height"]

    def test_every_supported_format_reports_its_dimensions(self):
        self.assertEqual((31, 17), self.dims("a.png", png(31, 17)))
        self.assertEqual((20000, 20000), self.dims("a.png", png(20000, 20000, fill=False)))
        self.assertEqual((7, 9), self.dims("a.gif", gif(7, 9)))
        self.assertEqual((12, 5), self.dims("a.bmp", bmp(12, 5)))
        self.assertEqual((64, 48), self.dims("a.ico", ico(64, 48)))
        self.assertEqual((256, 256), self.dims("a.ico", ico(0, 0)), "0 in an ICO entry means 256")
        self.assertEqual((640, 480), self.dims("a.jpg", jpeg(640, 480)))
        self.assertEqual((800, 600), self.dims("a.jpeg", jpeg(800, 600)))
        self.assertEqual((321, 123), self.dims("a.webp", webp_x(321, 123)))
        self.assertEqual((55, 44), self.dims("a.webp", webp_l(55, 44)))
        self.assertEqual((66, 33), self.dims("a.webp", webp_lossy(66, 33)))

    def test_a_gif_frame_larger_than_its_declared_screen_is_measured_by_the_frame(self):
        """A 1x1 logical screen over a 30000x30000 frame is a 900 MP image: the size the browser
        must hold is the frame's, so the header check (and the caption) say so."""
        self.assertEqual((30000, 30000), self.dims("a.gif", gif_frames((1, 1), (0, 0, 30000, 30000))))
        self.assertEqual((110, 55), self.dims("a.gif", gif_frames((10, 10), (0, 0, 10, 10), (100, 50, 10, 5))))
        self.assertEqual((10, 10), self.dims("a.gif", gif_frames((10, 10), (0, 0, 4, 4))))

    def test_a_gif_with_a_global_colour_table_is_walked_past_it(self):
        """The table sits between the header and the first block; skipping it wrongly reads
        colour bytes as blocks and misses the frame."""
        self.assertEqual((500, 400), self.dims("a.gif", gif_frames((1, 1), (0, 0, 500, 400), global_table=True)))
        self.assertEqual((3, 3), self.dims("a.gif", gif_frames((3, 3), (0, 0, 2, 2), global_table=True)))

    def test_a_frame_far_into_the_file_is_found_by_the_walker(self):
        big = gif_frames((10, 10), (0, 0, 10, 10), (0, 0, 9000, 9000), pad=300 * 1024)
        self.assertGreater(len(big), IM.HEAD_BYTES)
        self.assertEqual((9000, 9000), tuple(IM.gif_extent([big])))
        chunks = [big[i:i + 777] for i in range(0, len(big), 777)]   # any chunking, same answer
        self.assertEqual((9000, 9000), tuple(IM.gif_extent(chunks)))

    def test_a_gif_cut_off_mid_frame_keeps_the_size_it_had_seen(self):
        data = gif_frames((5, 5), (0, 0, 8, 9))
        for cut in range(14, len(data)):
            with self.subTest(cut=cut):
                s = IM.sniff("a.gif", data[:cut])
                self.assertTrue(s["ok"])
                self.assertGreaterEqual(s["width"], 5)

    def test_a_jpeg_with_a_big_metadata_segment_is_still_measured(self):
        self.assertEqual((640, 480), self.dims("p.jpg", jpeg(640, 480, app_pad=60000)))

    def test_svg_dimensions_come_from_width_height_or_viewbox_and_stay_unknown_otherwise(self):
        self.assertEqual((40, 30), self.dims(
            "a.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="40px" height="30"/>'.encode()))
        self.assertEqual((100, 50), self.dims(
            "a.svg", b'<?xml version="1.0"?><!-- c --><svg viewBox="0 0 100 50"></svg>'))
        s = IM.sniff("a.svg", b'<svg width="100%" height="50%"></svg>')
        self.assertTrue(s["ok"])
        self.assertIsNone(s["width"], "a percentage is not a size - said as unknown, not guessed")
        s = IM.sniff("a.svg", b'<svg stroke-width="999999" data-height="7" height="5"/>')
        self.assertIsNone(s["width"], "stroke-width is not the width")

    def test_a_file_that_is_not_the_image_its_name_claims_is_not_ok(self):
        for name, data in (("a.png", b"<html><script>1</script></html>"), ("a.png", gif(1, 1)),
                           ("a.gif", png(1, 1)), ("a.jpg", b"\xff\xd8\xff\xd9"),
                           ("a.svg", b"<html>not svg</html>"), ("a.png", b"\x89PNG\r\n\x1a\n"),
                           ("a.ico", b"\0\0\1\0\0\0"), ("a.bmp", b"BM")):
            with self.subTest(name=name, data=data[:12]):
                s = IM.sniff(name, data)
                self.assertFalse(s["ok"])
                self.assertTrue(s["note"])

    def test_which_names_are_images(self):
        for n in ("a.PNG", "d/b.JpEg", "c.jpg", "d.gif", "e.webp", "f.svg", "g.ico", "h.bmp"):
            self.assertTrue(IM.is_image_path(n), n)
        for n in ("a.pdf", "a.png.txt", "a", "png", "a.tiff", "a.avif"):
            self.assertFalse(IM.is_image_path(n), n)


# ======================================================================================== the pair
class ABeforeAndAnAfter(Repo):
    def test_a_modified_image_reports_both_sides_sizes_and_dimensions(self):
        """The reader sees what changed in size: bytes and pixels of BOTH sides."""
        old, new = png(30, 20), png(60, 40, (200, 10, 10))
        write(self.d, "img/logo.png", old)
        self.commit()
        write(self.d, "img/logo.png", new)
        got = IM.pair(self.d, "img/logo.png", "img/logo.png", "HEAD", "")
        self.assertEqual("modified", got["status"])
        self.assertEqual(("ok", len(old), 30, 20), tuple(got["old"][k] for k in
                                                         ("state", "size", "width", "height")))
        self.assertEqual(("ok", len(new), 60, 40), tuple(got["new"][k] for k in
                                                         ("state", "size", "width", "height")))
        self.assertEqual("image/png", got["new"]["mime"])
        self.assertTrue(got["diff"]["ok"])

    def test_an_added_image_has_only_a_new_side_and_a_deleted_one_only_an_old_side(self):
        write(self.d, "gone.gif", gif(3, 3))
        self.commit()
        os.remove(os.path.join(self.d, "gone.gif"))
        write(self.d, "fresh.png", png(4, 4))                      # untracked: not in git yet
        added = IM.pair(self.d, "fresh.png", "fresh.png", "HEAD", "")
        self.assertEqual(("added", "missing", "ok"), (added["status"], added["old"]["state"],
                                                      added["new"]["state"]))
        self.assertFalse(added["diff"]["ok"], "no difference to draw from one side")
        gone = IM.pair(self.d, "gone.gif", "gone.gif", "HEAD", "")
        self.assertEqual(("deleted", "ok", "missing"), (gone["status"], gone["old"]["state"],
                                                        gone["new"]["state"]))

    def test_two_revisions_compare_without_touching_the_working_tree(self):
        write(self.d, "a.png", png(10, 10))
        self.commit("one")
        git(self.d, "tag", "v1")
        write(self.d, "a.png", png(20, 10))
        self.commit("two")
        write(self.d, "a.png", png(99, 99))                        # the work tree is a third state
        got = IM.pair(self.d, "a.png", "a.png", "v1", "HEAD")
        self.assertEqual((10, 20), (got["old"]["width"], got["new"]["width"]))

    def test_a_renamed_image_reads_the_old_side_by_its_old_name(self):
        write(self.d, "old.png", png(5, 5))
        self.commit()
        git(self.d, "mv", "old.png", "new.png")
        got = IM.pair(self.d, "new.png", "old.png", "HEAD", "")
        self.assertEqual(("ok", "ok"), (got["old"]["state"], got["new"]["state"]))

    def test_the_pixel_difference_is_offered_only_when_it_fits_and_says_why_not(self):
        """Pixel difference needs both images decoded: a pair over the budget keeps the other
        modes and is told why - the limit is stated, not silent."""
        write(self.d, "big.png", png(5000, 5000, fill=False))
        self.commit()
        write(self.d, "big.png", png(5001, 5000, fill=False))
        got = IM.pair(self.d, "big.png", "big.png", "HEAD", "")
        self.assertEqual("ok", got["old"]["state"])
        self.assertFalse(got["diff"]["ok"])
        self.assertIn("pixel", got["diff"]["note"])
        self.assertIn("MP", got["diff"]["note"])
        self.assertEqual(IM.MAX_DIFF_PIXELS, got["limits"]["diff_pixels"])

    def test_a_file_that_grew_after_it_was_measured_is_judged_again_as_read(self):
        """The header said 10 bytes; the bytes that arrive are over the limit: still refused."""
        big = png(2, 2) + b"\0" * (IM.MAX_BYTES + 1)
        with mock.patch.object(IM, "_load", return_value=("present", 10, big, "")):
            with self.assertRaises(IM.ImageUnavailable) as cm:
                IM.read_image(self.d, "a.png", "")
        self.assertEqual("too_large", cm.exception.state)

    def test_a_directory_called_x_png_is_not_an_image_on_either_side(self):
        """A folder named like an image is not a file: neither the revision's tree nor the working
        tree's directory is handed out as one."""
        write(self.d, "d.png/inner.txt", "x")
        self.commit()
        got = IM.pair(self.d, "d.png", "d.png", "HEAD", "")
        self.assertEqual("unreadable", got["old"]["state"])
        self.assertEqual("unreadable", got["new"]["state"])
        self.assertIn("not a file", got["old"]["note"])
        self.assertRaises(IM.ImageUnavailable, IM.read_image, self.d, "d.png", "HEAD")
        self.assertRaises(IM.ImageUnavailable, IM.read_image, self.d, "d.png", "")

    def test_a_non_image_name_is_refused_and_a_non_image_content_is_not_ok(self):
        write(self.d, "a.pdf", b"%PDF-1.4")
        with self.assertRaises(ValueError):
            IM.pair(self.d, "a.pdf", "a.pdf", "HEAD", "")
        write(self.d, "fake.png", b"<html>hi</html>")
        got = IM.pair(self.d, "fake.png", "fake.png", "HEAD", "")
        self.assertEqual("undecodable", got["new"]["state"])


# ======================================================================================= too large
class TooLargeToDiffIsSaidNotCapped(Repo):
    def test_a_200_mb_file_is_a_labelled_state_and_costs_a_header_read(self):
        p = write(self.d, "huge.png", png(10, 10))
        with open(p, "r+b") as fh:
            fh.truncate(200 * 1024 * 1024)                          # sparse: 200 MB, no disk used
        t0 = time.time()
        got = IM.pair(self.d, "huge.png", "huge.png", "HEAD", "")
        self.assertLess(time.time() - t0, 3.0)
        side = got["new"]
        self.assertEqual("too_large", side["state"])
        self.assertEqual(200 * 1024 * 1024, side["size"])
        self.assertIn("too large to diff", side["note"])
        self.assertIn("200.0 MB", side["note"])
        self.assertIn("open externally", side["note"])
        self.assertTrue(side["note"].startswith("too large to diff"))

    def test_a_20000_by_20000_png_of_a_few_bytes_is_too_large_by_pixels(self):
        write(self.d, "wall.png", png(20000, 20000, fill=False))
        side = IM.pair(self.d, "wall.png", "wall.png", "HEAD", "")["new"]
        self.assertEqual("too_large", side["state"])
        self.assertEqual((20000, 20000), (side["width"], side["height"]))
        self.assertIn("20000", side["note"])
        self.assertIn("open externally", side["note"])

    def test_the_byte_limit_holds_for_a_git_revision_side_too(self):
        write(self.d, "a.png", png(300, 300))
        self.commit()
        with mock.patch.object(IM, "MAX_BYTES", 500):
            got = IM.pair(self.d, "a.png", "a.png", "HEAD", "")
        self.assertEqual("too_large", got["old"]["state"])
        self.assertEqual("too_large", got["new"]["state"])
        self.assertFalse(got["diff"]["ok"])

    def test_a_file_over_the_limit_is_refused_on_its_header_not_read_up_to_the_limit(self):
        """Refusing a 200 MB file must not cost a 32 MB read first - on either kind of side."""
        write(self.d, "a.png", png(300, 300))
        self.commit()
        seen = []
        real = IM._git_bytes
        with mock.patch.object(IM, "MAX_BYTES", 500), mock.patch.object(IM, "HEAD_BYTES", 100), \
                mock.patch.object(IM, "_git_bytes", lambda r, v, p, lim, c=None: (seen.append(lim),
                                                                                 real(r, v, p, lim, c))[1]):
            self.assertRaises(IM.ImageUnavailable, IM.read_image, self.d, "a.png", "HEAD")
            from mokata.ide import diffs
            reads = []
            real_open = diffs.open_contained

            def spy(root, rel):
                got = real_open(root, rel)
                if got:
                    fh, st = got
                    orig = fh.read
                    fh.read = lambda n=-1: (reads.append(n), orig(n))[1]
                    return fh, st
                return got
            with mock.patch.object(diffs, "open_contained", spy):
                self.assertRaises(IM.ImageUnavailable, IM.read_image, self.d, "a.png", "")
        self.assertEqual([100], seen)
        self.assertEqual([100], reads)

    def test_one_huge_side_does_not_hide_the_other(self):
        write(self.d, "a.png", png(8, 8))
        self.commit()
        write(self.d, "a.png", png(20000, 20000, fill=False))
        got = IM.pair(self.d, "a.png", "a.png", "HEAD", "")
        self.assertEqual(("ok", "too_large"), (got["old"]["state"], got["new"]["state"]))
        self.assertFalse(got["diff"]["ok"])
        self.assertTrue(got["diff"]["note"])


# ======================================================================================= containment
class OnlyInsideTheRepo(Repo):
    def setUp(self):
        super().setUp()
        self._o = tempfile.TemporaryDirectory()
        self.addCleanup(self._o.cleanup)
        self.out = os.path.realpath(self._o.name)
        write(self.out, "secret.png", png(2, 2))

    def test_a_symlink_to_a_file_outside_is_not_read(self):
        try:
            os.symlink(os.path.join(self.out, "secret.png"), os.path.join(self.d, "leak.png"))
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks on this platform")
        with self.assertRaises(Exception) as cm:
            IM.pair(self.d, "leak.png", "leak.png", "HEAD", "")
        self.assertEqual("PathRefused", type(cm.exception).__name__)
        self.assertEqual("PathRefused", type(self._raises(IM.read_image, self.d, "leak.png", "")
                                             ).__name__)

    def test_a_symlinked_directory_out_of_the_repo_is_not_walked(self):
        try:
            os.symlink(self.out, os.path.join(self.d, "linkdir"))
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks on this platform")
        self.assertEqual("PathRefused", type(self._raises(IM.pair, self.d, "linkdir/secret.png",
                                                          "linkdir/secret.png", "HEAD", "")
                                             ).__name__)

    def test_dot_dot_and_absolute_paths_outside_are_refused(self):
        for bad in ("../x.png", "a/../../x.png", os.path.join(self.out, "secret.png")):
            with self.subTest(bad=bad):
                self.assertEqual("PathRefused", type(self._raises(
                    IM.pair, self.d, bad, bad, "HEAD", "")).__name__)

    def test_aliases_of_one_file_are_the_same_file(self):
        write(self.d, "a/b.png", png(3, 3))
        self.commit()
        for alias in ("a/./b.png", "a//b.png", "a/../a/b.png"):
            with self.subTest(alias=alias):
                got = IM.pair(self.d, alias, alias, "HEAD", "")
                self.assertEqual("a/b.png", got["path"])
                self.assertEqual("ok", got["old"]["state"], "the base side finds it by its repo name")

    def test_an_option_shaped_revision_is_refused(self):
        write(self.d, "a.png", png(3, 3))
        self.commit()
        for bad in ("--output=x", "-x", "a..b", ""):
            if not bad:
                continue
            with self.subTest(bad=bad):
                self.assertRaises(ValueError, IM.pair, self.d, "a.png", "a.png", bad, "")

    def test_the_mokata_record_and_git_dir_are_not_images_to_read(self):
        write(self.d, ".mokata/x.png", png(2, 2))
        write(self.d, ".git/x.png", png(2, 2))
        for p in (".mokata/x.png", ".git/x.png"):
            with self.subTest(p=p):
                self.assertIn(type(self._raises(IM.pair, self.d, p, p, "HEAD", "")).__name__,
                              ("PathRefused", "ValueError", "BadRequest"))

    def test_dot_git_in_any_case_is_refused_by_its_NAME_alone(self):
        """On APFS the on-disk identity check would also catch `.GIT`; on a case-sensitive disk only
        the name rule does. Pin the name rule with the identity half switched off."""
        for p in (".GIT/x.png", "a/.Git/x.png", ".MOKATA/x.png"):
            with self.subTest(p=p), mock.patch("mokata.repo_paths.aliases_private", return_value=False):
                with self.assertRaises(Exception) as cm:
                    IM.pair(self.d, p, p, "HEAD", "")
                self.assertEqual("PathRefused", type(cm.exception).__name__)

    def _raises(self, fn, *a):
        try:
            fn(*a)
        except Exception as exc:  # noqa: BLE001 - the test inspects the type
            return exc
        self.fail("expected a refusal")


# ======================================================================================== the routes
class TheRoutes(Repo):
    def setUp(self):
        super().setUp()
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        write(self.d, "img/a.png", png(30, 20))
        write(self.d, "img/evil.svg", EVIL_SVG)
        write(self.d, "img/fake.png", b"<html><script>alert(1)</script></html>")
        self.commit()
        write(self.d, "img/a.png", png(60, 40, (1, 2, 3)))
        state = AppState(self.d, TOKEN, PORT, None, False)
        self.c = TestClient(build_app(state), base_url=BASE, follow_redirects=False)
        self.c.get(f"/pair?t={TOKEN}")

    def test_the_raw_route_serves_the_image_bytes_as_an_image_that_runs_nothing(self):
        r = self.c.get("/api/image/raw", params={"path": "img/a.png"})
        self.assertEqual(200, r.status_code)
        self.assertEqual(png(60, 40, (1, 2, 3)), r.content)
        self.assertTrue(r.headers["content-type"].startswith("image/png"))
        self.assertEqual("nosniff", r.headers["x-content-type-options"])
        self.assertIn("sandbox", r.headers["content-security-policy"])
        self.assertIn("default-src 'none'", r.headers["content-security-policy"])

    def test_the_base_side_is_the_file_at_the_revision_not_the_working_tree(self):
        r = self.c.get("/api/image/raw", params={"path": "img/a.png", "rev": "HEAD"})
        self.assertEqual(png(30, 20), r.content)

    def test_an_svg_with_script_onload_and_external_href_is_served_as_an_inert_image(self):
        """The SVG bytes are NOT sanitised or rewritten (we show what the repo holds); what makes
        it safe is how it is served: as image/svg+xml, nosniff, with a CSP that allows nothing and
        a sandbox that blocks scripts if someone opens the URL directly."""
        r = self.c.get("/api/image/raw", params={"path": "img/evil.svg"})
        self.assertEqual(200, r.status_code)
        self.assertEqual(EVIL_SVG.encode(), r.content)
        self.assertTrue(r.headers["content-type"].startswith("image/svg+xml"))
        self.assertEqual("nosniff", r.headers["x-content-type-options"])
        csp = r.headers["content-security-policy"]
        self.assertIn("default-src 'none'", csp)
        self.assertIn("sandbox", csp)
        self.assertNotIn("allow-scripts", csp)
        self.assertNotIn("script-src", csp.replace("default-src 'none'", ""))
        self.assertEqual("same-origin", r.headers["cross-origin-resource-policy"])

    def test_a_gif_frame_over_the_pixel_limit_is_too_large_whatever_the_screen_says(self):
        write(self.d, "bomb.gif", gif_frames((1, 1), (0, 0, 30000, 30000)))
        j = self.c.get("/api/image/info", params={"path": "bomb.gif"}).json()
        self.assertEqual("too_large", j["new"]["state"])
        self.assertIn("30000", j["new"]["note"])
        self.assertEqual(413, self.c.get("/api/image/raw", params={"path": "bomb.gif"}).status_code)

    def test_an_oversize_frame_after_the_header_window_is_refused_on_info_raw_and_diff(self):
        """A 10x10 screen, 300 KB of padding frame, THEN a 9000x9000 frame: 81 MP is over the
        view and the difference budgets, and every path judges it by the same walker."""
        bomb = gif_frames((10, 10), (0, 0, 10, 10), (0, 0, 9000, 9000), pad=300 * 1024)
        write(self.d, "late.gif", bomb)
        plain = gif_frames((10, 10), (0, 0, 10, 10))
        git(self.d, "add", "-A")
        write(self.d, "ok.gif", plain)
        git(self.d, "add", "-A")
        git(self.d, "commit", "-qm", "gifs")
        j = self.c.get("/api/image/info", params={"path": "late.gif", "base": "HEAD"}).json()
        self.assertEqual("too_large", j["old"]["state"], "committed side (git stream)")
        self.assertEqual("too_large", j["new"]["state"], "working-tree side (file stream)")
        self.assertIn("9000", j["new"]["note"])
        self.assertFalse(j["diff"]["ok"])
        for rev in ("", "HEAD"):
            r = self.c.get("/api/image/raw", params={"path": "late.gif", "rev": rev} if rev
                           else {"path": "late.gif"})
            self.assertEqual(413, r.status_code, rev)
        # the pixel difference against a small image is refused too, not offered
        write(self.d, "mix.gif", plain)
        d = IM.pair(self.d, "late.gif", "ok.gif", "HEAD", "")
        self.assertFalse(d["diff"]["ok"], d["diff"])
        self.assertEqual("too_large", d["new"]["state"])

    def test_a_server_error_500_also_carries_nosniff(self):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        c = TestClient(build_app(AppState(self.d, TOKEN, PORT, None, False)), base_url=BASE,
                       raise_server_exceptions=False)
        c.get(f"/pair?t={TOKEN}")
        with mock.patch.object(IM, "read_image", side_effect=RuntimeError("boom")):
            r = c.get("/api/image/raw", params={"path": "img/a.png"})
        self.assertEqual(500, r.status_code)
        self.assertEqual("nosniff", r.headers.get("x-content-type-options"))

    def test_every_error_response_carries_nosniff_not_only_the_image_ones(self):
        """Any error JSON from any route (a refusal, a 404, a 405, an unpaired 401, a bad Host)
        may not be sniffed into something else."""
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        paired = [
            ("get", "/api/image/raw", {"params": {"path": "../x.png"}}),
            ("get", "/api/image/info", {"params": {"path": "img/a.txt"}}),
            ("get", "/api/file", {"params": {"path": "../../etc/passwd"}}),
            ("get", "/api/no-such-route", {}),
            ("post", "/api/image/raw", {}),
            ("get", "/api/highlight", {}),
        ]
        for verb, url, kw in paired:
            with self.subTest(url=url, verb=verb):
                r = getattr(self.c, verb)(url, **kw)
                self.assertGreaterEqual(r.status_code, 400, url)
                self.assertEqual("nosniff", r.headers.get("x-content-type-options"), url)
        fresh = TestClient(build_app(AppState(self.d, TOKEN, PORT, None, False)), base_url=BASE)
        self.assertEqual(401, fresh.get("/api/file").status_code)
        self.assertEqual("nosniff", fresh.get("/api/file").headers.get("x-content-type-options"))
        bad = TestClient(build_app(AppState(self.d, TOKEN, PORT, None, False)),
                         base_url="http://evil.example:80")
        r = bad.get("/api/file")
        self.assertEqual(403, r.status_code)
        self.assertEqual("nosniff", r.headers.get("x-content-type-options"))

    def test_the_content_type_comes_from_the_name_never_from_the_bytes(self):
        r = self.c.get("/api/image/raw", params={"path": "img/fake.png"})
        self.assertEqual(415, r.status_code)
        self.assertTrue(r.headers["content-type"].startswith("application/json"))
        self.assertNotIn(b"alert", r.content)

    def test_refusals_are_json_with_the_right_status(self):
        g = lambda **p: self.c.get("/api/image/raw", params=p)  # noqa: E731
        self.assertEqual(400, g(path="../x.png").status_code)
        self.assertEqual(400, g().status_code)
        self.assertEqual(400, g(path="img/a.png", rev="--output=x").status_code)
        self.assertEqual(400, g(path="img/a.pdf").status_code)
        self.assertEqual(404, g(path="img/none.png").status_code)
        self.assertEqual(404, g(path="img/none.png", rev="HEAD").status_code)
        self.assertEqual(404, g(path="img/a.png", rev="no-such-branch").status_code)

    def test_a_too_large_image_is_413_with_the_label_and_is_never_streamed(self):
        write(self.d, "wall.png", png(20000, 20000, fill=False))
        r = self.c.get("/api/image/raw", params={"path": "wall.png"})
        self.assertEqual(413, r.status_code)
        self.assertIn("too large to diff", r.json()["note"])

    def test_a_symlink_out_of_the_repo_is_refused_by_the_route(self):
        with tempfile.TemporaryDirectory() as out:
            write(out, "s.png", png(2, 2))
            try:
                os.symlink(os.path.join(out, "s.png"), os.path.join(self.d, "leak.png"))
            except (OSError, NotImplementedError):
                self.skipTest("no symlinks on this platform")
            self.assertEqual(400, self.c.get("/api/image/raw", params={"path": "leak.png"}).status_code)
            self.assertEqual(400, self.c.get("/api/image/info", params={"path": "leak.png"}).status_code)

    def test_the_info_route_answers_the_pair(self):
        r = self.c.get("/api/image/info", params={"path": "img/a.png", "base": "HEAD"})
        self.assertEqual(200, r.status_code)
        j = r.json()
        self.assertEqual((30, 60), (j["old"]["width"], j["new"]["width"]))
        self.assertEqual("modified", j["status"])
        self.assertFalse(j["sample"])
        self.assertEqual(400, self.c.get("/api/image/info", params={"path": "img/a.txt"}).status_code)

    def test_the_sample_has_no_files_and_says_so(self):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        st = AppState(tempfile.gettempdir(), TOKEN, PORT, None, True)
        c = TestClient(build_app(st), base_url=BASE, follow_redirects=False)
        c.get(f"/pair?t={TOKEN}")
        j = c.get("/api/image/info", params={"path": "a.png"}).json()
        self.assertTrue(j["sample"])
        self.assertEqual("sample", j["status"])


# ================================================================================= the page code
class ThePageNeverPutsAnImageInTheDom(unittest.TestCase):
    """SVG is untrusted: the script that draws the panel must build nodes and use `<img>`, so there
    is no path from a repo file's bytes to markup."""

    def setUp(self):
        with open(os.path.join(STATIC, "features", "imagediff.js"), encoding="utf-8") as fh:
            self.js = fh.read()

    def test_no_markup_sink_is_used(self):
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                     "new Function", "DOMParser", "createContextualFragment", "srcdoc",
                     "<object", "<embed", "<iframe", "<svg"):
            self.assertNotIn(sink, self.js, sink)

    def test_an_image_is_shown_only_through_img_elements_from_the_image_route(self):
        self.assertIn('createElement("img")', self.js)
        self.assertIn("/api/image/raw", self.js)
        self.assertNotRegex(self.js, r"\.(?:src|href)\s*=\s*[^;]*\b(?:data:|blob:)")

    def test_the_worker_script_is_served_same_origin_and_touches_no_dom(self):
        from mokata.ide.server import STATIC_FILES
        self.assertIn("imagediff-worker.js", STATIC_FILES)
        with open(os.path.join(STATIC, "imagediff-worker.js"), encoding="utf-8") as fh:
            w = fh.read()
        self.assertIn("OffscreenCanvas", w)
        self.assertNotRegex(w, r"\bdocument\b|\bwindow\b|innerHTML")

    def test_modes_progress_and_cancel_are_named_in_the_panel(self):
        for needle in ("Side by side", "Swipe", "Onion skin", "Difference", "Cancel",
                       "too large to diff", "open externally"):
            self.assertTrue(needle in self.js or needle.lower() in self.js.lower(), needle)


if __name__ == "__main__":
    unittest.main()
