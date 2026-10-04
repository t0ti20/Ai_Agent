# 3. YAML is the single source of truth for configuration

Date: 2026-10-04 · Status: accepted

## Context

Agents are configured from code, files, environment and (later) a GUI. Settings that live
only in a GUI or a database are invisible to Git and code review.

## Decision

- One Pydantic model (`config/models.py`) defines the schema; the JSON Schema in
  `configs/schema/` is generated from it and checked in CI.
- Files merge in a fixed order with per-value source tracking (CF-01, CF-04), so every error
  and every `config show --resolved` line points at a file and line.
- Unknown keys are errors (catches typos).
- Secrets are only ever referenced as `${ENV_VAR}`; a literal `api_key` is rejected (CF-02).
- The future GUI edits these files (GUI-02) rather than keeping its own store.

## Consequences

Features from the requirements' example config that are not built yet (`extends`, `mcp`,
`memory`, `security`, `observability`) are rejected as unknown keys until their phase lands,
instead of being silently ignored.
