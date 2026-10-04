# Prompts

Jinja2 templates that override the ones shipped with agent-base. Lookup order:
`agents/<name>/prompts/` → this folder → `src/agent_base/prompts/`.

- To change the system prompt for every agent, copy
  `src/agent_base/prompts/system/default.md.j2` to `prompts/system/default.md.j2` and edit it.
- To change it for one model profile only, add a variant such as
  `prompts/system/default.local.md.j2` (used when `model.profile` is `local`).
- Keep templates free of volatile values (timestamps, request IDs) so the prompt
  stays identical across requests and caches well.
