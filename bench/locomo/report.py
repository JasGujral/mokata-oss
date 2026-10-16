"""The LOCOMO report and the PUBLISH gate — where pins 1, 3, 4, 6, 7 and 8 are enforced.

`render_report()` always works (a fixture renders with a FIXTURE banner) — reviewing a draft is
not publishing it. `publish()` is the one path to a publishable artifact, and it REFUSES, listing
every reason, unless all pins hold. There is deliberately no option that changes the title or the
headline: pin 8 says a below-field result is published AS-IS or NOT AT ALL, so the only lever is
`acknowledge_below_field`, which adds the gap line and nothing else.

THE HEADLINE IS DECLARED UP FRONT: arm D (the full mokata retrieval stack). Picking the best arm
after seeing the numbers would itself be a reframing.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
from typing import Any, Dict, List, Optional

from .dataset import CATEGORIES, DATA_FILE, EXCLUDED_CATEGORIES, sha256_file
from .harness import GATE_POSTURE, MIN_REPEATS, derive_kind
from .pins import open_disclosures
from .retrieval import ARM_D, ARMS, EMBEDDER_ARMS

HEADLINE_ARM = ARM_D
#: Pin 8's "materially below", declared before any run: absolute J-score points.
MATERIAL_GAP_PP = 5.0
#: The field number pin 8 compares against. Recorded from doc 84's BM row, which cites
#: arXiv:2504.19413; `verified` stays False until someone checks the figure (and its metric — J
#: score vs another accuracy) against the source. An unverified number cannot gate anything.
FIELD_REFERENCE = {"system": "mem0", "metric": "LOCOMO (as recorded in doc 84 BM row)",
                   "value": 92.5, "source": "arXiv:2504.19413 via doc 84 BM row", "verified": False}
HASHING_IDS = ("hashing-v1",)
FIXTURE_BANNER = "> ⚠ **FIXTURE RUN — NOT EVIDENCE.** Fake answerer/judge or unlocked dataset.\n"


class PublishRefused(RuntimeError):
    def __init__(self, reasons: List[str]):
        super().__init__("REFUSING TO PUBLISH:\n  " + "\n  ".join(reasons))
        self.reasons = reasons


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class LocomoArchive:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        with open(os.path.join(self.path, "run.json"), encoding="utf-8") as fh:
            self.manifest = json.load(fh)

    def run_dirs(self, arm: str) -> List[str]:
        out = []
        for conv in self.manifest["conversations"]:
            for n in range(1, int(self.manifest["repeats"]) + 1):
                out.append(os.path.join(self.path, arm.replace(":", "_"), conv, f"run{n}"))
        return out

    @property
    def is_real(self) -> bool:
        m = self.manifest
        return m.get("kind") == "real" == derive_kind(m["answerer"]["id"], m["judge"]["id"],
                                                       m.get("dataset") or {})

    def digest(self) -> str:
        h = hashlib.sha256()
        files = [os.path.join(self.path, "run.json")]
        for arm in self.manifest["arms"]:
            for d in self.run_dirs(arm):
                files += [os.path.join(d, f) for f in ("answers.jsonl", "verdicts.jsonl", "meta.json")]
        for f in files:
            h.update(os.path.relpath(f, self.path).replace(os.sep, "/").encode())
            if os.path.exists(f):
                with open(f, "rb") as fh:
                    h.update(fh.read().replace(b"\r\n", b"\n"))
        return h.hexdigest()


def arm_stats(arch: LocomoArchive, arm: str) -> Dict[str, Any]:
    labels: List[Optional[bool]] = []
    by_cat: Dict[int, List[bool]] = {}
    tokens: List[int] = []
    for d in arch.run_dirs(arm):
        for v in _read_jsonl(os.path.join(d, "verdicts.jsonl")):
            labels.append(v["correct"])
            if v["correct"] is not None:
                by_cat.setdefault(int(v["category"]), []).append(v["correct"])
        tokens += [a["tokens_per_retrieval"] for a in _read_jsonl(os.path.join(d, "answers.jsonl"))]
    graded = [x for x in labels if x is not None]
    return {"arm": arm, "graded": len(graded), "ungradable": len(labels) - len(graded),
            "j": 100.0 * sum(graded) / len(graded) if graded else None,
            "by_category": {CATEGORIES.get(c, str(c)): 100.0 * sum(v) / len(v)
                            for c, v in sorted(by_cat.items())},
            "median_tokens_per_retrieval": statistics.median(tokens) if tokens else None}


def title(manifest: Dict[str, Any]) -> str:
    """Pin 3 — FIXED template. The three declarations are in the title, not a footnote."""
    return (f"Governed memory on LOCOMO — embedder `{manifest.get('embedder') or 'none'}`, "
            f"gate posture: {GATE_POSTURE}, no LLM fact extraction")


def _f(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.1f}"


def render_report(arch: LocomoArchive, gap_line: str = "") -> str:
    m = arch.manifest
    stats = {a: arm_stats(arch, a) for a in m["arms"]}
    head = stats.get(HEADLINE_ARM, {}).get("j")
    out = [f"# {title(m)}", ""]
    if not arch.is_real:
        out += [FIXTURE_BANNER]
    out += [f"**Headline (declared before the run: arm `{HEADLINE_ARM}`): J = {_f(head)}**", ""]
    if gap_line:
        out += [gap_line, ""]
    out += [f"- Answerer: {m['answerer']['id']} `{m['answerer'].get('model') or 'default'}` · "
            f"Judge: {m['judge']['id']} `{m['judge'].get('model') or 'default'}` (separate)",
            f"- Dataset: `{m['dataset'].get('url', '?')}` sha256 `{m['dataset'].get('sha256', '?')}`",
            f"- {len(m['conversations'])} conversations × {m['repeats']} repeats; top-k {m['k']}; "
            f"categories excluded: {[CATEGORIES[c] for c in EXCLUDED_CATEGORIES]} (category names "
            "follow mem0's mapping — verify against the dataset README)",
            "", "## The ablation (the contribution)", "",
            "| arm | J | ungradable | median tokens / retrieval | "
            + " | ".join(CATEGORIES[c] for c in sorted(CATEGORIES) if c not in EXCLUDED_CATEGORIES)
            + " |", "|---|---|---|---|" + "---|" * (len(CATEGORIES) - len(EXCLUDED_CATEGORIES))]
    for a in m["arms"]:
        s = stats[a]
        cats = " | ".join(_f(s["by_category"].get(CATEGORIES[c])) for c in sorted(CATEGORIES)
                          if c not in EXCLUDED_CATEGORIES)
        out.append(f"| `{a}` | {_f(s['j'])} | {s['ungradable']} | "
                   f"{_f(s['median_tokens_per_retrieval'])} | {cats} |")
    out += ["", "## Context, not a same-row comparison", "",
            f"mem0 and Zep run an LLM over the dialogue to EXTRACT facts before retrieval; mokata "
            f"stores each turn as-is and every durable write is human-gated (here: {GATE_POSTURE}). "
            f"Field reference: {FIELD_REFERENCE['system']} {FIELD_REFERENCE['value']} "
            f"({FIELD_REFERENCE['source']}; verified: {FIELD_REFERENCE['verified']}).",
            "", "## Reproduce", "", f"Archive digest (sha256): `{arch.digest()}`", "", "```",
            f"python -m bench.locomo report --run {arch.path}", "```", ""]
    return "\n".join(out)


def publish(arch: LocomoArchive, data_dir: str, *, acknowledge_below_field: bool = False) -> str:
    m = arch.manifest
    reasons: List[str] = []
    # pin 4 — declared, real, >= 5 repeats
    if not arch.is_real:
        reasons.append("pin 4: not a real run (answerer/judge must be claude-cli or anthropic-api "
                       "and the dataset locked from the canonical URL)")
    if int(m.get("repeats", 0)) < MIN_REPEATS:
        reasons.append(f"pin 4: repeats={m.get('repeats')} < {MIN_REPEATS}")
    if not m.get("finished"):
        reasons.append("pin 4: run did not finish")
    # pin 6 — every arm including the full-context control
    missing = [a for a in ARMS if a not in m.get("arms", [])]
    if missing:
        reasons.append(f"pin 6: arms missing from the ablation: {missing}")
    # pin 1 — never measured on hashing
    emb = m.get("embedder")
    if any(a in EMBEDDER_ARMS for a in m.get("arms", [])) and (not emb or emb in HASHING_IDS):
        reasons.append(f"pin 1: embedder arms measured on {emb!r} — the entry needs the "
                       "embeddings leg (model2vec), never HashingEmbedder")
    # pin 7 — reproducible from a committed artifact
    data = os.path.join(data_dir, DATA_FILE)
    if not os.path.exists(data):
        reasons.append(f"pin 7: dataset {data} not present to verify against the run's sha")
    elif sha256_file(data) != (m.get("dataset") or {}).get("sha256"):
        reasons.append("pin 7: dataset on disk does not match the sha256 the run recorded")
    for a in m.get("arms", []):
        for d in arch.run_dirs(a):
            for f in ("answers.jsonl", "verdicts.jsonl", "gate_ledger.jsonl", "meta.json"):
                if not os.path.exists(os.path.join(d, f)):
                    reasons.append(f"pin 7: missing {os.path.relpath(os.path.join(d, f), arch.path)}")
            meta = os.path.join(d, "meta.json")
            if os.path.exists(meta):
                with open(meta, encoding="utf-8") as fh:
                    err = json.load(fh).get("harness_error")
                if err:
                    reasons.append(f"pin 7: {os.path.relpath(d, arch.path)} harness error: {err}")
    # pin 8a — no benchmark on a ranker disclosed as regressed
    for d in open_disclosures():
        reasons.append(f"pin 8: ranker disclosed as regressed — {d}")
    # pin 8b — the stop condition
    head = arm_stats(arch, HEADLINE_ARM).get("j") if HEADLINE_ARM in m.get("arms", []) else None
    gap_line = ""
    if not FIELD_REFERENCE["verified"]:
        reasons.append("pin 8: the field reference number is not verified against its source — "
                       "an unverified number cannot decide the stop condition")
    elif head is not None:
        gap = FIELD_REFERENCE["value"] - head
        if gap >= MATERIAL_GAP_PP:
            gap_line = (f"**The headline is {gap:.1f} points below the published field "
                        f"({FIELD_REFERENCE['system']} {FIELD_REFERENCE['value']}). Published as-is.**")
            if not acknowledge_below_field:
                reasons.append(f"pin 8 STOP: headline {head:.1f} is {gap:.1f} pp below the field "
                               f"(material gap {MATERIAL_GAP_PP} pp). Publish AS-IS "
                               "(--acknowledge-below-field) or not at all; it is never reframed.")
    if reasons:
        raise PublishRefused(reasons)
    return render_report(arch, gap_line=gap_line)
