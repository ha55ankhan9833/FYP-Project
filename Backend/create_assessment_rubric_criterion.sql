/*
    Version 2 (approved 2026-11-17): creates ONLY dbo.AssessmentRubricCriterion.
    Does not touch any existing table or record.

    Purpose: store teacher-defined rubric criteria per assessment.
    The Python layer (assessment_rubric_service.py) enforces:
      - criterion maximums sum exactly to the rubric allocation
        (total_marks - test allocation from validation_percentage)
      - case-insensitive unique names
      - only the owning teacher can modify
      - the rubric is locked once any submission exists
    The database constraints below are a second line of defence.

    Column sizes mirror dbo.EvaluationCriterion
    (criterion_name NVARCHAR(150), rating DECIMAL(5,2)) so every stored
    criterion can be copied into an evaluation without truncation.

    Idempotent: safe to re-run; creates the table only if missing.
*/

SET XACT_ABORT ON;
BEGIN TRANSACTION;

IF OBJECT_ID(N'dbo.AssessmentRubricCriterion', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.AssessmentRubricCriterion (
        rubric_criterion_id INT IDENTITY(1,1) NOT NULL,
        assessment_id       INT              NOT NULL,
        criterion_name      NVARCHAR(150)    NOT NULL,
        max_marks           DECIMAL(5,2)     NOT NULL,
        description         NVARCHAR(1000)   NULL,
        created_at          DATETIME2        NOT NULL
            CONSTRAINT DF_AssessmentRubricCriterion_CreatedAt
            DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_AssessmentRubricCriterion
            PRIMARY KEY (rubric_criterion_id),

        CONSTRAINT FK_AssessmentRubricCriterion_Assessment
            FOREIGN KEY (assessment_id)
            REFERENCES dbo.Assessment (assessment_id),
            -- No ON DELETE CASCADE: assessment deletion is already
            -- blocked by related rows (see DELETE /api/assessments).

        CONSTRAINT UQ_AssessmentRubricCriterion_Name
            UNIQUE (assessment_id, criterion_name),

        CONSTRAINT CK_AssessmentRubricCriterion_MaxMarks
            CHECK (max_marks > 0),

        CONSTRAINT CK_AssessmentRubricCriterion_Name
            CHECK (LEN(LTRIM(RTRIM(criterion_name))) > 0)
    );
END;

COMMIT TRANSACTION;

/*
    ROLLBACK SCRIPT (only if needed, also requires approval):

    DROP TABLE IF EXISTS dbo.AssessmentRubricCriterion;
*/
