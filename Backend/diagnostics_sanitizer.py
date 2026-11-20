"""
Remove host filesystem details from compiler diagnostics.

Raw g++ output contains absolute paths such as
    C:\\Users\\<name>\\AppData\\Local\\Temp\\fyp_cpp_build_x1y2\\student_solution.cpp:5:5: error: ...
    In file included from C:/Users/<name>/.../mingw64/include/c++/16.2.0/iostream:41,
which reveal the Windows username, toolchain location and build layout.

Rules
- The submission's own file (any absolute path ending in student_solution.*,
  or the known build directory + filename, or a sandbox /work/main.*) ->
  bare "student_solution.<ext>". Line and column numbers are kept.
- Any other absolute path (toolchain headers, project folders, UNC shares,
  POSIX system paths) -> "<toolchain>/<filename>", keeping the filename
  so "In file included from <toolchain>/iostream:41" stays meaningful.
- Lines that echo the student's own source ("    5 | code" and the caret
  line under it) are left untouched: they contain only what the student
  wrote, not host data.
- Idempotent; never raises; never shortens the error text itself.

Raw diagnostics are not stored anywhere: every producer sanitizes before
returning, and every persistence/response point sanitizes again.
"""

import re

SUBMISSION_STEM = "student_solution"
TOOLCHAIN = "<toolchain>"

# One path component: anything but separators, quotes, wildcard/colon chars
# and line breaks. Spaces are allowed ("D:\Fyp Project\Backend").
_COMPONENT = r'[^\\/:*?"<>|\r\n]+'
_WINDOWS_PATH = re.compile(
    rf'(?:(?<![A-Za-z0-9])[A-Za-z]:|\\\\{_COMPONENT}\\{_COMPONENT})'
    rf'(?:[\\/]{_COMPONENT})+')
_POSIX_PATH = re.compile(
    r'(?<![\w.~])/(?:tmp|work|home|usr|opt|var|root|mnt|Users|private|build)'
    r'(?:/[^\s:\'"<>|]+)+')
_SOURCE_ECHO = re.compile(r'^\s*\d*\s*\|')


def _replacement(path):
    name = re.split(r"[\\/]", path.rstrip("\\/"))[-1]
    stem, dot, ext = name.partition(".")
    if stem == SUBMISSION_STEM or (stem == "main" and path.startswith("/work/")):
        return f"{SUBMISSION_STEM}.{ext or 'cpp'}"
    return f"{TOOLCHAIN}/{name}"


def _sanitize_line(line, build_directory):
    if _SOURCE_ECHO.match(line):
        return line
    if build_directory:
        # Exact build folder first (handles either slash direction/case).
        variants = {build_directory, build_directory.replace("\\", "/")}
        for variant in variants:
            line = re.sub(re.escape(variant) + r"[\\/]+(" + SUBMISSION_STEM + r"\.\w+)",
                          r"\1", line, flags=re.I)
            line = re.sub(re.escape(variant), "<build>", line, flags=re.I)
    line = _WINDOWS_PATH.sub(lambda m: _replacement(m.group(0)), line)
    line = _POSIX_PATH.sub(lambda m: _replacement(m.group(0)), line)
    return line


def sanitize_compiler_diagnostics(text, build_directory=None):
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""
    build = str(build_directory).rstrip("\\/") if build_directory else None
    return "\n".join(_sanitize_line(line, build) for line in text.split("\n"))


def contains_host_path(text):
    """True if any absolute Windows/UNC or sensitive POSIX path remains."""
    if not isinstance(text, str):
        return False
    for line in text.split("\n"):
        if _SOURCE_ECHO.match(line):
            continue
        if _WINDOWS_PATH.search(line) or _POSIX_PATH.search(line):
            return True
    return False
