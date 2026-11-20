import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from diagnostics_sanitizer import sanitize_compiler_diagnostics


MAX_SOURCE_BYTES = 100_000
MAX_DIAGNOSTICS_CHARS = 20_000
DEFAULT_TIMEOUT = 15


def compile_cpp_source(
    source_code,
    timeout=DEFAULT_TIMEOUT
):
    """
    Compile C++ source code using trusted local G++.

    Compilation only. This service does NOT execute
    student-generated executables.

    IMPORTANT:
    This is not a security sandbox. Compiler resource
    isolation is still required for hostile submissions.
    """

    if not isinstance(source_code, str) or not source_code.strip():
        return {
            "success": False,
            "status": "INVALID_SOURCE",
            "executable_path": None,
            "build_directory": None,
            "diagnostics": "Non-empty C++ source code required."
        }

    if len(source_code.encode("utf-8")) > MAX_SOURCE_BYTES:
        return {
            "success": False,
            "status": "SOURCE_TOO_LARGE",
            "executable_path": None,
            "build_directory": None,
            "diagnostics": "Source code exceeds size limit."
        }

    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 < timeout <= 30
    ):
        raise ValueError("Timeout must be between 0 and 30 seconds")

    compiler = shutil.which("g++")

    if not compiler:
        return {
            "success": False,
            "status": "COMPILER_UNAVAILABLE",
            "executable_path": None,
            "build_directory": None,
            "diagnostics": "G++ compiler not found."
        }

    build_directory = None

    try:
        build_directory = Path(
            tempfile.mkdtemp(prefix="fyp_cpp_build_")
        ).resolve()

        source_path = build_directory / "student_solution.cpp"
        executable_path = build_directory / "student_solution.exe"
        log_path = build_directory / "compiler.log"

        source_path.write_text(
            source_code,
            encoding="utf-8"
        )

        command = [
            compiler,
            "-std=c++17",
            "-O0",
            "-fdiagnostics-color=never",
            str(source_path),
            "-o",
            str(executable_path)
        ]

        try:
            with open(log_path, "wb") as log_file:
                result = subprocess.run(
                    command,
                    cwd=str(build_directory),
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    shell=False,
                    check=False
                )

        except subprocess.TimeoutExpired:
            diagnostics = "G++ compilation timed out."

            cleanup_cpp_build(build_directory)

            return {
                "success": False,
                "status": "COMPILATION_TIMEOUT",
                "executable_path": None,
                "build_directory": None,
                "diagnostics": diagnostics
            }

        with open(log_path, "rb") as log_file:
            diagnostics = log_file.read(
                MAX_DIAGNOSTICS_CHARS
            ).decode("utf-8", errors="replace")

        # Strip host paths (username, temp folder, toolchain location)
        # before the text leaves this function; raw text is not kept.
        diagnostics = sanitize_compiler_diagnostics(
            diagnostics, build_directory)

        log_path.unlink(missing_ok=True)

        if result.returncode != 0 or not executable_path.is_file():
            cleanup_cpp_build(build_directory)

            return {
                "success": False,
                "status": "COMPILATION_FAILED",
                "executable_path": None,
                "build_directory": None,
                "diagnostics": diagnostics
            }

        return {
            "success": True,
            "status": "COMPILED",
            "executable_path": str(executable_path),
            "build_directory": str(build_directory),
            "diagnostics": diagnostics
        }

    except OSError as error:
        if build_directory is not None:
            cleanup_cpp_build(build_directory)

        return {
            "success": False,
            "status": "COMPILATION_ERROR",
            "executable_path": None,
            "build_directory": None,
            "diagnostics": sanitize_compiler_diagnostics(
                str(error), build_directory)
        }


def cleanup_cpp_build(build_directory):
    """
    Remove only directories created by this service
    directly inside the system temporary directory.
    """

    if not isinstance(build_directory, (str, Path)):
        raise ValueError("Invalid build directory")

    path = Path(build_directory).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()

    if (
        path.parent != temp_root
        or not path.name.startswith("fyp_cpp_build_")
    ):
        raise ValueError("Unauthorized build directory cleanup")

    if path.exists():
        shutil.rmtree(path)