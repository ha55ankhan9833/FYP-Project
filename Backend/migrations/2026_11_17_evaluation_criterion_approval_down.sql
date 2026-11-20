-- ======================================================================
-- Rollback of 2026_11_17_evaluation_criterion_approval_up.sql
-- (NOT YET APPROVED - do not run)
--
-- Drops ONLY dbo.EvaluationCriterionApproval. Refuses while it holds
-- teacher approvals, so no teacher decision is lost silently: export
-- them first if a rollback is ever needed after use.
-- After rollback the application keeps working: criterion-level
-- approval requests return 409 and students see FINAL_TOTAL_ONLY or
-- AI_RECOMMENDATION_ACCEPTED_BY_TEACHER breakdowns, as before.
-- ======================================================================
SET XACT_ABORT ON;
SET NOCOUNT ON;

IF DB_NAME() NOT IN (N'FYP_E2E_Test', N'FYP_Database')
    THROW 50000, 'Run only in FYP_E2E_Test or FYP_Database', 1;

BEGIN TRANSACTION;

IF OBJECT_ID(N'dbo.EvaluationCriterionApproval', N'U') IS NULL
    THROW 50003, 'dbo.EvaluationCriterionApproval does not exist', 1;

IF EXISTS (SELECT 1 FROM dbo.EvaluationCriterionApproval)
    THROW 50002, 'Table holds teacher approvals; export them before dropping', 1;

DROP TABLE dbo.EvaluationCriterionApproval;

COMMIT TRANSACTION;

SELECT DB_NAME() AS database_name,
       OBJECT_ID(N'dbo.EvaluationCriterionApproval', N'U') AS table_object_id;
