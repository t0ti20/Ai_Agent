"""Hello agent: the smallest useful agent. Copy it, or start fresh with `agent new <name>`."""

from agent_base import BaseAgent

from .tools import current_time, word_count


class HelloAgent(BaseAgent):
    tools = [current_time, word_count]
