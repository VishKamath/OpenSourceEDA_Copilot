"""
tests/test_verification_pipeline.py: Unit tests for Closed-Loop Verification Pipeline
=====================================================================================
Validates:
1. RTL port extraction and AST analysis.
2. Autonomous testbench synthesis (ATPG).
3. End-to-end verification without user testbenches.
4. Closed-loop autonomous testbench repair and healing.
"""

from pathlib import Path
import pytest
from tools.eda_runners import make_sandbox, run_iverilog_simulation
from agents.verification_agent import (
    extract_module_interface,
    generate_self_checking_tb,
    diagnose_and_patch_tb,
)
from workflows.graph import build_eda_copilot_graph
from workflows.state import CopilotState


def test_rtl_module_interface_extraction():
    """Verify regex port interface extraction on a multi-bit ALU module."""
    rtl = """
    module alu (
        input  wire        clk,
        input  wire        rst_n,
        input  wire [3:0]  a,
        input  wire [3:0]  b,
        input  wire [1:0]  op,
        output reg  [7:0]  result,
        output wire        zero_flag
    );
        // RTL logic
    endmodule
    """
    interface = extract_module_interface(rtl)
    assert interface.name == "alu"
    assert interface.clock_port is not None
    assert interface.clock_port.name == "clk"
    assert interface.reset_port is not None
    assert interface.reset_port.name == "rst_n"

    port_map = {p.name: p for p in interface.ports}
    assert port_map["a"].width == 4
    assert port_map["b"].width == 4
    assert port_map["op"].width == 2
    assert port_map["result"].width == 8
    assert port_map["zero_flag"].width == 1


def test_autonomous_tb_generation_and_simulation(tmp_path: Path):
    """Verify that an auto-generated testbench compiles and simulates in Icarus."""
    sandbox = make_sandbox(base=tmp_path, tag="atpg_test")

    alu_v = sandbox / "alu.v"
    alu_v.write_text("""
module alu (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [3:0] a,
    input  wire [3:0] b,
    output reg  [3:0] result
);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            result <= 4'b0000;
        else
            result <= a ^ b;
    end
endmodule
""")

    interface = extract_module_interface(alu_v.read_text())
    tb_code = generate_self_checking_tb(interface)
    tb_file = sandbox / "alu_tb.v"
    tb_file.write_text(tb_code)

    res = run_iverilog_simulation(sources=[alu_v, tb_file], cwd=sandbox)
    assert res.succeeded is True
    assert res.metrics["simulation_finished"] is True
    assert "ALL_ASSERTIONS_PASSED" in res.stdout


def test_end_to_end_verification_zero_user_tb(tmp_path: Path):
    """
    Verify FSM executes end-to-end verification when user supplies ONLY RTL design.
    FSM must autonomously generate testbench -> lint -> simulate -> pass.
    """
    sandbox = make_sandbox(base=tmp_path, tag="zero_tb")
    counter_v = sandbox / "counter.v"
    counter_v.write_text("""
module counter (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [3:0] d_in,
    output reg  [3:0] count
);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            count <= 4'b0000;
        else
            count <= count + 1'b1;
    end
endmodule
""")

    app = build_eda_copilot_graph()
    init_state: CopilotState = {
        "intent": "verification",
        "top_module": "counter",
        "design_sources": [str(counter_v)],
        "workspace_dir": str(sandbox),
        "max_iterations": 3,
    }

    final_state = app.invoke(init_state)
    assert final_state["current_phase"] == "TERMINAL_SUCCESS"
    assert final_state["simulation_passed"] is True
    assert len(final_state["testbench_sources"]) > 0
    assert Path(final_state["testbench_sources"][0]).exists()


def test_closed_loop_autonomous_repair(tmp_path: Path):
    """
    Verify that an initially failing testbench is diagnosed and patched
    by node_repair_tb to achieve closure on subsequent iteration.
    """
    sandbox = make_sandbox(base=tmp_path, tag="repair_test")
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

    # TB with a glitch: asserts en = 0 causing count not to advance to 4
    # Patched repair will force enable high and pass
    buggy_tb = sandbox / "buggy_tb.v"
    buggy_tb.write_text("""
module counter_tb;
    reg clk, rst_n, en;
    wire [3:0] count;
    counter dut (.clk(clk), .rst_n(rst_n), .en(en), .count(count));
    always #5 clk = ~clk;
    initial begin
        clk = 0; rst_n = 0; en = 0;
        #15 rst_n = 1;
        #10 en = 0; // Bug: disable increment!
        #40;
        if (count == 4'd4) begin
            $display("PASS");
        end else begin
            $fatal(1, "Assertion failed: count mismatch!");
        end
        $finish;
    end
endmodule
""")

    app = build_eda_copilot_graph()
    init_state: CopilotState = {
        "intent": "verification",
        "top_module": "counter",
        "design_sources": [str(counter_v)],
        "testbench_sources": [str(buggy_tb)],
        "workspace_dir": str(sandbox),
        "max_iterations": 3,
    }

    final_state = app.invoke(init_state)
    assert final_state["current_phase"] == "TERMINAL_SUCCESS"
    assert final_state["simulation_passed"] is True
    assert final_state["iteration_count"] >= 1
