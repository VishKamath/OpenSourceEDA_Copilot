"""
tests/test_eda_runners.py: Unit tests for Layer 0 Deterministic EDA Runners
==========================================================================
Validates:
1. ToolResult packet contract and immutability.
2. Workspace sandboxing and security containment.
3. Icarus Verilog compilation & simulation with self-checking testbench.
4. Verilator linting.
5. Yosys logic synthesis and netlist generation.
"""

import pytest
from dataclasses import FrozenInstanceError
from pathlib import Path
from tools.eda_runners import (
    ToolResult,
    make_sandbox,
    validate_safe_path,
    run_iverilog_simulation,
    run_verilator_lint,
    run_yosys_synthesis,
    run_opensta_timing,
)


def test_tool_result_contract():
    """Verify ToolResult transaction packet immutability and success logic."""
    res_pass = ToolResult(command=["iverilog", "-v"], returncode=0)
    assert res_pass.succeeded is True
    assert "EXIT_0" in res_pass.summary()

    res_fail = ToolResult(command=["vvp"], returncode=1)
    assert res_fail.succeeded is False

    res_timeout = ToolResult(command=["sleep"], returncode=None, timedout=True)
    assert res_timeout.succeeded is False
    assert "TIMEOUT" in res_timeout.summary()

    # Verify immutability (frozen dataclass)
    with pytest.raises(FrozenInstanceError):
        res_pass.returncode = 1  # type: ignore


def test_sandbox_and_security(tmp_path: Path):
    """Verify workspace allocation and directory traversal defense."""
    sandbox = make_sandbox(base=tmp_path, tag="sec_test")
    assert sandbox.is_dir()

    # Valid internal file
    safe_file = sandbox / "design.v"
    safe_file.write_text("module top; endmodule")
    resolved = validate_safe_path(safe_file, sandbox)
    assert resolved == safe_file.resolve()

    # Path traversal attack detection
    forbidden_file = tmp_path / "secret.txt"
    forbidden_file.write_text("classified")
    with pytest.raises(PermissionError):
        validate_safe_path(forbidden_file, sandbox)


def test_iverilog_simulation(tmp_path: Path):
    """Verify 2-stage Icarus simulation on a 4-bit synchronous counter."""
    sandbox = make_sandbox(base=tmp_path, tag="iverilog_test")

    # 1. Counter RTL
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

    # 2. Self-checking testbench
    tb_v = sandbox / "counter_tb.v"
    tb_v.write_text("""
module counter_tb;
    reg clk;
    reg rst_n;
    reg en;
    wire [3:0] count;

    counter dut (
        .clk(clk),
        .rst_n(rst_n),
        .en(en),
        .count(count)
    );

    always #5 clk = ~clk;

    initial begin
        clk = 0;
        rst_n = 0;
        en = 0;
        #15;
        rst_n = 1;
        #10;
        en = 1;
        #40;
        en = 0;
        #10;
        if (count == 4'd4) begin
            $display("TEST_PASSED: count = %0d", count);
        end else begin
            $fatal(1, "TEST_FAILED: expected count 4, got %0d", count);
        end
        $finish;
    end
endmodule
""")

    res = run_iverilog_simulation(sources=[counter_v, tb_v], cwd=sandbox)
    assert res.succeeded is True
    assert res.metrics["simulation_finished"] is True
    assert "TEST_PASSED" in res.stdout


def test_verilator_lint(tmp_path: Path):
    """Verify Verilator static lint runner."""
    sandbox = make_sandbox(base=tmp_path, tag="lint_test")
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

    res = run_verilator_lint(sources=[counter_v], top_module="counter", cwd=sandbox)
    assert res.succeeded is True
    assert res.metrics["lint_passed"] is True
    assert res.metrics["error_count"] == 0


def test_yosys_synthesis(tmp_path: Path):
    """Verify Yosys logic synthesis and netlist generation."""
    sandbox = make_sandbox(base=tmp_path, tag="synth_test")
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

    out_netlist = sandbox / "counter_gate.v"
    res = run_yosys_synthesis(
        sources=[counter_v],
        top_module="counter",
        output_netlist=out_netlist,
        cwd=sandbox,
    )

    assert res.succeeded is True
    assert res.metrics["netlist_generated"] is True
    assert res.metrics["num_cells"] > 0
    assert out_netlist.exists()


def test_opensta_timing(tmp_path: Path):
    """Verify OpenSTA static timing analysis and slack metrics extraction."""
    local_dir = Path(__file__).resolve().parent.parent / "lib"
    fallback_dir = Path("/home/vishalkamath/Desktop/ImageUpscalerAccelerator/OpenSTA/examples")
    ex_dir = local_dir if (local_dir / "sky130hd_tt.lib.gz").exists() else fallback_dir
    lib = ex_dir / "sky130hd_tt.lib.gz"
    netlist = ex_dir / "gcd_sky130hd.v"
    sdc = ex_dir / "gcd_sky130hd.sdc"

    if not (lib.exists() and netlist.exists() and sdc.exists()):
        pytest.skip("OpenSTA example benchmark files not found.")

    sandbox = make_sandbox(base=tmp_path, tag="sta_test")
    res = run_opensta_timing(
        liberty_file=lib,
        netlist_file=netlist,
        sdc_file=sdc,
        top_module="gcd",
        cwd=sandbox,
    )

    assert res.succeeded is True
    assert res.metrics["timing_closed"] is True
    assert res.metrics["wns_setup_ns"] is not None
    assert res.metrics["wns_setup_ns"] > 0.0
    assert res.metrics["tns_ns"] == 0.0

