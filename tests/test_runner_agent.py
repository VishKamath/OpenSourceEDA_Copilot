"""
tests/test_runner_agent.py: Unit tests for AutoGen Tool Binding & Sandbox Boundary
==================================================================================
Validates:
1. Tool serialization into JSON protocol packets.
2. MPU boundary enforcement against path traversal attacks.
3. AutoGen FunctionTool execution on real RTL stimulus.
4. AutoGen AssistantAgent instantiation and configuration.
"""

import json
import pytest
from pathlib import Path
from autogen_core import CancellationToken
from autogen_agentchat.agents import AssistantAgent

from tools.eda_runners import make_sandbox
from agents.runner_agent import (
    eda_lint,
    eda_simulate,
    eda_synthesize,
    get_eda_tools_for_workspace,
    create_eda_runner_agent,
)


@pytest.fixture
def sandbox_with_counter(tmp_path: Path):
    """Allocates a sandboxed workspace populated with counter RTL and TB."""
    ws = make_sandbox(base=tmp_path, tag="agent_test")

    counter_v = ws / "counter.v"
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

    tb_v = ws / "counter_tb.v"
    tb_v.write_text("""
module counter_tb;
    reg clk, rst_n, en;
    wire [3:0] count;
    counter dut (.clk(clk), .rst_n(rst_n), .en(en), .count(count));
    always #5 clk = ~clk;
    initial begin
        clk = 0; rst_n = 0; en = 0;
        #15 rst_n = 1; #10 en = 1; #40 en = 0; #10;
        if (count == 4'd4) $display("PASS"); else $fatal(1, "FAIL");
        $finish;
    end
endmodule
""")
    return ws, counter_v, tb_v


def test_sandbox_boundary_enforcement(tmp_path: Path):
    """Verify that agent tools reject paths outside the workspace (MPU check)."""
    ws = make_sandbox(base=tmp_path, tag="mpu_test")
    forbidden = tmp_path / "leak.v"
    forbidden.write_text("module secret; endmodule")

    with pytest.raises(PermissionError):
        eda_lint(sources=[str(forbidden)], top_module="secret", workspace_dir=str(ws))

    with pytest.raises(PermissionError):
        eda_simulate(sources=[str(forbidden)], workspace_dir=str(ws))


def test_autogen_function_tool_execution(sandbox_with_counter):
    """Verify AutoGen FunctionTool execution via run_json interface."""
    import asyncio

    async def _run_test():
        ws, counter_v, tb_v = sandbox_with_counter

        tools = get_eda_tools_for_workspace(str(ws))
        tool_dict = {t.name: t for t in tools}

        assert "eda_lint" in tool_dict
        assert "eda_simulate" in tool_dict
        assert "eda_synthesize" in tool_dict
        assert "eda_sta" in tool_dict

        token = CancellationToken()

        # 1. Execute Lint Tool
        lint_tool = tool_dict["eda_lint"]
        lint_resp = await lint_tool.run_json({"sources": [str(counter_v)], "top_module": "counter"}, token)
        lint_data = json.loads(lint_resp)
        assert lint_data["succeeded"] is True
        assert lint_data["metrics"]["lint_passed"] is True

        # 2. Execute Simulation Tool
        sim_tool = tool_dict["eda_simulate"]
        sim_resp = await sim_tool.run_json({"sources": [str(counter_v), str(tb_v)]}, token)
        sim_data = json.loads(sim_resp)
        assert sim_data["succeeded"] is True
        assert sim_data["metrics"]["simulation_finished"] is True

        # 3. Execute Synthesis Tool
        synth_tool = tool_dict["eda_synthesize"]
        synth_resp = await synth_tool.run_json({"sources": [str(counter_v)], "top_module": "counter"}, token)
        synth_data = json.loads(synth_resp)
        assert synth_data["succeeded"] is True
        assert synth_data["metrics"]["num_cells"] > 0

    asyncio.run(_run_test())


def test_create_eda_runner_agent(tmp_path: Path):
    """Verify AutoGen AssistantAgent construction with bound EDA tools."""
    ws = make_sandbox(base=tmp_path, tag="agent_inst")
    agent = create_eda_runner_agent(name="eda_verifier", workspace_dir=str(ws))

    assert isinstance(agent, AssistantAgent)
    assert agent.name == "eda_verifier"
    assert "Autonomous EDA" in agent._system_messages[0].content
