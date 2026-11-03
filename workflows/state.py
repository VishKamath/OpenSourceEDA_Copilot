"""
workflows/state.py: Synchronous State Register Bank for LangGraph FSM
=====================================================================
Hardware Mental Model:
- CopilotState: Top-level synchronous register bank (`reg [N-1:0] state_q`).
  Holds all design inputs, runtime flags, figures of merit (FOMs), and history
  across execution cycles.
- Node updates: Next-state assignments (`state_d <= ...`).
"""

from __future__ import annotations

import operator
from typing import Any, Dict, List, Optional
from typing_extensions import Annotated, TypedDict


class CopilotState(TypedDict, total=False):
    """
    Synchronous State Register Bank.
    Maintains complete execution context across FSM transitions.
    """
    # -------------------------------------------------------------------------
    # 1. Opcode & Instruction Decoding (Inputs)
    # -------------------------------------------------------------------------
    intent: str                          # "verification", "synthesis", "sta", "full_flow"
    top_module: str                      # Name of the top-level DUT module
    design_sources: List[str]            # Paths to RTL design files
    testbench_sources: List[str]         # Paths to self-checking testbench files
    sdc_file: Optional[str]              # Path to SDC timing constraints
    liberty_file: Optional[str]          # Path to Liberty timing library (.lib/.lib.gz)
    clock_period_ns: Optional[float]     # Target clock period in ns (default: 10.0ns -> 100MHz)
    workspace_dir: str                   # Dedicated isolated scratchpad directory
    user_prompt: Optional[str]           # Natural language prompt for flow specificity
    prompt_spec: Optional[Dict[str, Any]]# Structured extracted hardware constraints & directives

    # -------------------------------------------------------------------------
    # 2. Control Registers & Watchdog Guardrails
    # -------------------------------------------------------------------------
    current_phase: str                   # Current FSM phase tag
    iteration_count: int                 # Repair loop counter (prevents infinite FSM lockup)
    max_iterations: int                  # Watchdog limit (default: 3)
    status_message: str                  # Human/Agent-readable execution summary
    errors: Annotated[List[str], operator.add]  # Accumulator for error diagnostics

    # -------------------------------------------------------------------------
    # 3. Verification Figures of Merit (FOMs)
    # -------------------------------------------------------------------------
    lint_passed: bool                    # Verilator lint check status
    simulation_passed: bool              # Icarus simulation check status
    coverage_pct: float                  # Code/Toggle coverage percentage
    coverage_target_met: bool            # Coverage >= target threshold (e.g. >= 90.0%)

    # -------------------------------------------------------------------------
    # 4. Synthesis Figures of Merit (FOMs)
    # -------------------------------------------------------------------------
    synthesis_passed: bool               # Yosys synthesis completion flag
    gate_netlist: Optional[str]          # Path to output structural netlist (.v)
    cell_count: int                      # Total gate cell count
    dff_count: int                       # Sequential register count
    chip_area: Optional[float]           # Estimated gate area from Liberty cell library

    # -------------------------------------------------------------------------
    # 5. Static Timing Analysis (STA) Figures of Merit (FOMs)
    # -------------------------------------------------------------------------
    sta_passed: bool                     # OpenSTA execution status
    wns_setup_ns: Optional[float]        # Worst Negative Slack for setup (max delay)
    wns_hold_ns: Optional[float]         # Worst Negative Slack for hold (min delay)
    tns_ns: Optional[float]              # Total Negative Slack
    timing_closed: bool                  # True if wns_setup >= 0 and wns_hold >= 0

    # -------------------------------------------------------------------------
    # 6. Telemetry & Bus Log Trace
    # -------------------------------------------------------------------------
    tool_history: Annotated[List[Dict[str, Any]], operator.add]  # Appended tool execution log
