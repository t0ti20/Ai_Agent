"""agent-base: a reusable base agent. Subclass it, add skills and tools, pick a model in YAML."""

from agent_base.adapters.llm.base import Capabilities, ModelAdapter, ModelRequest, ModelResponse
from agent_base.adapters.registry import register_adapter
from agent_base.capabilities.tools.decorator import Tool, tool
from agent_base.config.loader import LoadedConfig, load_config
from agent_base.config.models import AgentConfig, ModelProfile
from agent_base.core.agent import BaseAgent
from agent_base.core.errors import (
    AgentBaseError,
    BudgetExceeded,
    ConfigError,
    ModelError,
    PolicyDenied,
)
from agent_base.core.events import Event, FinalEvent, RunResult, TextDelta, Usage
from agent_base.core.loop import LoopStrategy, ReActLoop, RunContext, SingleShotLoop
from agent_base.core.messages import Message, ToolCallPart, ToolResultPart
from agent_base.core.session import Session

__version__ = "0.1.0.dev0"

__all__ = [
    "AgentBaseError",
    "AgentConfig",
    "BaseAgent",
    "BudgetExceeded",
    "Capabilities",
    "ConfigError",
    "Event",
    "FinalEvent",
    "LoadedConfig",
    "LoopStrategy",
    "Message",
    "ModelAdapter",
    "ModelError",
    "ModelProfile",
    "ModelRequest",
    "ModelResponse",
    "PolicyDenied",
    "ReActLoop",
    "RunContext",
    "RunResult",
    "Session",
    "SingleShotLoop",
    "TextDelta",
    "Tool",
    "ToolCallPart",
    "ToolResultPart",
    "Usage",
    "load_config",
    "register_adapter",
    "tool",
]
