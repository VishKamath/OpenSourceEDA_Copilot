"""
tests/test_synthesis_sta_pipeline.py: Unit tests for Synthesis & Signoff STA Integration
========================================================================================
Validates:
1. Automated SDC constraint generation.
2. End-to-end Full Flow execution (Verification -> Synthesis -> STA).
3. CLI execution via main.py.
"""

import subprocess
import sys
from pathlib import Path
import pytest

from tools.eda_runners import make_sandbox
from agents.timing_agent import generate_sdc_constraints
from workflows.graph import build_eda_copilot_graph
from workflows.state import CopilotState


def test_sdc_constraint_generation():
    """Verify SDC clock, uncertainty, and I/O budget generation."""
    sdc = generate_sdc_constraints(
        clock_port="clk",
        clock_period_ns=5.0,
        uncertainty_ns=0.15,
        input_ports=["a", "b"],
        output_ports=["result"],
    )

    assert "create_clock -name clk -period 5.0" in sdc
    assert "set_clock_uncertainty 0.15 [get_clocks clk]" in sdc
    assert "set_input_delay 1.0 -clock clk [get_ports {a b}]" in sdc
    assert "set_output_delay 1.0 -clock clk [get_ports {result}]" in sdc


def test_full_flow_fsm_integration(tmp_path: Path):
    """
    Verify full flow chaining:
    Raw RTL -> Auto ATPG TB -> Sim -> Yosys Synthesis -> Auto SDC -> OpenSTA Signoff
    """
    local_dir = Path(__file__).resolve().parent.parent / "lib"
    fallback_dir = Path("/home/vishalkamath/Desktop/ImageUpscalerAccelerator/OpenSTA/examples")
    ex_dir = local_dir if (local_dir / "sky130hd_tt.lib.gz").exists() else fallback_dir
    lib = ex_dir / "sky130hd_tt.lib.gz"

    if not lib.exists():
        pytest.skip("Sky130 standard cell library not found.")

    sandbox = make_sandbox(base=tmp_path, tag="full_flow")
    counter_v = sandbox / "counter.v"
    counter_v.write_text("""
module counter (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       en,
    output reg  [3:0] count
);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            count <= 4'b0000;
        else if (en)
            count <= count + 1'b1;
    end
endmodule
""")

    app = build_eda_copilot_graph()
    init_state: CopilotState = {
        "intent": "full_flow",
        "top_module": "counter",
        "design_sources": [str(counter_v)],
        "liberty_file": str(lib),
        "clock_period_ns": 10.0,
        "workspace_dir": str(sandbox),
        "max_iterations": 3,
    }

    final_state = app.invoke(init_state)

    # 1. Verification passed
    assert final_state["simulation_passed"] is True
    # 2. Synthesis passed
    assert final_state["synthesis_passed"] is True
    assert final_state["gate_netlist"] is not None
    # 3. Overall FSM passed
    assert final_state["current_phase"] == "TERMINAL_SUCCESS"


def test_cli_execution_full_flow(tmp_path: Path):
    """Verify execution through the main.py CLI interface."""
    local_dir = Path(__file__).resolve().parent.parent / "lib"
    fallback_dir = Path("/home/vishalkamath/Desktop/ImageUpscalerAccelerator/OpenSTA/examples")
    ex_dir = local_dir if (local_dir / "sky130hd_tt.lib.gz").exists() else fallback_dir
    lib = ex_dir / "sky130hd_tt.lib.gz"

    if not lib.exists():
        pytest.skip("Sky130 standard cell library not found.")

    sandbox = make_sandbox(base=tmp_path, tag="cli_test")
    counter_v = sandbox / "counter.v"
    counter_v.write_text("""
module counter (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       en,
    output reg  [3:0] count
);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            count <= 4'b0000;
        else if (en)
            count <= count + 1'b1;
    end
endmodule
""")

    cmd = [
        sys.executable,
        "main.py",
        "--intent", "full_flow",
        "--top", "counter",
        "--sources", str(counter_v),
        "--liberty", str(lib),
        "--clock-period", "10.0",
        "--workspace", str(sandbox),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0
    assert "PIPELINE EXECUTION SUMMARY" in proc.stdout
    assert "SUCCESS (CLOSED)" in proc.stdout
