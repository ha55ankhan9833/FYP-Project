import hashlib
import json
import re
from pathlib import Path

from db import get_connection
from test_case_approval_validator import (
    validate_assessment_approval
)


# Built-in reviewed reference (Assessment 4 factorial). Kept so the
# original behaviour cannot be lost if the JSON file is missing.
TRUSTED_REFERENCE_HASHES = frozenset({
    "53b7d425901f831aae4dae9bfaeab839de9ec51415f319ce4943f43b07893993"
})

TRUSTED_HASHES_FILE = Path(__file__).with_name("trusted_reference_hashes.json")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def load_trusted_reference_hashes(path=TRUSTED_HASHES_FILE):
    """
    Human-reviewed reference hashes allowed to run on the host.
    Malformed entries are ignored; a missing file means built-ins only.
    """

    hashes = set(TRUSTED_REFERENCE_HASHES)

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return frozenset(hashes)
    except (OSError, ValueError):
        return frozenset(hashes)

    for entry in data.get("references", []) if isinstance(data, dict) else []:
        value = entry.get("sha256") if isinstance(entry, dict) else None
        if isinstance(value, str) and _HASH_PATTERN.match(value):
            hashes.add(value)

    return frozenset(hashes)


def choose_execution_mode(source_hash, sandbox_available,
                          trusted_hashes=None):
    """
    'sandbox'      - verified isolated sandbox runs any APPROVED reference
    'trusted_host' - host may run ONLY a human-reviewed, allow-listed hash
    Otherwise generation is refused.
    """

    if sandbox_available:
        return "sandbox"

    trusted = (load_trusted_reference_hashes()
               if trusted_hashes is None else trusted_hashes)

    if source_hash in trusted:
        return "trusted_host"

    raise ValueError(
        "Reference solution has not been reviewed for host execution "
        "and no secure sandbox is available"
    )


def validate_generation_preflight(assessment_id, teacher_id):
    """
    Generic pre-generation checks for ANY assessment owned by teacher_id:
    ownership, finalized teacher-approved constraints matching the
    assessment, an APPROVED C++ reference with an intact hash, and no
    existing test cases. Does not execute anything.
    """

    if type(assessment_id) is not int or assessment_id <= 0:
        raise ValueError("Invalid assessment ID")

    if not isinstance(teacher_id, str) or not teacher_id.strip():
        raise ValueError("Invalid teacher ID")

    conn = get_connection()

    try:
        conn.autocommit = False
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                assessment_id,
                value_constraints,
                code_constraints
            FROM dbo.Assessment WITH (UPDLOCK, HOLDLOCK)
            WHERE assessment_id = ?
              AND teacher_id = ?
        """, assessment_id, teacher_id)

        assessment = cursor.fetchone()

        if assessment is None:
            raise ValueError(
                "Assessment not found or access denied"
            )

        constraints = str(assessment[1] or "").strip()
        code_constraints = str(assessment[2] or "").strip()

        approval = validate_assessment_approval(
            cursor, assessment_id
        )

        if not approval["valid"]:
            raise ValueError("Constraints not approved")

        if approval["approved_value_constraints"] != constraints:
            raise ValueError(
                "Approved constraints do not match assessment"
            )

        # Code constraints (e.g. "recursion_required") describe how
        # students must write code; they do not change valid inputs.
        # They are still required to match the teacher approval.
        if approval["approved_code_constraints"] != code_constraints:
            raise ValueError(
                "Approved code constraints do not match assessment"
            )

        cursor.execute("""
            SELECT
                reference_solution_id,
                source_code,
                source_hash,
                language,
                approval_status
            FROM dbo.ReferenceSolution WITH (UPDLOCK, HOLDLOCK)
            WHERE assessment_id = ?
        """, assessment_id)

        reference = cursor.fetchone()

        if reference is None:
            raise ValueError("Reference solution not found")

        reference_id = int(reference[0])
        source_code = reference[1]
        stored_hash = reference[2]
        language = reference[3]
        status = reference[4]

        if status != "APPROVED":
            raise ValueError(
                "Reference solution must be APPROVED"
            )

        if language != "cpp":
            raise ValueError("Only C++ is supported")

        actual_hash = hashlib.sha256(
            source_code.encode("utf-8")
        ).hexdigest()

        if actual_hash != stored_hash:
            raise ValueError("Reference source hash mismatch")

        cursor.execute("""
            SELECT COUNT(*)
            FROM dbo.TestCase WITH (UPDLOCK, HOLDLOCK)
            WHERE assessment_id = ?
        """, assessment_id)

        existing_count = int(cursor.fetchone()[0])

        if existing_count:
            raise ValueError(
                "Assessment already has test cases"
            )

        result = {
            "assessment_id": assessment_id,
            "reference_solution_id": reference_id,
            "source_code": source_code,
            "source_hash": actual_hash,
            "constraints": constraints,
            "code_constraints": code_constraints,
            "ready": True
        }

        conn.commit()
        return result

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()
