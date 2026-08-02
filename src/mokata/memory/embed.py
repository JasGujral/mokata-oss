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


# --- the NOISE FLOOR ------------------------------------------------------------------------
# DB.S8f (K2) — the cosine an embedder returns between a query and an item that has NOTHING to do
# with it. Every OTHER ranking tier contributes exactly 0.0 to an item it did not reach: no lexical
# overlap is 0.0, no edge is `EDGE_WEIGHT * 0.0`, no telemetry is 0.0. The semantic tier is the one
# tier that does not — cosine between two unrelated bags of tokens is a POSITIVE number, so every
# item in the store sits on a pedestal the fusion reads as evidence.
#
# That pedestal is a property of the EMBEDDER, not of mokata, so it is declared by the embedder and
# the fusion caps the tier's weight against it (`tiered.semantic_weight_for`). A quiet embedder
# earns its full weight; a noisy one is held to the share of the non-match budget it can be trusted
# with. See the RANKING PRINCIPLE at the top of `tiered.py`.

#: The probe corpus for an embedder that declares no floor: short query-shaped text against longer
#: item-shaped text, pairwise UNRELATED but drawn from ordinary shared vocabulary. Shared vocabulary
#: is the point — a disjoint-token probe measures 0.0 against a bag-of-words embedder and would
#: certify the noisiest embedder in the tree as silent.
NOISE_PROBE_QUERIES = (
    "retry budget", "auth token", "cache eviction", "index rebuild",
    "queue backoff", "schema migration", "leader election", "rate limit",
)
NOISE_PROBE_DOCS = (
    "the deployment pipeline runs a smoke test after every release to the staging cluster",
    "quarterly revenue rose on the back of a strong subscription renewal cycle in europe",
    "the kitchen renovation is blocked on a countertop delivery from the supplier",
    "a garbage collection pause was observed during the nightly batch reconciliation job",
    "the onboarding document explains how new engineers request laptop hardware",
    "seasonal rainfall patterns shifted after the reservoir expansion was completed",
    "the design review board approved the new typography scale for marketing pages",
    "a legal hold was placed on the archive pending the outcome of the audit",
)

#: What an UNPROBEABLE embedder is assumed to be. FAIL-CLOSED, and deliberately the worst possible
#: value: an embedder we cannot characterize might return 1.0 between unrelated items, and the
#: principle does not have an exemption for signals we failed to measure. The same posture
#: `embedder_identity` takes when its dim probe raises (`0` → treated as a stamp mismatch).
UNCHARACTERIZED_NOISE_FLOOR = 1.0

_FLOOR_CACHE_ATTR = "_mokata_noise_floor"


class HashingEmbedder:
    """A deterministic, local, dependency-free embedder: hashes word tokens into a fixed-dim
    bag-of-words vector (L2-normalized). Reproducible across processes (a stable hash, not the
    salted built-in ``hash``), so rankings are deterministic — good enough for tiered ranking
    and the default test double, with zero deps and no network.

    Honest about what it is: this is TOKEN-HASH similarity, not meaning. Two paraphrases sharing
    no tokens score 0. That is why DB.S4 blesses a real extra — and why `doctor` says which of the
    two is live rather than printing "semantic" over both."""

    embedder_id = HASHING_ID

    #: DB.S8f — MEASURED, not estimated, and the number is why this embedder is now weighted the
    #: way it is. Over the DB.S8 fixture (`tests/_scale_fixture.py`), cosine between a probe query
    #: and an item that is NOT its answer reaches p99 = 0.6455 at N=2,000 and 0.6124 at N=5,000;
    #: 0.65 covers both. For scale: the fixture's real answers score 0.71-0.83, so this embedder's
    #: noise very nearly OVERLAPS its signal — which is doc 52's M-6 ("a test seam, not semantic
    #: recall") with a number against it.
    #:
    #: Declared rather than probed because a canned probe cannot see a corpus: the probe set above
    #: measures 0.53 for this embedder, which understates what a real store does to it. A declared
    #: floor that a corpus-scale test re-measures is honest; a probe that flatters it is not.
    noise_floor = 0.65

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


def measure_noise_floor(embedder: Embedder,
                        degrade_out: Optional[Callable[[str], None]] = None) -> float:
    """Probe `embedder`'s unrelated-pair cosine over `NOISE_PROBE_QUERIES` x `NOISE_PROBE_DOCS`.

    The WORST observed pair, not the mean: this feeds an inequality that has to hold for the worst
    unrelated item in the store, and a mean would let the tail through by construction. 64 encodes,
    once, on fixed text — so the answer is deterministic and the ranking stays a pure function of
    its inputs.

    Raises nothing: an embedder that cannot be probed is UNCHARACTERIZED, which fails closed.
    """
    try:
        qs = [embedder(q) for q in NOISE_PROBE_QUERIES]
        ds = [embedder(d) for d in NOISE_PROBE_DOCS]
    except Exception as exc:
        # D5 — DEGRADES LOUD, and fails CLOSED. Deliberately broad for the reason every other
        # embedder handler in this module is: an embedder is ANY caller-supplied callable, so its
        # raisables span a hosted provider's transport errors, an optional extra's own tree, and
        # plain TypeError from a callable that does not take a string. There is exactly one honest
        # response to all of them.
        #
        # The FALLBACK is right — treat an embedder we could not characterize as maximally noisy,
        # so the ranking principle is not waived for a signal we failed to measure — but silence
        # would be the bug: the user's semantic tier has just been collapsed to its budget share
        # and would rank on almost nothing, with no way to tell that from "the tier is quiet".
        from ..degrade import note_degraded
        note_degraded("memory-embedder-noise", FAILURE_ENGINE,
                      fallback="the embedder could not be characterized — the semantic tier is "
                               "weighted as if it were maximally noisy",
                      fix="declare `noise_floor` on the embedder, or run `mokata doctor`",
                      detail=f"{type(exc).__name__}: {exc}", out=degrade_out)
        return UNCHARACTERIZED_NOISE_FLOOR
    worst = 0.0
    for qv in qs:
        for dv in ds:
            worst = max(worst, cosine(qv, dv))
    return float(worst)


def noise_floor_of(embedder: Optional[Embedder]) -> float:
    """The noise floor the fusion caps the semantic tier against. DECLARED, else PROBED, else 1.0.

    Three tiers of knowledge, and the order is the point:

      1. **DECLARED** (`embedder.noise_floor`) — the embedder characterized itself, ideally against
         a real corpus. `HashingEmbedder` does; a hosted provider that knows its own distribution
         can, and skips the probe's round trips by doing so.
      2. **PROBED** (`measure_noise_floor`) — nobody said, so ask. This is the "capability-PROBED,
         never assumed" posture `_can_nominate` takes with `lexical_search`, applied to a number
         instead of to a method. Cached on the instance: 64 encodes once, never per recall.
      3. **UNCHARACTERIZED** — the probe could not run. Fails closed at 1.0.

    `None` (semantic tier off) answers 0.0: there is no tier to cap, and 0.0 is the value that
    leaves `semantic_weight_for` returning the declared weight unchanged, so the no-embedder
    ranking is arithmetically untouched.
    """
    if embedder is None:
        return 0.0
    declared = getattr(embedder, "noise_floor", None)
    if declared is not None:
        try:
            return max(0.0, min(1.0, float(declared)))
        except (TypeError, ValueError):
            # A non-numeric declaration is a broken embedder, not a licence to ignore the bound.
            return UNCHARACTERIZED_NOISE_FLOOR
    cached = getattr(embedder, _FLOOR_CACHE_ATTR, None)
    if cached is not None:
        return float(cached)
    floor = measure_noise_floor(embedder)
    try:
        setattr(embedder, _FLOOR_CACHE_ATTR, floor)
    except (AttributeError, TypeError):
        # `functools.partial`, a builtin, a slotted object — all legal embedders under the seam's
        # "any callable" contract, and none of them takes an attribute. Re-probing costs 64 encodes
        # per recall for those, which is a cost, not a wrong answer; declaring `noise_floor` is the
        # documented way out.
        pass
    return floor


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
def make_embedder(name: Optional[str], *,
                  degrade_out: Optional[Callable[[str], None]] = None,
                  semantic_store: bool = False) -> Optional[Embedder]:
    """Resolve an embedder by name (from `settings.memory.embedder`).

      * ``"auto"``               -> DB.S4 detection: the blessed extra if usable, else hashing;
      * ``"hashing"``/``"local"``-> the zero-dep default, explicitly;
      * ``"model2vec"``          -> the blessed extra, explicitly (degrades to hashing if absent —
                                    an explicit ask still never breaks a recall);
      * anything else incl. None -> None, so the semantic tier is OFF unless explicitly wired.

    The None default is UNCHANGED from Stage 35e: a repo that has not opted in gets byte-identical
    behaviour, because opting a user into embedding their memory is exactly the kind of thing P2
    says you ask about first.

    EXPLICIT-ASK LEGIBILITY (2026-08-01). An explicit ask that lands on hashing now SAYS SO.
    `detect_embedder` deliberately stays silent when the extra is merely ABSENT — a notice that
    fires on every default install is noise, and that reasoning is correct for ``auto``, which is
    a request to use whatever is best. It is NOT correct for an ask that NAMED the extra: there,
    silence tells a user who wrote ``memory.embedder: model2vec`` that they got what they asked
    for, when they got token-hashing instead.

    What changed the severity is DB.S8f's K2 bound. Before it, an unnoticed fallback to hashing
    meant "semantic recall is worse than you think". After it, `HashingEmbedder`'s 0.65 noise
    floor weights the tier down to **0.077** — so the tier the user explicitly asked for is very
    nearly INERT, and nothing anywhere said so. The degrade was always real; it is now large
    enough that staying quiet about it is misreporting.

    `semantic_store=True` says the caller opted into a semantic STORE (pgvector), which is itself
    a semantic ask even when `memory.embedder` is unset — the ONE path DB.S8f found that reaches a
    live embedder without naming one. Landing on hashing there fills a real vector index with
    token-hash vectors, which is worth exactly as much of a notice as the named ask.
    """
    if name in ("hashing", "local"):
        return HashingEmbedder()
    if name in ("auto", "model2vec", "embeddings"):
        asked_for_the_extra = semantic_store or name in ("model2vec", "embeddings")
        embedder = detect_embedder(degrade_out=degrade_out)
        if asked_for_the_extra and getattr(embedder, "embedder_id", None) == HASHING_ID \
                and not _extra_is_installed():
            # ONLY when the extra is ABSENT. When it is installed but the MODEL failed to load,
            # `detect_embedder` has already said so with the better message (it knows the cause),
            # and saying it twice for one fallback is how a channel gets tuned out.
            from ..degrade import FAILURE_ENGINE, note_degraded
            asked = "a semantic (pgvector) store" if semantic_store else f"`{name}`"
            note_degraded(
                "memory-embedder-ask", FAILURE_ENGINE,
                fallback=f"you asked for {asked}, and semantic recall is TOKEN-HASH (hashing) — "
                         "the `embeddings` extra is not installed. Its noise floor bounds the "
                         "semantic tier to a weak tiebreak (weight 0.077), so it is close to off",
                fix="pip install 'mokata[embeddings]'  (~30MB, numpy-only) — or set "
                    "`memory.embedder: hashing` to say this is deliberate and silence this",
                detail=f"asked={name!r} semantic_store={semantic_store} resolved={HASHING_ID}",
                out=degrade_out)
        return embedder
    return None
