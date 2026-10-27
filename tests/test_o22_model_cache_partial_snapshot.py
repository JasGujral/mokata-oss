"""O22 (the 0.0.26 cut, install-e2e all 9 legs) — a model model2vec fetched is FOUND offline.

CI, every OS and installer: setup reported "✓ semantic memory model active" and then
"✗ meaning search index — model2vec model … could not be found on this machine (loaded offline):
IncompleteSnapshotError … 6 file(s) are missing". huggingface_hub ≥ 1.x refuses
`snapshot_download(local_files_only=True)` unless EVERY file of the repo is cached, and model2vec's
own fetch downloads only the files it reads. The offline question is now "are the files model2vec
reads in the cache?" (`embed.cached_model_dir`), asked of both loaders.
"""

import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import _support  # noqa: F401

from mokata.ide import search as S
from mokata.memory import embed as E


class IncompleteSnapshotError(Exception):
    pass


def _fake_hub(cache_dir, present):
    """A huggingface_hub whose snapshot check is the strict ≥1.x one and whose per-file cache
    lookup answers for exactly the `present` files."""
    hub = types.ModuleType("huggingface_hub")
    calls = {"snapshot": []}

    def try_to_load_from_cache(repo_id, filename, **_kw):
        return os.path.join(cache_dir, filename) if filename in present else None

    def snapshot_download(repo_id, local_files_only=False, **_kw):
        calls["snapshot"].append(local_files_only)
        if local_files_only:
            raise IncompleteSnapshotError("The cached snapshot … is incomplete: 6 file(s) are missing")
        return cache_dir

    hub.try_to_load_from_cache = try_to_load_from_cache
    hub.snapshot_download = snapshot_download
    return hub, calls


class AModelFetchedByModel2vecIsFoundOffline(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for name in E.MODEL_FILES:
            with open(os.path.join(self.dir, name), "w", encoding="utf-8") as fh:
                fh.write("x")

    def test_the_ide_loader_resolves_the_cached_files_with_no_network(self):
        hub, calls = _fake_hub(self.dir, set(E.MODEL_FILES))
        with mock.patch.dict(sys.modules, {"huggingface_hub": hub}):
            self.assertEqual(self.dir, S._model_dir(allow_network=False))
        self.assertEqual([], calls["snapshot"], "no snapshot call — offline or online")

    def test_control_the_strict_snapshot_question_is_what_failed(self):
        hub, _ = _fake_hub(self.dir, set(E.MODEL_FILES))
        with self.assertRaises(IncompleteSnapshotError):
            hub.snapshot_download("m", local_files_only=True)

    def test_a_missing_model_file_is_not_found_offline_and_says_so(self):
        hub, _ = _fake_hub(self.dir, {"config.json", "tokenizer.json"})
        with mock.patch.dict(sys.modules, {"huggingface_hub": hub}):
            self.assertIsNone(E.cached_model_dir("m"))
            with self.assertRaises(E.ModelUnavailable) as cm:
                S._model_dir(allow_network=False)
        self.assertIn("not in the Hugging Face cache", str(cm.exception))

    def test_a_missing_model_is_fetched_when_the_user_allowed_it(self):
        hub, calls = _fake_hub(self.dir, set())
        with mock.patch.dict(sys.modules, {"huggingface_hub": hub}):
            self.assertEqual(self.dir, S._model_dir(allow_network=True))
        self.assertEqual([False], calls["snapshot"])

    def test_memory_loads_the_cached_model_from_disk(self):
        hub, calls = _fake_hub(self.dir, set(E.MODEL_FILES))
        seen = []
        m2v = types.ModuleType("model2vec")

        class StaticModel:
            @staticmethod
            def from_pretrained(path):
                seen.append(path)
                return "model"
        m2v.StaticModel = StaticModel
        with mock.patch.dict(sys.modules, {"huggingface_hub": hub, "model2vec": m2v}):
            E._load_model2vec(E.MODEL2VEC_MODEL)
        self.assertEqual([self.dir], seen, "loaded from the cached directory, not by repo id")


if __name__ == "__main__":
    unittest.main()
