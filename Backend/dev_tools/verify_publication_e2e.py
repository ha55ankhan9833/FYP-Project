"""Explicitly authorized, rollback-only SQL integration check.

Uses existing assessments, teachers and enrollments. Never creates student
records/credentials. Refuses any database except FYP_E2E_Test before writes.
Does not start Flask, grading, AI, compilation or C++ execution.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pyodbc
import db
import assessment_section_service as publishing
import student_assessment_service as students
from assessment_publication_policy import publication_problems


class TransactionView:
    """Let services share one isolated transaction; outer finally rolls it back."""
    def __init__(self, connection):
        self.connection = connection
    @property
    def autocommit(self):
        return self.connection.autocommit
    @autocommit.setter
    def autocommit(self, value):
        assert value is False
    def cursor(self):
        return self.connection.cursor()
    def commit(self):
        pass
    def rollback(self):
        pass
    def close(self):
        pass


def counts(cursor):
    result = {}
    for table in ("Assessment", "AssessmentSection", "TestCase", "Student_Export", "Submission", "AssessmentRubricCriterion"):
        cursor.execute(f"SELECT COUNT(*) FROM dbo.{table}")
        result[table] = cursor.fetchone()[0]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-rollback-writes", action="store_true")
    if not parser.parse_args().allow_rollback_writes:
        parser.error("Explicit --allow-rollback-writes is required")
    conn = pyodbc.connect(db.connection_string(db.E2E_TEST_DATABASE), autocommit=False)
    before = None
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DB_NAME()")
        assert cursor.fetchone()[0] == "FYP_E2E_Test", "Refusing non-E2E database"
        before = counts(cursor)
        cursor.execute("""SELECT a.assessment_id, a.teacher_id, a.course_code,
                     a.total_marks, a.validation_percentage, a.assessment_type
                     FROM dbo.Assessment a WHERE NOT EXISTS
                     (SELECT 1 FROM dbo.AssessmentSection x WHERE x.assessment_id=a.assessment_id)
                     ORDER BY a.assessment_id DESC""")
        chosen = None
        sections = []
        candidates = cursor.fetchall()
        for row in candidates:
            if publication_problems(cursor, row[0], row[3], row[4], row[5]):
                continue
            cursor.execute("""SELECT DISTINCT s.section_id FROM dbo.SectionCourseTeacher t
                INNER JOIN dbo.Section_Export s ON s.section_id COLLATE DATABASE_DEFAULT=t.section_id COLLATE DATABASE_DEFAULT
                WHERE t.teacher_id=? AND t.course_no=? AND EXISTS
                  (SELECT 1 FROM dbo.Enrollment_Export e WHERE e.section_id COLLATE DATABASE_DEFAULT=s.section_id COLLATE DATABASE_DEFAULT
                   AND e.course_no COLLATE DATABASE_DEFAULT=t.course_no COLLATE DATABASE_DEFAULT)
                ORDER BY s.section_id""", row[1], row[2])
            available = [r[0] for r in cursor.fetchall()]
            if available:
                chosen = row
                sections = available[:2]
                break
        assert chosen is not None, "No ready unpublished fixture with an allocated enrolled section exists; no records created"
        aid, teacher, course, _, _, kind = chosen
        identities = []
        for section in sections:
            cursor.execute("""SELECT TOP (1) e.student_id FROM dbo.Enrollment_Export e
                INNER JOIN dbo.Student_Export st ON st.student_id COLLATE DATABASE_DEFAULT=e.student_id COLLATE DATABASE_DEFAULT
                WHERE e.section_id=? AND e.course_no=?""", section, course)
            row = cursor.fetchone()
            assert row, "Existing enrolled student required"
            identities.append(row[0])
        cursor.execute("""SELECT TOP (1) st.student_id FROM dbo.Student_Export st WHERE NOT EXISTS
            (SELECT 1 FROM dbo.Enrollment_Export e WHERE e.student_id COLLATE DATABASE_DEFAULT=st.student_id COLLATE DATABASE_DEFAULT
             AND e.section_id=? AND e.course_no=?)""", sections[0], course)
        outside = cursor.fetchone()
        view = TransactionView(conn)
        factory = lambda: view
        def inaccessible(identity):
            try:
                students.get_student_assessments(identity, aid, factory)
            except students.StudentAssessmentNotFound:
                return
            raise AssertionError("Assessment unexpectedly visible")
        inaccessible(identities[0])
        print("PASS unpublished assessment inaccessible")
        # One section only; an unrelated enrolled student remains excluded.
        result = publishing.assign_sections(aid, teacher, [sections[0]], factory)
        assert result["published_section_ids"] == [sections[0]]
        assert students.get_student_assessments(identities[0], aid, factory)["assessment_type"] == kind
        if outside:
            inaccessible(outside[0])
        else:
            print("LIMITATION no existing outsider student in E2E copy")
        print("PASS one selected section visible with enrollment checked")
        if outside:
            print("PASS outsider student denied")
        retry = publishing.assign_sections(aid, teacher, [sections[0]], factory)
        assert retry["replayed"] and not retry["assigned"]
        result = publishing.assign_sections(aid, teacher, sections, factory)
        assert sorted(result["published_section_ids"]) == sorted(sections)
        assert len(result["assigned"]) == len(sections)-1
        for identity in identities:
            item = students.get_student_assessments(identity, aid, factory)
            assert set(item) == set(students.PUBLIC_FIELDS) | {"publishing_status"}
        print("PASS safe retry, actual type retained, no answer/hidden-case fields")
        if len(sections)==2:
            print("PASS two selected sections")
        else:
            print("LIMITATION E2E copy has only one allocated enrolled section; multi-section behavior verified in isolated tests")
        cursor.execute("""SELECT TOP (1) s.section_id FROM dbo.Section_Export s WHERE NOT EXISTS
            (SELECT 1 FROM dbo.SectionCourseTeacher t WHERE t.section_id COLLATE DATABASE_DEFAULT=s.section_id COLLATE DATABASE_DEFAULT
             AND t.teacher_id=? AND t.course_no=?) ORDER BY s.section_id""", teacher, course)
        denied = cursor.fetchone()
        if denied:
            try:
                publishing.assign_sections(aid, teacher, [denied[0]], factory)
            except publishing.SectionAssignmentError as error:
                assert error.status_code == 403
            else:
                raise AssertionError("Unauthorized section was accepted")
            print("PASS unauthorized section rejected")
        else:
            print("LIMITATION no unrelated section exists in E2E copy; unauthorized-section denial verified in isolated tests")
    finally:
        conn.rollback()
        try:
            if before is not None:
                assert counts(conn.cursor()) == before, "Fixture counts changed after rollback"
                print("PASS FYP_E2E_Test rolled back; assessment, publication, case, student, submission and rubric counts unchanged")
        finally:
            conn.rollback()
            conn.close()


if __name__ == "__main__":
    main()
