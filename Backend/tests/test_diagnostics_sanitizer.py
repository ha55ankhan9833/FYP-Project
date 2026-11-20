"""
Compiler-diagnostics sanitization. No compiler runs (subprocess mocked),
no database (fake connections).
"""

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tests.fakes import FakeConnection

import compilation_failure_evaluation_service as cfes
import cpp_compilation_service as ccs
import grading_compilation_failure_completion as gcfc
from compilation_contract import to_sandbox_compile_result
from diagnostics_sanitizer import contains_host_path, sanitize_compiler_diagnostics as s
from evaluation_review_service import student_safe_feedback
from grading_job_failure_service import release_job_after_infrastructure_failure

REAL = (  # captured from the approved compile on 2026-11-17
    "C:\\Users\\Lenovo\\AppData\\Local\\Temp\\fyp_cpp_build_w2ewyvr_\\student_solution.cpp: "
    "In function 'int main()':\n"
    "C:\\Users\\Lenovo\\AppData\\Local\\Temp\\fyp_cpp_build_w2ewyvr_\\student_solution.cpp:5:5: "
    "error: expected ',' or ';' before 'std'\n"
    "    5 |     std::cout << value << '\\n';\n"
    "      |     ^~~")


class SanitizerTests(unittest.TestCase):
    def test_real_diagnostics(self):
        out = s(REAL)
        self.assertEqual(out.splitlines()[0],
                         "student_solution.cpp: In function 'int main()':")
        self.assertEqual(out.splitlines()[1],
                         "student_solution.cpp:5:5: error: expected ',' or ';' "
                         "before 'std'")
        self.assertEqual(out.splitlines()[2:], REAL.splitlines()[2:])  # echo kept
        for secret in ("Lenovo", "AppData", "fyp_cpp_build_", "C:\\"):
            self.assertNotIn(secret, out)
        self.assertFalse(contains_host_path(out))

    def test_different_temp_directory_names_and_slash_styles(self):
        for path in (r"C:\Users\Alice\AppData\Local\Temp\fyp_cpp_build_ab12\student_solution.cpp",
                     r"C:\Users\Bob Smith\AppData\Local\Temp\tmpq9x\student_solution.cpp",
                     "C:/Users/Alice/AppData/Local/Temp/fyp_cpp_build_zz/student_solution.cpp",
                     r"d:\builds\run-77\student_solution.cpp",
                     r"\\fileserver\share\jobs\42\student_solution.cpp"):
            with self.subTest(path=path):
                out = s(f"{path}:12:3: warning: unused variable 'x' [-Wunused-variable]")
                self.assertEqual(out, "student_solution.cpp:12:3: warning: unused "
                                      "variable 'x' [-Wunused-variable]")

    def test_known_build_directory_with_unusual_name(self):
        build = r"C:\Users\Lenovo\AppData\Local\Temp\custom build (1)"
        text = f"{build}\\student_solution.cpp:3:1: error: 'foo' was not declared"
        self.assertEqual(s(text, build),
                         "student_solution.cpp:3:1: error: 'foo' was not declared")

    def test_multiline_with_include_chain_and_template_notes(self):
        tc = "C:/Users/Lenovo/AppData/Local/Microsoft/WinGet/Packages/WinLibs/mingw64"
        text = "\n".join([
            f"In file included from {tc}/include/c++/16.2.0/bits/stl_algobase.h:64,",
            f"                 from {tc}/include/c++/16.2.0/vector:62,",
            r"                 from C:\Users\Lenovo\AppData\Local\Temp\fyp_cpp_build_x\student_solution.cpp:2:",
            f"{tc}/include/c++/16.2.0/bits/stl_pair.h: In instantiation of 'struct std::pair<int, X>':",
            r"C:\Users\Lenovo\AppData\Local\Temp\fyp_cpp_build_x\student_solution.cpp:9:22:   required from here",
            "    9 |     std::pair<int, X> p;",
            "      |                      ^",
            f"{tc}/include/c++/16.2.0/bits/stl_pair.h:302:17: error: 'std::pair<_T1, _T2>::second' has incomplete type",
            "compilation terminated."])
        out = s(text)
        self.assertEqual(out.splitlines(), [
            "In file included from <toolchain>/stl_algobase.h:64,",
            "                 from <toolchain>/vector:62,",
            "                 from student_solution.cpp:2:",
            "<toolchain>/stl_pair.h: In instantiation of 'struct std::pair<int, X>':",
            "student_solution.cpp:9:22:   required from here",
            "    9 |     std::pair<int, X> p;",
            "      |                      ^",
            "<toolchain>/stl_pair.h:302:17: error: 'std::pair<_T1, _T2>::second' has "
            "incomplete type",
            "compilation terminated."])
        self.assertFalse(contains_host_path(out))

    def test_student_source_echo_is_not_altered(self):
        echo = '   14 |     std::ifstream in("C:\\\\data\\\\input.txt");'
        text = (r"C:\Temp\fyp_cpp_build_a\student_solution.cpp:14:5: error: 'ifstream' "
                "is not a member of 'std'\n" + echo + "\n      |     ^~~~~~~~")
        out = s(text).splitlines()
        self.assertEqual(out[0], "student_solution.cpp:14:5: error: 'ifstream' is "
                                 "not a member of 'std'")
        self.assertEqual(out[1], echo)

    def test_sandbox_posix_paths(self):
        out = s("/work/main.cpp:4:3: error: x\n"
                "In file included from /usr/include/c++/13/iostream:39,\n"
                "/tmp/ccA1b2.o: undefined reference to `foo()'")
        self.assertEqual(out.splitlines(), [
            "student_solution.cpp:4:3: error: x",
            "In file included from <toolchain>/iostream:39,",
            "<toolchain>/ccA1b2.o: undefined reference to `foo()'"])

    def test_linker_and_os_error_paths(self):
        out = s(r"C:/Users/Lenovo/AppData/Local/Microsoft/WinGet/x/mingw64/bin/../lib/gcc/"
                r"x86_64-w64-mingw32/16.2.0/../../../../x86_64-w64-mingw32/bin/ld.exe: "
                r"C:\Users\Lenovo\AppData\Local\Temp\ccXyZ.o:student_solution.cpp:"
                "(.text+0x1a): undefined reference to `helper()'")
        self.assertNotIn("Lenovo", out)
        self.assertIn("undefined reference to `helper()'", out)
        self.assertIn("<toolchain>/ld.exe", out)
        self.assertEqual(s("[WinError 5] Access is denied: 'C:\\Users\\Lenovo\\x'"),
                         "[WinError 5] Access is denied: '<toolchain>/x'")

    def test_safe_text_is_unchanged_and_idempotent(self):
        plain = ("student_solution.cpp:5:5: error: expected ';' before 'return'\n"
                 "ratio 3:2 and time 12:30 and URL-like a/b/c stay")
        self.assertEqual(s(plain), plain)
        self.assertEqual(s(s(REAL)), s(REAL))
        self.assertEqual(s(""), "")
        self.assertEqual(s(None), "")


def mocked_compile(diag_text):
    def fake(command, **kwargs):
        build = Path(command[-1]).parent
        kwargs["stdout"].write(diag_text.replace("{BUILD}", str(build)).encode())
        return subprocess.CompletedProcess(command, 1)
    return fake


class ProducerTests(unittest.TestCase):
    def test_compile_service_returns_sanitized_diagnostics(self):
        raw = "{BUILD}\\student_solution.cpp:5:5: error: expected ';' before 'std'"
        with patch.object(ccs.shutil, "which", return_value="g++.exe"), \
             patch.object(ccs.subprocess, "run", side_effect=mocked_compile(raw)):
            result = ccs.compile_cpp_source("int main(){ int x = 1 return 0; }")
        self.assertEqual(result["status"], "COMPILATION_FAILED")
        self.assertEqual(result["diagnostics"],
                         "student_solution.cpp:5:5: error: expected ';' before 'std'")

    def test_success_and_failure_behaviour_preserved(self):
        def ok(command, **kwargs):
            Path(command[-1]).write_bytes(b"EXE")
            kwargs["stdout"].write(
                f"{Path(command[-1]).parent}\\student_solution.cpp:1:1: warning: x".encode())
            return subprocess.CompletedProcess(command, 0)
        with patch.object(ccs.shutil, "which", return_value="g++.exe"), \
             patch.object(ccs.subprocess, "run", side_effect=ok):
            result = ccs.compile_cpp_source("int main(){}")
        try:
            self.assertEqual(result["status"], "COMPILED")
            self.assertTrue(Path(result["executable_path"]).is_file())
            self.assertEqual(result["diagnostics"], "student_solution.cpp:1:1: warning: x")
            self.assertEqual(to_sandbox_compile_result(result)["status"], "COMPILED")
        finally:
            ccs.cleanup_cpp_build(result["build_directory"])

    def test_contract_sanitizes_defensively(self):
        mapped = to_sandbox_compile_result({"success": False,
                                            "status": "COMPILATION_FAILED",
                                            "diagnostics": REAL})
        self.assertFalse(contains_host_path(mapped["diagnostics"]))


class PersistenceTests(unittest.TestCase):
    def capture_insert(self, responder_rows):
        inserted = {}

        def responder(sql, params):
            if sql.startswith("INSERT INTO dbo.Evaluation"):
                inserted["diagnostics"] = params[1]
                return [], 1
            if sql.startswith("UPDATE dbo.GradingJob"):
                return [], 1
            return responder_rows(sql), -1
        return FakeConnection(responder), inserted

    def test_worker_completion_persists_sanitized_text(self):
        conn, inserted = self.capture_insert(
            lambda sql: [(21,)] if "FROM dbo.GradingJob" in sql else [])
        with patch.object(gcfc, "get_connection", return_value=conn):
            gcfc.complete_compilation_failure_job(
                5, "w1", str(uuid4()),
                {"success": False, "status": "COMPILATION_FAILED", "diagnostics": REAL})
        self.assertFalse(contains_host_path(inserted["diagnostics"]))
        self.assertIn("student_solution.cpp:5:5: error", inserted["diagnostics"])

    def test_legacy_evaluation_service_persists_sanitized_text(self):
        conn, inserted = self.capture_insert(
            lambda sql: [(21,)] if "FROM dbo.Submission" in sql else [])
        with patch.object(cfes, "get_connection", return_value=conn):
            cfes.save_compilation_failure_evaluation(
                21, {"success": False, "status": "COMPILATION_FAILED",
                     "diagnostics": REAL})
        self.assertFalse(contains_host_path(inserted["diagnostics"]))

    def test_job_last_error_sanitized(self):
        captured = {}

        def responder(sql, params):
            captured["message"] = params[1]
            return [(1, 2, "PENDING", 1)], 1
        release_job_after_infrastructure_failure(
            1, "w1", str(uuid4()), "SANDBOX_COMPILE_FAILURE",
            r"[WinError 2] cannot find C:\Users\Lenovo\AppData\Local\Temp\fyp_x\student_solution.cpp",
            connection_factory=lambda: FakeConnection(responder))
        self.assertNotIn("Lenovo", captured["message"])
        self.assertIn("student_solution.cpp", captured["message"])


class StudentViewTests(unittest.TestCase):
    def test_older_unsanitized_rows_are_cleaned_for_students(self):
        out = student_safe_feedback(REAL, [])
        self.assertFalse(contains_host_path(out))
        self.assertIn("student_solution.cpp:5:5: error: expected ',' or ';'", out)


if __name__ == "__main__":
    unittest.main()
