# agent-base

A reusable base agent. Subclass it, drop in skills and tools, pick a model in YAML, and
get budgets, approvals, sessions, tests and a CLI with no extra code.

```python
from agent_base import BaseAgent, tool


@tool
def lookup_order(order_id: str) -> dict:
    """Look up an order by its ID.

    Args:
        order_id: The order number, like "A-1001".
    """
    ...


class SupportAgent(BaseAgent):
    tools = [lookup_order]
    skills = ["triage", "refunds"]  # SKILL.md folders, loaded on demand
```

The same agent runs on Claude, OpenAI, or a local model (Ollama, vLLM, LM Studio, llama.cpp)
by changing `model.profile`.

## Quick start

```bash
uv sync --all-extras
uv run agent run hello "What time is it in Cairo?" --profile mock    # offline, scripted model
cp .env.example .env                                                  # then add ANTHROPIC_API_KEY
uv run agent run hello "What time is it in Cairo?"                    # Claude (profile: smart)
uv run agent run hello "What time is it in Cairo?" --profile local    # Ollama on localhost
uv run agent new my-agent && uv run pytest agents/my-agent            # your own agent, tested
```

`agent chat <agent>` starts a conversation; `--session <id>` resumes a saved one.

## What you get (v0.1)

| Area | What works today |
|---|---|
| Core | `BaseAgent` with `run()` / `stream()`, lifecycle hooks, ReAct and single-shot loops, structured event stream |
| Budgets | max steps, tokens, cost (from profile prices) and wall clock; graceful stop with partial output |
| Tools | `@tool` builds JSON Schema from type hints and docstrings, validates arguments, runs parallel calls with timeouts |
| Skills | Agent Skills `SKILL.md` folders with progressive disclosure; project, agent and user search paths |
| Models | `anthropic`, `openai`, `openai_compat` (local servers), `mock`; fallback chain across profiles |
| Config | layered YAML (`default` → `models` → `env` → `agent.yaml` → env vars → `--set`), `${VAR}` secrets, JSON Schema, errors with file:line |
| Safety | write/destructive/external tools need approval by default; secrets never in YAML |
| Sessions | saved as JSON, resumable; history kept append-only |
| CLI | `agent new`, `run`, `chat`, `config validate/show/schema`, `skill new/validate` |

Planned next (see the roadmap in [docs/requirements.md](docs/requirements.md)): MCP client,
Claude Code / OpenCode harness adapters, LiteLLM, memory and compaction, the eval harness
with a CI gate, OpenTelemetry tracing, and a Docker sandbox.

## Configuration

```yaml
# agents/support/agent.yaml (merged over configs/default.yaml and configs/models.yaml)
agent:
  name: support
  instructions: Answer order questions. Open a ticket when you cannot resolve one.
  budgets: {max_steps: 20, max_cost_usd: 0.50}
model:
  profile: smart          # smart | fast | openai | local | mock, from configs/models.yaml
  effort: medium
  fallback: [local]
tools:
  require_approval: [create_ticket]
```

`uv run agent config show support --resolved` prints every final value with the file and
line it came from.

## Docs

- [Requirements and roadmap](docs/requirements.md)
- [Concepts](docs/concepts.md)
- [Architecture decisions](docs/adr/)
- [AGENTS.md](AGENTS.md): commands and conventions for contributors and coding agents
