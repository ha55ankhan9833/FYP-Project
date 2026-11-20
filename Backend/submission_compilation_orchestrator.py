from cpp_compilation_service import (
    compile_cpp_source,
    cleanup_cpp_build
)
from compilation_failure_evaluation_service import (
    save_compilation_failure_evaluation
)


def process_submission_compilation(submission_id, source_code):
    """
    Development-only compilation orchestration.

    Requires a previously saved, authorized submission.
    Does not execute student C++.
    Does not call the LLM.

    WARNING: Local G++ compilation is not sandboxed.
    Do not expose this workflow to untrusted submissions.
    """

    # Fail closed (2026-11-17): this compiles STUDENT code with host g++,
    # which is not isolated. Nothing calls it; the grading worker uses
    # secure_execution_gate instead. It stays disabled so it can never
    # be wired to a route or worker by accident.
    raise RuntimeError(
        "Unsandboxed student compilation is disabled; use the grading "
        "pipeline with a verified secure sandbox"
    )

    if type(submission_id) is not int or submission_id <= 0:
        raise ValueError("Invalid submission ID")

    if not isinstance(source_code, str) or not source_code.strip():
        raise ValueError("Invalid source code")

    compilation = compile_cpp_source(source_code)

    if not isinstance(compilation, dict):
        raise ValueError("Invalid compilation response")

    try:
        status = compilation.get("status")
        success = compilation.get("success")

        if status == "COMPILATION_FAILED":
            if success is not False:
                raise ValueError("Inconsistent compilation result")

            evaluation = save_compilation_failure_evaluation(
                submission_id,
                compilation
            )

            return {
                "submission_id": submission_id,
                "status": "COMPILATION_FAILED",
                "evaluation": evaluation,
                "llm_called": False
            }

        if status == "COMPILED":
            if success is not True:
                raise ValueError("Inconsistent compilation result")

            return {
                "submission_id": submission_id,
                "status": "AWAITING_ISOLATED_EXECUTION",
                "evaluation": None,
                "llm_called": False
            }

        return {
            "submission_id": submission_id,
            "status": status,
            "evaluation": None,
            "llm_called": False
        }

    finally:
        build_directory = compilation.get("build_directory")

        if build_directory is not None:
            cleanup_cpp_build(build_directory)


if __name__ == "__main__":
    print("COMPILATION ORCHESTRATOR READY")
