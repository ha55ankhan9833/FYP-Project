"""
Recorded-evidence durability: a checkout without the git-ignored reports must
report them MISSING (never verified), and restore accepts only byte-identical
files. Temporary folders only; no database, no compiler.
"""

import json
import tempfile
import unittest
from pathlib import Path

from tests import fakes  # noqa: F401

from dev_tools import recorded_evidence_grading as reg
from dev_tools import verified_evidence as ve

RECORDS = {"demo": {"report": "trusted_execution_demo.json",
                    "report_sha256": ve.hashlib.sha256(b'{"x": 1}').hexdigest(),
                    "fixture_sha256": "f" * 64}}


class EvidenceTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.reports = Path(self.tmp.name) / "reports"
        self.backup_dir = Path(self.tmp.name) / "backup"
        self.reports.mkdir()

    def test_clean_checkout_reports_missing_not_verified(self):
        self.assertEqual(ve.status(self.reports, RECORDS), {"demo": ve.MISSING})

    def test_wrong_bytes_are_a_mismatch(self):
        (self.reports / "trusted_execution_demo.json").write_bytes(b'{"x": 2}')
        self.assertEqual(ve.status(self.reports, RECORDS), {"demo": ve.MISMATCH})

    def test_backup_and_restore_round_trip(self):
        (self.reports / "trusted_execution_demo.json").write_bytes(b'{"x": 1}')
        self.assertEqual(ve.backup(self.backup_dir, self.reports, RECORDS),
                         ["trusted_execution_demo.json"])
        manifest = json.loads((self.backup_dir / ve.MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(manifest["demo"]["report_sha256"], RECORDS["demo"]["report_sha256"])
        (self.reports / "trusted_execution_demo.json").unlink()          # "clean checkout"
        self.assertEqual(ve.restore(self.backup_dir, self.reports, RECORDS), (["demo"], []))
        self.assertEqual(ve.status(self.reports, RECORDS), {"demo": ve.PRESENT})

    def test_restore_rejects_tampered_backup(self):
        self.backup_dir.mkdir()
        (self.backup_dir / "trusted_execution_demo.json").write_bytes(b'{"x": 999}')
        self.assertEqual(ve.restore(self.backup_dir, self.reports, RECORDS), ([], ["demo"]))
        self.assertEqual(ve.status(self.reports, RECORDS), {"demo": ve.MISSING})

    def test_backup_refuses_incomplete_evidence(self):
        with self.assertRaises(RuntimeError):
            ve.backup(self.backup_dir, self.reports, RECORDS)

    def test_missing_report_is_an_integrity_error_not_verified(self):
        original = reg.REPORTS
        reg.REPORTS = self.reports
        try:
            with self.assertRaisesRegex(reg.RecordIntegrityError, "missing"):
                reg.read_record("array_sum_int_accumulator")
        finally:
            reg.REPORTS = original

    def test_every_reviewed_record_is_covered(self):
        self.assertEqual(set(ve.status()), set(reg.REVIEWED_RECORDS))


if __name__ == "__main__":
    unittest.main()
