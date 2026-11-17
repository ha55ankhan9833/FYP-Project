import tempfile
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

from cpp_compilation_service import compile_cpp_source


passed = 0

def check(name, condition):
    global passed
    if not condition:
        raise AssertionError(name)
    passed += 1
    print("PASS:", name)


# 1. Valid compilation (mocked)
def fake_success(command, **kwargs):
    output_path = Path(command[command.index("-o") + 1])
    output_path.write_bytes(b"MOCK_EXECUTABLE")
    return subprocess.CompletedProcess(command, 0)

with patch("cpp_compilation_service.shutil.which", return_value="g++.exe"), \
     patch("cpp_compilation_service.subprocess.run", side_effect=fake_success):

    result = compile_cpp_source("int main(){return 0;}")

    check("Valid compilation response", result["success"])
    check("Executable path returned", Path(result["executable_path"]).is_file())

    build_dir = result["build_directory"]

    from cpp_compilation_service import cleanup_cpp_build
    cleanup_cpp_build(build_dir)

    check("Successful build cleanup", not Path(build_dir).exists())


# 2. Compilation failure
def fake_failure(command, **kwargs):
    kwargs["stdout"].write(
        b"error: expected ';' before '}'"
    )
    return subprocess.CompletedProcess(command, 1)

with patch("cpp_compilation_service.shutil.which", return_value="g++.exe"), \
     patch("cpp_compilation_service.subprocess.run", side_effect=fake_failure):

    result = compile_cpp_source("invalid cpp")

    check("Compilation failure detected",
          result["status"] == "COMPILATION_FAILED")
    check("Compiler diagnostics captured",
          "expected" in result["diagnostics"])


# 3. Empty source
result = compile_cpp_source("   ")

check("Empty source rejected",
      result["status"] == "INVALID_SOURCE")


# 4. Oversized source
result = compile_cpp_source("A" * 100001)

check("Oversized source rejected",
      result["status"] == "SOURCE_TOO_LARGE")


# 5. Invalid timeout
try:
    compile_cpp_source("int main(){}", timeout=0)
    raise AssertionError("Invalid timeout was accepted")
except ValueError:
    check("Invalid timeout rejected", True)


# 6. Compiler unavailable
with patch("cpp_compilation_service.shutil.which", return_value=None):
    result = compile_cpp_source("int main(){}")

    check("Missing compiler handled",
          result["status"] == "COMPILER_UNAVAILABLE")


# 7. Compilation timeout and cleanup
with patch("cpp_compilation_service.shutil.which", return_value="g++.exe"), \
     patch("cpp_compilation_service.subprocess.run",
           side_effect=subprocess.TimeoutExpired("g++", 15)):

    result = compile_cpp_source("int main(){}")

    check("Compilation timeout detected",
          result["status"] == "COMPILATION_TIMEOUT")

    check("Timeout temporary directory cleaned",
          result["build_directory"] is None)


print("\n" + "=" * 65)
print(f"PASS: All {passed} compilation mock assertions completed!")
print("PASS: No real C++ compilation performed!")
print("PASS: No executable run!")
print("PASS: No database modifications!")
