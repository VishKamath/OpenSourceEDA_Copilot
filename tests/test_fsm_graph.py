"""
tests/test_fsm_graph.py: Unit tests for LangGraph FSM Controller
================================================================
Validates:
1. Verification Flow: RTL -> Lint -> Simulate -> Success
2. Watchdog Guardrails: Buggy TB -> Repair loopback -> Abort on max_iterations
3. Synthesis Flow: RTL -> Yosys Netlist -> Success
4. STA Flow: Netlist + SDC + Liberty -> OpenSTA -> Slack closure
"""

import pytest
from pathlib import Path
from workflows.graph import build_eda_copilot_graph
from workflows.state import CopilotState
from tools.eda_runners import make_sandbox


@pytest.fixture
def counter_workspace(tmp_path: Path):
    """Provides valid counter RTL and self-checking testbench."""
    sandbox = make_sandbox(base=tmp_path, tag="fsm_counter")

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

    tb_v = sandbox / "counter_tb.v"
    tb_v.write_text("""
module counter_tb;
    reg clk, rst_n, en;
    wire [3:0] count;

    counter dut (.clk(clk), .rst_n(rst_n), .en(en), .count(count));

    always #5 clk = ~clk;

    initial begin
        clk = 0; rst_n = 0; en = 0;
        #15 rst_n = 1;
        #10 en = 1;
        #40 en = 0;
        #10;
        if (count == 4'd4)
            $display("TEST_PASSED");
        else
            $fatal(1, "MISMATCH");
        $finish;
    end
endmodule
""")

    return sandbox, counter_v, tb_v


def test_fsm_verification_flow_success(counter_workspace):
    """Verify that valid RTL and TB successfully traverse to TERMINAL_SUCCESS."""
    sandbox, counter_v, tb_v = counter_workspace

    app = build_eda_copilot_graph()

    init_state: CopilotState = {
        "intent": "verification",
        "top_module": "counter",
        "design_sources": [str(counter_v)],
        "testbench_sources": [str(tb_v)],
        "workspace_dir": str(sandbox),
        "max_iterations": 2,
    }

    final_state = app.invoke(init_state)

    assert final_state["current_phase"] == "TERMINAL_SUCCESS"
    assert final_state["lint_passed"] is True
    assert final_state["simulation_passed"] is True
    assert final_state["coverage_target_met"] is True


def test_fsm_watchdog_termination_on_failure(tmp_path: Path):
    """
    Verify FSM watchdog counter (guardrail):
    A failing testbench must loop through repair and abort when reaching max_iterations.
    """
    sandbox = make_sandbox(base=tmp_path, tag="fsm_fail")

    counter_v = sandbox / "counter.v"
    counter_v.write_text("""
module counter (input clk, rst_n, en, output reg [3:0] count);
    always @(posedge clk or negedge rst_n)
        if (!rst_n) count <= 0; else if (en) count <= count + 1;
endmodule
""")

    # Failing TB: unconditionally fatal
    bad_tb_v = sandbox / "bad_tb.v"
    bad_tb_v.write_text("""
module counter_tb;
    reg clk;
    initial begin
        #10;
        $fatal(1, "INTENTIONAL_ASSERTION_FAILURE");
    end
endmodule
""")

    app = build_eda_copilot_graph()

    init_state: CopilotState = {
        "intent": "verification",
        "top_module": "counter",
        "design_sources": [str(counter_v)],
        "testbench_sources": [str(bad_tb_v)],
        "workspace_dir": str(sandbox),
        "max_iterations": 2,
    }

    final_state = app.invoke(init_state)

    # Must abort cleanly without infinite loop
    assert final_state["current_phase"] == "TERMINAL_ABORT"
    assert final_state["simulation_passed"] is False
    assert final_state["iteration_count"] == 2


def test_fsm_synthesis_flow_success(counter_workspace):
    """Verify synthesis intent routes directly to Yosys and produces netlist."""
    sandbox, counter_v, _ = counter_workspace

    app = build_eda_copilot_graph()

    init_state: CopilotState = {
        "intent": "synthesis",
        "top_module": "counter",
        "design_sources": [str(counter_v)],
        "workspace_dir": str(sandbox),
    }

    final_state = app.invoke(init_state)

    assert final_state["current_phase"] == "TERMINAL_SUCCESS"
    assert final_state["synthesis_passed"] is True
    assert final_state["cell_count"] > 0
    assert final_state["gate_netlist"] is not None
    assert Path(final_state["gate_netlist"]).exists()


def test_fsm_sta_flow_success(tmp_path: Path):
    """Verify STA intent routes to OpenSTA and verifies timing closure."""
    local_dir = Path(__file__).resolve().parent.parent / "lib"
    fallback_dir = Path("/home/vishalkamath/Desktop/ImageUpscalerAccelerator/OpenSTA/examples")
    ex_dir = local_dir if (local_dir / "sky130hd_tt.lib.gz").exists() else fallback_dir
    lib = ex_dir / "sky130hd_tt.lib.gz"
    netlist = ex_dir / "gcd_sky130hd.v"
    sdc = ex_dir / "gcd_sky130hd.sdc"

    if not (lib.exists() and netlist.exists() and sdc.exists()):
        pytest.skip("OpenSTA benchmark files not found.")

    sandbox = make_sandbox(base=tmp_path, tag="fsm_sta")
    app = build_eda_copilot_graph()

    init_state: CopilotState = {
        "intent": "sta",
        "top_module": "gcd",
        "design_sources": [str(netlist)],
        "liberty_file": str(lib),
        "sdc_file": str(sdc),
        "workspace_dir": str(sandbox),
    }

    final_state = app.invoke(init_state)

    assert final_state["current_phase"] == "TERMINAL_SUCCESS"
    assert final_state["sta_passed"] is True
    assert final_state["timing_closed"] is True
    assert final_state["wns_setup_ns"] is not None
    assert final_state["wns_setup_ns"] > 0.0
