"""Stage 35e / DB.S4 — pluggable embedder seam, a zero-dependency local embedder, and the
auto-detected blessed extra.

Semantic memory turns text into a vector. The embedder is a SEAM: any callable
``text -> list[float]``. The default :class:`HashingEmbedder` is deterministic, local, and
dependency-free (a hashing bag-of-words), so semantic memory works with **zero deps** and
never forces a network or a model download. Real providers (a local model, or the store's own
embedding) are wired by config. With **no embedder configured the semantic tier is simply
OFF** — lexical (and graph, when wired) still work (local-first, P8; degrade-never-break).

DB.S4 adds three things, all of them consequences of one decision (Jas, 2026-07-14): a
hashing-only default made "semantic retrieval" untrue for most installs, so ONE blessed
lightweight extra is auto-detected — and the moment two embedders can both be live, their
vectors must never meet.

  1. **Identity.** Every embedder carries an ``embedder_id`` and a ``dim``. A bare callable has
     neither, so :func:`embedder_identity` answers ``("custom", <probed dim>)`` for it. The id is
     what gets STAMPED on a vector index; the stamp is the whole reason identity exists.
  2. **The blessed extra.** ``mokata[embeddings]`` = model2vec static embeddings (numpy-only, no
     torch, CPU-fast). :class:`Model2VecEmbedder` imports it LAZILY — the core never imports
     numpy — and :func:`detect_embedder` returns it only when the import AND the model both
     actually work.
  3. **Degrade-clean, bounded.** The model is fetched on first use, so first use can fail:
     offline, no cache, a hub error. That degrades to :class:`HashingEmbedder` with a classed D5
     notice — never a hang (the fetch runs under a bounded, offline-first probe) and never a
     crash. An install that CAN'T embed is honestly worse than one that never tried.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import math
import os
import re
import zlib
from typing import Callable, List, Optional, Tuple

from ..degrade import FAILURE_ENGINE
from ..errors import DegradedCapability

EMBED_DIM = 64
_WORD = re.compile(r"[a-z0-9]+")

# An embedder is any callable text -> vector. (Kept as a plain alias so the core stays
# dependency-free and any provider — local model, hosted, the store's own — can be wired.)
Embedder = Callable[[str], List[float]]

# --- identity -------------------------------------------------------------------------------
# The id STAMPED on a vector index. Vectors from two different embedders are not comparable —
# cosine between them is arithmetic over unrelated coordinate systems, which ranks confidently and
# means nothing — so the id is the key the stamp binding turns on. Version the id, not just the
# name: a model2vec model change is as breaking as an embedder change.
HASHING_ID = "hashing-v1"
MODEL2VEC_MODEL = "minishlab/potion-base-8M"
MODEL2VEC_ID = f"model2vec:{MODEL2VEC_MODEL}"
CUSTOM_ID = "custom"

# The blessed extra, as a pip requirement — ONE place, shared by the consent flow and the docs.
EMBEDDINGS_EXTRA = "mokata[embeddings]"

# Bounded first-use model fetch (the D0 discipline applied to a network-capable import). A model
# download on a cold cache is the one embedder path that can block, and blocking a `recall` is
# strictly worse than ranking it lexically.
MODEL_FETCH_TIMEOUT_S = 30.0


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class HashingEmbedder:
    """A deterministic, local, dependency-free embedder: hashes word tokens into a fixed-dim
    bag-of-words vector (L2-normalized). Reproducible across processes (a stable hash, not the
    salted built-in ``hash``), so rankings are deterministic — good enough for tiered ranking
    and the default test double, with zero deps and no network.

    Honest about what it is: this is TOKEN-HASH similarity, not meaning. Two paraphrases sharing
    no tokens score 0. That is why DB.S4 blesses a real extra — and why `doctor` says which of the
    two is live rather than printing "semantic" over both."""

    embedder_id = HASHING_ID

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim

    def __call__(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        for tok in _WORD.findall((text or "").lower()):
            vec[zlib.adler32(tok.encode("utf-8")) % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec


class ModelUnavailable(DegradedCapability):
    """The blessed extra is absent, or present but unable to produce a vector (no cached model
    and no network). Callers degrade to :class:`HashingEmbedder` — never propagate.

    A `DegradedCapability` (D5 taxonomy), classed `engine-unavailable`: the capability exists in
    principle and its ENGINE could not be brought up. Not `unreachable` — that class means "the
    shared database is not answering", and every word of it is wrong for a local model file."""

    failure_class = FAILURE_ENGINE


class Model2VecEmbedder:
    """The blessed extra: model2vec STATIC embeddings (numpy-only, no torch, CPU-fast).

    Static embeddings are a lookup table distilled from a sentence transformer: no forward pass,
    no GPU, ~30MB on disk — which is exactly why this one is blessed and the heavy families
    (sentence-transformers, hosted APIs) stay documented-never-bundled.

    Constructed EAGERLY on purpose: `__init__` loads the model, so a machine that cannot load it
    fails HERE, at detection time, where the caller degrades cleanly — rather than on the first
    `recall`, mid-query, where there is nothing left to do but crash or silently return zeros.
    """

    embedder_id = MODEL2VEC_ID

    def __init__(self, model_name: str = MODEL2VEC_MODEL, model: object = None) -> None:
        self.model_name = model_name
        self.embedder_id = f"model2vec:{model_name}"
        self._model = model if model is not None else _load_model2vec(model_name)
        # The dim is the MODEL's, never a mokata constant: stamping a wrong dim is how a re-embed
        # migration would silently no-op. Probe it once, from a real encode.
        probe = self._encode_one("dimension probe")
        self.dim = len(probe)
        if not self.dim:
            raise ModelUnavailable(f"model2vec model '{model_name}' produced an empty vector")

    def _encode_one(self, text: str) -> List[float]:
        vecs = self._model.encode([text or ""])
        row = vecs[0]
        return [float(x) for x in row]

    def __call__(self, text: str) -> List[float]:
        vec = self._encode_one(text)
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec


def _load_model2vec(model_name: str):
    """Import model2vec and load the static model, BOUNDED and offline-first.

    Two failures live here and they are different: the extra is not installed (ImportError — the
    zero-dep default, not a degrade at all), and the extra IS installed but the model cannot be
    obtained (cold cache + offline, a hub error, a corrupt cache). Both raise `ModelUnavailable`
    so the caller has ONE thing to catch, but the message distinguishes them because the fixes
    do: one is `pip install`, the other is "get online once".
    """
    try:
        from model2vec import StaticModel                        # type: ignore
    except ImportError as exc:                                    # the extra simply isn't installed
        raise ModelUnavailable(f"model2vec is not installed ({exc})") from exc

    # Bound the fetch. `HF_HUB_ETAG_TIMEOUT`/`HF_HUB_DOWNLOAD_TIMEOUT` are what huggingface_hub
    # (model2vec's fetcher) actually reads; without them a cold cache on a black-holed network
    # hangs a `recall` rather than falling to the lexical floor. Set only for THIS load, and
    # restored after, so mokata never mutates the caller's environment durably.
    bounds = {"HF_HUB_ETAG_TIMEOUT": str(int(MODEL_FETCH_TIMEOUT_S)),
              "HF_HUB_DOWNLOAD_TIMEOUT": str(int(MODEL_FETCH_TIMEOUT_S))}
    saved = {k: os.environ.get(k) for k in bounds}
    os.environ.update(bounds)
    try:
        return StaticModel.from_pretrained(model_name)
    except Exception as exc:
        # DEGRADE_CLEAN, and deliberately broad with no narrow class to name: the raisables span
        # huggingface_hub's error tree, `requests`' transport errors, `safetensors`, and plain
        # OSError on a corrupt cache — every one of them from an OPTIONAL extra mokata cannot
        # import at module scope to name in an `except`. There is exactly one honest response to
        # all of them (fall back to hashing), so narrowing would only convert a clean degrade into
        # an uncaught crash on whichever class we failed to guess.
        raise ModelUnavailable(f"model2vec model '{model_name}' unavailable: "
                               f"{type(exc).__name__}: {exc}") from exc
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def embedder_identity(embedder: Optional[Embedder]) -> Tuple[str, int]:
    """The ``(embedder_id, dim)`` STAMPED on a vector index for `embedder`.

    A class-based embedder declares both. A bare callable (the seam's whole point — any
    `text -> list[float]` is legal) declares neither, so its dim is PROBED from a real call and
    its id is `"custom"`. That is deliberately coarse: mokata cannot tell two anonymous lambdas
    apart, so it refuses to pretend it can, and a `custom` stamp is a promise about the dim only.
    Returns `("", 0)` for no embedder — the semantic tier is off and there is nothing to stamp."""
    if embedder is None:
        return "", 0
    eid = getattr(embedder, "embedder_id", None) or CUSTOM_ID
    dim = getattr(embedder, "dim", None)
    if not dim:
        try:
            dim = len(embedder("dimension probe"))
        except Exception:
            # DEGRADE_CLEAN: an unprobeable custom embedder is a caller's broken callable, not a
            # mokata failure. `0` means "unknown dim" and the stamp check treats it as a mismatch —
            # fail-closed, so an unidentifiable embedder can never be waved through onto an index.
            dim = 0
    return str(eid), int(dim or 0)


def detect_embedder(*, degrade_out: Optional[Callable[[str], None]] = None
                    ) -> Embedder:
    """DB.S4 — the AUTO tier: the blessed extra when it is installed AND works, else hashing.

    Never returns None and never raises: this is the "semantic on, quality honest" path. The
    difference between the two outcomes is reported, not hidden — but only when it is a real
    DEGRADE. The extra being absent is the documented zero-dep default (no notice; a notice that
    fires on every default install is noise, the DB.S3 lexical-floor lesson). The extra being
    INSTALLED and unusable is a degrade: the user paid 30MB for semantic recall and is getting
    token-hash, so `note_degraded` says so ONCE with the fix."""
    try:
        return Model2VecEmbedder()
    except ModelUnavailable as exc:
        if _extra_is_installed():
            from ..degrade import FAILURE_ENGINE, note_degraded
            note_degraded(
                "memory-embedder", FAILURE_ENGINE,
                fallback="semantic recall is TOKEN-HASH (hashing) — the model could not be loaded",
                fix="run `mokata doctor`; the model is fetched once — connect to the network "
                    "once and re-run, or `pip install -U mokata[embeddings]`",
                detail=str(exc), out=degrade_out)
        return HashingEmbedder()


def _extra_is_installed() -> bool:
    """True when `model2vec` imports — i.e. the extra IS present and the failure was the MODEL.
    Import-only (no model load), so it is cheap and cannot itself touch the network."""
    try:
        import model2vec                                          # noqa: F401  # type: ignore
        return True
    except ImportError:
        return False


# Registry so config can name an embedder; unknown / None -> semantic stays OFF.
def make_embedder(name: Optional[str]) -> Optional[Embedder]:
    """Resolve an embedder by name (from `settings.memory.embedder`).

      * ``"auto"``               -> DB.S4 detection: the blessed extra if usable, else hashing;
      * ``"hashing"``/``"local"``-> the zero-dep default, explicitly;
      * ``"model2vec"``          -> the blessed extra, explicitly (degrades to hashing if absent —
                                    an explicit ask still never breaks a recall);
      * anything else incl. None -> None, so the semantic tier is OFF unless explicitly wired.

    The None default is UNCHANGED from Stage 35e: a repo that has not opted in gets byte-identical
    behaviour, because opting a user into embedding their memory is exactly the kind of thing P2
    says you ask about first."""
    if name in ("hashing", "local"):
        return HashingEmbedder()
    if name in ("auto", "model2vec", "embeddings"):
        return detect_embedder()
    return None
