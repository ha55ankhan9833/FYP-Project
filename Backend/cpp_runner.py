import subprocess
from pathlib import Path


def run_trusted_cpp(executable_path, input_data, timeout=2):
    """
    Run ONLY a trusted local C++ executable.

    WARNING:
    This is NOT a security sandbox.
    Never use for untrusted or uploaded C++ programs.
    """

    executable = Path(executable_path).resolve()

    if not executable.is_file() or executable.suffix.lower() != ".exe":
        raise ValueError("Valid executable required")

    if not isinstance(input_data, str):
        raise ValueError("Input data must be text")

    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 < timeout <= 10
    ):
        raise ValueError("Timeout must be between 0 and 10 seconds")

    try:
        result = subprocess.run(
            [str(executable)],
            input=input_data,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False
        )

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "expected_output": None,
            "error": "Execution timed out"
        }

    except OSError as error:
        return {
            "success": False,
            "expected_output": None,
            "error": str(error)
        }

    if result.returncode != 0:
        return {
            "success": False,
            "expected_output": None,
            "error": "Program exited with a non-zero status"
        }

    return {
        "success": True,
        "expected_output": result.stdout,
        "error": None
    }
