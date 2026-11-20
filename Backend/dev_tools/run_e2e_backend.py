"""
DEVELOPMENT-ONLY: run the unchanged Flask backend against the isolated
FYP_E2E_Test database on 127.0.0.1:5000.

    .venv\\Scripts\\python.exe dev_tools\\run_e2e_backend.py

Refuses to start unless the connected database really is FYP_E2E_Test,
contains no real students (only E2E-TEST-*), and the production student
execution gate is disabled. A normal `python app.py` is unaffected and
always uses FYP_Database.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db  # noqa: E402


def preflight(connect=None):
    conn = (connect or db.get_connection)()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DB_NAME()")
        name = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM dbo.Student_Export "
                       "WHERE student_id NOT LIKE 'E2E-TEST-%'")
        real_students = int(cursor.fetchone()[0])
    finally:
        conn.rollback()
        conn.close()
    if name != db.E2E_TEST_DATABASE:
        raise SystemExit(f"REFUSED: connected to {name}, not {db.E2E_TEST_DATABASE}")
    if real_students:
        raise SystemExit(f"REFUSED: {real_students} non-test students in {name}")

    import secure_execution_gate
    if secure_execution_gate.get_secure_sandbox() is not None:
        raise SystemExit("REFUSED: production student execution is enabled")
    return name


def main():
    db.select_database(db.E2E_TEST_DATABASE)
    print("E2E backend database:", preflight())
    import app as app_module
    app_module.app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
