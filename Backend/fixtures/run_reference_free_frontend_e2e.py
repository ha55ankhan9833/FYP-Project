"""
E2E driver: the CURRENT FRONTEND max/min wording, reference-free, through the
REAL API on the FYP_E2E_Test backend (127.0.0.1:5000).

    python fixtures/run_reference_free_frontend_e2e.py <step> [--confirm FYP_E2E_Test]

Identical to run_reference_free_e2e.py, except the problem statement, input
format and output format are the exact reviewed frontend texts of the
`max_min_loops_frontend_v1` registry entry (read from
verified_problem_specs.json, not retyped). No reference solution is ever
submitted. Nothing is published. The token is never printed.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_reference_free_e2e as rf  # noqa: E402

base = rf.base
spec = next(s for s in json.loads((base.ROOT / "verified_problem_specs.json").read_text(
    encoding="utf-8"))["specs"] if s["spec_id"] == "max_min_loops_frontend_v1")
base.PROBLEM = spec["problem_statements"][0]
base.INPUT_FORMAT = spec["input_formats"][0]
base.OUTPUT_FORMAT = spec["output_formats"][0]
base.STATE = base.ROOT / "fixtures" / "reference_free_frontend_e2e" / "setup_state.json"
base.__doc__ = __doc__

if __name__ == "__main__":
    base.main()
