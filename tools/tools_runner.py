import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------
# Section 1: the response bus
# ---------------------------------------------------------------
@dataclass(frozen=True)
class ToolResult:
    command: List[str]                       # what we ran, one argument per item
    returncode: Optional[int]                # exit code; None if no process finished normally
    stdout: str = ""                         # text from file descriptor 1
    stderr: str = ""                         # text from file descriptor 2
    timedout: bool = False                   # True if we had to kill it
    generatedfiles: List[Path] = field(default_factory=list)  # fresh list per packet

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and not self.timedout


# ---------------------------------------------------------------
# Section 2: sandbox and input validation
# ---------------------------------------------------------------
def make_run_dir(base: Path, tag: str) -> Path:
    """Create a brand-new, uniquely named run folder under base."""
    unique = uuid.uuid4().hex[:8]
    run_dir = base / f"{tag}_{unique}"
    run_dir.mkdir(parents=True, exist_ok=False)   # must be new: error if it exists
    return run_dir


def validate_input(candidate: Path, allowed_root: Path) -> Path:
    """Return the resolved path if it is an existing file inside allowed_root."""
    real = candidate.resolve()            # absolute, '..' removed, symlinks followed
    root = allowed_root.resolve()
    if not real.is_relative_to(root):     # containment first: leaks nothing
        raise PermissionError(f"outside allowed folder: {real}")
    if not real.is_file():
        raise FileNotFoundError(f"no such file: {real}")
    return real


# ---------------------------------------------------------------
# Section 3: safe subprocess execution
# ---------------------------------------------------------------
def _to_text(value) -> str:
    """Normalise captured output (None, bytes, or str) into a str."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_tool(command: List[str], cwd: Path, timeout_s: float) -> ToolResult:
    try:
        done = subprocess.run(command, cwd=cwd, capture_output=True,
                              text=True, timeout=timeout_s, check=False)
    except FileNotFoundError as e:
        # Binary not installed: no process ever existed, so no exit code.
        return ToolResult(command=command, returncode=None, stderr=str(e))
    except subprocess.TimeoutExpired as e:
        # Ran, hung, we killed it: no exit code, timedout set.
        return ToolResult(command=command, returncode=None, timedout=True,
                          stdout=_to_text(e.stdout), stderr=_to_text(e.stderr))
    return ToolResult(command=command, returncode=done.returncode,
                      stdout=done.stdout, stderr=done.stderr)


# ---------------------------------------------------------------
# Standalone tests (skipped when this file is imported)
# ---------------------------------------------------------------
if __name__ == "__main__":
    # Section 1 tests
    r = ToolResult(command=["iverilog", "-v"], returncode=0)
    print(r.succeeded)                                 # True
    r = ToolResult(command=["yosys"], returncode=None, timedout=True)
    print(r.succeeded)                                 # False
    r3 = ToolResult(command=["yosys"], returncode=2)
    print(r3.succeeded)                                # False
    try:
        r3.returncode = 0
    except Exception as e:
        print(type(e).__name__)                        # FrozenInstanceError

    # Section 2 tests
    root = Path("/tmp/copilot")
    d = make_run_dir(root, "alu")
    print(d, d.is_dir())                               # True

    f = d / "alu.v"
    f.write_text("module alu; endmodule")
    print(validate_input(f, root))                     # inside: returns the path

    link = d / "sneaky.v"
    link.symlink_to("/etc/passwd")

    attacks = {
        "outside": Path("/etc/passwd"),
        "missing": d / "missing.v",
        "dot-dot": root / ".." / "etc" / "passwd",
        "symlink": link,
    }
    for name, path in attacks.items():
        try:
            print(name, validate_input(path, root))
        except Exception as e:
            print(name, type(e).__name__, e)

    # Section 3 tests
    r = run_tool(["echo", "hello"], d, 5)
    print(r.returncode, repr(r.stdout), r.succeeded)           # 0 'hello\n' True

    r = run_tool(["ls", "/nonexistent"], d, 5)
    print(r.returncode, r.succeeded, repr(r.stderr))           # 2 False '...'

    r = run_tool(["not_a_real_tool"], d, 5)
    print(r.returncode, r.timedout, r.succeeded)               # None False False
    print(r.stderr)

    r = run_tool(["sleep", "5"], d, 1)
    print(r.returncode, r.timedout, r.succeeded)               # None True False

    r = run_tool(["sh", "-c", "echo started; sleep 5"], d, 1)  # experiment only
    print(repr(r.stdout), r.timedout)                          # does 'started' survive?