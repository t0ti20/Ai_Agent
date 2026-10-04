# Concepts

## Layers

| Layer | Package | Job |
|---|---|---|
| Interfaces | `interfaces/` | CLI today; REST/SSE API, GUI, A2A and MCP server later. Only calls the runtime. |
| Runtime | `core/` | `BaseAgent`, loop strategies, sessions, budgets, hooks, events. |
| Capabilities | `capabilities/` | Tools and skills; memory and sub-agents later. |
| Policy | `policy/` | Approval gate for risky tools; full permission engine later. |
| Adapters | `adapters/` | Model providers; harnesses, sandboxes and vector stores later. |
| Platform | `config/` | Layered config used by every layer; tracing and secrets managers later. |

## The loop

`ReActLoop` (the default) repeats:

1. Check budgets (steps, tokens, cost, wall clock).
2. Send the system prompt, the session history and the tool specs to the model, streaming
   text and thinking deltas out as events.
3. If the model declined (refusal), stop without adding its partial turn to history.
4. Append the assistant turn. With no tool calls, the run is complete.
5. For each tool call: run `on_before_tool`, check approval, then run the approved calls
   concurrently with per-tool timeouts and append all results in one user turn.

Tool failures (unknown tool, bad arguments, timeout, denial, exceptions) go back to the
model as error results so it can recover; they do not end the run.

## Events

`agent.stream(prompt)` yields `text_delta`, `thinking_delta`, `tool_call`, `tool_result`,
`state` (model call, tool calls, awaiting approval, fallback), `error`, and exactly one
`final` carrying the `RunResult`. `agent.run(prompt)` returns that result directly.
`agent run --json` prints the same events as JSON lines.

## Messages

`Message` is provider-neutral: text, thinking, tool-call and tool-result parts. Assistant
turns also keep the provider's own content in `Message.native`; an adapter replays its own
native content unchanged and converts the parts for anything else. That is how Claude's
signed thinking blocks survive a multi-turn conversation, and how a session can still
switch providers.

## Skills

A skill is a folder with `SKILL.md` (front matter `name` + `description`, then
instructions) and optional `references/`, `scripts/`, `assets/`. Only names and
descriptions go into the system prompt. The model reads a skill with `load_skill` and
its files with `read_skill_file` when needed. Search order (later overrides earlier):
project `skills/`, the agent's own `skills/`, then `~/.agent-base/skills`.

## Configuration

Layers merge in order: `configs/default.yaml`, `configs/models.yaml`,
`configs/env/<AGENT_ENV>.yaml`, the agent's `agent.yaml`, `AGENT_BASE__SECTION__KEY`
environment variables, then `--set key=value` flags. Mappings merge key by key; lists
replace. `${VAR}` and `${VAR:-default}` are filled from the environment (and `.env`);
an unset variable only fails when the profile that needs it is used.
