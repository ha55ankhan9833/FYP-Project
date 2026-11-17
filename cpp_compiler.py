import subprocess
from pathlib import Path


def compile_cpp(source_path, output_path, timeout=15):
    """
    Compile a trusted local C++ source file.
    Does not execute the compiled program.
    """

    source = Path(source_path).resolve()
    output = Path(output_path).resolve()

    if not source.is_file() or source.suffix.lower() != ".cpp":
        raise ValueError("Valid .cpp source file required")

    if output.suffix.lower() != ".exe":
        raise ValueError("Output must be an .exe file")

    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 < timeout <= 60
    ):
        raise ValueError("Timeout must be between 0 and 60 seconds")

    try:
        result = subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-O0",
                str(source),
                "-o",
                str(output)
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False
        )

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Compilation timed out",
            "output_path": None
        }

    except OSError as error:
        return {
            "success": False,
            "error": str(error),
            "output_path": None
        }

    return {
        "success": result.returncode == 0,
        "error": result.stderr,
        "output_path": (
            str(output) if result.returncode == 0 else None
        )
    }
