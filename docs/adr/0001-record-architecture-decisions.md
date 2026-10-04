# 1. Record architecture decisions

Date: 2026-10-04 · Status: accepted

## Context

agent-base is meant to be inherited by many agents and maintained over years. Decisions
about its structure need to be findable and explained, not rediscovered from code.

## Decision

Record each architectural decision as a short Markdown file in `docs/adr/`, numbered in
order, with Context, Decision and Consequences sections. A superseded ADR stays, marked
"superseded by N".

## Consequences

Changes to layers, adapter interfaces, config format or message format start with an ADR.
