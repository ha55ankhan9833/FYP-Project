-- FYP Auto Test Case Generator
-- Reference Solution Storage

IF OBJECT_ID('dbo.ReferenceSolution', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ReferenceSolution
    (
        reference_solution_id INT IDENTITY(1,1)
            NOT NULL PRIMARY KEY,

        assessment_id INT NOT NULL,

        source_code NVARCHAR(MAX) NOT NULL,

        source_hash CHAR(64) NOT NULL,

        language VARCHAR(20) NOT NULL
            CONSTRAINT DF_ReferenceSolution_Language
            DEFAULT 'cpp',

        approval_status VARCHAR(20) NOT NULL
            CONSTRAINT DF_ReferenceSolution_Status
            DEFAULT 'PENDING',

        created_at DATETIME2 NOT NULL
            CONSTRAINT DF_ReferenceSolution_CreatedAt
            DEFAULT SYSUTCDATETIME(),

        approved_at DATETIME2 NULL,

        CONSTRAINT FK_ReferenceSolution_Assessment
            FOREIGN KEY (assessment_id)
            REFERENCES dbo.Assessment(assessment_id),

        CONSTRAINT UQ_ReferenceSolution_Assessment
            UNIQUE (assessment_id),

        CONSTRAINT CK_ReferenceSolution_Status
            CHECK (
                approval_status IN (
                    'PENDING',
                    'APPROVED',
                    'REJECTED'
                )
            ),

        CONSTRAINT CK_ReferenceSolution_Language
            CHECK (language = 'cpp'),

        CONSTRAINT CK_ReferenceSolution_ApprovedAt
            CHECK (
                (
                    approval_status = 'APPROVED'
                    AND approved_at IS NOT NULL
                )
                OR
                (
                    approval_status <> 'APPROVED'
                    AND approved_at IS NULL
                )
            )
    );
END;
