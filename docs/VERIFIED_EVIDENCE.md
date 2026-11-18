# Recorded Execution Evidence — Backup and Restore

Verified-fixture grading (`dev_tools/fixture_submission_grader.py`) replays
the RECORDED outputs of owner-approved developer fixtures. Those reports live
in `dev_tools/reports/trusted_execution_*.json`, which is **git-ignored**
(raw run output that also contains hidden test inputs/outputs). Git only
stores their SHA-256 pins in `dev_tools/recorded_evidence_grading.py`
(`REVIEWED_RECORDS`).

Consequence: a clean checkout has the pins but not the evidence. That state is
never treated as verified:

- `read_record` raises `RecordIntegrityError: ... recorded evidence ... is missing`;
- the grader exits `REFUSED: ...` and claims nothing;
- `python dev_tools/verified_evidence.py status` lists `MISSING` and exits 1;
- the replay/integrity tests are skipped and say why.

## Records (2026-11-18)

| Record | Assessment | Report SHA-256 | Fixture SHA-256 |
|---|---|---|---|
| prime_correct | 5 | 62b170a4…fa793 | f258e59b…b824 |
| prime_buggy_square | 5 | 9bfe61fe…dbdff8 | d52fba8b…3bdd |
| prime_compile_error | 5 | f70d5fa6…838b8 | 89b1c9eb…b1e1 |
| array_sum_int_accumulator | 6 | c3b33890…22b05 | ac95167f…d0e6f4 |

## Procedure

```
.venv\Scripts\python.exe dev_tools\verified_evidence.py status
.venv\Scripts\python.exe dev_tools\verified_evidence.py backup "D:\Fyp Project\FYP_verified_evidence_backup"
.venv\Scripts\python.exe dev_tools\verified_evidence.py restore "D:\Fyp Project\FYP_verified_evidence_backup"
```

- `backup` refuses if any record is missing or altered, never overwrites a
  different file, and writes `VERIFIED_EVIDENCE_MANIFEST.json`.
- `restore` copies back only files whose SHA-256 equals the reviewed pin; a
  tampered or unknown file is rejected (exit 1). Restoring cannot create
  evidence.

A backup was made on 2026-11-18 to `D:\Fyp Project\FYP_verified_evidence_backup`
(outside the repository; a restore drill into an empty folder verified all 4
records). It is on the same disk: keep a second copy off this machine. The
files contain hidden test data — store them like the database backups, not in
a public location.
