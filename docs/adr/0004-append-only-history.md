# 4. Conversation history is append-only

Date: 2026-10-04 · Status: accepted

## Context

Current Claude models return signed thinking blocks that are bound to the exact conversation
prefix that produced them (system prompt, tool set, earlier messages). Editing an earlier
turn invalidates them, and newer accounts get a 400 for it. Prompt caching has the same
requirement: any change in the prefix restarts the cache.

## Decision

- Sessions only append. Hooks get the request but are documented not to edit earlier
  messages; rewriting history means forking a session.
- Assistant turns keep the provider's native content and the same adapter replays it byte for
  byte (AD-02).
- The system prompt is rendered once per agent instance and must not contain per-request
  values; tool specs are sorted so the tool set is stable.
- Progressive skill disclosure happens through tool results, never by editing the system
  prompt mid-conversation.
- Unanswered tool calls (a crash mid-step) are closed by appending error results, not by
  deleting the call.

## Consequences

Context compaction (Phase 2) must use server-side compaction or append-only summaries
rather than rewriting old turns.
