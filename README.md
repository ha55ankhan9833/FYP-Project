# Auto Test Case Generator (FYP)

Final Year Project: an offline-AI assisted system that helps instructors create
C++ programming assessments, generates 10 verified test cases per assessment,
suggests problem-specific rubrics, and supports teacher-approved grading.

## Repository layout

| Path | Contents |
|---|---|
| `Backend/` | Flask + SQL Server REST API, test-case generation, verified oracles, rubric workflow, grading worker, tests and docs. Full development history is preserved. |
| `Frontend/` | React + Vite instructor web app. |
| `FYP_Architecture_Workflow_Clean_Final.pdf` | Engineering handbook: dataset, models, architecture and workflow. |

Key documentation:

- `Backend/docs/API_CONTRACT.md` - backend API contract
- `Backend/docs/STEP1_3_GENERATION_CONTRACT.md` - assessment creation, rubric and generation flow
- `Frontend/README.md` and `Frontend/docs/` - frontend screens and integration notes

## Running locally (summary)

Backend (Python 3, SQL Server with ODBC Driver 18, optional Ollama for Qwen):

```bash
cd Backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m unittest discover -s tests -t .
.venv\Scripts\python app.py
```

Copy `Backend/.env.example` to `Backend/.env` and set your own secrets. Model
weights, the training dataset and database contents are not part of this
repository.

Frontend (Node.js):

```bash
cd Frontend
npm install
npm run dev
```

## Not included

Secrets and `.env` files, login tokens, virtual environments, `node_modules`,
build output, model weights, database backups, browser verification
screenshots, and hidden assessment test-case data (E2E run state and real
assessment test inputs/outputs). A few backend tests and fixture scripts read
those private fixture files and are expected to fail or be skipped on a fresh
clone.
