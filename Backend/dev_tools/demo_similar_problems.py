"""
Demo: top-5 most similar TRAINING problems for a new problem (read-only).

    .venv_ai\\Scripts\\python.exe dev_tools\\demo_similar_problems.py [original dataset .json]

The optional dataset path is only used to print a short description snippet
for each match; retrieval itself uses the local index and model only.
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import embedding_model as em  # noqa: E402
import problem_embedding_index as pei  # noqa: E402

INDEX_DIR = ROOT / "models" / "embeddings" / "index_train_v1"

NEW_PROBLEM = {
    "problem": ("Write a C++ program that reads a positive integer N and determines whether "
                "N is a prime number. A prime number is greater than 1 and has no positive "
                "divisors other than 1 and itself."),
    "input_format": "A single line containing one integer N.",
    "output_format": "Print YES if N is prime, otherwise print NO.",
}


def main(dataset_path=None):
    descriptions = {}
    if dataset_path:
        descriptions = {r["Problem_ID"]: r["Problem_Description"]
                        for r in json.loads(Path(dataset_path).read_text(encoding="utf-8"))}

    index = pei.load_index(INDEX_DIR)
    text = em.problem_text(NEW_PROBLEM["problem"], NEW_PROBLEM["input_format"],
                           NEW_PROBLEM["output_format"])
    start = time.perf_counter()
    query = em.encode([text])[0]
    results = index.search(query, k=5)
    elapsed = time.perf_counter() - start

    print("NEW PROBLEM:", NEW_PROBLEM["problem"])
    print(f"query tokens: {em.token_counts([text])[0]} | encode+search: {elapsed:.3f}s "
          f"| corpus: {len(index)} training problems\n")
    for r in results:
        print(f"#{r['rank']}  {r['problem_id']}  cosine={r['score']:.4f}"
              + (f"  (+{len(r['near_duplicates'])} near-duplicate(s) folded)"
                 if r["near_duplicates"] else ""))
        if r["problem_id"] in descriptions:
            print("    description:", descriptions[r["problem_id"]][:150].replace("\n", " "))
        print("    input format:", r["input_format"][:150].replace("\n", " "))
        print("    constraints :", r["value_constraints"][:150].replace("\n", " "))


if __name__ == "__main__":
    main(*sys.argv[1:2])
