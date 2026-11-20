# Setup and external services

Verified on the development machine on 2026-11-17 (Windows 11 Pro).

## Python environments

| Purpose | venv | Python | Requirements |
|---|---|---|---|
| Backend API, tests, dev tools | `.venv` | 3.14.10 | `requirements.txt` (direct) / `requirements-lock.txt` (exact) |
| AI constraint service | `.venv_ai` | 3.12.10 | `requirements-ai.txt` |

The tests use only the standard library (`unittest`), so there is no separate
development requirements file:

```bash
.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

Configuration: copy `.env.example` to `.env` and set `JWT_SECRET_KEY`
(32 characters or more).

## External services

| Service | Used by | Endpoint / location | Verified version | Notes |
|---|---|---|---|---|
| **SQL Server** | every data access (`db.py`) | instance on this machine; Windows authentication; *ODBC Driver 18 for SQL Server* | SQL Server 2025 Developer (17.0) | Databases: `FYP_Database` (application data) and `FYP_E2E_Test` (isolated E2E copy, selected only by `dev_tools/run_e2e_backend.py`). The server name is set in `db.py`. |
| **Ollama** | AI rubric grading (`ollama_grading_service.py`) | `http://127.0.0.1:11434` | 0.40.16, model `qwen2.5-coder:3b` | Local only. If unreachable, the grading job is released and never scored zero. |
| **G++ (MinGW-w64)** | development harness only (`dev_tools/trusted_execution_harness.py`); owner-allowlisted reference generation | on `PATH` | 16.2.0 | **Student code is never executed:** the production execution gate (`secure_execution_gate.py`) stays disabled until a verified sandbox exists. |
| **AI constraint service** | `/api/ai/analyze`, `/api/ai/drafts` | `http://127.0.0.1:5001/analyze` (`ai_local_api.py`) | FLAN-T5 (`models/flan_t5_constraint_final`, ~296 MB, CPU) | Start it separately: `.venv_ai\Scripts\python.exe ai_local_api.py`. The model folder is not in git. |

## Running

```bash
.venv\Scripts\python.exe app.py
```

```bash
.venv\Scripts\python.exe dev_tools\run_e2e_backend.py
```

- `app.py` serves the API on 127.0.0.1:5000 against `FYP_Database`. It runs
  with `debug=True`, which is for development only (see
  `docs/BACKEND_GAP_ANALYSIS.md`, G17).
- `run_e2e_backend.py` runs the same API against `FYP_E2E_Test` and refuses
  any other database.

## Not in version control

- `.env` (secrets) and login tokens (`dev_tools/.tokens/`).
- Virtual environments (`.venv/`, `.venv_ai/`) and local AI models (`models/`).
- Generated reports (`dev_tools/reports/`), which contain hidden test data.
  The recorded trusted-fixture reports there are needed by
  `fixture_submission_grader.py` and some tests, which skip without them.
- Local backups (`_backups/`, `*.bak`) and superseded module variants.
