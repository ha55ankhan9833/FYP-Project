"""
Maps cpp_compilation_service.compile_cpp_source() results onto the
secure_execution_gate sandbox contract used by grading_pipeline.

Pure function: it compiles nothing and runs nothing. It is NOT wired into
the gate - host compilation of student code stays disabled. It exists so
that any future isolated compiler that reuses compile_cpp_source's result
format is classified correctly:

  COMPILED            -> {"status": "COMPILED", artifact}   tests may run
  COMPILATION_FAILED  -> {"status": "COMPILATION_FAILED"}   genuine compiler
                         verdict: pipeline records 0 test marks, no LLM call
  anything else       -> SandboxInfrastructureError          job released,
                         never a zero (compiler missing, timeout, OS error,
                         rejected input that should have been stopped at
                         submission time)
"""

from diagnostics_sanitizer import sanitize_compiler_diagnostics
from secure_execution_gate import SandboxInfrastructureError

STUDENT_VERDICTS = {"COMPILED", "COMPILATION_FAILED"}
INFRASTRUCTURE_STATUSES = {"COMPILER_UNAVAILABLE", "COMPILATION_TIMEOUT",
                           "COMPILATION_ERROR", "INVALID_SOURCE",
                           "SOURCE_TOO_LARGE"}


def to_sandbox_compile_result(result):
    if not isinstance(result, dict):
        raise SandboxInfrastructureError("Malformed compilation result")

    status = result.get("status")
    success = result.get("success")

    if status == "COMPILED":
        if success is not True or not result.get("build_directory") \
                or not result.get("executable_path"):
            raise SandboxInfrastructureError("Inconsistent COMPILED result")
        return {"status": "COMPILED",
                "diagnostics": sanitize_compiler_diagnostics(
                    result.get("diagnostics") or ""),
                "artifact": result["build_directory"]}

    if status == "COMPILATION_FAILED":
        if success is not False:
            raise SandboxInfrastructureError("Inconsistent COMPILATION_FAILED")
        return {"status": "COMPILATION_FAILED",
                "diagnostics": sanitize_compiler_diagnostics(
                    result.get("diagnostics") or ""),
                "artifact": None}

    if status in INFRASTRUCTURE_STATUSES:
        raise SandboxInfrastructureError(
            f"Compilation infrastructure status {status}")

    raise SandboxInfrastructureError(f"Unknown compilation status {status!r}")
