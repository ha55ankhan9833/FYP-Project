"""
Isolated E2E test database tooling (dev_tools/e2e_test_db.py), the
FYP_DB_NAME switch in db.py and the write guards of the E2E tools.
Fake connections only: nothing here touches SQL Server.
"""

import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tests.fakes import FakeConnection

import db
from dev_tools import e2e_api_client
from dev_tools import e2e_test_db as tdb
from dev_tools import fixture_submission_grader as fsg

PASSWORDS = {"teacher": "Teacher#Secret1", "E2E-TEST-0001": "Stu#One1",
             "E2E-TEST-0002": "Stu#Two2", "E2E-TEST-0003": "Stu#Three3"}


def use_database(test, name):
    """Select a database for one test and always restore the default."""
    test.addCleanup(db.select_database, db.DEFAULT_DATABASE)
    db.select_database(name)


class DatabaseSwitchTests(unittest.TestCase):

    def test_default_is_real_database_and_ignores_environment(self):
        with patch.dict(os.environ, {"FYP_DB_NAME": "FYP_E2E_Test"}):
            self.assertEqual(db.database_name(), "FYP_Database")
            self.assertIn("DATABASE=FYP_Database;", db.connection_string())

    def test_only_allow_listed_names(self):
        use_database(self, "FYP_E2E_Test")
        self.assertIn("DATABASE=FYP_E2E_Test;", db.connection_string())
        for bad in ("BIITDBNew", "master", "FYP_Database;DROP", "fyp_e2e_test"):
            with self.assertRaises(RuntimeError):
                db.select_database(bad)
        self.assertEqual(db.database_name(), "FYP_E2E_Test")
        with self.assertRaises(RuntimeError):
            db.connection_string("BIITDBNew")

    def test_db_module_has_no_os_or_process_imports(self):
        import ast
        from pathlib import Path
        tree = ast.parse(Path(db.__file__).read_text(encoding="utf-8"))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                 for a in n.names}
        self.assertEqual(names, {"pyodbc"})

    def test_launcher_preflight(self):
        from dev_tools import run_e2e_backend as launcher

        def conn(name, real):
            def responder(sql, params):
                return ([(name,)] if "DB_NAME" in sql else [(real,)]), -1
            return lambda: FakeConnection(responder)

        self.assertEqual(launcher.preflight(conn("FYP_E2E_Test", 0)), "FYP_E2E_Test")
        with self.assertRaises(SystemExit):
            launcher.preflight(conn("FYP_Database", 0))
        with self.assertRaises(SystemExit):
            launcher.preflight(conn("FYP_E2E_Test", 47))

    def test_status_route_reports_active_database(self):
        from flask_jwt_extended import create_access_token
        import app as app_module
        with app_module.app.app_context():
            token = create_access_token(identity="BIIT235",
                                        additional_claims={"role": "teacher"})
        use_database(self, "FYP_E2E_Test")
        r = app_module.app.test_client().get(
            "/api/grading/status", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.get_json()["active_database"], "FYP_E2E_Test")
        self.assertFalse(r.get_json()["student_execution_enabled"])


class SeedResponder:
    """Fake target database: empty, then accepts the seed statements."""

    def __init__(self, db_name="FYP_E2E_Test", counts=None, rowcounts=None):
        self.db_name = db_name
        self.counts = counts or {"Assessment": 0, "Student_Export": 0}
        self.rowcounts = rowcounts or {}

    def __call__(self, sql, params):
        if sql == "SELECT DB_NAME()":
            return [(self.db_name,)], -1
        if "FROM sys.tables" in sql:
            return [("dbo", t) for t in self.counts], -1
        if sql.startswith("SELECT COUNT_BIG(*) FROM [dbo].["):
            return [(self.counts[sql.split("[")[2].rstrip("]")],)], -1
        if sql.startswith("SET IDENTITY_INSERT"):
            return [], -1
        if sql.startswith("INSERT INTO dbo."):
            table = sql.split()[2].split(".")[1]
            if table in self.rowcounts:
                return [], self.rowcounts[table]
            expected = {"AssessmentRubricCriterion": 2, "TestCase": 10}
            return [], expected.get(table, 1)
        raise AssertionError(sql)


class SeedTests(unittest.TestCase):

    def run_seed(self, responder):
        conn = FakeConnection(responder)
        result = tdb.seed("FYP_E2E_Test", PASSWORDS,
                          connect_fn=lambda name, autocommit=False: conn)
        return conn, result

    def test_seed_statements_and_single_commit(self):
        conn, applied = self.run_seed(SeedResponder())
        self.assertEqual(conn.commits, 1)
        inserts = [s for s, _ in conn.executed if s.startswith("INSERT")]
        self.assertEqual(len(inserts), 7 + 6)
        for sql, _ in conn.executed:
            # FYP_Database is only ever read through SELECT
            self.assertNotRegex(sql, r"(INSERT INTO|UPDATE|DELETE FROM|MERGE)\s+FYP_Database")
            self.assertNotIn("DROP", sql)
        identity = [s for s, _ in conn.executed if s.startswith("SET IDENTITY_INSERT")]
        self.assertEqual(identity, [
            "SET IDENTITY_INSERT dbo.Assessment ON;", "SET IDENTITY_INSERT dbo.Assessment OFF;",
            "SET IDENTITY_INSERT dbo.AssessmentRubricCriterion ON;",
            "SET IDENTITY_INSERT dbo.AssessmentRubricCriterion OFF;",
            "SET IDENTITY_INSERT dbo.TestCase ON;", "SET IDENTITY_INSERT dbo.TestCase OFF;"])
        students = [p for s, p in conn.executed if "INTO dbo.Student_Export" in s]
        self.assertEqual([p[0] for p in students],
                         ["E2E-TEST-0001", "E2E-TEST-0002", "E2E-TEST-0003"])
        enrollments = [p for s, p in conn.executed if "INTO dbo.Enrollment_Export" in s]
        self.assertEqual(enrollments, [
            ("E2E-ENR-0001", "E2E-TEST-0001", "SEC000001", "CSC-101", "BIIT235"),
            ("E2E-ENR-0002", "E2E-TEST-0002", "SEC000001", "CSC-101", "BIIT235"),
            ("E2E-ENR-0003", "E2E-TEST-0003", "SEC000001", "CSC-101", "BIIT235")])

    def test_passwords_only_bound_as_parameters(self):
        conn, applied = self.run_seed(SeedResponder())
        for sql, params in conn.executed:
            for secret in PASSWORDS.values():
                self.assertNotIn(secret, sql)
        self.assertNotIn("Secret", repr(applied))
        teacher = next(p for s, p in conn.executed if "INTO dbo.Teacher_Export" in s)
        self.assertEqual(teacher, ("Teacher#Secret1", "BIIT235"))

    def test_unexpected_rowcount_rolls_back(self):
        responder = SeedResponder(rowcounts={"TestCase": 9})
        conn = FakeConnection(responder)
        with self.assertRaisesRegex(tdb.Refused, "TestCase"):
            tdb.seed("FYP_E2E_Test", PASSWORDS, connect_fn=lambda *a, **k: conn)
        self.assertEqual((conn.commits, conn.rollbacks), (0, 1))

    def test_refuses_non_empty_or_wrong_database(self):
        for responder in (SeedResponder(counts={"Student_Export": 47}),
                          SeedResponder(db_name="FYP_Database")):
            conn = FakeConnection(responder)
            with self.assertRaises(tdb.Refused):
                tdb.seed("FYP_E2E_Test", PASSWORDS, connect_fn=lambda *a, **k: conn)
            self.assertFalse(conn.statements("INSERT"))
            self.assertEqual(conn.commits, 0)

    def test_confirm_required_for_every_write(self):
        for action in (tdb.create, tdb.drop):
            with self.assertRaises(tdb.Refused):
                action("FYP_Database", connect_fn=self.fail)
        with self.assertRaises(tdb.Refused):
            tdb.seed(None, PASSWORDS, connect_fn=self.fail)


class CreateDropTests(unittest.TestCase):

    def test_create_clones_schema_only_and_verifies_empty(self):
        calls = []

        def master(sql, params):
            calls.append(sql)
            if sql.startswith("SELECT DB_ID"):
                return [(None,)], -1
            return [], -1

        def target(sql, params):
            if sql.startswith("SELECT DB_NAME()"):
                return [("FYP_E2E_Test", "READ_WRITE")], -1
            if "FROM sys.tables" in sql:
                return [("dbo", "Assessment"), ("dbo", "Student_Export")], -1
            self.assertIn("COUNT_BIG", sql)      # real counts, not metadata
            return [(0,)], -1

        conns = {"master": FakeConnection(master), "FYP_E2E_Test": FakeConnection(target)}
        conns["master"].cursor = self._with_nextset(conns["master"])
        result = tdb.create("FYP_E2E_Test",
                            connect_fn=lambda name, autocommit=False: conns[name])
        self.assertTrue(result["all_tables_empty"])
        self.assertIn("DBCC CLONEDATABASE (FYP_Database, FYP_E2E_Test) "
                      "WITH NO_STATISTICS, NO_QUERYSTORE;", calls)

    def test_create_refuses_if_target_exists_or_clone_has_rows(self):
        conn = FakeConnection(lambda sql, p: ([(7,)], -1))
        with self.assertRaisesRegex(tdb.Refused, "already exists"):
            tdb.create("FYP_E2E_Test", connect_fn=lambda *a, **k: conn)
        self.assertEqual(len(conn.executed), 1)

    def test_drop_targets_only_the_test_database(self):
        executed = []

        def master(sql, params):
            executed.append(sql)
            return ([(9,)], -1) if sql.startswith("SELECT DB_ID") else ([], -1)

        tdb.drop("FYP_E2E_Test", connect_fn=lambda *a, **k: FakeConnection(master))
        self.assertEqual(executed[1:], [
            "ALTER DATABASE [FYP_E2E_Test] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;",
            "DROP DATABASE [FYP_E2E_Test];"])
        self.assertTrue(all("FYP_Database" not in s for s in executed))

    @staticmethod
    def _with_nextset(conn):
        original = conn.cursor

        def cursor():
            c = original()
            c.nextset = lambda: False
            return c
        return cursor


class PasswordPromptTests(unittest.TestCase):

    def test_hidden_prompts_and_mismatch(self):
        answers = iter(["a", "b"])
        with self.assertRaisesRegex(tdb.Refused, "do not match"):
            tdb.prompt_password("x", 10, prompt=lambda _: next(answers))
        answers = iter(["", ""])
        with self.assertRaises(tdb.Refused):
            tdb.prompt_password("x", 10, prompt=lambda _: next(answers))

    def test_collect_passwords_prints_nothing_secret(self):
        values = iter(["T1", "T1", "A1", "A1", "B2", "B2", "C3", "C3"])
        out = io.StringIO()
        with redirect_stdout(out):
            result = tdb.collect_passwords(prompt=lambda _: next(values))
        self.assertEqual(result["E2E-TEST-0003"], "C3")
        self.assertEqual(out.getvalue(), "")

    def test_plan_never_contains_passwords(self):
        self.assertNotIn("password\": \"", str(tdb.TEST_STUDENTS))


class WriteGuardTests(unittest.TestCase):

    def test_fixture_grader_refuses_real_database(self):
        conn = FakeConnection(lambda sql, p: ([("FYP_Database",)], -1))
        with self.assertRaisesRegex(fsg.SubmissionNotEligible, "only allowed"):
            fsg.require_test_database(lambda: conn)
        ok = FakeConnection(lambda sql, p: ([("FYP_E2E_Test",)], -1))
        self.assertEqual(fsg.require_test_database(lambda: ok), "FYP_E2E_Test")

    def test_api_driver_refuses_unless_both_sides_use_test_database(self):
        with self.assertRaises(SystemExit):              # default database
            e2e_api_client.require_test_backend(lambda: (200, {}))
        use_database(self, "FYP_E2E_Test")
        with self.assertRaises(SystemExit):
            e2e_api_client.require_test_backend(
                lambda: (200, {"active_database": "FYP_Database"}))
        with self.assertRaises(SystemExit):
            e2e_api_client.require_test_backend(
                lambda: (200, {"active_database": "FYP_E2E_Test",
                               "student_execution_enabled": True}))
        e2e_api_client.require_test_backend(
            lambda: (200, {"active_database": "FYP_E2E_Test",
                           "student_execution_enabled": False}))

    def test_api_driver_requires_matching_identity(self):
        ok = lambda: (200, {"role": "student", "user_id": "E2E-TEST-0002"})  # noqa: E731
        e2e_api_client.require_identity(None, "student", "E2E-TEST-0002", ok)
        for wrong in ((200, {"role": "student", "user_id": "E2E-TEST-0001"}),
                      (200, {"role": "teacher", "user_id": "E2E-TEST-0002"}),
                      (401, {})):
            with self.assertRaises(SystemExit):
                e2e_api_client.require_identity(None, "student", "E2E-TEST-0002",
                                                lambda w=wrong: w)
        self.assertEqual(e2e_api_client.STUDENT_FIXTURE["E2E-TEST-0003"],
                         "prime_compile_error")


class FourthStudentTests(unittest.TestCase):

    def precheck(self, db_name="FYP_E2E_Test", exists=(0, 0, 0), assigned=1, teaches=1):
        def responder(sql, params):
            if sql.startswith("SELECT DB_NAME(), (SELECT COUNT(*) FROM dbo.Student_Export"):
                return [(db_name, *exists, assigned, teaches)], -1
            if sql.startswith("INSERT INTO dbo."):
                return [], 1
            raise AssertionError(sql)
        return FakeConnection(responder)

    def test_add_student_inserts_exactly_two_rows(self):
        conn = self.precheck()
        applied = tdb.add_student("FYP_E2E_Test", "Pw#4secret",
                                  connect_fn=lambda *a, **k: conn)
        inserts = [(s, p) for s, p in conn.executed if s.startswith("INSERT")]
        self.assertEqual([p[0] for _, p in inserts], ["E2E-TEST-0004", "E2E-ENR-0004"])
        self.assertEqual(inserts[0][1], ("E2E-TEST-0004", "E2E Test Student 4",
                                         "e2e.test4@e2e.test", "Pw#4secret", "SEC000001"))
        self.assertEqual(inserts[1][1], ("E2E-ENR-0004", "E2E-TEST-0004", "SEC000001",
                                         "CSC-101", "BIIT235"))
        self.assertEqual(conn.commits, 1)
        for sql, _ in conn.executed:
            self.assertNotIn("Pw#4secret", sql)
            self.assertNotIn("FYP_Database", sql)
        self.assertNotIn("Pw#4secret", repr(applied))

    def test_add_student_refusals_write_nothing(self):
        for kwargs in ({"db_name": "FYP_Database"}, {"exists": (1, 0, 0)},
                       {"exists": (0, 1, 0)}, {"exists": (0, 0, 1)},
                       {"assigned": 0}, {"teaches": 0}):
            with self.subTest(**kwargs):
                conn = self.precheck(**kwargs)
                with self.assertRaises(tdb.Refused):
                    tdb.add_student("FYP_E2E_Test", "x", connect_fn=lambda *a, **k: conn)
                self.assertFalse(conn.statements("INSERT"))
                self.assertEqual(conn.commits, 0)
        with self.assertRaises(tdb.Refused):
            tdb.add_student("FYP_Database", "x", connect_fn=self.fail)

    def cleanup_conn(self, submissions=(5,), counts=(2, 2, 1, 1, 1, 1, 1),
                     db_name="FYP_E2E_Test", short=None):
        tables = [t for t, _ in tdb.CLEANUP_STEPS]

        def responder(sql, params):
            if sql.startswith("SELECT DB_NAME(), (SELECT COUNT(*) FROM dbo.EvaluationCriterionApproval"):
                return [(db_name, *counts)], -1
            if sql.startswith("SELECT submission_id FROM dbo.Submission"):
                return [(s,) for s in submissions], -1
            if sql.startswith("DELETE"):
                table = next(t for t in tables if f"dbo.{t} " in sql + " "
                             or f"dbo.{t}\n" in sql)
                n = counts[tables.index(table)]
                return [], (n - 1 if table == short else n)
            raise AssertionError(sql)
        return FakeConnection(responder)

    def test_cleanup_dry_run_deletes_nothing(self):
        conn = self.cleanup_conn()
        result = tdb.cleanup_student("FYP_E2E_Test", connect_fn=lambda *a, **k: conn)
        self.assertEqual(result["would_delete"]["Evaluation"], 1)
        self.assertFalse(conn.statements("DELETE"))
        self.assertEqual(conn.commits, 0)

    def test_cleanup_execute_scoped_to_fourth_student(self):
        conn = self.cleanup_conn()
        result = tdb.cleanup_student("FYP_E2E_Test", connect_fn=lambda *a, **k: conn,
                                     dry_run=False)
        deletes = [(s, p) for s, p in conn.executed if s.startswith("DELETE")]
        self.assertEqual(len(deletes), 7)
        for sql, params in deletes:
            self.assertIn("E2E-TEST-0004", params)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(result["deleted"]["Student_Export"], 1)

    def test_cleanup_refusals(self):
        conn = self.cleanup_conn(submissions=(3,))
        with self.assertRaisesRegex(tdb.Refused, "protected"):
            tdb.cleanup_student("FYP_E2E_Test", connect_fn=lambda *a, **k: conn,
                                dry_run=False)
        self.assertFalse(conn.statements("DELETE"))
        conn = self.cleanup_conn(short="Evaluation")
        with self.assertRaisesRegex(tdb.Refused, "rolled back"):
            tdb.cleanup_student("FYP_E2E_Test", connect_fn=lambda *a, **k: conn,
                                dry_run=False)
        self.assertEqual((conn.commits, conn.rollbacks), (0, 1))
        with self.assertRaisesRegex(tdb.Refused, "only for the fourth"):
            tdb.cleanup_student("FYP_E2E_Test", connect_fn=self.fail,
                                student=tdb.TEST_STUDENTS[1])
        conn = self.cleanup_conn(db_name="FYP_Database")
        with self.assertRaises(tdb.Refused):
            tdb.cleanup_student("FYP_E2E_Test", connect_fn=lambda *a, **k: conn)

    def test_driver_criterion_parsing(self):
        self.assertEqual(e2e_api_client.parse_criteria(
            ["Logic & Algorithm=1.00", "Code Quality=2"]),
            [{"criterion_name": "Logic & Algorithm", "marks": "1.00"},
             {"criterion_name": "Code Quality", "marks": "2"}])
        with self.assertRaises(SystemExit):
            e2e_api_client.parse_criteria(["Logic"])
        self.assertEqual(e2e_api_client.STUDENT_FIXTURE["E2E-TEST-0004"],
                         "prime_buggy_square")


if __name__ == "__main__":
    unittest.main()
