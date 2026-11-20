"""In-memory fake pyodbc connection for transactional unit tests."""

import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def approved_rubric_response(sql, total_marks, validation_percentage):
    """Fake answer for the rubric-ready check secure saves now perform: an
    approved rubric (one criterion) matching the allocation exactly.
    Returns None when `sql` is not a rubric statement."""
    if "AssessmentRubricCriterion" not in sql:
        return None
    if "OBJECT_ID" in sql:
        return [(1,)], -1
    if sql.startswith("SELECT rubric_criterion_id"):
        from grading_allocation import split_allocation
        _, rubric = split_allocation(total_marks, validation_percentage)
        return ([(1, "Approved criterion", rubric, None)] if rubric > 0 else []), -1
    raise AssertionError(f"unexpected rubric SQL: {sql}")


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []
        self.rowcount = -1
        self.description = None

    def execute(self, sql, *params):
        if len(params) == 1 and isinstance(params[0], (tuple, list)):
            params = tuple(params[0])
        self.conn.executed.append((" ".join(sql.split()), params))
        rows, rowcount = self.conn.responder(" ".join(sql.split()), params)
        self._rows = list(rows or [])
        self.rowcount = rowcount
        return self

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def close(self):
        pass


class FakeConnection:
    """
    responder(sql, params) -> (rows, rowcount)
    Records every statement, commit and rollback.
    """

    def __init__(self, responder):
        self.responder = responder
        self.executed = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.autocommit = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True

    def statements(self, fragment):
        return [s for s, _ in self.executed if fragment in s]
