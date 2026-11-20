"""
Embedding index: leakage prevention, index consistency and similarity ranking.

* Manifest / leakage / field-whitelist checks are pure Python (run in .venv).
* Ranking and vector checks need numpy: they run in .venv_ai and are skipped,
  with a reason, in .venv (which has no numpy):
      .venv_ai\\Scripts\\python.exe -m unittest tests.test_problem_embedding_index
* Checks against the real built index (models/embeddings/index_train_v1, not
  in git) are skipped when that directory is absent.
"""

import copy
import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import embedding_model as em
import problem_embedding_index as pei

HAS_NUMPY = importlib.util.find_spec("numpy") is not None
ROOT = Path(__file__).resolve().parents[1]
REAL_INDEX = ROOT / "models" / "embeddings" / "index_train_v1"
SPLITS = json.loads((Path(__file__).parent / "data" / "split_ids.json")
                    .read_text(encoding="utf-8"))["splits"]
TRAIN_IDS = {p for p, s in SPLITS.items() if s == "train"}


def make_manifest(metadata, **overrides):
    manifest = {
        "format": pei.INDEX_FORMAT, "split": "train", "dimensions": 384,
        "count": len(metadata),
        "problem_ids_sha256": pei.ids_digest([m["problem_id"] for m in metadata]),
    }
    manifest.update(overrides)
    return manifest


GOOD_META = [{"problem_id": "P002", "input_format": "N", "value_constraints": "1 <= N"},
             {"problem_id": "P003", "input_format": "R C", "value_constraints": "1 <= R"}]


class SplitFixtureTests(unittest.TestCase):

    def test_split_fixture_matches_the_processed_dataset(self):
        counts = {}
        for split in SPLITS.values():
            counts[split] = counts.get(split, 0) + 1
        self.assertEqual(counts, {"train": 5636, "validation": 716, "test": 709})


class ManifestAndLeakageTests(unittest.TestCase):

    def test_valid_manifest_passes(self):
        ids = pei.validate_manifest(make_manifest(GOOD_META), GOOD_META, SPLITS)
        self.assertEqual(ids, ["P002", "P003"])

    def test_non_training_split_rejected(self):
        for split in ("validation", "test", "all"):
            with self.subTest(split=split), self.assertRaises(pei.IndexIntegrityError):
                pei.validate_manifest(make_manifest(GOOD_META, split=split), GOOD_META)

    def test_validation_or_test_problem_in_index_rejected(self):
        val_id = next(p for p, s in SPLITS.items() if s == "validation")
        test_id = next(p for p, s in SPLITS.items() if s == "test")
        for leaked in (val_id, test_id, "P9999"):   # P9999: no recorded split
            meta = GOOD_META + [{"problem_id": leaked, "input_format": "x",
                                 "value_constraints": "y"}]
            with self.subTest(leaked=leaked), self.assertRaises(pei.IndexIntegrityError):
                pei.validate_manifest(make_manifest(meta), meta, SPLITS)

    def test_forbidden_fields_never_stored(self):
        for field in ("CPP_Solution", "Test_Cases", "Sample_Input", "Sample_Output",
                      "Code_Constraints", "expected_output"):
            meta = copy.deepcopy(GOOD_META)
            meta[0][field] = "secret"
            with self.subTest(field=field), self.assertRaises(pei.IndexIntegrityError):
                pei.validate_manifest(make_manifest(meta), meta)

    def test_unexpected_field_rejected(self):
        meta = copy.deepcopy(GOOD_META)
        meta[0]["problem_statement"] = "x"
        with self.assertRaises(pei.IndexIntegrityError):
            pei.validate_manifest(make_manifest(meta), meta)

    def test_count_dimension_duplicate_and_id_digest_checks(self):
        bad = [
            (make_manifest(GOOD_META, count=3), GOOD_META),
            (make_manifest(GOOD_META, dimensions=100), GOOD_META),
            (make_manifest(GOOD_META, kind="graph"), GOOD_META),
            (make_manifest(GOOD_META, format="other"), GOOD_META),
            (make_manifest(GOOD_META, problem_ids_sha256="0" * 64), GOOD_META),
            (make_manifest(GOOD_META + GOOD_META[:1]), GOOD_META + GOOD_META[:1]),
        ]
        for manifest, meta in bad:
            with self.assertRaises(pei.IndexIntegrityError):
                pei.validate_manifest(manifest, meta)


@unittest.skipUnless(REAL_INDEX.exists(), "built index not present (models/ is not in git)")
class RealIndexConsistencyTests(unittest.TestCase):
    """Pure-Python checks of the actual built index."""

    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((REAL_INDEX / "manifest.json").read_text(encoding="utf-8"))
        cls.metadata = json.loads((REAL_INDEX / "metadata.json").read_text(encoding="utf-8"))

    def test_contains_exactly_the_training_split(self):
        ids = pei.validate_manifest(self.manifest, self.metadata, SPLITS)
        self.assertEqual(set(ids), TRAIN_IDS)
        self.assertEqual(len(ids), 5636)
        self.assertEqual(self.manifest["excluded"],
                         {"validation": 716, "test": 709, "no_recorded_split": 350})

    def test_vectors_file_matches_manifest_hash(self):
        digest = hashlib.sha256((REAL_INDEX / "vectors.npy").read_bytes()).hexdigest()
        self.assertEqual(digest, self.manifest["vectors_sha256"])

    def test_provenance(self):
        self.assertEqual(self.manifest["model"]["repo"], em.MODEL_REPO)
        self.assertEqual(self.manifest["model"]["revision"], em.MODEL_REVISION)
        self.assertEqual(self.manifest["model"]["pooling"], "cls")
        self.assertEqual(self.manifest["original_dataset_sha256"],
                         "b77745760e7ff750ff204c8093532035dca40326b291bc565a8c3e0a0a4f6b09")
        self.assertEqual(self.manifest["token_lengths"]["over_max_length_truncated"], 0)


@unittest.skipUnless(HAS_NUMPY, "numpy not installed in this environment; run with .venv_ai")
class RankingTests(unittest.TestCase):

    def setUp(self):
        import numpy as np
        self.np = np
        rng = np.random.default_rng(7)
        base = rng.normal(size=(6, 384)).astype(np.float32)
        base[5] = base[4] + 0.01 * rng.normal(size=384).astype(np.float32)  # near-duplicate of row 4
        self.vectors = base / np.linalg.norm(base, axis=1, keepdims=True)
        self.meta = [{"problem_id": f"P{100 + i}", "input_format": f"in{i}",
                      "value_constraints": f"c{i}"} for i in range(6)]
        self.index = pei.ProblemIndex(self.vectors, self.meta, make_manifest(self.meta))

    def test_scores_are_cosine_and_sorted(self):
        query = self.vectors[2] * 3.0                       # not normalised on purpose
        results = self.index.search(query, k=4, collapse_threshold=None)
        self.assertEqual(results[0]["problem_id"], "P102")
        self.assertAlmostEqual(results[0]["score"], 1.0, places=5)
        scores = [r["score"] for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True))
        expected = sorted((float(self.vectors[i] @ self.vectors[2]) for i in range(6)), reverse=True)
        for got, want in zip(scores, expected):
            self.assertAlmostEqual(got, want, places=5)
        self.assertEqual([r["rank"] for r in results], [1, 2, 3, 4])

    def test_exclusion_removes_self(self):
        results = self.index.search(self.vectors[2], k=5, exclude_ids={"P102"})
        self.assertNotIn("P102", [r["problem_id"] for r in results])

    def test_near_duplicates_are_collapsed(self):
        results = self.index.search(self.vectors[4], k=5)
        ids = [r["problem_id"] for r in results]
        self.assertIn("P104", ids)
        self.assertNotIn("P105", ids)
        self.assertEqual(results[0]["near_duplicates"], ["P105"])
        raw = self.index.search(self.vectors[4], k=2, collapse_threshold=None)
        self.assertEqual([r["problem_id"] for r in raw], ["P104", "P105"])

    def test_min_score_and_k_bounds(self):
        self.assertEqual(len(self.index.search(self.vectors[0], k=5, min_score=0.99)), 1)
        for bad in (0, 21, 2.0, "5"):
            with self.subTest(k=bad), self.assertRaises(ValueError):
                self.index.search(self.vectors[0], k=bad)

    def test_bad_queries_rejected(self):
        with self.assertRaises(ValueError):
            self.index.search(self.np.zeros(384), k=1)
        with self.assertRaises(ValueError):
            self.index.search(self.np.ones(10), k=1)

    def test_results_expose_only_whitelisted_fields(self):
        result = self.index.search(self.vectors[0], k=1)[0]
        self.assertEqual(set(result), {"rank", "problem_id", "score", "input_format",
                                       "value_constraints", "near_duplicates"})

    def _write_index(self, vectors, meta):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        self.np.save(folder / "vectors.npy", vectors, allow_pickle=False)
        manifest = make_manifest(meta, vectors_sha256=hashlib.sha256(
            (folder / "vectors.npy").read_bytes()).hexdigest())
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (folder / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        return folder

    def test_load_index_round_trip_and_tamper_detection(self):
        folder = self._write_index(self.vectors, self.meta)
        self.assertEqual(len(pei.load_index(folder)), 6)
        tampered = self.vectors.copy()
        tampered[0, 0] += 0.5
        self.np.save(folder / "vectors.npy", tampered, allow_pickle=False)
        with self.assertRaises(pei.IndexIntegrityError):
            pei.load_index(folder)                                   # hash mismatch

    def test_unnormalised_or_misshapen_vectors_rejected(self):
        with self.assertRaises(pei.IndexIntegrityError):
            pei.load_index(self._write_index(self.vectors * 2, self.meta))
        with self.assertRaises(pei.IndexIntegrityError):
            pei.load_index(self._write_index(self.vectors[:, :100].copy(), self.meta))


@unittest.skipUnless(HAS_NUMPY and REAL_INDEX.exists(), "needs numpy and the built index")
class RealIndexRankingTests(unittest.TestCase):

    def test_full_verification_and_self_retrieval(self):
        index = pei.load_index(REAL_INDEX, SPLITS)
        for row in (0, 1000, 5635):
            top = index.search(index.vectors[row], k=1, collapse_threshold=None)[0]
            self.assertEqual(top["problem_id"], index.problem_ids[row])
            self.assertAlmostEqual(top["score"], 1.0, places=4)


if __name__ == "__main__":
    unittest.main()
