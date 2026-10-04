# Reusable AI Agent Template — Requirements & Feature Plan

2026-10-04 · Khaled El-Sayed

> Exported from the Claude Doc [Reusable AI Agent Template — Requirements & Feature Plan](https://claude.ai/artifact/Wz96foq2S2Pq1gzD4JRKwq) on 2026-10-04. Requirement IDs (FR-, AD-, CF-, SEC-, OBS-, NFR-) are referenced from code and docs; keep them stable.

## Vision, goals, non-goals

Build one open, well-documented base agent that any new agent inherits from: you subclass it, drop in skills and tools, pick a model in a YAML file, and get logging, tests, evals and safety for free.

**Working name:** `agent-base` (rename freely).

**Goals**

1. **Inherit, don't rebuild.** A new agent = a subclass + a config file + a few skills/tools. Target: a working "hello agent" in under 15 minutes from `git clone`.
2. **Model-agnostic.** Swap Claude, OpenAI, Gemini, a local model (Ollama, vLLM, llama.cpp), or a whole coding harness (Claude Code, OpenCode, Codex CLI) by changing one config value.
3. **Standards-first.** Speak the formats the ecosystem already uses (Agent Skills / `SKILL.md`, MCP for tools, A2A for agent-to-agent, OpenTelemetry for traces, `AGENTS.md` for repo instructions) instead of inventing private ones.
4. **Testable by design.** Every agent ships with unit tests, a mock LLM, and an eval suite that runs in CI.
5. **Safe by default.** Least-privilege tools, approval gates, sandboxed execution, secrets never in the prompt.
6. **Documented and organized.** A predictable folder layout, docs site, examples, and templates so the repo is easy to learn and extend.

**Non-goals (v1)**

- Not a hosted SaaS platform; it is a library + template + CLI you run yourself.
- Not a new model or a fine-tuning toolkit.
- No visual no-code builder in v1 (a config GUI is in scope; a drag-and-drop flow editor is a later phase).
- No guarantee of identical behavior across models; the adapter normalizes the interface, not model quality.

## What the community needs (research, Oct 2026)

Developers don't lack frameworks; they lack a base that is portable across models, observable, testable and secure out of the box. Each finding below maps to a requirement later in this doc.

| Need | Evidence | What it means for this template |
|---|---|---|
| Less framework friction, easier upgrades | A study of 1,575 agent projects and 11,910 developer discussions compared frameworks on efficiency, abstraction clarity, learning cost, performance and maintainability, and found version/dependency breakage as a recurring failure class ([arXiv 2512.01939](https://arxiv.org/html/2512.01939v1)) | Small core, clear layers, pinned deps, semantic versioning, migration guides |
| Portable skills | Agent Skills (`SKILL.md` folder + optional `scripts/`, `references/`, `assets/`) is an open standard loaded by progressive disclosure, adopted by Claude Code, Codex, Cursor, Gemini CLI and others ([agentskills.io](https://agentskills.io/)) | `skills/` folder follows the spec exactly; skills work in this agent and in other tools |
| Standard tool connectivity | MCP's 2026-07-28 revision made the core stateless and deprecated roots, sampling and logging; Tier-1 SDKs (TS, Python, Go, C#) support it ([MCP changelog](https://github.com/modelcontextprotocol/specification/releases/tag/2026-07-28), [Scalar explainer](https://scalar.com/learn/mcp/what-is-mcp)) | MCP client built in; tools can also be exposed as an MCP server; target the 2026-07-28 spec |
| Agent-to-agent interop | A2A reached v1.0 under the Linux Foundation; agents publish an Agent Card at `/.well-known/agent-card.json` ([Tyk guide](https://tyk.io/learning-center/a2a-protocol-architecture-and-technical-specification/)) | Optional A2A server/client module; auto-generate the Agent Card from config |
| Many providers, one interface | Gateways such as LiteLLM expose 100+ providers, including Ollama and vLLM, in OpenAI format ([LiteLLM](https://github.com/yuukiLike/litellm)); not every "OpenAI-compatible" server implements the full spec ([Langdock](https://docs.langdock.com/settings/models/openai-compatible)) | Own adapter interface + capability flags; LiteLLM as one adapter, not the only one |
| Harness-level adapters | Claude Code runs headless (`claude -p`, JSON/stream-JSON output, allowed-tools flags) and via the Agent SDK in Python/TS ([Claude Code docs](https://code.claude.com/docs/en/headless)); conformance suites already test 15 coding CLIs incl. OpenCode headless/ACP/SDK modes ([harness-test](https://pkg.go.dev/github.com/belt-sh/harness-test)) | Separate "harness adapter" type that delegates the whole loop to Claude Code / OpenCode / Codex |
| Observability | OpenTelemetry GenAI semantic conventions (`gen_ai.*` spans for model calls, tool calls, agent invocations; MCP conventions since v1.39) are the vendor-neutral standard ([Greptime](https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions)) | Emit OTel spans by default; works with Langfuse, Phoenix, Datadog, Jaeger |
| Evals that keep up | Only 52.4% of teams run offline evals and 37.3% online evals ([Mastra](https://mastra.ai/articles/ai-agent-evaluation)); output-only checks pass 20-40% more cases than trajectory evaluation reveals ([Latitude](https://latitude.so/blog/best-ai-agent-evaluation-platforms-2026-comprehensive-comparison)) | Built-in eval harness scoring outcome + trajectory + cost, gated in CI |
| Security | Indirect prompt injection via repos, `AGENTS.md`/`CLAUDE.md` files and MCP responses is the primary threat; mandatory sandbox controls and default-ask approvals recommended ([NVIDIA](https://developer.nvidia.com/blog/?p=112277)); OWASP publishes an agent security audit play ([OWASP](https://owasp.org/secure-agent-playbook/plugins/ai-security-skills/plays/agent-security-audit.html)) | Permission policy engine, sandboxed tool execution, secret injection, audit log |
| Visibility over agents | Only 24.4% of organizations have full visibility into which agents talk to each other ([NuTech](https://nutechdigital.com/ai-agents-in-your-apps-what-developers-need-to-know-in-2026/)) | Agent registry, identity per agent, traced delegation |
| One instructions file | `AGENTS.md` is the cross-tool repo instruction file; nearest file wins in monorepos ([Morph](https://morphllm.com/agents-md-guide)) | Ship `AGENTS.md` in the template and in every generated agent |

## Architecture: layers and folder structure

The core is six layers; each talks only to the layer below through an interface, so any one can be swapped without touching the rest.

**Layer rules**

- **Interfaces layer** (CLI, REST/WebSocket API, GUI, A2A server, MCP server) only calls the Runtime.
- **Runtime** owns the agent loop: plan → act → observe, state, sessions, hooks, cancellation, budgets.
- **Capabilities** = skills, tools, memory, sub-agents. Registered via plugins and discovered by convention.
- **Policy & safety** wraps every tool call and model call: permissions, approvals, guardrails, redaction.
- **Adapters** translate to the outside world: LLM providers, harnesses, vector stores, sandboxes, MCP servers.
- **Platform** (config, logging, tracing, secrets, storage) is used by every layer.

**Folder structure (the template repo)**

```text
agent-base/
├── AGENTS.md                  # instructions for coding agents working on this repo
├── README.md                  # quick start in 5 commands
├── pyproject.toml             # or package.json for the TS port
├── configs/
│   ├── default.yaml           # base config every agent inherits
│   ├── models.yaml            # model profiles (fast, smart, local, cheap)
│   ├── schema/agent.schema.json  # JSON Schema for validation + editor autocomplete
│   └── env/                   # dev.yaml, prod.yaml overlays
├── src/agent_base/
│   ├── core/                  # BaseAgent, AgentLoop, State, Session, Events, Hooks
│   ├── adapters/
│   │   ├── llm/               # anthropic, openai, gemini, ollama, vllm, litellm, openai_compat
│   │   ├── harness/           # claude_code, opencode, codex_cli (delegate the whole loop)
│   │   ├── memory/            # in_memory, sqlite, redis, pgvector, chroma
│   │   └── sandbox/           # local, docker, e2b-style remote
│   ├── capabilities/
│   │   ├── skills/            # SKILL.md loader, progressive disclosure
│   │   ├── tools/             # @tool decorator, registry, JSON-Schema generation
│   │   ├── mcp/               # MCP client + server
│   │   ├── memory/            # short-term, long-term, summarization/compaction
│   │   └── subagents/         # delegation, handoffs, orchestration patterns
│   ├── policy/                # permissions, approvals, guardrails, injection filters
│   ├── interfaces/            # cli/, api/, gui/, a2a/
│   ├── observability/         # OTel tracing, metrics, cost tracking, audit log
│   ├── config/                # loader, merge, env interpolation, validation
│   └── plugins/               # entry-point discovery for third-party packages
├── skills/                    # project-level skills (Agent Skills format)
│   └── example-skill/SKILL.md
├── tools/                     # project-level tools (python modules)
├── prompts/                   # versioned system prompts and templates
├── agents/                    # concrete agents that inherit BaseAgent
│   └── hello_agent/{agent.py, agent.yaml, skills/, tools/, evals/}
├── evals/                     # datasets, graders, eval configs
├── tests/                     # unit, integration, contract tests per adapter
├── examples/                  # runnable examples, one feature each
├── docs/                      # MkDocs/Docusaurus site
├── templates/                 # cookiecutter/copier template for `agent new`
├── docker/                    # Dockerfile, compose with Ollama + Langfuse
└── .github/workflows/         # lint, test, eval gate, release
```

**Inheritance model**

```python
class SupportAgent(BaseAgent):
    config_file = "agent.yaml"          # merged over configs/default.yaml
    skills = ["triage", "refunds"]      # resolved from skills/ folders
    tools = [lookup_order, create_ticket]

    async def on_before_tool(self, call):   # hook override
        ...
```

Agents can override any hook (`on_start`, `on_before_model`, `on_after_model`, `on_before_tool`, `on_after_tool`, `on_error`, `on_finish`) or replace whole components (loop strategy, memory, policy) through config or code.

## Functional requirements

IDs are stable so issues and PRs can reference them. Priority: **M** = must for v1.0, **S** = should, **C** = could (later).

| ID | Area | Requirement | Priority |
|---|---|---|---|
| FR-01 | Core | `BaseAgent` class with an async `run()` / `stream()` API, overridable lifecycle hooks, and pluggable loop strategy (ReAct, plan-and-execute, single-shot) | M |
| FR-02 | Core | Structured event stream (model delta, tool call, tool result, state change, error, final) consumable by CLI, API and GUI | M |
| FR-03 | Core | Sessions with resume, fork and replay from a saved transcript | M |
| FR-04 | Core | Budgets per run: max steps, max tokens, max cost, wall-clock timeout; graceful stop with partial result | M |
| FR-05 | Core | Structured output: response validated against a Pydantic/JSON Schema with automatic retry on validation failure | M |
| FR-06 | Core | Human-in-the-loop: pause for approval or input, persist state, resume later | S |
| FR-07 | Skills | Load skills in Agent Skills format (`SKILL.md` + front matter), three-stage progressive disclosure (name/description → body → referenced files) | M |
| FR-08 | Skills | Skill search paths: built-in → project `skills/` → agent `skills/` → user dir; later paths override | M |
| FR-09 | Skills | `agent skill new` scaffold and `agent skill validate` (spec checks, length limits) | S |
| FR-10 | Tools | `@tool` decorator that builds JSON Schema from type hints + docstring; sync and async tools | M |
| FR-11 | Tools | Tool registry with namespaces, enable/disable per agent, risk level per tool (read / write / destructive / external) | M |
| FR-12 | Tools | Built-in toolset: file read/write (scoped), shell (sandboxed), HTTP fetch, web search adapter, code execution | S |
| FR-13 | Tools | Parallel tool calls when the model supports them; per-tool timeout and retry | S |
| FR-14 | MCP | MCP client: connect stdio and streamable-HTTP servers from config, target spec 2026-07-28 with fallback to older revisions | M |
| FR-15 | MCP | Expose any agent's tools as an MCP server | S |
| FR-16 | Memory | Short-term conversation memory with automatic compaction/summarization when near the context limit | M |
| FR-17 | Memory | Long-term memory interface (store, search, forget) with SQLite default and vector-store adapters | S |
| FR-18 | Memory | RAG helper: ingest documents, chunk, embed, retrieve with citations | C |
| FR-19 | Multi-agent | Sub-agents as tools, handoffs, and supervisor/worker patterns with isolated context per sub-agent | S |
| FR-20 | Multi-agent | A2A server: publish Agent Card at `/.well-known/agent-card.json` generated from config; A2A client to call remote agents | C |
| FR-21 | Prompts | Versioned prompt templates (Jinja2) with variables, includes, and per-model variants | M |
| FR-22 | Interfaces | CLI: `agent new`, `agent run`, `agent chat`, `agent eval`, `agent serve`, `agent config validate` | M |
| FR-23 | Interfaces | REST + WebSocket/SSE API with OpenAPI docs | S |
| FR-24 | Interfaces | Web GUI: chat playground, config editor, trace viewer, eval dashboard | S |
| FR-25 | Testing | Mock LLM adapter with scripted responses and record/replay cassettes for deterministic tests | M |
| FR-26 | Testing | Eval harness: datasets (YAML/JSONL), graders (exact, regex, LLM-as-judge, state check), outcome + trajectory + cost scores, N-trial runs for flakiness | M |
| FR-27 | Testing | CI gate: fail the build if regression evals drop below a threshold | M |
| FR-28 | Plugins | Third-party packages register adapters, tools, skills via entry points; no core edits needed | S |
| FR-29 | Scaffolding | `agent new <name> --template=chat\|rag…` *(row truncated in the source doc: rest of requirement and priority missing)* | ? |

## LLM adapter layer

There are two adapter kinds, because "Claude Code" and "a local Llama" are different things: a **model adapter** gives you completions and your loop runs the agent; a **harness adapter** hands the whole task to an existing agent runtime and streams its events back.

| Kind | Adapter | How it connects | Notes |
|---|---|---|---|
| Model | `anthropic` | Native Messages API | Prompt caching, extended thinking, effort levels |
| Model | `openai` | Native Responses/Chat API | Reasoning effort, structured outputs |
| Model | `gemini` | Native API | Thinking budget |
| Model | `openai_compat` | Any OpenAI-format endpoint (`base_url`) | Covers Ollama, vLLM, LM Studio, llama.cpp server, LocalAI; capability flags fill feature gaps |
| Model | `litellm` | LiteLLM SDK or proxy | 100+ providers, fallback and spend tracking for free |
| Model | `mock` | Scripted / cassette replay | Tests and offline dev |
| Harness | `claude_code` | Claude Agent SDK (Python/TS) or `claude -p --output-format stream-json` | Inherits Claude Code tools, skills, MCP, permission modes |
| Harness | `opencode` | OpenCode headless / server mode or ACP | Any provider OpenCode supports |
| Harness | `codex_cli` | `codex exec` headless | OpenAI coding harness |
| Harness | `acp_generic` | Agent Client Protocol over stdio | One adapter for any ACP-speaking CLI |

**Adapter requirements**

- **AD-01** One `ModelAdapter` interface: `complete()`, `stream()`, `count_tokens()`, `capabilities()`. One `HarnessAdapter` interface: `run_task()`, `stream_events()`, `resume()`, `cancel()`.
- **AD-02** Normalized message format (text, images, tool calls, tool results, thinking blocks) with lossless round-trip per provider.
- **AD-03** Capability flags per model: tool calling, parallel tools, vision, JSON mode, streaming, thinking, max context, max output. The runtime degrades gracefully (e.g. emulates tool calling via prompt for models without it).
- **AD-04** Unified "effort" knob (`low | medium | high | max`) mapped to each provider's own setting (thinking budget, reasoning effort, temperature/top-p for local models).
- **AD-05** Routing: pick a model per step type (planner = smart, tool-arg filler = fast, summarizer = cheap) from named profiles.
- **AD-06** Fallback chain and retries with exponential backoff on rate limits, timeouts, 5xx; circuit breaker per provider.
- **AD-07** Token and cost accounting per call from a price table in config, rolled up per run and per session.
- **AD-08** Harness adapters map their native events onto the same event stream (FR-02) and respect the same policy layer where the harness allows it (allowed-tools, permission mode, working directory).
- **AD-09** Contract test suite every adapter must pass (streaming, tool round-trip, error mapping, cancellation) so community adapters are trustworthy.
- **AD-10** Adding an adapter = one class + entry-point registration; no core changes.

## Configuration (YAML + GUI)

YAML is the single source of truth; the GUI is just an editor for the same file, so nothing configured in the GUI is invisible to Git.

```yaml
# agents/support_agent/agent.yaml  (merged over configs/default.yaml)
agent:
  name: support-agent
  description: Answers order questions and opens tickets
  extends: base                 # inherit another agent's config
  loop: react                   # react | plan_execute | single_shot
  budgets: { max_steps: 25, max_cost_usd: 0.50, timeout_s: 300 }

model:
  profile: smart                # from configs/models.yaml
  effort: medium                # low | medium | high | max
  fallback: [fast, local]

profiles:                       # usually lives in models.yaml
  smart: { adapter: anthropic, model: claude-opus-5-5, temperature: 0.2 }
  fast:  { adapter: openai_compat, base_url: ${LITELLM_URL}, model: gpt-fast }
  local: { adapter: openai_compat, base_url: http://localhost:11434/v1, model: qwen3:14b }
  coder: { adapter: claude_code, permission_mode: acceptEdits, allowed_tools: [Read, Edit, Bash(pytest*)] }

skills:  { paths: [./skills, ~/.agent-base/skills], enabled: [triage, refunds] }
tools:   { enabled: [lookup_order, create_ticket], require_approval: [create_ticket] }
mcp:
  servers:
    github: { transport: http, url: https://mcp.example.com, auth: oauth }
memory:  { short_term: { compaction_at: 0.8 }, long_term: { adapter: sqlite, path: ./data/mem.db } }
security: { sandbox: docker, network: allowlist, allow_hosts: [api.example.com] }
observability: { otel_endpoint: ${OTEL_EXPORTER_OTLP_ENDPOINT}, capture_content: false }
```

**Config requirements**

- **CF-01** Layered merge: `default.yaml` → `models.yaml` → env overlay (`dev`/`prod`) → agent's `agent.yaml` → environment variables → CLI flags. Later wins.
- **CF-02** `${VAR}` interpolation; secrets only ever referenced, never stored in YAML; `.env` support for dev and a secrets-manager adapter for prod.
- **CF-03** JSON Schema published for the config, giving validation (`agent config validate`), clear error messages with line numbers, and autocomplete in VS Code.
- **CF-04** `agent config show --resolved` prints the final merged config with the source of each value.
- **CF-05** Hot reload in dev for prompts, skills and model profile; restart required for adapters.
- **CF-06** TOML and JSON accepted as alternatives; YAML is the documented default.

**GUI requirements**

- **GUI-01** Form generated from the JSON Schema: pick model profile, effort, budgets, enable/disable skills, tools and MCP servers.
- **GUI-02** Writes back to YAML with comments preserved and shows a diff before saving.
- **GUI-03** Built-in playground: chat with the agent, swap model live, compare two models side by side on the same prompt.
- **GUI-04** Trace viewer (step tree of model and tool calls with tokens, latency, cost) and eval results page.
- **GUI-05** Runs locally (`agent ui`), single user by default; auth required when bound to a non-local address.

## Non-functional requirements

**Security and safety**

- **SEC-01** Permissions enforced in code, never by prompt: per-agent allow/deny lists for tools, file paths, hosts and shell commands.
- **SEC-02** Default-ask for write/destructive/external tools; approval callbacks for CLI, API and GUI; a project-level deny list users cannot override.
- **SEC-03** All tool output, fetched pages, MCP results and repo files are tagged untrusted; they can never change permissions or config. Block agent writes to its own config, skills, hooks and `AGENTS.md`.
- **SEC-04** Sandboxed execution for shell and code tools (Docker by default; remote sandbox adapter optional) with no network unless allow-listed.
- **SEC-05** Secret injection: credentials bound at tool creation, never placed in the model context or logs; automatic redaction of keys and PII in traces.
- **SEC-06** Append-only audit log of every tool call, approval and policy decision.
- **SEC-07** Prompt-injection test pack in the eval suite (direct, indirect, tool-output poisoning) mapped to OWASP LLM01.
- **SEC-08** MCP servers and A2A agents pinned by URL/version and verified (signed Agent Cards where available).

**Observability**

- **OBS-01** OpenTelemetry tracing using GenAI semantic conventions: agent invocation, model call, tool call, sub-agent, compaction as nested spans.
- **OBS-02** Prompt/response content capture is opt-in (off by default) for privacy.
- **OBS-03** Metrics: tokens, cost, latency, tool error rate, steps per run; exportable via OTLP.
- **OBS-04** One-command local stack (Docker Compose) with a trace UI such as Langfuse or Arize Phoenix.

**Quality, performance, portability**

- **NFR-01** Core package installs with no heavy deps; adapters and extras are optional (`pip install agent-base[anthropic,ollama,ui]`).
- **NFR-02** Framework overhead under 50 ms per loop step excluding model latency; fully async; handles 100 concurrent sessions per process.
- **NFR-03** Python 3.11+ on Linux, macOS, Windows; Docker image published. A TypeScript port is a later phase.
- **NFR-04** Test coverage ≥ 85% on core; every adapter passes the contract suite (AD-09).
- **NFR-05** Semantic versioning, changelog, deprecation warnings for one minor release before removal, migration guide per major.
- **NFR-06** Typed public API (mypy/pyright strict) and stable extension points documented as such.
- **NFR-07** Works fully offline with a local model and the mock adapter.
- **NFR-08** Apache-2.0 or MIT license; CONTRIBUTING, CODE_OF_CONDUCT and SECURITY policy in the repo.

## Feature roadmap

Phase 1 is where the template becomes useful on its own: one agent, real tools and skills, cloud or local model. Everything after widens reach without changing the core.

**Roadmap: 5 phases, 4 gates.** Ship a working base in Phase 1, then grow outward. Each gate must pass before the next phase starts.

| Phase | Version | Name | Scope |
|---|---|---|---|
| 0 | v0.0 | Foundation | Repo layout, AGENTS.md, CI, docs site skeleton, ADRs. Core interfaces, config loader + JSON Schema, event model, mock LLM adapter |
| **1** | **v0.1 MVP** | **Core MVP** | **BaseAgent + ReAct loop, hooks, budgets, @tool registry, SKILL.md loader. Adapters: anthropic, openai, openai_compat (Ollama/vLLM); CLI run/chat/new** |
| 2 | v0.5 | Ecosystem | MCP client, Claude Code + OpenCode harnesses, LiteLLM, memory + compaction. Eval harness + CI gate, OTel tracing, Docker sandbox, permission policy |
| 3 | v1.0 | Production | Config GUI + playground + trace viewer, REST/SSE API, approvals, audit log. Plugin entry points, agent templates, injection test pack, full docs |
| 4 | v2.x | Advanced | A2A server/client, multi-agent patterns, long-term memory + RAG, TypeScript port. Prompt optimization from failed evals, cost-aware routing, skill marketplace |

Gates:

- **0 → 1:** config validates, mock agent passes tests
- **1 → 2:** same agent runs on a cloud and a local model
- **2 → 3:** evals block a regressing PR in CI
- **3 → 4:** v1.0 definition of done met

Phase 1 is highlighted because it is the first release you can inherit from; gates are the checks that must pass before moving on.

**Feature ideas to add and enhance**

| Feature | What it gives you | Phase |
|---|---|---|
| Agent doctor (`agent doctor`) | Diagnoses config, keys, model reachability, MCP servers | 2 |
| Self-healing tool calls | Auto-repairs invalid tool arguments from schema errors before failing | 2 |
| Context engineering kit | Compaction, pinned facts, scratchpad files, tool-result trimming | 2 |
| Model arena | Run one task on N models; compare quality, cost, latency side by side | 3 |
| Time-travel debugger | Replay a saved run step by step, edit a step, re-run from there | 3 |
| Durable execution | Checkpoint every step; resume after crash or restart | 3 |
| Prompt and skill versioning | Version, diff and A/B test prompts and skills against evals | 3 |
| Guardrail plugins | PII redaction, topic limits, toxicity and jailbreak filters | 3 |
| Feedback capture | Thumbs up/down and corrections in the GUI become eval cases | 3 |
| Secrets adapters | Vault, AWS/GCP/Azure secret managers | 3 |
| Local model benchmark | Tokens/sec and tool-call accuracy per local model on your hardware | 3 |
| Triggers and schedules | Run agents from cron, webhooks, Slack, GitHub or email events | 4 |
| Eval generation | Turn failed production traces into new regression cases automatically | 4 |
| Cost-aware router | Picks the cheapest model that meets a quality bar per step | 4 |
| Agent registry and identity | Every agent has an ID, owner, version; delegation is traced | 4 |
| Skill marketplace | `agent skill install <git-url>` with signature and version pinning | 4 |
| Multi-tenant mode | Per-user quotas, rate limits, isolated memory and keys | 4 |
| Voice I/O | Speech-to-text and text-to-speech adapters for voice agents | 4 |

## Tech stack, documentation, definition of done

**Suggested stack (Python first)**

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.11+, `asyncio` | Largest agent ecosystem; official MCP, A2A and Claude Agent SDKs exist |
| Packaging | `uv` + `pyproject.toml`, optional extras | Fast installs, clean optional adapters |
| Config + validation | Pydantic v2 models → exported JSON Schema; `ruamel.yaml` to keep comments | One model drives validation, docs and GUI forms |
| Templates | Jinja2 for prompts; Copier for `agent new` | Copier can update generated agents when the template changes |
| CLI | Typer | Typed, auto help |
| API | FastAPI + SSE/WebSocket | OpenAPI docs for free |
| GUI | React + Vite served by FastAPI (or Gradio for a fast v0) | Schema-driven forms, trace viewer |
| Tracing | OpenTelemetry SDK + OTLP exporter | Vendor-neutral |
| Tests | pytest, pytest-asyncio, VCR-style cassettes | Deterministic adapter tests |
| Evals | Own harness; optional DeepEval/Phoenix integration | CI gate without lock-in |
| Lint/type | Ruff, Pyright strict, pre-commit | Consistent code |
| Docs | MkDocs Material + mkdocstrings | API reference from docstrings |
| CI | GitHub Actions: lint → unit → contract → eval gate → release to PyPI + GHCR | Every PR is checked end to end |

**Documentation set**

- `README.md`: what it is, 5-command quick start, one GIF.
- `AGENTS.md`: build/test commands and conventions for coding agents working on the repo.
- Docs site: Concepts (layers, loop, events) · Tutorials (first agent, add a skill, add a tool, switch to a local model, write evals) · How-to guides (one per adapter, MCP, A2A, deploy) · Reference (config schema, CLI, API, hooks) · ADRs (`docs/adr/`) for every architectural decision.
- One runnable example per feature in `examples/`, each tested in CI.
- Docstrings on every public class and function; generated API reference.

**Definition of done for v1.0**

- [ ] `agent new hello && agent run hello "hi"` works on Linux, macOS and Windows
- [ ] Same agent runs unchanged on Anthropic, OpenAI, a local Ollama model and the Claude Code harness by changing only `model.profile`
- [ ] A skill written for this template loads in Claude Code without edits (and vice versa)
- [ ] MCP server from config is usable as tools; traces show up in Langfuse/Phoenix
- [ ] Eval suite with injection tests runs in CI and blocks a regressing PR
- [ ] Docs site published with tutorials for every M requirement

**Open questions**

- [ ] Python only for v1, or Python + TypeScript from day one?
- [ ] License: Apache-2.0 (patent grant) or MIT?
- [ ] Should harness adapters be in core or a separate `agent-base-harnesses` package?

**Sources**

- [An Empirical Study of Agent Developer Practices in AI Agent Frameworks (arXiv 2512.01939)](https://arxiv.org/html/2512.01939v1)
- [Agent Skills — agentskills.io](https://agentskills.io/)
- [MCP 2026-07-28 release](https://github.com/modelcontextprotocol/specification/releases/tag/2026-07-28) · [What is MCP (Scalar)](https://scalar.com/learn/mcp/what-is-mcp)
- [A2A protocol architecture (Tyk)](https://tyk.io/learning-center/a2a-protocol-architecture-and-technical-specification/)
- [LiteLLM](https://github.com/yuukiLike/litellm) · [OpenAI-compatible endpoints (Langdock)](https://docs.langdock.com/settings/models/openai-compatible)
- [Claude Code headless / Agent SDK](https://code.claude.com/docs/en/headless) · [harness-test conformance suite](https://pkg.go.dev/github.com/belt-sh/harness-test)
- [OpenTelemetry GenAI semantic conventions (Greptime)](https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions)
- [AI agent evaluation (Mastra)](https://mastra.ai/articles/ai-agent-evaluation) · [Agent evaluation platforms (Latitude)](https://latitude.so/blog/best-ai-agent-evaluation-platforms-2026-comprehensive-comparison)
- [Sandboxing agentic workflows (NVIDIA)](https://developer.nvidia.com/blog/?p=112277) · [OWASP agent security audit](https://owasp.org/secure-agent-playbook/plugins/ai-security-skills/plays/agent-security-audit.html)
- [AI agents in apps 2026 (NuTech)](https://nutechdigital.com/ai-agents-in-your-apps-what-developers-need-to-know-in-2026/) · [AGENTS.md guide (Morph)](https://morphllm.com/agents-md-guide)
