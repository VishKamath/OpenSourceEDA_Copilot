"""
agents/prompt_parser.py: Natural Language Prompt Interpreter for EDA Specificity
================================================================================
Hardware Mental Model:
- PromptParser: High-Level Specification & Constraint Ingestion Decoder.
  Translates human engineer intent (frequency targets, corner cases, reset polarities,
  assertions, and optimization goals) into formal digital constraints (SDC, ATPG stimulus,
  and synthesis parameters).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class PromptSpec:
    """
    Structured hardware specification extracted from user prompt.
    """
    raw_prompt: str = ""

    # Pipeline intent
    intent: Optional[str] = None               # "verification", "synthesis", "sta", "full_flow"
    top_module: Optional[str] = None           # e.g., "bit4_ca", "counter", "siren_neuron"
    max_iterations: Optional[int] = None       # e.g., 5

    # Clock & Timing constraints
    clock_port: Optional[str] = None           # e.g., "clk", "clk_i"
    clock_period_ns: Optional[float] = None    # e.g., 4.0
    clock_frequency_mhz: Optional[float] = None# e.g., 250.0
    uncertainty_ns: Optional[float] = None     # e.g., 0.15
    input_delay_ns: Optional[float] = None     # e.g., 0.8
    output_delay_ns: Optional[float] = None    # e.g., 0.8

    # Verification & Stimulus specifics
    test_corner_cases: bool = False            # True if user requested corner/edge cases
    stimulus_cycles: Optional[int] = None      # Number of randomized transactions
    reset_active_high: Optional[bool] = None   # True: active-high, False: active-low
    reset_duration_ns: Optional[int] = None    # e.g., 50
    target_coverage_pct: Optional[float] = None# e.g., 95.0
    custom_assertions: List[str] = field(default_factory=list)
    custom_checks: List[str] = field(default_factory=list)

    # Synthesis specifics
    flatten: Optional[bool] = None             # True to flatten hierarchy
    optimize_area: Optional[bool] = None       # True to optimize for area
    optimize_timing: Optional[bool] = None     # True to optimize for timing/speed
    keep_hierarchy: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_user_prompt(prompt: str) -> PromptSpec:
    """
    Parses natural language prompts for EDA specifications and directives.
    Extracts frequency targets, corner case instructions, timing margins,
    reset requirements, assertions, and synthesis goals.
    """
    spec = PromptSpec(raw_prompt=prompt)
    if not prompt or not prompt.strip():
        return spec

    p_lower = prompt.lower()

    # -------------------------------------------------------------------------
    # 1. Pipeline Intent Detection
    # -------------------------------------------------------------------------
    if any(k in p_lower for k in ["full flow", "full-flow", "end to end", "signoff flow", "complete flow"]):
        spec.intent = "full_flow"
    elif any(k in p_lower for k in ["synthesize", "synthesis", "logic synthesis", "gate netlist", "yosys"]):
        spec.intent = "synthesis"
    elif any(k in p_lower for k in ["sta", "timing analysis", "timing signoff", "slack", "opensta"]):
        spec.intent = "sta"
    elif any(k in p_lower for k in ["verify", "verification", "simulate", "simulation", "testbench", "lint"]):
        spec.intent = "verification"

    # -------------------------------------------------------------------------
    # 2. Top Module Extraction
    # -------------------------------------------------------------------------
    top_match = re.search(
        r"(?:top(?:_level)?|module|design|target)\s*(?:is|module|:)?\s*([a-zA-Z_][a-zA-Z0-9_]*)",
        prompt,
        re.IGNORECASE,
    )
    if top_match:
        cand = top_match.group(1).lower()
        if cand not in ["is", "module", "level", "flow", "verification", "synthesis", "sta"]:
            spec.top_module = top_match.group(1)

    # -------------------------------------------------------------------------
    # 3. Frequency & Clock Constraints
    # -------------------------------------------------------------------------
    # Look for Frequency (e.g., 250MHz, 250 MHz, 1.2GHz, 500mhz)
    freq_match = re.search(r"(\d+(?:\.\d+)?)\s*(ghz|mhz|khz)", p_lower)
    if freq_match:
        val = float(freq_match.group(1))
        unit = freq_match.group(2)
        if unit == "ghz":
            freq_mhz = val * 1000.0
        elif unit == "khz":
            freq_mhz = val / 1000.0
        else:
            freq_mhz = val

        spec.clock_frequency_mhz = freq_mhz
        if freq_mhz > 0:
            spec.clock_period_ns = round(1000.0 / freq_mhz, 3)

    # Look for explicit Period (e.g., period 4ns, 5.0 ns clock, 10ns period)
    period_match = re.search(
        r"(?:period|clock|clk)\s*(?:of|is|:)?\s*(\d+(?:\.\d+)?)\s*(ns|ps)",
        p_lower,
    )
    if not period_match:
        period_match = re.search(r"(\d+(?:\.\d+)?)\s*(ns|ps)\s*(?:clock|period)", p_lower)

    if period_match:
        val = float(period_match.group(1))
        unit = period_match.group(2)
        period_ns = val if unit == "ns" else val / 1000.0
        spec.clock_period_ns = round(period_ns, 3)
        if period_ns > 0:
            spec.clock_frequency_mhz = round(1000.0 / period_ns, 2)

    # Clock port name (e.g., clock clk_i, port clk_in, clock port clk)
    clk_port_match = re.search(
        r"(?:clock|clk)\s*(?:port|pin|name|signal)?\s*(?:is|:)?\s*([a-zA-Z_][a-zA-Z0-9_]*)",
        prompt,
        re.IGNORECASE,
    )
    if clk_port_match:
        cand = clk_port_match.group(1)
        if cand.lower() not in ["period", "frequency", "of", "is", "at", "with", "target", "port", "pin", "name"]:
            spec.clock_port = cand

    # Clock uncertainty (e.g. uncertainty 0.15ns, 150ps uncertainty, skew 0.2ns)
    unc_match = re.search(
        r"(?:uncertainty|skew|jitter)\s*(?:of|is|:)?\s*(\d+(?:\.\d+)?)\s*(ns|ps)",
        p_lower,
    )
    if not unc_match:
        unc_match = re.search(r"(\d+(?:\.\d+)?)\s*(ns|ps)\s*(?:uncertainty|skew|jitter)", p_lower)
    if unc_match:
        val = float(unc_match.group(1))
        spec.uncertainty_ns = val if unc_match.group(2) == "ns" else round(val / 1000.0, 4)

    # Input/Output Delays (e.g., input delay 1.2ns or 0.8ns input delay)
    inp_del_match = re.search(r"input\s*delay\s*(?:of|is|:)?\s*(\d+(?:\.\d+)?)\s*(ns|ps)", p_lower)
    if not inp_del_match:
        inp_del_match = re.search(r"(\d+(?:\.\d+)?)\s*(ns|ps)\s*input\s*delay", p_lower)
    if inp_del_match:
        val = float(inp_del_match.group(1))
        spec.input_delay_ns = val if inp_del_match.group(2) == "ns" else round(val / 1000.0, 4)

    out_del_match = re.search(r"output\s*delay\s*(?:of|is|:)?\s*(\d+(?:\.\d+)?)\s*(ns|ps)", p_lower)
    if not out_del_match:
        out_del_match = re.search(r"(\d+(?:\.\d+)?)\s*(ns|ps)\s*output\s*delay", p_lower)
    if out_del_match:
        val = float(out_del_match.group(1))
        spec.output_delay_ns = val if out_del_match.group(2) == "ns" else round(val / 1000.0, 4)

    # -------------------------------------------------------------------------
    # 4. Verification Specifics (ATPG, Stimulus, Corners)
    # -------------------------------------------------------------------------
    if any(k in p_lower for k in [
        "corner case", "corner cases", "corner-case", "edge case", "edge cases",
        "boundary", "boundaries", "overflow", "underflow", "all ones", "all zeros",
        "extreme values", "directed test"
    ]):
        spec.test_corner_cases = True

    # Stimulus count (e.g., 50 cycles, 100 transactions, repeat 40)
    stim_match = re.search(
        r"(\d+)\s*(?:cycles?|transactions?|vectors?|stimulus|iterations?|repeats?)",
        p_lower,
    )
    if not stim_match:
        stim_match = re.search(r"(?:repeat|run)\s*(\d+)", p_lower)
    if stim_match:
        spec.stimulus_cycles = int(stim_match.group(1))

    # Reset polarity
    if "active high" in p_lower or "active-high" in p_lower:
        spec.reset_active_high = True
    elif "active low" in p_lower or "active-low" in p_lower:
        spec.reset_active_high = False

    # Reset duration
    rst_dur_match = re.search(r"reset\s*(?:for|duration|pulse)?\s*(\d+)\s*(ns|cycles?)", p_lower)
    if rst_dur_match:
        spec.reset_duration_ns = int(rst_dur_match.group(1))

    # Target Coverage (e.g. 95% coverage, coverage >= 90%)
    cov_match = re.search(r"(?:coverage|target)\s*(?:>=|>|of|:)?\s*(\d+(?:\.\d+)?)\s*%", p_lower)
    if not cov_match:
        cov_match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*coverage", p_lower)
    if cov_match:
        spec.target_coverage_pct = float(cov_match.group(1))

    # Specific Custom Assertions & Checks
    assert_matches = re.findall(
        r"(?:assert|ensure|check\s+that|verify\s+that)\s+([^,.;\n]+)",
        prompt,
        re.IGNORECASE,
    )
    for a in assert_matches:
        cleaned = a.strip()
        if len(cleaned) > 3 and not any(k in cleaned.lower() for k in ["timing closed", "coverage", "synthesis"]):
            spec.custom_assertions.append(cleaned)

    # -------------------------------------------------------------------------
    # 5. Synthesis Specifics
    # -------------------------------------------------------------------------
    if any(k in p_lower for k in ["optimize area", "area optimization", "minimize area", "min area", "small area"]):
        spec.optimize_area = True
    if any(k in p_lower for k in ["optimize timing", "timing optimization", "speed optimization", "max frequency"]):
        spec.optimize_timing = True

    if "flatten" in p_lower:
        spec.flatten = True
    elif "keep hierarchy" in p_lower or "preserve hierarchy" in p_lower:
        spec.keep_hierarchy = True

    # Cell count limit (e.g. under 50 cells, max 100 cells)
    cell_lim_match = re.search(r"(?:under|max|limit)\s*(\d+)\s*cells?", p_lower)
    if cell_lim_match:
        spec.cell_limit = int(cell_lim_match.group(1))

    # Max repair iterations
    iter_match = re.search(r"(?:max|allow)\s*(\d+)\s*(?:repair|repair loops?|retries|iterations?)", p_lower)
    if iter_match:
        spec.max_iterations = int(iter_match.group(1))

    return spec
