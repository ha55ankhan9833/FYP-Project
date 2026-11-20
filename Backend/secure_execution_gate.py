"""
Secure execution gate for untrusted student C++.

CURRENT STATUS: DISABLED (by design).

Host audit (2026-11-17, read-only):
- Win32_Processor.VirtualizationFirmwareEnabled = False
- Win32_ComputerSystem.HypervisorPresent        = False
- docker: not installed; Windows Sandbox: not installed

Without firmware virtualization none of Docker Desktop, WSL2,
Hyper-V or Windows Sandbox can run, and the remaining host-only
mechanisms (subprocess timeouts, Windows Job Objects, restricted
tokens) do not give adequate filesystem/network isolation for
hostile native code. Therefore get_secure_sandbox() returns None and
the grading worker refuses to claim jobs. Student submissions are
still accepted and queued as PENDING; nothing is executed.

To enable (requires owner approval for each step):
1. Enable Intel VT-x / AMD-V in BIOS/UEFI.
2. Install Docker Desktop (WSL2 backend) and pull a pinned GCC image,
   e.g.  docker pull gcc:13
3. Set environment variable FYP_SECURE_SANDBOX=docker for the worker.
4. Run the sandbox self-check and the adversarial test programs
   listed in BACKEND_INTEGRATION_REPORT.md before grading real work.

The DockerCppSandbox below is written against that design but is
UNVERIFIED on this machine: it has only been exercised with mocked
subprocess calls.

Sandbox contract (any implementation):
    compile(source_code) -> {"status": "COMPILED" | "COMPILATION_FAILED",
                             "diagnostics": str, "artifact": opaque}
        raises SandboxInfrastructureError for anything that is not
        a genuine compiler verdict (daemon down, timeout, image missing).
    run_tests(artifact, cases) -> [{"test_number", "status", "stdout"}]
        cases contain only test_number and input_data - expected
        outputs never enter the sandbox.
        status in {"completed", "timeout", "runtime_error"}
    cleanup(artifact) -> None
"""

import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from diagnostics_sanitizer import sanitize_compiler_diagnostics


class SandboxUnavailableError(RuntimeError):
    pass


class SandboxInfrastructureError(RuntimeError):
    """Failure of the grading infrastructure, never the student's fault."""


SANDBOX_ENV_VAR = "FYP_SECURE_SANDBOX"
DEFAULT_IMAGE = "gcc:13"
MAX_OUTPUT_BYTES = 64 * 1024
MAX_DIAGNOSTICS_CHARS = 20_000
# Docker reserves these exit codes for its own failures.
DOCKER_INFRA_EXIT_CODES = (125, 126, 127)


_registered_sandbox = None
_cached_docker_sandbox = None


def register_secure_sandbox(sandbox):
    """For tests or a future verified implementation. None to clear."""
    global _registered_sandbox
    if sandbox is not None and (getattr(sandbox, "synthetic", False) or
                                getattr(sandbox, "evidence_provenance", None)):
        raise ValueError("A synthetic or recorded development evidence source "
                         "can never be registered as the production sandbox")
    _registered_sandbox = sandbox


def get_secure_sandbox():
    """Return a verified sandbox instance, or None (execution disabled)."""
    global _cached_docker_sandbox

    if _registered_sandbox is not None:
        return _registered_sandbox

    if os.getenv(SANDBOX_ENV_VAR, "disabled").strip().lower() != "docker":
        return None

    if _cached_docker_sandbox is None:
        candidate = DockerCppSandbox()
        if not candidate.self_check():
            return None
        _cached_docker_sandbox = candidate

    return _cached_docker_sandbox


def describe_sandbox_status():
    sandbox = get_secure_sandbox()
    return {
        "student_execution_enabled": sandbox is not None,
        "sandbox": type(sandbox).__name__ if sandbox else None,
        "reason": None if sandbox else (
            "No verified isolated sandbox. Firmware virtualization is "
            "disabled on the host, so container/VM isolation is "
            "unavailable, and student C++ is never compiled or run. Jobs "
            "stay PENDING for execution-based grading; the opt-in LLM "
            "analysis worker may instead review the source code only "
            "(no test results), always pending teacher approval."
        )
    }


class DockerCppSandbox:
    """
    Container-isolated compile + run. UNVERIFIED on this host.

    Isolation per container: no network, read-only root filesystem,
    all capabilities dropped, no-new-privileges, unprivileged user,
    memory/CPU/PID limits, file-size limit, wall-clock timeout with
    forced kill, source mounted read-only for execution.
    """

    def __init__(self, image=DEFAULT_IMAGE, docker="docker"):
        self.image = image
        self.docker = docker

    # -- helpers ---------------------------------------------------

    def _base_args(self, name, memory, cpus, pids):
        return [
            self.docker, "run", "--rm", "--name", name,
            "--network", "none",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", "65534:65534",
            "--memory", memory, "--memory-swap", memory,
            "--cpus", cpus,
            "--pids-limit", str(pids),
            "--ulimit", "fsize=10485760",
            "--tmpfs", "/tmp:rw,size=64m",
        ]

    def _kill(self, name):
        try:
            subprocess.run(
                [self.docker, "kill", name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10, shell=False, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    # -- contract --------------------------------------------------

    def self_check(self):
        if shutil.which(self.docker) is None:
            return False
        try:
            info = subprocess.run(
                [self.docker, "image", "inspect", self.image],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15, shell=False, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return info.returncode == 0

    def compile(self, source_code, timeout=30):
        if not isinstance(source_code, str) or not source_code.strip():
            raise ValueError("Source code required")

        work = Path(tempfile.mkdtemp(prefix="fyp_sandbox_")).resolve()
        (work / "main.cpp").write_text(source_code, encoding="utf-8")
        name = f"fyp-compile-{uuid.uuid4().hex}"

        args = self._base_args(name, "512m", "1", 64) + [
            "-v", f"{work}:/work:rw", "-w", "/work",
            self.image,
            "g++", "-std=c++17", "-O0", "-fdiagnostics-color=never",
            "main.cpp", "-o", "main"
        ]

        try:
            result = subprocess.run(
                args, stdin=subprocess.DEVNULL,
                capture_output=True, timeout=timeout,
                shell=False, check=False
            )
        except subprocess.TimeoutExpired:
            self._kill(name)
            self.cleanup(work)
            raise SandboxInfrastructureError("Sandboxed compilation timed out")
        except OSError as error:
            self.cleanup(work)
            raise SandboxInfrastructureError(f"Sandbox launch failed: {error}")

        diagnostics = sanitize_compiler_diagnostics(
            (result.stdout + result.stderr).decode(
                "utf-8", errors="replace")[:MAX_DIAGNOSTICS_CHARS],
            str(work))

        if result.returncode in DOCKER_INFRA_EXIT_CODES:
            self.cleanup(work)
            raise SandboxInfrastructureError(
                f"Sandbox runtime error (exit {result.returncode})"
            )

        if result.returncode != 0 or not (work / "main").is_file():
            self.cleanup(work)
            return {
                "status": "COMPILATION_FAILED",
                "diagnostics": diagnostics,
                "artifact": None
            }

        return {
            "status": "COMPILED",
            "diagnostics": diagnostics,
            "artifact": str(work)
        }

    def run_tests(self, artifact, cases, timeout_per_test=2):
        work = Path(artifact).resolve()
        results = []

        for case in cases:
            name = f"fyp-run-{uuid.uuid4().hex}"
            args = self._base_args(name, "256m", "0.5", 32) + [
                "-i", "-v", f"{work}:/work:ro", "-w", "/work",
                self.image, "/work/main"
            ]

            try:
                proc = subprocess.run(
                    args,
                    input=case["input_data"].encode("utf-8"),
                    capture_output=True,
                    timeout=timeout_per_test + 5,   # container start-up
                    shell=False, check=False
                )
            except subprocess.TimeoutExpired:
                self._kill(name)
                results.append({
                    "test_number": case["test_number"],
                    "status": "timeout",
                    "stdout": None
                })
                continue
            except OSError as error:
                raise SandboxInfrastructureError(
                    f"Sandbox launch failed: {error}"
                )

            if proc.returncode in DOCKER_INFRA_EXIT_CODES:
                raise SandboxInfrastructureError(
                    f"Sandbox runtime error (exit {proc.returncode})"
                )

            if proc.returncode != 0:
                results.append({
                    "test_number": case["test_number"],
                    "status": "runtime_error",
                    "stdout": None
                })
                continue

            results.append({
                "test_number": case["test_number"],
                "status": "completed",
                "stdout": proc.stdout[:MAX_OUTPUT_BYTES].decode(
                    "utf-8", errors="replace"
                )
            })

        return results

    def cleanup(self, artifact):
        if not artifact:
            return
        path = Path(artifact).resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
        if path.parent != temp_root or not path.name.startswith("fyp_sandbox_"):
            raise ValueError("Unauthorized sandbox cleanup")
        shutil.rmtree(path, ignore_errors=True)
