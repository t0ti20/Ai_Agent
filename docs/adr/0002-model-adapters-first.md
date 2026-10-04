# 2. Two adapter kinds; model adapters first

Date: 2026-10-04 · Status: accepted

## Context

The requirements name two different things a profile can point at: a model API
(Claude, OpenAI, a local model) where agent-base runs the loop, and a whole agent harness
(Claude Code, OpenCode, Codex CLI) that runs its own loop.

## Decision

- `ModelAdapter` (AD-01): `stream()` yields text/thinking deltas and then one
  `ModelResponse`; `complete()`, `count_tokens()`, `capabilities()` and `cost()` build on it.
  Phase 1 ships `anthropic`, `openai`, `openai_compat` and `mock`.
- `HarnessAdapter` (`run_task`, `stream_events`, `resume`, `cancel`) is Phase 2. It will map
  harness events onto the same event stream (AD-08).
- Adapters are found by name in `adapters/registry.py`, imported lazily so a missing optional
  SDK only matters when its profile is used. Entry-point discovery for third-party adapters
  is Phase 3; `register_adapter()` covers in-process registration until then.
- `openai` uses Chat Completions for now, the format every OpenAI-compatible server shares.
  A Responses API adapter can be added beside it without touching the runtime.

## Consequences

Whether harness adapters live in core or a separate package is still open (see the open
questions in the requirements).
