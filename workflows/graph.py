"""
workflows/graph.py: Top-Level Finite State Machine (FSM) Controller
===================================================================
Hardware Mental Model:
- StateGraph: Top-level synchronous sequential state machine.
- Router Node: Instruction decoder / Opcode dispatch (`case (opcode)`).
- Nodes: Combinational/Sequential functional units updating the state registers.
- Conditional Edges: Combinational next-state decoding (`always_comb`).
- Watchdog Guardrails: Timeout counter preventing latch-up / infinite repair loops.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Literal

from langgraph.graph import END, START, StateGraph

from tools.eda_runners import (
    make_sandbox,
    run_iverilog_simulation,
    run_opensta_timing,
    run_verilator_lint,
    run_yosys_synthesis,
)
from agents.verification_agent import (
    extract_module_interface,
    generate_self_checking_tb,
    diagnose_and_patch_tb,
)
from agents.timing_agent import generate_sdc_constraints
from agents.prompt_parser import parse_user_prompt, PromptSpec
from workflows.state import CopilotState


# ==============================================================================
# SECTION 1: FSM NODES (STATE REGISTER UPDATE UNITS)
# ==============================================================================

def node_router(state: CopilotState) -> Dict:
    """
    Instruction Decoder / Opcode Dispatcher.
    Initializes workspace scratchpad, parses user prompt specificity,
    and applies extracted constraints.
    """
    workspace = state.get("workspace_dir")
    if not workspace:
        sandbox = make_sandbox(tag=state.get("intent", "eda"))
        workspace = str(sandbox)

    res: Dict[str, Any] = {
        "workspace_dir": workspace,
        "iteration_count": state.get("iteration_count", 0),
        "max_iterations": state.get("max_iterations", 3),
        "current_phase": "ROUTER_INIT",
        "errors": [],
        "tool_history": [],
    }

    # Ingest and decode user prompt if provided
    user_prompt = state.get("user_prompt")
    if user_prompt:
        spec = parse_user_prompt(user_prompt)
        res["prompt_spec"] = spec.to_dict()

        # Specificity overrides: only apply if state does not have an explicit CLI override
        if spec.clock_period_ns and (state.get("clock_period_ns") is None or state.get("clock_period_ns") == 10.0):
            res["clock_period_ns"] = spec.clock_period_ns

        if spec.max_iterations and (state.get("max_iterations") is None or state.get("max_iterations") == 3):
            res["max_iterations"] = spec.max_iterations

        if spec.top_module and not state.get("top_module"):
            res["top_module"] = spec.top_module

        if spec.intent and (not state.get("intent") or state.get("intent") == "full_flow"):
            res["intent"] = spec.intent

    return res


def node_generate_tb(state: CopilotState) -> Dict:
    """
    Automatic Test Pattern Generation (ATPG) Node.
    Synthesizes a self-checking testbench if none is provided,
    incorporating any prompt-specified corner cases or stimulus counts.
    """
    tb_sources = state.get("testbench_sources", [])
    if tb_sources:
        return {"current_phase": "TB_READY"}

    design_files = [Path(p) for p in state["design_sources"]]
    top_module = state["top_module"]
    workspace = Path(state["workspace_dir"])

    rtl_text = "\n".join(f.read_text() for f in design_files if f.exists())
    interface = extract_module_interface(rtl_text, target_module=top_module)
    if not interface.name or interface.name == "dut":
        interface.name = top_module

    # Pass prompt specification to testbench generator
    prompt_spec_data = state.get("prompt_spec")
    prompt_spec = PromptSpec(**prompt_spec_data) if prompt_spec_data else None

    tb_code = generate_self_checking_tb(interface, prompt_spec=prompt_spec)
    tb_file = workspace / f"{top_module}_auto_tb.v"
    tb_file.write_text(tb_code)

    corner_msg = " [with directed corner cases]" if (prompt_spec and prompt_spec.test_corner_cases) else ""
    return {
        "current_phase": "TB_GENERATED",
        "testbench_sources": [str(tb_file)],
        "status_message": f"Autonomous self-checking testbench generated: {tb_file.name}{corner_msg}",
    }


def node_lint(state: CopilotState) -> Dict:
    """Executes Verilator static lint check on design RTL sources."""
    sources = [Path(p) for p in state["design_sources"]]
    top_module = state["top_module"]
    workspace = Path(state["workspace_dir"])

    res = run_verilator_lint(sources=sources, top_module=top_module, cwd=workspace)

    errors = []
    if not res.succeeded:
        errors.append(f"Lint failed with exit code {res.returncode}: {res.stderr}")

    return {
        "current_phase": "VERIFICATION_LINT",
        "lint_passed": res.succeeded and res.metrics.get("lint_passed", False),
        "errors": errors,
        "tool_history": [res.metrics],
    }


def node_simulate(state: CopilotState) -> Dict:
    """Executes Icarus Verilog 2-stage simulation with testbenches."""
    sources = [Path(p) for p in state["design_sources"] + state.get("testbench_sources", [])]
    workspace = Path(state["workspace_dir"])

    res = run_iverilog_simulation(sources=sources, cwd=workspace)

    sim_ok = res.succeeded and res.metrics.get("simulation_finished", False)
    errors = []
    if not sim_ok:
        errors.append(f"Simulation failed: {res.stderr or res.stdout[-300:]}")

    # Set initial coverage metric (can be hooked to Verilator coverage in Milestone 4)
    coverage_val = 100.0 if sim_ok else 0.0
    coverage_met = coverage_val >= 90.0

    return {
        "current_phase": "VERIFICATION_SIMULATION",
        "simulation_passed": sim_ok,
        "coverage_pct": coverage_val,
        "coverage_target_met": coverage_met,
        "errors": errors,
        "tool_history": [res.metrics],
    }


def node_repair_tb(state: CopilotState) -> Dict:
    """
    Watchdog Guarded Autonomous Repair Node.
    Analyzes error diagnostics, applies stimulus/assertion patches,
    increments loop counter, and re-triggers simulation.
    """
    next_iteration = state.get("iteration_count", 0) + 1
    msg = f"Entering repair loop (iteration {next_iteration}/{state.get('max_iterations', 3)})"

    tb_sources = state.get("testbench_sources", [])
    error_summary = " ".join(state.get("errors", []))

    if tb_sources:
        tb_path = Path(tb_sources[0])
        if tb_path.exists():
            original_code = tb_path.read_text()
            patched_code = diagnose_and_patch_tb(
                tb_content=original_code,
                error_log=error_summary,
                iteration=next_iteration,
            )
            tb_path.write_text(patched_code)
            msg += f" | Patched {tb_path.name}"

    return {
        "iteration_count": next_iteration,
        "current_phase": "VERIFICATION_REPAIR",
        "status_message": msg,
        "errors": [],
    }


def node_synthesis(state: CopilotState) -> Dict:
    """Executes Yosys logic synthesis, generating gate-level netlists."""
    sources = [Path(p) for p in state["design_sources"]]
    top_module = state["top_module"]
    workspace = Path(state["workspace_dir"])
    liberty = Path(state["liberty_file"]) if state.get("liberty_file") else None
    if not (liberty and liberty.exists()):
        local_lib = Path(__file__).resolve().parent.parent / "lib" / "sky130hd_tt.lib.gz"
        default_lib = Path("/home/vishalkamath/Desktop/ImageUpscalerAccelerator/OpenSTA/examples/sky130hd_tt.lib.gz")
        if local_lib.exists():
            liberty = local_lib
        elif default_lib.exists():
            liberty = default_lib
    out_netlist = workspace / f"{top_module}_synth_netlist.v"

    res = run_yosys_synthesis(
        sources=sources,
        top_module=top_module,
        output_netlist=out_netlist,
        liberty_file=liberty,
        cwd=workspace,
    )

    synth_ok = res.succeeded and res.metrics.get("netlist_generated", False)
    errors = []
    if not synth_ok:
        errors.append(f"Synthesis failed: {res.stderr or res.stdout[-300:]}")

    return {
        "current_phase": "SYNTHESIS_YOSYS",
        "synthesis_passed": synth_ok,
        "gate_netlist": str(out_netlist) if synth_ok else None,
        "cell_count": res.metrics.get("num_cells", 0),
        "dff_count": res.metrics.get("dff_count", 0),
        "chip_area": res.metrics.get("chip_area"),
        "errors": errors,
        "tool_history": [res.metrics],
    }


def node_sta(state: CopilotState) -> Dict:
    """Executes OpenSTA static timing signoff."""
    top_module = state["top_module"]
    workspace = Path(state["workspace_dir"])

    # Locate or fallback Liberty cell library
    liberty = Path(state["liberty_file"]) if state.get("liberty_file") else None
    if not (liberty and liberty.exists()):
        local_lib = Path(__file__).resolve().parent.parent / "lib" / "sky130hd_tt.lib.gz"
        default_lib = Path("/home/vishalkamath/Desktop/ImageUpscalerAccelerator/OpenSTA/examples/sky130hd_tt.lib.gz")
        if local_lib.exists():
            liberty = local_lib
        elif default_lib.exists():
            liberty = default_lib

    netlist = Path(state.get("gate_netlist") or state.get("design_sources", [""])[0])

    # Locate or auto-generate SDC constraints
    sdc = Path(state["sdc_file"]) if state.get("sdc_file") else None
    if not (sdc and sdc.exists()):
        period = state.get("clock_period_ns") or 10.0
        p_data = state.get("prompt_spec") or {}
        clk_port = p_data.get("clock_port") or "clk"
        uncertainty = p_data.get("uncertainty_ns") if p_data.get("uncertainty_ns") is not None else 0.2
        inp_delay = p_data.get("input_delay_ns")
        out_delay = p_data.get("output_delay_ns")

        sdc_text = generate_sdc_constraints(
            clock_port=clk_port,
            clock_period_ns=period,
            uncertainty_ns=uncertainty,
            input_delay_ns=inp_delay,
            output_delay_ns=out_delay,
        )
        sdc_file_path = workspace / f"{top_module}_auto.sdc"
        sdc_file_path.write_text(sdc_text)
        sdc = sdc_file_path

    if not (liberty and liberty.exists() and netlist.exists()):
        return {
            "current_phase": "STA_OPENSTA",
            "sta_passed": False,
            "timing_closed": False,
            "errors": [f"STA requires valid liberty_file and netlist_file. Netlist: {netlist}, Liberty: {liberty}"],
        }

    res = run_opensta_timing(
        liberty_file=liberty,
        netlist_file=netlist,
        sdc_file=sdc,
        top_module=top_module,
        cwd=workspace,
    )

    sta_ok = res.succeeded
    timing_closed = res.metrics.get("timing_closed", False)
    errors = []
    if not sta_ok:
        errors.append(f"OpenSTA execution error: {res.stderr or res.stdout[-300:]}")
    elif not timing_closed:
        errors.append(f"Timing closure failed: WNS={res.metrics.get('wns_setup_ns')}ns")

    return {
        "current_phase": "STA_OPENSTA",
        "sta_passed": sta_ok,
        "wns_setup_ns": res.metrics.get("wns_setup_ns"),
        "wns_hold_ns": res.metrics.get("wns_hold_ns"),
        "tns_ns": res.metrics.get("tns_ns"),
        "timing_closed": timing_closed,
        "errors": errors,
        "tool_history": [res.metrics],
    }


def node_success(state: CopilotState) -> Dict:
    """Terminal state for successful pipeline closure."""
    return {
        "current_phase": "TERMINAL_SUCCESS",
        "status_message": f"Pipeline successfully closed for intent '{state.get('intent')}'.",
    }


def node_abort(state: CopilotState) -> Dict:
    """Terminal state for unrecoverable errors or watchdog expiration."""
    reasons = "; ".join(state.get("errors", [])) or "Watchdog limit exceeded."
    return {
        "current_phase": "TERMINAL_ABORT",
        "status_message": f"Pipeline aborted in phase '{state.get('current_phase')}': {reasons}",
    }


# ==============================================================================
# SECTION 2: COMBINATIONAL NEXT-STATE DECODING (CONDITIONAL EDGES)
# ==============================================================================

def route_intent(state: CopilotState) -> Literal["node_generate_tb", "node_lint", "node_synthesis", "node_sta"]:
    """Opcode decoder: maps user intent to starting execution phase."""
    intent = state.get("intent", "verification")
    if intent in ("verification", "full_flow"):
        if not state.get("testbench_sources"):
            return "node_generate_tb"
        return "node_lint"
    elif intent == "synthesis":
        return "node_synthesis"
    elif intent == "sta":
        return "node_sta"
    return "node_lint"


def route_after_lint(state: CopilotState) -> Literal["node_simulate", "node_abort"]:
    """Branch condition: abort on lint failure, else proceed to simulation."""
    if state.get("lint_passed"):
        return "node_simulate"
    return "node_abort"


def route_after_verification(state: CopilotState) -> Literal["node_synthesis", "node_success", "node_repair_tb", "node_abort"]:
    """
    Verification closure evaluator:
    - Pass + full_flow -> proceed to synthesis
    - Pass + verification_only -> success
    - Fail + within watchdog limit -> enter repair loop
    - Fail + watchdog expired -> abort
    """
    sim_ok = state.get("simulation_passed", False)
    cov_ok = state.get("coverage_target_met", False)
    intent = state.get("intent", "verification")

    if sim_ok and cov_ok:
        return "node_synthesis" if intent == "full_flow" else "node_success"

    # Check loop watchdog timer (guardrail against infinite loops)
    iter_count = state.get("iteration_count", 0)
    max_iters = state.get("max_iterations", 3)
    if iter_count < max_iters:
        return "node_repair_tb"
    return "node_abort"


def route_after_synthesis(state: CopilotState) -> Literal["node_sta", "node_success", "node_abort"]:
    """Synthesis closure evaluator."""
    if not state.get("synthesis_passed"):
        return "node_abort"
    if state.get("intent") == "full_flow":
        return "node_sta"
    return "node_success"


def route_after_sta(state: CopilotState) -> Literal["node_success", "node_abort"]:
    """Timing closure evaluator."""
    if state.get("sta_passed") and state.get("timing_closed"):
        return "node_success"
    return "node_abort"


# ==============================================================================
# SECTION 3: LANGGRAPH FSM COMPILATION
# ==============================================================================

def build_eda_copilot_graph() -> StateGraph:
    """
    Assembles the complete synchronous StateGraph.
    Connects nodes and conditional edges according to the target hardware FSM.
    """
    workflow = StateGraph(CopilotState)

    # Register Nodes
    workflow.add_node("node_router", node_router)
    workflow.add_node("node_generate_tb", node_generate_tb)
    workflow.add_node("node_lint", node_lint)
    workflow.add_node("node_simulate", node_simulate)
    workflow.add_node("node_repair_tb", node_repair_tb)
    workflow.add_node("node_synthesis", node_synthesis)
    workflow.add_node("node_sta", node_sta)
    workflow.add_node("node_success", node_success)
    workflow.add_node("node_abort", node_abort)

    # Wire Edges
    workflow.add_edge(START, "node_router")

    workflow.add_conditional_edges(
        "node_router",
        route_intent,
        {
            "node_generate_tb": "node_generate_tb",
            "node_lint": "node_lint",
            "node_synthesis": "node_synthesis",
            "node_sta": "node_sta",
        },
    )

    workflow.add_edge("node_generate_tb", "node_lint")

    workflow.add_conditional_edges(
        "node_lint",
        route_after_lint,
        {
            "node_simulate": "node_simulate",
            "node_abort": "node_abort",
        },
    )

    workflow.add_conditional_edges(
        "node_simulate",
        route_after_verification,
        {
            "node_synthesis": "node_synthesis",
            "node_success": "node_success",
            "node_repair_tb": "node_repair_tb",
            "node_abort": "node_abort",
        },
    )

    # Loopback edge from repair back to simulate
    workflow.add_edge("node_repair_tb", "node_simulate")

    workflow.add_conditional_edges(
        "node_synthesis",
        route_after_synthesis,
        {
            "node_sta": "node_sta",
            "node_success": "node_success",
            "node_abort": "node_abort",
        },
    )

    workflow.add_conditional_edges(
        "node_sta",
        route_after_sta,
        {
            "node_success": "node_success",
            "node_abort": "node_abort",
        },
    )

    workflow.add_edge("node_success", END)
    workflow.add_edge("node_abort", END)

    return workflow.compile()
