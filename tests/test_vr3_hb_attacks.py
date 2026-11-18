"""Round-3 verifier attacks on 0.0.29 H-b: every-k sweep of stores that are NOT root entries (outside git dir,
deeper git dir, worktree commondir) and fresh in-root moves (.mokata, in-root gitdir, differently spelled gitfile).
Grading: a read that returns protected bytes is listed; it is only acceptable when it equals a read of the
already-swapped tree (k=1 equivalence) AND the store had not yet been judged at that k."""
from __future__ import annotations

import os
import unittest

import _support  # noqa: F401
from test_hb_verify_attacks import Tree, Sweep, w, OUTSIDE, GIT, OBJ


def outside_layout(t):
    out = os.path.join(t.base, "outgit")
    os.rename(t.p(".git"), out)
    w(t.p(".git"), ("gitdir: " + out + "\n").encode())
    return out


def worktree_layout(t):
    """linked worktree: .git file -> <base>/maingit/worktrees/wt ; commondir -> ../.. ."""
    main = os.path.join(t.base, "maingit")
    wt = os.path.join(main, "worktrees", "wt")
    os.rename(t.p(".git"), main)
    w(os.path.join(wt, "commondir"), b"../..\n")
    w(os.path.join(wt, "HEAD"), GIT)
    w(os.path.join(wt, "config"), GIT)
    w(t.p(".git"), ("gitdir: " + wt + "\n").encode())
    return wt


def worktree_common_layout(t):
    worktree_layout(t)
    return os.path.join(t.base, "maingit")


def deeper_layout(t):
    d = t.p("sub", "gd")
    os.makedirs(os.path.dirname(d), exist_ok=True)
    os.rename(t.p(".git"), d)
    w(t.p(".git"), b"gitdir: sub/gd\n")
    return d


def inroot_layout(name):
    def f(t):
        d = t.p(name)
        os.rename(t.p(".git"), d)
        w(t.p(".git"), ("gitdir: " + name + "\n").encode())
        return d
    return f


def inroot_dotslash(t):
    d = t.p(".gitdata")
    os.rename(t.p(".git"), d)
    w(t.p(".git"), b"gitdir: ./sub/../.gitdata/\n")
    return d


def mokata_layout(t):
    w(t.p(".mokata", "config"), GIT)
    return t.p(".mokata")


def mokata_symlink(t):
    w(t.p(".mokatareal", "config"), GIT)
    os.rename(t.p(".mokata"), t.p(".mokata.old"))
    os.symlink(t.p(".mokatareal"), t.p(".mokata"))
    return t.p(".mokatareal")


def swap_mv(t, store):
    def swap():
        os.rename(t.p("docs"), t.p("docs.aside"))
        os.rename(store, t.p("docs"))
    return swap


def swap_mv_deep(t, store):
    def swap():
        os.rename(t.p("docs", "sub"), t.p("docs", "sub.aside"))
        os.rename(store, t.p("docs", "sub"))
    return swap


def swap_dirlink(t, store):
    def swap():
        os.rename(t.p("docs"), t.p("docs.aside"))
        os.symlink(store, t.p("docs"))
    return swap


def swap_hardlink(t, store):
    def swap():
        os.rename(t.p("docs", "config"), t.p("docs", "config.aside"))
        os.link(os.path.join(store, "config"), t.p("docs", "config"))
    return swap


def swap_gitfile_to_docs(t, store):
    def swap():
        os.rename(store, t.p("docs.gd"))
        with open(t.p(".git"), "wb") as fh:
            fh.write(b"gitdir: docs\n")
        os.rename(t.p("docs"), t.p("docs.aside"))
        os.rename(t.p("docs.gd"), t.p("docs"))
    return swap


def swap_gitfile_to_dir(t, store):
    def swap():
        os.rename(t.p(".git"), t.p(".git.aside"))
        os.mkdir(t.p(".git"))
        os.rename(t.p("docs"), t.p("docs.aside"))
        os.rename(store, t.p("docs"))
    return swap


def swap_symlink_away(t, store):
    def swap():
        os.rename(store, t.p("spare"))
        os.symlink(t.p("docs"), store)
        os.rename(t.p("docs"), t.p("docs.aside"))
        os.rename(t.p("spare"), t.p("docs"))
    return swap


def swap_mv_symlink_back(t, store):
    def swap():
        os.rename(t.p("docs"), t.p("docs.aside"))
        os.rename(store, t.p("docs"))
        os.symlink(t.p("docs"), store)
    return swap


LAYOUTS = {
    "outside": outside_layout,
    "worktree": worktree_layout,
    "worktree-common": worktree_common_layout,
    "deeper": deeper_layout,
    "inroot .gitdata": inroot_layout(".gitdata"),
    "inroot gd": inroot_layout("gd"),
    "inroot ./sub/../.gitdata/": inroot_dotslash,
    ".mokata": mokata_layout,
    ".mokata symlink": mokata_symlink,
}
SWAPS = {
    "mv->docs": swap_mv, "mv->docs/sub": swap_mv_deep, "dirlink": swap_dirlink, "hardlink": swap_hardlink,
    "gitfile->docs": swap_gitfile_to_docs, ".git file->dir": swap_gitfile_to_dir,
    "gitdir->symlink-to-docs": swap_symlink_away, "mv + symlink back": swap_mv_symlink_back,
}
PROT = lambda d: bool(d) and (OUTSIDE in d or GIT in d or OBJ in d)  # noqa: E731


def _rel(sn):
    return "docs/sub/f.txt" if sn == "mv->docs/sub" else "docs/config"


class _H(unittest.TestCase):
    def runTest(self):
        pass


def table(only=None):
    h = _H()
    rows = {}
    for ln, layout in LAYOUTS.items():
        if only and ln not in only:
            continue
        for sn, swapf in SWAPS.items():
            rel = _rel(sn)
            t0 = Tree(h)
            layout(t0)
            n = Sweep(t0.repo, rel, lambda: None).run(10 ** 9)[2]["n"]
            prot, nonequiv, fifo = [], [], []
            for k in range(1, n + 2):
                t = Tree(h)
                store = layout(t)
                got, data, st = Sweep(t.repo, rel, swapf(t, store)).run(k)
                if st["blocking_fifo"]:
                    fifo.append(k)
                if PROT(data):
                    prot.append(k)
                    ref = Tree(h)
                    rstore = layout(ref)
                    swapf(ref, rstore)()
                    want = Sweep(ref.repo, rel, lambda: None).run(10 ** 9)[1]
                    if data != want:
                        nonequiv.append(k)
            rows[(ln, sn)] = (n, prot, nonequiv, fifo)
            print(f"{ln:26s} {sn:24s} n={n:3d} protected_k={prot} nonequiv_k={nonequiv} fifo={fifo}", flush=True)
    return rows


class Round3(unittest.TestCase):
    def test_in_root_stores_never_leak_at_k_ge_2(self):
        """every in-root store (.mokata, in-root gitdirs, any spelling) moved at every k >= 2: no protected byte"""
        bad = []
        for ln in (".mokata", "inroot .gitdata", "inroot gd", "inroot ./sub/../.gitdata/"):
            layout = LAYOUTS[ln]
            for sn in ("mv->docs", "mv->docs/sub", "dirlink", "gitfile->docs", "mv + symlink back"):
                rel = _rel(sn)
                t0 = Tree(self)
                layout(t0)
                n = Sweep(t0.repo, rel, lambda: None).run(10 ** 9)[2]["n"]
                for k in range(2, n + 2):
                    t = Tree(self)
                    store = layout(t)
                    got, data, st = Sweep(t.repo, rel, SWAPS[sn](t, store)).run(k)
                    if st["blocking_fifo"] or PROT(data):
                        bad.append((ln, sn, k, data))
        self.assertEqual([], bad)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "table":
        table(sys.argv[2:] or None)
    else:
        unittest.main()
