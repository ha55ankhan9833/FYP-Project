-- ======================================================================
-- Teacher-approved criterion marks (NOT YET APPROVED - do not run)
--
-- Adds ONE new table. No existing table, column, constraint or row is
-- changed. The AI recommendation stays in dbo.EvaluationCriterion
-- (unchanged audit trail); teacher decisions go here.
--
-- Order: run in FYP_E2E_Test first, verify, then FYP_Database. Each run
-- needs explicit owner approval. Idempotency: refuses if the table exists.
-- Rollback: 2026_11_17_evaluation_criterion_approval_down.sql
-- ======================================================================
SET XACT_ABORT ON;
SET NOCOUNT ON;

IF DB_NAME() NOT IN (N'FYP_E2E_Test', N'FYP_Database')
    THROW 50000, 'Run only in FYP_E2E_Test or FYP_Database', 1;

BEGIN TRANSACTION;

IF OBJECT_ID(N'dbo.EvaluationCriterionApproval', N'U') IS NOT NULL
    THROW 50001, 'dbo.EvaluationCriterionApproval already exists', 1;

CREATE TABLE dbo.EvaluationCriterionApproval (
    approval_id     INT IDENTITY(1, 1) NOT NULL
        CONSTRAINT PK_EvaluationCriterionApproval PRIMARY KEY,
    submission_id   INT NOT NULL
        CONSTRAINT FK_ECApproval_Evaluation
        REFERENCES dbo.Evaluation (submission_id),
    criterion_name  NVARCHAR(150) NOT NULL,
    approved_marks  DECIMAL(5, 2) NOT NULL
        CONSTRAINT CK_ECApproval_Marks CHECK (approved_marks >= 0),
    teacher_comment NVARCHAR(1000) NULL,
    approved_by     VARCHAR(50) COLLATE Latin1_General_CI_AS NOT NULL
        CONSTRAINT FK_ECApproval_Teacher
        REFERENCES dbo.Teacher_Export (teacher_id),
    approved_at     DATETIME2 NOT NULL
        CONSTRAINT DF_ECApproval_ApprovedAt DEFAULT SYSUTCDATETIME(),
    CONSTRAINT UQ_ECApproval_Criterion UNIQUE (submission_id, criterion_name),
    CONSTRAINT CK_ECApproval_Name CHECK (LEN(LTRIM(RTRIM(criterion_name))) > 0)
);

COMMIT TRANSACTION;

SELECT DB_NAME() AS database_name,
       OBJECT_ID(N'dbo.EvaluationCriterionApproval', N'U') AS table_object_id,
       (SELECT COUNT(*) FROM dbo.EvaluationCriterionApproval) AS rows_now;
