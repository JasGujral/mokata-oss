"""The LOCOMO dataset — fetched ONLY by the explicit `python -m bench.locomo fetch` command.

Nothing else in the harness touches the network: `load()` reads the local copy and raises
`DatasetMissing` (naming the fetch command) when it is absent. Tests never fetch; `fetch()` takes
an injectable opener so its lock-writing can be tested offline.

The fetch writes `locomo.lock.json` beside the data: url, sha256, bytes, fetched_at. A run records
that sha; publication refuses when the data on disk no longer matches it (pin 7).

LOCOMO categories (as mem0 used them; VERIFY against the fetched dataset's README before
publishing): 1 multi-hop · 2 temporal · 3 open-domain · 4 single-hop · 5 adversarial (excluded,
as in mem0's evaluation).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

DATASET_URL = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"
DATA_FILE = "locomo10.json"
LOCK_FILE = "locomo.lock.json"
CATEGORIES = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop", 5: "adversarial"}
EXCLUDED_CATEGORIES = (5,)


class DatasetMissing(FileNotFoundError):
    pass


@dataclass
class Turn:
    dia_id: str
    speaker: str
    text: str
    session: int
    date: str


@dataclass
class QA:
    question: str
    answer: str
    category: int
    evidence: List[str] = field(default_factory=list)


@dataclass
class Conversation:
    id: str
    turns: List[Turn]
    qa: List[QA]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(dest_dir: str, url: str = DATASET_URL,
          opener: Optional[Callable[[str], bytes]] = None) -> Dict[str, Any]:
    """Download the dataset and write its lock. The ONLY network call in bench.locomo."""
    if opener is None:
        import urllib.request

        def opener(u: str) -> bytes:
            with urllib.request.urlopen(u, timeout=300) as resp:   # noqa: S310 (fixed https URL)
                return resp.read()
    data = opener(url)
    json.loads(data.decode("utf-8"))                     # refuse to lock something that is not JSON
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, DATA_FILE)
    with open(path, "wb") as fh:
        fh.write(data)
    lock = {"url": url, "sha256": sha256_file(path), "bytes": len(data),
            "fetched_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    with open(os.path.join(dest_dir, LOCK_FILE), "w", encoding="utf-8") as fh:
        json.dump(lock, fh, indent=2)
    return lock


def read_lock(data_dir: str) -> Dict[str, Any]:
    p = os.path.join(data_dir, LOCK_FILE)
    if not os.path.exists(p):
        raise DatasetMissing(f"no {LOCK_FILE} in {data_dir} — run `python -m bench.locomo fetch "
                             f"--data {data_dir}` (an explicit download; nothing else fetches)")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _session_no(key: str) -> Optional[int]:
    m = re.fullmatch(r"session_(\d+)", key)
    return int(m.group(1)) if m else None


def parse(raw: List[Dict[str, Any]]) -> List[Conversation]:
    convs = []
    for i, sample in enumerate(raw):
        conv = sample.get("conversation") or {}
        turns: List[Turn] = []
        for key in sorted((k for k in conv if _session_no(k) is not None), key=_session_no):
            n = _session_no(key)
            date = str(conv.get(f"{key}_date_time", ""))
            for t in conv[key] or []:
                turns.append(Turn(dia_id=str(t.get("dia_id")), speaker=str(t.get("speaker", "")),
                                  text=str(t.get("text", "")), session=n, date=date))
        qa = [QA(question=str(q["question"]), answer=str(q.get("answer", q.get("adversarial_answer", ""))),
                 category=int(q.get("category", 0)), evidence=[str(e) for e in q.get("evidence") or []])
              for q in sample.get("qa") or []]
        convs.append(Conversation(id=str(sample.get("sample_id", f"conv-{i}")), turns=turns, qa=qa))
    return convs


def load(data_dir: str) -> List[Conversation]:
    path = os.path.join(data_dir, DATA_FILE)
    if not os.path.exists(path):
        raise DatasetMissing(f"{path} not found — run `python -m bench.locomo fetch --data "
                             f"{data_dir}` (an explicit download; nothing else fetches)")
    with open(path, encoding="utf-8") as fh:
        return parse(json.load(fh))
