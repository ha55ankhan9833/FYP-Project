"""
Automatic LLM-analysis worker (W-A1..W-A4). Fakes only: no database, no
Ollama, no compiler, no real sleeping.
"""

import inspect
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests import fakes  # noqa: F401
from tests.test_llm_analysis_grading import Recorder

import grading_job_claim_service as claims
import llm_analysis_worker as w

OK = {"ollama_available": True, "model_available": True, "model": w.MODEL_NAME, "error": None}
PROTECTED = "f" * 64


def job(job_id, sha="a" * 64, submission_id=None):
    return {"job_id": job_id, "submission_id": submission_id or job_id + 100,
            "source_sha256": sha}


class Claims:
    def __init__(self, lose=()):
        self.calls, self.lose = [], set(lose)

    def __call__(self, job_id, worker_id, lease_seconds, max_attempts):
        self.calls.append((job_id, worker_id, lease_seconds, max_attempts))
        if job_id in self.lose:
            return None
        return {"job_id": job_id, "submission_id": job_id + 100, "attempt_count": 1,
                "claim_token": "11111111-2222-3333-4444-555555555555"}


class CycleTests(unittest.TestCase):

    def cycle(self, candidates, health=OK, rec=None, claims_fn=None, protected=frozenset()):
        rec = rec or Recorder()
        claims_fn = claims_fn or Claims()
        result = w.run_cycle("llm-analysis-test", protected, deps=rec.deps(),
                             health=lambda: health, candidates=lambda: candidates,
                             claim_by_id=claims_fn)
        return result, rec, claims_fn

    def test_1_normal_pending_job_is_claimed_and_analysed(self):
        result, rec, c = self.cycle([job(6)])
        self.assertEqual((result["outcome"], result["claimed"]), ("ANALYSED", True))
        self.assertEqual(c.calls, [(6, "llm-analysis-test", 600, 3)])
        self.assertEqual(rec.calls[0][0], "complete")
        self.assertEqual(rec.calls[0][2]["auto_evaluation_status"], "AI_ANALYSIS_UNVERIFIED")

    def test_2_no_pending_jobs(self):
        result, rec, c = self.cycle([])
        self.assertEqual(result["outcome"], "NO_ELIGIBLE_JOB")
        self.assertEqual((c.calls, rec.calls), ([], []))

    def test_3_ollama_unavailable_claims_nothing(self):
        def down(url, timeout):
            raise ConnectionError("refused")
        health = w.check_ollama(get=down)
        self.assertEqual((health["ollama_available"], health["model_available"]), (False, False))
        result, rec, c = self.cycle([job(6)], health=health)
        self.assertEqual(result["outcome"], "MODEL_UNAVAILABLE")
        self.assertEqual((c.calls, rec.calls), ([], []))          # no attempt consumed

    def test_4_model_missing_claims_nothing(self):
        class Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"models": [{"name": "llama3:8b"}]}
        seen = {}

        def get(url, timeout):
            seen["timeout"] = timeout
            return Resp()
        health = w.check_ollama(get=get)
        self.assertEqual((health["ollama_available"], health["model_available"],
                          health["error"]), (True, False, "MODEL_NOT_INSTALLED"))
        self.assertEqual(seen["timeout"], w.HEALTH_TIMEOUT_SECONDS)   # bounded
        self.assertTrue(w.OLLAMA_TAGS_URL.endswith("/api/tags"))      # no model load
        result, _, c = self.cycle([job(6)], health=health)
        self.assertEqual((result["outcome"], c.calls), ("MODEL_UNAVAILABLE", []))

    def test_5_verified_fixture_job_is_skipped_not_claimed(self):
        result, rec, c = self.cycle([job(6, PROTECTED)], protected={PROTECTED})
        self.assertEqual(result["outcome"], "NO_ELIGIBLE_JOB")
        self.assertEqual(result["skipped_verified_fixture_jobs"], [6])
        self.assertEqual((c.calls, rec.calls), ([], []))

    def test_6_mixed_queue_skips_fixture_and_processes_next(self):
        result, _, c = self.cycle([job(6, PROTECTED), job(7)], protected={PROTECTED})
        self.assertEqual(result["outcome"], "ANALYSED")
        self.assertEqual([call[0] for call in c.calls], [7])
        self.assertEqual(result["skipped_verified_fixture_jobs"], [6])

    def test_7_duplicate_worker_loses_race_and_writes_nothing(self):
        result, rec, c = self.cycle([job(6)], claims_fn=Claims(lose={6}))
        self.assertEqual(result["outcome"], "CLAIM_LOST")
        self.assertEqual(rec.calls, [])
        # The by-id claim keeps the atomic eligibility rules of the queue claim.
        sql = " ".join(claims.CLAIM_BY_ID_SQL.split())
        self.assertIn("WHERE job_id = ? AND attempt_count < ?", sql)
        self.assertIn("job_status = 'PENDING'", sql)
        self.assertIn("UPDLOCK, READPAST", claims.CLAIM_SQL)
        self.assertIn("NOT EXISTS (SELECT 1 FROM dbo.Evaluation", " ".join(w.CANDIDATES_SQL.split()))

    def test_8_failure_releases_job_and_loop_backs_off(self):
        from rubric_ai_grader import RubricGradingUnavailable
        rec = Recorder(grader=RubricGradingUnavailable("ollama timeout"))
        result, rec, _ = self.cycle([job(6)], rec=rec)
        self.assertEqual((result["outcome"], result["error_code"]),
                         ("INFRASTRUCTURE_FAILURE", "AI_GRADING_UNAVAILABLE"))
        self.assertEqual([c[0] for c in rec.calls], ["release"])
        release = rec.calls[0][1]
        self.assertEqual((release[3], release[5]), ("AI_GRADING_UNAVAILABLE", 3))
        delays, backoff = [], 0
        for _ in range(6):
            delay, backoff = w.next_delay("MODEL_UNAVAILABLE", backoff, poll=10)
            delays.append(delay)
        self.assertEqual(delays, [10, 20, 40, 80, 120, 120])
        self.assertEqual(w.next_delay("NO_ELIGIBLE_JOB", 120, poll=10), (10, 0))
        self.assertEqual(w.next_delay("ANALYSED", 0, poll=10), (0, 0))

    def test_8b_loop_survives_cycle_errors(self):
        outcomes = iter([RuntimeError("db down"), {"outcome": "NO_ELIGIBLE_JOB"}])

        def cycle(worker_id, hashes):
            value = next(outcomes)
            if isinstance(value, Exception):
                raise value
            return value
        slept = []
        with tempfile.TemporaryDirectory() as tmp:
            n = w.run_loop("wid", "FYP_E2E_Test", cycle=cycle, poll=10, max_cycles=2,
                           heartbeat_path=Path(tmp) / "hb.json", sleep=slept.append)
            beat = json.loads((Path(tmp) / "hb.json").read_text(encoding="utf-8"))
        self.assertEqual((n, slept), (2, [10, 10]))
        self.assertEqual(beat["last_outcome"], "NO_ELIGIBLE_JOB")

    def test_9_graceful_shutdown(self):
        stop = threading.Event()
        calls = []

        def cycle(worker_id, hashes):
            calls.append(1)
            stop.set()                      # Ctrl+C arrives during the cycle
            return {"outcome": "ANALYSED"}
        n = w.run_loop("wid", "FYP_E2E_Test", stop_event=stop, cycle=cycle,
                       heartbeat_path=None)
        self.assertEqual((n, len(calls)), (1, 1))      # finished the cycle, then exited
        stop2 = threading.Event()
        stop2.set()
        self.assertEqual(w.run_loop("wid", "db", stop_event=stop2, cycle=cycle,
                                    heartbeat_path=None), 0)

    def test_11_never_executes_code(self):
        rec = Recorder(sandbox=object())
        result, rec, c = self.cycle([job(6)], rec=rec)
        self.assertEqual(result["outcome"], "EXECUTION_AVAILABLE")
        self.assertEqual((c.calls, rec.calls), ([], []))
        for module in (w, __import__("llm_analysis_grading")):
            source = inspect.getsource(module)
            for forbidden in ("subprocess", "compile_cpp", "run_tests(", "os.system"):
                self.assertNotIn(forbidden, source)


class StatusTests(unittest.TestCase):

    def test_snapshot_reports_counts_only_and_honest_heartbeat(self):
        class Cur:
            def __init__(self):
                self.rows = iter([(2, 1, 3), (None,)])

            def execute(self, sql, *p):
                pass

            def fetchone(self):
                return next(self.rows)

        class Conn:
            def cursor(self):
                return Cur()

            def rollback(self):
                pass

            def close(self):
                pass
        snap = w.status_snapshot("FYP_E2E_Test", connection_factory=Conn,
                                 health=lambda: dict(OK), heartbeat=lambda db: {"status": "NOT_OBSERVED"})
        self.assertEqual(snap["queue"], {"pending": 2, "processing": 1, "failed": 3})
        self.assertEqual((snap["ollama_available"], snap["model_available"]), (True, True))
        self.assertNotIn("error", snap)
        self.assertEqual(snap["worker"]["status"], "NOT_OBSERVED")
        text = json.dumps(snap)
        for private in ("student", "source", "submission_id"):
            self.assertNotIn(private, text)

    def test_heartbeat_states(self):
        now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hb.json"
            self.assertEqual(w.read_heartbeat("FYP_E2E_Test", path, now)["status"], "NOT_OBSERVED")
            w.write_heartbeat(path, "FYP_E2E_Test", "wid", {"outcome": "NO_ELIGIBLE_JOB"},
                              clock=lambda: now - timedelta(seconds=15))
            beat = w.read_heartbeat("FYP_E2E_Test", path, now)
            self.assertEqual((beat["status"], beat["seconds_since_heartbeat"]),
                             ("HEARTBEAT_RECENT", 15))
            self.assertEqual(w.read_heartbeat("FYP_Database", path, now)["status"], "NOT_OBSERVED")
            late = now + timedelta(seconds=w.HEARTBEAT_STALE_SECONDS + 60)
            self.assertEqual(w.read_heartbeat("FYP_E2E_Test", path, late)["status"],
                             "NO_RECENT_HEARTBEAT")

    def test_health_is_cached_for_the_endpoint(self):
        probes = []
        clock = iter([100.0, 105.0, 120.0])
        w._HEALTH_CACHE.update(at=0.0, value=None)
        check = lambda: probes.append(1) or dict(OK)  # noqa: E731
        for _ in range(3):
            w.cached_health(ttl=10, check=check, clock=lambda: next(clock))
        self.assertEqual(len(probes), 2)
        w._HEALTH_CACHE.update(at=0.0, value=None)

    def test_10_status_endpoint_is_teacher_only(self):
        from tests.test_api_routes import auth, flask_app
        client = flask_app.test_client()
        snap = {"mode": "LLM_ANALYSIS", "ollama_available": True, "model_available": True,
                "model": w.MODEL_NAME, "queue": {"pending": 0, "processing": 0, "failed": 0},
                "worker": {"status": "NOT_OBSERVED"}}
        with patch.object(w, "status_snapshot", return_value=snap) as m:
            self.assertEqual(client.get("/api/grading/status",
                                        headers=auth("student", "S1")).status_code, 403)
            m.assert_not_called()
            r = client.get("/api/grading/status", headers=auth("teacher"))
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["llm_analysis"], snap)
        self.assertFalse(body["student_execution_enabled"])
        self.assertEqual(client.get("/api/grading/status").status_code, 401)

    def test_status_failure_does_not_break_endpoint(self):
        from tests.test_api_routes import auth, flask_app
        with patch.object(w, "status_snapshot", side_effect=RuntimeError("x")):
            r = flask_app.test_client().get("/api/grading/status", headers=auth("teacher"))
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.get_json()["llm_analysis"])


class ScopeTests(unittest.TestCase):

    def test_assessment_scope_is_passed_to_the_candidate_query(self):
        seen = {}

        class Cur:
            def execute(self, sql, params):
                seen["sql"], seen["params"] = " ".join(sql.split()), params

            def fetchall(self):
                return [(5, 9, "src")]

        class Conn:
            def cursor(self):
                return Cur()

            def rollback(self):
                pass

            def close(self):
                pass
        rows = w.load_candidates(connection_factory=Conn, assessment_id=6)
        self.assertEqual(seen["params"], (3, 6, 6))
        self.assertIn("AND (? IS NULL OR x.assessment_id = ?)", seen["sql"])
        self.assertEqual(rows, [{"job_id": 5, "submission_id": 9,
                                 "source_sha256": w.source_sha256("src")}])
        w.load_candidates(connection_factory=Conn)
        self.assertEqual(seen["params"], (3, None, None))     # unscoped by default


class RunnerTests(unittest.TestCase):

    def test_12_protected_hashes_cover_existing_verified_path(self):
        from dev_tools import recorded_evidence_grading as reg
        from dev_tools import run_e2e_llm_worker as runner
        hashes = runner.protected_hashes()
        for record in reg.REVIEWED_RECORDS.values():           # A5 fixtures stay verified
            self.assertIn(record["fixture_sha256"], hashes)
        extra = runner.protected_hashes(approvals={"k": "ABC"}, records={})
        self.assertEqual(extra, frozenset({"abc"}))
        # Gate V approved the A6 int-accumulator fixture: it is now protected.
        self.assertIn("ac95167f7e4acc460199f96df3325ba85501d2efdd585fb380cd4f1848e0d6f4", hashes)
        # Unapproved, unrecorded sources (e.g. the worker-demo solution) are not.
        self.assertNotIn("e5dd6317588d8d565e4f0daa7deaaad27fe80a5c9a8b7acb6d0e81549c4fcb4b", hashes)


if __name__ == "__main__":
    unittest.main()
