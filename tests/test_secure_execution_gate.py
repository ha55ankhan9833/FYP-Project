import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import fakes  # noqa: F401

import secure_execution_gate as gate


class GateTests(unittest.TestCase):
    def setUp(self):
        gate.register_secure_sandbox(None)
        gate._cached_docker_sandbox = None

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(gate.SANDBOX_ENV_VAR, None)
            self.assertIsNone(gate.get_secure_sandbox())
            self.assertFalse(
                gate.describe_sandbox_status()["student_execution_enabled"])

    def test_docker_mode_without_docker_stays_disabled(self):
        with patch.dict(os.environ, {gate.SANDBOX_ENV_VAR: "docker"}), \
             patch.object(gate.shutil, "which", return_value=None):
            self.assertIsNone(gate.get_secure_sandbox())


class DockerSandboxMockTests(unittest.TestCase):
    """Mocked only: no container, compiler or binary is ever run."""

    def test_compile_uses_isolation_flags_and_cleans_on_failure(self):
        sandbox = gate.DockerCppSandbox()
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            return subprocess.CompletedProcess(args, 1, b"",
                                               b"error: expected ';'")

        with patch.object(gate.subprocess, "run", side_effect=fake_run):
            r = sandbox.compile("int main(){")

        self.assertEqual(r["status"], "COMPILATION_FAILED")
        self.assertIsNone(r["artifact"])
        args = captured["args"]
        for flag in ("--network", "none", "--read-only", "--cap-drop",
                     "no-new-privileges", "--pids-limit", "--memory"):
            self.assertIn(flag, args)
        work_dir = Path(args[args.index("-v") + 1].split(":/work")[0])
        self.assertTrue(work_dir.name.startswith("fyp_sandbox_"))
        self.assertFalse(work_dir.exists(), "temp dir must be removed")

    def test_docker_exit_125_is_infrastructure_not_student(self):
        sandbox = gate.DockerCppSandbox()
        with patch.object(gate.subprocess, "run",
                          return_value=subprocess.CompletedProcess(
                              [], 125, b"", b"daemon error")):
            with self.assertRaises(gate.SandboxInfrastructureError):
                sandbox.compile("int main(){}")

    def test_compile_timeout_is_infrastructure(self):
        sandbox = gate.DockerCppSandbox()
        with patch.object(gate.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("docker", 30)):
            with self.assertRaises(gate.SandboxInfrastructureError):
                sandbox.compile("int main(){}")

    def test_run_maps_statuses(self):
        sandbox = gate.DockerCppSandbox()
        responses = [
            subprocess.CompletedProcess([], 0, b"2\n", b""),
            subprocess.CompletedProcess([], 1, b"", b""),
            subprocess.TimeoutExpired("docker", 7),
        ]

        kills = []

        def fake_run(args, **kwargs):
            if args[1] == "kill":
                kills.append(args[2])
                return subprocess.CompletedProcess(args, 0)
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        cases = [{"test_number": n, "input_data": "1\n"} for n in (1, 2, 3)]
        with patch.object(gate.subprocess, "run", side_effect=fake_run):
            r = sandbox.run_tests("unused", cases)

        self.assertEqual(len(kills), 1, "timed-out container must be killed")

        self.assertEqual([x["status"] for x in r],
                         ["completed", "runtime_error", "timeout"])
        self.assertEqual(r[0]["stdout"], "2\n")
        self.assertIsNone(r[1]["stdout"])

    def test_cleanup_refuses_foreign_paths(self):
        with self.assertRaises(ValueError):
            gate.DockerCppSandbox().cleanup("C:\\Windows")


if __name__ == "__main__":
    unittest.main()
