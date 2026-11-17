# Docker-free student C++ execution — isolation assessment (2026-11-17)

**Decision: student execution stays DISABLED.** No sandbox was implemented
or activated in this phase. `secure_execution_gate.get_secure_sandbox()`
still returns `None`, so the grading worker claims no jobs.

## Host facts (read-only checks)
| Item | Value |
|---|---|
| OS | Windows 11 Pro, build 26300 |
| Firmware virtualization | disabled (rules out Hyper-V, Windows Sandbox, WSL2, Docker) |
| Session elevation | not elevated |
| `pywin32` in `.venv` | not installed |
| Compiler | WinLibs MinGW-w64 g++ on PATH |

## Threat model
Student source is hostile. Both **compiling** it (g++ parses attacker
input) and **running** the result must be contained. Assets to protect:
`.env` (JWT secret), the project directory, SQL Server (Windows
integrated auth means any process running as this user can connect),
Flask on 127.0.0.1:5000, Ollama on 127.0.0.1:11434, other users' files,
and host availability (CPU, memory, disk, process table).

The key fact: **any process started normally as the current user inherits
that user's SQL Server login and file access.** Timeouts and resource
limits alone (the old `cpp_runner.py`) protect availability only, not data.

## Options considered
| Mechanism | What it gives | What it does not give |
|---|---|---|
| Job Object | memory and CPU-time limits, active-process limit, kill-on-close, process-tree termination | no file, network or identity isolation |
| Restricted token / low integrity | blocks writes to medium-integrity locations | reads are still allowed; network still allowed |
| **AppContainer + Job Object** | no network or loopback (no capabilities granted), file access only where explicitly granted, low integrity, combined with Job limits. The model used by browser renderer sandboxes | shares the host kernel; can read anything granted to ALL APPLICATION PACKAGES |
| Dedicated low-privilege local account | separate identity, no SQL login | needs account creation (forbidden without approval); no network isolation by itself |
| VM / container | separate kernel | not available on this host |

AppContainer combined with a Job Object is the strongest Docker-free option and is
supported on Windows 11 without virtualization.

## Why it is not enabled now
1. **Not yet verified.** Saying it is "adequate for untrusted code" needs
   adversarial verification. I will not run that against your everyday
   machine, which holds your database and credentials. It should run on a
   disposable machine.
2. **Shared kernel.** Unlike a VM, a Windows kernel or driver vulnerability
   is a direct escape path. That residual risk must be accepted explicitly.
3. **Readable system locations.** AppContainers can read paths granted to
   ALL APPLICATION PACKAGES. I must audit that the project folder, `.env`,
   SQL Server data and the MinGW install are not readable, or are safe.
4. **Toolchain unknowns.** It is unverified whether MinGW g++ and its
   runtime DLLs work inside an AppContainer with no added capabilities.
5. **Dependencies.** A maintainable implementation uses `pywin32` (install
   needs your approval) or substantial hand-written Win32 `ctypes` code.

## Practical paths (each needs your decision)
- **A. External judge (recommended for an FYP):** a separate low-value
  machine or a managed cloud runner executes code. Here, only structured
  evidence is returned to the existing gate interface.
- **B. AppContainer + Job sandbox on this host:** implement it disabled by
  default and verify it on a disposable Windows machine first. Then record
  the shared-kernel risk acceptance before enabling it here.
- **C. Enable firmware virtualization** (excluded by your current rules),
  which unlocks the existing, unverified `DockerCppSandbox`.

## Integration contract (unchanged, already enforced)
Any sandbox must implement `compile` / `run_tests` / `cleanup` from
`secure_execution_gate.py`. It must return only structured evidence
(status, stdout) and receive only test inputs, never expected outputs. It
must report infrastructure faults as `SandboxInfrastructureError`, which
the pipeline releases without a zero. The pipeline already enforces:
exactly 10 tests, the equal-weight score, Python verification, refusal of
synthetic evidence, job ownership and leases, and mandatory teacher review.

## Steps that need your approval (for path B)
1. Install `pywin32` into `.venv`.
2. Let me implement `appcontainer_sandbox.py` behind the gate, disabled by
   default, tested with mocks and trusted developer programs only.
3. Provide a disposable Windows machine for isolation verification.
4. Review the verification results and the residual-risk statement.
5. Enable it with an explicit configuration flag.
6. Run one controlled end-to-end submission: an authorized student
   identity in an enrolled section, Assessment 5 assigned to SEC000001.
