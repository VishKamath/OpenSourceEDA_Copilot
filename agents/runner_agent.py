"""
agents/runner_agent.py: AutoGen Execution Sandbox & EDA Tool Binding
====================================================================
Hardware Mental Model:
- AutoGen Agent: Testbench Bus Functional Model (BFM) / Sequencer
- FunctionTool: Command transaction interface mapping high-level instructions
  into pin-level executions.
- Boundary Validator: Memory Protection Unit (MPU) isolating the agent
  within its designated scratchpad memory space.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from autogen_core.tools import FunctionTool
from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import (
    ChatCompletionClient,
    CreateResult,
    ModelCapabilities,
    ModelInfo,
    RequestUsage,
)

from tools.eda_runners import (
    ToolResult,
    run_iverilog_simulation,
    run_opensta_timing,
    run_verilator_lint,
    run_yosys_synthesis,
    validate_safe_path,
)


class MockEdaClient(ChatCompletionClient):
    """
    Mock model client used for deterministic offline simulation, unit testing,
    and fallback execution when no live LLM API endpoint is specified.
    """
    @property
    def model_info(self) -> ModelInfo:
        return {
            "vision": False,
            "function_calling": True,
            "json_output": True,
            "family": "mock",
            "structured_output": True,
        }

    @property
    def capabilities(self) -> ModelCapabilities:
        return {
            "vision": False,
            "function_calling": True,
            "json_output": True,
            "structured_output": True,
        }

    async def create(self, messages, *args, **kwargs) -> CreateResult:
        return CreateResult(
            finish_reason="stop",
            usage=RequestUsage(prompt_tokens=10, completion_tokens=10),
            content="Task completed.",
        )

    async def create_stream(self, messages, *args, **kwargs):
        raise NotImplementedError("Streaming not supported in mock client.")

    def actual_usage(self) -> RequestUsage:
        return RequestUsage(prompt_tokens=0, completion_tokens=0)

    def total_usage(self) -> RequestUsage:
        return RequestUsage(prompt_tokens=0, completion_tokens=0)

    def count_tokens(self, messages, **kwargs) -> int:
        return 10

    def remaining_tokens(self, messages, **kwargs) -> int:
        return 100000

    async def close(self) -> None:
        pass


# ==============================================================================
# SECTION 1: SANDBOXED TOOL WRAPPERS (STIMULUS PROTOCOL CONVERTERS)
# ==============================================================================

def _serialize_tool_result(res: ToolResult) -> Dict[str, Any]:
    """Converts an immutable ToolResult transaction packet into a JSON-serializable dict."""
    return {
        "command": res.command,
        "returncode": res.returncode,
        "succeeded": res.succeeded,
        "timedout": res.timedout,
        "metrics": res.metrics,
        "generated_files": [str(p) for p in res.generated_files],
        "stdout_tail": res.stdout[-800:] if len(res.stdout) > 800 else res.stdout,
        "stderr_tail": res.stderr[-800:] if len(res.stderr) > 800 else res.stderr,
    }


def eda_lint(sources: List[str], top_module: str, workspace_dir: str) -> Dict[str, Any]:
    """
    Executes Verilator static lint check on Verilog/SystemVerilog RTL.
    Guards workspace boundaries using MPU validation.
    """
    ws = Path(workspace_dir).resolve()
    safe_sources = [validate_safe_path(Path(s), ws) for s in sources]
    res = run_verilator_lint(sources=safe_sources, top_module=top_module, cwd=ws)
    return _serialize_tool_result(res)


def eda_simulate(sources: List[str], workspace_dir: str) -> Dict[str, Any]:
    """
    Executes a 2-stage Icarus Verilog simulation (iverilog -> vvp).
    Detects $fatal, assertions, and collects waveform artifacts.
    """
    ws = Path(workspace_dir).resolve()
    safe_sources = [validate_safe_path(Path(s), ws) for s in sources]
    res = run_iverilog_simulation(sources=safe_sources, cwd=ws)
    return _serialize_tool_result(res)


def eda_synthesize(
    sources: List[str],
    top_module: str,
    workspace_dir: str,
    liberty_file: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Executes logic synthesis via Yosys. Generates structural gate-level netlists
    and extracts cell, wire, and register statistics.
    """
    ws = Path(workspace_dir).resolve()
    safe_sources = [validate_safe_path(Path(s), ws) for s in sources]
    lib_path = Path(liberty_file).resolve() if liberty_file else None

    out_netlist = ws / f"{top_module}_synth_netlist.v"
    res = run_yosys_synthesis(
        sources=safe_sources,
        top_module=top_module,
        output_netlist=out_netlist,
        liberty_file=lib_path,
        cwd=ws,
    )
    return _serialize_tool_result(res)


def eda_sta(
    netlist_file: str,
    liberty_file: str,
    sdc_file: str,
    top_module: str,
    workspace_dir: str,
) -> Dict[str, Any]:
    """
    Executes OpenSTA static timing signoff. Evaluates setup/hold slacks (WNS)
    and Total Negative Slack (TNS) against constraints.
    """
    ws = Path(workspace_dir).resolve()
    netlist_path = Path(netlist_file).resolve()
    lib_path = Path(liberty_file).resolve()
    sdc_path = Path(sdc_file).resolve()

    res = run_opensta_timing(
        liberty_file=lib_path,
        netlist_file=netlist_path,
        sdc_file=sdc_path,
        top_module=top_module,
        cwd=ws,
    )
    return _serialize_tool_result(res)


# ==============================================================================
# SECTION 2: AUTOGEN TOOL BINDING FACTORY
# ==============================================================================

def get_eda_tools_for_workspace(workspace_dir: str) -> List[FunctionTool]:
    """
    Factory creating pre-bound AutoGen FunctionTools pinned to a sandboxed workspace.
    The agent cannot escape or redirect execution outside the specified workspace.
    """
    ws = str(Path(workspace_dir).resolve())

    def lint_bound(sources: List[str], top_module: str) -> str:
        """Run Verilator static lint check on the given RTL sources."""
        result = eda_lint(sources=sources, top_module=top_module, workspace_dir=ws)
        return json.dumps(result, indent=2)

    def simulate_bound(sources: List[str]) -> str:
        """Run Icarus Verilog simulation on RTL and testbench files."""
        result = eda_simulate(sources=sources, workspace_dir=ws)
        return json.dumps(result, indent=2)

    def synthesize_bound(sources: List[str], top_module: str, liberty_file: Optional[str] = None) -> str:
        """Synthesize RTL to gate-level netlist using Yosys."""
        result = eda_synthesize(sources=sources, top_module=top_module, workspace_dir=ws, liberty_file=liberty_file)
        return json.dumps(result, indent=2)

    def sta_bound(netlist_file: str, liberty_file: str, sdc_file: str, top_module: str) -> str:
        """Run OpenSTA signoff static timing analysis."""
        result = eda_sta(netlist_file=netlist_file, liberty_file=liberty_file, sdc_file=sdc_file, top_module=top_module, workspace_dir=ws)
        return json.dumps(result, indent=2)

    return [
        FunctionTool(lint_bound, name="eda_lint", description="Runs Verilator static linting on RTL sources"),
        FunctionTool(simulate_bound, name="eda_simulate", description="Runs Icarus Verilog simulation on RTL and testbenches"),
        FunctionTool(synthesize_bound, name="eda_synthesize", description="Runs Yosys logic synthesis to generate gate netlists"),
        FunctionTool(sta_bound, name="eda_sta", description="Runs OpenSTA static timing analysis to check slack closure"),
    ]


# ==============================================================================
# SECTION 3: AGENT FACTORY (THE UVM SEQUENCER PERSONA)
# ==============================================================================

EDA_ENGINEER_SYSTEM_PROMPT = """
You are an expert Autonomous EDA Verification and Synthesis Engineer.
Your role is to drive open-source silicon tools (iverilog, verilator, yosys, OpenSTA)
to compile, verify, synthesize, and timing-close digital hardware designs.

Operational Principles:
1. Always run eda_lint first before driving simulations.
2. When tests fail or assertions trip, analyze the simulation log, diagnose the root cause,
   and refine the stimulus or fix the testbench.
3. In synthesis, inspect gate cell counts and flip-flop inferences.
4. In STA, ensure Worst Negative Slack (WNS) >= 0.0ns to confirm timing closure.
"""


def create_eda_runner_agent(
    name: str = "eda_runner_agent",
    workspace_dir: Optional[str] = None,
    model_client: Optional[ChatCompletionClient] = None,
) -> AssistantAgent:
    """
    Constructs an AutoGen AssistantAgent pre-wired with sandboxed EDA tools.
    Defaults to MockEdaClient if no model_client is provided.
    """
    target_ws = workspace_dir or "/tmp/eda_workspace"
    tools = get_eda_tools_for_workspace(target_ws)
    client = model_client or MockEdaClient()

    agent = AssistantAgent(
        name=name,
        system_message=EDA_ENGINEER_SYSTEM_PROMPT.strip(),
        tools=tools,
        model_client=client,
    )
    return agent
