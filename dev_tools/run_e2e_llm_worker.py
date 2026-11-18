"""
DEVELOPMENT/DEMO: automatic LLM-analysis worker for FYP_E2E_Test.
Run it in its own terminal next to the E2E backend; stop with Ctrl+C.

    .venv\\Scripts\\python.exe dev_tools\\run_e2e_llm_worker.py
        -> polls about every 10 s, analyses one eligible job at a time
    .venv\\Scripts\\python.exe dev_tools\\run_e2e_llm_worker.py --check
        -> read-only: Ollama/model preflight + which job WOULD be chosen
           (nothing claimed, nothing written)

Guards: refuses unless connected to FYP_E2E_Test and no production sandbox
is configured. Never compiles or executes anything. Submissions whose exact
source matches an approved or reviewed verified-execution fixture are left
PENDING for dev_tools/fixture_submission_grader.py.
"""

import argparse
import json
import logging
import signal
import socket
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
import llm_analysis_grading as lag  # noqa: E402
import llm_analysis_worker as worker  # noqa: E402
from dev_tools import recorded_evidence_grading as reg  # noqa: E402
from dev_tools import trusted_execution_harness as teh  # noqa: E402
from dev_tools.run_e2e_evaluation_worker import (Refused,  # noqa: E402
                                                 require_environment)


def protected_hashes(approvals=None, records=None):
    """Hashes reserved for the verified recorded-evidence path."""
    approvals = teh.load_approvals() if approvals is None else approvals
    records = reg.REVIEWED_RECORDS if records is None else records
    return frozenset(h.lower() for h in approvals.values()) | \
        frozenset(r["fixture_sha256"].lower() for r in records.values())


def default_worker_id():
    return f"{lag.WORKER_PREFIX}-loop-{socket.gethostname()}"[:100]


def main():
    parser = argparse.ArgumentParser(description="Automatic LLM-analysis worker (E2E)")
    parser.add_argument("--check", action="store_true", help="read-only preflight")
    parser.add_argument("--interval", type=int, default=worker.POLL_SECONDS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    db.select_database(db.E2E_TEST_DATABASE)
    try:
        require_environment()
    except Refused as error:
        sys.exit(f"REFUSED: {error}")
    hashes = protected_hashes()

    if args.check:
        health = worker.check_ollama()
        job, skipped = worker.choose_job(worker.load_candidates(), hashes)
        print(json.dumps({"database": db.database_name(), "health": health,
                          "protected_fixture_hashes": len(hashes),
                          "would_process_job": job and {k: job[k] for k in ("job_id", "submission_id")},
                          "skipped_verified_fixture_jobs": [s["job_id"] for s in skipped]},
                         indent=1))
        print("CHECK ONLY - nothing claimed or written")
        return

    if not 5 <= args.interval <= 300:
        sys.exit("REFUSED: --interval must be 5..300 seconds")
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())     # Ctrl+C: finish cycle, exit
    worker_id = default_worker_id()
    logging.info("LLM-analysis worker %s on %s (poll %ss, %d protected fixture hashes)",
                 worker_id, db.database_name(), args.interval, len(hashes))
    cycles = worker.run_loop(worker_id, db.database_name(), hashes, stop_event=stop,
                             poll=args.interval)
    logging.info("worker stopped after %d cycles", cycles)


if __name__ == "__main__":
    main()
