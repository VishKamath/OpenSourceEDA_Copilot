"""
tests/test_prompt_parser.py: Tests for Natural Language Prompt Parser
=====================================================================
"""

import pytest
from agents.prompt_parser import parse_user_prompt, PromptSpec


def test_parse_prompt_frequency_and_clock():
    prompt = "Synthesize counter targeting 250MHz clock with 150ps uncertainty and 0.8ns input delay"
    spec = parse_user_prompt(prompt)

    assert spec.intent == "synthesis"
    assert spec.clock_frequency_mhz == 250.0
    assert spec.clock_period_ns == 4.0
    assert spec.uncertainty_ns == 0.15
    assert spec.input_delay_ns == 0.8


def test_parse_prompt_verification_corner_cases():
    prompt = "Run verification on bit4_ca with corner cases, 100 cycles of stimulus, and active-low reset"
    spec = parse_user_prompt(prompt)

    assert spec.intent == "verification"
    assert spec.test_corner_cases is True
    assert spec.stimulus_cycles == 100
    assert spec.reset_active_high is False


def test_parse_prompt_sta_full_flow():
    prompt = "Run full flow on module fa targeting 5ns period clock port clk_in, ensure coverage >= 95%"
    spec = parse_user_prompt(prompt)

    assert spec.intent == "full_flow"
    assert spec.clock_period_ns == 5.0
    assert spec.clock_port == "clk_in"
    assert spec.target_coverage_pct == 95.0


def test_parse_prompt_synthesis_area():
    prompt = "Synthesize siren_neuron with area optimization, flatten hierarchy, max 500 cells"
    spec = parse_user_prompt(prompt)

    assert spec.intent == "synthesis"
    assert spec.optimize_area is True
    assert spec.flatten is True
    assert spec.cell_limit == 500


def test_empty_prompt():
    spec = parse_user_prompt("")
    assert spec.intent is None
    assert spec.test_corner_cases is False
