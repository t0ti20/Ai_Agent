# AGENTS.md

Instructions for coding agents (and people) working on this repository.

## What this is

`agent-base`: a reusable base agent. A new agent is a `BaseAgent` subclass plus an
`agent.yaml`, with skills (Agent Skills `SKILL.md` folders) and `@tool` functions.
Requirements live in `docs/requirements.md`; their IDs (FR-07, AD-02, CF-01, SEC-02, …)
are cited in docstrings, so keep them stable and cite them when you implement one.

## Commands

```bash
uv sync --all-extras                      # install everything (Python 3.11+)
uv run pytest                             # all tests: tests/ and agents/*/tests/, offline
uv run ruff check . && uv run ruff format --check .
uv run pyright                            # strict mode on src/
uv run agent config validate hello        # config + skills + tools + prompt of an agent
uv run agent config schema --out configs/schema/agent.schema.json   # after changing config models
uv run agent run hello "hi" --profile mock                           # offline smoke test
```

Every change must leave tests, ruff and pyright passing. `tests/test_config.py` fails if
the committed JSON Schema is out of date.

## Layout

```text
src/agent_base/
  core/            BaseAgent, loop strategies, messages, events, sessions, budgets, prompts
  adapters/llm/    one file per provider: anthropic, openai, openai_compat, mock
  capabilities/    tools/ (@tool, registry), skills/ (SKILL.md loader)
  policy/          approvals (default-ask for risky tools)
  config/          Pydantic schema + layered YAML loader
  interfaces/cli/  the `agent` command
  prompts/, templates/   shipped Jinja2 prompts and `agent new` / `skill new` templates
configs/           default.yaml, models.yaml, env/<env>.yaml, schema/agent.schema.json
agents/<name>/     agent.py, agent.yaml, tools.py, skills/, tests/
skills/            project-level skills shared by all agents
docs/              requirements, concepts, ADRs (docs/adr/)
```

Layer rule: interfaces call core; core uses capabilities, policy and adapters through their
interfaces; adapters never import from core.agent or core.loop; config is used by all.

## Conventions

- **History is append-only.** Never edit or reorder earlier messages in a session. Claude
  binds thinking blocks to the exact prior conversation, and the prompt cache needs an
  unchanged prefix. The system prompt is rendered once per agent and must not contain
  timestamps or other per-request values.
- **Adapters replay their own native content** (`Message.native`) and convert everything
  else from the neutral parts. A new adapter is one class + one line in
  `adapters/registry.py`, and needs wire-level tests like `tests/test_adapters.py`
  (real SDK client, `httpx2.MockTransport`, no network).
- **Secrets are never in YAML.** Profiles reference them as `${ENV_VAR}`; the loader rejects
  literal `api_key` values. Never log or print resolved secrets.
- **Safety is enforced in code.** Tools declare a `risk`; write/destructive/external tools
  need approval unless config auto-approves them. Do not rely on prompt wording for safety.
- **Tests run offline.** Use `agent_base.testing.load_test_agent` or the `MockAdapter`.
  Do not add tests that call real model APIs.
- **Claude defaults:** `claude-opus-5-5`, effort set explicitly, no `temperature` (current
  Claude models reject sampling parameters), server-side refusal fallback on, streaming on.
- Typed public API (pyright strict), Google-style docstrings with an `Args:` block on tools.
