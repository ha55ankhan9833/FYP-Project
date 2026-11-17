"""
RAG retrievers (need numpy; run with .venv_ai, skipped in .venv):
    .venv_ai\\Scripts\\python.exe -m unittest tests.test_rag_retrievers
Real-index checks are skipped when models/embeddings is absent (not in git);
real-model checks also need torch + transformers.
"""

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

HAS_NUMPY = importlib.util.find_spec("numpy") is not None
HAS_TORCH = HAS_NUMPY and importlib.util.find_spec("torch") is not None \
    and importlib.util.find_spec("transformers") is not None
HAS_TOKENIZERS = importlib.util.find_spec("tokenizers") is not None

ROOT = Path(__file__).resolve().parents[1]
EMB = ROOT / "models" / "embeddings"
REAL = all((EMB / d / "manifest.json").exists()
           for d in ("index_train_v1", "index_flan_train_v1", "tfidf_train_v1"))
SPLITS = json.loads((Path(__file__).parent / "data" / "split_ids.json")
                    .read_text(encoding="utf-8"))["splits"]
TRAIN = {p for p, s in SPLITS.items() if s == "train"}
VALIDATION = sorted((p for p, s in SPLITS.items() if s == "validation"), key=lambda p: int(p[1:]))


def words(text):
    return len(text.split()) + 1


@unittest.skipUnless(HAS_NUMPY, "numpy not installed in this environment; run with .venv_ai")
class TfidfAndProviderTests(unittest.TestCase):

    def setUp(self):
        import rag_retrievers as rr
        self.rr = rr
        self.ids = ["P002", "P003", "P004"]
        self.texts = ["prime number check divisors", "sum of array elements", "prime factor sum"]
        self.meta = [{"problem_id": p, "input_format": f"in {p}", "value_constraints": f"1 <= N <= {i + 5}"}
                     for i, p in enumerate(self.ids)]

    def test_tfidf_ranking_and_round_trip(self):
        index = self.rr.TfidfIndex.build(self.ids, self.texts)
        ranked = index.rank("is the number prime", 3)
        self.assertEqual(ranked[0][0], "P002")
        self.assertGreater(ranked[0][1], ranked[-1][1])
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        index.save(folder / "t", self.meta, {})
        loaded, _meta, manifest = self.rr.TfidfIndex.load(folder / "t")
        self.assertEqual(loaded.rank("is the number prime", 3), ranked)
        self.assertEqual(manifest["kind"], "tfidf")
        with open(folder / "t" / "tfidf.npz", "ab") as fh:     # tamper
            fh.write(b"x")
        import problem_embedding_index as pei
        with self.assertRaises(pei.IndexIntegrityError):
            self.rr.TfidfIndex.load(folder / "t")

    def test_tfidf_load_refuses_leaked_ids(self):
        import problem_embedding_index as pei
        val_id = VALIDATION[0]
        index = self.rr.TfidfIndex.build(["P002", val_id], ["a b", "c d"])
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        meta = [{"problem_id": p, "input_format": "x", "value_constraints": "y"} for p in ["P002", val_id]]
        index.save(folder / "t", meta, {})
        with self.assertRaises(pei.IndexIntegrityError):
            self.rr.TfidfIndex.load(folder / "t", SPLITS)

    def test_hybrid_and_provider(self):
        rr = self.rr

        class Fixed:
            def __init__(self, name, order):
                self.name, self.order = name, order

            def rank(self, text, n):
                return [(p, 1.0) for p in self.order[:n]]

        hybrid = rr.HybridRetriever([Fixed("a", ["P003", "P002"]), Fixed("b", ["P003", "P004"])])
        self.assertEqual(hybrid.rank("q", 3)[0][0], "P003")
        provider = rr.RagContextProvider(hybrid, {m["problem_id"]: m for m in self.meta},
                                         set(self.ids), k_max=2)
        sel = provider("Read N.", "One integer N.", "Print N.", words)
        self.assertEqual([e["problem_id"] for e in sel["examples"]][0], "P003")
        self.assertEqual(sel["method"], "hybrid")
        bad = rr.RagContextProvider(Fixed("x", ["P9999"]), {}, set(self.ids))
        from rag_context import ContextError
        with self.assertRaises(ContextError):
            bad("Read N.", "One integer N.", "Print N.", words)


@unittest.skipUnless(HAS_NUMPY and REAL, "needs numpy and the built indexes")
class RealIndexLeakageTests(unittest.TestCase):

    def test_all_three_indexes_hold_exactly_the_training_split(self):
        import problem_embedding_index as pei
        import rag_retrievers as rr
        bge = pei.load_index(rr.BGE_INDEX_DIR, SPLITS)
        flan = pei.load_index(rr.FLAN_INDEX_DIR, SPLITS)
        tfidf, _meta, manifest = rr.TfidfIndex.load(rr.TFIDF_INDEX_DIR, SPLITS)
        for ids in (bge.problem_ids, flan.problem_ids, tfidf.problem_ids):
            self.assertEqual(set(ids), TRAIN)
            self.assertEqual(len(ids), 5636)
        self.assertEqual(flan.vectors.shape, (5636, 512))
        self.assertEqual(bge.manifest["problem_ids_sha256"], manifest["problem_ids_sha256"])

    def test_tfidf_retrieval_for_validation_queries_returns_training_problems_only(self):
        import rag_retrievers as rr
        retriever, _meta, allowed = rr.load_retriever("tfidf", split_of=SPLITS)
        original_ids = set(VALIDATION[:25])
        for pid in VALIDATION[:25]:
            ranked = retriever.rank(f"problem {pid} array sum integers", 10)
            self.assertTrue({p for p, _ in ranked} <= TRAIN)
            self.assertFalse({p for p, _ in ranked} & original_ids)


@unittest.skipUnless(HAS_TORCH and REAL and HAS_TOKENIZERS, "needs torch, transformers and indexes")
class RealModelBudgetTests(unittest.TestCase):
    """Real FLAN-T5 tokenizer + retrievers on short, long and near-limit validation problems."""

    @classmethod
    def setUpClass(cls):
        from unittest.mock import patch
        import ai_constraint_service as svc
        import rag_retrievers as rr
        path = Path(r"D:\Combine dataset to p2237 to p4411\FYP_DATASET_P001_P7411_FINAL_VERIFIED"
                    r"\FYP_DATASET_P001_P7411_FINAL_VERIFIED.json")
        if not path.exists():
            raise unittest.SkipTest("original dataset not available")
        cls.svc, cls.rr = svc, rr
        # The service resolves MODEL_PATH at import time; point it at the local model.
        cls._patch = patch.object(svc, "MODEL_PATH",
                                  str(ROOT / "models" / "flan_t5_constraint_final"))
        cls._patch.start()
        svc.load_model.cache_clear()
        cls.tokenizer, cls.model, _ = svc.load_model()
        cls.original = {r["Problem_ID"]: r for r in json.loads(path.read_text(encoding="utf-8"))}

    @classmethod
    def tearDownClass(cls):
        cls.svc.load_model.cache_clear()
        cls._patch.stop()

    def count(self, text):
        return len(self.tokenizer(text, truncation=False)["input_ids"])

    def test_short_long_and_near_limit_prompts_never_exceed_512(self):
        rr = self.rr
        lengths = sorted(VALIDATION, key=lambda p: self.count(self.svc.build_prompt(
            self.original[p]["Problem_Description"], self.original[p]["Input_Specification"],
            self.original[p]["Output_Specification"])))
        picks = [lengths[0], lengths[len(lengths) // 2], lengths[-1]]      # short, typical, longest
        for mode in ("flan", "tfidf"):
            retriever, meta, allowed = rr.load_retriever(mode, generator=(self.tokenizer, self.model),
                                                         split_of=SPLITS)
            provider = rr.RagContextProvider(retriever, meta, allowed, k_max=5)
            for pid in picks:
                r = self.original[pid]
                args = (r["Problem_Description"], r["Input_Specification"], r["Output_Specification"])
                with self.subTest(mode=mode, problem=pid):
                    sel = provider(*args, self.count)
                    self.assertLessEqual(sel["prompt_tokens"], 512)
                    self.assertEqual(self.count(sel["prompt"]), sel["prompt_tokens"])
                    self.assertTrue(sel["prompt"].endswith(
                        self.svc.build_prompt(*args)[len("Generate value constraints for the "
                                                         "following C++ programming problem.\n"):]))
                    self.assertTrue({e["problem_id"] for e in sel["examples"]} <= TRAIN)
        self.assertGreater(self.count(self.svc.build_prompt(
            self.original[picks[-1]]["Problem_Description"],
            self.original[picks[-1]]["Input_Specification"],
            self.original[picks[-1]]["Output_Specification"])), 440)        # really near the limit


if __name__ == "__main__":
    unittest.main()
