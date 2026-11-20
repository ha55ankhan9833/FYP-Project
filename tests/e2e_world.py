"""
Stateful in-memory stand-in for FYP_Database used by the end-to-end
tests. It answers exactly the SQL statements issued by the real routes and
services in the student -> grading -> teacher -> student flow, enforces
the real unique constraints and ownership predicates, and applies writes
transactionally (undone on rollback or on close without commit).

Every unrecognised statement raises, so a new query cannot silently pass.
"""

import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyodbc

from tests.fakes import BACKEND, FakeConnection

PLAN = json.loads((Path(BACKEND) / "fixtures/new_assessment_csc101/"
                   "migration_to_10_tests.json").read_text())
MISSING = object()


class WorldConnection(FakeConnection):
    def __init__(self, world):
        super().__init__(lambda sql, params: world.handle(self, sql, params))
        self.undo = []

    def commit(self):
        super().commit()
        self.undo = []

    def rollback(self):
        super().rollback()
        for action in reversed(self.undo):
            action()
        self.undo = []

    def close(self):
        if self.undo:                       # uncommitted work is discarded
            self.rollback()
        super().close()


class World:
    def __init__(self):
        self.now = datetime(2026, 11, 18, 12, 0)
        self.teachers = {
            "BIIT235": ("Teacher A", "teacher.a@example.test", "t-pass", "CS"),
            "BIIT999": ("Teacher B", "teacher.b@example.test", "t-pass-b", "CS")}
        self.students = {
            "S-1": ("Student One", "s1@example.test", "s-pass-1"),
            "S-2": ("Student Two", "s2@example.test", "s-pass-2"),
            "S-3": ("Student Three", "s3@example.test", "s-pass-3"),
            "S-OUT": ("Other Section", "out@example.test", "s-pass-out")}
        self.enrollment = {("S-1", "SEC000001", "CSC-101"),
                           ("S-2", "SEC000001", "CSC-101"),
                           ("S-3", "SEC000001", "CSC-101"),
                           ("S-OUT", "SEC000002", "CSC-101")}
        self.sections = {"SEC000001", "SEC000002", "SEC000003"}
        self.sct = {("SEC000001", "BIIT235", "CSC-101"),
                    ("SEC000002", "BIIT235", "CSC-101"),
                    ("SEC000003", "BIIT999", "CSC-101")}
        self.assessment = {
            "assessment_id": 5, "teacher_id": "BIIT235", "course_code": "CSC-101",
            "assessment_type": "Lab", "title": "Prime Number Check",
            "problem": "Print YES if N is prime, otherwise NO.",
            "input_format": "One integer N", "output_format": "YES or NO",
            "total": Decimal("10.00"), "val": Decimal("50.00"),
            "deadline": datetime(2026, 12, 15, 23, 59), "allow_late": False,
            "penalty": Decimal("0.00"), "value_constraints": "1 <= N <= 1000000",
            "additional": None, "code_constraints": None}
        self.rubric = [(1, "Logic & Algorithm", Decimal("3.00"), None),
                       (2, "Code Quality", Decimal("2.00"), None)]
        self.tests = [(p["test_number"], f"Test {p['test_number']}",
                       p["category"], p["input_data"], p["expected_output"])
                      for p in PLAN["proposed_10"]]
        self.asec, self.subs, self.jobs, self.evals = {}, {}, {}, {}
        self.crits = []
        self.approvals = []
        self.approval_table = False
        self._ids = {"asec": 0, "sub": 0, "job": 0, "crit": 0, "approval": 0}
        self.fail_job_insert = False
        self.connections = 0

    # ------------------------------------------------------------ helpers
    def connect(self, *args, **kwargs):
        self.connections += 1
        return WorldConnection(self)

    def _next(self, name):
        self._ids[name] += 1
        return self._ids[name]

    def _put(self, conn, container, key, value):
        old = container.get(key, MISSING)
        container[key] = value

        def undo():
            if old is MISSING:
                container.pop(key, None)
            else:
                container[key] = old
        conn.undo.append(undo)

    def _update_job(self, conn, job_id, **changes):
        self._put(conn, self.jobs, job_id, {**self.jobs[job_id], **changes})

    def _owned(self, job, worker, token):
        return (job is not None and job["status"] == "PROCESSING"
                and job["worker"] == worker
                and str(job["token"]).lower() == str(token).lower()
                and job["lease"] is not None and job["lease"] > self.now)

    def _assessment_of_sub(self, sid):
        sub = self.subs.get(sid)
        return None if sub is None else self.asec[sub["asec"]]

    def _job_of(self, sid):
        return next((j for j in self.jobs.values() if j["submission_id"] == sid),
                    None)

    def _visible(self, student, sec_id):
        a = self.assessment
        return ((student, sec_id, a["course_code"]) in self.enrollment and
                (sec_id, a["teacher_id"], a["course_code"]) in self.sct)

    # ----------------------------------------------------------- dispatch
    def handle(self, conn, sql, p):
        for fragment, handler in self.HANDLERS:
            if fragment in sql:
                return handler(self, conn, p)
        raise AssertionError(f"unexpected SQL: {sql[:160]}")

    # --- authentication
    def teacher_login(self, conn, p):
        return [(tid, *row) for tid, row in self.teachers.items()
                if row[1] == p[0]], -1

    def student_login(self, conn, p):
        return [(sid, *row) for sid, row in self.students.items()
                if row[1] == p[0]], -1

    # --- section assignment
    def owned_assessment(self, conn, p):
        a = self.assessment
        ok = p[0] == a["assessment_id"] and p[1] == a["teacher_id"]
        return ([(a["assessment_id"], a["course_code"], a["deadline"],
                  a["allow_late"], a["total"], a["val"])] if ok else []), -1

    def count_test_cases(self, conn, p):
        return [(len(self.tests) if p[0] == self.assessment["assessment_id"] else 0,)], -1

    def section_exists(self, conn, p):
        return ([(p[0],)] if p[0] in self.sections else []), -1

    def teaches(self, conn, p):
        return [(1 if (p[0], p[1], p[2]) in self.sct else 0,)], -1

    def existing_assignment(self, conn, p):
        return [(i,) for i, (aid, sec) in self.asec.items()
                if aid == p[0] and sec.lower() == p[1].lower()], -1

    def insert_assignment(self, conn, p):
        if any(v == (p[0], p[1]) for v in self.asec.values()):
            raise pyodbc.IntegrityError("23000", "(2627) UQ_AssessmentSection")
        new_id = self._next("asec")
        self._put(conn, self.asec, new_id, (p[0], p[1]))
        return [(new_id,)], 1

    # --- student listing / rubric
    def student_assessments(self, conn, p):
        a = self.assessment
        if any(aid == 5 and self._visible(p[0], sec)
               for aid, sec in self.asec.values()):
            return [(5, a["course_code"], a["assessment_type"], a["title"],
                     a["problem"], a["input_format"], a["output_format"],
                     a["total"], a["deadline"], a["allow_late"], a["penalty"],
                     a["val"])], -1
        return [], -1

    def student_submitted(self, conn, p):
        return [(self.asec[s["asec"]][0],) for s in self.subs.values()
                if s["student"] == p[0]], -1

    def rubric_table_exists(self, conn, p):
        return [(1,)], -1

    def rubric_totals(self, conn, p):
        return [(5, sum(r[2] for r in self.rubric))], -1

    def load_rubric(self, conn, p):
        return (list(self.rubric) if p[0] == 5 else []), -1

    def sysdatetime(self, conn, p):
        return [(self.now,)], -1

    # --- submission transaction
    def lock_student(self, conn, p):
        return ([(p[0],)] if p[0] in self.students else []), -1

    def submission_assessment(self, conn, p):
        a = self.assessment
        if p[0] != 5:
            return [], -1
        return [(5, a["deadline"], a["allow_late"], a["penalty"],
                 a["course_code"], a["total"], a["val"])], -1

    def assignment_for_student(self, conn, p):
        rows = sorted((i, sec) for i, (aid, sec) in self.asec.items()
                      if aid == p[0] and self._visible(p[1], sec)
                      and p[2] == self.assessment["course_code"])
        return rows[:1], -1

    def count_student_submissions(self, conn, p):
        return [(sum(1 for s in self.subs.values() if s["student"] == p[0]
                     and self.asec[s["asec"]][0] == p[1]),)], -1

    def insert_submission(self, conn, p):
        if any(s["student"] == p[0] and s["asec"] == p[1]
               for s in self.subs.values()):
            raise pyodbc.IntegrityError(
                "23000", "(2627) UQ_Submission_Student_AssessmentSection")
        sid = self._next("sub")
        self._put(conn, self.subs, sid, {
            "student": p[0], "asec": p[1], "file": p[2], "source": p[3],
            "uploaded": self.now, "submitted": self.now})
        return [(sid,)], 1

    def insert_job(self, conn, p):
        if self.fail_job_insert:
            raise RuntimeError("GradingJob insert failed")
        if self._job_of(p[0]) is not None:
            raise pyodbc.IntegrityError("23000", "(2627) UQ_GradingJob_Submission")
        jid = self._next("job")
        self._put(conn, self.jobs, jid, {
            "job_id": jid, "submission_id": p[0], "status": "PENDING",
            "attempts": 0, "worker": None, "token": None, "lease": None,
            "created": self.now, "completed": None, "last_error": None})
        return [], 1

    # --- worker
    def claim_by_id(self, conn, p):
        worker, lease, job_id, max_attempts = p
        job = self.jobs.get(job_id)
        if job is None or job["attempts"] >= max_attempts or not (
                job["status"] == "PENDING" or
                (job["status"] == "PROCESSING" and job["lease"] < self.now)):
            return [], 0
        token = str(uuid.uuid4()).upper()
        until = self.now + timedelta(seconds=lease)
        self._update_job(conn, job_id, status="PROCESSING", worker=worker,
                         attempts=job["attempts"] + 1, token=token, lease=until)
        return [(job_id, job["submission_id"], job["attempts"] + 1, token,
                 until)], 1

    def mark_exhausted(self, conn, p):
        return [], 0

    def prepare(self, conn, p):
        job = self.jobs.get(p[0])
        if not self._owned(job, p[1], p[2]):
            return [], -1
        s = self.subs[job["submission_id"]]
        return [(job["job_id"], job["submission_id"], s["source"], s["file"],
                 s["student"], s["asec"])], -1

    def renew(self, conn, p):
        job = self.jobs.get(p[1])
        if not self._owned(job, p[2], p[3]):
            return [], 0
        until = self.now + timedelta(seconds=p[0])
        self._update_job(conn, p[1], lease=until)
        return [(until,)], 1

    def assessment_marks(self, conn, p):
        aid = self._assessment_of_sub(p[0])
        return ([(5, self.assessment["total"])] if aid else []), -1

    def grading_config(self, conn, p):
        a = self.assessment
        return ([(5, a["total"], a["val"], a["penalty"])] if p[0] == 5 else []), -1

    def problem_context(self, conn, p):
        a = self.assessment
        return [(a["problem"], a["input_format"], a["output_format"],
                 a["value_constraints"], a["additional"],
                 a["code_constraints"])], -1

    def grading_tests(self, conn, p):
        return (list(self.tests) if p[0] == 5 else []), -1

    def hidden_tests(self, conn, p):
        return [(t[3], t[4]) for t in self.tests], -1

    def lock_job(self, conn, p):
        job = self.jobs.get(p[0])
        return ([(job["submission_id"],)] if self._owned(job, p[1], p[2])
                else []), -1

    def assessment_limit(self, conn, p):
        return [(self.assessment["total"],)], -1

    def check_evaluation(self, conn, p):
        return ([(p[0],)] if p[0] in self.evals else []), -1

    def _insert_eval(self, conn, sid, **values):
        if sid in self.evals:
            raise pyodbc.IntegrityError("23000", "(2627) PK_Evaluation")
        row = {"suggested": None, "auto_status": None, "feedback": None,
               "final": None, "result_status": None, "instructor": None,
               "passed": 0, "run": 0}
        row.update(values)
        if row["passed"] > row["run"]:
            raise pyodbc.IntegrityError("23000", "CK_Evaluation_Tests")
        self._put(conn, self.evals, sid, row)

    def insert_auto_evaluation(self, conn, p):
        self._insert_eval(conn, p[0], suggested=Decimal(str(p[1])),
                          auto_status=p[2], feedback=p[3], passed=p[4], run=p[5])
        return [], 1

    def insert_failure_evaluation(self, conn, p):
        self._insert_eval(conn, p[0], suggested=Decimal("0"),
                          auto_status="COMPILATION_FAILED", feedback=p[1])
        return [], 1

    def insert_manual_evaluation(self, conn, p):
        self._insert_eval(conn, p[0], auto_status="MANUAL_GRADING",
                          feedback=p[1], final=Decimal(str(p[2])),
                          result_status="FINALIZED", instructor=p[3])
        return [], 1

    def insert_criterion(self, conn, p):
        if p[0] not in self.evals:
            raise pyodbc.IntegrityError("23000", "FK_EvaluationCriterion_Evaluation")
        cid = self._next("crit")
        self.crits.append((cid, p[0], p[1], Decimal(str(p[2])), p[3]))
        conn.undo.append(lambda: self.crits.remove(
            next(c for c in self.crits if c[0] == cid)))
        return [], 1

    def complete_job(self, conn, p):
        job = self.jobs.get(p[0])
        if not self._owned(job, p[1], p[2]):
            return [], 0
        self._update_job(conn, p[0], status="COMPLETED", completed=self.now,
                         lease=None)
        return [], 1

    def release(self, conn, p):
        max_attempts, message, job_id, worker, token = p
        job = self.jobs.get(job_id)
        if job is None or job["status"] != "PROCESSING" or job["worker"] != worker \
                or str(job["token"]).lower() != str(token).lower():
            return [], 0
        status = "FAILED" if job["attempts"] >= max_attempts else "PENDING"
        self._update_job(conn, job_id, status=status, worker=None, token=None,
                         lease=None, last_error=message)
        return [(job_id, job["submission_id"], status, job["attempts"])], 1

    # --- teacher review
    def teacher_owns(self, conn, p):
        a = self.assessment
        return ([(1,)] if (p[0], p[1]) == (5, a["teacher_id"]) else []), -1

    def teacher_list(self, conn, p):
        if (p[0], p[1]) != (5, self.assessment["teacher_id"]):
            return [], -1
        rows = []
        for sid, s in sorted(self.subs.items()):
            j, e = self._job_of(sid), self.evals.get(sid)
            rows.append((sid, s["student"], self.students[s["student"]][0],
                         s["file"], s["uploaded"], s["submitted"],
                         self.assessment["deadline"], j and j["status"],
                         j and j["attempts"], j and j["last_error"],
                         1 if e else 0, e and e["suggested"],
                         e and e["auto_status"], e and e["final"],
                         e and e["result_status"], e and e["passed"],
                         e and e["run"], self.asec[s["asec"]][1]))
        return rows, -1

    def teacher_submission(self, conn, p):
        s = self.subs.get(p[0])
        a = self.assessment
        if s is None or p[1] != a["teacher_id"]:
            return [], -1
        j = self._job_of(p[0])
        return [(p[0], s["student"], s["file"], s["source"], s["uploaded"],
                 s["submitted"], 5, a["title"], a["total"], a["val"],
                 a["deadline"], a["allow_late"], a["penalty"],
                 j and j["status"], j and j["attempts"],
                 j and j["last_error"])], -1

    def evaluation(self, conn, p):
        e = self.evals.get(p[0])
        if e is None:
            return [], -1
        return [(e["suggested"], e["auto_status"], e["feedback"], e["final"],
                 e["result_status"], e["instructor"], e["passed"], e["run"])], -1

    def criteria(self, conn, p):
        return [(c[2], c[3], c[4]) for c in sorted(self.crits)
                if c[1] == p[0]], -1

    def lock_for_finalize(self, conn, p):
        s = self.subs.get(p[0])
        if s is None or p[1] != self.assessment["teacher_id"]:
            return [], -1
        j = self._job_of(p[0])
        return [(self.assessment["total"], j and j["status"], 5,
                 self.assessment["val"])], -1

    def lock_evaluation(self, conn, p):
        e = self.evals.get(p[0])
        return ([(e["result_status"], e["auto_status"], e["passed"], e["run"])]
                if e else []), -1

    # --- manual-grading fallback
    def manual_lock(self, conn, p):
        s = self.subs.get(p[0])
        if s is None or p[1] != self.assessment["teacher_id"]:
            return [], -1
        j = self._job_of(p[0])
        if j is None:
            return [(self.assessment["total"], 5, None, None, None, 0, self.now)], -1
        active = 1 if j["lease"] is not None and j["lease"] > self.now else 0
        return [(self.assessment["total"], 5, j["job_id"], j["status"],
                 j["attempts"], active, self.now)], -1

    def manual_lock_evaluation(self, conn, p):
        e = self.evals.get(p[0])
        return ([(e["result_status"], e["auto_status"])] if e else []), -1

    def manual_close_job(self, conn, p):
        worker, job_id, sid = p
        job = self.jobs.get(job_id)
        if job is None or job["submission_id"] != sid or \
                job["status"] not in ("PENDING", "FAILED"):
            return [], 0
        self._update_job(conn, job_id, status="COMPLETED", completed=self.now,
                         worker=worker, token=None, lease=None)
        return [], 1

    def insert_manual_evaluation(self, conn, p):
        self._insert_eval(conn, p[0], auto_status="MANUALLY_GRADED",
                          feedback=p[1], final=Decimal(str(p[2])),
                          result_status="FINALIZED", instructor=p[3])
        return [], 1

    # --- teacher-approved criterion marks (table exists only after the
    #     approved migration; approval_table toggles it in tests)
    def approval_table_exists(self, conn, p):
        return [(1 if self.approval_table else 0,)], -1

    def approved_criteria(self, conn, p):
        return [(a[2], a[3], a[4], a[5], self.now) for a in sorted(self.approvals)
                if a[1] == p[0]], -1

    def insert_approval(self, conn, p):
        if p[0] not in self.evals:
            raise pyodbc.IntegrityError("23000", "FK_ECApproval_Evaluation")
        if any(a[1] == p[0] and a[2].lower() == p[1].lower()
               for a in self.approvals):
            raise pyodbc.IntegrityError("23000", "(2627) UQ_ECApproval_Criterion")
        if Decimal(str(p[2])) < 0:
            raise pyodbc.IntegrityError("23000", "CK_ECApproval_Marks")
        aid = self._next("approval")
        row = (aid, p[0], p[1], Decimal(str(p[2])), p[3], p[4])
        self.approvals.append(row)
        conn.undo.append(lambda: self.approvals.remove(row))
        return [], 1

    def finalize(self, conn, p):
        e = self.evals.get(p[2])
        if e is None or e["result_status"] == "FINALIZED":
            return [], 0
        self._put(conn, self.evals, p[2], {**e, "final": Decimal(str(p[0])),
                                           "instructor": p[1],
                                           "result_status": "FINALIZED"})
        return [], 1

    # --- student result
    def student_submission(self, conn, p):
        for sid, s in self.subs.items():
            if s["student"] == p[1] and self.asec[s["asec"]][0] == p[0]:
                j, e = self._job_of(sid), self.evals.get(sid)
                return [(sid, s["file"], s["uploaded"], s["submitted"],
                         j and j["status"], 1 if e else 0,
                         e and e["result_status"], e and e["final"],
                         e and e["instructor"], e and e["feedback"],
                         e and e["passed"], e and e["run"],
                         self.assessment["total"], e and e["auto_status"],
                         self.assessment["val"], 5)], -1
        return [], -1

    # --- development fixture grader (read-only)
    def grader_submission(self, conn, p):
        s = self.subs.get(p[0])
        if s is None:
            return [], -1
        aid, sec = self.asec[s["asec"]]
        a, j = self.assessment, self._job_of(p[0])
        return [(p[0], s["student"], s["asec"], s["source"], aid, sec,
                 a["course_code"], a["teacher_id"], j and j["job_id"],
                 j and j["status"], j and j["attempts"],
                 1 if (s["student"], sec, a["course_code"]) in self.enrollment else 0,
                 1 if (sec, a["teacher_id"], a["course_code"]) in self.sct else 0,
                 1 if p[0] in self.evals else 0)], -1

    # Order matters: the more specific fragment first.
    HANDLERS = [
        ("FROM Teacher_Export WHERE teacher_email = ?", teacher_login),
        ("FROM Student_Export WHERE student_email = ?", student_login),
        ("SELECT assessment_id, course_code, deadline_at, allow_late, total_marks, "
         "validation_percentage FROM dbo.Assessment", owned_assessment),
        ("SELECT COUNT(*) FROM dbo.TestCase WITH (HOLDLOCK) WHERE assessment_id = ?",
         count_test_cases),
        ("FROM dbo.Section_Export WHERE section_id = ?", section_exists),
        ("SELECT CASE WHEN EXISTS ( SELECT 1 FROM dbo.SectionCourseTeacher AS sct",
         teaches),
        ("SELECT assessment_section_id FROM dbo.AssessmentSection WITH (UPDLOCK, HOLDLOCK)",
         existing_assignment),
        ("INSERT INTO dbo.AssessmentSection", insert_assignment),
        ("SELECT DISTINCT a.assessment_id", student_assessments),
        ("SELECT sec.assessment_id FROM dbo.Submission AS s", student_submitted),
        ("OBJECT_ID(N'dbo.AssessmentRubricCriterion'", rubric_table_exists),
        ("OBJECT_ID(N'dbo.EvaluationCriterionApproval'", approval_table_exists),
        ("FROM dbo.EvaluationCriterionApproval WHERE submission_id = ?",
         approved_criteria),
        ("INSERT INTO dbo.EvaluationCriterionApproval", insert_approval),
        ("SUM(max_marks)", rubric_totals),
        ("FROM dbo.AssessmentRubricCriterion WITH (HOLDLOCK)", load_rubric),
        ("SELECT SYSDATETIME()", sysdatetime),
        ("FROM dbo.Student_Export WITH (UPDLOCK, HOLDLOCK)", lock_student),
        ("FROM dbo.Assessment WITH (HOLDLOCK)", submission_assessment),
        ("FROM dbo.AssessmentSection asec WITH (HOLDLOCK)", assignment_for_student),
        ("FROM dbo.Submission WITH (UPDLOCK, HOLDLOCK) WHERE student_id = ?",
         count_student_submissions),
        ("INSERT INTO dbo.Submission", insert_submission),
        ("INSERT INTO dbo.GradingJob", insert_job),
        ("SELECT a.total_marks, a.assessment_id, j.job_id, j.job_status", manual_lock),
        ("SELECT result_status, auto_evaluation_status FROM dbo.Evaluation WITH",
         manual_lock_evaluation),
        ("worker_id = ?, claim_token = NULL, lease_expires_at = NULL "
         "WHERE job_id = ? AND submission_id = ?", manual_close_job),
        ("VALUES (?, NULL, 'MANUALLY_GRADED'", insert_manual_evaluation),
        ("last_error = 'Maximum grading attempts exhausted'", mark_exhausted),
        ("SET job_status = CASE WHEN attempt_count >= ?", release),
        ("SET job_status = 'COMPLETED'", complete_job),
        ("SET job_status = 'PROCESSING'", claim_by_id),
        ("SET lease_expires_at = DATEADD", renew),
        ("WHERE j.job_id = ? AND j.worker_id = ?", prepare),
        ("SELECT submission_id FROM dbo.GradingJob WITH (UPDLOCK, HOLDLOCK)", lock_job),
        ("SELECT a.assessment_id, a.total_marks FROM dbo.Submission", assessment_marks),
        ("SELECT a.assessment_id, a.total_marks, a.validation_percentage", grading_config),
        ("SELECT problem_statement, input_format, output_format", problem_context),
        ("FROM dbo.TestCase WHERE assessment_id = ? AND visibility = 'hidden'",
         hidden_tests),
        ("FROM dbo.TestCase WHERE assessment_id = ? ORDER BY test_number", grading_tests),
        ("SELECT a.total_marks, j.job_status, a.assessment_id, "
         "a.validation_percentage FROM dbo.Submission", lock_for_finalize),
        ("SELECT a.total_marks FROM dbo.Submission", assessment_limit),
        ("SELECT result_status, auto_evaluation_status, tests_passed, tests_run "
         "FROM dbo.Evaluation WITH", lock_evaluation),
        ("SELECT submission_id FROM dbo.Evaluation WITH (UPDLOCK, HOLDLOCK)",
         check_evaluation),
        ("'MANUAL_GRADING'", insert_manual_evaluation),
        ("VALUES ( ?, 0, 'COMPILATION_FAILED'", insert_failure_evaluation),
        ("INSERT INTO dbo.Evaluation (", insert_auto_evaluation),
        ("INSERT INTO dbo.EvaluationCriterion", insert_criterion),
        ("UPDATE dbo.Evaluation SET final_score = ?", finalize),
        ("SELECT 1 FROM dbo.Assessment WHERE assessment_id = ? AND teacher_id = ?",
         teacher_owns),
        ("SELECT s.submission_id, s.student_id, st.student_name", teacher_list),
        ("SELECT s.submission_id, s.student_id, s.file_name, s.source_code",
         teacher_submission),
        ("SELECT s.submission_id, s.student_id, s.assessment_section_id, s.source_code",
         grader_submission),
        ("SELECT suggested_score, auto_evaluation_status, auto_evaluation_feedback",
         evaluation),
        ("SELECT criterion_name, rating, explanation FROM dbo.EvaluationCriterion",
         criteria),
        ("SELECT s.submission_id, s.file_name, s.uploaded_at", student_submission),
    ]
