"""
tools/eda_runners.py: Layer 0 Deterministic EDA Execution Runners
==================================================================
Hardware Mental Model:
- ToolResult: Immutable UVM transaction packet (uvm_sequence_item)
- execute_cmd: Physical pin-level driver BFM with hardware watchdog timer
- make_sandbox: Partitioned scratchpad RAM address space
- validate_safe_path: Memory Protection Unit (MPU) containment check
"""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ==============================================================================
# SECTION 1: THE ToolResult TRANSACTION PACKET
# (Hardware equivalent: UVM Sequence Item / AXI Response Transaction)
# ==============================================================================

@dataclass(frozen=True)
class ToolResult:
    """
    Immutable telemetry packet representing the execution result of an EDA tool.
    Carries process exit codes, captured streams, generated artifacts, and parsed metrics.
    """
    command: List[str]
    returncode: Optional[int]
    stdout: str = ""
    stderr: str = ""
    timedout: bool = False
    generated_files: List[Path] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        """Analogous to checking (RESP == 2'b00 && !TIMEOUT) on an AXI bus."""
        return self.returncode == 0 and not self.timedout

    def summary(self) -> str:
        """Concise status string for logger or agent consumption."""
        tool = self.command[0] if self.command else "unknown"
        status = "TIMEOUT" if self.timedout else f"EXIT_{self.returncode}"
        metrics_str = f" | metrics={self.metrics}" if self.metrics else ""
        return f"[{tool}] {status} (ok={self.succeeded}){metrics_str}"


# ==============================================================================
# SECTION 2: SANDBOXING & MEMORY PROTECTION (MPU)
# ==============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKSPACE_BASE = PROJECT_ROOT / "workspaces"


def make_sandbox(base: Optional[Path] = None, tag: str = "run") -> Path:
    """
    Allocates an isolated scratchpad workspace.
    Guarantees no cross-contamination between parallel or sequential tool runs.
    """
    target_base = base or DEFAULT_WORKSPACE_BASE
    target_base.mkdir(parents=True, exist_ok=True)
    unique_tag = f"{tag}_{uuid.uuid4().hex[:8]}"
    run_dir = target_base / unique_tag
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def validate_safe_path(candidate: Path, allowed_root: Path) -> Path:
    """
    Hardware MPU analogy: Validates candidate address against permitted address range.
    Ensures relative directory traversals (e.g. '../../etc/passwd') or symlink attacks
    cannot read or clobber files outside the designated workspace.
    """
    resolved_candidate = candidate.resolve()
    resolved_root = allowed_root.resolve()
    if not resolved_candidate.is_relative_to(resolved_root):
        raise PermissionError(f"Access violation: {resolved_candidate} is outside {resolved_root}")
    return resolved_candidate


# ==============================================================================
# SECTION 3: DETERMINISTIC SUBPROCESS ENGINE (WATCHDOG DRIVER)
# ==============================================================================

def _to_text(value: Any) -> str:
    """Normalizes raw subprocess output bytes/None to UTF-8 text."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def execute_cmd(
    command: List[str],
    cwd: Path,
    timeout_s: float = 60.0,
    env: Optional[Dict[str, str]] = None,
) -> ToolResult:
    """
    Drives an external tool binary with strict timeout guardrails (watchdog timer).
    Uses shell=False to avoid command injection vulnerabilities.
    """
    # Merge custom environment with parent environment if provided
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            env=merged_env,
        )
        return ToolResult(
            command=command,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            timedout=False,
        )
    except FileNotFoundError as err:
        return ToolResult(
            command=command,
            returncode=None,
            stdout="",
            stderr=f"Executable not found on system: {err}",
            timedout=False,
        )
    except subprocess.TimeoutExpired as err:
        return ToolResult(
            command=command,
            returncode=None,
            stdout=_to_text(err.stdout),
            stderr=_to_text(err.stderr) + f"\nProcess aborted: watchdog timeout exceeded ({timeout_s}s)",
            timedout=True,
        )


# ==============================================================================
# SECTION 4: ICARUS VERILOG RUNNER (2-STAGE EXECUTION)
# ==============================================================================

def run_iverilog_simulation(
    sources: List[Path],
    top_module: Optional[str] = None,
    cwd: Optional[Path] = None,
    timeout_s: float = 30.0,
) -> ToolResult:
    """
    Executes a 2-stage Icarus simulation:
      1. Elaboration: iverilog -g2012 -o sim.vvp <sources>
      2. Simulation:  vvp sim.vvp
    Detects $fatal, $finish, and syntax errors.
    """
    workdir = cwd or make_sandbox(tag="iverilog")
    vvp_file = workdir / "sim.vvp"

    # Stage 1: Elaboration
    compile_cmd = ["iverilog", "-g2012", "-o", str(vvp_file)]
    if top_module:
        compile_cmd.extend(["-s", top_module])
    compile_cmd.extend([str(s) for s in sources])

    compile_res = execute_cmd(compile_cmd, cwd=workdir, timeout_s=timeout_s)
    if not compile_res.succeeded:
        return compile_res

    # Stage 2: Simulation Execution
    sim_cmd = ["vvp", str(vvp_file)]
    sim_res = execute_cmd(sim_cmd, cwd=workdir, timeout_s=timeout_s)

    combined_stdout = f"[STAGE 1 - COMPILATION]\n{compile_res.stdout}\n[STAGE 2 - SIMULATION]\n{sim_res.stdout}"
    combined_stderr = f"{compile_res.stderr}\n{sim_res.stderr}".strip()

    # Search for runtime assertion errors or $fatal markers
    has_fatal = bool(re.search(r"(\$fatal|Fatal Error|Assertion failed)", sim_res.stdout, re.IGNORECASE))
    final_returncode = 1 if has_fatal else sim_res.returncode

    # Inspect generated waveforms
    vcd_files = list(workdir.glob("*.vcd"))
    generated_artifacts = [vvp_file] + vcd_files

    metrics = {
        "simulation_finished": not sim_res.timedout and final_returncode == 0,
        "waveforms_generated": [p.name for p in vcd_files],
    }

    return ToolResult(
        command=sim_cmd,
        returncode=final_returncode,
        stdout=combined_stdout,
        stderr=combined_stderr,
        timedout=sim_res.timedout,
        generated_files=generated_artifacts,
        metrics=metrics,
    )


# ==============================================================================
# SECTION 5: VERILATOR LINT & COVERAGE RUNNER
# ==============================================================================

def run_verilator_lint(
    sources: List[Path],
    top_module: str,
    cwd: Optional[Path] = None,
    timeout_s: float = 30.0,
) -> ToolResult:
    """Runs Verilator static lint check on RTL sources."""
    workdir = cwd or make_sandbox(tag="verilator_lint")
    cmd = [
        "verilator",
        "--lint-only",
        "-Wall",
        "-Wno-DECLFILENAME",
        "-Wno-UNUSEDSIGNAL",
        "-Wno-UNDRIVEN",
        "--top-module",
        top_module,
    ] + [str(s) for s in sources]

    res = execute_cmd(cmd, cwd=workdir, timeout_s=timeout_s)

    # Parse warning and error counts
    warnings = len(re.findall(r"%Warning", res.stderr))
    errors = len(re.findall(r"%Error", res.stderr))
    metrics = {
        "warning_count": warnings,
        "error_count": errors,
        "lint_passed": res.succeeded and errors == 0,
    }

    return ToolResult(
        command=cmd,
        returncode=res.returncode,
        stdout=res.stdout,
        stderr=res.stderr,
        timedout=res.timedout,
        generated_files=[],
        metrics=metrics,
    )


def run_verilator_coverage(
    sources: List[Path],
    tb_cxx: Path,
    top_module: str,
    cwd: Optional[Path] = None,
    timeout_s: float = 60.0,
) -> ToolResult:
    """
    Compiles RTL with Verilator with code coverage enabled (--coverage),
    executes simulation, and parses line/toggle coverage metrics.
    """
    workdir = cwd or make_sandbox(tag="verilator_cov")
    bin_name = "Vsim"

    # Step 1: Compile with coverage instrumentation
    build_cmd = [
        "verilator",
        "--binary",
        "--coverage",
        "-Wall",
        "-Wno-DECLFILENAME",
        "--top-module",
        top_module,
        "-o",
        bin_name,
    ] + [str(s) for s in sources] + [str(tb_cxx)]

    build_res = execute_cmd(build_cmd, cwd=workdir, timeout_s=timeout_s)
    if not build_res.succeeded:
        return build_res

    # Step 2: Execute compiled binary to produce coverage.dat
    sim_bin = workdir / "obj_dir" / bin_name
    sim_res = execute_cmd([str(sim_bin)], cwd=workdir, timeout_s=timeout_s)

    # Step 3: Run verilator_coverage report
    cov_dat = workdir / "coverage.dat"
    cov_artifacts = [sim_bin]
    if cov_dat.exists():
        cov_artifacts.append(cov_dat)

    cov_cmd = ["verilator_coverage", "--annotate-min", "1", "coverage.dat"]
    cov_res = execute_cmd(cov_cmd, cwd=workdir, timeout_s=timeout_s)

    # Step 4: Parse line and toggle coverage percentages
    # Verilator prints lines like: "Total coverage (12/15) 80.00%"
    cov_match = re.search(r"Total coverage\s*\(\d+/\d+\)\s*([\d\.]+)%", cov_res.stdout)
    coverage_pct = float(cov_match.group(1)) if cov_match else 0.0

    metrics = {
        "coverage_percentage": coverage_pct,
        "coverage_target_met": coverage_pct >= 90.0,
    }

    combined_stdout = f"[BUILD]\n{build_res.stdout}\n[SIM]\n{sim_res.stdout}\n[COVERAGE]\n{cov_res.stdout}"
    combined_stderr = f"{build_res.stderr}\n{sim_res.stderr}\n{cov_res.stderr}".strip()

    return ToolResult(
        command=build_cmd,
        returncode=sim_res.returncode,
        stdout=combined_stdout,
        stderr=combined_stderr,
        timedout=build_res.timedout or sim_res.timedout,
        generated_files=cov_artifacts,
        metrics=metrics,
    )


# ==============================================================================
# SECTION 6: YOSYS LOGIC SYNTHESIS RUNNER
# ==============================================================================

def run_yosys_synthesis(
    sources: List[Path],
    top_module: str,
    output_netlist: Optional[Path] = None,
    liberty_file: Optional[Path] = None,
    cwd: Optional[Path] = None,
    timeout_s: float = 60.0,
) -> ToolResult:
    """
    Synthesizes Verilog RTL into gate-level netlist using Yosys.
    Extracts cell count, wire count, and flip-flop usage statistics.
    """
    workdir = cwd or make_sandbox(tag="yosys_synth")
    netlist_out = output_netlist or (workdir / f"{top_module}_netlist.v")
    script_file = workdir / "synth.ys"

    # Build Yosys synthesis TCL command sequence
    lines: List[str] = []
    for s in sources:
        lines.append(f"read_verilog -sv {s.resolve()}")
    lines.append(f"hierarchy -check -top {top_module}")
    lines.append("proc; opt; fsm; opt; memory; opt; techmap; opt")

    if liberty_file:
        lines.append(f"dfflibmap -liberty {liberty_file.resolve()}")
        lines.append(f"abc -liberty {liberty_file.resolve()}")
        lines.append("clean")
    else:
        lines.append(f"synth -top {top_module}")

    lines.append("stat")
    lines.append(f"write_verilog -noattr {netlist_out.resolve()}")

    script_file.write_text("\n".join(lines) + "\n")

    cmd = ["yosys", "-s", str(script_file.resolve())]
    res = execute_cmd(cmd, cwd=workdir, timeout_s=timeout_s)

    # Parse synthesis statistics from Yosys log (supports both legacy and modern Yosys formats)
    cells_match = re.search(r"(?:Number of cells:\s+|^\s*)(\d+)\s+cells", res.stdout, re.MULTILINE)
    wires_match = re.search(r"(?:Number of wires:\s+|^\s*)(\d+)\s+wires", res.stdout, re.MULTILINE)
    dff_match = re.search(r"^\s*(\d+)\s+(\$_\S*DFF\S*|\S*dff\S*)", res.stdout, re.MULTILINE)
    chip_area_match = re.search(r"Chip area for top module '[^']+':\s*([\d\.]+)", res.stdout)

    num_cells = int(cells_match.group(1)) if cells_match else 0
    num_wires = int(wires_match.group(1)) if wires_match else 0
    dff_count = int(dff_match.group(1)) if dff_match else 0

    metrics = {
        "num_cells": num_cells,
        "num_wires": num_wires,
        "dff_count": dff_count,
        "chip_area": float(chip_area_match.group(1)) if chip_area_match else None,
        "netlist_generated": netlist_out.exists(),
    }

    generated = [script_file]
    if netlist_out.exists():
        generated.append(netlist_out)

    return ToolResult(
        command=cmd,
        returncode=res.returncode,
        stdout=res.stdout,
        stderr=res.stderr,
        timedout=res.timedout,
        generated_files=generated,
        metrics=metrics,
    )


# ==============================================================================
# SECTION 7: OPENSTA STATIC TIMING ANALYSIS RUNNER
# ==============================================================================

def run_opensta_timing(
    liberty_file: Path,
    netlist_file: Path,
    sdc_file: Path,
    top_module: str,
    cwd: Optional[Path] = None,
    timeout_s: float = 60.0,
) -> ToolResult:
    """
    Executes signoff static timing analysis using OpenSTA.
    Extracts Worst Negative Slack (WNS setup/hold), Total Negative Slack (TNS),
    and validates timing closure.
    """
    workdir = cwd or make_sandbox(tag="sta_timing")
    script_file = workdir / "run_sta.tcl"

    sta_commands = f"""
read_liberty {liberty_file.resolve()}
read_verilog {netlist_file.resolve()}
link_design {top_module}
read_sdc {sdc_file.resolve()}
report_checks -path_delay min_max -fields {{slew cap input nets fanout}} -digits 4
report_worst_slack -max
report_worst_slack -min
report_tns
exit
"""
    script_file.write_text(sta_commands)

    cmd = ["sta", "-no_splash", "-exit", str(script_file.resolve())]
    res = execute_cmd(cmd, cwd=workdir, timeout_s=timeout_s)

    # Parse Slack Metrics
    # OpenSTA output format:
    # "worst slack 0.75" (from report_worst_slack -max)
    # "worst slack 0.43" (from report_worst_slack -min)
    # "tns 0.00"
    slack_matches = re.findall(r"worst slack(?:\s+-(?:max|min))?\s+([-+]?[\d\.]+)", res.stdout)
    tns_match = re.search(r"tns\s+([-+]?[\d\.]+)", res.stdout)

    wns_setup = float(slack_matches[0]) if len(slack_matches) >= 1 else None
    wns_hold = float(slack_matches[1]) if len(slack_matches) >= 2 else None
    tns = float(tns_match.group(1)) if tns_match else None

    # Timing is closed if setup slack >= 0 and hold slack >= 0
    met_timing = False
    if wns_setup is not None and wns_hold is not None:
        met_timing = (wns_setup >= 0.0) and (wns_hold >= 0.0)

    metrics = {
        "wns_setup_ns": wns_setup,
        "wns_hold_ns": wns_hold,
        "tns_ns": tns,
        "timing_closed": met_timing,
    }

    generated = [script_file]

    return ToolResult(
        command=cmd,
        returncode=res.returncode,
        stdout=res.stdout,
        stderr=res.stderr,
        timedout=res.timedout,
        generated_files=generated,
        metrics=metrics,
    )
