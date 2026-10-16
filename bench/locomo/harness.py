"""Run the LOCOMO ablation: arms × conversations × repeats, archived per G2's layout (pin 4):

    <out>/run.json
    <out>/{arm}/{conversation}/run{N}/answers.jsonl   question, category, gold, retrieved ids,
                                                      tokens_per_retrieval, answer, answer_raw
    <out>/{arm}/{conversation}/run{N}/verdicts.jsonl  judge label + judge raw output
    <out>/{arm}/{conversation}/run{N}/gate_ledger.jsonl  every benchmark auto-approval
    <out>/{arm}/{conversation}/run{N}/meta.json

Fresh state per conversation per run (G2's fresh-clone rule, adapted): a NEW memory store each time.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import tempfile
from typing import Any, Callable, Dict, List, Optional, Sequence

from .dataset import DATASET_URL, EXCLUDED_CATEGORIES, Conversation
from .llm import REAL_IDS, answer_prompt, judge_prompt, parse_label
from .retrieval import ARM_FULL, ARMS, EMBEDDER_ARMS, MokataRetriever, estimate_tokens, render_turn

SCHEMA = "locomo-run/1"
MIN_REPEATS = 5
GATE_POSTURE = "benchmark auto-approve (ledgered)"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


def derive_kind(answerer_id: str, judge_id: str, dataset_lock: Dict[str, Any]) -> str:
    real = (answerer_id in REAL_IDS and judge_id in REAL_IDS
            and dataset_lock.get("url") == DATASET_URL and bool(dataset_lock.get("sha256")))
    return "real" if real else "fixture"


def run_benchmark(*, conversations: Sequence[Conversation], out_dir: str, answerer: Any,
                  judge: Any, dataset_lock: Dict[str, Any], repeats: int = MIN_REPEATS,
                  arms: Sequence[str] = ARMS, embedder: Any = None, k: int = 10,
                  max_questions: Optional[int] = None,
                  retriever_factory: Optional[Callable[[str, str], Any]] = None,
                  log: Callable[[str], None] = lambda _m: None) -> str:
    if os.path.exists(out_dir) and os.listdir(out_dir):
        raise FileExistsError(f"{out_dir} is not empty — a run archive is never overwritten")
    unknown = [a for a in arms if a not in ARMS]
    if unknown:
        raise ValueError(f"unknown arms {unknown}")
    if any(a in EMBEDDER_ARMS for a in arms) and embedder is None and retriever_factory is None:
        raise ValueError("arms C/D need an embedder (pin 1: model2vec for a publishable entry)")
    factory = retriever_factory or (lambda arm, wd: MokataRetriever(arm, wd, embedder))
    os.makedirs(out_dir, exist_ok=True)
    emb_id = getattr(embedder, "embedder_id", None) if embedder is not None else None
    manifest = {
        "schema": SCHEMA, "kind": derive_kind(answerer.id, judge.id, dataset_lock),
        "answerer": {"id": answerer.id, "model": getattr(answerer, "model", "")},
        "judge": {"id": judge.id, "model": getattr(judge, "model", "")},
        "dataset": dataset_lock, "repeats": repeats, "arms": list(arms), "k": k,
        "embedder": emb_id, "gate_posture": GATE_POSTURE, "llm_fact_extraction": False,
        "conversations": [c.id for c in conversations], "max_questions": max_questions,
        "excluded_categories": list(EXCLUDED_CATEGORIES), "started": _now(), "finished": None,
    }
    _write(os.path.join(out_dir, "run.json"), manifest)
    work = tempfile.mkdtemp(prefix="locomo-work-")
    try:
        for n in range(1, repeats + 1):
            for conv in conversations:
                qas = [q for q in conv.qa if q.category not in EXCLUDED_CATEGORIES][:max_questions]
                for arm in arms:
                    rdir = os.path.join(out_dir, arm.replace(":", "_"), conv.id, f"run{n}")
                    os.makedirs(rdir)
                    log(f"[locomo] {arm}/{conv.id}/run{n} ({len(qas)} questions)")
                    meta: Dict[str, Any] = {"arm": arm, "conversation": conv.id, "repeat": n,
                                            "started": _now()}
                    answers, verdicts, ledger = [], [], []
                    try:
                        retr = None
                        if arm != ARM_FULL:
                            retr = factory(arm, os.path.join(work, f"{arm[0]}-{conv.id}-{n}"))
                            retr.ingest(conv)
                            ledger = list(getattr(retr, "ledger", []))
                            meta["embedder"] = getattr(retr, "embedder_id", None)
                        full = "\n".join(render_turn(t) for t in conv.turns)
                        for q in qas:
                            if retr is None:
                                ids, context = [], full
                            else:
                                hits = retr.retrieve(q.question, k)
                                ids, context = [h[0] for h in hits], "\n".join(h[1] for h in hits)
                            raw = answerer.complete(answer_prompt(q.question, context))
                            answers.append({"question": q.question, "category": q.category,
                                            "gold": q.answer, "retrieved": ids,
                                            "evidence": q.evidence,
                                            "tokens_per_retrieval": estimate_tokens(context),
                                            "answer": raw.strip()})
                            jraw = judge.complete(judge_prompt(q.question, q.answer, raw.strip()))
                            verdicts.append({"question": q.question, "category": q.category,
                                             "correct": parse_label(jraw), "judge_raw": jraw})
                    except Exception as exc:   # recorded, never the whole run
                        meta["harness_error"] = f"{type(exc).__name__}: {exc}"
                    finally:
                        _jsonl(os.path.join(rdir, "answers.jsonl"), answers)
                        _jsonl(os.path.join(rdir, "verdicts.jsonl"), verdicts)
                        _jsonl(os.path.join(rdir, "gate_ledger.jsonl"), ledger)
                        meta.update(finished=_now(), questions=len(qas), answered=len(answers),
                                    auto_approved=sum(1 for x in ledger if x.get("committed")),
                                    secret_blocked=sum(1 for x in ledger if x.get("blocked")))
                        _write(os.path.join(rdir, "meta.json"), meta)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    manifest["finished"] = _now()
    _write(os.path.join(out_dir, "run.json"), manifest)
    return out_dir


def _write(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
