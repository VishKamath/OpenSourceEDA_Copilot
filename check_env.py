#!/usr/bin/env python3
"""
Milestone 0: Environment Diagnostics & EDA Binary Path Resolution
Validates host toolchain readiness and python environment dependencies.
"""

import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# Color codes for terminal reporting
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def probe_eda_binaries(required_tools: List[str]) -> Dict[str, Tuple[bool, str]]:
    """
    Scans the system PATH bus (analogous to MMIO address decoding)
    to confirm target EDA tool peripherals are present and executable.
    
    Supports environment variable overrides: <TOOLNAME_UPPER>_BIN (e.g. STA_BIN)
    """
    results: Dict[str, Tuple[bool, str]] = {}
    for tool in required_tools:
        # Check explicit env override first (e.g., STA_BIN)
        env_override = os.environ.get(f"{tool.upper()}_BIN")
        if env_override and Path(env_override).is_file() and os.access(env_override, os.X_OK):
            results[tool] = (True, env_override)
            continue

        resolved = shutil.which(tool)
        if resolved:
            results[tool] = (True, resolved)
        else:
            results[tool] = (False, "NOT FOUND in PATH")
    return results


def probe_python_packages(required_pkgs: List[str]) -> Dict[str, Tuple[bool, str]]:
    """
    Probes Python package imports (analogous to verifying package bindings in SV).
    """
    results: Dict[str, Tuple[bool, str]] = {}
    for pkg in required_pkgs:
        try:
            mod = __import__(pkg)
            version = getattr(mod, "__version__", "installed")
            results[pkg] = (True, f"v{version}")
        except ImportError:
            results[pkg] = (False, "NOT INSTALLED")
    return results


def main() -> int:
    print(f"\n{BOLD}{CYAN}======================================================{RESET}")
    print(f"{BOLD}{CYAN}    AGENTIC EDA COPILOT - MILESTONE 0 DIAGNOSTICS    {RESET}")
    print(f"{BOLD}{CYAN}======================================================{RESET}\n")

    # 1. Check EDA toolchain binaries
    target_eda_tools = ["iverilog", "vvp", "verilator", "yosys", "sta"]
    eda_results = probe_eda_binaries(target_eda_tools)

    print(f"{BOLD}1. Host EDA Binaries Check:{RESET}")
    print(f"{'-'*60}")
    missing_eda: List[str] = []
    for tool, (found, path_or_err) in eda_results.items():
        if found:
            print(f"  [{GREEN}PASS{RESET}] {tool:<12} -> {path_or_err}")
        else:
            print(f"  [{RED}FAIL{RESET}] {tool:<12} -> {RED}{path_or_err}{RESET}")
            missing_eda.append(tool)
    print(f"{'-'*60}\n")

    # 2. Check Python packages
    target_pkgs = ["langgraph", "autogen_agentchat", "pydantic"]
    pkg_results = probe_python_packages(target_pkgs)

    print(f"{BOLD}2. Python Agentic Frameworks Check:{RESET}")
    print(f"{'-'*60}")
    missing_pkgs: List[str] = []
    for pkg, (installed, info) in pkg_results.items():
        if installed:
            print(f"  [{GREEN}PASS{RESET}] {pkg:<20} -> {info}")
        else:
            print(f"  [{YELLOW}WARN{RESET}] {pkg:<20} -> {YELLOW}{info}{RESET}")
            missing_pkgs.append(pkg)
    print(f"{'-'*60}\n")

    # 3. Check Standard Cell Liberty Library
    print(f"{BOLD}3. Standard Cell Liberty Library Check:{RESET}")
    print(f"{'-'*60}")
    local_lib = Path(__file__).resolve().parent / "lib" / "sky130hd_tt.lib.gz"
    fallback_lib = Path("/home/vishalkamath/Desktop/ImageUpscalerAccelerator/OpenSTA/examples/sky130hd_tt.lib.gz")
    if local_lib.is_file():
        print(f"  [{GREEN}PASS{RESET}] {'sky130hd_tt.lib.gz':<20} -> {local_lib} (Local)")
    elif fallback_lib.is_file():
        print(f"  [{GREEN}PASS{RESET}] {'sky130hd_tt.lib.gz':<20} -> {fallback_lib} (External)")
    else:
        print(f"  [{YELLOW}WARN{RESET}] {'sky130hd_tt.lib.gz':<20} -> {YELLOW}NOT FOUND in lib/{RESET}")
    print(f"{'-'*60}\n")

    # Summary
    if missing_pkgs:
        venv_path = Path(__file__).resolve().parent / ".venv"
        print(f"{YELLOW}[NOTICE]{RESET} Python packages ({', '.join(missing_pkgs)}) are not installed in:")
        print(f"  {sys.executable}")
        if venv_path.is_dir():
            print(f"  Workspace virtual environment is present. Activate it via:")
            print(f"    {CYAN}source .venv/bin/activate{RESET}")
            print(f"  Or execute directly with:")
            print(f"    {CYAN}.venv/bin/python check_env.py{RESET}\n")
        else:
            print(f"  Install required dependencies via:")
            print(f"    {CYAN}pip install -r requirements.txt{RESET}\n")

    if missing_eda:
        print(f"{RED}{BOLD}[DIAGNOSTIC FAILED]{RESET} Missing EDA binaries: {', '.join(missing_eda)}")
        if "sta" in missing_eda:
            print(f"{YELLOW}Note on OpenSTA:{RESET} If installed in a custom location, export STA_BIN=/path/to/sta")
        return 1

    print(f"{GREEN}{BOLD}[DIAGNOSTIC PASSED]{RESET} All foundational EDA binaries and paths verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
