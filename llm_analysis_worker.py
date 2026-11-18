"""
Automatic LLM-analysis worker: one cycle at a time, reusing the existing
grading queue and llm_analysis_grading.analyse_claimed_job.

One cycle:
  1. Refuse if a secure sandbox exists (real execution must grade).
  2. Ollama preflight (GET /api/tags, bounded timeout, does NOT load the
     model): if Ollama or qwen2.5-coder:3b is unavailable, claim nothing,
     so no attempt is consumed and every job stays PENDING.
  3. Mark exhausted expired jobs FAILED (existing retry service).
  4. Read-only look at eligible pending jobs (oldest first). Jobs whose
     exact source hash belongs to a reviewed/approved verified-execution
     fixture are SKIPPED (left PENDING for the recorded-evidence grader);
     the first other job is chosen, so a skipped job never blocks others.
  5. Claim exactly that job by id (atomic UPDATE with the existing
     attempt/lease rules). If another worker won the race, nothing happens.
  6. analyse_claimed_job: on any failure the job is released through the
     existing failure service (PENDING again, FAILED after 3 attempts ->
     teacher manual-grading fallback). No Evaluation, no automatic zero.

Nothing here compiles or executes code. Teacher approval stays mandatory.
"""

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import llm_analysis_grading as lag
from grading_pipeline import MAX_ATTEMPTS, GradingDependencies

logger = logging.getLogger("llm_analysis_worker")

OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
MODEL_NAME = "qwen2.5-coder:3b"
HEALTH_TIMEOUT_SECONDS = 3
POLL_SECONDS = 10
MAX_BACKOFF_SECONDS = 120
CANDIDATE_LIMIT = 50
HEARTBEAT_FILE = Path(__file__).resolve().parent / "dev_tools" / ".runtime" / \
    "llm_worker_heartbeat.json"
HEARTBEAT_STALE_SECONDS = 3 * MAX_BACKOFF_SECONDS

# Read-only: jobs the claim SQL would accept, oldest first (same rules).
CANDIDATES_SQL = f"""
SELECT TOP ({CANDIDATE_LIMIT}) j.job_id, j.submission_id, s.source_code
FROM dbo.GradingJob AS j
INNER JOIN dbo.Submission AS s ON s.submission_id = j.submission_id
WHERE j.attempt_count < ?
  AND (j.job_status = 'PENDING'
       OR (j.job_status = 'PROCESSING' AND j.lease_expires_at < SYSUTCDATETIME()))
  AND NOT EXISTS (SELECT 1 FROM dbo.Evaluation AS e
                  WHERE e.submission_id = j.submission_id)
ORDER BY j.created_at ASC, j.job_id ASC;
"""

QUEUE_COUNTS_SQL = """
SELECT
    SUM(CASE WHEN job_status = 'PENDING' THEN 1 ELSE 0 END),
    SUM(CASE WHEN job_status = 'PROCESSING' THEN 1 ELSE 0 END),
    SUM(CASE WHEN job_status = 'FAILED' THEN 1 ELSE 0 END)
FROM dbo.GradingJob;
"""

LAST_ANALYSIS_SQL = f"""
SELECT MAX(completed_at) FROM dbo.GradingJob
WHERE job_status = 'COMPLETED' AND worker_id LIKE '{lag.WORKER_PREFIX}%';
"""


def source_sha256(source_code):
    return hashlib.sha256((source_code or "").encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------
# Ollama preflight (no model load: /api/tags only lists local models)
# ----------------------------------------------------------------------

def check_ollama(get=requests.get, url=OLLAMA_TAGS_URL, model=MODEL_NAME,
                 timeout=HEALTH_TIMEOUT_SECONDS):
    try:
        response = get(url, timeout=timeout)
        response.raise_for_status()
        names = [m.get("name") for m in response.json().get("models", [])]
    except Exception as error:
        return {"ollama_available": False, "model_available": False,
                "model": model, "error": type(error).__name__}
    available = model in names
    return {"ollama_available": True, "model_available": available,
            "model": model, "error": None if available else "MODEL_NOT_INSTALLED"}


_HEALTH_CACHE = {"at": 0.0, "value": None}
_HEALTH_LOCK = threading.Lock()


def cached_health(ttl=10, check=check_ollama, clock=time.monotonic):
    """For the status endpoint: at most one Ollama probe per ttl seconds."""
    with _HEALTH_LOCK:
        now = clock()
        if _HEALTH_CACHE["value"] is None or now - _HEALTH_CACHE["at"] > ttl:
            _HEALTH_CACHE.update(at=now, value=check())
        return dict(_HEALTH_CACHE["value"])


# ----------------------------------------------------------------------
# Job selection (read-only) and one cycle
# ----------------------------------------------------------------------

def load_candidates(connection_factory=None):
    from db import get_connection
    conn = (connection_factory or get_connection)()
    try:
        cursor = conn.cursor()
        cursor.execute(CANDIDATES_SQL, (MAX_ATTEMPTS,))
        return [{"job_id": int(r[0]), "submission_id": int(r[1]),
                 "source_sha256": source_sha256(r[2])} for r in cursor.fetchall()]
    finally:
        conn.rollback()
        conn.close()


def choose_job(candidates, protected_hashes):
    """First candidate that is NOT a verified-execution fixture."""
    skipped = [c for c in candidates if c["source_sha256"] in protected_hashes]
    for candidate in candidates:
        if candidate["source_sha256"] not in protected_hashes:
            return candidate, skipped
    return None, skipped


def run_cycle(worker_id, protected_hashes=frozenset(), deps=None,
              health=check_ollama, candidates=load_candidates, claim_by_id=None):
    deps = deps or GradingDependencies()
    if deps.get_sandbox() is not None:
        return {"outcome": "EXECUTION_AVAILABLE", "claimed": False}

    status = health()
    if not (status["ollama_available"] and status["model_available"]):
        return {"outcome": "MODEL_UNAVAILABLE", "claimed": False, "health": status}

    deps.mark_exhausted()
    job, skipped = choose_job(candidates(), protected_hashes)
    if job is None:
        return {"outcome": "NO_ELIGIBLE_JOB", "claimed": False,
                "skipped_verified_fixture_jobs": [s["job_id"] for s in skipped]}

    if claim_by_id is None:
        from grading_job_claim_service import claim_grading_job_by_id
        claim_by_id = claim_grading_job_by_id
    claim = claim_by_id(job["job_id"], worker_id, lease_seconds=lag.LEASE_SECONDS,
                        max_attempts=MAX_ATTEMPTS)
    if claim is None or int(claim["submission_id"]) != job["submission_id"]:
        # Another worker claimed it first (or it changed): nothing written.
        return {"outcome": "CLAIM_LOST", "claimed": False, "job_id": job["job_id"]}

    result = lag.analyse_claimed_job(claim, worker_id, deps)
    return {**{k: result.get(k) for k in (
        "job_id", "submission_id", "outcome", "error_code", "job_status",
        "suggested_score", "auto_evaluation_status", "criteria_saved")},
        "claimed": True, "attempt": claim.get("attempt_count"),
        "skipped_verified_fixture_jobs": [s["job_id"] for s in skipped]}


# ----------------------------------------------------------------------
# Loop with backoff, graceful stop and a local heartbeat file
# ----------------------------------------------------------------------

FAILURE_OUTCOMES = {"MODEL_UNAVAILABLE", "INFRASTRUCTURE_FAILURE", "CYCLE_ERROR"}


def next_delay(outcome, current_backoff, poll=POLL_SECONDS):
    """(sleep_seconds, new_backoff). Work found -> poll again immediately."""
    if outcome in FAILURE_OUTCOMES:
        backoff = min(max(current_backoff * 2, poll), MAX_BACKOFF_SECONDS)
        return backoff, backoff
    if outcome in ("ANALYSED", "CLAIM_LOST"):
        return 0, 0
    return poll, 0


def write_heartbeat(path, database, worker_id, last_result, clock=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"worker_id": worker_id, "pid": os.getpid(), "database": database,
               "at_utc": (clock or (lambda: datetime.now(timezone.utc)))().isoformat(),
               "last_outcome": last_result.get("outcome"),
               "last_error_code": last_result.get("error_code")}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def run_loop(worker_id, database, protected_hashes=frozenset(), stop_event=None,
             cycle=run_cycle, poll=POLL_SECONDS, max_cycles=None,
             heartbeat_path=HEARTBEAT_FILE, sleep=None):
    """Runs until stop_event is set (Ctrl+C sets it in the runner)."""
    stop_event = stop_event or threading.Event()

    def sliced_wait(seconds):
        # 1-second slices so Ctrl+C (which sets stop_event) is noticed quickly
        # on Windows, where a long Event.wait is not interrupted by signals.
        for _ in range(int(seconds)):
            if stop_event.wait(1):
                return

    wait = sleep or sliced_wait
    backoff, cycles = 0, 0
    while not stop_event.is_set() and (max_cycles is None or cycles < max_cycles):
        cycles += 1
        try:
            result = cycle(worker_id, protected_hashes)
        except Exception as error:          # DB down etc.: log, back off, continue
            logger.exception("worker cycle failed")
            result = {"outcome": "CYCLE_ERROR", "error_code": type(error).__name__}
        level = logging.WARNING if result["outcome"] in FAILURE_OUTCOMES else logging.INFO
        logger.log(level, "cycle %s: %s", cycles, json.dumps(result, default=str))
        if heartbeat_path:
            try:
                write_heartbeat(heartbeat_path, database, worker_id, result)
            except OSError:
                logger.warning("could not write heartbeat file")
        delay, backoff = next_delay(result["outcome"], backoff, poll)
        if delay:
            wait(delay)
    return cycles


# ----------------------------------------------------------------------
# Teacher status (counts only; no student data)
# ----------------------------------------------------------------------

def read_heartbeat(database, path=HEARTBEAT_FILE, now=None):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        at = datetime.fromisoformat(data["at_utc"])
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "NOT_OBSERVED",
                "note": "No worker heartbeat found on this machine."}
    if data.get("database") != database:
        return {"status": "NOT_OBSERVED",
                "note": "The last heartbeat belongs to another database."}
    age = ((now or datetime.now(timezone.utc)) - at).total_seconds()
    if age > HEARTBEAT_STALE_SECONDS:
        return {"status": "NO_RECENT_HEARTBEAT", "seconds_since_heartbeat": int(age)}
    return {"status": "HEARTBEAT_RECENT", "seconds_since_heartbeat": int(age),
            "last_outcome": data.get("last_outcome")}


def status_snapshot(database, connection_factory=None, health=cached_health,
                    heartbeat=read_heartbeat):
    snapshot = {"mode": "LLM_ANALYSIS (no execution; teacher approval required)",
                **{k: v for k, v in health().items() if k != "error"}}
    try:
        from db import get_connection
        conn = (connection_factory or get_connection)()
        try:
            cursor = conn.cursor()
            cursor.execute(QUEUE_COUNTS_SQL)
            pending, processing, failed = cursor.fetchone()
            cursor.execute(LAST_ANALYSIS_SQL)
            last = cursor.fetchone()[0]
        finally:
            conn.rollback()
            conn.close()
        snapshot["queue"] = {"pending": int(pending or 0), "processing": int(processing or 0),
                             "failed": int(failed or 0)}
        snapshot["last_llm_analysis_completed_at_utc"] = last.isoformat() if last else None
    except Exception:
        snapshot["queue"] = None
    snapshot["worker"] = heartbeat(database)
    return snapshot
