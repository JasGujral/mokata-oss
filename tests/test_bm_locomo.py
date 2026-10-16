"""BM (LOCOMO) harness (0.0.23 D14) — the 8 pins, each enforced; pin 8's stop condition is a
REFUSAL TO PUBLISH. Runs on a tiny in-test fixture dataset with fake answerer/judge and mokata's
real memory store. The dataset is never fetched here: `urlopen` is patched to explode.
"""

import json
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (src/ on the path + provisioning refusal)

import _g2_fixture  # noqa: F401  (repo root + src on the path)

from bench.locomo import __main__ as cli
from bench.locomo import pins as P
from bench.locomo import report as R
from bench.locomo.dataset import (DATA_FILE, DATASET_URL, DatasetMissing, fetch, load, read_lock,
                                  sha256_file)
from bench.locomo.harness import run_benchmark
from bench.locomo.llm import FakeCompleter, parse_label
from bench.locomo.retrieval import ARM_C, ARM_FULL, ARMS, ARM_A

FIXTURE = [
    {"sample_id": "conv-a", "conversation": {
        "speaker_a": "Caroline", "speaker_b": "Melanie",
        "session_1_date_time": "1:56 pm on 8 May, 2023",
        "session_1": [
            {"speaker": "Caroline", "dia_id": "D1:1", "text": "I went to the LGBTQ support group yesterday."},
            {"speaker": "Melanie", "dia_id": "D1:2", "text": "I painted a sunrise over the lake last year."}],
        "session_2_date_time": "1:14 pm on 25 May, 2023",
        "session_2": [
            {"speaker": "Caroline", "dia_id": "D2:1", "text": "I am researching adoption agencies now."}]},
     "qa": [{"question": "What did Melanie paint?", "answer": "a sunrise", "evidence": ["D1:2"], "category": 4},
            {"question": "What is Caroline researching?", "answer": "adoption agencies", "evidence": ["D2:1"], "category": 1},
            {"question": "When did Caroline go to the support group?", "answer": "7 May 2023", "evidence": ["D1:1"], "category": 2},
            {"question": "What did Caroline paint?", "adversarial_answer": "a sunrise", "evidence": [], "category": 5}]},
    {"sample_id": "conv-b", "conversation": {
        "speaker_a": "Jon", "speaker_b": "Gina", "session_1_date_time": "9 June, 2023",
        "session_1": [{"speaker": "Jon", "dia_id": "D1:1", "text": "My dance studio opens next month."}]},
     "qa": [{"question": "What is Jon opening?", "answer": "dance studio", "evidence": ["D1:1"], "category": 4}]},
]


def _answer(prompt):
    ctx = prompt.split("## Memories", 1)[1].split("## Question", 1)[0].lower()
    for gold in ("sunrise", "adoption agencies", "dance studio"):
        if gold in ctx and gold.split()[0] in prompt.split("## Question", 1)[1].lower() + gold:
            q = prompt.split("## Question", 1)[1].lower()
            if ("paint" in q and gold == "sunrise") or ("research" in q and gold.startswith("adoption")) \
                    or ("open" in q and gold == "dance studio"):
                return gold
    return "unknown"


def _judge(prompt):
    gold = re.search(r"Gold answer: (.*)", prompt).group(1).strip().lower()
    got = re.search(r"Generated answer: (.*)", prompt).group(1).strip().lower()
    return json.dumps({"label": "CORRECT" if got and got in gold else "WRONG"})


def _no_network(*_a, **_k):
    raise AssertionError("the LOCOMO tests must never touch the network")


class TestPins(unittest.TestCase):
    def test_eight_pins_and_pin_8_is_the_stop(self):
        self.assertEqual([n for n, _t, _w in P.PINS], list(range(1, 9)))
        self.assertIn("STOP CONDITION", P.PINS[7][1])
        for _n, _t, where in P.PINS:
            self.assertTrue(where)                         # every pin names its enforcement

    def test_the_disclosure_state_is_the_backlogs(self):
        self.assertEqual(P.RANKER_DISCLOSURES["FTS-NORMALIZE-FLATTENS"]["state"], "closed")
        self.assertEqual(len(P.open_disclosures()), 2)


class TestDataset(unittest.TestCase):
    def test_load_without_fetch_names_the_explicit_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(DatasetMissing) as cm:
                load(tmp)
            self.assertIn("python -m bench.locomo fetch", str(cm.exception))
            with self.assertRaises(DatasetMissing):
                read_lock(tmp)

    def test_fetch_writes_a_sha_lock_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = json.dumps(FIXTURE).encode("utf-8")
            lock = fetch(tmp, opener=lambda url: body)
            self.assertEqual(lock["url"], DATASET_URL)
            self.assertEqual(lock["sha256"], sha256_file(os.path.join(tmp, DATA_FILE)))
            self.assertEqual(read_lock(tmp)["bytes"], len(body))
            self.assertEqual([c.id for c in load(tmp)], ["conv-a", "conv-b"])

    def test_fetch_refuses_to_lock_non_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                fetch(tmp, opener=lambda url: b"<html>rate limited</html>")
            self.assertFalse(os.path.exists(os.path.join(tmp, DATA_FILE)))

    def test_parse_orders_sessions_and_keeps_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetch(tmp, opener=lambda url: json.dumps(FIXTURE).encode())
            conv = load(tmp)[0]
            self.assertEqual([t.dia_id for t in conv.turns], ["D1:1", "D1:2", "D2:1"])
            self.assertEqual(conv.turns[2].date, "1:14 pm on 25 May, 2023")
            self.assertEqual(conv.qa[3].answer, "a sunrise")     # adversarial_answer kept

    def test_judge_labels_parse_and_garbage_is_ungradable(self):
        self.assertTrue(parse_label('{"label": "CORRECT"}'))
        self.assertFalse(parse_label('ok {"label": "WRONG"}'))
        self.assertIsNone(parse_label("probably right"))


class TestTheAblation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from mokata.memory.embed import HashingEmbedder
        cls.tmp = tempfile.mkdtemp()
        cls.data = os.path.join(cls.tmp, "data")
        fetch(cls.data, opener=lambda url: json.dumps(FIXTURE).encode())
        cls.out = os.path.join(cls.tmp, "run")
        with mock.patch("urllib.request.urlopen", _no_network):
            run_benchmark(conversations=load(cls.data), out_dir=cls.out,
                          answerer=FakeCompleter(_answer), judge=FakeCompleter(_judge),
                          dataset_lock=read_lock(cls.data), repeats=5,
                          embedder=HashingEmbedder(), k=2)
        cls.arch = R.LocomoArchive(cls.out)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_layout_is_arm_conversation_runN_with_raw_beside_verdict(self):
        for arm in ARMS:
            for d in self.arch.run_dirs(arm):
                for f in ("answers.jsonl", "verdicts.jsonl", "gate_ledger.jsonl", "meta.json"):
                    self.assertTrue(os.path.exists(os.path.join(d, f)), (d, f))
        with open(os.path.join(self.arch.run_dirs(ARM_A)[0], "verdicts.jsonl"), encoding="utf-8") as fh:
            self.assertIn("judge_raw", json.loads(fh.readline()))

    def test_every_write_was_a_ledgered_benchmark_auto_approval(self):
        with open(os.path.join(self.arch.run_dirs(ARM_A)[0], "gate_ledger.jsonl"), encoding="utf-8") as fh:
            rows = [json.loads(x) for x in fh]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["posture"] == "benchmark-auto-approve" and r["committed"] for r in rows))
        self.assertFalse(self.arch.manifest["llm_fact_extraction"])

    def test_adversarial_category_is_excluded(self):
        with open(os.path.join(self.arch.run_dirs(ARM_A)[0], "answers.jsonl"), encoding="utf-8") as fh:
            cats = {json.loads(x)["category"] for x in fh}
        self.assertNotIn(5, cats)

    def test_tokens_per_retrieval_is_first_class(self):
        full = R.arm_stats(self.arch, ARM_FULL)["median_tokens_per_retrieval"]
        lexical = R.arm_stats(self.arch, ARM_A)["median_tokens_per_retrieval"]
        self.assertGreater(full, lexical)
        self.assertIn("median tokens / retrieval", R.render_report(self.arch))

    def test_report_title_declares_embedder_posture_and_no_extraction(self):
        text = R.render_report(self.arch)
        first = text.splitlines()[0]
        for must in ("hashing-v1", "benchmark auto-approve (ledgered)", "no LLM fact extraction"):
            self.assertIn(must, first)
        self.assertIn("FIXTURE RUN", text)
        self.assertIn("declared before the run: arm `D:+expansion`", text)

    def test_publish_refuses_the_fixture_with_every_reason(self):
        with self.assertRaises(R.PublishRefused) as cm:
            R.publish(self.arch, self.data)
        text = "\n".join(cm.exception.reasons)
        self.assertIn("pin 4: not a real run", text)
        self.assertIn("pin 1:", text)                               # hashing arms
        self.assertIn("ARM-D-CONFOUND-BACK", text)                  # pin 8a
        self.assertIn("field reference number is not verified", text)

    # --- the stop condition, on a SHAPE-REAL copy with every other pin satisfied ----------
    def _shape_real(self, name):
        dst = os.path.join(self.tmp, name)
        shutil.copytree(self.out, dst)
        mp = os.path.join(dst, "run.json")
        with open(mp, encoding="utf-8") as fh:
            m = json.load(fh)
        m["answerer"]["id"] = m["judge"]["id"] = "claude-cli"
        m["embedder"] = "model2vec:minishlab/potion-base-8M"
        m["kind"] = "real"
        with open(mp, "w", encoding="utf-8") as fh:
            json.dump(m, fh)
        return R.LocomoArchive(dst)

    def _all_other_pins_hold(self):
        closed = {k: dict(v, state="closed") for k, v in P.RANKER_DISCLOSURES.items()}
        return (mock.patch.dict(P.RANKER_DISCLOSURES, closed),
                mock.patch.dict(R.FIELD_REFERENCE, {"verified": True}))

    def test_pin_8_stop_refuses_a_below_field_headline(self):
        arch = self._shape_real("stop")
        a, b = self._all_other_pins_hold()
        with a, b:
            with self.assertRaises(R.PublishRefused) as cm:
                R.publish(arch, self.data)
        self.assertEqual(len(cm.exception.reasons), 1, cm.exception.reasons)
        self.assertIn("pin 8 STOP", cm.exception.reasons[0])
        self.assertIn("never reframed", cm.exception.reasons[0])

    def test_as_is_publication_changes_nothing_but_the_gap_line(self):
        arch = self._shape_real("asis")
        a, b = self._all_other_pins_hold()
        with a, b:
            published = R.publish(arch, self.data, acknowledge_below_field=True)
            draft = R.render_report(arch)
        self.assertIn("Published as-is.", published)
        self.assertEqual(published.splitlines()[:3], draft.splitlines()[:3])   # title + headline
        self.assertEqual([x for x in published.splitlines() if "Published as-is" not in x
                          and x.strip()], [x for x in draft.splitlines() if x.strip()])

    def test_open_ranker_disclosure_alone_blocks_publication(self):
        arch = self._shape_real("ranker")
        with mock.patch.dict(R.FIELD_REFERENCE, {"verified": True, "value": 0.0}):
            with self.assertRaises(R.PublishRefused) as cm:
                R.publish(arch, self.data)
        self.assertTrue(all(r.startswith("pin 8: ranker") for r in cm.exception.reasons))

    def test_pin_7_dataset_drift_and_pin_6_missing_arm(self):
        arch = self._shape_real("drift")
        with open(os.path.join(arch.path, "run.json"), encoding="utf-8") as fh:
            m = json.load(fh)
        m["arms"] = [x for x in m["arms"] if x != ARM_FULL]
        with open(os.path.join(arch.path, "run.json"), "w", encoding="utf-8") as fh:
            json.dump(m, fh)
        other = os.path.join(self.tmp, "otherdata")
        fetch(other, opener=lambda url: json.dumps(FIXTURE[:1]).encode())
        with self.assertRaises(R.PublishRefused) as cm:
            R.publish(R.LocomoArchive(arch.path), other)
        text = "\n".join(cm.exception.reasons)
        self.assertIn("pin 7: dataset on disk does not match", text)
        self.assertIn("pin 6: arms missing", text)

    def test_cli_publish_refuses_and_run_needs_yes(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(cli.main(["publish", "--run", self.out, "--data", self.data]), 2)
            self.assertEqual(cli.main(["run", "--data", self.data, "--out",
                                       os.path.join(self.tmp, "x")]), 2)
        self.assertIn("REFUSING TO PUBLISH", err.getvalue())
        self.assertIn("REFUSING without --yes", err.getvalue())

    def test_embedder_arm_without_an_embedder_is_refused(self):
        from bench.locomo.retrieval import MokataRetriever
        with self.assertRaises(ValueError):
            MokataRetriever(ARM_C, self.tmp, None)


if __name__ == "__main__":
    unittest.main()
