"""AI settings from `study.yaml` (`ai:`), validated. Plan P-26 to P-28, P-58.

`mode` is the master mode for the agent functions. A function may have its own mode in
`modes` (P-58): the Slack Agent runs live in the demo while the Quality Check agents stay
on replay. Functions in OWN_MODE never follow `mode`: without their own entry they are off,
so switching the master mode (`ADJUD_AI_MODE`) never turns the Slack Agent on.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Mode = Literal["off", "replay", "live"]
SLACK_AGENT_NAME = "slack_agent"  # the Agent in Slack (P-58)
OWN_MODE = frozenset({SLACK_AGENT_NAME})


class Prices(BaseModel):
    """US dollars per million tokens, entered from the pricing page at go-live."""

    model_config = ConfigDict(extra="forbid")
    input: float = 0.0
    output: float = 0.0


class AiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode = "off"
    modes: dict[str, Mode] = Field(default_factory=dict)  # per function, over `mode` (P-58)
    model: str = "claude-haiku-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    max_tokens: int = 16000
    timeout_seconds: float = 120.0
    max_tool_turns: int = 8
    max_retries: int = 2  # schema-invalid answers re-asked, then continue without AI
    min_confidence: float = 0.8
    budget_usd: float = 5.0
    price_per_mtok: Prices = Field(default_factory=Prices)
    functions: dict[str, bool] = Field(default_factory=dict)

    def mode_for(self, function: str) -> Mode:
        """The mode one function runs in: its own, else the master mode (OWN_MODE: off)."""
        if function in self.modes:
            return self.modes[function]
        return "off" if function in OWN_MODE else self.mode

    def enabled(self, function: str) -> bool:
        return self.mode_for(function) != "off" and self.functions.get(function, False)

    def cost(self, usage: dict[str, int]) -> float:
        """Cache writes cost 1.25x and cache reads 0.1x the input price."""
        p = self.price_per_mtok
        tokens_in = (
            usage.get("input_tokens", 0)
            + 1.25 * usage.get("cache_creation_input_tokens", 0)
            + 0.1 * usage.get("cache_read_input_tokens", 0)
        )
        return (tokens_in * p.input + usage.get("output_tokens", 0) * p.output) / 1_000_000
