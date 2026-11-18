"""Explainer agent (#19): LLM that answers 'why' questions using only tool outputs."""

from energy_brain.explainer.agent import ExplainerAgent, ExplainerAnswer, load_prompt
from energy_brain.explainer.context import PlanContext, load_backtest
from energy_brain.explainer.llm import FakeLLMClient, GroqClient, LLMError, LLMResponse, ToolCall
from energy_brain.explainer.tools import TOOL_DECLARATIONS, ExplainerTools
from energy_brain.explainer.verify import verify_answer

__all__ = [
    "TOOL_DECLARATIONS",
    "ExplainerAgent",
    "ExplainerAnswer",
    "ExplainerTools",
    "FakeLLMClient",
    "GroqClient",
    "LLMError",
    "LLMResponse",
    "PlanContext",
    "ToolCall",
    "load_backtest",
    "load_prompt",
    "verify_answer",
]
