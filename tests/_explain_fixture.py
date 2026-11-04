"""The P46 fixture repo (plan §2a "done when"): one session whose change

  * alters a CALL PATH       — `checkout` now calls `validate` and `apply_discount`
  * adds a BRANCHY function  — `apply_discount` (if/elif chain, a loop, `and`)
  * adds a MODULE EDGE       — `shop/cart.py` now imports `shop.pricing`
  * injects a PATTERN DEVIATION — `validate` raises `ValueError` where the module raises `ShopError`
  * injects an AC CONTRADICTION — AC-1 says a discount never makes the total negative; the
                                  "BIG" code subtracts 50 from any amount
  * leaves an EDGE CASE UNTESTED — a `PCT150` code (a percentage over 100)
  * REMOVES a function another module still calls (`legacy_total`, called by `shop/report.py`)

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import subprocess

import _support  # noqa: F401
from _plan import sealed

CART_OLD = (
    "from shop.errors import ShopError\n"
    "\n\n"
    "def total(items):\n"
    "    return sum(i for i in items)\n"
    "\n\n"
    "def legacy_total(items):\n"
    "    return total(items)\n"
    "\n\n"
    "def checkout(items):\n"
    "    if not items:\n"
    "        raise ShopError(\"empty cart\")\n"
    "    return total(items)\n")
CART_NEW = (
    "from shop.errors import ShopError\n"
    "from shop.pricing import apply_discount\n"
    "\n\n"
    "def total(items):\n"
    "    return sum(i for i in items)\n"
    "\n\n"
    "def validate(items):\n"
    "    if any(i < 0 for i in items):\n"
    "        raise ValueError(\"negative item\")\n"
    "    return items\n"
    "\n\n"
    "def checkout(items, code=\"\"):\n"
    "    if not items:\n"
    "        raise ShopError(\"empty cart\")\n"
    "    return apply_discount(total(validate(items)), code)\n")
PRICING_NEW = (
    "def apply_discount(amount, code):\n"
    "    if not code:\n"
    "        return amount\n"
    "    if code == \"HALF\":\n"
    "        return amount / 2\n"
    "    elif code == \"BIG\":\n"
    "        return amount - 50\n"
    "    elif code.startswith(\"PCT\") and code[3:].isdigit():\n"
    "        return amount * (100 - int(code[3:])) / 100\n"
    "    for c in (\"X\", \"Y\"):\n"
    "        if code == c and amount > 10:\n"
    "            return amount - 1\n"
    "    return amount\n")
REPORT = ("from shop.cart import legacy_total\n\n\ndef summary(items):\n"
          "    return legacy_total(items)\n")
ERRORS = "class ShopError(Exception):\n    pass\n"
TEST_OLD = "from shop.cart import total\n\n\ndef test_total():\n    assert total([1, 2]) == 3\n"
TEST_NEW = (TEST_OLD + "\n\ndef test_no_code():\n    from shop.pricing import apply_discount\n"
            "    assert apply_discount(10, '') == 10\n")


def _silent(*_a, **_k):
    pass


def git(d, *a):
    return subprocess.run(["git", "-C", d, *a], check=True, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL).stdout


def write(d, rel, text):
    p = os.path.join(d, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def line_of(text: str, needle: str) -> int:
    for n, ln in enumerate(text.split("\n"), 1):
        if needle in ln:
            return n
    raise AssertionError(f"{needle!r} not in text")


def build_repo(d: str) -> None:
    """Base commit + mokata + run r1 (spec with AC-1, AC-2) bound to session S1, whose recorded
    writes are the change above (snapshots: the session is not in a worktree)."""
    from mokata import run_intent as RI
    from mokata import test_runs as TR
    from mokata.config import Surface
    from mokata.govern.resume import PipelineCheckpoint
    from mokata.ide import snapshots as S
    from mokata.init import init_repo
    git(d, "init", "-q", "-b", "main")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    git(d, "config", "commit.gpgsign", "false")
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    write(d, "src/shop/__init__.py", "")
    write(d, "src/shop/errors.py", ERRORS)
    write(d, "src/shop/cart.py", CART_OLD)
    write(d, "src/shop/report.py", REPORT)
    write(d, "tests/test_cart.py", TEST_OLD)
    git(d, "add", "-A")
    git(d, "commit", "-qm", "base")
    surface = Surface.load(d)
    PipelineCheckpoint(surface.state, "r1").ensure_registered()
    crit = [{"id": "AC-1", "text": "a discount never makes the total negative",
             "anchors": [{"file": "src/shop/pricing.py", "symbol": "apply_discount",
                          "kind": "impl", "source": "spec", "confidence": "high"}]},
            {"id": "AC-2", "text": "no code leaves the amount unchanged"}]
    surface.state.write("emitted_spec__r1", sealed({"title": "discounts", "criteria": crit,
                                                     "approach": "a", "domains": []}))
    RI.bind_registered(d, "S1", "r1")
    for rel, new in (("src/shop/cart.py", CART_NEW), ("src/shop/pricing.py", PRICING_NEW),
                     ("tests/test_cart.py", TEST_NEW)):
        path = os.path.join(d, *rel.split("/"))
        S.capture_before_first_write(d, path, "S1", "r1")
        write(d, rel, new)
        S.capture(d, path, S.AFTER, "S1", "r1")
    TR.record_test_run(d, "r1", passed=False, command="python -m unittest",
                       tests=["tests/test_cart.py::test_no_code"],
                       ac_map={"AC-2": ["tests/test_cart.py::test_no_code"]}, at=1000.0)
    TR.record_test_run(d, "r1", passed=True, command="python -m unittest",
                       tests=["tests/test_cart.py::test_no_code"],
                       ac_map={"AC-2": ["tests/test_cart.py::test_no_code"]}, at=2000.0)


def claude_doc(bundle: dict, pages: int) -> dict:
    """What Claude Code would submit for the fixture: every section the plan names, plus four
    claims that MUST be dropped (uncited, one-sided, a traversal path, a too-wide span)."""
    big = line_of(PRICING_NEW, '"BIG"') + 1
    pct = line_of(PRICING_NEW, "PCT")
    ve = line_of(CART_NEW, "raise ValueError")
    return {
        "content_hash": bundle["content_hash"], "model": "claude-opus (self-reported)",
        "pages_read": list(range(1, pages + 1)),
        "sections": [
            {"id": "summary", "blocks": [
                {"kind": "prose", "text": "checkout now validates items and applies a discount "
                 "code before returning the total.",
                 "cites": ["fact:s:head:src/shop/cart.py::checkout"]},
                {"kind": "prose", "text": "This sentence cites nothing and must be dropped."}]},
            {"id": "walkthrough", "blocks": [
                {"kind": "example", "text": "apply_discount(80, 'HALF') -> 40.0",
                 "cites": [f"src/shop/pricing.py:{line_of(PRICING_NEW, 'HALF')}-"
                           f"{line_of(PRICING_NEW, 'HALF') + 1}"]},
                {"kind": "code", "cites": [f"src/shop/pricing.py:{big - 1}-{big}"]}]},
            {"id": "flow", "blocks": [{"kind": "use_fact_diagram", "diagram": "flow"}]},
            {"id": "flowchart", "blocks": [
                {"kind": "flowchart", "text": "the discount code decides the branch",
                 "mermaid": "flowchart TD\n  a{code empty} -->|yes| r[return amount]\n"
                            "  a -->|no| h{HALF}\n  h -->|yes| hh[amount / 2]\n"
                            "  h -->|no| b{BIG}\n  b -->|yes| bb[amount - 50]\n",
                 "cites": ["fact:c:src/shop/pricing.py::apply_discount"]}]},
            {"id": "architecture", "blocks": [{"kind": "use_fact_diagram",
                                               "diagram": "architecture"}]},
            {"id": "edge_cases", "blocks": [
                {"kind": "edge_case", "text": "a PCT code over 100 (PCT150) makes the total "
                 "negative", "cites": [f"src/shop/pricing.py:{pct}-{pct + 1}"]}]},
            {"id": "discrepancies", "blocks": [
                {"kind": "finding", "text": "validate raises ValueError; the module's other "
                 "errors are ShopError.", "cites": [f"src/shop/cart.py:{ve}", "fact:p:1"]},
                {"kind": "finding", "text": "BIG subtracts 50 from any amount, contradicting "
                 "AC-1 (never negative).", "cites": [f"src/shop/pricing.py:{big}",
                                                     "fact:ac:AC-1"]},
                {"kind": "finding", "text": "one-sided: only the change is cited.",
                 "cites": [f"src/shop/pricing.py:{big}"]},
                {"kind": "prose", "text": "a traversal", "cites": ["../../etc/passwd:1"]},
                {"kind": "prose", "text": "too wide", "cites": ["src/shop/report.py:1-400"]}]},
            {"id": "tests", "blocks": [
                {"kind": "prose", "text": "AC-1 has no test.", "cites": ["fact:ac:AC-1"]}]},
        ]}
