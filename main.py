#!/usr/bin/env python3
"""
main.py: Top-Level CLI Entry Point for Agentic EDA Copilot
=========================================================
Drives the autonomous Verification, Synthesis, and STA pipelines.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List

# Auto-fallback to local virtual environment if langgraph is not installed in current interpreter
_venv_python = Path(__file__).resolve().parent / ".venv" / "bin" / "python"
if _venv_python.is_file() and Path(sys.prefix).resolve() != _venv_python.parent.parent.resolve():
    try:
        import langgraph  # noqa: F401
    except ImportError:
        os.execv(str(_venv_python), [str(_venv_python)] + sys.argv)

try:
    from workflows.graph import build_eda_copilot_graph
    from workflows.state import CopilotState
    from agents.prompt_parser import parse_user_prompt, PromptSpec
    from agents.verification_agent import extract_module_interface
except ImportError as err:
    print(
        f"\033[91m[ERROR]\033[0m Failed to import required dependencies: {err}\n"
        f"Please activate the virtual environment:\n"
        f"  source .venv/bin/activate\n"
        f"Or run via:\n"
        f"  .venv/bin/python main.py <args>\n",
        file=sys.stderr,
    )
    sys.exit(1)

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def print_banner(intent: str, top_module: str, prompt_spec: Optional[PromptSpec] = None) -> None:
    print(f"\n{BOLD}{CYAN}======================================================================{RESET}")
    print(f"{BOLD}{CYAN}                 AUTONOMOUS AGENTIC EDA COPILOT                       {RESET}")
    print(f"{BOLD}{CYAN}======================================================================{RESET}")
    print(f"  Target Top Module : {BOLD}{top_module}{RESET}")
    print(f"  Execution Pipeline: {BOLD}{intent.upper()}{RESET}")
    if prompt_spec and prompt_spec.raw_prompt:
        print(f"\n  {YELLOW}Prompt Specificity Decoded:{RESET}")
        print(f"   • Prompt          : \"{prompt_spec.raw_prompt}\"")
        if prompt_spec.clock_period_ns:
            freq = f" ({prompt_spec.clock_frequency_mhz:.2f} MHz)" if prompt_spec.clock_frequency_mhz else ""
            print(f"   • Clock Target    : {prompt_spec.clock_period_ns} ns{freq}")
        if prompt_spec.clock_port:
            print(f"   • Clock Port      : {prompt_spec.clock_port}")
        if prompt_spec.uncertainty_ns is not None:
            print(f"   • Uncertainty     : {prompt_spec.uncertainty_ns} ns")
        if prompt_spec.input_delay_ns is not None:
            print(f"   • Input Delay     : {prompt_spec.input_delay_ns} ns")
        if prompt_spec.output_delay_ns is not None:
            print(f"   • Output Delay    : {prompt_spec.output_delay_ns} ns")
        if prompt_spec.test_corner_cases:
            print(f"   • Directed Tests  : Corner & Boundary Conditions (Zeros, Ones, Alternating)")
        if prompt_spec.stimulus_cycles:
            print(f"   • Stimulus Run    : {prompt_spec.stimulus_cycles} cycles")
        if prompt_spec.reset_active_high is not None:
            polarity = "Active-High" if prompt_spec.reset_active_high else "Active-Low"
            print(f"   • Reset Polarity  : {polarity}")
        if prompt_spec.optimize_area:
            print(f"   • Synthesis Opt   : Area Optimization")
        if prompt_spec.optimize_timing:
            print(f"   • Synthesis Opt   : Speed / Timing Optimization")
    print(f"{CYAN}----------------------------------------------------------------------{RESET}\n")


def run_interactive_shell(
    default_sources: Optional[List[str]] = None,
    default_top: Optional[str] = None,
    default_liberty: Optional[str] = None,
) -> int:
    """Launches an interactive prompt-driven shell for autonomous EDA flows."""
    print(f"\n{BOLD}{CYAN}======================================================================{RESET}")
    print(f"{BOLD}{CYAN}      AUTONOMOUS AGENTIC EDA COPILOT - INTERACTIVE PROMPT SHELL       {RESET}")
    print(f"{BOLD}{CYAN}======================================================================{RESET}")
    print("Type natural language prompts to drive verification, synthesis, or STA signoff.")
    print("Examples:")
    print("  • 'verify bit4_ca with corner cases at 200MHz'")
    print("  • 'synthesize bit4_ca targeting 500MHz with 150ps uncertainty'")
    print("  • 'full flow on fa with 50 cycles of stimulus, optimize for area'")
    print("Commands: 'sources <file.v...>', 'top <module>', 'status', 'help', 'exit'\n")

    current_sources = default_sources or []
    current_top = default_top
    app = build_eda_copilot_graph()

    while True:
        try:
            line = input(f"{BOLD}{CYAN}copilot>{RESET} ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting interactive shell.")
            break

        if not line:
            continue
        if line.lower() in ["exit", "quit", "q"]:
            print("Session terminated.")
            break
        if line.lower() == "help":
            print("Interactive Copilot Help:")
            print("  Specify prompt directives like:")
            print("    'verify <module> [with corner cases] [at <freq>]'")
            print("    'synthesize <module> [target <period>ns] [with area optimization]'")
            print("    'sources <path1> <path2>...' to set RTL sources")
            print("    'top <module>' to set top-level module")
            print("    'status' to view current session configuration")
            continue
        if line.lower() == "status":
            print(f"  Current Top Module: {current_top}")
            print(f"  Current Sources   : {current_sources}")
            continue
        if line.lower().startswith("sources "):
            current_sources = [str(Path(p).resolve()) for p in line[8:].split()]
            print(f"Configured sources: {current_sources}")
            continue
        if line.lower().startswith("top "):
            current_top = line[4:].strip()
            print(f"Configured top module: {current_top}")
            continue

        # Parse user prompt
        spec = parse_user_prompt(line)
        top_mod = spec.top_module or current_top
        sources = current_sources

        if not sources:
            candidates = list(Path(".").glob("*.v")) + list(Path(".").glob("*.sv"))
            if candidates:
                sources = [str(c.resolve()) for c in candidates]
            else:
                print(f"{RED}Error:{RESET} No RTL sources specified. Use 'sources <file.v>' first.")
                continue

        if not top_mod:
            try:
                rtl_content = Path(sources[0]).read_text()
                iface = extract_module_interface(rtl_content)
                top_mod = iface.name
            except Exception:
                top_mod = "dut"

        intent = spec.intent or "full_flow"
        print_banner(intent, top_mod, spec)

        state: CopilotState = {
            "intent": intent,
            "top_module": top_mod,
            "design_sources": sources,
            "testbench_sources": [],
            "user_prompt": line,
            "clock_period_ns": spec.clock_period_ns or 10.0,
            "liberty_file": default_liberty,
            "sdc_file": None,
            "max_iterations": spec.max_iterations or 3,
            "workspace_dir": "",
        }

        final_state = app.invoke(state)
        print_summary_report(final_state)

    return 0


def print_summary_report(state: CopilotState) -> None:
    print(f"\n{BOLD}{CYAN}======================================================================{RESET}")
    print(f"{BOLD}{CYAN}                    PIPELINE EXECUTION SUMMARY                        {RESET}")
    print(f"{BOLD}{CYAN}======================================================================{RESET}")

    status = state.get("current_phase", "UNKNOWN")
    passed = status == "TERMINAL_SUCCESS"
    color = GREEN if passed else RED
    status_str = "SUCCESS (CLOSED)" if passed else "FAILED / ABORTED"

    print(f"  Overall Status       : {color}{BOLD}{status_str}{RESET}")
    print(f"  Status Details       : {state.get('status_message', 'N/A')}")
    print(f"  Workspace Scratchpad : {state.get('workspace_dir')}")

    # Verification Section
    if state.get("lint_passed") is not None or state.get("simulation_passed") is not None:
        print(f"\n{BOLD}1. Verification Flow Metrics:{RESET}")
        lint_col = GREEN if state.get("lint_passed") else RED
        sim_col = GREEN if state.get("simulation_passed") else RED
        print(f"   - Static Lint Check : {lint_col}{state.get('lint_passed')}{RESET}")
        print(f"   - Functional Sim    : {sim_col}{state.get('simulation_passed')}{RESET}")
        print(f"   - Coverage Target   : {state.get('coverage_pct', 0.0):.1f}% (Met: {state.get('coverage_target_met')})")
        print(f"   - Repair Iterations : {state.get('iteration_count', 0)} / {state.get('max_iterations', 3)}")

    # Synthesis Section
    if state.get("synthesis_passed") is not None:
        print(f"\n{BOLD}2. Logic Synthesis Metrics:{RESET}")
        synth_col = GREEN if state.get("synthesis_passed") else RED
        print(f"   - Synthesis Status  : {synth_col}{state.get('synthesis_passed')}{RESET}")
        print(f"   - Gate Cell Count   : {state.get('cell_count', 0)}")
        print(f"   - Sequential DFFs   : {state.get('dff_count', 0)}")
        if state.get("gate_netlist"):
            print(f"   - Output Netlist    : {state.get('gate_netlist')}")

    # STA Section
    if state.get("sta_passed") is not None:
        print(f"\n{BOLD}3. Static Timing Signoff Metrics:{RESET}")
        timing_col = GREEN if state.get("timing_closed") else RED
        print(f"   - Timing Closed     : {timing_col}{state.get('timing_closed')}{RESET}")
        print(f"   - Setup Slack (WNS) : {state.get('wns_setup_ns')} ns")
        print(f"   - Hold Slack (WNS)  : {state.get('wns_hold_ns')} ns")
        print(f"   - Total Neg Slack   : {state.get('tns_ns')} ns")

    if state.get("errors"):
        print(f"\n{RED}{BOLD}Diagnostics & Error Logs:{RESET}")
        for err in state["errors"]:
            print(f"   {RED}! {err}{RESET}")

    print(f"{BOLD}{CYAN}======================================================================{RESET}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Autonomous Agentic EDA Copilot for Verification, Synthesis, and STA.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-p", "--prompt",
        type=str,
        default=None,
        help="Natural language prompt for flow specificity (e.g. 'Target 250MHz clock with 150ps uncertainty, test corner cases and overflow, 50 cycles of stimulus').",
    )
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="Launch interactive Copilot prompt shell.",
    )
    parser.add_argument(
        "--intent",
        choices=["verification", "synthesis", "sta", "full_flow"],
        default=None,
        help="Target execution pipeline.",
    )
    parser.add_argument("--top", default=None, help="Top-level Verilog/SystemVerilog module name.")
    parser.add_argument(
        "--sources",
        nargs="+",
        default=[],
        help="Path(s) to RTL design source files.",
    )
    parser.add_argument(
        "--testbenches",
        nargs="*",
        default=[],
        help="Path(s) to testbench source files (optional; auto-synthesized if omitted).",
    )
    parser.add_argument(
        "--clock-period",
        type=float,
        default=10.0,
        help="Target clock period in nanoseconds.",
    )
    parser.add_argument("--liberty", default=None, help="Path to Liberty standard cell library (.lib/.lib.gz).")
    parser.add_argument("--sdc", default=None, help="Path to SDC timing constraints.")
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=3,
        help="Maximum autonomous repair loops.",
    )
    parser.add_argument("--workspace", default=None, help="Target isolated workspace directory.")

    args = parser.parse_args()

    if args.interactive:
        return run_interactive_shell(
            default_sources=args.sources,
            default_top=args.top,
            default_liberty=args.liberty,
        )

    # Prompt-first resolution
    spec: Optional[PromptSpec] = None
    if args.prompt:
        spec = parse_user_prompt(args.prompt)

    # Determine top module
    top_mod = args.top or (spec.top_module if spec else None)
    if not top_mod and args.sources:
        try:
            rtl_text = Path(args.sources[0]).read_text()
            iface = extract_module_interface(rtl_text)
            top_mod = iface.name
        except Exception:
            pass

    if not top_mod:
        parser.error("Top-level module name is required (specify --top <module> or in --prompt).")

    if not args.sources:
        candidates = list(Path(".").glob("*.v")) + list(Path(".").glob("*.sv"))
        if candidates:
            sources = [str(c.resolve()) for c in candidates]
        else:
            parser.error("RTL sources are required (specify --sources <path.v ...>).")
    else:
        sources = [str(Path(s).resolve()) for s in args.sources]

    # Intent determination
    intent = args.intent or (spec.intent if spec and spec.intent else "full_flow")

    # Clock period override from prompt
    clock_period = args.clock_period
    if clock_period == 10.0 and spec and spec.clock_period_ns:
        clock_period = spec.clock_period_ns

    # Max iterations override from prompt
    max_iter = args.max_iterations
    if max_iter == 3 and spec and spec.max_iterations:
        max_iter = spec.max_iterations

    print_banner(intent, top_mod, spec)

    init_state: CopilotState = {
        "intent": intent,
        "top_module": top_mod,
        "design_sources": sources,
        "testbench_sources": [str(Path(tb).resolve()) for tb in args.testbenches] if args.testbenches else [],
        "clock_period_ns": clock_period,
        "user_prompt": args.prompt,
        "liberty_file": str(Path(args.liberty).resolve()) if args.liberty else None,
        "sdc_file": str(Path(args.sdc).resolve()) if args.sdc else None,
        "max_iterations": max_iter,
        "workspace_dir": str(Path(args.workspace).resolve()) if args.workspace else "",
    }

    app = build_eda_copilot_graph()
    final_state = app.invoke(init_state)

    print_summary_report(final_state)

    return 0 if final_state.get("current_phase") == "TERMINAL_SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
